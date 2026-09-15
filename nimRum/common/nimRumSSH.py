"""nimRumSSH — Shared SSH helper for all remote device operations.

Single source of truth for SSH connectivity: key path, user resolution,
command execution, and file transfer. All modules that need to SSH to
devices should use this module.
"""

import logging
import os
import subprocess
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Module-level state, configured once at WebUI startup
_registry = None
_ssh_key_path = ""


def configure(registry, ssh_key_path: str) -> None:
    """Configure SSH module with device registry and key path.

    Called once at WebUI startup. All other functions use these settings.

    Args:
        registry: DeviceRegistry instance for IP/user lookup.
        ssh_key_path: Path to the SSH private key file.
    """
    global _registry, _ssh_key_path
    _registry = registry
    _ssh_key_path = ssh_key_path
    logger.info("SSH configured: key=%s, registry=%s",
                ssh_key_path, "yes" if registry else "no")


def get_registry():
    """Get the configured device registry."""
    return _registry


def get_key_path() -> str:
    """Get the configured SSH key path."""
    return _ssh_key_path


def get_ssh_opts(timeout: int = 5) -> list:
    """Build base SSH options list.

    Args:
        timeout: Connection timeout in seconds.

    Returns:
        List of SSH option arguments.
    """
    opts = ["-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={timeout}",
            "-o", "StrictHostKeyChecking=accept-new"]
    if _ssh_key_path and os.path.isfile(_ssh_key_path):
        opts = ["-i", _ssh_key_path] + opts
    return opts


def resolve_target(hostname: str) -> str:
    """Resolve a hostname to an SSH target string (user@ip).

    Uses device registry for IP and SSH user lookup.
    Falls back to bare hostname if registry has no IP.

    Args:
        hostname: Device hostname (e.g. 'speaker1').

    Returns:
        SSH target string (e.g. 'pi@192.0.2.10').
    """
    if _registry:
        ip = _registry.get_ip(hostname)
        user = _registry.get_ssh_user(hostname)
        if ip:
            return f"{user}@{ip}"
    return hostname


def run_on_device(hostname: str, command: str,
                  timeout: int = 10) -> Tuple[int, str, str]:
    """Run a command on a remote device via SSH.

    Args:
        hostname: Device hostname.
        command: Shell command to run on the device.
        timeout: Total operation timeout in seconds.

    Returns:
        Tuple of (returncode, stdout, stderr).
    """
    target = resolve_target(hostname)
    opts = get_ssh_opts(timeout=min(timeout, 5))

    cmd = ["ssh", *opts, target, command]
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


def run_on_device_sudo(hostname: str, command: str,
                       timeout: int = 10) -> Tuple[int, str, str]:
    """Run a command with sudo on a remote device via SSH.

    Args:
        hostname: Device hostname.
        command: Shell command to run with sudo.
        timeout: Total operation timeout in seconds.

    Returns:
        Tuple of (returncode, stdout, stderr).
    """
    return run_on_device(hostname, f"sudo bash -c '{command}'", timeout=timeout)


def scp_to_device(hostname: str, local_path: str,
                  remote_path: str, timeout: int = 30) -> Tuple[int, str]:
    """Copy a file to a remote device via SCP.

    Args:
        hostname: Device hostname.
        local_path: Local file path to copy.
        remote_path: Remote destination path (without user@host prefix).
        timeout: Total operation timeout in seconds.

    Returns:
        Tuple of (returncode, error_message).
    """
    target = resolve_target(hostname)
    opts = get_ssh_opts(timeout=min(timeout, 5))

    cmd = ["scp", *opts, local_path, f"{target}:{remote_path}"]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout)
        err = res.stderr.decode("utf-8", errors="replace").strip()
        return (res.returncode, err)
    except subprocess.TimeoutExpired:
        return (-1, "SCP timeout")
    except Exception as e:
        return (-1, str(e))


def scp_from_device(hostname: str, remote_path: str,
                    local_path: str, timeout: int = 30) -> Tuple[int, str]:
    """Copy a file from a remote device via SCP.

    Args:
        hostname: Device hostname.
        remote_path: Remote file path to copy.
        local_path: Local destination path.
        timeout: Total operation timeout in seconds.

    Returns:
        Tuple of (returncode, error_message).
    """
    target = resolve_target(hostname)
    opts = get_ssh_opts(timeout=min(timeout, 5))

    cmd = ["scp", *opts, f"{target}:{remote_path}", local_path]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout)
        err = res.stderr.decode("utf-8", errors="replace").strip()
        return (res.returncode, err)
    except subprocess.TimeoutExpired:
        return (-1, "SCP timeout")
    except Exception as e:
        return (-1, str(e))


def restart_rx(hostname: str) -> Tuple[int, str, str]:
    """Restart the RX process on a device via systemctl.

    Args:
        hostname: Device hostname.

    Returns:
        Tuple of (returncode, stdout, stderr).
    """
    cmd = "sudo systemctl restart nimrum-rx; true"
    return run_on_device(hostname, cmd, timeout=10)
