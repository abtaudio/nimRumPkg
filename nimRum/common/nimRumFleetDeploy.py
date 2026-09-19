"""nimRumFleetDeploy — Shared fleet deploy logic used by both CLI and WebUI.

Single implementation of the deploy-to-device sequence:
  1. Detect target architecture (uname -m)
  2. Select correct wheel for that architecture
  3. SCP wheel to device
  4. pip3 install (force-reinstall, no-deps)
  5. Symlink .so + ldconfig
  6. Restart services

Both the CLI (scripts/deploy_cli.py) and WebUI (nimRumSystemDeploy.py)
call these functions.
"""

import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# SSH options matching the project convention
SSH_OPTS_DEFAULT = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=5",
    "-o", "StrictHostKeyChecking=accept-new",
]

# The single definition of "restart every nimRum process on this box, whatever its
# role is". It asks the device which nimrum-* units are enabled rather than naming
# nimrum-rx/tx/src, so it does the right thing on a plain RX, the SRC box, and the TX
# box (which also runs nimrum-src) alike. Oneshot units are skipped: they do boot-time
# work (e.g. nimrum-dac loading out-of-tree DAC modules) and restarting them mid-session
# either does nothing or leaves them failed. Both the remote deploy path and TX's own
# self-update/restart path use this, so a hardcoded pair can no longer strand a
# co-located service on a stale image.
RESTART_NIMRUM_UNITS_CMD = (
    "for u in $(systemctl list-unit-files 'nimrum-*' --no-legend"
    " | awk '/enabled/{print $1}'); do"
    " [ \"$(systemctl show -p Type --value $u)\" = oneshot ]"
    " || sudo systemctl restart $u;"
    " done 2>/dev/null; true"
)


def restart_all_nimrum_units(target: str, ssh_opts: List[str],
                             timeout: int = 10) -> None:
    """Restart every enabled non-oneshot nimrum-* unit on a remote device.

    Role-agnostic by design — see RESTART_NIMRUM_UNITS_CMD. Best effort: the shell
    swallows per-unit failures so one wedged unit does not block the rest.

    Args:
        target: SSH target string (hostname, or user@ip).
        ssh_opts: SSH options list.
        timeout: SSH timeout in seconds.
    """
    _run_ssh(target, RESTART_NIMRUM_UNITS_CMD, ssh_opts, timeout=timeout)


def _build_ssh_opts(ssh_key: str = "", timeout: int = 5) -> List[str]:
    """Build SSH option list with optional key file."""
    opts = [
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={timeout}",
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    if ssh_key and os.path.isfile(ssh_key):
        opts = ["-i", ssh_key] + opts
    return opts


def _run_ssh(target: str, command: str, ssh_opts: List[str],
             timeout: int = 60) -> Tuple[int, str, str]:
    """Run a command on a remote device via SSH."""
    cmd = ["ssh", *ssh_opts, target, command]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return (
            res.returncode,
            res.stdout.decode("utf-8", errors="replace"),
            res.stderr.decode("utf-8", errors="replace"),
        )
    except subprocess.TimeoutExpired:
        return (-1, "", "SSH timeout")
    except Exception as e:
        return (-1, "", str(e))


def _run_scp(src: str, dst: str, ssh_opts: List[str],
             timeout: int = 30) -> Tuple[int, str]:
    """SCP a file. dst should include user@host:path."""
    cmd = ["scp", *ssh_opts, src, dst]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout)
        err = res.stderr.decode("utf-8", errors="replace").strip()
        return (res.returncode, err)
    except subprocess.TimeoutExpired:
        return (-1, "SCP timeout")
    except Exception as e:
        return (-1, str(e))


def select_wheel_for_arch(wheel_paths: List[str], arch: str) -> Optional[str]:
    """Select the correct wheel file for a given architecture.

    Args:
        wheel_paths: List of available .whl file paths.
        arch: Target architecture string (e.g. 'aarch64', 'armv7l').

    Returns:
        Path to matching wheel, or None if not found.
    """
    # Normalize arch from dpkg-style to uname-style
    if arch in ("armhf", "armv7l"):
        search = "armv7l"
    elif arch in ("arm64", "aarch64"):
        search = "aarch64"
    else:
        search = arch

    for wp in wheel_paths:
        if search in os.path.basename(wp):
            return wp
    return None


def deploy_single_device(
    target: str,
    wheel_paths: List[str],
    ssh_opts: List[str],
    restart: bool = True,
    with_deps: bool = False,
) -> Dict:
    """Deploy a wheel to a single device.

    Args:
        target: SSH target string (hostname, or user@ip).
        wheel_paths: List of all available wheel paths (both archs).
        ssh_opts: SSH options list.
        restart: Whether to restart after install.
        with_deps: If True, install dependencies from wheel metadata.
                   Use for new device setup. Default: skip deps (faster).

    Returns:
        Dict with 'ok': True on success, or 'error': str on failure.
    """
    # 1. Detect architecture
    rc, stdout, stderr = _run_ssh(target, "uname -m", ssh_opts, timeout=10)
    if rc != 0:
        return {"error": f"SSH connect failed: {stderr.strip()}"}
    arch = stdout.strip()

    # 2. Select correct wheel
    wheel_path = select_wheel_for_arch(wheel_paths, arch)
    if not wheel_path:
        return {"error": f"No wheel for architecture '{arch}'"}

    wheel_name = os.path.basename(wheel_path)
    remote_tmp = f"/tmp/{wheel_name}"

    # The wheel filename encodes the version we expect on disk afterwards:
    # nimrum-<version>-<pytag>-<abi>-<platform>.whl. Verifying against this is
    # what turns a no-op install (same version reinstalled, or a pip that
    # changed nothing) from a false success into a real check.
    expected_version = ""
    parts = wheel_name.split("-")
    if len(parts) >= 2:
        expected_version = parts[1]

    # 3. SCP wheel to device
    rc, err = _run_scp(wheel_path, f"{target}:{remote_tmp}", ssh_opts)
    if rc != 0:
        return {"error": f"SCP failed: {err}"}

    # 4. Install via pip3
    # Try with --break-system-packages (Trixie), fall back without (Bullseye)
    no_deps_flag = "" if with_deps else "--no-deps"
    install_cmd = (
        f"pip3 install --no-cache-dir --force-reinstall {no_deps_flag} {remote_tmp} 2>&1 || "
        f"pip3 install --no-cache-dir --break-system-packages --root-user-action=ignore "
        f"--force-reinstall {no_deps_flag} {remote_tmp} 2>&1; "
        f"rm -f {remote_tmp}; "
        # 5. Symlink shared library
        "nimDir=$(python3 -c \"import nimRum,os;print(os.path.dirname(nimRum.__file__))\" 2>/dev/null); "
        "if [ -f \"$nimDir/libnimRumDSP.so\" ]; then "
        "  ln -sf \"$nimDir/libnimRumDSP.so\" /usr/local/lib/libnimRumDSP.so; ldconfig; "
        "fi; "
        "echo INSTALL_DONE"
    )
    rc, stdout, stderr = _run_ssh(target, f"sudo bash -c '{install_cmd}'",
                                  ssh_opts, timeout=60)
    if "INSTALL_DONE" not in stdout and "Successfully installed" not in stdout:
        return {"error": f"Install failed: {stdout[-200:]}"}

    # 5b. Verify the version actually importable now, as a *separate* command so
    # its quoting never collides with the install heredoc's single quotes. The
    # metadata version is pip's own source of truth; a deploy that did not land
    # shows the old version here and is caught. Double quotes only on the remote.
    verify_cmd = (
        'python3 -c "from importlib.metadata import version;'
        'print(chr(10) + \\"NIMRUM_VER=\\" + version(\\"nimrum\\"))"'
    )
    _rc, vout, _verr = _run_ssh(target, verify_cmd, ssh_opts, timeout=15)

    installed_version = ""
    for line in vout.splitlines():
        line = line.strip()
        if line.startswith("NIMRUM_VER="):
            installed_version = line.split("=", 1)[1].strip()
            break

    if not installed_version:
        return {"error": f"Installed but version unreadable: {vout[-200:]}"}

    if expected_version and installed_version != expected_version:
        return {"error": (f"Version mismatch after install: device reports "
                          f"{installed_version}, expected {expected_version} "
                          f"(from {wheel_name}). The deploy did not take.")}

    # 6. Restart services — every enabled non-oneshot nimrum-* unit on this
    #    device, role-agnostic. Shared with TX's own restart path so a hardcoded
    #    service list can no longer strand a co-located unit. See
    #    restart_all_nimrum_units / RESTART_NIMRUM_UNITS_CMD.
    if restart:
        restart_all_nimrum_units(target, ssh_opts)

    return {"ok": True, "version": installed_version}


def deploy_fleet(
    wheel_paths: List[str],
    devices: List[Dict],
    ssh_opts: List[str],
    restart: bool = True,
    with_deps: bool = False,
    on_progress: Optional[Callable[[str, Dict], None]] = None,
    max_workers: int = 8,
) -> Dict[str, Dict]:
    """Deploy to multiple devices in parallel.

    Args:
        wheel_paths: List of available .whl file paths (both architectures).
        devices: List of dicts with at least 'hostname' and 'target' (SSH target).
                 Optional: 'roles' (list of role strings for service selection).
        ssh_opts: SSH options list.
        restart: Whether to restart services after install.
        with_deps: If True, install dependencies from wheel metadata.
        on_progress: Callback(hostname, result_dict) called after each device.
        max_workers: Max parallel deployments.

    Returns:
        Dict mapping hostname → result dict.
    """
    results = {}

    def _do_one(dev: Dict) -> Tuple[str, Dict]:
        hostname = dev["hostname"]
        target = dev["target"]

        result = deploy_single_device(
            target=target,
            wheel_paths=wheel_paths,
            ssh_opts=ssh_opts,
            restart=restart,
            with_deps=with_deps,
        )
        return (hostname, result)

    workers = min(max_workers, len(devices))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_do_one, dev): dev["hostname"] for dev in devices}
        for future in as_completed(futures):
            hostname, result = future.result()
            results[hostname] = result
            if on_progress:
                on_progress(hostname, result)

    return results


def fetch_device_registry_from_tx(
    tx_host: str = "",
    ssh_opts: Optional[List[str]] = None,
) -> Optional[Dict]:
    """Fetch nimrum_devices.yaml from TX device.

    Args:
        tx_host: TX hostname or IP. Defaults to NIMRUM_TX_HOST env var.
        ssh_opts: SSH options (uses defaults if None).

    Returns:
        Parsed YAML dict, or None on failure.
    """
    import yaml

    if not tx_host:
        tx_host = os.environ.get("NIMRUM_TX_HOST", "")
    if not tx_host:
        logger.error("No TX host specified and NIMRUM_TX_HOST not set")
        return None

    if ssh_opts is None:
        ssh_opts = SSH_OPTS_DEFAULT

    rc, stdout, stderr = _run_ssh(
        tx_host,
        "sudo cat /root/nimRum/nimrum_devices.yaml 2>/dev/null || "
        "cat ~/nimRum/nimrum_devices.yaml 2>/dev/null",
        ssh_opts,
        timeout=10,
    )
    if rc != 0 or not stdout.strip():
        logger.warning("Failed to fetch device registry from %s: %s",
                       tx_host, stderr.strip())
        return None

    try:
        data = yaml.safe_load(stdout)
        if isinstance(data, dict) and "devices" in data:
            return data
    except Exception as e:
        logger.warning("Failed to parse device registry: %s", e)

    return None
