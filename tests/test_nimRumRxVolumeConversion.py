#!/usr/bin/env python3
"""Volume conversion tests.

The load-bearing property: one wire volume step is 0.5 dB on every DAC in the
fleet. volCal is stored in wire steps, so if a board had its own scale the same
volCal would mean a different number of dB depending on where it landed and a
level calibration could not be fleet-wide.
"""

import math

import pytest

from nimRum.rx.nimRumRxVolumeConversion import nimRumRxVolumeConversion

# Must match VOL_DB_PER_STEP in nimRumTxCfg and
# LIB_PREZO_SOFT_VOL_DB_PER_STEP in libPrezoMagicPipe.h
DB_PER_STEP = 0.5
VOL_MAX = 127
MIN_DB = -63.0


def soft_volume_gain(volume: int) -> float:
    """Model of _magpipe_setSoftVolumeVal in libPrezoMagicPipe.c.

    Kept here so a change to the C curve without a matching change to the
    Python scale fails a test rather than only showing up as wrong playback
    level on the DACs that use software volume.
    """
    if volume <= 0:
        return 0.0
    volume = min(volume, VOL_MAX)
    db = MIN_DB + (volume - 1) * DB_PER_STEP
    return min(pow(10.0, db / 20.0), 1.0)


class TestSoftVolumeCurve:
    def test_zero_is_mute(self):
        assert soft_volume_gain(0) == 0.0

    def test_max_is_unity(self):
        assert soft_volume_gain(VOL_MAX) == pytest.approx(1.0)

    def test_lowest_step_is_min_db(self):
        assert 20.0 * math.log10(soft_volume_gain(1)) == pytest.approx(MIN_DB)

    def test_every_step_is_half_a_db(self):
        for v in range(2, VOL_MAX + 1):
            step_db = 20.0 * math.log10(soft_volume_gain(v) /
                                        soft_volume_gain(v - 1))
            assert step_db == pytest.approx(DB_PER_STEP, abs=1e-6)

    def test_range_matches_the_hardware_mixer(self):
        """PCM512x/TAS5756M spans -63 dB to 0 dB over the same 127 steps."""
        span = (20.0 * math.log10(soft_volume_gain(VOL_MAX)) -
                20.0 * math.log10(soft_volume_gain(1)))
        assert span == pytest.approx((VOL_MAX - 1) * DB_PER_STEP)


class TestNimRumSoftVolPassThrough:
    """PCM5102A and NIMRUM_SFT_VOL must not rescale the wire value.

    The C side applies the 0-127 / 0.5 dB scale, so any rescaling here changes
    the dB per step. The old code mapped 0-127 onto 28-100.
    """

    @pytest.mark.parametrize("pcm_type", ["PCM5102A", "NIMRUM_SFT_VOL"])
    def test_value_passes_through_unchanged(self, pcm_type):
        conv = nimRumRxVolumeConversion(pcmType=pcm_type)
        for volIn in (0, 1, 40, 64, 100, 127):
            soft, volOut = conv.getVolVal(volIn)
            assert soft == 1, f"{pcm_type} must use software volume"
            assert volOut == volIn

    def test_soft_and_hardware_paths_agree_in_db(self):
        """A given wire value must mean the same dB on both DAC families."""
        soft_conv = nimRumRxVolumeConversion(pcmType="PCM5102A")
        hw_conv = nimRumRxVolumeConversion(pcmType="PCM_5122")

        for volIn in (1, 20, 64, 100, 127):
            _, soft_out = soft_conv.getVolVal(volIn)
            _, hw_reg = hw_conv.getVolVal(volIn)

            soft_db = 20.0 * math.log10(soft_volume_gain(soft_out))
            # Register 207 = 0 dB, 0.5 dB per step down from there.
            hw_db = (hw_reg - 207) * DB_PER_STEP

            assert soft_db == pytest.approx(hw_db, abs=0.01)


class TestHardwareMixer:
    def test_pcm5122_is_one_to_one(self):
        conv = nimRumRxVolumeConversion(pcmType="PCM_5122")
        soft, volOut = conv.getVolVal(127)
        assert soft == 0
        assert volOut == 207  # 0 dB register

    def test_pcm5122_zero_is_silent(self):
        conv = nimRumRxVolumeConversion(pcmType="PCM_5122")
        _, volOut = conv.getVolVal(0)
        assert volOut == 0
