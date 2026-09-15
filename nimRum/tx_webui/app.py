#!/usr/bin/env python3
"""nimRumWebUI — Flask app creation, shared state, helpers, and core routes.

This module is the backbone of the nimRum WebUI. It creates the Flask app,
manages shared state (status cache, device registry), provides helper
functions for TX communication, and registers all route blueprints.
"""

import json
import logging
import os
import socket
import time
import threading

from flask import Flask, send_from_directory, jsonify, request

_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_DIR = os.path.dirname(_DIR)
STATIC_DIR = os.path.join(_DIR, "static")

logger = logging.getLogger(__name__)


def _find_ssh_key_path() -> str:
    """Find the nimRum SSH private key.

    Looks for ssh_key field in txConfig.yaml, or defaults to id_nimrum
    next to txConfig.yaml (cwd or ~/nimRum/).
    """
    import yaml as _yaml
    # Find txConfig.yaml
    candidates = [
        os.path.join(os.getcwd(), "txConfig.yaml"),
        os.path.expanduser("~/nimRum/txConfig.yaml"),
        "/root/nimRum/txConfig.yaml",
    ]
    for cfg_path in candidates:
        if os.path.isfile(cfg_path):
            cfg_dir = os.path.dirname(cfg_path)
            # Try to read ssh_key from config
            key_name = "id_nimrum"
            try:
                with open(cfg_path, 'r') as f:
                    data = _yaml.safe_load(f)
                tx_cfg = data.get("nimRumTXConfig", {})
                key_name = tx_cfg.get("ssh_key", key_name)
            except Exception:
                pass
            key_path = os.path.join(cfg_dir, key_name)
            if os.path.isfile(key_path):
                return key_path
    return ""


_SSH_KEY_PATH: str = _find_ssh_key_path()


def _find_tx_config_path() -> str | None:
    """Find the txConfig.yaml file path.

    Returns:
        Path to txConfig.yaml, or None if not found.
    """
    candidates = [
        os.path.join(os.getcwd(), "txConfig.yaml"),
        os.path.expanduser("~/nimRum/txConfig.yaml"),
        "/root/nimRum/txConfig.yaml",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


TX_STATUS_PORT: int = 53475
TX_TIMEOUT: float = 1.0

# How often the background thread refreshes the TX status cache. This sets the
# floor on how quickly an externally-triggered change (IR remote, volume knob,
# LanCtrl) becomes visible in the WebUI: the browser polls /api/status once per
# second, but it only ever sees whatever this cache last fetched. UI-initiated
# commands bypass it, because POST /api/status writes the response into the cache
# directly — which is why a click felt instant while the remote did not.
# The fetch is a local UDP round-trip to TX, so polling this often is cheap.
STATUS_POLL_INTERVAL_S: float = 0.5

# The device registry is written to disk on this cadence, independent of the poll
# interval above, so making the poll faster does not multiply flash writes.
REGISTRY_SAVE_INTERVAL_S: float = 30.0

app = Flask(__name__, static_folder=STATIC_DIR)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

_status_cache: dict = {"txOnline": False}
_status_lock = threading.Lock()

# Device registry — single source of truth for device IPs, SSH users, roles
from nimRum.tx_fleet.device_registry import DeviceRegistry
_device_registry = DeviceRegistry()


def _get_tx_status() -> dict | None:
    """Query TX for current status. Returns dict or None."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(TX_TIMEOUT)
        sock.sendto(
            json.dumps({"cmd": "getStatus"}).encode(),
            ("127.0.0.1", TX_STATUS_PORT),
        )
        data, _ = sock.recvfrom(65535)
        sock.close()
        return json.loads(data.decode())
    except Exception:
        return None


def _send_tx_command(cmd_dict: dict) -> dict | None:
    """Send a command to TX. Returns response dict or None."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(TX_TIMEOUT)
        sock.sendto(
            json.dumps(cmd_dict).encode(),
            ("127.0.0.1", TX_STATUS_PORT),
        )
        data, _ = sock.recvfrom(65535)
        sock.close()
        return json.loads(data.decode())
    except Exception:
        return None


def _status_updater() -> None:
    """Refresh the TX status cache in the background.

    Runs every STATUS_POLL_INTERVAL_S. The device registry is saved to disk on the
    slower REGISTRY_SAVE_INTERVAL_S cadence to limit flash wear.
    """
    save_every = max(1, round(REGISTRY_SAVE_INTERVAL_S / STATUS_POLL_INTERVAL_S))
    _save_counter = 0
    while True:
        st = _get_tx_status()
        with _status_lock:
            _status_cache.clear()
            if st is not None:
                st["txOnline"] = True
                _status_cache.update(st)
            else:
                _status_cache["txOnline"] = False

        # Feed device registry from seen devices
        if st is not None:
            _device_registry.update_from_status(st)
            _save_counter += 1
            if _save_counter >= save_every:
                _device_registry.save()
                _save_counter = 0

        time.sleep(STATUS_POLL_INTERVAL_S)


_updater_thread = threading.Thread(target=_status_updater, daemon=True)
_updater_thread.start()


# ---------------------------------------------------------------------------
# Core routes
# ---------------------------------------------------------------------------

GROUPS_FILE = os.path.join(_PKG_DIR, "nimRumWebUI_groups.json")


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "topology.html")


@app.route("/list")
def list_view():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/system")
def system_view():
    return send_from_directory(STATIC_DIR, "system.html")


@app.route("/config")
def config_view():
    return send_from_directory(STATIC_DIR, "config.html")


@app.route("/about")
def about_view():
    return send_from_directory(STATIC_DIR, "about.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/api/status")
def api_status():
    with _status_lock:
        return jsonify(_status_cache)


@app.route("/api/status", methods=["POST"])
def api_command():
    cmd = request.get_json(force=True)
    resp = _send_tx_command(cmd)
    if resp is None:
        return jsonify({"error": "TX offline"}), 503
    # Update cache so subsequent GETs reflect the latest state
    if resp and "clients" in resp:
        with _status_lock:
            _status_cache.clear()
            resp["txOnline"] = True
            _status_cache.update(resp)
    return jsonify(resp)


@app.route("/api/devices")
def api_devices():
    """Return all known devices from the device registry."""
    devices = _device_registry.all_devices()

    # Get live client status for configured devices
    with _status_lock:
        clients = _status_cache.get("clients", [])
        known_sources = _status_cache.get("sources", [])
    # Build a quick lookup: name → paused status
    client_online = {}
    for cli in clients:
        name = cli.get("name", "")
        # A configured client is online if not paused and status >= -1
        client_online[name] = (cli.get("paused", 1) == 0 and
                               cli.get("status", -10) >= -1)

    # Build a set of hostnames for known (online) SRC devices
    # SRC names in the sources list are sourceName, not hostname.
    # We need to match by hostname from registry. Use the seenDevices IPs.
    src_online_ips = set()
    for src in known_sources:
        # Sources are online if they appear in the list (TX filters by 60s timeout)
        src_online_ips.add(src.get("name", ""))

    result = []
    for hostname, info in sorted(devices.items()):
        roles = info.get("roles", [])
        # For configured devices, use live client status
        # For unconfigured devices, fall back to ping-based is_online
        if info.get("is_configured"):
            online = client_online.get(hostname, False)
        else:
            online = _device_registry.is_online(hostname)

        # SRC devices: also check if they appear in known sources list
        if "src" in roles and not online and known_sources:
            # If this hostname is in the registry with src role and TX
            # currently has known sources, it was seen recently by a ping.
            online = True

        # One field, two meanings, so say which. An RX reports its nimRumLib version;
        # a source reports its nimRumPkg version, because it links no nimRumLib. Without
        # the label a source's version reads as a wildly out-of-date library.
        version = info.get("version", "")
        component = ""
        if version:
            component = "nimRumPkg" if ("rx" not in roles) else "nimRumLib"

        entry = {
            "hostName": hostname,
            "ip": info.get("ip", ""),
            "sshUser": info.get("ssh_user", ""),
            "roles": roles,
            "version": version,
            "versionComponent": component,
            "isConfigured": info.get("is_configured", False),
            "online": online,
        }
        result.append(entry)
    return jsonify({"devices": result})


@app.route("/api/groups", methods=["GET"])
def api_groups_get():
    """Return current groups from TX status."""
    with _status_lock:
        groups = _status_cache.get("groups", None)
    if groups is not None:
        return jsonify({"groups": groups})
    return jsonify({"groups": None})


@app.route("/api/groups", methods=["POST"])
def api_groups_store():
    """Store group membership at TX (source of truth)."""
    data = request.get_json(force=True)
    resp = _send_tx_command({"cmd": "setGroups", "groups": data.get("groups", [])})
    if resp is None:
        return jsonify({"error": "TX offline"}), 503
    return jsonify(resp)


# ---------------------------------------------------------------------------
# Register Blueprints
# ---------------------------------------------------------------------------

from nimRum.tx_webui.routes_levels import levels_bp
from nimRum.tx_webui.routes_channelmap import channelmap_bp
from nimRum.tx_webui.routes_calibration import calibration_bp
from nimRum.tx_webui.routes_system import system_bp
from nimRum.tx_webui.routes_network import network_bp

app.register_blueprint(levels_bp)
app.register_blueprint(channelmap_bp)
app.register_blueprint(calibration_bp)
app.register_blueprint(system_bp)
app.register_blueprint(network_bp)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run(port: int = 80) -> None:
    """Start the WebUI server."""
    app.run(host="0.0.0.0", port=port, threaded=True)


def main() -> None:
    """Entry point for runNimRumWebUI console script."""
    import sys

    port = 80
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    print(f"nimRumWebUI starting on port {port}")
    run(port=port)


if __name__ == "__main__":
    main()
