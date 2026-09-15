"""Tests for the WiFi link survey parsing and fleet-relative judgement.

The sample outputs are real captures. They cover the two driver behaviours
that matter: a full field set, and a driver reporting neither WIDTH nor
CENTER_FRQ1 (UWE5622).
"""

import pytest

from nimRum.tx_fleet.network_survey import (
    SLOW_LINK_MBPS,
    SLOW_RATIO,
    UNSTABLE_SPREAD,
    _build_remote_command,
    annotate_channel_consensus,
    annotate_relative,
    parse_signal_poll,
)

# MT7612U. Full field set.
POLL_RPI = """RSSI=-62
LINKSPEED=351
NOISE=9999
FREQUENCY=5540
WIDTH=80 MHz
CENTER_FRQ1=5530
AVG_RSSI=-61
"""

# UWE5622. Reports neither WIDTH nor CENTER_FRQ1.
POLL_OPI = """RSSI=-74
LINKSPEED=52
NOISE=9999
FREQUENCY=5180
"""

# Reports 5180 while the AP block is 5530/80. Stale driver field.
POLL_STALE_FREQ = """RSSI=-45
LINKSPEED=434
NOISE=9999
FREQUENCY=5180
WIDTH=80 MHz
CENTER_FRQ1=5530
"""


class TestParseSignalPoll:
    def test_rpi_full_field_set(self):
        r = parse_signal_poll(POLL_RPI)
        assert r["link_speed_mbps"] == 351
        assert r["rssi_dbm"] == -62
        assert r["avg_rssi_dbm"] == -61
        assert r["frequency_mhz"] == 5540
        assert r["center_freq_mhz"] == 5530
        assert r["width_mhz"] == 80

    def test_width_parsed_out_of_unit_suffix(self):
        assert parse_signal_poll("WIDTH=80 MHz")["width_mhz"] == 80

    def test_opi_missing_fields_stay_absent(self):
        """Absent must not become zero — the UI shows n/a for these."""
        r = parse_signal_poll(POLL_OPI)
        assert r["link_speed_mbps"] == 52
        assert r["rssi_dbm"] == -74
        assert "width_mhz" not in r
        assert "center_freq_mhz" not in r

    def test_noise_sentinel_is_dropped(self):
        """NOISE=9999 is wpa_supplicant's 'unavailable', not a reading."""
        assert "noise_dbm" not in parse_signal_poll(POLL_RPI)

    def test_real_noise_is_kept(self):
        assert parse_signal_poll("NOISE=-95")["noise_dbm"] == -95

    def test_frequency_fields_parsed_but_not_judged(self):
        """The parser only parses; the fleet decides what is plausible."""
        r = parse_signal_poll(POLL_RPI)
        assert r["frequency_mhz"] == 5540
        assert r["center_freq_mhz"] == 5530
        assert "channel_disagrees" not in r

    def test_stale_frequency_not_judged_by_the_parser_either(self):
        r = parse_signal_poll(POLL_STALE_FREQ)
        assert r["frequency_mhz"] == 5180
        assert "channel_disagrees" not in r

    def test_empty_output(self):
        assert parse_signal_poll("") == {}

    def test_garbage_lines_ignored(self):
        r = parse_signal_poll("Selected interface 'wlan0'\nLINKSPEED=100\n\n")
        assert r == {"link_speed_mbps": 100}

    def test_keys_are_case_insensitive(self):
        assert parse_signal_poll("linkspeed=200")["link_speed_mbps"] == 200

    def test_negative_and_positive_values(self):
        r = parse_signal_poll("RSSI=-62\nLINKSPEED=351")
        assert r["rssi_dbm"] < 0
        assert r["link_speed_mbps"] > 0


class TestAnnotateRelative:
    def test_median_and_ratio(self):
        results = [
            {"hostname": "a", "link_speed_mbps": 390},
            {"hostname": "b", "link_speed_mbps": 390},
            {"hostname": "c", "link_speed_mbps": 780},
        ]
        annotate_relative(results)
        assert results[0]["fleet_median_mbps"] == 390
        assert results[0]["link_speed_ratio"] == 1.0
        assert results[2]["link_speed_ratio"] == 2.0

    def test_slow_client_flagged_by_ratio(self):
        """A receiver at 39 Mbps against a fleet median of 390."""
        results = [
            {"hostname": "rx4", "link_speed_mbps": 390},
            {"hostname": "rx10", "link_speed_mbps": 390},
            {"hostname": "rx42", "link_speed_mbps": 39},
        ]
        annotate_relative(results)
        assert results[2]["slow"] is True
        assert results[0]["slow"] is False

    def test_opi_not_flagged_when_the_fleet_is_all_opi(self):
        """An absolute limit would paint these red forever; relative does not."""
        results = [
            {"hostname": "rx40", "link_speed_mbps": 78},
            {"hostname": "rx41", "link_speed_mbps": 65},
            {"hostname": "rx43", "link_speed_mbps": 65},
        ]
        annotate_relative(results)
        assert all(r["link_speed_ratio"] >= SLOW_RATIO for r in results)
        assert all(r["slow"] is False for r in results)

    def test_absolute_floor_still_catches_the_outlier_among_opis(self):
        """39 Mbps is bad even against its own board class."""
        results = [
            {"hostname": "rx40", "link_speed_mbps": 78},
            {"hostname": "rx41", "link_speed_mbps": 65},
            {"hostname": "rx42", "link_speed_mbps": 39},
        ]
        annotate_relative(results)
        assert results[2]["slow"] is True
        assert results[2]["link_speed_mbps"] < SLOW_LINK_MBPS
        assert results[0]["slow"] is False

    def test_absolute_floor_catches_a_uniformly_slow_fleet(self):
        results = [
            {"hostname": "a", "link_speed_mbps": 30},
            {"hostname": "b", "link_speed_mbps": 30},
        ]
        annotate_relative(results)
        assert all(r["slow"] is True for r in results)

    def test_errored_devices_are_left_alone(self):
        results = [
            {"hostname": "a", "link_speed_mbps": 390},
            {"hostname": "tx", "error": "no wpa_cli (wired?)"},
        ]
        annotate_relative(results)
        assert "slow" not in results[1]
        assert "link_speed_ratio" not in results[1]

    def test_no_speeds_at_all(self):
        results = [{"hostname": "a", "error": "SSH timeout"}]
        annotate_relative(results)
        assert results == [{"hostname": "a", "error": "SSH timeout"}]

    def test_returns_the_same_list(self):
        results = [{"hostname": "a", "link_speed_mbps": 100}]
        assert annotate_relative(results) is results


class TestChannelConsensus:
    """All devices share one AP, so the fleet is its own reference.

    In the captured snapshot the AP was on 5530 / 80 MHz (channels 100-112).
    Most receivers reported FREQUENCY=5540 correctly; the rest reported 5180
    (channel 36) while associated and passing audio.
    """

    @staticmethod
    def _fleet():
        return [
            # MT7612U — correct, 5540 inside the 5530/80 block.
            {"hostname": "rxa", "frequency_mhz": 5540,
             "center_freq_mhz": 5530, "width_mhz": 80},
            {"hostname": "rxe", "frequency_mhz": 5540,
             "center_freq_mhz": 5530, "width_mhz": 80},
            {"hostname": "rxc", "frequency_mhz": 5540,
             "center_freq_mhz": 5530, "width_mhz": 80},
            # brcmfmac twin that agrees.
            {"hostname": "rxi", "frequency_mhz": 5540,
             "center_freq_mhz": 5530, "width_mhz": 80},
            # brcmfmac twin that does not.
            {"hostname": "rxh", "frequency_mhz": 5180,
             "center_freq_mhz": 5530, "width_mhz": 80},
            # UWE5622 — no WIDTH, no CENTER_FRQ1, stale FREQUENCY.
            {"hostname": "rxf", "frequency_mhz": 5180},
            {"hostname": "rxg", "frequency_mhz": 5180},
            # Wired.
            {"hostname": "srcbox", "error": "no wlan0 association (wired?)"},
        ]

    def test_block_taken_from_the_majority(self):
        r = annotate_channel_consensus(self._fleet())
        agreeing = next(d for d in r if d["hostname"] == "rxa")
        assert agreeing["fleet_center_freq_mhz"] == 5530
        assert agreeing["fleet_width_mhz"] == 80

    def test_devices_inside_the_block_are_not_flagged(self):
        r = annotate_channel_consensus(self._fleet())
        for name in ("rxa", "rxe", "rxc", "rxi"):
            d = next(x for x in r if x["hostname"] == name)
            assert d["channel_disagrees"] is False, name

    def test_opis_are_now_flagged_too(self):
        """The bug: they escaped the check by omitting CENTER_FRQ1/WIDTH."""
        r = annotate_channel_consensus(self._fleet())
        for name in ("rxf", "rxg"):
            d = next(x for x in r if x["hostname"] == name)
            assert d["channel_disagrees"] is True, name

    def test_p50_still_flagged(self):
        r = annotate_channel_consensus(self._fleet())
        d = next(x for x in r if x["hostname"] == "rxh")
        assert d["channel_disagrees"] is True

    def test_same_reported_channel_gets_the_same_verdict(self):
        """The stale-report group all read 5180 and must agree in the output."""
        r = annotate_channel_consensus(self._fleet())
        verdicts = {d["channel_disagrees"] for d in r
                    if d.get("frequency_mhz") == 5180}
        assert verdicts == {True}

    def test_wired_device_untouched(self):
        r = annotate_channel_consensus(self._fleet())
        d = next(x for x in r if x["hostname"] == "srcbox")
        assert "channel_disagrees" not in d
        assert "fleet_center_freq_mhz" not in d

    def test_block_edges_inclusive(self):
        base = [{"hostname": "ref", "frequency_mhz": 5530,
                 "center_freq_mhz": 5530, "width_mhz": 80}]
        for freq, expected in ((5490, False), (5570, False),
                               (5489, True), (5571, True)):
            fleet = base + [{"hostname": "dut", "frequency_mhz": freq}]
            r = annotate_channel_consensus(fleet)
            dut = next(x for x in r if x["hostname"] == "dut")
            assert dut["channel_disagrees"] is expected, freq

    def test_no_block_reported_by_anyone_is_a_no_op(self):
        """A fleet where no driver reports a block gives nothing to judge."""
        fleet = [{"hostname": "rx40", "frequency_mhz": 5180},
                 {"hostname": "rx42", "frequency_mhz": 5180}]
        r = annotate_channel_consensus(fleet)
        assert all("channel_disagrees" not in d for d in r)

    def test_majority_wins_over_a_single_outlier_block(self):
        fleet = [
            {"hostname": "a", "frequency_mhz": 5540,
             "center_freq_mhz": 5530, "width_mhz": 80},
            {"hostname": "b", "frequency_mhz": 5540,
             "center_freq_mhz": 5530, "width_mhz": 80},
            {"hostname": "odd", "frequency_mhz": 5180,
             "center_freq_mhz": 5210, "width_mhz": 80},
        ]
        r = annotate_channel_consensus(fleet)
        assert next(x for x in r if x["hostname"] == "odd")["fleet_center_freq_mhz"] == 5530
        assert next(x for x in r if x["hostname"] == "odd")["channel_disagrees"] is True


class TestSpreadReporting:
    """The spread is reported, not smoothed away — a link that does not sit
    still is itself the finding. Sample sets are real measurements.
    """

    @staticmethod
    def _summarise(speeds):
        """Mirror the min/max/median/spread block in collect_link_stats."""
        speeds = sorted(speeds)
        out = {
            "link_speed_mbps": speeds[len(speeds) // 2],
            "link_speed_min_mbps": speeds[0],
            "link_speed_max_mbps": speeds[-1],
            "link_speed_samples": speeds,
        }
        spread = round(speeds[-1] / speeds[0], 2)
        out["link_speed_spread"] = spread
        out["link_unstable"] = bool(spread >= UNSTABLE_SPREAD)
        return out

    def test_p50_is_rock_stable(self):
        r = self._summarise([434] * 8)
        assert r["link_speed_spread"] == 1.0
        assert r["link_unstable"] is False
        assert r["link_speed_min_mbps"] == r["link_speed_max_mbps"]

    def test_p4_wanders_but_within_adjacent_mcs_steps(self):
        """351-390 is ~1.11 — normal rate adaptation, below the threshold."""
        r = self._summarise([360, 390, 390, 390, 390, 351, 360, 360])
        assert r["link_speed_min_mbps"] == 351
        assert r["link_speed_max_mbps"] == 390
        assert r["link_speed_spread"] == 1.11
        assert r["link_unstable"] is False

    def test_p42_spread(self):
        r = self._summarise([52, 58, 58, 58, 58, 58, 58, 58])
        assert r["link_speed_spread"] == 1.12
        assert r["link_unstable"] is False

    def test_large_swing_is_flagged(self):
        """One receiver across half an hour: 351 to 526 is 1.4986, shown as 1.50."""
        r = self._summarise([351, 400, 468, 526])
        assert r["link_speed_spread"] == 1.5
        assert r["link_unstable"] is True

    def test_flag_agrees_with_the_displayed_spread(self):
        """A row showing 1.50 must not appear unflagged beside it."""
        r = self._summarise([351, 526])
        assert (r["link_speed_spread"] >= UNSTABLE_SPREAD) is r["link_unstable"]

    def test_just_below_the_boundary_is_not_flagged(self):
        r = self._summarise([100, 148])
        assert r["link_speed_spread"] == 1.48
        assert r["link_unstable"] is False

    def test_median_is_still_the_headline(self):
        r = self._summarise([100, 200, 300])
        assert r["link_speed_mbps"] == 200

    def test_single_sample_has_no_spread(self):
        r = self._summarise([400])
        assert r["link_speed_spread"] == 1.0
        assert r["link_unstable"] is False

    def test_threshold_sits_above_normal_mcs_stepping(self):
        """Guards the constant: 1.12 must not trip it, 1.5 must."""
        assert UNSTABLE_SPREAD > 1.12
        assert UNSTABLE_SPREAD <= 1.5


class TestRemoteCommand:
    """The remote command silently collected one sample instead of N, because
    the wpa_cli probe ended in 'exit $?' inside the sampling loop.
    """

    def test_polls_the_requested_number_of_times(self):
        cmd = _build_remote_command("wlan0", 5, 0.4)
        assert "seq 5" in cmd

    def test_no_exit_inside_the_sampling_loop(self):
        """'exit $?' after a successful poll would end the whole shell."""
        cmd = _build_remote_command("wlan0", 5, 0.4)
        loop = cmd.split("for i in", 1)[1]
        assert "signal_poll" in loop
        # Only the failure path may exit, via '|| exit'.
        assert "signal_poll; exit" not in loop
        assert "|| exit" in loop

    def test_path_resolution_happens_before_the_loop(self):
        cmd = _build_remote_command("wlan0", 3, 0.4)
        assert cmd.index("/usr/sbin/wpa_cli") < cmd.index("for i in")

    def test_sleeps_between_polls(self):
        assert "sleep 0.4" in _build_remote_command("wlan0", 3, 0.4)

    def test_interface_is_used(self):
        assert "-i wlan1" in _build_remote_command("wlan1", 3, 0.4)

    def test_missing_wpa_cli_is_distinguishable(self):
        cmd = _build_remote_command("wlan0", 3, 0.4)
        assert "NO_WPA_CLI" in cmd

    def test_block_separator_present(self):
        """collect_link_stats splits samples on '---'."""
        assert '"---"' in _build_remote_command("wlan0", 3, 0.4)
