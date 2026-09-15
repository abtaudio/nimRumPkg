"""nimRumDeviceRegistry — Persistent device registry built from TX seen-devices.

TX C code tracks all devices that ping (configured or not). This module
persists that data to a YAML file and provides lookup APIs used by deploy,
calibration, and the WebUI.

The registry is the single source of truth for:
- Device IP addresses (no /etc/hosts or mDNS dependency)
- SSH username per device
- Device roles (rx, src, tx — bitmask, accumulated)
- nimRum version running on each device
- Last-seen timestamp

Usage:
    from nimRum.tx_fleet.device_registry import DeviceRegistry
    registry = DeviceRegistry("/path/to/nimrum_devices.yaml")
    registry.update_from_status(tx_status_dict)
    info = registry.get("speaker1")
    # info = {"ip": "192.0.2.10", "sshUser": "pi", "roles": ["rx"], ...}
"""

import logging
import os
import time
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Role bitmask values (match C defines in libPrezoCommPacketDefs.h)
ROLE_RX = 0x01
ROLE_SRC = 0x02
ROLE_TX = 0x04

_ROLE_NAMES = {ROLE_RX: "rx", ROLE_SRC: "src", ROLE_TX: "tx"}


def _roles_to_list(bitmask: int) -> List[str]:
    """Convert a role bitmask to a list of role names."""
    result = []
    for bit, name in _ROLE_NAMES.items():
        if bitmask & bit:
            result.append(name)
    return result


def _roles_from_list(names: List[str]) -> int:
    """Convert a list of role names to a bitmask."""
    bitmask = 0
    name_to_bit = {v: k for k, v in _ROLE_NAMES.items()}
    for name in names:
        bitmask |= name_to_bit.get(name, 0)
    return bitmask


class DeviceRegistry:
    """Persistent device registry backed by a YAML file."""

    def __init__(self, path: str = ""):
        """Initialize registry.

        Args:
            path: Path to the YAML file. If empty, uses
                  ~/nimRum/nimrum_devices.yaml or ./nimrum_devices.yaml.
        """
        if not path:
            # Try working directory first, then home
            cwd_path = os.path.join(os.getcwd(), "nimrum_devices.yaml")
            home_path = os.path.join(
                os.path.expanduser("~"), "nimRum", "nimrum_devices.yaml",
            )
            if os.path.isfile(cwd_path):
                path = cwd_path
            elif os.path.isfile(home_path):
                path = home_path
            else:
                # Default to cwd
                path = cwd_path

        self._path = path
        self._devices: Dict[str, dict] = {}
        self._dirty = False
        self._load()

    @property
    def path(self) -> str:
        return self._path

    def _load(self) -> None:
        """Load registry from YAML file."""
        if not os.path.isfile(self._path):
            self._devices = {}
            return
        try:
            with open(self._path, "r") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict) and "devices" in data:
                self._devices = data["devices"]
            else:
                self._devices = {}
        except Exception as e:
            logger.warning("Failed to load device registry %s: %s", self._path, e)
            self._devices = {}

    def save(self) -> None:
        """Save registry to YAML file (only if dirty)."""
        if not self._dirty:
            return
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            data = {"devices": self._devices}
            with open(self._path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=True)
            self._dirty = False
        except Exception as e:
            logger.error("Failed to save device registry %s: %s", self._path, e)

    def update_from_status(self, status: dict) -> None:
        """Update registry from TX status dict (seenDevices field).

        Call this periodically (e.g. every 5-10 seconds) from the TX
        status polling loop.
        """
        seen_devices = status.get("seenDevices", [])
        if not seen_devices:
            return

        for dev in seen_devices:
            hostname = dev.get("hostName", "").strip()
            if not hostname:
                continue

            ip = dev.get("ipAddr", "").strip()
            ssh_user = dev.get("sshUser", "").strip()
            roles_bitmask = dev.get("roles", 0)
            is_configured = dev.get("isConfigured", 0)
            last_seen_ns = dev.get("lastSeenNs", 0)

            # Get or create entry
            entry = self._devices.get(hostname, {})
            changed = False

            # Update IP if changed and non-empty
            if ip and entry.get("ip") != ip:
                entry["ip"] = ip
                changed = True

            # Update sshUser if provided
            if ssh_user and entry.get("ssh_user") != ssh_user:
                entry["ssh_user"] = ssh_user
                changed = True

            # Accumulate roles (OR with existing)
            existing_roles = _roles_from_list(entry.get("roles", []))
            new_roles = existing_roles | roles_bitmask
            new_roles_list = _roles_to_list(new_roles)
            if entry.get("roles") != new_roles_list:
                entry["roles"] = new_roles_list
                changed = True

            # Update configured flag
            if entry.get("is_configured") != bool(is_configured):
                entry["is_configured"] = bool(is_configured)
                changed = True

            # Version the device reports for its own nimRum component. For an RX that
            # is nimRumLib; for a SOURCE it is nimRumPkg, because a source links no
            # nimRumLib and never will. TX puts both in the same field, so anything
            # displaying it must label by role. "0.0.0" means nothing was reported and
            # is stored as empty rather than shown as a version.
            reported = str(dev.get("libVersion", "")).strip()
            if reported and reported != "0.0.0" and entry.get("version") != reported:
                entry["version"] = reported
                changed = True

            # Update last_seen (always, but only mark dirty on other changes)
            if last_seen_ns > 0:
                entry["last_seen"] = last_seen_ns

            if changed:
                self._devices[hostname] = entry
                self._dirty = True

    def get(self, hostname: str) -> Optional[dict]:
        """Get device info by hostname. Returns None if not found."""
        return self._devices.get(hostname)

    def get_ip(self, hostname: str) -> str:
        """Get IP address for a hostname. Returns empty string if unknown."""
        entry = self._devices.get(hostname)
        if entry:
            return entry.get("ip", "")
        return ""

    def get_ssh_user(self, hostname: str) -> str:
        """Get SSH user for a hostname. Returns 'pi' as default."""
        entry = self._devices.get(hostname)
        if entry:
            return entry.get("ssh_user", "pi")
        return "pi"

    def get_ssh_target(self, hostname: str) -> str:
        """Get SSH target string (user@ip) for a hostname."""
        ip = self.get_ip(hostname)
        if not ip:
            # Fallback: try hostname directly (relies on DNS/hosts)
            return hostname
        return ip

    def all_devices(self) -> Dict[str, dict]:
        """Return all devices in the registry."""
        return dict(self._devices)

    def get_devices_by_role(self, role: str) -> List[str]:
        """Get list of hostnames that have a specific role."""
        result = []
        for hostname, entry in self._devices.items():
            if role in entry.get("roles", []):
                result.append(hostname)
        return sorted(result)

    def is_online(self, hostname: str, timeout_s: float = 10.0) -> bool:
        """Check if a device was seen recently (within timeout_s seconds).

        Note: last_seen is in nanoseconds from CLOCK_REALTIME on TX.
        We compare against the latest seen timestamp in the registry
        to determine relative freshness.
        """
        entry = self._devices.get(hostname)
        if not entry:
            return False
        last_seen = entry.get("last_seen", 0)
        if last_seen <= 0:
            return False
        # Find the most recent last_seen across all devices
        max_seen = max(
            (d.get("last_seen", 0) for d in self._devices.values()),
            default=0,
        )
        if max_seen <= 0:
            return False
        # If this device's last_seen is within timeout of the newest, it's online
        age_ns = max_seen - last_seen
        return age_ns < (timeout_s * 1_000_000_000)

    def set_ssh_user(self, hostname: str, ssh_user: str) -> None:
        """Manually set SSH user for a device (override)."""
        if hostname not in self._devices:
            self._devices[hostname] = {}
        self._devices[hostname]["ssh_user"] = ssh_user
        self._dirty = True

    def remove(self, hostname: str) -> None:
        """Remove a device from the registry."""
        if hostname in self._devices:
            del self._devices[hostname]
            self._dirty = True
