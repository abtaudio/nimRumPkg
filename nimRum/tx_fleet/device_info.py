"""nimRumDeviceInfo — Collect hardware/software info from fleet devices.

Queries each device via SSH and returns structured info (OS, kernel, arch,
DAC, WiFi adapter, nimRum version, etc.). Uses nimRumSSH for all remote
access and the device registry for device discovery.

Can be used as:
- Imported module: collect_fleet_info() returns a dict
- CLI tool: python -m nimRum.tx_fleet.device_info
"""

import json
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from nimRum.common.nimRumSSH import (
    get_registry,
    get_ssh_opts,
    resolve_target,
    run_on_device,
)

logger = logging.getLogger(__name__)

# Python script sent to each device to collect info in one SSH call
_COLLECT_SCRIPT = r"""
import os, re, json
info = {}
try:
    info['model'] = open('/proc/device-tree/model','rb').read().replace(b'\x00',b'').decode().strip()
except: info['model'] = ''
try:
    for line in open('/etc/os-release'):
        if line.startswith('PRETTY_NAME='):
            info['os'] = line.split('=',1)[1].strip().strip('"')
except: info['os'] = ''
info['kernel'] = os.popen('uname -r').read().strip()
info['arch'] = os.popen('uname -m').read().strip()
r = os.popen('aplay --version 2>/dev/null').read()
m = re.search(r'version (\S+)', r)
info['alsa'] = m.group(1) if m else ''
try:
    info['hat'] = open('/proc/device-tree/hat/product','rb').read().replace(b'\x00',b'').decode().strip()
except: info['hat'] = ''
info['sound'] = os.popen('aplay -l 2>/dev/null | grep "^card" | head -1').read().strip()
# WiFi adapter detection
wifi = ''
usb = os.popen('lsusb 2>/dev/null').read()
if '8812' in usb or '2357' in usb or 'DWA-182' in usb:
    wifi = 'RTL8812AU'
elif '8821' in usb or 'c811' in usb:
    wifi = 'RTL8821CU'
elif '148f:7612' in usb or 'MediaTek' in usb:
    wifi = 'MT7612U'
elif os.path.exists('/sys/class/net/wlan0'):
    drv = os.popen('readlink /sys/class/net/wlan0/device/driver 2>/dev/null').read().strip()
    if 'brcm' in drv:
        wifi = 'brcmfmac'
    elif 'sprdwl' in drv:
        wifi = 'UWE5622'
    elif drv:
        wifi = os.path.basename(drv)
    else:
        wifi = 'wlan0 (unknown)'
else:
    wifi = 'ethernet'
info['wifi'] = wifi
# nimRum version
v = os.popen('pip3 show nimrum 2>/dev/null').read()
m = re.search(r'Version:\s*(\S+)', v)
info['nimrum_version'] = m.group(1) if m else ''
# Uptime
info['uptime'] = os.popen('uptime -p 2>/dev/null').read().strip()
print(json.dumps(info))
"""


def collect_device_info(hostname: str, timeout: int = 15) -> Dict:
    """Collect hardware/software info from a single device.

    Args:
        hostname: Device hostname (e.g. 'speaker1').
        timeout: SSH timeout in seconds.

    Returns:
        Dict with device info, or {"error": "..."} on failure.
    """
    target = resolve_target(hostname)
    opts = get_ssh_opts(timeout=min(timeout, 5))
    cmd = ["ssh", *opts, target, "python3"]

    try:
        res = subprocess.run(
            cmd,
            input=_COLLECT_SCRIPT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if res.returncode != 0:
            return {"hostname": hostname, "error": res.stderr.strip() or "SSH failed"}
        info = json.loads(res.stdout.strip())
        info["hostname"] = hostname
        return info
    except subprocess.TimeoutExpired:
        return {"hostname": hostname, "error": "SSH timeout"}
    except json.JSONDecodeError as e:
        return {"hostname": hostname, "error": f"Parse error: {e}"}
    except Exception as e:
        return {"hostname": hostname, "error": str(e)}


def collect_fleet_info(
    devices: Optional[List[str]] = None,
    max_workers: int = 8,
    timeout: int = 15,
) -> List[Dict]:
    """Collect info from all (or specified) fleet devices in parallel.

    Args:
        devices: List of hostnames, or None for all from registry.
        max_workers: Max parallel SSH connections.
        timeout: Per-device SSH timeout.

    Returns:
        List of dicts, one per device (with 'hostname' key always set).
    """
    if devices is None:
        registry = get_registry()
        if not registry:
            return [{"error": "Device registry not configured"}]
        devices = registry.get_devices_by_role("rx")
        devices += [d for d in registry.get_devices_by_role("src")
                    if d not in devices]

    if not devices:
        return []

    results = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(devices))) as pool:
        futures = {
            pool.submit(collect_device_info, hostname, timeout): hostname
            for hostname in devices
        }
        for future in as_completed(futures):
            results.append(future.result())

    # Sort by hostname for consistent output
    results.sort(key=lambda d: d.get("hostname", ""))
    return results


def print_fleet_info(results: List[Dict]) -> None:
    """Print fleet info as a formatted table (CLI output)."""
    columns = ["Device", "Version", "Arch", "OS", "Kernel", "WiFi", "DAC/HAT", "Uptime"]
    rows = []
    for r in results:
        if "error" in r:
            rows.append([r.get("hostname", "?"), f"ERROR: {r['error']}",
                         "", "", "", "", "", ""])
            continue

        # Shorten OS name
        os_name = r.get("os", "?")
        if "trixie" in os_name.lower():
            os_short = "Trixie"
        elif "bullseye" in os_name.lower():
            os_short = "Bullseye"
        elif "bookworm" in os_name.lower():
            os_short = "Bookworm"
        else:
            os_short = os_name[:20]

        arch = r.get("arch", "?")
        bits = "arm64" if arch == "aarch64" else "armv7" if "arm" in arch else arch

        # DAC info
        hat = r.get("hat", "")
        sound = r.get("sound", "")
        if hat:
            dac = hat
        elif "pcm512x" in sound:
            dac = "pcm512x"
        elif "pcm5102" in sound:
            dac = "pcm5102a"
        elif "TAS5756" in sound:
            dac = "TAS5756M"
        elif sound and ":" in sound:
            dac = sound.split(":")[1].strip()[:25]
        else:
            dac = sound[:25] if sound else "?"

        rows.append([
            r.get("hostname", "?"),
            r.get("nimrum_version", "?"),
            bits,
            os_short,
            r.get("kernel", "?"),
            r.get("wifi", "?"),
            dac,
            r.get("uptime", "?").replace("up ", ""),
        ])

    # Calculate column widths
    widths = [len(c) for c in columns]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(val))

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*columns))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        print(fmt.format(*row))


def main() -> None:
    """CLI entry point — collect and display fleet device info."""
    import sys

    # Try to configure SSH from the standard locations
    from nimRum.tx_fleet.device_registry import DeviceRegistry
    from nimRum.common.nimRumSSH import configure

    registry = DeviceRegistry()
    # Find SSH key
    key_candidates = [
        os.path.join(os.getcwd(), "id_nimrum"),
        os.path.expanduser("~/nimRum/id_nimrum"),
    ]
    key_path = ""
    for k in key_candidates:
        if os.path.isfile(k):
            key_path = k
            break

    if not key_path:
        print("ERROR: Cannot find id_nimrum SSH key", file=sys.stderr)
        sys.exit(1)

    configure(registry, key_path)

    print(f"Collecting info from fleet ({registry.path})...")
    results = collect_fleet_info()
    if not results:
        print("No devices found in registry.")
        sys.exit(1)

    print()
    print_fleet_info(results)


if __name__ == "__main__":
    main()
