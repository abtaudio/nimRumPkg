"""Tests for level measurement and volCal derivation.

Regression cover for the "+10 on LFE, -10 on fronts" bug: a subwoofer was
measured over 200-8000 Hz, where it produces nothing, and its noise-floor
reading then set the reference for every other speaker.
"""

import numpy as np
import pytest

from nimRum.calibration.nimRumCalibrationDeploy import (
    compute_vol_adjustments,
    compute_vol_adjustments_detailed,
)
from nimRum.calibration.nimRumCalibrationEngine import (
    LEVEL_BANDS,
    CalibrationEngine,
    TransferFunction,
)

FLOOR_DB = -60.0


def _make_tf(passband: tuple, level_db: float = 0.0) -> TransferFunction:
    """Build a synthetic transfer function: flat in passband, floor outside.

    Args:
        passband: (low_hz, high_hz) where the speaker produces output.
        level_db: Flat level inside the passband.

    Returns:
        A TransferFunction usable with compute_relative_level().
    """
    freqs = np.linspace(0.0, 24000.0, 4801)
    mag = np.full_like(freqs, FLOOR_DB)
    inband = (freqs >= passband[0]) & (freqs <= passband[1])
    mag[inband] = level_db
    return TransferFunction(
        freqs=freqs, magnitude_db=mag, phase_rad=np.zeros_like(freqs))


@pytest.fixture
def engine() -> CalibrationEngine:
    return CalibrationEngine(sample_rate=48000)


class TestComputeRelativeLevel:
    def test_full_range_reads_its_own_level(self, engine):
        tf = _make_tf((80.0, 18000.0), level_db=-3.0)
        level = engine.compute_relative_level(tf, speaker_type='full-range')
        assert level == pytest.approx(-3.0, abs=0.1)

    def test_subwoofer_in_sub_band_reads_its_own_level(self, engine):
        tf = _make_tf((25.0, 120.0), level_db=-3.0)
        level = engine.compute_relative_level(tf, speaker_type='subwoofer')
        assert level == pytest.approx(-3.0, abs=0.1)

    def test_subwoofer_in_full_range_band_reads_noise_floor(self, engine):
        """The original bug. Kept as documentation of why the band matters."""
        tf = _make_tf((25.0, 120.0), level_db=-3.0)
        level = engine.compute_relative_level(tf, speaker_type='full-range')
        assert level < FLOOR_DB + 10.0

    def test_sub_and_main_at_same_level_compare_equal(self, engine):
        """Each measured in its own band, so 0 dB diff is reachable."""
        main = engine.compute_relative_level(
            _make_tf((80.0, 18000.0), level_db=-6.0),
            speaker_type='full-range')
        sub = engine.compute_relative_level(
            _make_tf((25.0, 120.0), level_db=-6.0),
            speaker_type='subwoofer')
        assert sub == pytest.approx(main, abs=0.1)

    def test_unknown_speaker_type_falls_back_to_full_range(self, engine):
        tf = _make_tf((80.0, 18000.0), level_db=-3.0)
        assert engine.compute_relative_level(tf, speaker_type='nonsense') == \
            pytest.approx(
                engine.compute_relative_level(tf, speaker_type='full-range'))

    def test_explicit_band_overrides_speaker_type(self, engine):
        tf = _make_tf((25.0, 120.0), level_db=-3.0)
        level = engine.compute_relative_level(
            tf, freq_low=30.0, freq_high=100.0, speaker_type='full-range')
        assert level == pytest.approx(-3.0, abs=0.1)

    def test_empty_band_returns_zero(self, engine):
        tf = _make_tf((80.0, 18000.0))
        assert engine.compute_relative_level(
            tf, freq_low=30000.0, freq_high=40000.0) == 0.0

    def test_power_average_not_db_average(self, engine):
        """A mean of dB is a geometric mean of power and under-weights peaks."""
        freqs = np.linspace(0.0, 24000.0, 4801)
        mag = np.full_like(freqs, FLOOR_DB)
        band = (freqs >= 200.0) & (freqs <= 8000.0)
        mag[band] = 0.0
        # Put +20 dB on the lower half of the band.
        boosted = band & (freqs <= 4100.0)
        mag[boosted] = 20.0
        tf = TransferFunction(
            freqs=freqs, magnitude_db=mag, phase_rad=np.zeros_like(freqs))

        level = engine.compute_relative_level(tf, speaker_type='full-range')
        db_mean = float(np.mean(mag[band]))
        assert level > db_mean

    def test_sub_band_sits_inside_the_eq_fit_range(self):
        from nimRum.calibration.nimRumCalibrationEngine import EQ_FIT_RANGES
        fit_low, fit_high = EQ_FIT_RANGES['subwoofer']
        lvl_low, lvl_high = LEVEL_BANDS['subwoofer']
        assert fit_low <= lvl_low < lvl_high <= fit_high


class TestComputeVolAdjustments:
    def test_empty_input(self):
        assert compute_vol_adjustments({}) == {}

    def test_louder_speaker_gets_negative_adjustment(self):
        adj = compute_vol_adjustments({'a': 0.0, 'b': -4.0})
        assert adj['a'] < 0
        assert adj['b'] > 0

    def test_equal_levels_give_zero(self):
        adj = compute_vol_adjustments({'a': -5.0, 'b': -5.0, 'c': -5.0})
        assert adj == {'a': 0, 'b': 0, 'c': 0}

    def test_explicit_reference_speaker(self):
        adj = compute_vol_adjustments(
            {'a': 0.0, 'b': -6.0}, reference='a')
        assert adj['a'] == 0
        assert adj['b'] == 12  # +6 dB in 0.5 dB steps

    def test_subwoofer_does_not_move_the_mains(self):
        """The "-10 on fronts" half of the bug."""
        levels = {'fl': 0.0, 'fr': 0.0, 'fc': 0.0, 'lfe': -40.0}
        types = {'fl': 'full-range', 'fr': 'full-range',
                 'fc': 'full-range', 'lfe': 'subwoofer'}
        adj = compute_vol_adjustments(levels, speaker_types=types)
        assert adj['fl'] == 0
        assert adj['fr'] == 0
        assert adj['fc'] == 0

    def test_without_types_the_subwoofer_still_poisons_the_reference(self):
        """Documents why speaker_types must be passed by callers."""
        levels = {'fl': 0.0, 'fr': 0.0, 'fc': 0.0, 'lfe': -40.0}
        adj = compute_vol_adjustments(levels)
        assert adj['fl'] < 0

    def test_reference_group_is_mains_only(self):
        levels = {'fl': -2.0, 'fr': -4.0, 'lfe': -40.0}
        types = {'fl': 'full-range', 'fr': 'full-range', 'lfe': 'subwoofer'}
        _, info = compute_vol_adjustments_detailed(
            levels, speaker_types=types)
        assert info['reference_source'] == 'group-average'
        assert info['reference_speakers'] == ['fl', 'fr']
        assert info['reference_level'] == pytest.approx(-3.0)

    def test_all_subwoofers_falls_back_to_all_average(self):
        levels = {'lfe1': -10.0, 'lfe2': -14.0}
        types = {'lfe1': 'subwoofer', 'lfe2': 'subwoofer'}
        adj, info = compute_vol_adjustments_detailed(
            levels, speaker_types=types)
        assert info['reference_source'] == 'all-average'
        assert info['reference_level'] == pytest.approx(-12.0)
        assert adj['lfe1'] == -4  # -2 dB in 0.5 dB steps
        assert adj['lfe2'] == 4

    def test_clamped_values_are_reported(self):
        levels = {'a': 0.0, 'b': -40.0}
        adj, info = compute_vol_adjustments_detailed(levels, reference='a')
        assert adj['b'] == 20  # clamped to +10 dB, in 0.5 dB steps
        assert info['clamped'] == ['b']
        assert info['raw']['b'] == pytest.approx(40.0)  # raw stays dB

    def test_unclamped_values_are_not_reported(self):
        adj, info = compute_vol_adjustments_detailed(
            {'a': 0.0, 'b': -4.0}, reference='a')
        assert info['clamped'] == []
        assert adj['b'] == 8  # +4 dB in 0.5 dB steps

    def test_clamp_limit_is_configurable(self):
        adj, info = compute_vol_adjustments_detailed(
            {'a': 0.0, 'b': -40.0}, reference='a', max_adjustment=6)
        assert adj['b'] == 12  # max_adjustment is dB, adj is steps
        assert info['clamped'] == ['b']

    def test_no_sub_mains_offset_is_applied(self):
        """Calibration must be offset-free; the offset is the user's slider."""
        levels = {'fl': -6.0, 'lfe': -6.0}
        types = {'fl': 'full-range', 'lfe': 'subwoofer'}
        adj = compute_vol_adjustments(levels, speaker_types=types)
        assert adj['lfe'] == 0
        assert adj['fl'] == 0

    def test_wrapper_matches_detailed(self):
        levels = {'a': 0.0, 'b': -3.0, 'c': -6.0}
        assert compute_vol_adjustments(levels) == \
            compute_vol_adjustments_detailed(levels)[0]


class TestVolCalUnits:
    """volCal is in wire volume steps, not dB.

    Writing the dB value straight into volCal applied only half the intended
    correction on every DAC in the fleet. These tests pin the unit so that
    cannot silently come back.
    """

    def test_step_size_matches_the_wire(self):
        from nimRum.calibration.nimRumCalibrationDeploy import VOL_DB_PER_STEP
        assert VOL_DB_PER_STEP == 0.5

    def test_six_db_becomes_twelve_steps(self):
        adj = compute_vol_adjustments({'a': 0.0, 'b': -6.0}, reference='a')
        assert adj['b'] == 12

    def test_adjustment_cancels_the_measured_difference(self):
        """The whole point: applying volCal must null the measured error."""
        from nimRum.calibration.nimRumCalibrationDeploy import VOL_DB_PER_STEP
        levels = {'ref': -10.0, 'hot': -4.0, 'quiet': -13.0}
        adj = compute_vol_adjustments(levels, reference='ref')
        for name, level in levels.items():
            corrected = level + adj[name] * VOL_DB_PER_STEP
            assert corrected == pytest.approx(levels['ref'], abs=0.25)

    def test_raw_info_stays_in_db(self):
        _, info = compute_vol_adjustments_detailed(
            {'a': 0.0, 'b': -6.0}, reference='a')
        assert info['raw']['b'] == pytest.approx(6.0)
