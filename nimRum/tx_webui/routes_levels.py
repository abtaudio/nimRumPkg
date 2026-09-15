#!/usr/bin/env python3
"""Levels routes — per-client volume and level calibration endpoints."""

import logging

from flask import Blueprint, send_from_directory, jsonify, request

from nimRum.tx_webui.app import (
    STATIC_DIR,
    _find_tx_config_path,
    _send_tx_command,
    _status_cache,
    _status_lock,
)

logger = logging.getLogger(__name__)

levels_bp = Blueprint('levels_bp', __name__)


@levels_bp.route("/levels")
def levels_view():
    return send_from_directory(STATIC_DIR, "levels.html")


@levels_bp.route("/api/levels")
def api_levels():
    """Return level settings for the Levels tab.

    mainVolume is live runtime state read from TX. startupVolume is the value in
    txConfig.yaml that TX comes up with after a restart — a different quantity,
    so it is reported separately and never overwrites the live value.

    Reads txConfig.yaml to extract volCal calibration reference comments.
    """
    config_path = _find_tx_config_path()
    result = {"mainVolume": 0, "startupVolume": 0, "clients": []}

    # Get live state from TX
    with _status_lock:
        st = _status_cache

    if st.get("txOnline"):
        result["mainVolume"] = st.get("mainVolume", 0)

    # Read config file for volCal comments and base values
    if config_path:
        try:
            with open(config_path, 'r') as f:
                lines = f.readlines()

            import yaml as _yaml
            with open(config_path, 'r') as f:
                parsed = _yaml.safe_load(f)

            clients_cfg = parsed.get("nimRumTXConfig", {}).get("clients", [])
            result["startupVolume"] = parsed.get("nimRumTXConfig", {}).get(
                "startupVolume", 0
            )

            # Extract # cal: comments from volCal lines
            import re
            cal_refs = {}  # client index -> cal ref string
            current_client_idx = -1
            for line in lines:
                if re.match(r'^[\s-]+name:\s*\S+', line):
                    current_client_idx += 1
                vol_cal_match = re.match(
                    r'^\s+volCal:\s*-?\d+\s*#\s*cal:\s*(.*)', line
                )
                if vol_cal_match and current_client_idx >= 0:
                    cal_refs[current_client_idx] = vol_cal_match.group(1).strip()

            for i, cli_cfg in enumerate(clients_cfg):
                layouts = cli_cfg.get("layouts", {})
                stereo_entry = layouts.get("stereo", layouts.get("default", {}))
                multi_entry = layouts.get("5.1(side)", layouts.get("default", {}))
                if stereo_entry is None:
                    stereo_entry = {}
                if multi_entry is None:
                    multi_entry = {}
                entry = {
                    "name": cli_cfg.get("name", ""),
                    "location": cli_cfg.get("location", ""),
                    "volStereoAdj": stereo_entry.get("level", 0),
                    "volMultiAdj": multi_entry.get("level", 0),
                    "volCal": cli_cfg.get("volCal", 0),
                    "calRef": cal_refs.get(i, ""),
                }
                result["clients"].append(entry)
        except Exception as e:
            logger.warning("Failed reading levels from config: %s", e)

    return jsonify(result)


@levels_bp.route("/api/levels/save", methods=["POST"])
def api_levels_save():
    """Save level settings to txConfig.yaml.

    Preserves comments and formatting via line-by-line replacement.
    """
    import re
    import yaml as _yaml

    data = request.get_json(force=True)
    new_startup_vol = data.get("startupVolume")
    new_clients = data.get("clients", [])

    config_path = _find_tx_config_path()
    if not config_path:
        return jsonify({"error": "txConfig.yaml not found"}), 404

    try:
        with open(config_path, 'r') as f:
            content = f.read()
    except OSError as e:
        return jsonify({"error": f"Failed to read config: {e}"}), 500

    lines = content.split('\n')
    result_lines = []
    current_client_idx = -1
    current_layout = None

    for line in lines:
        # Update startupVolume (what TX comes up with after a restart)
        startup_vol_match = re.match(r'^(\s+startupVolume:\s*)\d+(.*)', line)
        if startup_vol_match and new_startup_vol is not None:
            line = f"{startup_vol_match.group(1)}{new_startup_vol}{startup_vol_match.group(2)}"
            result_lines.append(line)
            continue

        # Detect client name
        name_match = re.match(r'^[\s-]+name:\s*(\S+)', line)
        if name_match:
            current_client_idx += 1
            current_layout = None

        # Detect layout name (e.g. "      stereo:" or "      5.1(side):")
        layout_match = re.match(r'^(\s{6,})(\S+):\s*$', line)
        if layout_match and current_client_idx >= 0:
            candidate = layout_match.group(2)
            if candidate in ("stereo", "5.1(side)", "default"):
                current_layout = candidate

        # Update level inside a layout block
        level_match = re.match(r'^(\s+level:\s*)-?\d+(.*)', line)
        if level_match and current_client_idx >= 0 and current_client_idx < len(new_clients):
            cli = new_clients[current_client_idx]
            if current_layout == "stereo":
                val = cli.get("volStereoAdj", 0)
                line = f"{level_match.group(1)}{val}{level_match.group(2)}"
            elif current_layout in ("5.1(side)", "default"):
                val = cli.get("volMultiAdj", 0)
                line = f"{level_match.group(1)}{val}{level_match.group(2)}"

        # Update volCal (preserve # cal: comment)
        vol_cal_match = re.match(r'^(\s+volCal:\s*)-?\d+(.*)', line)
        if vol_cal_match and current_client_idx >= 0 and current_client_idx < len(new_clients):
            cli = new_clients[current_client_idx]
            val = cli.get("volCal", 0)
            line = f"{vol_cal_match.group(1)}{val}{vol_cal_match.group(2)}"

        result_lines.append(line)

    new_content = '\n'.join(result_lines)

    # Validate before writing
    try:
        validated = _yaml.safe_load(new_content)
        if not isinstance(validated, dict):
            return jsonify({"error": "Modified YAML is not valid"}), 500
    except _yaml.YAMLError as e:
        return jsonify({"error": f"YAML syntax error: {e}"}), 500

    try:
        with open(config_path, 'w') as f:
            f.write(new_content)
    except OSError as e:
        return jsonify({"error": f"Failed to write config: {e}"}), 500

    # Deliberately does not touch the live volume: saving the startup value
    # should not change what is currently playing.
    return jsonify({"ok": True})
