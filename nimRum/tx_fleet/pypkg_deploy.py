"""nimRumSystemDeploy — Fleet software update and management.

Handles deploying wheels to RX/SRC devices in parallel, TX self-update,
and fleet restart/reboot operations. Uses nimRumSSH for all remote access.

All operations use the same SSH+SCP mechanism as calibration deploy.
"""

import logging
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from nimRum.common.nimRumSSH import (
    get_registry,
    get_ssh_opts,
    resolve_target,
    run_on_device,
    scp_to_device,
)

logger = logging.getLogger(__name__)


def configure(registry, ssh_key_path: str) -> None:
    """Configure SSH (delegates to nimRumSSH.configure).

    Called by WebUI at startup. Configures the shared SSH module.
    """
    from nimRum.common.nimRumSSH import configure as _ssh_configure
    _ssh_configure(registry, ssh_key_path)


def get_tx_version() -> str:
    """Get the currently installed nimrum version on TX."""
    try:
        res = subprocess.run(
            ["pip3", "show", "nimrum"],
            capture_output=True, timeout=5,
        )
        for line in res.stdout.decode().split("\n"):
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "unknown"


def get_tx_wheel_path() -> str:
    """Find the installed wheel file for the current TX version.

    Looks in the wheels/ directory next to txConfig.yaml.
    Returns empty if not found.
    """
    import glob
    # Check the wheels directory next to txConfig (cwd = /root/nimRum)
    wheels_dir = os.path.join(os.getcwd(), "wheels")
    if os.path.isdir(wheels_dir):
        wheels = sorted(glob.glob(os.path.join(wheels_dir, "nimrum*.whl")),
                        reverse=True)
        if wheels:
            return wheels[0]
    return ""


# ---------------------------------------------------------------------------
# TX self-update
# ---------------------------------------------------------------------------

def update_tx_from_pypi(version: str = "") -> Dict:
    """Update TX from PyPI. Kills and restarts TX+WebUI after install.

    Args:
        version: Specific version string, or empty for latest.

    Returns:
        Dict with 'ok' or 'error'.
    """
    pkg = f"nimrum=={version}" if version else "nimrum"
    try:
        cmd = ["pip3", "install", "--break-system-packages",
               "--upgrade", pkg]
        res = subprocess.run(cmd, capture_output=True, timeout=120)
        if res.returncode != 0:
            err = res.stderr.decode("utf-8", errors="replace").strip()
            return {"error": f"pip install failed: {err}"}

        # A PyPI install leaves no wheel on disk, so the fleet update had
        # nothing correct to push and silently deployed a stale wheel. Fetch
        # the matching wheels into wheels/ so get_tx_wheel_path() finds them.
        wheel_result = _fetch_wheels_to_cache(version)

        # Schedule restart (give time for HTTP response to be sent)
        threading.Timer(1.0, _restart_tx).start()
        msg = "Installed, restarting..."
        if wheel_result.get("error"):
            # TX itself updated fine; only the fleet-deploy cache failed. Report
            # it rather than let a later fleet update push the wrong version.
            msg += (" WARNING: could not cache wheel for fleet deploy: "
                    + wheel_result["error"])
        return {"ok": True, "message": msg,
                "wheels": wheel_result.get("wheels", [])}
    except Exception as e:
        return {"error": str(e)}


def _fetch_wheels_to_cache(version: str = "") -> Dict:
    """Download the nimrum wheel(s) from PyPI into the wheels/ cache dir.

    The fleet update pushes a wheel to each device, so TX must keep the actual
    wheel files, not just the installed package. A bare `pip install` from PyPI
    does not leave one behind, which is why a PyPI-based TX update used to leave
    the fleet update with no correct wheel to push.

    The fleet is mixed-architecture (aarch64 and armv7l), so both wheels for the
    release are fetched. `pip download` on TX would only get TX's own arch and
    silently leave the 32-bit devices with no wheel; instead we read the PyPI
    release metadata and download every published .whl for the exact version.

    Args:
        version: Specific version string, or empty for the latest.

    Returns:
        Dict with 'wheels' (list of cached wheel paths) or 'error'.
    """
    import json as _json
    import urllib.request

    wheels_dir = os.path.join(os.getcwd(), "wheels")
    try:
        os.makedirs(wheels_dir, exist_ok=True)
    except OSError as e:
        return {"error": f"cannot create {wheels_dir}: {e}"}

    # Resolve the version and its file list from the PyPI JSON API.
    try:
        if version:
            url = f"https://pypi.org/pypi/nimrum/{version}/json"
        else:
            url = "https://pypi.org/pypi/nimrum/json"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read())
    except Exception as e:
        return {"error": f"PyPI metadata fetch failed: {e}"}

    resolved = data.get("info", {}).get("version", version)
    # /pypi/<name>/json lists all releases under "releases"; the versioned
    # endpoint lists only that version's files under "urls".
    if version:
        files = data.get("urls", [])
    else:
        files = data.get("releases", {}).get(resolved, [])

    wheels = [f for f in files if f.get("packagetype") == "bdist_wheel"
              and f.get("filename", "").endswith(".whl")]
    if not wheels:
        return {"error": f"no wheels published for nimrum {resolved}"}

    saved = []
    for f in wheels:
        fname = f["filename"]
        dest = os.path.join(wheels_dir, fname)
        if os.path.isfile(dest):
            saved.append(dest)
            continue
        try:
            req = urllib.request.Request(f["url"])
            with urllib.request.urlopen(req, timeout=60) as r:
                content = r.read()
            # Write to a temp name then rename, so a partial download never
            # looks like a usable wheel to get_tx_wheel_path().
            tmp = dest + ".part"
            with open(tmp, "wb") as out:
                out.write(content)
            os.replace(tmp, dest)
            saved.append(dest)
        except Exception as e:
            return {"error": f"download of {fname} failed: {e}"}

    # Remove any other-version wheels so the cache holds only the current TX
    # version. The fleet deploy is scoped to a version anyway, but a tidy cache
    # avoids surprises and keeps get_tx_wheel_path unambiguous.
    import glob
    keep = {os.path.basename(p) for p in saved}
    for old in glob.glob(os.path.join(wheels_dir, "nimrum-*.whl")):
        if os.path.basename(old) not in keep:
            try:
                os.remove(old)
            except OSError:
                pass  # best effort; a leftover cannot mis-deploy now that the
                      # fleet deploy is version-scoped

    return {"wheels": sorted(saved)}


def update_tx_from_wheel(wheel_path: str) -> Dict:
    """Update TX from a local wheel file.

    Args:
        wheel_path: Path to the .whl file on TX filesystem.

    Returns:
        Dict with 'ok' or 'error'.
    """
    if not os.path.isfile(wheel_path):
        return {"error": f"Wheel not found: {wheel_path}"}

    try:
        cmd = ["pip3", "install", "--break-system-packages",
               "--force-reinstall", "--no-deps", wheel_path]
        res = subprocess.run(cmd, capture_output=True, timeout=120)
        if res.returncode != 0:
            err = res.stderr.decode("utf-8", errors="replace").strip()
            return {"error": f"pip install failed: {err}"}

        # Copy wheel to known location for fleet deploy
        import shutil
        shutil.copy2(wheel_path, "/tmp/nimrum_deploy_wheel.whl")

        threading.Timer(1.0, _restart_tx).start()
        return {"ok": True, "message": "Installed, restarting..."}
    except Exception as e:
        return {"error": str(e)}


def _restart_tx() -> None:
    """Restart every enabled non-oneshot nimrum-* unit on the TX box, locally.

    Not just nimrum-tx/webui: the TX box may also run nimrum-src (the co-located
    AudioSource). A hardcoded pair left that service running its old image after a
    TX self-update or a "Restart TX" click, so it kept reporting a stale version in
    its discovery ping. This runs the same role-agnostic loop the remote deploy path
    uses (RESTART_NIMRUM_UNITS_CMD), but locally via bash rather than over SSH, since
    it is executing on the TX box itself.
    """
    import subprocess
    from nimRum.common.nimRumFleetDeploy import RESTART_NIMRUM_UNITS_CMD
    subprocess.run(
        ["bash", "-c", RESTART_NIMRUM_UNITS_CMD],
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# Fleet deploy (parallel)
# ---------------------------------------------------------------------------

# Progress tracking for fleet operations
_fleet_lock = threading.Lock()
_fleet_state = {
    "running": False,
    "total": 0,
    "completed": 0,
    "results": {},  # hostname → {"ok": True} or {"error": "..."}
}


def get_fleet_state() -> Dict:
    """Get current fleet operation progress."""
    with _fleet_lock:
        return dict(_fleet_state)


def deploy_fleet(wheel_path: str, devices: Optional[List[str]] = None,
                 include_tx: bool = False) -> Dict:
    """Deploy a wheel to all (or specified) RX/SRC devices in parallel.

    Args:
        wheel_path: Local path to .whl file on TX.
        devices: List of hostnames, or None for all from registry.
        include_tx: When selecting devices from the registry (devices is None),
            keep the local TX host in the set instead of excluding it. This routes
            the TX box through the same verified deploy path (install + version
            check + role-agnostic restart) as the rest of the fleet, rather than
            relying on the operator to also run the separate TX self-update. The
            deploy reaches TX over loopback SSH like any other device. Ignored when
            an explicit devices list is given — that list is taken as-is.

    Returns:
        Dict with 'ok' if started, or 'error'.
    """
    if not os.path.isfile(wheel_path):
        return {"error": f"Wheel not found: {wheel_path}"}

    if not get_registry():
        return {"error": "Device registry not configured"}

    with _fleet_lock:
        if _fleet_state["running"]:
            return {"error": "Fleet operation already in progress"}

    if devices is None:
        devices = get_registry().get_devices_by_role("rx")
        devices += [d for d in get_registry().get_devices_by_role("src")
                    if d not in devices]
        if not include_tx:
            # By default exclude the local TX device — it is deployed separately
            # via the TX Software section. include_tx=True keeps it here so a
            # single fleet update also covers the TX box (and its co-located
            # nimrum-src) through the verified path.
            import platform
            local_host = platform.node()
            devices = [d for d in devices if d != local_host]

    if not devices:
        return {"error": "No devices found"}

    with _fleet_lock:
        _fleet_state["running"] = True
        _fleet_state["total"] = len(devices)
        _fleet_state["completed"] = 0
        _fleet_state["results"] = {}

    # Run in background thread
    t = threading.Thread(target=_fleet_deploy_worker,
                         args=(wheel_path, devices), daemon=True)
    t.start()
    return {"ok": True, "total": len(devices)}


def _fleet_deploy_worker(wheel_path: str, devices: List[str]) -> None:
    """Background worker that deploys to all devices in parallel."""
    try:
        with ThreadPoolExecutor(max_workers=len(devices)) as pool:
            futures = {
                pool.submit(_deploy_single_device, hostname, wheel_path): hostname
                for hostname in devices
            }
            for future in as_completed(futures):
                hostname = futures[future]
                result = future.result()
                with _fleet_lock:
                    _fleet_state["results"][hostname] = result
                    _fleet_state["completed"] += 1
    finally:
        with _fleet_lock:
            _fleet_state["running"] = False


def _deploy_single_device(hostname: str, wheel_path: str) -> Dict:
    """Deploy wheel to a single device via shared fleet deploy logic.

    Returns:
        Dict with 'ok' or 'error'.
    """
    from nimRum.common.nimRumFleetDeploy import deploy_single_device

    target = resolve_target(hostname)
    opts = get_ssh_opts()

    # Deploy exactly the version in wheel_path (the TX version, per the UI's
    # "same as TX"), just for both architectures. Globbing all `nimrum*.whl`
    # let a stale OLDER version in the dir win, because select_wheel_for_arch
    # takes the first arch match and 2.8.5 sorts before 2.8.7 — that shipped
    # 2.8.5 to the fleet on a 2.8.7 TX. Scope the glob to this version only.
    import glob
    wheel_dir = os.path.dirname(wheel_path)
    wheel_name = os.path.basename(wheel_path)
    # nimrum-<version>-<pytag>-<abi>-<platform>.whl
    parts = wheel_name.split("-")
    version = parts[1] if len(parts) >= 2 else ""
    if version:
        wheel_paths = glob.glob(
            os.path.join(wheel_dir, f"nimrum-{version}-*.whl"))
    else:
        wheel_paths = []
    if not wheel_paths:
        wheel_paths = [wheel_path]

    return deploy_single_device(
        target=target,
        wheel_paths=wheel_paths,
        ssh_opts=opts,
        restart=True,
    )


# ---------------------------------------------------------------------------
# Fleet restart/reboot
# ---------------------------------------------------------------------------

def restart_fleet(devices: Optional[List[str]] = None) -> Dict:
    """Restart RX/SRC processes on all devices (parallel)."""
    if not get_registry():
        return {"error": "Device registry not configured"}
    if devices is None:
        devices = get_registry().get_devices_by_role("rx")
    cmd = "systemctl restart nimrum-rx nimrum-src 2>/dev/null; true"
    return _fleet_ssh_command(devices, cmd, use_sudo=False)


def reboot_fleet(devices: Optional[List[str]] = None) -> Dict:
    """Reboot all RX/SRC devices (parallel)."""
    if not get_registry():
        return {"error": "Device registry not configured"}
    if devices is None:
        devices = get_registry().get_devices_by_role("rx")
    return _fleet_ssh_command(devices, "reboot")


def _fleet_ssh_command(devices: List[str], command: str,
                      use_sudo: bool = True) -> Dict:
    """Run a command on multiple devices in parallel."""
    results = {}

    def _run_one(hostname):
        cmd = f"sudo {command}" if use_sudo else command
        rc, stdout, stderr = run_on_device(hostname, cmd, timeout=10)
        if rc == 0 or rc == -1:
            # -1 is timeout but for reboot that's expected
            return {"ok": True}
        return {"error": stderr or "Command failed"}

    with ThreadPoolExecutor(max_workers=len(devices)) as pool:
        futures = {pool.submit(_run_one, h): h for h in devices}
        for future in as_completed(futures):
            hostname = futures[future]
            results[hostname] = future.result()

    return {"ok": True, "results": results}


# ---------------------------------------------------------------------------
# Device config fetch/push
# ---------------------------------------------------------------------------

def fetch_device_config(hostname: str, prefer: str = "rx") -> Dict:
    """Fetch rxConfig.yaml or audioSourceConfig.yaml from a device.

    Args:
        hostname: Device hostname.
        prefer: Which config to try first — "rx" or "src".

    Returns:
        Dict with 'content' (yaml string) and 'filename', or 'error'.
    """
    if prefer == "src":
        cmd = (
            "if [ -f /root/nimRum/audioSourceConfig.yaml ]; then "
            "  echo FILE=audioSourceConfig.yaml; cat /root/nimRum/audioSourceConfig.yaml; "
            "elif [ -f /root/nimRum/rxConfig.yaml ]; then "
            "  echo FILE=rxConfig.yaml; cat /root/nimRum/rxConfig.yaml; "
            "else echo FILE=NONE; fi"
        )
    else:
        cmd = (
            "if [ -f /root/nimRum/rxConfig.yaml ]; then "
            "  echo FILE=rxConfig.yaml; cat /root/nimRum/rxConfig.yaml; "
            "elif [ -f /root/nimRum/audioSourceConfig.yaml ]; then "
            "  echo FILE=audioSourceConfig.yaml; cat /root/nimRum/audioSourceConfig.yaml; "
            "else echo FILE=NONE; fi"
        )

    rc, output, stderr = run_on_device(hostname, f"sudo bash -c '{cmd}'", timeout=10)
    if rc != 0:
        return {"error": f"SSH failed: {stderr}"}

    lines = output.split("\n", 1)
    if lines[0].startswith("FILE=NONE"):
        return {"error": "No config file found on device"}

    filename = lines[0].replace("FILE=", "").strip()
    content = lines[1] if len(lines) > 1 else ""
    return {"filename": filename, "content": content}


def push_device_config(hostname: str, filename: str, content: str,
                       restart: bool = True) -> Dict:
    """Push a config file to a device and optionally restart.

    Args:
        hostname: Device hostname.
        filename: Config filename (rxConfig.yaml or audioSourceConfig.yaml).
        content: YAML content to write.
        restart: Whether to restart the device process after writing.

    Returns:
        Dict with 'ok' or 'error'.
    """
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
    try:
        tmp.write(content)
        tmp.close()

        # SCP to device /tmp
        rc, err = scp_to_device(
            hostname, tmp.name, "/tmp/nimrum_config_update.yaml", timeout=10,
        )
        if rc != 0:
            return {"error": f"SCP failed: {err}"}

        # Move to correct location + restart
        move_cmd = f"sudo cp /tmp/nimrum_config_update.yaml /root/nimRum/{filename}"
        if restart:
            move_cmd += "; sudo systemctl restart nimrum-rx nimrum-src 2>/dev/null; true"

        run_on_device(hostname, move_cmd, timeout=10)
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}
    finally:
        os.unlink(tmp.name)
