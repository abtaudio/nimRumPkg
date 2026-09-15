"""Network routes — WiFi link survey and (later) load tests.

Thin by design: all work lives in nimRum.tx_fleet.network_survey, which is also
a CLI tool (python -m nimRum.tx_fleet.network_survey). These handlers only
translate HTTP to a function call.
"""

import logging

from flask import Blueprint, jsonify, request, send_from_directory

from nimRum.tx_webui.app import STATIC_DIR, _device_registry, _SSH_KEY_PATH

logger = logging.getLogger(__name__)

network_bp = Blueprint('network_bp', __name__)

import nimRum.tx_fleet.network_survey as _survey

# routes_system also configures this, but do not depend on another blueprint's
# import order for it. nimRumSSH.configure is idempotent.
from nimRum.common.nimRumSSH import configure as _ssh_configure
_ssh_configure(_device_registry, _SSH_KEY_PATH)


@network_bp.route("/network")
def network_view():
    return send_from_directory(STATIC_DIR, "network.html")


@network_bp.route("/api/network/links")
def api_network_links():
    """Survey WiFi link speed and RSSI across all SRC/RX devices.

    On demand only — this fans out one SSH connection per device. Do not put
    it behind an auto-refresh timer; sixteen parallel sessions on a repeating
    schedule is real CPU load on a Pi Zero 2W.

    Link speed is reported as median plus min/max/spread rather than a single
    number. The movement is the point: a healthy link repeats the same value,
    and re-running and getting something different is information.

    Query params:
        devices: optional space-separated hostnames
        samples: optional polls per device
    """
    devices = request.args.get("devices", "").split() or None
    try:
        samples = int(request.args.get("samples", _survey.DEFAULT_SAMPLES))
    except ValueError:
        samples = _survey.DEFAULT_SAMPLES
    samples = max(1, min(samples, 15))

    results = _survey.collect_fleet_link_stats(devices=devices, samples=samples)

    median = next((r["fleet_median_mbps"] for r in results
                   if "fleet_median_mbps" in r), None)
    center = next((r["fleet_center_freq_mhz"] for r in results
                   if "fleet_center_freq_mhz" in r), None)
    width = next((r["fleet_width_mhz"] for r in results
                  if "fleet_width_mhz" in r), None)

    return jsonify({
        "devices": results,
        "fleet_median_mbps": median,
        "fleet_center_freq_mhz": center,
        "fleet_width_mhz": width,
        "slow_ratio": _survey.SLOW_RATIO,
        "slow_link_mbps": _survey.SLOW_LINK_MBPS,
        "unstable_spread": _survey.UNSTABLE_SPREAD,
        "samples": samples,
        "num_devices": len(results),
    })
