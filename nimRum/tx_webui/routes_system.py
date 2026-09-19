#!/usr/bin/env python3
"""System and config routes — fleet management, updates, and device configuration."""

import os

from flask import Blueprint, jsonify, request

from nimRum.tx_webui.app import _device_registry, _SSH_KEY_PATH

system_bp = Blueprint('system_bp', __name__)

# ---------------------------------------------------------------------------
# System API endpoints (software update, fleet deploy, config)
# ---------------------------------------------------------------------------

import nimRum.tx_fleet.pypkg_deploy as _sys_deploy
_sys_deploy.configure(_device_registry, _SSH_KEY_PATH)

import nimRum.tx_fleet.device_info as _device_info


@system_bp.route("/api/system/tx-version")
def api_system_tx_version():
    from nimRum.tx import libNimRumTx_py
    return jsonify({
        "version": _sys_deploy.get_tx_version(),
        "libVersion": libNimRumTx_py.c_libNimRumGetVersion(),
    })


@system_bp.route("/api/system/pypi-latest")
def api_system_pypi_latest():
    """Check latest nimrum version available on PyPI."""
    import urllib.request
    import json as _json
    try:
        url = "https://pypi.org/pypi/nimrum/json"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
            return jsonify({"version": data["info"]["version"]})
    except Exception as e:
        return jsonify({"version": "", "error": str(e)})

@system_bp.route("/api/system/tx-update", methods=["POST"])
def api_system_tx_update():
    data = request.get_json(force=True)
    source = data.get("source", "pypi")
    if source == "pypi":
        version = data.get("version", "")
        result = _sys_deploy.update_tx_from_pypi(version)
    elif source == "wheel":
        path = data.get("path", "")
        result = _sys_deploy.update_tx_from_wheel(path)
    else:
        result = {"error": "Unknown source"}
    return jsonify(result)


@system_bp.route("/api/system/upload-wheel", methods=["POST"])
def api_system_upload_wheel():
    if 'wheel' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files['wheel']
    if not f.filename.endswith('.whl'):
        return jsonify({"error": "File must be a .whl"}), 400
    save_path = os.path.join("/tmp", f.filename)
    f.save(save_path)
    return jsonify({"ok": True, "path": save_path})


@system_bp.route("/api/system/fleet-update", methods=["POST"])
def api_system_fleet_update():
    data = request.get_json(force=True)
    wheel_path = data.get("wheel_path", "")
    if not wheel_path:
        wheel_path = _sys_deploy.get_tx_wheel_path()
    if not wheel_path:
        return jsonify({"error": "No wheel available. Upload one or update TX from PyPI first."}), 400
    result = _sys_deploy.deploy_fleet(wheel_path)
    return jsonify(result)


@system_bp.route("/api/system/fleet-update/status")
def api_system_fleet_status():
    return jsonify(_sys_deploy.get_fleet_state())


@system_bp.route("/api/system/restart-fleet", methods=["POST"])
def api_system_restart_fleet():
    return jsonify(_sys_deploy.restart_fleet())


@system_bp.route("/api/system/reboot-fleet", methods=["POST"])
def api_system_reboot_fleet():
    return jsonify(_sys_deploy.reboot_fleet())


@system_bp.route("/api/system/restart-tx", methods=["POST"])
def api_system_restart_tx():
    _sys_deploy._restart_tx()
    return jsonify({"ok": True})


@system_bp.route("/api/system/reboot-tx", methods=["POST"])
def api_system_reboot_tx():
    """Reboot the TX device (full system reboot)."""
    import threading
    import subprocess

    def _do_reboot():
        subprocess.run(["sudo", "reboot"], timeout=10)

    threading.Timer(1.0, _do_reboot).start()
    return jsonify({"ok": True})


@system_bp.route("/api/system/device-info")
def api_system_device_info():
    """Collect hardware/software info from all fleet devices."""
    results = _device_info.collect_fleet_info()
    return jsonify({"devices": results})


@system_bp.route("/api/config/reference")
def api_config_reference():
    """Config field documentation, parsed from the per-config Markdown docs.

    Source: nimRum/rx/rxConfig.md, nimRum/tx/txConfig.md and
    nimRum/audio_source/audioSourceConfig.md, read via nimRumConfigDoc.
    """
    from nimRum.common.nimRumConfigDoc import get_all_references
    return jsonify(get_all_references())


@system_bp.route("/api/config/tx")
def api_config_tx_get():
    """Read txConfig.yaml."""
    candidates = [
        os.path.join(os.getcwd(), "txConfig.yaml"),
        os.path.expanduser("~/nimRum/txConfig.yaml"),
        "/root/nimRum/txConfig.yaml",
    ]
    for path in candidates:
        if os.path.isfile(path):
            with open(path, 'r') as f:
                return jsonify({"content": f.read(), "path": path})
    return jsonify({"error": "txConfig.yaml not found"}), 404


@system_bp.route("/api/config/tx", methods=["PUT"])
def api_config_tx_put():
    """Write txConfig.yaml (validates YAML syntax first)."""
    import yaml as _yaml
    data = request.get_json(force=True)
    content = data.get("content", "")
    path = data.get("path", "")
    if not path:
        return jsonify({"error": "No path specified"}), 400
    # Validate YAML syntax before writing
    try:
        parsed = _yaml.safe_load(content)
        if not isinstance(parsed, dict):
            return jsonify({"error": "YAML must be a mapping (dict), got " + type(parsed).__name__}), 400
    except _yaml.YAMLError as e:
        return jsonify({"error": f"YAML syntax error: {e}"}), 400
    try:
        with open(path, 'w') as f:
            f.write(content)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@system_bp.route("/api/config/device/<name>")
def api_config_device_get(name):
    """Fetch config from an RX/SRC device."""
    prefer = request.args.get("prefer", "rx")
    result = _sys_deploy.fetch_device_config(name, prefer=prefer)
    if "error" in result:
        return jsonify(result), 500
    return jsonify(result)


@system_bp.route("/api/config/device/<name>", methods=["PUT"])
def api_config_device_put(name):
    """Push config to an RX/SRC device."""
    data = request.get_json(force=True)
    filename = data.get("filename", "rxConfig.yaml")
    content = data.get("content", "")
    restart = data.get("restart", True)

    # Validate RX config before pushing: a non-integer in a ctypes-bound field
    # (e.g. staticDelay_us: 710us) is valid YAML but crash-loops the RX on
    # restart. Reject it here rather than taking the device down.
    if "rx" in filename.lower():
        from nimRum.rx.nimRumRxCfg import validate_rx_config_yaml
        ok, err = validate_rx_config_yaml(content)
        if not ok:
            return jsonify({"error": err}), 400

    result = _sys_deploy.push_device_config(name, filename, content, restart)
    if "error" in result:
        return jsonify(result), 500
    return jsonify(result)
