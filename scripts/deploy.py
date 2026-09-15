#!/usr/bin/env python3
"""deploy_cli.py — CLI fleet deploy using shared nimRumFleetDeploy logic.

Replaces the bash deploy.sh script. Uses nimrum_devices.yaml from TX
as the single source of truth for the device list.

Usage:
    python3 scripts/deploy_cli.py                   Deploy to all RX/SRC devices
    python3 scripts/deploy_cli.py -d "4 42"         Deploy to specific devices
    python3 scripts/deploy_cli.py -b                Deploy + restart
    python3 scripts/deploy_cli.py -r                Deploy + reboot
    python3 scripts/deploy.py --tx myTX           Specify TX host (or set NIMRUM_TX_HOST)
    python3 scripts/deploy_cli.py --wheel-dir dist/ Specify wheel directory

The device list is fetched from TX's nimrum_devices.yaml. If TX is
unreachable, use -d flag to specify devices manually.
"""

import argparse
import glob
import os
import subprocess
import sys
import time

# Add parent package to path so we can import nimRum modules
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PKG_DIR)

from nimRum.common.nimRumFleetDeploy import (
    SSH_OPTS_DEFAULT,
    deploy_fleet,
    deploy_single_device,
    fetch_device_registry_from_tx,
    select_wheel_for_arch,
    _build_ssh_opts,
    _run_ssh,
)

# Terminal colors
GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
NC = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{NC} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{NC} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠{NC} {msg}")


def step(msg: str) -> None:
    print(f"{CYAN}►{NC} {msg}")


def find_wheels(wheel_dir: str) -> list:
    """Find all nimrum .whl files in the given directory."""
    pattern = os.path.join(wheel_dir, "nimrum*.whl")
    wheels = sorted(glob.glob(pattern))
    return wheels


def get_devices_from_tx(tx_host: str, ssh_opts: list, by_ip: bool = False) -> list:
    """Fetch device list from TX's nimrum_devices.yaml.

    Returns list of dicts:
    [{'hostname': ..., 'ip': ..., 'target': ..., 'roles': [...]}, ...]
    """
    data = fetch_device_registry_from_tx(tx_host, ssh_opts)
    if not data:
        return []

    devices = []
    for hostname, info in data.get("devices", {}).items():
        # Skip TX itself
        roles = info.get("roles", [])
        if "tx" in roles and "rx" not in roles and "src" not in roles:
            continue

        ip = (info.get("ip") or "").strip()
        sshUser = (info.get("ssh_user") or "").strip()

        # Reach devices by the hostname TX reports, not by its learned address.
        #
        # Both come from TX's registry at runtime, so neither reintroduces the
        # hardcoded `prezo{i}` construction that leaked the naming scheme into
        # this repo. But an IP defeats ~/.ssh/config, whose Host blocks match on
        # hostname: `<ip>` gets neither the right User nor the right IdentityFile,
        # and TX only carries ssh_user for the few devices whose discovery ping
        # reported one. Deploying by IP failed on 14 of 15 devices with
        # `Permission denied (publickey)` while plain `ssh <hostname>` worked on
        # every one of them - and it deployed to the 15th, which is worse than
        # failing on all of them, because a wire-breaking release then lands on
        # part of the fleet.
        #
        # --by-ip keeps the address path for a machine with no SSH config, DNS or
        # mDNS. It is a flag rather than a fallback on purpose: an implicit
        # fallback needs a name lookup to decide, and gethostbyname has no
        # timeout, so an absent device stalls the whole deploy for ~40 s.
        if by_ip and ip:
            target = f"{sshUser}@{ip}" if sshUser else ip
        else:
            target = hostname or ip

        devices.append({
            "hostname": hostname,
            "ip": ip,
            "target": target,
            "roles": roles,
        })

    return sorted(devices, key=lambda d: d["hostname"])


def _numeric_suffix(hostname: str) -> str:
    """Return the trailing digits of a hostname, or '' if it ends in a letter."""
    digits = ""
    for ch in reversed(hostname):
        if not ch.isdigit():
            break
        digits = ch + digits
    return digits


def filter_devices(devices: list, wanted: str) -> list:
    """Filter the TX device list by hostname or by trailing device number.

    A token matches either the full hostname, or — when the token is all digits
    — any hostname ending in that number. The numeric form keeps the short
    `-d "4 42"` spelling usable without this script knowing anything about the
    site's host-naming scheme.
    """
    tokens = wanted.split()
    if not tokens:
        return devices

    selected = []
    for dev in devices:
        hostname = dev["hostname"]
        suffix = _numeric_suffix(hostname)
        for tok in tokens:
            if tok == hostname or (tok.isdigit() and suffix and tok == suffix):
                selected.append(dev)
                break
    return selected


def main():
    parser = argparse.ArgumentParser(
        description="Deploy nimRum wheels to fleet devices.",
    )
    parser.add_argument(
        "-d", "--devices", type=str, default="",
        help='Device IDs to deploy to (e.g. "4 42"). Default: all RX/SRC.',
    )
    parser.add_argument(
        "-b", "--restart", action="store_true",
        help="Restart services after deploy.",
    )
    parser.add_argument(
        "-r", "--reboot", action="store_true",
        help="Reboot devices after deploy.",
    )
    parser.add_argument(
        "--tx", type=str, default=os.environ.get("NIMRUM_TX_HOST", ""),
        help="TX hostname for fetching device registry (default: $NIMRUM_TX_HOST).",
    )
    parser.add_argument(
        "--wheel-dir", type=str, default="",
        help="Directory containing .whl files (default: dist/).",
    )
    parser.add_argument(
        "--ssh-key", type=str, default="",
        help="SSH private key file (default: use ssh-agent/config).",
    )
    parser.add_argument(
        "--by-ip",
        action="store_true",
        help="Reach devices by TX's learned IP instead of the hostname. For a "
             "machine with no ~/.ssh/config, DNS or mDNS; note TX only knows "
             "ssh_user for some devices.",
    )
    parser.add_argument(
        "--with-deps", action="store_true",
        help="Install dependencies from wheel metadata (for new device setup).",
    )
    args = parser.parse_args()

    # Find wheels
    wheel_dir = args.wheel_dir or os.path.join(PKG_DIR, "dist")
    wheels = find_wheels(wheel_dir)
    if not wheels:
        fail(f"No wheels found in {wheel_dir}. Run ./build.sh first.")
        sys.exit(1)

    step(f"Wheels: {', '.join(os.path.basename(w) for w in wheels)}")

    # Build SSH options
    ssh_opts = _build_ssh_opts(args.ssh_key)

    # Get device list
    if not args.tx and not args.devices:
        fail("TX hostname not set. Use --tx <hostname> or set NIMRUM_TX_HOST env var.")
        fail("Alternatively, use -d flag to specify devices manually.")
        sys.exit(1)

    if args.tx:
        step(f"Fetching device list from {args.tx}...")
    devices = get_devices_from_tx(args.tx, ssh_opts, by_ip=args.by_ip)

    if not devices:
        fail(f"Cannot reach {args.tx} or no devices in registry.")
        fail("Use -d flag to specify devices manually, e.g.: -d \"4 42 9\"")
        sys.exit(1)

    # Filter if specific devices requested
    if args.devices:
        devices = filter_devices(devices, args.devices)
        if not devices:
            fail(f"No matching devices for: {args.devices}")
            sys.exit(1)

    noAddr = [d["hostname"] for d in devices if not d["ip"]]
    if noAddr:
        warn(f"No address in TX registry, falling back to hostname: "
             f"{', '.join(noAddr)}")

    listed = ", ".join(
        f"{d['hostname']} ({d['ip']})" if d["ip"] else d["hostname"]
        for d in devices
    )
    step(f"Deploying to {len(devices)} device(s): {listed}")
    print()

    # Deploy
    results = {}
    start_time = time.time()

    def on_progress(hostname: str, result: dict):
        results[hostname] = result
        if result.get("ok"):
            ok(f"{hostname:12s} deployed")
        else:
            fail(f"{hostname:12s} {result.get('error', 'unknown error')}")

    deploy_fleet(
        wheel_paths=wheels,
        devices=devices,
        ssh_opts=ssh_opts,
        restart=args.restart,
        with_deps=args.with_deps,
        on_progress=on_progress,
    )

    # Handle reboot (separate pass after deploy)
    if args.reboot:
        step("Rebooting devices...")
        for dev in devices:
            if results.get(dev["hostname"], {}).get("ok"):
                _run_ssh(dev["target"], "sudo reboot &>/dev/null &",
                         ssh_opts, timeout=5)

    # Also copy wheels to TX for WebUI fleet deploy
    if args.tx and not any(d["hostname"] == args.tx for d in devices):
        # Copy wheels to TX so WebUI can use them for future deploys
        tx_target = args.tx
        _run_ssh(tx_target, "mkdir -p /root/nimRum/wheels", ssh_opts, timeout=5)
        for w in wheels:
            from nimRum.common.nimRumFleetDeploy import _run_scp
            _run_scp(w, f"{tx_target}:/root/nimRum/wheels/", ssh_opts, timeout=15)

    # Summary
    elapsed = time.time() - start_time
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f" {CYAN}Deploy Summary{NC} ({elapsed:.1f}s)")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    fail_count = 0
    for dev in sorted(devices, key=lambda d: d["hostname"]):
        hostname = dev["hostname"]
        r = results.get(hostname, {"error": "not attempted"})
        if r.get("ok"):
            action = "deployed + restarted" if args.restart else \
                     "deployed + rebooting" if args.reboot else "deployed"
            print(f"  {GREEN}✓{NC} {hostname:12s} {action}")
        else:
            print(f"  {RED}✗{NC} {hostname:12s} {r.get('error', 'failed')}")
            fail_count += 1

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if fail_count > 0:
        warn(f"{fail_count} device(s) failed")
        sys.exit(1)
    else:
        ok("All devices deployed successfully")


if __name__ == "__main__":
    main()
