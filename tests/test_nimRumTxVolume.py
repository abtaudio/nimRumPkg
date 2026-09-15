#!/usr/bin/env python3
"""Volume resolution tests for the TX config layer.

Two bugs these pin down, both of which made EQ level calibration wrong:

1. getVolume added the layout level twice — once via getLevelForLayout and
   again via the cliVolStereoAdj/cliVolMultiAdj snapshots, which are read from
   the same layouts.*.level field.
2. The acoustic measurement played through volCal and the layout level, so a
   re-run measured the previous run's own correction and cancelled it.
"""

import sys
import types

import pytest

# nimRumTxCfg pulls in lirc and GPIO via nimRumTxRemote / nimRumRotaryEnc, which
# are not present on a dev machine. Only module-level constants and the pure
# volume logic are under test, so stub the hardware imports.
for _name in ("lirc", "RPi", "RPi.GPIO", "gpiozero"):
    sys.modules.setdefault(_name, types.ModuleType(_name))

from nimRum.tx.nimRumTxCfg import (  # noqa: E402
    MULTI_LAYOUT_KEYS,
    STEREO_LAYOUT_KEYS,
    VOL_DB_PER_STEP,
    VOL_MAX,
    nimRumTxCfg,
)


def make_cfg(clients, volume=64, activeLayout="stereo"):
    """Build a nimRumTxCfg without running __init__ (which needs hardware)."""
    cfg = nimRumTxCfg.__new__(nimRumTxCfg)
    cfg.numOfCli = len(clients)
    cfg.cliIds = list(range(len(clients)))
    cfg.cliNames = [c["name"] for c in clients]
    cfg.cliVolCal = [c.get("volCal", 0) for c in clients]
    cfg._clientLayouts = [c["layouts"] for c in clients]
    cfg.volume = volume
    cfg.activeLayout = activeLayout
    cfg.activeChannels = 2 if activeLayout == "stereo" else 6
    cfg.temporary_mute = False
    cfg.scene_mute = {i: False for i in cfg.cliIds}
    cfg.cliVolStereoAdj = [0] * cfg.numOfCli
    cfg.cliVolMultiAdj = [0] * cfg.numOfCli
    for cliId in cfg.cliIds:
        cfg._refreshVolAdjSnapshots(cliId)
    return cfg


ONE_CLIENT = [{
    "name": "fl",
    "volCal": 0,
    "layouts": {
        "stereo": {"channel": 0, "level": 0},
        "5.1(side)": {"channel": 0, "level": 0},
    },
}]


class TestGetVolumeDoesNotDoubleCount:
    def test_layout_level_is_applied_once(self):
        clients = [{
            "name": "fl",
            "layouts": {
                "stereo": {"channel": 0, "level": -6},
                "5.1(side)": {"channel": 0, "level": -6},
            },
        }]
        cfg = make_cfg(clients, volume=64, activeLayout="stereo")
        # 64 - 6, not 64 - 12
        assert cfg.getVolume(0) == 58

    def test_multi_layout_level_is_applied_once(self):
        clients = [{
            "name": "lfe",
            "layouts": {
                "stereo": {"channel": 0, "level": 0},
                "5.1(side)": {"channel": 5, "level": 8},
            },
        }]
        cfg = make_cfg(clients, volume=64, activeLayout="5.1(side)")
        assert cfg.getVolume(0) == 72

    def test_snapshots_are_not_added_on_top(self):
        """Corrupting the snapshots must not change the resolved volume."""
        cfg = make_cfg(ONE_CLIENT, volume=64)
        baseline = cfg.getVolume(0)
        cfg.cliVolStereoAdj[0] = 30
        cfg.cliVolMultiAdj[0] = 30
        assert cfg.getVolume(0) == baseline

    def test_volcal_is_applied(self):
        clients = [{
            "name": "fl",
            "volCal": -4,
            "layouts": {"stereo": {"channel": 0, "level": -2}},
        }]
        cfg = make_cfg(clients, volume=64)
        assert cfg.getVolume(0) == 58  # 64 - 2 - 4

    def test_clamped_to_range(self):
        low = make_cfg(
            [{"name": "a", "volCal": -30,
              "layouts": {"stereo": {"channel": 0, "level": -30}}}],
            volume=10)
        assert low.getVolume(0) == 0

        high = make_cfg(
            [{"name": "a", "volCal": 30,
              "layouts": {"stereo": {"channel": 0, "level": 30}}}],
            volume=120)
        assert high.getVolume(0) == VOL_MAX


class TestMeasurementVolume:
    def test_excludes_volcal_and_layout_level(self):
        clients = [{
            "name": "fl",
            "volCal": -10,
            "layouts": {"stereo": {"channel": 0, "level": 6}},
        }]
        cfg = make_cfg(clients, volume=64)
        assert cfg.getMeasurementVolume() == 64
        assert cfg.getVolume(0) == 60  # the trimmed playback level differs

    def test_identical_for_differently_trimmed_speakers(self):
        """The reason it exists: one common reference for the whole set."""
        clients = [
            {"name": "a", "volCal": -8,
             "layouts": {"stereo": {"channel": 0, "level": 0}}},
            {"name": "b", "volCal": 12,
             "layouts": {"stereo": {"channel": 1, "level": -4}}},
        ]
        cfg = make_cfg(clients, volume=70)
        assert cfg.getVolume(0) != cfg.getVolume(1)
        assert cfg.getMeasurementVolume() == 70

    def test_tracks_main_volume_without_raising_it(self):
        for vol in (0, 10, 64, 127):
            cfg = make_cfg(ONE_CLIENT, volume=vol)
            assert cfg.getMeasurementVolume() == vol

    def test_stays_in_range(self):
        assert make_cfg(ONE_CLIENT, volume=200).getMeasurementVolume() == VOL_MAX
        assert make_cfg(ONE_CLIENT, volume=-5).getMeasurementVolume() == 0


class TestSetLevelForLayout:
    def test_write_is_visible_to_getvolume(self):
        """A Levels slider must actually change the audible volume."""
        cfg = make_cfg(ONE_CLIENT, volume=64, activeLayout="stereo")
        assert cfg.setLevelForLayout(0, STEREO_LAYOUT_KEYS, -10) is True
        assert cfg.getVolume(0) == 54

    def test_write_updates_the_snapshot(self):
        cfg = make_cfg(ONE_CLIENT, volume=64)
        cfg.setLevelForLayout(0, MULTI_LAYOUT_KEYS, 7)
        assert cfg.cliVolMultiAdj[0] == 7

    def test_falls_back_to_default_layout(self):
        clients = [{"name": "a", "layouts": {"default": {"channel": 0,
                                                         "level": 0}}}]
        cfg = make_cfg(clients, volume=64, activeLayout="stereo")
        assert cfg.setLevelForLayout(0, STEREO_LAYOUT_KEYS, -3) is True
        assert cfg.getVolume(0) == 61

    def test_missing_entry_reports_failure(self):
        clients = [{"name": "a", "layouts": {"7.1": {"channel": 0,
                                                     "level": 0}}}]
        cfg = make_cfg(clients, volume=64)
        assert cfg.setLevelForLayout(0, STEREO_LAYOUT_KEYS, -3) is False


class TestStepSize:
    def test_matches_the_calibration_deploy_constant(self):
        from nimRum.calibration.nimRumCalibrationDeploy import (
            VOL_DB_PER_STEP as deploy_step,
        )
        assert VOL_DB_PER_STEP == deploy_step
