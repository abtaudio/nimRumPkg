#!/usr/bin/env python3
"""Calibration routes — speaker measurement, EQ fitting, and deployment."""

import json
import logging
import os
import threading
import time

from flask import Blueprint, send_from_directory, jsonify, request

from nimRum.tx_webui.app import (
    STATIC_DIR,
    _find_tx_config_path,
    _get_tx_status,
    _send_tx_command,
)

logger = logging.getLogger(__name__)

calibration_bp = Blueprint('calibration_bp', __name__)

# Calibration imports are lazy-loaded to avoid pulling numpy (~130MB) at startup.
# Only loaded when calibration features are actually used.
_CalibrationEngine = None
_EQBand = None
_deploy_calibration_profile = None


def _ensure_calibration_imports() -> None:
    """Lazy-load calibration dependencies (numpy-heavy) on first use."""
    global _CalibrationEngine, _EQBand, _deploy_calibration_profile
    if _CalibrationEngine is None:
        from nimRum.calibration import CalibrationEngine, EQBand, deploy_calibration_profile
        _CalibrationEngine = CalibrationEngine
        _EQBand = EQBand
        _deploy_calibration_profile = deploy_calibration_profile


# Calibration state
_cal_lock = threading.Lock()
_cal_state: dict = {
    "state": "idle",      # idle, measuring, computing, done, error
    "status": "Idle",
    "speaker": None,
    "result": None,
    "error": None,
    "bands": [],
}


def _disable_eq_for_measurement(speaker_name: str):
    """Disable EQ on an RX device before measurement (SSH + SIGUSR1 hot-reload).

    Returns the previous 'enabled' state (True/False/None if no config file).
    """
    from nimRum.common.nimRumSSH import run_on_device
    # Check current state first
    rc, stdout, _ = run_on_device(
        speaker_name,
        "sudo grep '^enabled:' /root/nimRum/calibration/eq_config.yaml 2>/dev/null || echo 'NONE'",
        timeout=10,
    )
    was_enabled = None
    if rc == 0 and 'NONE' not in stdout:
        was_enabled = 'true' in stdout.lower()

    if was_enabled:
        # Only need to disable if it was enabled
        run_on_device(
            speaker_name,
            "sudo sed -i 's/^enabled:.*/enabled: false/' "
            "/root/nimRum/calibration/eq_config.yaml; "
            "sudo pkill -USR1 -f runNimRumRx; true",
            timeout=10,
        )
    return was_enabled


def _enable_eq_for_measurement(speaker_name: str) -> None:
    """Re-enable EQ on an RX device after measurement (SSH + SIGUSR1 hot-reload).

    Only re-enables if EQ was actually enabled before the measurement.
    """
    from nimRum.common.nimRumSSH import run_on_device
    run_on_device(
        speaker_name,
        "if sudo test -f /root/nimRum/calibration/eq_config.yaml; then "
        "  sudo sed -i 's/^enabled:.*/enabled: true/' "
        "  /root/nimRum/calibration/eq_config.yaml; "
        "  sudo pkill -USR1 -f runNimRumRx; "
        "fi; true",
        timeout=10,
    )


def _calibration_worker(speaker_name: str, speaker_type: str = 'full-range',
                        mic_cal_path: str = '', verify_only: bool = False) -> None:
    """Background thread that runs the full calibration measurement flow.

    If verify_only=True, measures WITH EQ active (no disable/enable) and
    compares against the expected corrected response from the saved profile.
    """
    global _cal_state

    _ensure_calibration_imports()

    try:
        with _cal_lock:
            _cal_state["state"] = "measuring"
            _cal_state["status"] = "Preparing..."
            _cal_state["speaker"] = speaker_name
            _cal_state["speaker_type"] = speaker_type
            _cal_state["result"] = None
            _cal_state["error"] = None
            _cal_state["bands"] = []

        # Get current status to find the target speaker index
        st = _get_tx_status()
        clients = st.get("clients", []) if st else []
        target_idx = None
        for i, cli in enumerate(clients):
            if cli.get("name") == speaker_name:
                target_idx = i
                break

        if target_idx is None:
            with _cal_lock:
                _cal_state["state"] = "error"
                _cal_state["status"] = "Speaker not found"
                _cal_state["error"] = f"Speaker '{speaker_name}' not found in client list"
            return

        saved_scene_mute = st.get("sceneMute", {})
        # TX resolves this: main volume only, no volCal and no layout level.
        # Fall back to mainVolume if TX predates the field.
        meas_volume = st.get("measVolume", st.get("mainVolume", 20))

        # --- STEP 1: Disable EQ on target speaker (measure raw response) ---
        eq_was_enabled = None
        if not verify_only:
            eq_was_enabled = _disable_eq_for_measurement(speaker_name)
            time.sleep(0.3)  # SIGUSR1 hot-reload is near-instant

        try:
            # --- STEP 2: Inject chirp directly on target speaker ---
            # Signal injection zeroes all dataOut channels except the target's.
            # Mute all other speakers to prevent bleed (e.g. sub sharing same channel).
            chirp_duration = 10.0 if speaker_type == 'subwoofer' else 5.0

            # Mute all, then set the target to the measurement volume.
            #
            # An explicit val bypasses get_effective_volume, which is the point:
            # volCal (the Levels tab "Trim") and the layout level must NOT be in
            # the signal path here. Measuring through them means measuring the
            # correction the previous calibration applied, and volCal is then
            # rewritten from that reading — so every re-run cancels the previous
            # one, and taste/format offsets leak into a physical correction.
            for i in range(len(clients)):
                _send_tx_command({"cmd": "setVolume", "cliId": i, "val": 0})
            _send_tx_command({"cmd": "setVolume", "cliId": target_idx,
                              "val": meas_volume})

            _send_tx_command({
                "cmd": "injectSignal",
                "mode": "chirp",
                "targetIdx": target_idx,
                "volume": 100,
                "duration_s": chirp_duration,
                "oneShot": True,
            })
            time.sleep(0.2)

            with _cal_lock:
                _cal_state["status"] = "Playing chirp + recording..."

            # --- STEP 3: Start recording ---
            record_duration = chirp_duration + 1.5
            engine = _CalibrationEngine(sample_rate=48000)
            measurement = engine.measure(duration_s=record_duration)

        finally:
            # --- STEP 4: Always restore state (even on error/exception) ---
            _send_tx_command({"cmd": "stopInjection"})
            for i in range(len(clients)):
                _send_tx_command({"cmd": "setVolume", "cliId": i, "val": -1})
            if eq_was_enabled:
                _enable_eq_for_measurement(speaker_name)
            muted_ids = [int(k) for k, v in saved_scene_mute.items() if v]
            if muted_ids:
                _send_tx_command({"cmd": "setSceneMute", "cliIds": muted_ids, "mute": True})

        with _cal_lock:
            _cal_state["state"] = "computing"
            _cal_state["status"] = "Computing transfer function..."

        # Generate the reference chirp signal for transfer function computation
        from nimRum.tonegen.nimRumToneGen import get_chirp, get_silence
        import numpy as np

        silence = get_silence(0.5, 48000)
        chirp_ref = get_chirp(20.0, 20000.0, chirp_duration, 48000, 0.8)
        reference = np.concatenate([silence, chirp_ref, silence]).astype(np.float64)
        reference = reference / 2147483648.0  # Normalize int32 to float

        # Use first channel of recording
        recording = measurement.samples[:, 0]

        # Check signal level — reject if recording is just noise
        rec_peak = float(np.max(np.abs(recording)))
        # Threshold: -55 dBFS (0.0018). A chirp spreads energy across freqs,
        # so peak is lower than a sinus at the same volume.
        min_peak_level = 0.0018
        if rec_peak < min_peak_level:
            peak_dbfs = 20.0 * np.log10(rec_peak) if rec_peak > 0 else -120.0
            with _cal_lock:
                _cal_state["state"] = "error"
                _cal_state["error"] = (
                    f"Signal too low ({peak_dbfs:.0f} dBFS) at main volume "
                    f"{meas_volume}. Raise the main volume and try again — "
                    f"per-speaker Trim is bypassed during measurement and "
                    f"cannot help here."
                )
            return

        tf = engine.compute_transfer_function(reference, recording,
                                               mic_cal_path=mic_cal_path or None)

        with _cal_lock:
            _cal_state["status"] = "Fitting EQ bands..."

        bands = engine.auto_fit_eq(tf, max_bands=10, speaker_type=speaker_type)

        # Compute corrected response for display
        from nimRum.calibration.nimRumEQMath import apply_biquad_to_spectrum, biquad_coeffs
        corrected_db = tf.magnitude_db.copy()
        for band in bands:
            coeffs = biquad_coeffs(
                filter_type=band.type,
                freq=band.freq,
                gain=band.gain,
                q=band.q,
                sample_rate=48000,
            )
            correction = apply_biquad_to_spectrum(coeffs, tf.freqs, 48000)
            corrected_db = corrected_db + correction

        # Subsample for JSON transfer — use log-spaced frequencies for
        # good resolution at low frequencies (important for subwoofers)
        num_out_points = 500
        f_lo = max(15.0, tf.freqs[1])  # Skip DC
        f_hi = min(22000.0, tf.freqs[-1])
        freqs_log = np.geomspace(f_lo, f_hi, num_out_points)
        valid = tf.freqs > 0
        freqs_out = freqs_log.tolist()
        mag_out = np.interp(freqs_log, tf.freqs[valid], tf.magnitude_db[valid]).tolist()
        corr_out = np.interp(freqs_log, tf.freqs[valid], corrected_db[valid]).tolist()

        bands_out = [
            {"type": b.type, "freq": b.freq, "gain": b.gain, "q": b.q}
            for b in bands
        ]

        # Compute relative level for volume calibration. The band follows
        # speaker_type — a subwoofer measured in the full-range band reads as
        # mic noise floor.
        relative_level_db = engine.compute_relative_level(
            tf, speaker_type=speaker_type)

        with _cal_lock:
            _cal_state["state"] = "done"
            _cal_state["status"] = "Done"
            _cal_state["bands"] = bands_out
            _cal_state["result"] = {
                "freqs": freqs_out,
                "magnitude_db": mag_out,
                "corrected_db": corr_out,
                "bands": bands_out,
                "speaker": speaker_name,
                "speaker_type": speaker_type,
                "relative_level_db": round(relative_level_db, 2),
            }

            # In verify mode, include the expected curve from saved profile
            if verify_only:
                cal_dir = os.path.join(os.getcwd(), "calibration")
                plot_path = os.path.join(cal_dir, f"{speaker_name}_plot.json")
                if os.path.isfile(plot_path):
                    try:
                        with open(plot_path, 'r') as f:
                            saved_plot = json.load(f)
                        _cal_state["result"]["expected_db"] = saved_plot.get(
                            "corrected_db", [])
                    except Exception:
                        pass
                _cal_state["status"] = "Verification complete"

    except Exception as e:
        logger.exception("Calibration measurement failed")
        with _cal_lock:
            _cal_state["state"] = "error"
            _cal_state["status"] = f"Error: {e}"
            _cal_state["error"] = str(e)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@calibration_bp.route("/calibration")
def calibration_view():
    return send_from_directory(STATIC_DIR, "calibration.html")


@calibration_bp.route("/api/calibration/mic-files")
def api_calibration_mic_files():
    """List available microphone calibration files."""
    mic_dir = os.path.join(os.getcwd(), "calibration", "mic")
    files = []
    if os.path.isdir(mic_dir):
        for f in sorted(os.listdir(mic_dir)):
            if f.endswith('.txt'):
                files.append(f)
    return jsonify({"files": files})


@calibration_bp.route("/api/calibration/measure", methods=["POST"])
def api_calibration_measure():
    """Start a calibration measurement for a speaker (background thread)."""
    with _cal_lock:
        if _cal_state["state"] in ("measuring", "computing"):
            return jsonify({"error": "Measurement already in progress"}), 409

    data = request.get_json(force=True)
    speaker = data.get("speaker")
    if not speaker:
        return jsonify({"error": "No speaker specified"}), 400

    speaker_type = data.get("speaker_type", "full-range")
    mic_cal_file = data.get("mic_cal_file", "")

    # Resolve mic cal path
    mic_cal_path = ""
    if mic_cal_file:
        mic_dir = os.path.join(os.getcwd(), "calibration", "mic")
        candidate = os.path.join(mic_dir, mic_cal_file)
        if os.path.isfile(candidate):
            mic_cal_path = candidate

    thread = threading.Thread(
        target=_calibration_worker,
        args=(speaker, speaker_type, mic_cal_path),
        daemon=True,
        name="calibration",
    )
    thread.start()

    return jsonify({"status": "Measurement started", "speaker": speaker})


@calibration_bp.route("/api/calibration/verify", methods=["POST"])
def api_calibration_verify():
    """Start a verification measurement (EQ stays active, compares to expected)."""
    with _cal_lock:
        if _cal_state["state"] in ("measuring", "computing"):
            return jsonify({"error": "Measurement already in progress"}), 409
        # Reset state immediately so polls don't see stale "done"
        _cal_state["state"] = "measuring"
        _cal_state["status"] = "Starting verification..."
        _cal_state["result"] = None
        _cal_state["error"] = None

    data = request.get_json(force=True)
    speaker = data.get("speaker")
    if not speaker:
        return jsonify({"error": "No speaker specified"}), 400

    speaker_type = data.get("speaker_type", "full-range")
    mic_cal_file = data.get("mic_cal_file", "")

    mic_cal_path = ""
    if mic_cal_file:
        mic_dir = os.path.join(os.getcwd(), "calibration", "mic")
        candidate = os.path.join(mic_dir, mic_cal_file)
        if os.path.isfile(candidate):
            mic_cal_path = candidate

    thread = threading.Thread(
        target=_calibration_worker,
        args=(speaker, speaker_type, mic_cal_path, True),
        daemon=True,
        name="calibration-verify",
    )
    thread.start()

    return jsonify({"status": "Verification started", "speaker": speaker})


@calibration_bp.route("/api/calibration/status")
def api_calibration_status():
    """Return current calibration state and results if done."""
    with _cal_lock:
        resp = {
            "state": _cal_state["state"],
            "status": _cal_state["status"],
            "speaker": _cal_state["speaker"],
        }
        if _cal_state["state"] == "done" and _cal_state["result"]:
            resp["result"] = _cal_state["result"]
        if _cal_state["state"] == "error":
            resp["error"] = _cal_state["error"]
    return jsonify(resp)


@calibration_bp.route("/api/calibration/cancel", methods=["POST"])
def api_calibration_cancel():
    """Cancel an in-progress measurement. Discards any partial data."""
    with _cal_lock:
        if _cal_state["state"] not in ("measuring", "computing"):
            return jsonify({"ok": True, "message": "Nothing to cancel"})
        _cal_state["state"] = "idle"
        _cal_state["status"] = "Cancelled"
        _cal_state["result"] = None
        _cal_state["error"] = None
        _cal_state["bands"] = []

    # Restore volumes — get status and unmute all
    st = _get_tx_status()
    if st:
        clients = st.get("clients", [])
        for i in range(len(clients)):
            _send_tx_command({"cmd": "setVolume", "cliId": i, "val": -1})

    return jsonify({"ok": True, "message": "Measurement cancelled"})


@calibration_bp.route("/api/calibration/save", methods=["POST"])
def api_calibration_save():
    """Save the current calibration result as a YAML profile."""
    with _cal_lock:
        if _cal_state["state"] != "done" or not _cal_state["bands"]:
            return jsonify({"error": "No calibration result to save"}), 400
        bands_data = _cal_state["bands"]
        speaker = _cal_state["speaker"]
        speaker_type = _cal_state.get("speaker_type", "full-range")

    data = request.get_json(force=True)
    speaker = data.get("speaker", speaker)
    enabled = data.get("enabled", True)

    if not speaker:
        return jsonify({"error": "No speaker specified"}), 400

    _ensure_calibration_imports()
    bands = [
        _EQBand(type=b["type"], freq=b["freq"], gain=b["gain"], q=b["q"])
        for b in bands_data
    ]

    cal_dir = os.path.join(os.getcwd(), "calibration")
    engine = _CalibrationEngine(sample_rate=48000)

    # Get measured level from the measurement result
    with _cal_lock:
        result_data = _cal_state.get("result")
    relative_level_db = None
    if result_data:
        relative_level_db = result_data.get("relative_level_db")

    filepath = engine.save_profile(speaker, bands, path=cal_dir,
                                   speaker_type=speaker_type,
                                   relative_level_db=relative_level_db)

    # Also save the frequency response data for the UI plot
    with _cal_lock:
        result_data = _cal_state.get("result")
    if result_data:
        plot_path = os.path.join(cal_dir, f"{speaker}_plot.json")
        with open(plot_path, 'w') as f:
            json.dump(result_data, f)

    return jsonify({"ok": True, "path": filepath, "speaker": speaker})


@calibration_bp.route("/api/calibration/profile/<speaker>")
def api_calibration_get_profile(speaker):
    """Get the saved calibration profile for a speaker (if it exists)."""
    cal_dir = os.path.join(os.getcwd(), "calibration")
    profile_path = os.path.join(cal_dir, f"{speaker}_calibration.yaml")
    if not os.path.isfile(profile_path):
        return jsonify({"exists": False, "speaker": speaker})

    # Parse the YAML and return bands
    try:
        import yaml
        with open(profile_path, 'r') as f:
            data = yaml.safe_load(f)
        bands = data.get("filters", [])
        enabled = data.get("enabled", True)
        result = {
            "exists": True,
            "speaker": speaker,
            "enabled": enabled,
            "bands": bands,
        }
        # Include plot data if available
        plot_path = os.path.join(cal_dir, f"{speaker}_plot.json")
        if os.path.isfile(plot_path):
            with open(plot_path, 'r') as f:
                result["plot"] = json.load(f)
        return jsonify(result)
    except Exception as e:
        return jsonify({"exists": True, "speaker": speaker, "error": str(e)})


@calibration_bp.route("/api/calibration/profiles")
def api_calibration_all_profiles():
    """Get calibration status for all speakers (local profiles + live RX check)."""
    import yaml
    cal_dir = os.path.join(os.getcwd(), "calibration")
    st = _get_tx_status()
    clients = st.get("clients", []) if st else []

    result = []
    for cli in clients:
        name = cli.get("name", "")
        location = cli.get("location", "")
        profile_path = os.path.join(cal_dir, f"{name}_calibration.yaml")
        entry = {"name": name, "location": location, "hasProfile": False,
                 "enabled": False, "bands": 0, "deployedOnRx": False,
                 "rxEnabled": False, "cpuLoad": cli.get("cpuLoad", -1),
                 "speakerType": "full-range"}
        if os.path.isfile(profile_path):
            try:
                with open(profile_path, 'r') as f:
                    data = yaml.safe_load(f)
                entry["hasProfile"] = True
                entry["enabled"] = data.get("enabled", True)
                entry["bands"] = len(data.get("filters", []))
                entry["speakerType"] = data.get("speaker_type", "full-range")
            except Exception:
                pass

        # Live check: is the config deployed on the RX?
        try:
            from nimRum.common.nimRumSSH import run_on_device
            rc, output, _ = run_on_device(
                name,
                "if sudo test -f /root/nimRum/calibration/eq_config.yaml; then"
                "  echo DEPLOYED=1;"
                "  sudo grep 'enabled:' /root/nimRum/calibration/eq_config.yaml;"
                "else"
                "  echo DEPLOYED=0;"
                "fi",
                timeout=4,
            )
            if rc == 0:
                for line in output.strip().split('\n'):
                    if line.startswith('DEPLOYED=1'):
                        entry["deployedOnRx"] = True
                    elif 'enabled: true' in line:
                        entry["rxEnabled"] = True
        except Exception:
            pass

        result.append(entry)
    return jsonify({"speakers": result})


@calibration_bp.route("/api/calibration/speaker-type", methods=["POST"])
def api_calibration_speaker_type():
    """Set the speaker type for a device (persisted in the local profile)."""
    import yaml
    data = request.get_json(force=True)
    speaker = data.get("speaker")
    speaker_type = data.get("speaker_type", "full-range")

    if not speaker:
        return jsonify({"error": "No speaker specified"}), 400
    if speaker_type not in ("full-range", "subwoofer"):
        return jsonify({"error": "Invalid speaker_type"}), 400

    cal_dir = os.path.join(os.getcwd(), "calibration")
    os.makedirs(cal_dir, exist_ok=True)
    profile_path = os.path.join(cal_dir, f"{speaker}_calibration.yaml")

    if os.path.isfile(profile_path):
        # Update existing profile
        with open(profile_path, 'r') as f:
            profile_data = yaml.safe_load(f) or {}
        profile_data["speaker_type"] = speaker_type
        with open(profile_path, 'w') as f:
            yaml.dump(profile_data, f, default_flow_style=False, sort_keys=False)
    else:
        # Create a minimal profile with just the speaker type
        profile_data = {
            "speaker": speaker,
            "sample_rate": 48000,
            "enabled": True,
            "speaker_type": speaker_type,
            "filters": [],
        }
        with open(profile_path, 'w') as f:
            yaml.dump(profile_data, f, default_flow_style=False, sort_keys=False)

    return jsonify({"ok": True, "speaker": speaker, "speaker_type": speaker_type})


@calibration_bp.route("/api/calibration/enable", methods=["POST"])
def api_calibration_enable():
    """Toggle the enabled flag in eq_config.yaml on an RX device."""
    from nimRum.common.nimRumSSH import run_on_device

    data = request.get_json(force=True)
    speaker = data.get("speaker")
    enable = data.get("enable", True)

    if not speaker:
        return jsonify({"error": "No speaker specified"}), 400

    enabled_str = "true" if enable else "false"
    # Update config file and signal RX to reload DSP (no restart needed)
    rc, _, stderr = run_on_device(
        speaker,
        f"sudo sed -i 's/^enabled:.*/enabled: {enabled_str}/' "
        f"/root/nimRum/calibration/eq_config.yaml && "
        f"sudo pkill -USR1 -f runNimRumRx",
        timeout=10,
    )
    if rc != 0:
        return jsonify({"error": f"SSH failed: {stderr}"}), 500

    return jsonify({"ok": True, "speaker": speaker, "enabled": enable})


@calibration_bp.route("/api/calibration/deploy", methods=["POST"])
def api_calibration_deploy():
    """Deploy a saved calibration profile to the RX device + update txConfig.yaml."""
    data = request.get_json(force=True)
    speaker = data.get("speaker")
    if not speaker:
        return jsonify({"error": "No speaker specified"}), 400

    # Find the saved profile
    cal_dir = os.path.join(os.getcwd(), "calibration")
    profile_path = os.path.join(cal_dir, f"{speaker}_calibration.yaml")
    if not os.path.isfile(profile_path):
        return jsonify({"error": f"No saved profile for {speaker}"}), 404

    # Deploy EQ profile to RX in background thread (SSH can take a few seconds)
    def _do_deploy():
        _ensure_calibration_imports()
        result = _deploy_calibration_profile(speaker, profile_path)
        with _cal_lock:
            _cal_state["deploy_result"] = result

    with _cal_lock:
        _cal_state["deploy_result"] = None

    thread = threading.Thread(target=_do_deploy, daemon=True, name="deploy")
    thread.start()
    thread.join(timeout=20)  # Wait up to 20s for SSH

    with _cal_lock:
        result = _cal_state.get("deploy_result")

    if result is None:
        return jsonify({"error": "Deploy timed out"}), 504
    if "error" in result:
        return jsonify(result), 500

    # Also update txConfig.yaml with level from this speaker's plot data
    plot_path = os.path.join(cal_dir, f"{speaker}_plot.json")
    if os.path.isfile(plot_path):
        try:
            with open(plot_path, 'r') as f:
                plot_data = json.load(f)

            level = plot_data.get("relative_level_db")

            if level is not None:
                from nimRum.calibration.nimRumCalibrationDeploy import (
                    compute_vol_adjustments_detailed,
                    deploy_levels_to_tx_config,
                )
                # Gather levels from ALL measured speakers for relative calc
                all_levels, all_types = _gather_measured_levels(cal_dir)

                if all_levels:
                    adjustments, info = compute_vol_adjustments_detailed(
                        all_levels, speaker_types=all_types)
                    deploy_levels_to_tx_config(adjustments, measured_levels=all_levels)
                    result["level_adjustments"] = adjustments
                    result["level_info"] = info
                    if info["clamped"]:
                        result["level_warning"] = (
                            "Clamped at the ±10 dB limit: "
                            + ", ".join(info["clamped"])
                            + ". The measurement is out of range — check the "
                              "speaker type and mic placement."
                        )
        except Exception as e:
            logger.warning("Failed to update txConfig levels: %s", e)
            result["level_warning"] = str(e)

    return jsonify(result)


def _gather_measured_levels(cal_dir):
    """Read measured level and speaker type for every calibrated client.

    Reads the per-speaker plot.json files written during measurement. Both are
    needed together: a level is only comparable against another level measured
    in the same band, and the band follows the speaker type.

    Args:
        cal_dir: Directory holding the <speaker>_plot.json files.

    Returns:
        Tuple of (levels, speaker_types), each a dict keyed by speaker name.
    """
    st = _get_tx_status()
    clients = st.get("clients", []) if st else []

    levels = {}
    speaker_types = {}

    for cli in clients:
        name = cli.get("name", "")
        plot_path = os.path.join(cal_dir, f"{name}_plot.json")
        if not os.path.isfile(plot_path):
            continue
        try:
            with open(plot_path, 'r') as f:
                plot_data = json.load(f)
        except (OSError, ValueError) as e:
            logger.warning("Could not read %s: %s", plot_path, e)
            continue
        if "relative_level_db" in plot_data:
            levels[name] = plot_data["relative_level_db"]
            speaker_types[name] = plot_data.get("speaker_type", "full-range")

    return levels, speaker_types


@calibration_bp.route("/api/calibration/deploy-levels", methods=["POST"])
def api_calibration_deploy_levels():
    """Deploy measured volume levels (volCal) to txConfig.yaml.

    Expects JSON body with:
        levels: dict of {speaker_name: relative_level_db}
        reference: (optional) speaker name to use as 0 dB reference
        speaker_types: (optional) dict of {speaker_name: speaker_type}

    Computes volCal offsets so every speaker is flat at the same level within
    its own working band, and writes them to txConfig.yaml. Per-layout level
    (volStereoAdj / volMultiAdj) is not touched — a 5.1 LFE boost belongs
    there, not here.
    """
    from nimRum.calibration.nimRumCalibrationDeploy import (
        compute_vol_adjustments_detailed,
        deploy_levels_to_tx_config,
    )

    data = request.get_json(force=True)
    levels = data.get("levels")
    if not levels or not isinstance(levels, dict):
        return jsonify({"error": "No levels data provided"}), 400

    reference = data.get("reference")
    speaker_types = data.get("speaker_types")
    if not isinstance(speaker_types, dict):
        # Not supplied by the caller — recover it from the stored plot data.
        cal_dir = os.path.join(os.getcwd(), "calibration")
        _, speaker_types = _gather_measured_levels(cal_dir)

    # Compute adjustments
    adjustments, info = compute_vol_adjustments_detailed(
        levels, reference=reference, speaker_types=speaker_types)

    # Deploy to txConfig.yaml
    result = deploy_levels_to_tx_config(adjustments, measured_levels=levels)

    if "error" in result:
        return jsonify(result), 500

    result["level_info"] = info
    if info["clamped"]:
        result["level_warning"] = (
            "Clamped at the ±10 dB limit: " + ", ".join(info["clamped"])
            + ". The measurement is out of range — check the speaker type and "
              "mic placement. Note volCal is written in 0.5 dB volume steps, "
              "so ±10 dB is ±20."
        )
    return jsonify(result)


@calibration_bp.route("/api/calibration/levels")
def api_calibration_levels():
    """Get stored relative levels from all calibrated speakers.

    Reads the per-speaker plot.json files (saved during measurement) and
    returns the relative_level_db and speaker_type for each speaker that has
    been measured, plus the suggested volCal values and how they were derived.
    """
    cal_dir = os.path.join(os.getcwd(), "calibration")
    st = _get_tx_status()
    clients = st.get("clients", []) if st else []

    levels, speaker_types = _gather_measured_levels(cal_dir)

    # Compute suggested adjustments
    from nimRum.calibration.nimRumCalibrationDeploy import (
        compute_vol_adjustments_detailed,
    )
    if levels:
        adjustments, info = compute_vol_adjustments_detailed(
            levels, speaker_types=speaker_types)
    else:
        adjustments, info = {}, None

    return jsonify({
        "levels": levels,
        "speaker_types": speaker_types,
        "suggested_adjustments": adjustments,
        "level_info": info,
        "num_measured": len(levels),
        "num_clients": len(clients),
    })


# ---------------------------------------------------------------------------
# Import EQ endpoints
# ---------------------------------------------------------------------------

@calibration_bp.route("/api/calibration/preview-import", methods=["POST"])
def api_calibration_preview_import():
    """Preview imported EQ bands — compute and return frequency response curve."""
    _ensure_calibration_imports()
    import numpy as np
    from nimRum.calibration.nimRumEQMath import biquad_coeffs, apply_biquad_to_spectrum

    data = request.get_json(force=True)
    bands = data.get("bands", [])
    if not bands:
        return jsonify({"error": "No bands provided"}), 400

    # Generate frequency axis (log-spaced, 20–20kHz)
    freqs = np.logspace(np.log10(20), np.log10(20000), 400)
    # Start from 0 dB (flat)
    magnitude_db = np.zeros(len(freqs))

    # Apply each band to get the combined response
    for band in bands:
        band_type = band.get("type", "Peaking")
        if band_type.lower() == "raw":
            # Raw coefficients — compute frequency response directly
            b0 = band.get("b0", 1.0)
            b1 = band.get("b1", 0.0)
            b2 = band.get("b2", 0.0)
            a1 = band.get("a1", 0.0)
            a2 = band.get("a2", 0.0)
            coeffs = (b0, b1, b2, a1, a2)
        else:
            # Parametric band
            freq = band.get("freq", 1000.0)
            gain = band.get("gain", 0.0)
            q = band.get("q", 1.0)
            try:
                coeffs = biquad_coeffs(
                    filter_type=band_type, freq=freq, gain=gain,
                    q=q, sample_rate=48000,
                )
            except Exception:
                continue

        # Compute this band's response and add to total
        band_db = apply_biquad_to_spectrum(coeffs, freqs, 48000)
        magnitude_db += band_db

    return jsonify({
        "freqs": freqs.tolist(),
        "magnitude_db": magnitude_db.tolist(),
        "corrected_db": magnitude_db.tolist(),  # Same (no "before" reference)
    })


@calibration_bp.route("/api/calibration/deploy-import", methods=["POST"])
def api_calibration_deploy_import():
    """Deploy imported EQ bands to an RX device.

    Generates eq_config.yaml from the provided bands and deploys via SSH.
    """
    _ensure_calibration_imports()
    data = request.get_json(force=True)
    speaker = data.get("speaker")
    bands = data.get("bands", [])

    if not speaker:
        return jsonify({"error": "No speaker specified"}), 400
    if not bands:
        return jsonify({"error": "No bands provided"}), 400

    # Generate eq_config.yaml content
    lines = ["enabled: true", "filters:"]
    for band in bands:
        band_type = band.get("type", "Peaking")
        if band_type.lower() == "raw":
            lines.append(f"  - type: Raw")
            lines.append(f"    b0: {band.get('b0', 1.0)}")
            lines.append(f"    b1: {band.get('b1', 0.0)}")
            lines.append(f"    b2: {band.get('b2', 0.0)}")
            lines.append(f"    a1: {band.get('a1', 0.0)}")
            lines.append(f"    a2: {band.get('a2', 0.0)}")
        else:
            lines.append(f"  - type: {band_type}")
            lines.append(f"    freq: {band.get('freq', 1000.0)}")
            lines.append(f"    gain: {band.get('gain', 0.0)}")
            lines.append(f"    q: {band.get('q', 1.0)}")

    config_content = "\n".join(lines) + "\n"

    # Write to local file and deploy via SSH
    import tempfile
    from nimRum.common.nimRumSSH import run_on_device, scp_to_device

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as tmp:
        tmp.write(config_content)
        tmp_path = tmp.name

    try:
        # Ensure calibration dir exists on device
        run_on_device(speaker, "sudo mkdir -p /root/nimRum/calibration", timeout=10)

        # SCP the config file
        rc, err = scp_to_device(speaker, tmp_path,
                                "/root/nimRum/calibration/eq_config.yaml", timeout=10)
        if rc != 0:
            return jsonify({"error": f"SCP to {speaker} failed: {err}"}), 500

        # Signal RX to reload
        run_on_device(speaker, "sudo pkill -USR1 -f runNimRumRx; true", timeout=10)
    finally:
        os.unlink(tmp_path)

    return jsonify({"ok": True, "speaker": speaker, "bands": len(bands)})
