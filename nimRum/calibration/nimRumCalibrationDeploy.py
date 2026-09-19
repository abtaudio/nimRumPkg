"""Deploy calibration level results to txConfig.yaml.

Reads the existing txConfig.yaml and updates volCal per client based on
measured level differences.

volCal is the physical correction: every speaker flat at the same level within
its own working band. Per-layout level (volStereoAdj / volMultiAdj) is format
and taste and is deliberately not touched here — a 5.1 LFE boost belongs there,
not in calibration.

volCal is stored in **wire volume steps**, not dB. One step is
VOL_DB_PER_STEP dB on every DAC in the fleet, so the dB the measurement
produces must be converted before it is written.
"""

import logging
import os
import re
from typing import Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# One wire volume step in dB. Mirrors VOL_DB_PER_STEP in nimRumTxCfg, which is
# the authority; duplicated rather than imported so this module stays free of
# the TX runtime's lirc/GPIO dependencies (same reason as
# LEVEL_REFERENCE_TYPES below).
VOL_DB_PER_STEP = 0.5

# Speaker types whose level may set the volCal reference. A band-limited
# speaker is measured in a different band (see LEVEL_BANDS in
# nimRumCalibrationEngine) so it must never set the reference for full-range
# ones. Defined here rather than imported to keep this module free of numpy.
LEVEL_REFERENCE_TYPES = ('full-range',)

# Where to find txConfig.yaml (same search order as WebUI/TX)
_TX_CONFIG_CANDIDATES = [
    os.path.join(os.getcwd(), "txConfig.yaml"),
    os.path.expanduser("~/nimRum/txConfig.yaml"),
    "/root/nimRum/txConfig.yaml",
]


def find_tx_config_path() -> Optional[str]:
    """Find the txConfig.yaml file path.

    Returns:
        Path to txConfig.yaml, or None if not found.
    """
    for path in _TX_CONFIG_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def compute_vol_adjustments(
    levels: Dict[str, float],
    reference: Optional[str] = None,
    max_adjustment: int = 10,
    speaker_types: Optional[Dict[str, str]] = None,
) -> Dict[str, int]:
    """Compute volCal values from measured relative levels.

    Every speaker should end up flat at the same level *within its own
    working band*. The adjustment is
    ``volCal = -(measured_level - reference_level)``, so louder speakers get a
    negative adjustment and quieter ones a positive adjustment.

    The measured levels must come from a measurement taken with the
    per-speaker terms bypassed (see nimRumTxCfg.getMeasurementVolume).
    Measuring through the existing volCal makes each run cancel the previous
    one.

    The reference is taken from full-range speakers only. A band-limited
    speaker (subwoofer) is measured in a different band, so letting it into
    the reference average drags every other speaker's volCal with it — that
    is what produced the "-10 on fronts" artefact.

    Note that comparing a subwoofer's 30-100 Hz level against a main's
    200-8000 Hz level is a *convention*, not a physical equality. It puts the
    sub in a sane ballpark; the rest is the user's per-layout level slider.
    No sub/mains offset is applied here on purpose.

    Args:
        levels: Dict mapping speaker name -> measured relative level in dB.
        reference: Optional speaker name to use as reference (0 dB). Overrides
                   the automatic reference-group average.
        max_adjustment: Maximum absolute adjustment in dB (default ±10).
                        Values beyond this are clamped.
        speaker_types: Optional dict mapping speaker name -> speaker type
                       ('full-range' or 'subwoofer'). Names not present are
                       treated as 'full-range'.

    Returns:
        Dict mapping speaker name -> volCal value in **wire volume steps**
        (VOL_DB_PER_STEP dB each), which is the unit txConfig.yaml stores.
    """
    adjustments, _ = compute_vol_adjustments_detailed(
        levels,
        reference=reference,
        max_adjustment=max_adjustment,
        speaker_types=speaker_types,
    )
    return adjustments


def compute_vol_adjustments_detailed(
    levels: Dict[str, float],
    reference: Optional[str] = None,
    max_adjustment: int = 10,
    speaker_types: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, int], Dict]:
    """Compute volCal values and report how they were derived.

    Same computation as compute_vol_adjustments(), but also returns
    diagnostics so callers can surface a suspect result instead of writing it
    silently. A clamped value means the measurement is out of range, not that
    the speaker needs ±10 dB.

    Args:
        levels: Dict mapping speaker name -> measured relative level in dB.
        reference: Optional speaker name to use as reference (0 dB).
        max_adjustment: Maximum absolute adjustment in dB.
        speaker_types: Optional dict mapping speaker name -> speaker type.

    Returns:
        Tuple of (adjustments, info). adjustments are in wire volume steps.
        info holds:
            reference_level: the dB level used as 0 dB
            reference_source: 'speaker:<name>', 'group-average' or 'all-average'
            reference_speakers: names that contributed to the reference
            clamped: names whose adjustment hit ±max_adjustment
            raw: unclamped, unrounded adjustment per speaker, in dB
    """
    info = {
        "reference_level": 0.0,
        "reference_source": "none",
        "reference_speakers": [],
        "clamped": [],
        "raw": {},
    }
    if not levels:
        return {}, info

    types = speaker_types or {}

    if reference and reference in levels:
        ref_level = levels[reference]
        info["reference_source"] = f"speaker:{reference}"
        info["reference_speakers"] = [reference]
    else:
        ref_names = [
            name for name in levels
            if types.get(name, 'full-range') in LEVEL_REFERENCE_TYPES
        ]
        if ref_names:
            info["reference_source"] = "group-average"
        else:
            # No full-range speaker measured. Fall back to all of them so the
            # relative result between same-type speakers is still meaningful.
            ref_names = list(levels)
            info["reference_source"] = "all-average"
            logger.warning(
                "compute_vol_adjustments: no full-range speaker in the "
                "measurement set, using the average of all %d speakers as "
                "reference", len(ref_names))
        ref_level = sum(levels[n] for n in ref_names) / len(ref_names)
        info["reference_speakers"] = sorted(ref_names)

    info["reference_level"] = ref_level

    adjustments = {}
    for name, level in levels.items():
        # Negative adjustment = speaker is too loud relative to reference
        adj_db = -(level - ref_level)
        info["raw"][name] = adj_db
        if adj_db > max_adjustment or adj_db < -max_adjustment:
            info["clamped"].append(name)
            logger.warning(
                "compute_vol_adjustments: %s wants %.1f dB, clamped to %+d dB. "
                "The measurement is out of range — check speaker type and mic "
                "placement rather than trusting this value.",
                name, adj_db, max_adjustment if adj_db > 0 else -max_adjustment)
        adj_db = max(-max_adjustment, min(max_adjustment, adj_db))
        # dB -> wire volume steps. Writing the dB value straight into volCal
        # applied only half the correction (since fixed).
        adjustments[name] = int(round(adj_db / VOL_DB_PER_STEP))

    info["clamped"] = sorted(info["clamped"])
    return adjustments, info


def deploy_levels_to_tx_config(
    adjustments: Dict[str, int],
    measured_levels: Optional[Dict[str, float]] = None,
    config_path: Optional[str] = None,
) -> Dict:
    """Write volCal values to txConfig.yaml.

    Reads the YAML as text to preserve comments and formatting, then
    uses targeted replacements for volCal values.

    Args:
        adjustments: Dict mapping speaker name -> volCal value (wire steps).
        measured_levels: Optional dict mapping speaker name -> original
                         measured relative level in dB. Written as a comment
                         next to volCal for UI display.
        config_path: Path to txConfig.yaml. If None, auto-detected.

    Returns:
        Dict with 'ok' on success, or 'error' on failure.
    """
    if config_path is None:
        config_path = find_tx_config_path()
    if config_path is None:
        return {"error": "txConfig.yaml not found"}

    try:
        with open(config_path, 'r') as f:
            content = f.read()
    except Exception as e:
        return {"error": f"Failed to read txConfig.yaml: {e}"}

    # Parse YAML to find client names and their order
    try:
        parsed = yaml.safe_load(content)
        clients = parsed.get("nimRumTXConfig", {}).get("clients", [])
    except Exception as e:
        return {"error": f"Failed to parse txConfig.yaml: {e}"}

    # Process line by line: find each client block and update values
    lines = content.split('\n')
    result_lines = []
    current_client = None
    current_client_indent = "      "
    vol_found_for_client = False
    last_field_idx = -1  # Track where to insert if volCal is missing

    for i, line in enumerate(lines):
        # Detect client name lines (e.g. "    - name: speaker1" or "      name: speaker1")
        name_match = re.match(r'^[\s-]+name:\s*(\S+)', line)
        if name_match:
            # Before moving to next client, check if previous client needs volCal inserted
            if current_client and current_client in adjustments and not vol_found_for_client:
                new_val = adjustments[current_client]
                # Insert after the last field line of the previous client
                if measured_levels and current_client in measured_levels:
                    meas = measured_levels[current_client]
                    cal_comment = f"# cal: measured {meas:+.1f} dB"
                else:
                    cal_comment = "# cal: auto-leveled"
                insert_line = f"{current_client_indent}volCal: {new_val} {cal_comment}"
                if last_field_idx >= 0 and last_field_idx < len(result_lines):
                    result_lines.insert(last_field_idx + 1, insert_line)
                else:
                    result_lines.append(insert_line)

            current_client = name_match.group(1)
            vol_found_for_client = False
            last_field_idx = len(result_lines)  # Will be updated as we process fields
            # Detect indent from the name line (strip the "- " prefix)
            indent_match = re.match(r'^(\s+)-\s+', line)
            if indent_match:
                current_client_indent = indent_match.group(1) + "  "
            else:
                current_client_indent = "      "

        # Track last field line for this client (non-empty, indented, key: value)
        if current_client and re.match(r'^\s+\w+', line) and line.strip():
            last_field_idx = len(result_lines)

        # Update volCal for current client
        vol_match = re.match(r'^(\s+)volCal:\s*(-?\d+)(.*)', line)
        if vol_match and current_client and current_client in adjustments:
            vol_found_for_client = True
            indent = vol_match.group(1)
            old_comment = vol_match.group(3).strip()
            new_val = adjustments[current_client]
            # Preserve any existing comment that isn't our auto-generated one
            comment = ""
            if old_comment and not old_comment.startswith("# cal:"):
                comment = " " + old_comment
            else:
                if measured_levels and current_client in measured_levels:
                    meas = measured_levels[current_client]
                    comment = f" # cal: measured {meas:+.1f} dB"
                else:
                    comment = f" # cal: auto-leveled"
            line = f"{indent}volCal: {new_val}{comment}"
            current_client_indent = indent  # Use same indent for future inserts

        result_lines.append(line)

    # Handle the last client if it needs volCal inserted
    if current_client and current_client in adjustments and not vol_found_for_client:
        new_val = adjustments[current_client]
        if measured_levels and current_client in measured_levels:
            meas = measured_levels[current_client]
            cal_comment = f"# cal: measured {meas:+.1f} dB"
        else:
            cal_comment = "# cal: auto-leveled"
        insert_line = f"{current_client_indent}volCal: {new_val} {cal_comment}"
        if last_field_idx >= 0 and last_field_idx < len(result_lines):
            result_lines.insert(last_field_idx + 1, insert_line)
        else:
            result_lines.append(insert_line)

    new_content = '\n'.join(result_lines)

    # Validate the modified YAML before writing
    try:
        validated = yaml.safe_load(new_content)
        if not isinstance(validated, dict):
            return {"error": "Modified YAML is not valid"}
    except yaml.YAMLError as e:
        return {"error": f"Modified YAML has syntax error: {e}"}

    try:
        with open(config_path, 'w') as f:
            f.write(new_content)
    except Exception as e:
        return {"error": f"Failed to write txConfig.yaml: {e}"}

    logger.info("Deployed level adjustments to %s: %s", config_path, adjustments)

    return {"ok": True, "path": config_path, "adjustments": adjustments}


# ---------------------------------------------------------------------------
# EQ profile deploy (push eq_config.yaml to RX device via SSH)
# ---------------------------------------------------------------------------

def deploy_calibration_profile(speaker_name: str, profile_path: str) -> Dict:
    """Deploy a calibration profile to an RX device via SSH.

    SCPs the profile YAML to the RX device, then signals the RX process
    to hot-reload the DSP config (no restart, no sync loss).

    Args:
        speaker_name: Hostname of the RX device (e.g. 'speaker1').
        profile_path: Local path to the calibration YAML file.

    Returns:
        Dict with 'ok' and 'speaker' on success, or 'error' on failure.
    """
    from nimRum.common.nimRumSSH import run_on_device, scp_to_device

    # Step 1: Ensure calibration directory exists on RX
    rc, _, stderr = run_on_device(
        speaker_name, "sudo mkdir -p /root/nimRum/calibration", timeout=10,
    )
    if rc != 0:
        logger.error("Deploy mkdir failed for %s: %s", speaker_name, stderr)
        return {"error": f"SSH mkdir failed: {stderr}"}

    # Step 2: SCP to tmp (where we have write access), then sudo move
    rc, err = scp_to_device(
        speaker_name, profile_path, "/tmp/nimrum_eq_config.yaml", timeout=15,
    )
    if rc != 0:
        logger.error("Deploy SCP failed for %s: %s", speaker_name, err)
        return {"error": f"SCP failed: {err}"}

    # Move from tmp to correct location with sudo
    rc, _, stderr = run_on_device(
        speaker_name,
        "sudo cp /tmp/nimrum_eq_config.yaml /root/nimRum/calibration/eq_config.yaml "
        "&& rm /tmp/nimrum_eq_config.yaml",
        timeout=10,
    )
    if rc != 0:
        logger.error("Deploy mv failed for %s: %s", speaker_name, stderr)
        return {"error": f"Move failed: {stderr}"}

    # Step 3: Signal RX to hot-reload DSP config (no restart, no sync loss)
    run_on_device(speaker_name, "sudo pkill -USR1 -f runNimRumRx", timeout=10)

    logger.info("Deployed calibration to %s, DSP reloading", speaker_name)
    return {"ok": True, "speaker": speaker_name}
