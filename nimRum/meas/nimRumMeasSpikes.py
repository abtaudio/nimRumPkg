#!/usr/bin/env python3

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
#
# As a special exception, you may link this program with the nimRumLib
# shared libraries (libNimRumTx_ct.so, libNimRumRx_ct.so) provided by
# AbtAudio AB without those libraries being subject to the GPL-3.0.

import numpy as np
import matplotlib.pyplot as plt
import wavio
import time
import statistics
from pathlib import Path
import os 

# Largest step in synchDiffTime_us, between one accepted sample and the next,
# that can still be one continuous measurement of the same speaker pair.
#
# A step larger than this means the two channels latched onto different spike
# events, so the sample is not a measurement of anything. The populations do not
# overlap: measured 2026-09-06 clean samples sat at 32 us p50 against 2831 us for
# mispaired ones, and again on an identical-pair bench 2026-09-15 at 1 us p50 /
# 6 us p99 against ~77850 us for all four bad samples in a 2706-sample run.
# Anywhere between 200 us and several ms would separate them equally well.
#
# This gate lived in the analysis tools rather than here until 2026-09-15, which
# left the printed Mean, the plot and the .npy taking the mispaired samples in.
# On that 2706-sample run it put the running mean at -84 us against a true -26 us.
MISPAIR_STEP_LIMIT_US = 200

class NIMRUM_MEAS_SPIKES():
    """Spike-pair timing measurement between two ADC channels.

    ON THE PRINTED STATISTICS - do your own maths
    -------------------------------------------------------------------------
    Only ONE printed column is a raw measurement: synchDiffTime_us, the spike
    separation between the two channels. Compute whatever you need from that,
    over the period you care about. The rest are convenience values with
    surprising windows, kept because they are useful at the console but not
    meant to be quoted:

    * Mean - a running mean over the RETAINED window, whose length is not a
      fixed number you can look up. It is nominally maxTime_sec (default 200 s),
      but _limitdata() drops one sample per add() once that is exceeded,
      including on calls that produced no sample - so under heavy rejection or
      dropout the window collapses towards its 20-sample floor. It also stays
      polluted for a long time after any change to levels, cabling or devices,
      and nothing in the output says how many samples it currently spans. It
      does now exclude mispaired samples, which it did not before 2026-09-15.

    * pairDist_us - how far apart the two spike indices sat inside the same
      capture buffer. Useful for spotting a gross wiring or level problem, but
      NOT a mispairing test: both indices come from one buffer, so a pair can be
      mutually consistent and still be the wrong event. Measured 2026-09-15 it
      read 0 on two of four known-mispaired samples.

    * N - a monotonic counter of accepted samples, for de-duplicating when a
      reader polls the log rather than following it.

    So: take synchDiffTime_us, decide your own window, and compute mean,
    median and spread yourself.

    Three columns were removed on 2026-09-15, because none of them was a level
    and their names did not say so - one of them had already produced a wrong
    "no systematic offset" conclusion:

    * DistanceToMean was just synchDiffTime_us - Mean.
    * Diff R/L was each channel's spike position against the expected interval.
    * (R-L=) was the FIRST DIFFERENCE of synchDiffTime_us, since both channels
      subtract the same expected_interval. Averaged over a run it goes to ~0
      whatever the offset is. It WAS the per-sample validity indicator, applied
      by the analysis tools at a 200 us threshold - so rather than being lost it
      moved into this file as MISPAIR_STEP_LIMIT_US, which is what marks such a
      sample SUSPECT and keeps it out of the mean, the plot and the .npy.

    A SUSPECT line carries its reason and is excluded from the series. Skip those
    lines when parsing; the accepted lines need no further gating.
    """


    def __init__(self, capRate=48000, maxTime_sec=200, maxDiff_us=None, maxInt=1,
                 resultFolder=None, trigPeakFrac=0.25, trigDecay=0.98,
                 quietWarn_sec=60.0, verbose=False):
        # verbose=True restores the per-candidate rejection prints. They were
        # unconditional while the detector was being developed, and at 0.35 lines
        # per second they came to 89 499 of 358 527 lines in one 72 h log - 25% of
        # it, one message repeated. Every one of them is already counted in
        # rejectCounts and summarised at each store, so nothing is lost by
        # default; turn this on when changing the detector itself.
        #
        # Volume is not only a readability problem on this device. A measurement
        # host can be as small as a Pi 2B v1.1, and one has been found with the
        # measurement service hung at 102% CPU and a frozen log, and print storms
        # starving a loop is a fault this project has already paid for once.
        self.verbose = verbose
        self.capRate = capRate

        # Pair-distance bound. None = derive it from the observed spike interval
        # (half of it), which is the only principled bound: beyond half an
        # interval you cannot tell which spike is which, and below it you are
        # truncating real measurements.
        #
        # It used to default to a fixed 250 ms and to drop the sample silently.
        # That is a bias, not a filter: a receiver has been measured 17-24 ms
        # out, so a fixed bound sits close enough to the signal to shape the
        # distribution, and nothing counted what it removed. Now the sample is
        # kept, the distance is printed, and the drops are counted. Mispairing is
        # decided by MISPAIR_STEP_LIMIT_US instead, which separates cleanly
        # (32 us for good pairs against 2831 us for mispaired ones).
        self.maxDiff_us = maxDiff_us

        # Trigger level, relative to the peak envelope this channel has recently
        # been showing rather than to full scale.
        #
        # Two wrong designs preceded this one:
        #
        # 1. maxInt/500, a fixed fraction of full scale. Since the spike shares
        #    the volume-controlled output path with the music, turning the volume
        #    down puts the spike under the bar and every buffer becomes "just
        #    noise" - which is how an 11 h run lost 7 h 24 m while the service
        #    still looked healthy. (It was also *inert* whenever the caller left
        #    maxInt at 1 while feeding int32 counts, which is why the same code
        #    could appear to work for months.)
        #
        # 2. peak > median(|x|) * factor, i.e. SNR against the buffer's own
        #    "noise floor". Measured 2026-09-07 23:34: on this bench the channel
        #    carries MUSIC as well as the spike, so the median is program
        #    material, the spike sits only ~5x above it, and a x10 factor rejects
        #    every buffer. The median is not a noise floor whenever anything else
        #    is playing.
        #
        # What works is neither absolute nor content-relative but *history*
        # relative: reject a buffer only if its peak is far below the peak
        # envelope recently seen on this channel. A level change moves the
        # envelope with it, so it cannot silence the measurement; an empty or
        # disconnected channel drops well under it and is still rejected. Being
        # permissive here is safe because level is not what identifies a spike -
        # the bipolar "middle of square" shape check below, the pair distance and
        # the downstream |R-L| gate all still apply.
        self.trigPeakFrac = trigPeakFrac
        self.trigDecay = trigDecay
        self.refPeak = {}
        # Its ONLY job is to stop the threshold being 0 on digital silence, so it
        # has to be negligible against any real signal. Do not scale it to
        # something like maxInt/20000: callers pass maxInt as int32 full scale
        # (2147483647) while the measured spike peaks around 86000, i.e. -88 dBFS,
        # so anything derived from full scale rejects everything. Measured
        # 2026-09-07 23:47 by doing exactly that.
        self.trigAbsFloor = max(1.0, maxInt / 1e9)

        self.synchDiff_X = np.array([])
        self.synchDiff_Y = np.array([])
        self.rXArr = np.array([])
        self.lXArr = np.array([])

        self.maxTime_sec = maxTime_sec # To limit size of stored data
        self.currentTime_sec = 0.0

        self.storeInterval_sec = 10*60

        self.plotTitle = "Synch Diff - Px VS Py"
        plotFileName = self.plotTitle.replace(" ", "_") + ".pdf"
        if resultFolder == None:
            resultFolder = str(Path.home())
        self.plotFileName = os.path.join(resultFolder, plotFileName)
        self.plotLabel = "Px VS Py"

        self.lastStorageTime = time.time()
        self.sampleN = 0

        # storeResult() renders a PDF and writes a .npy from inside the capture
        # path, which on a Pi 2B takes long enough to starve the PortAudio
        # callback — "Got callback status:input overflow" in the log. Samples are
        # dropped, so the very next inter-spike interval spans the gap and the
        # pair latches onto different spike events. Measured 2026-09-10: four
        # excursions of 490-705 ms in one hour, every one immediately after a
        # "Stored:" line, and each drawn into the plot at full height so a
        # +-100us real signal was compressed into a flat line at zero.
        #
        # The sample after a store is therefore suspect by construction, whether
        # or not the interval test happens to catch it.
        self.storeGap = False

        # Why nothing is coming out, when nothing is coming out. A long run must
        # not be able to stop producing samples and still look healthy, so every
        # rejection path is counted and a warning is printed while the drought
        # lasts rather than once at the end.
        self.quietWarn_sec = quietWarn_sec
        self.lastAcceptTime = time.time()
        self.lastQuietWarnTime = 0.0
        self.rejectCounts = {
            "noise": 0,       # peak did not stand above the noise floor
            "noSpike": 0,     # no clean bipolar edge found
            "pairFar": 0,     # pair distance beyond maxDiff_us
            "mispair": 0,     # step in the measurement itself, see MISPAIR_STEP_LIMIT_US
            "suspect": 0,     # channels latched onto different spikes; printed,
                              # but kept out of the plotted/stored series
        }
        # Snapshot of rejectCounts at the previous store, so the periodic summary
        # can report the interval as well as the running total. A total alone
        # cannot distinguish "it was bad for ten minutes at the start" from
        # "it is bad right now", which is the question being asked of it.
        self.rejectCountsAtLastReport = dict(self.rejectCounts)
        self.acceptedAtLastReport = 0

    def _warnIfQuiet(self):
        """Print a warning while no samples are being accepted.

        Silence here is indistinguishable from a healthy idle system unless it
        says so, and that is exactly how hours of a run have been lost before.
        """
        quiet_sec = time.time() - self.lastAcceptTime
        if quiet_sec < self.quietWarn_sec:
            return
        if (time.time() - self.lastQuietWarnTime) < self.quietWarn_sec:
            return
        self.lastQuietWarnTime = time.time()
        print("WARNING: no accepted spike pair for {:.0f}s. "
              "Rejections since start: noise={} noSpike={} pairFar={} mispair={} "
              "suspect={}. N={} - check the output level and the cabling.".format(
                  quiet_sec, self.rejectCounts["noise"],
                  self.rejectCounts["noSpike"], self.rejectCounts["pairFar"],
                  self.rejectCounts["mispair"], self.rejectCounts["suspect"],
                  self.sampleN))

    def _reportRejections(self):
        """One line per store with what was rejected, as interval and total.

        This is where the per-candidate prints went. Rejections are normal and
        expected - the detector sees every capture buffer, most of which hold no
        spike - so what matters is the rate and whether it is changing, not each
        event. `int` is the count since the previous report.
        """
        d = {k: self.rejectCounts[k] - self.rejectCountsAtLastReport.get(k, 0)
             for k in self.rejectCounts}
        acceptedInt = self.sampleN - self.acceptedAtLastReport

        print("Store {}s: accepted int={} tot={} | rejected int "
              "noise={} noSpike={} pairFar={} mispair={} suspect={} | tot "
              "noise={} noSpike={} pairFar={} mispair={} suspect={}".format(
                  self.storeInterval_sec, acceptedInt, self.sampleN,
                  d["noise"], d["noSpike"], d["pairFar"], d["mispair"], d["suspect"],
                  self.rejectCounts["noise"], self.rejectCounts["noSpike"],
                  self.rejectCounts["pairFar"], self.rejectCounts["mispair"],
                  self.rejectCounts["suspect"]))

        self.rejectCountsAtLastReport = dict(self.rejectCounts)
        self.acceptedAtLastReport = self.sampleN

    def storeResult(self):
        if len(self.synchDiff_X) == 2:
            print("No result to store")
            return 

        ax1 = plt.subplot(111)
        ax1.clear()

        ax1.plot(self.synchDiff_X, self.synchDiff_Y, label=self.plotLabel, color="black")#, marker=".")

        ax1.set_title(self.plotTitle)
        ax1.set_xlabel("Time [s]")
        ax1.set_ylabel("Channel accuracy [us]")

        # Autoscale to the bulk of the data, not to its extremes. One 0.5 s
        # excursion on an unclipped axis compresses a real +-100 us trace into a
        # flat line at zero — which is exactly how the 2026-09-10 plot managed to
        # hide a good result behind two bad samples. Outliers are still drawn,
        # they just cannot dictate the scale, and the count goes in the title so
        # nothing is silently cropped.
        #
        # Median +- k*IQR, not percentiles: a percentile bound is only as robust
        # as the sample count allows, and p99 of 39 samples containing one wild
        # value still lands on the wild value. IQR does not care how few samples
        # there are.
        y = np.asarray(self.synchDiff_Y, dtype=float)
        y = y[np.isfinite(y)]
        if y.size >= 10:
            q25, q50, q75 = np.percentile(y, [25.0, 50.0, 75.0])
            pad = max(50.0, 3.0 * (q75 - q25))
            lo, hi = q50 - pad, q50 + pad
            clipped = int(np.count_nonzero((y < lo) | (y > hi)))
            ax1.set_ylim(lo, hi)
            if clipped:
                ax1.set_title("{}  ({} of {} samples outside axis)".format(
                    self.plotTitle, clipped, y.size))

        plt.savefig(self.plotFileName, format="pdf", bbox_inches="tight")

        npyFileName = self.plotFileName+'.npy'
        with open(npyFileName, 'wb') as f:
            np.save(f, self.synchDiff_X)
            np.save(f, self.synchDiff_Y)

        print("Stored: {} and {}".format(self.plotFileName, npyFileName))

    def _limitdata(self):
        """
        Just keep latest latest self.maxTime_sec seconds
        In case not spikes detected for a long time, or at least 20 data points 
        """
        while (self.currentTime_sec > self.maxTime_sec) and (len(self.synchDiff_X) > 20):
            self.currentTime_sec -= self.synchDiff_X[0]
            self.synchDiff_X -= self.synchDiff_X[0]

            self.synchDiff_X = np.delete(self.synchDiff_X, 0)
            self.synchDiff_Y = np.delete(self.synchDiff_Y, 0)

            self.rXArr = np.delete(self.rXArr, 0)
            self.lXArr = np.delete(self.lXArr, 0)

    def _pairDistLimitSamples(self):
        """Largest allowed pair distance in samples, or None for no limit.

        Returns None unless the caller set maxDiff_us explicitly, i.e. **no pair
        is rejected on distance by default**. That is deliberate, and it is the
        second attempt at this bound:

        * The original was a fixed 250 ms that discarded silently, which
          truncated the distribution — an arecord cross-check found the offset at
          -298 ms for ~70% of a capture, invisible to the script.
        * The replacement derived a bound from "half the observed spike interval"
          via median(diff(rXArr)). **That was wrong**: rXArr holds
          `(idx + frac) * time_per_sample`, a position *within the capture
          buffer*, not a point on an absolute timeline. Its consecutive
          differences are a small near-zero quantity, not the ~1 s spike
          interval, so the bound came out tiny and rejected ~48% of all pairs
          (2825 pairFar against 2983 accepted, 2026-09-08). Same class of bias as
          the bug it replaced, from a misreading of what rXArr contains.

        Both indices necessarily come from the same capture buffer, so "is this
        one spike event" cannot be decided from their distance. It is decided from
        the step in the measurement itself - MISPAIR_STEP_LIMIT_US in add(), which
        separates cleanly (p50 32 us for good pairs against 2831 us for mispaired
        ones). pairDist_us is still printed, but as a wiring/level sanity value
        only: on 2026-09-15 it read 0 on two of four known-mispaired samples.
        """
        if self.maxDiff_us is None:
            return None
        return (self.maxDiff_us * self.capRate) / 1000000

    def add(self, indata):
        capR = indata[:,0]
        capL = indata[:,1]

        capRIdx = self._find_spike(capR, "R")
        capLIdx = self._find_spike(capL, "L")

        if capRIdx is None or capLIdx is None:
            self.currentTime_sec += float(len(capR)) / float(self.capRate)
            self._limitdata()
            self._warnIfQuiet()
            return

        # Pair distance. Reported on every accepted line so the "same spike
        # event?" judgement can be made downstream, where it is reversible.
        sample_diff = abs(capRIdx - capLIdx)
        pairDist_us = (float(sample_diff) * 1000000.0) / float(self.capRate)

        max_allowed_diff = self._pairDistLimitSamples()
        if max_allowed_diff is not None and sample_diff > max_allowed_diff:
            # Beyond half a spike interval the two channels cannot be shown to be
            # the same event, so this one really is unusable — but it is counted.
            self.rejectCounts["pairFar"] += 1
            print("Spike pair too far apart: {} samples ({:.0f} us, max {}) "
                  "[pairFar={}]".format(sample_diff, pairDist_us,
                                        int(max_allowed_diff),
                                        self.rejectCounts["pairFar"]))
            self.currentTime_sec += float(len(capR)) / float(self.capRate)
            self._limitdata()
            self._warnIfQuiet()
            return

        # Sub-sample zero-crossing interpolation for both channels
        rX_us = self._zero_crossing_us(capR, capRIdx)
        lX_us = self._zero_crossing_us(capL, capLIdx)

        synchDiff_us = rX_us - lX_us

        suspect = False
        suspectReason = ""

        # Validate: reject if inter-spike interval deviates too much from expected
        if len(self.rXArr) > 3:
            expected_interval = np.median(np.diff(self.rXArr))
            rInterval = rX_us - self.rXArr[-1]
            lInterval = lX_us - self.lXArr[-1]
            rD = rInterval - expected_interval
            lD = lInterval - expected_interval

            # A single-channel interval jump means the two channels latched onto
            # different spike events, so this sample's synchDiff is meaningless.
            # It is REPORTED, not rejected.
            #
            # Rejecting here (which this code did until 2026-09-06) is wrong twice
            # over. It throws the sample away irreversibly at capture time, when
            # the same judgement can be made from the step in the measurement -
            # and it self-perpetuates, because a rejected sample is not appended
            # to rXArr, so the next interval spans two capture buffers and
            # re-trips the gate. Measured effect: ~50% of samples discarded, and
            # once tripped it stayed tripped until the process was restarted,
            # while still printing plausible-looking numbers.
            #
            # So the sample is still kept in rXArr/lXArr and still printed. What
            # changed on 2026-09-15 is only that it no longer reaches the mean,
            # the plot and the .npy: `suspect` already did that, and this gate now
            # sets it. Nothing is discarded irreversibly - the raw value stays in
            # the log, marked.
            #
            # rD - lD is the step in synchDiffTime_us since the previous accepted
            # sample: expected_interval cancels, leaving
            # (rX - lX) - (rX_prev - lX_prev). That is why it is the right
            # discriminator and pairDist_us is not - both spike indices come from
            # the same capture buffer, so a pair can be mutually consistent and
            # still belong to the wrong event. On the 2026-09-15 bench run
            # pairDist_us read 0 on two of the four mispaired samples.
            #
            # The two tests this replaced compared each channel's deviation
            # against expected_interval separately, so they only fired when ONE
            # channel jumped. Both of the 2026-09-15 events moved both channels
            # and went unflagged.
            step_us = abs(rD - lD)
            if step_us > MISPAIR_STEP_LIMIT_US:
                self.rejectCounts["mispair"] += 1
                if self.verbose:
                    print("Mispair: measurement stepped {:.0f} us (limit {}) "
                          "- suspect sample".format(step_us, MISPAIR_STEP_LIMIT_US))
                suspect = True
                suspectReason = "mispair, stepped {:.0f} us".format(step_us)

            meanY = statistics.mean(self.synchDiff_Y) if len(self.synchDiff_Y) else 0.0
        else:
            meanY = 0.0

        if self.storeGap:
            # Suspect by construction: the previous pass wrote the PDF/npy and
            # starved the capture. Do not rely on the mispair gate to notice.
            if self.verbose:
                print("First sample after a file store - suspect by construction")
            suspect = True
            suspectReason = "first after file store"
            self.storeGap = False

        # rXArr/lXArr get the sample even when it is suspect, deliberately.
        # Withholding it here is what made the pre-2026-09-06 gate
        # self-perpetuating: a missing entry makes the NEXT interval span two
        # capture buffers, which re-trips the test, and once tripped it stayed
        # tripped until the process restarted. Interval continuity must not
        # depend on whether a sample was usable.
        self.rXArr = np.append(self.rXArr, rX_us)
        self.lXArr = np.append(self.lXArr, lX_us)

        sTimeInt = int(np.round(synchDiff_us))

        if suspect:
            # Printed, so the log keeps the full raw record and nothing is hidden
            # — but kept out of synchDiff_X/Y, which feed the plot, the .npy and
            # the running mean. A mispaired sample is not a measurement of
            # anything, and at ~0.5 s it dwarfs the +-100 us signal on the plot.
            self.rejectCounts["suspect"] += 1
            print("synchDiffTime_us: {:4d}  SUSPECT ({}) pairDist_us:{:5.0f} "
                  "[suspect={}]".format(sTimeInt, suspectReason, pairDist_us,
                                        self.rejectCounts["suspect"]))
            self.currentTime_sec += float(len(capR)) / float(self.capRate)
            self._limitdata()
            self._maybeStore()
            return

        timestamp_sec = self.currentTime_sec + float(capRIdx) / float(self.capRate)
        self.synchDiff_X = np.append(self.synchDiff_X, timestamp_sec)
        self.synchDiff_Y = np.append(self.synchDiff_Y, synchDiff_us)

        self.sampleN += 1
        self.lastAcceptTime = time.time()
        print("synchDiffTime_us: {:4d}  Mean: {:4.0f} pairDist_us:{:5.0f} N:{}".format(
            sTimeInt, np.round(meanY), pairDist_us, self.sampleN))

        self.currentTime_sec += float(len(capR)) / float(self.capRate)
        self._limitdata()
        self._maybeStore()

    def _maybeStore(self):
        """Store on schedule, and flag that the next sample crossed a capture gap."""
        if (self.lastStorageTime + self.storeInterval_sec) < time.time():
            self._reportRejections()
            self.storeResult()
            self.lastStorageTime = time.time()
            self.storeGap = True


    def _find_spike(self, channel, chName="?"):
        """Find a single spike in a channel buffer.

        Returns the sample index of the last negative sample before the
        zero-crossing (rising edge), or None if no valid spike found.
        """
        # Reject buffers whose peak is far below what this channel has recently
        # been producing. See the reasoning in __init__ — neither an absolute
        # level nor an SNR against the buffer median works here.
        peak = float(np.max(np.abs(channel.astype(np.float64))))
        ref = max(peak, self.refPeak.get(chName, 0.0) * self.trigDecay)
        self.refPeak[chName] = ref
        threshold = max(ref * self.trigPeakFrac, self.trigAbsFloor)
        if peak < threshold:
            self.rejectCounts["noise"] += 1
            if self.verbose:
                print("Just noise? {} peak:{:.0f} < {:.0f} (refPeak:{:.0f} x{:.2f}) "
                      "[noise={}]".format(chName, peak, threshold, ref,
                                          self.trigPeakFrac,
                                          self.rejectCounts["noise"]))
            return None

        # Find steepest rising edge (max positive gradient)
        grad = np.gradient(channel.astype(np.float64))
        idx = int(np.argmax(grad))

        # Walk back to find last negative sample before zero-crossing
        while idx > 0 and channel[idx] > 0:
            idx -= 1

        if idx == 0:
            self.rejectCounts["noSpike"] += 1
            if self.verbose:
                print("Max gradient at fishy position (walked back to sample 0)")
            return None

        # Validate: must be a clean bipolar transition
        # (negative samples before, positive samples after)
        w = min(10, idx, len(channel) - idx - 2)
        if w < 3:
            self.rejectCounts["noSpike"] += 1
            if self.verbose:
                print("Spike too close to buffer edge (window {})".format(w))
            return None

        num_high = int(np.sum(channel[idx + 1:idx + w + 1] >= 0))
        num_low = int(np.sum(channel[idx - w:idx] < 0))

        if num_high < w - 1 or num_low < w - 1:
            self.rejectCounts["noSpike"] += 1
            if self.verbose:
                print("Failed 'middle of square' check. L/H: {}/{} of {}".format(
                    num_low, num_high, w))
            return None

        return idx

    def _zero_crossing_us(self, channel, idx):
        """Compute sub-sample zero-crossing time in microseconds.

        Linear interpolation between channel[idx] (negative) and
        channel[idx+1] (positive).
        """
        time_per_sample = 1000000.0 / float(self.capRate)
        y0 = float(channel[idx])
        y1 = float(channel[idx + 1])
        if abs(y1 - y0) > 0:
            frac = -y0 / (y1 - y0)
        else:
            frac = 0.0
        return (idx + frac) * time_per_sample




def main():
    import sys

    f = wavio.read(sys.argv[1])
    print("Opened: {} ({},{})".format(sys.argv[1], f.rate, f.sampwidth))

    o = NIMRUM_MEAS_SPIKES(capRate=f.rate)
    o.add(f.data)

    o.storeResult()
    plt.show()

if __name__ == '__main__':
    main()
