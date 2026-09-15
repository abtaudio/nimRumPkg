#!/usr/bin/env python3
"""Channel map routes — per-client channel assignment and layout management."""

import logging

from flask import Blueprint, send_from_directory, jsonify, request

from nimRum.tx_webui.app import (
    STATIC_DIR,
    _find_tx_config_path,
    _status_cache,
    _status_lock,
)

logger = logging.getLogger(__name__)

channelmap_bp = Blueprint('channelmap_bp', __name__)


@channelmap_bp.route("/channelmap")
def channelmap_view():
    return send_from_directory(STATIC_DIR, "channelmap.html")


@channelmap_bp.route("/api/channelmap")
def api_channelmap():
    """Return channel mapping data for the UI tab."""
    config_path = _find_tx_config_path()
    result = {"activeLayout": "", "defaultLayout": "stereo",
              "channelLayouts": {}, "clients": [], "virtualChannels": []}

    # Get live state from TX status
    with _status_lock:
        st = _status_cache

    if st.get("txOnline"):
        result["activeLayout"] = st.get("activeLayout", "")
        result["defaultLayout"] = st.get("defaultLayout", "stereo")
        result["channelLayouts"] = st.get("channelLayouts", {})

    # Read config file for per-client layout data
    if config_path:
        try:
            import yaml as _yaml
            with open(config_path, 'r') as f:
                parsed = _yaml.safe_load(f)
            tx_cfg = parsed.get("nimRumTXConfig", {})
            clients_cfg = tx_cfg.get("clients", [])
            for cli in clients_cfg:
                if not cli.get("enabled", True):
                    continue
                layouts = cli.get("layouts")
                if layouts is None:
                    # Legacy format: convert stereoChannel/multiChannel to layouts
                    stereo_ch = cli.get("stereoChannel", [0])
                    multi_ch = cli.get("multiChannel", [0])
                    vol_stereo = cli.get("volStereoAdj", 0)
                    vol_multi = cli.get("volMultiAdj", 0)
                    if isinstance(stereo_ch, list):
                        stereo_ch = stereo_ch[0] if stereo_ch else 0
                    if isinstance(multi_ch, list):
                        multi_ch = multi_ch[0] if multi_ch else 0
                    layouts = {
                        "stereo": {"channel": stereo_ch, "level": vol_stereo},
                        "5.1(side)": {"channel": multi_ch, "level": vol_multi},
                    }
                result["clients"].append({
                    "name": cli.get("name", ""),
                    "location": cli.get("location", ""),
                    "layouts": layouts,
                })
            result["virtualChannels"] = tx_cfg.get("virtualChannels", [])
            if not result["channelLayouts"]:
                result["channelLayouts"] = tx_cfg.get("channelLayouts", {})
            if not result["defaultLayout"]:
                result["defaultLayout"] = tx_cfg.get("default_layout", "stereo")
        except Exception:
            pass

    return jsonify(result)


@channelmap_bp.route("/api/channelmap/save", methods=["POST"])
def api_channelmap_save():
    """Save channel mapping changes to txConfig.yaml.

    Expects JSON with:
      - channelLayouts: {layoutName: {channels: {num: role}}}
      - default_layout: str
      - clients: [{name, layouts: {layoutName: {channel, level}|null}}]
    """
    import yaml as _yaml

    data = request.get_json(force=True)
    config_path = _find_tx_config_path()
    if not config_path:
        return jsonify({"error": "txConfig.yaml not found"}), 404

    try:
        with open(config_path, 'r') as f:
            parsed = _yaml.safe_load(f)
    except Exception as e:
        return jsonify({"error": f"Failed to read config: {e}"}), 500

    tx_cfg = parsed.get("nimRumTXConfig", {})

    # Update channelLayouts
    if "channelLayouts" in data:
        tx_cfg["channelLayouts"] = data["channelLayouts"]

    # Update default_layout
    if "default_layout" in data:
        tx_cfg["default_layout"] = data["default_layout"]

    # Update per-client layouts
    if "clients" in data:
        clients_cfg = tx_cfg.get("clients", [])
        # Build name→index map
        name_map = {c.get("name"): i for i, c in enumerate(clients_cfg)}
        for cli_data in data["clients"]:
            name = cli_data.get("name")
            if name in name_map:
                idx = name_map[name]
                clients_cfg[idx]["layouts"] = cli_data.get("layouts", {})
                # Remove legacy fields if present (migrated to layouts)
                for legacy_key in ("stereoChannel", "multiChannel",
                                   "volStereoAdj", "volMultiAdj"):
                    clients_cfg[idx].pop(legacy_key, None)

    parsed["nimRumTXConfig"] = tx_cfg

    try:
        with open(config_path, 'w') as f:
            _yaml.dump(parsed, f, default_flow_style=False, sort_keys=False,
                       allow_unicode=True)
    except Exception as e:
        return jsonify({"error": f"Failed to write config: {e}"}), 500

    return jsonify({"ok": True, "message": "Saved. Restart TX to apply."})
