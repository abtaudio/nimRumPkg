#!/usr/bin/env python3
"""nimRum WiFi link survey — read link speed and RSSI from every SRC/RX.

Link speed is the useful early-warning metric, and it is a better one than
RSSI: airtime is shared, so a client negotiating 39 Mbps occupies roughly ten
times the airtime per packet as one at 390 and degrades the whole fleet.

It is also not available where the other WiFi stats come from. Neither
/proc/net/wireless nor /sys/class/net/<if>/ exposes the negotiated bitrate, so
libPrezoHalNet_linkStatsUpdate() cannot read it from a file the way it reads
signal, quality and rx_errors. Getting it onto the wire needs netlink
NL80211_CMD_GET_STATION.

This module takes the other route: ask wpa_supplicant over SSH, on demand.
It is deliberately *not* continuous — a repeating fan-out to sixteen devices
costs real CPU on a Pi Zero 2W. Nothing here runs in the audio path; it is a
short-lived process on the device, triggered by hand.

Can be used as:
- Imported module: collect_fleet_link_stats() returns a list of dicts
- CLI tool: python -m nimRum.tx_fleet.network_survey
"""

import argparse
import logging
import os
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from nimRum.common.nimRumSSH import get_registry, resolve_target, get_ssh_opts

logger = logging.getLogger(__name__)

# wpa_cli is in /usr/sbin, which is not on the pi user's PATH — 'command -v
# wpa_cli' finds nothing. Always use an absolute path, and try both locations
# since Armbian and Raspberry Pi OS do not agree.
_WPA_CLI_PATHS = ("/usr/sbin/wpa_cli", "/sbin/wpa_cli")

# Default interface. RX resolves its own NIC from rxConfig, but signal_poll
# needs to be told, and every device in the fleet uses wlan0.
DEFAULT_INTERFACE = "wlan0"

# Below this the client is costing the fleet real airtime. Not used as a hard
# threshold: the OPis (UWE5622, 1x1) are legitimately 5-10x slower than the
# RPis, so an absolute limit paints them permanently red. Callers should prefer
# link_speed_ratio against the fleet median.
SLOW_LINK_MBPS = 50

# A link speed this far below the fleet median is worth flagging regardless of
# board class.
SLOW_RATIO = 0.25

# max/min within one run, beyond which the link is called unstable. Stepping
# between adjacent MCS rates is normal and gives roughly 1.1, so the threshold
# sits well above that. A steady receiver measures close to 1.00, while links
# stepping between rates run a little higher.
UNSTABLE_SPREAD = 1.5

# Seconds between polls. Back-to-back polls tend to return the same value and
# would make an unstable link look steady, which is the opposite of useful.
DEFAULT_SAMPLE_INTERVAL = 0.4

# Polls per device. Five at 0.4s spans ~2s, enough to see rate adaptation move.
DEFAULT_SAMPLES = 5


def _build_remote_command(interface: str, samples: int,
                          interval: float) -> str:
    """Build the shell command run on the device.

    Resolves wpa_cli once, then polls in a loop. The path resolution must not
    be inside the loop: an earlier version ended the probe with 'exit $?',
    which terminated the whole remote shell after the first poll, so every run
    silently collected exactly one sample and reported a spread of 1.00.

    Args:
        interface: Wireless interface name.
        samples: Number of polls.
        interval: Seconds to sleep between polls.

    Returns:
        A single shell command string, safe to pass to ssh.
    """
    candidates = " ".join(_WPA_CLI_PATHS)
    return (
        f'W=""; '
        f'for p in {candidates}; do '
        f'  if [ -x "$p" ]; then W="$p"; break; fi; '
        f'done; '
        f'if [ -z "$W" ]; then echo "NO_WPA_CLI" >&2; exit 127; fi; '
        f'for i in $(seq {samples}); do '
        f'  sudo -n "$W" -i {interface} signal_poll || exit $?; '
        f'  echo "---"; '
        f'  sleep {interval}; '
        f'done'
    )


def parse_signal_poll(output: str) -> Dict:
    """Parse the KEY=VALUE block that wpa_cli signal_poll prints.

    Fields vary by driver. The UWE5622 reports neither WIDTH nor
    CENTER_FRQ1, so those are absent rather than zero and must stay absent.

    FREQUENCY is the client's 20 MHz channel and CENTER_FRQ1 is the centre of
    the whole 40/80/160 MHz block, so the two normally differ. Whether a
    device's channel is plausible is decided by annotate_channel_consensus(),
    which needs the whole fleet — this function only parses.

    Args:
        output: Raw stdout from wpa_cli signal_poll.

    Returns:
        Dict with the fields that were present. Numeric fields are ints;
        'width_mhz' is parsed out of the "80 MHz" form.
    """
    raw = {}
    for line in output.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        raw[key.strip().upper()] = value.strip()

    result: Dict = {}

    def _int(key: str, out: str) -> None:
        if key in raw:
            m = re.match(r'^-?\d+', raw[key])
            if m:
                result[out] = int(m.group(0))

    _int("LINKSPEED", "link_speed_mbps")
    _int("RSSI", "rssi_dbm")
    _int("AVG_RSSI", "avg_rssi_dbm")
    _int("FREQUENCY", "frequency_mhz")
    _int("CENTER_FRQ1", "center_freq_mhz")
    _int("WIDTH", "width_mhz")

    # NOISE=9999 is wpa_supplicant's "not available" sentinel, not a reading.
    if "NOISE" in raw:
        m = re.match(r'^-?\d+', raw["NOISE"])
        if m and int(m.group(0)) != 9999:
            result["noise_dbm"] = int(m.group(0))

    return result


def annotate_channel_consensus(results: List[Dict]) -> List[Dict]:
    """Flag devices whose reported channel disagrees with the fleet.

    Every device associates with the same AP, so the fleet is its own
    reference. That is a better test than checking each device against its own
    CENTER_FRQ1, for two reasons found in the field:

    - Drivers that omit CENTER_FRQ1 and WIDTH (UWE5622) escaped a
      per-device check entirely, so several devices reporting a wrong frequency
      showed nothing while one device reporting the same wrong frequency was
      flagged.
    - FREQUENCY and CENTER_FRQ1 differing is *normal* on a wide channel:
      FREQUENCY is the client's 20 MHz channel, CENTER_FRQ1 the block centre.

    A worked example: the AP sat on an 80 MHz block centred at 5530 (channels
    100-112), most receivers reported FREQUENCY=5540 correctly, and the rest
    reported 5180 — channel 36, a different band segment. All of them were
    associated and carrying audio, so 5180 was a stale driver field rather than
    a real channel. Hence the wording: this says the *report* is untrustworthy,
    not that the link is broken.

    Args:
        results: List of dicts from collect_link_stats().

    Returns:
        The same list, with 'fleet_center_freq_mhz', 'fleet_width_mhz' and
        'channel_disagrees' filled in where a judgement was possible.
    """
    # Take the AP's block from the majority of devices that report one.
    blocks = [(r["center_freq_mhz"], r["width_mhz"]) for r in results
              if "center_freq_mhz" in r and "width_mhz" in r]
    if not blocks:
        return results

    center, width = Counter(blocks).most_common(1)[0][0]
    half = width / 2.0

    for r in results:
        if "error" in r or "frequency_mhz" not in r:
            continue
        r["fleet_center_freq_mhz"] = center
        r["fleet_width_mhz"] = width
        r["channel_disagrees"] = not (
            center - half <= r["frequency_mhz"] <= center + half)

    return results


def _short_ssh_error(stderr: str, returncode: int) -> str:
    """Reduce an SSH failure to one actionable line.

    Raw stderr went straight into the 'error' field, which is rendered as a single
    row in the Network tab. A changed host key produces fourteen lines of banner
    ("REMOTE HOST IDENTIFICATION HAS CHANGED", "SOMEONE IS DOING SOMETHING NASTY",
    a fingerprint, a remove-with command), so the row became an unreadable wall and
    the actual cause was buried in it. Classify the cases we know and truncate the
    rest.

    Note the host-key case is not a device fault at all: the survey runs on TX as
    root, so it is TX's /root/.ssh/known_hosts that is stale — typically after a
    device was reflashed or an address was reused. Say that, because the SSH banner
    says the opposite.

    Args:
        stderr: The failing command's stderr.
        returncode: Its exit status, used when stderr says nothing useful.

    Returns:
        A single line, no newlines, safe to render in a table cell.
    """
    text = (stderr or "").strip()

    if "REMOTE HOST IDENTIFICATION HAS CHANGED" in text or \
            "Host key verification failed" in text:
        return ("SSH host key changed — TX's known_hosts is stale for this address "
                "(reflashed device or reused IP), not a device fault")
    if "Permission denied" in text:
        return "SSH permission denied — check TX's key for this device"
    if "No route to host" in text or "Connection refused" in text:
        return "unreachable (powered off?)"
    if "Connection timed out" in text or "timed out" in text:
        return "SSH timeout"

    # Anything unclassified: first line only, bounded, newlines stripped.
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not first:
        return f"exit {returncode}"
    return first[:160]


def collect_link_stats(hostname: str, interface: str = DEFAULT_INTERFACE,
                       samples: int = DEFAULT_SAMPLES, timeout: int = 15,
                       interval: float = DEFAULT_SAMPLE_INTERVAL) -> Dict:
    """Read link stats from a single device.

    Link speed moves on two timescales and both are reported rather than
    smoothed away, because the movement is itself the signal:

    - Within a run, rate adaptation steps between MCS rates. A steady receiver
      returns near-identical samples while others wander over a range within a
      single run. A link that does not sit still is worth a look.
    - Across runs, it drifts further, a receiver's reported speed moving over a
      wide range within half an hour.

    So this returns min, max and spread alongside the median, and the caller is
    expected to show the range. Re-running and getting a different answer is
    information, not noise.

    Args:
        hostname: Device hostname.
        interface: Wireless interface to poll.
        samples: How many polls to take.
        timeout: SSH timeout in seconds. Must cover samples * interval.
        interval: Seconds between polls. Back-to-back polls tend to repeat the
                  same value and would make an unstable link look steady.

    Returns:
        Dict with the parsed fields plus 'hostname', or {'hostname', 'error'}.
        A wired device reports an error — that is expected, not a fault.
    """
    target = resolve_target(hostname)
    opts = get_ssh_opts(timeout=min(timeout, 5))
    samples = max(1, samples)

    # One SSH call, several spaced polls inside it — the connection dominates
    # the cost, and sleeping on the device costs nothing here.
    command = _build_remote_command(interface, samples, interval)
    cmd = ["ssh", *opts, target, command]

    # The remote sleeps have to fit inside the SSH timeout.
    budget = int(timeout + samples * interval + 2)

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=budget)
    except subprocess.TimeoutExpired:
        return {"hostname": hostname, "error": "SSH timeout"}
    except OSError as e:
        return {"hostname": hostname, "error": str(e)}

    if "NO_WPA_CLI" in res.stderr:
        return {"hostname": hostname, "error": "no wpa_cli (wired?)"}
    # wpa_cli exists but there is no control socket for the interface — a wired
    # device such as TX. Expected, not a fault, so say so plainly rather than
    # leaking wpa_supplicant's message.
    combined = res.stdout + res.stderr
    if "ctrl_ifname" in combined or "Failed to connect" in combined:
        return {"hostname": hostname,
                "error": f"no {interface} association (wired?)"}
    if res.returncode != 0:
        return {"hostname": hostname,
                "error": _short_ssh_error(res.stderr, res.returncode)}

    blocks = [b for b in res.stdout.split("---") if b.strip()]
    parsed = [parse_signal_poll(b) for b in blocks]
    parsed = [p for p in parsed if p]
    if not parsed:
        return {"hostname": hostname, "error": "no signal_poll output"}

    # Last sample carries the descriptive fields; the speeds carry the spread.
    result = dict(parsed[-1])
    speeds = sorted(p["link_speed_mbps"] for p in parsed
                    if "link_speed_mbps" in p)
    if speeds:
        result["link_speed_mbps"] = speeds[len(speeds) // 2]
        result["link_speed_min_mbps"] = speeds[0]
        result["link_speed_max_mbps"] = speeds[-1]
        result["link_speed_samples"] = speeds
        if speeds[0] > 0:
            # Round first, then compare, so the flag always agrees with the
            # number shown. 526/351 = 1.4986 displays as 1.50 and must not
            # appear unflagged next to it.
            spread = round(speeds[-1] / speeds[0], 2)
            result["link_speed_spread"] = spread
            result["link_unstable"] = bool(spread >= UNSTABLE_SPREAD)

    result["hostname"] = hostname
    result["interface"] = interface
    return result


def annotate_relative(results: List[Dict]) -> List[Dict]:
    """Add fleet-relative link speed judgement to each result.

    An absolute threshold is wrong here: the four OPis run 5-10x slower than
    the RPis by hardware, so a fixed limit marks them bad forever. The median
    of what is actually associated is the fairer reference.

    Args:
        results: List of dicts from collect_link_stats().

    Returns:
        The same list, with 'link_speed_ratio' and 'slow' filled in where a
        link speed is known. Mutated in place and returned for convenience.
    """
    speeds = sorted(r["link_speed_mbps"] for r in results
                    if r.get("link_speed_mbps"))
    median = speeds[len(speeds) // 2] if speeds else 0

    for r in results:
        speed = r.get("link_speed_mbps")
        if not speed or not median:
            continue
        ratio = speed / median
        r["link_speed_ratio"] = round(ratio, 2)
        r["slow"] = bool(ratio < SLOW_RATIO or speed < SLOW_LINK_MBPS)

    if median:
        for r in results:
            r["fleet_median_mbps"] = median

    return results


def collect_fleet_link_stats(
    devices: Optional[List[str]] = None,
    interface: str = DEFAULT_INTERFACE,
    samples: int = DEFAULT_SAMPLES,
    max_workers: int = 8,
    timeout: int = 15,
    interval: float = DEFAULT_SAMPLE_INTERVAL,
) -> List[Dict]:
    """Read link stats from all (or specified) SRC and RX devices in parallel.

    Args:
        devices: List of hostnames, or None for all RX + SRC from the registry.
        interface: Wireless interface to poll.
        samples: Polls per device. Min, max, median and spread are reported.
        max_workers: Max parallel SSH connections.
        timeout: Per-device SSH timeout.
        interval: Seconds between polls on the device.

    Returns:
        List of dicts sorted by hostname, one per device, each with 'hostname'
        set and either link stats or 'error'.
    """
    if devices is None:
        registry = get_registry()
        if not registry:
            return [{"error": "Device registry not configured"}]
        devices = registry.get_devices_by_role("rx")
        devices += [d for d in registry.get_devices_by_role("src")
                    if d not in devices]

    if not devices:
        return []

    results = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(devices))) as pool:
        futures = {
            pool.submit(collect_link_stats, host, interface, samples, timeout,
                        interval): host
            for host in devices
        }
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda d: d.get("hostname", ""))
    annotate_relative(results)
    return annotate_channel_consensus(results)


def print_link_stats(results: List[Dict]) -> None:
    """Print the survey as a formatted table (CLI output)."""
    columns = ["Device", "Link", "Range", "Spread", "vs median", "RSSI",
               "Chan", "Width", "Note"]
    rows = []

    for r in results:
        if "error" in r:
            rows.append([r.get("hostname", "?"), "-", "-", "-", "-", "-", "-",
                         "-", r["error"]])
            continue

        speed = r.get("link_speed_mbps")
        ratio = r.get("link_speed_ratio")
        lo = r.get("link_speed_min_mbps")
        hi = r.get("link_speed_max_mbps")
        spread = r.get("link_speed_spread")

        notes = []
        if r.get("slow"):
            notes.append("SLOW")
        if r.get("link_unstable"):
            notes.append("UNSTABLE")
        if r.get("channel_disagrees"):
            notes.append("stale chan report")

        if lo is not None and hi is not None:
            rng = f"{lo}" if lo == hi else f"{lo}-{hi}"
        else:
            rng = "n/a"

        rows.append([
            r.get("hostname", "?"),
            f"{speed} Mbps" if speed else "n/a",
            rng,
            f"{spread:.2f}" if spread else "n/a",
            f"{ratio:.2f}" if ratio else "n/a",
            f"{r['rssi_dbm']}" if "rssi_dbm" in r else "n/a",
            f"{r['frequency_mhz']}" if "frequency_mhz" in r else "n/a",
            f"{r['width_mhz']}" if "width_mhz" in r else "n/a",
            ", ".join(notes),
        ])

    widths = [len(c) for c in columns]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(val))

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*columns))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        print(fmt.format(*row))

    median = next((r["fleet_median_mbps"] for r in results
                   if "fleet_median_mbps" in r), None)
    if median:
        print(f"\nFleet median: {median} Mbps. "
              f"Flagged below {SLOW_RATIO:.0%} of median or {SLOW_LINK_MBPS} Mbps.")

    center = next((r["fleet_center_freq_mhz"] for r in results
                   if "fleet_center_freq_mhz" in r), None)
    width = next((r["fleet_width_mhz"] for r in results
                  if "fleet_width_mhz" in r), None)
    if center:
        stale = [r["hostname"] for r in results if r.get("channel_disagrees")]
        print(f"AP block: {center} MHz / {width} MHz wide "
              f"(from the fleet majority). Chan is each device's own reported "
              f"20 MHz channel, which sits inside that block.")
        if stale:
            print(f"'stale chan report' on {', '.join(stale)}: the driver is "
                  f"reporting a channel outside the AP's block. These devices "
                  f"are associated and passing audio, so it is the *report* "
                  f"that is stale, not the link.")

    print("\nLink is a spot reading. Range is the spread within this run; "
          "run it again and expect different numbers.\n"
          "A link that does not sit still is worth investigating — a healthy "
          "one repeats the same value.")


def _configure_ssh_or_exit() -> None:
    """Configure the SSH helper from the standard TX locations."""
    from nimRum.common.nimRumSSH import configure
    from nimRum.tx_fleet.device_registry import DeviceRegistry

    key_candidates = [
        os.path.join(os.getcwd(), "id_nimrum"),
        os.path.expanduser("~/nimRum/id_nimrum"),
    ]
    key_path = ""
    for k in key_candidates:
        if os.path.isfile(k):
            key_path = k
            break

    if not key_path:
        print("ERROR: Cannot find id_nimrum SSH key", file=sys.stderr)
        sys.exit(1)

    configure(DeviceRegistry(), key_path)


def main() -> None:
    """CLI entry point — survey the fleet and print a table."""
    parser = argparse.ArgumentParser(
        description="Read WiFi link speed and RSSI from every SRC/RX")
    parser.add_argument("-d", "--devices", default="",
                        help="Space-separated hostnames (default: all RX+SRC)")
    parser.add_argument("-i", "--interface", default=DEFAULT_INTERFACE,
                        help=f"Wireless interface (default: {DEFAULT_INTERFACE})")
    parser.add_argument("-n", "--samples", type=int, default=DEFAULT_SAMPLES,
                        help=f"Polls per device (default: {DEFAULT_SAMPLES}). "
                             "Min, max, median and spread are all reported.")
    parser.add_argument("--interval", type=float,
                        default=DEFAULT_SAMPLE_INTERVAL,
                        help="Seconds between polls "
                             f"(default: {DEFAULT_SAMPLE_INTERVAL})")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON instead of a table")
    args = parser.parse_args()

    _configure_ssh_or_exit()

    devices = args.devices.split() or None
    results = collect_fleet_link_stats(
        devices=devices, interface=args.interface, samples=args.samples,
        interval=args.interval)

    if not results:
        print("No devices found in registry.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        import json
        print(json.dumps(results, indent=2))
    else:
        print_link_stats(results)


if __name__ == "__main__":
    main()
