# Copyright (C) 2021-2026 AbtAudio AB
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""Tests for the mispairing gate in nimRumMeasSpikes.

The gate used to live in the analysis tools, which let mispaired samples into
the running mean, the plot and the .npy and pulled the reported figure well
away from the true value. These tests pin the gate to this module and pin the
printed format the tools parse.

matplotlib, wavio and sounddevice are stubbed: the module imports them at top
level but the capture path under test never calls them, and stubbing keeps the
test suite free of a 50 MB plotting dependency.
"""

import sys
import types
from typing import Tuple

import numpy as np
import pytest

SPIKE_AMPLITUDE = 10000
BUFFER_LEN = 4800          # 100 ms at 48 kHz
CAP_RATE = 48000
US_PER_SAMPLE = 1000000.0 / CAP_RATE


def _install_stubs() -> None:
    """Register empty stand-ins for the plotting and audio-file imports."""
    for name in ("matplotlib", "wavio", "sounddevice"):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    if "matplotlib.pyplot" not in sys.modules:
        pyplot = types.ModuleType("matplotlib.pyplot")
        sys.modules["matplotlib.pyplot"] = pyplot
        sys.modules["matplotlib"].pyplot = pyplot


_install_stubs()

from nimRum.meas.nimRumMeasSpikes import (       # noqa: E402  (after stubs)
    MISPAIR_STEP_LIMIT_US,
    NIMRUM_MEAS_SPIKES,
)


def _spike_buffer(index: int) -> np.ndarray:
    """A clean bipolar step: negative up to `index`, positive after it.

    The detector takes the steepest rising edge, walks back to the last
    negative sample and requires a run of negative samples before it and
    positive after, so a step satisfies it exactly at `index`.
    """
    buf = np.full(BUFFER_LEN, -SPIKE_AMPLITUDE, dtype=np.int32)
    buf[index + 1:] = SPIKE_AMPLITUDE
    return buf


def _stereo(right_index: int, left_index: int) -> np.ndarray:
    """Two-channel capture with a spike at the given index in each channel."""
    return np.stack([_spike_buffer(right_index), _spike_buffer(left_index)], axis=1)


def _meas() -> NIMRUM_MEAS_SPIKES:
    return NIMRUM_MEAS_SPIKES(capRate=CAP_RATE, maxInt=SPIKE_AMPLITUDE)


def _prime(meas: NIMRUM_MEAS_SPIKES, index: int = 100, count: int = 5) -> None:
    """Feed identical aligned samples until the gate has a history to work from.

    The gate needs len(rXArr) > 3, and a constant position makes the expected
    interval exactly zero, so any later step is the full step.
    """
    for _ in range(count):
        meas.add(_stereo(index, index))


def _step_us(samples: int) -> float:
    return samples * US_PER_SAMPLE


def test_aligned_samples_are_accepted() -> None:
    meas = _meas()
    _prime(meas)

    assert meas.sampleN == 5
    assert meas.rejectCounts["mispair"] == 0
    assert meas.rejectCounts["suspect"] == 0
    assert len(meas.synchDiff_Y) == 5


@pytest.mark.parametrize("offset_samples", [1, 4, 9])
def test_step_within_limit_is_accepted(offset_samples: int) -> None:
    """A step below the limit is a real movement of the pair, not a mispair."""
    assert _step_us(offset_samples) < MISPAIR_STEP_LIMIT_US
    meas = _meas()
    _prime(meas)

    meas.add(_stereo(100 + offset_samples, 100))

    assert meas.rejectCounts["mispair"] == 0
    assert meas.sampleN == 6
    assert meas.synchDiff_Y[-1] == pytest.approx(_step_us(offset_samples), abs=0.5)


@pytest.mark.parametrize("offset_samples", [12, 100, 3600])
def test_step_beyond_limit_is_excluded(offset_samples: int) -> None:
    """The mispaired sample is counted and printed, but not measured."""
    assert _step_us(offset_samples) > MISPAIR_STEP_LIMIT_US
    meas = _meas()
    _prime(meas)
    accepted_before = meas.sampleN
    series_before = len(meas.synchDiff_Y)

    meas.add(_stereo(100 + offset_samples, 100))

    assert meas.rejectCounts["mispair"] == 1
    assert meas.rejectCounts["suspect"] == 1
    assert meas.sampleN == accepted_before, "a mispair must not count as accepted"
    assert len(meas.synchDiff_Y) == series_before, "must not reach plot/.npy/mean"


def test_mispair_does_not_move_the_running_mean() -> None:
    """The regression this gate exists for: a 78 ms sample dragging the mean."""
    meas = _meas()
    _prime(meas)
    mean_before = float(np.mean(meas.synchDiff_Y))

    meas.add(_stereo(100 + 3600, 100))          # 75 ms out

    assert float(np.mean(meas.synchDiff_Y)) == pytest.approx(mean_before)


def test_gate_recovers_and_does_not_stay_tripped() -> None:
    """rXArr/lXArr must keep the rejected sample, or the gate self-perpetuates.

    This is a bug that was fixed: a withheld entry makes the next interval span
    two capture buffers, which re-trips the gate and stays tripped until the
    process restarts.

    Note the cost of the gate being a FIRST DIFFERENCE — one mispair event
    flags two consecutive samples, the event and the return to a consistent
    pairing. That is why two events produce four flagged lines. The third
    sample must be accepted again.
    """
    meas = _meas()
    _prime(meas)

    meas.add(_stereo(100 + 3600, 100))          # R jumps a spike event
    meas.add(_stereo(100 + 3600, 100 + 3600))   # L follows: still a step, still bad
    meas.add(_stereo(100 + 3600, 100 + 3600))   # consistent again

    assert len(meas.rXArr) == 8, "every sample must stay in the interval history"
    assert meas.rejectCounts["mispair"] == 2, "a first difference costs two samples"
    assert meas.sampleN == 6, "and then it recovers"
    assert meas.synchDiff_Y[-1] == pytest.approx(0.0, abs=0.5)


def test_accepted_line_format(capsys: pytest.CaptureFixture) -> None:
    """The tools parse this line; the removed columns must stay removed."""
    meas = _meas()
    _prime(meas, count=4)
    capsys.readouterr()

    meas.add(_stereo(100, 100))
    line = capsys.readouterr().out.strip().splitlines()[-1]

    assert "synchDiffTime_us:" in line
    assert "Mean:" in line
    assert "pairDist_us:" in line
    assert "N:" in line
    for removed in ("DistanceToMean", "Diff R/L", "(R-L="):
        assert removed not in line


def test_suspect_line_states_its_reason(capsys: pytest.CaptureFixture) -> None:
    meas = _meas()
    _prime(meas)
    capsys.readouterr()

    meas.add(_stereo(100 + 3600, 100))
    line = capsys.readouterr().out.strip().splitlines()[-1]

    assert "SUSPECT" in line
    assert "mispair" in line
    assert "pairDist_us:" in line


def test_first_sample_after_a_store_is_suspect() -> None:
    """Kept from before: the store starves the capture, so the next pair is bad."""
    meas = _meas()
    _prime(meas)
    meas.storeGap = True
    accepted_before = meas.sampleN

    meas.add(_stereo(100, 100))

    assert meas.sampleN == accepted_before
    assert meas.rejectCounts["suspect"] == 1
    assert meas.rejectCounts["mispair"] == 0, "not a mispair, a capture gap"


def test_reject_summary_reports_mispairs(capsys: pytest.CaptureFixture) -> None:
    meas = _meas()
    _prime(meas)
    meas.add(_stereo(100 + 3600, 100))
    capsys.readouterr()

    meas._reportRejections()
    out = capsys.readouterr().out

    assert "mispair=1" in out
