"""Calibration measurement engine.

Records audio from UMIK-1 via arecord, computes transfer function,
and auto-fits parametric EQ bands to flatten the speaker response.
"""

import logging
import os
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from nimRum.calibration.nimRumCalibrationProfile import (
    CalibrationProfile,
    EQBand,
    save_profile as _save_profile,
)
from nimRum.calibration.nimRumEQMath import (
    apply_biquad_to_spectrum,
    biquad_coeffs,
    smooth_spectrum,
)
from nimRum.calibration.nimRumMicCalibration import (
    apply_mic_correction,
    load_mic_calibration,
)

logger = logging.getLogger(__name__)

# Frequency range for EQ fitting — per speaker type
EQ_FIT_RANGES = {
    'full-range': (80.0, 18000.0),
    'subwoofer': (25.0, 120.0),
}

# Frequency band used to measure a speaker's relative level — per speaker type.
# Narrower than EQ_FIT_RANGES on purpose:
#   full-range: 200-8000 avoids room modes below 200 Hz and mic directivity /
#               HF rolloff above 8k. Both are noisy and would make
#               main-to-main matching worse.
#   subwoofer:  30-100 sits inside the 25-120 fit range but off the rolloff
#               edges.
# Levels from different bands are only comparable by convention, not
# physically — see the volCal item in docs/TODO.md.
LEVEL_BANDS = {
    'full-range': (200.0, 8000.0),
    'subwoofer': (30.0, 100.0),
}

# Minimum deviation from target to warrant a correction band
MIN_DEVIATION_DB = 2.0

# Default Q for corrective peaking filters
DEFAULT_Q = 2.0

# Maximum Q for fitted bands — wider bands are more robust in practice
MAX_Q = 5.0

# Correction damping: apply only this fraction of the measured deviation.
# Prevents overcorrection from measurement noise and room mode variance.
EQ_CORRECTION_FACTOR = 0.6

# Maximum gain magnitude for a single EQ band
MAX_BAND_GAIN_DB = 8.0

# Maximum total positive gain across all bands (prevents cascaded clipping)
MAX_TOTAL_BOOST_DB = 12.0

# Maximum combined peak boost across all frequencies (limits pre-gain attenuation)
# Keeps the DSP pre-gain to about -7 dB, preserving DAC SNR.
MAX_COMBINED_BOOST_DB = 6.0


@dataclass
class MeasurementResult:
    """Result of a measurement capture.

    Attributes:
        samples: Raw recorded samples as numpy array, shape (num_samples, channels).
        sample_rate: Sample rate of the recording.
        channels: Number of channels recorded.
        duration_s: Actual duration of the recording in seconds.
    """

    samples: np.ndarray
    sample_rate: int
    channels: int
    duration_s: float


@dataclass
class TransferFunction:
    """Frequency-domain transfer function result.

    Attributes:
        freqs: Frequency array in Hz.
        magnitude_db: Magnitude response in dB.
        phase_rad: Phase response in radians.
        coherence: Optional coherence estimate (0..1).
    """

    freqs: np.ndarray
    magnitude_db: np.ndarray
    phase_rad: np.ndarray
    coherence: Optional[np.ndarray] = None


class CalibrationEngine:
    """Engine for speaker calibration measurement and EQ fitting.

    Records audio from a measurement microphone (UMIK-1) using arecord,
    computes the transfer function relative to a reference signal, and
    auto-fits parametric EQ bands to correct the response.

    Example:
        engine = CalibrationEngine(mic_device='hw:1,0')
        result = engine.measure(duration_s=5.0)
        tf = engine.compute_transfer_function(reference, result.samples[:, 0])
        bands = engine.auto_fit_eq(tf, max_bands=8)
        engine.save_profile('livingroom_left', bands)
    """

    def __init__(
        self,
        mic_device: str = 'hw:1,0',
        sample_rate: int = 48000,
        mic_channels: int = 2,
    ) -> None:
        """Initialize the calibration engine.

        Args:
            mic_device: ALSA device name for the measurement mic.
            sample_rate: Sample rate in Hz.
            mic_channels: Number of input channels on the mic device.
        """
        self._mic_device = mic_device
        self._sample_rate = sample_rate
        self._mic_channels = mic_channels

    @property
    def sample_rate(self) -> int:
        """Current sample rate."""
        return self._sample_rate

    def measure(self, duration_s: float = 5.0) -> MeasurementResult:
        """Record audio from the measurement microphone.

        Uses subprocess arecord for maximum compatibility across Pi models.

        Args:
            duration_s: Duration to record in seconds.

        Returns:
            MeasurementResult with the raw captured samples.

        Raises:
            RuntimeError: If arecord fails or produces no data.
        """
        # Use a temporary WAV file to avoid pipe buffering issues
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            tmp_path = tmp.name

        try:
            cmd = [
                'arecord',
                '-D', self._mic_device,
                '-f', 'S24_3LE',
                '-r', str(self._sample_rate),
                '-c', str(self._mic_channels),
                '-d', str(int(np.ceil(duration_s))),
                '-t', 'wav',
                '--quiet',
                tmp_path,
            ]

            logger.info("Recording: %s", ' '.join(cmd))
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=duration_s + 10.0,
            )

            if result.returncode != 0:
                stderr = result.stderr.decode('utf-8', errors='replace')
                raise RuntimeError(
                    f"arecord failed (rc={result.returncode}): {stderr}"
                )

            samples = self._read_wav_24bit(tmp_path)

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        actual_duration = samples.shape[0] / self._sample_rate

        return MeasurementResult(
            samples=samples,
            sample_rate=self._sample_rate,
            channels=self._mic_channels,
            duration_s=actual_duration,
        )

    def compute_transfer_function(
        self,
        reference: np.ndarray,
        recording: np.ndarray,
        mic_cal_path: Optional[str] = None,
    ) -> TransferFunction:
        """Compute the transfer function between reference and recording.

        Divides the FFT of the recording by the FFT of the reference
        signal, with Hann windowing and fractional-octave smoothing.

        Args:
            reference: Reference signal (the played chirp/stimulus),
                       1D float array.
            recording: Recorded signal from the microphone,
                       1D float array (same length as reference, or will
                       be truncated/zero-padded).
            mic_cal_path: Optional path to a mic calibration file (.txt).
                          If provided, the mic's frequency response is
                          compensated before returning.

        Returns:
            TransferFunction with frequency, magnitude, and phase arrays.
        """
        # Ensure same length
        n = min(len(reference), len(recording))
        ref = np.asarray(reference[:n], dtype=np.float64)
        rec = np.asarray(recording[:n], dtype=np.float64)

        # Apply Hann window
        window = np.hanning(n)
        ref_w = ref * window
        rec_w = rec * window

        # FFT (real signals, use rfft)
        fft_ref = np.fft.rfft(ref_w)
        fft_rec = np.fft.rfft(rec_w)

        # Transfer function H = Rec / Ref, with regularization
        # Avoid division by zero using Wiener-like regularization
        power_ref = np.abs(fft_ref) ** 2
        reg = np.max(power_ref) * 1e-10
        H = (fft_rec * np.conj(fft_ref)) / (power_ref + reg)

        freqs = np.fft.rfftfreq(n, d=1.0 / self._sample_rate)

        magnitude_db = 20.0 * np.log10(np.abs(H) + 1e-30)
        phase_rad = np.angle(H)

        # Apply 1/6 octave smoothing to magnitude
        magnitude_db_smooth = smooth_spectrum(magnitude_db, octave_fraction=6.0)

        # Apply mic calibration correction if provided
        if mic_cal_path:
            try:
                mic_freqs, mic_corr = load_mic_calibration(mic_cal_path)
                magnitude_db_smooth = apply_mic_correction(
                    freqs, magnitude_db_smooth, mic_freqs, mic_corr,
                )
            except Exception as e:
                logger.warning("Failed to apply mic calibration: %s", e)

        return TransferFunction(
            freqs=freqs,
            magnitude_db=magnitude_db_smooth,
            phase_rad=phase_rad,
        )

    def auto_fit_eq(
        self,
        tf: TransferFunction,
        max_bands: int = 10,
        target_curve: str = 'flat',
        speaker_type: str = 'full-range',
    ) -> List[EQBand]:
        """Auto-fit parametric EQ bands to flatten the measured response.

        Iteratively finds the largest deviation from the target curve,
        fits a corrective Peaking (or Shelf) filter, applies it to the
        residual response, and repeats.

        Uses a downsampled (log-spaced) version of the transfer function
        for speed. The full-resolution data is not needed for parametric
        EQ fitting — 1000 points across 20Hz-20kHz is more than enough.

        Args:
            tf: Transfer function from compute_transfer_function().
            max_bands: Maximum number of EQ bands to fit.
            target_curve: Target response curve. Currently supports 'flat'.
            speaker_type: 'full-range' or 'subwoofer'. Controls fit range.

        Returns:
            List of EQBand instances describing the corrective EQ.
        """
        # Downsample to ~1000 log-spaced points for fast processing
        freqs_full = tf.freqs
        mag_full = tf.magnitude_db

        # Get fitting range for speaker type
        fit_min, fit_max = EQ_FIT_RANGES.get(speaker_type, EQ_FIT_RANGES['full-range'])

        # Create log-spaced frequency grid within fitting range
        f_min = max(fit_min, 20.0)
        f_max = min(fit_max, self._sample_rate / 2.0 - 1.0)
        num_points = 1000
        freqs = np.geomspace(f_min, f_max, num_points)

        # Interpolate magnitude onto the log-spaced grid
        # Only use positive frequencies above 0 Hz
        valid = freqs_full > 0
        residual = np.interp(freqs, freqs_full[valid], mag_full[valid])

        # Auto-detect passband: find where response drops significantly
        # from the average (indicates speaker rolloff / crossover).
        # Only EQ within the passband — don't fight intentional rolloff.
        passband_threshold_db = 10.0  # Drop of 10 dB = outside passband
        passband_avg = np.mean(residual[len(residual)//4:3*len(residual)//4])

        # Find upper passband limit (scan from high to low)
        upper_limit_idx = len(residual) - 1
        for idx in range(len(residual) - 1, 0, -1):
            if residual[idx] > (passband_avg - passband_threshold_db):
                upper_limit_idx = idx
                break

        # Find lower passband limit (scan from low to high)
        lower_limit_idx = 0
        for idx in range(len(residual)):
            if residual[idx] > (passband_avg - passband_threshold_db):
                lower_limit_idx = idx
                break

        # Narrow the effective fitting range to passband
        # (keep at least 1 octave of range)
        detected_low = freqs[lower_limit_idx]
        detected_high = freqs[upper_limit_idx]
        if detected_high > detected_low * 2.0:
            # Use detected passband, but don't go wider than original range
            f_min_eff = max(f_min, detected_low)
            f_max_eff = min(f_max, detected_high)
            # Mask out points outside the effective passband
            passband_mask = (freqs >= f_min_eff) & (freqs <= f_max_eff)
        else:
            passband_mask = np.ones(len(freqs), dtype=bool)

        logger.debug(
            "Passband detected: %.0f - %.0f Hz (config: %.0f - %.0f Hz)",
            detected_low, detected_high, f_min, f_max,
        )

        # Build target (flat = 0 dB relative to mean level IN passband)
        if target_curve == 'flat':
            target_level = np.mean(residual[passband_mask])
            target = np.full_like(residual, target_level)
        else:
            target = np.zeros_like(residual)

        bands: List[EQBand] = []
        total_boost = 0.0

        # Subwoofers: only cuts allowed (they're already loud enough,
        # boosting near rolloff causes mechanical distortion)
        allow_boost = speaker_type != 'subwoofer'
        max_band_gain = MAX_BAND_GAIN_DB
        max_total_boost = MAX_TOTAL_BOOST_DB
        if not allow_boost:
            max_band_gain = 0.0
            max_total_boost = 0.0

        for _ in range(max_bands):
            # Compute error (only within passband)
            error = residual - target
            # Zero out error outside passband — don't try to correct rolloff
            error[~passband_mask] = 0.0

            # Simple smoothing: moving average (fast on 1000 points)
            kernel_size = max(3, num_points // 20)
            kernel = np.ones(kernel_size) / kernel_size
            error_smooth = np.convolve(error, kernel, mode='same')

            # Find largest deviation
            idx_max = int(np.argmax(np.abs(error_smooth)))
            deviation = error_smooth[idx_max]

            if abs(deviation) < MIN_DEVIATION_DB:
                break

            # Determine filter parameters
            center_freq = freqs[idx_max]
            if center_freq < 1.0:
                break  # Avoid DC

            # Gain is inverted (cut where response is too high, boost where low)
            # Apply damping factor to avoid overcorrection from measurement noise
            gain = -deviation * EQ_CORRECTION_FACTOR
            gain = np.clip(gain, -MAX_BAND_GAIN_DB, max_band_gain)

            # Skip if gain rounds to zero (no-op band)
            if abs(round(gain, 2)) < 0.01:
                # Zero out this region so we don't get stuck
                residual[max(0, idx_max - kernel_size):
                         min(len(residual), idx_max + kernel_size)] = target[idx_max]
                continue

            # Skip boosts if not allowed
            if gain > 0 and not allow_boost:
                # Mark this deviation as handled (set residual flat here)
                # so the loop doesn't get stuck retrying it
                residual[max(0, idx_max - kernel_size):
                         min(len(residual), idx_max + kernel_size)] = target[idx_max]
                continue

            # Enforce total boost budget (cuts are unlimited)
            if gain > 0:
                remaining_boost = max_total_boost - total_boost
                if remaining_boost <= MIN_DEVIATION_DB:
                    continue  # Skip this boost, try next deviation
                gain = min(gain, remaining_boost)

            # Estimate Q from the width of the deviation
            q = self._estimate_q_from_deviation(
                error_smooth, idx_max, freqs
            )

            # Choose filter type
            filter_type = self._choose_filter_type(center_freq, speaker_type)

            band = EQBand(
                type=filter_type,
                freq=float(round(center_freq, 1)),
                gain=float(round(gain, 2)),
                q=float(round(q, 3)),
            )

            # Check combined peak boost BEFORE committing this band
            if band.gain > 0:
                trial_bands = bands + [band]
                combined_peak = self._compute_combined_peak_boost(
                    trial_bands, freqs,
                )
                if combined_peak > MAX_COMBINED_BOOST_DB:
                    # Reduce gain to fit within budget
                    headroom = MAX_COMBINED_BOOST_DB - self._compute_combined_peak_boost(
                        bands, freqs,
                    )
                    if headroom < MIN_DEVIATION_DB:
                        # No room left for boosts — skip
                        residual[max(0, idx_max - kernel_size):
                                 min(len(residual), idx_max + kernel_size)] = target[idx_max]
                        continue
                    band = EQBand(
                        type=band.type,
                        freq=band.freq,
                        gain=float(round(min(band.gain, headroom), 2)),
                        q=band.q,
                    )

            if band.gain > 0:
                total_boost += band.gain

            bands.append(band)

            # Apply this band to the residual
            coeffs = biquad_coeffs(
                filter_type=band.type,
                freq=band.freq,
                gain=band.gain,
                q=band.q,
                sample_rate=self._sample_rate,
            )
            correction = apply_biquad_to_spectrum(
                coeffs, freqs, self._sample_rate
            )
            residual = residual + correction

            logger.debug(
                "Band %d: %s @ %.1f Hz, gain=%.2f dB, Q=%.3f",
                len(bands), band.type, band.freq, band.gain, band.q,
            )

        return bands

    def save_profile(
        self,
        speaker_name: str,
        bands: List[EQBand],
        path: str = 'calibration/',
        speaker_type: str = 'full-range',
        relative_level_db: float = None,
    ) -> str:
        """Save calibration EQ bands as a YAML profile.

        Args:
            speaker_name: Name of the speaker/client.
            bands: List of EQ bands from auto_fit_eq().
            path: Directory to write the profile into.
            speaker_type: Type of speaker ('full-range' or 'subwoofer').
            relative_level_db: Optional measured broadband level (dB).

        Returns:
            Full path to the written profile file.
        """
        os.makedirs(path, exist_ok=True)
        filename = f"{speaker_name}_calibration.yaml"
        filepath = os.path.join(path, filename)

        profile = CalibrationProfile(
            speaker=speaker_name,
            sample_rate=self._sample_rate,
            enabled=True,
            speaker_type=speaker_type,
            filters=bands,
        )
        _save_profile(
            profile,
            filepath,
            relative_level_db=relative_level_db,
        )

        logger.info("Saved calibration profile: %s", filepath)
        return filepath

    def compute_relative_level(
        self,
        tf: TransferFunction,
        freq_low: float = None,
        freq_high: float = None,
        speaker_type: str = 'full-range',
    ) -> float:
        """Compute the broadband relative level from a transfer function.

        Averages the magnitude response over the speaker's own working band
        (see LEVEL_BANDS). The result is relative (dB) — compare across
        speakers of the same type to get offsets.

        Averaging is done in the power domain. A mean of dB values is a
        geometric mean of power, which under-weights peaks.

        A band-limited speaker must not be measured outside its own band:
        200-8000 Hz contains no subwoofer output at all, so a subwoofer
        measured there reads as mic noise floor. That produced the
        "+10 on LFE, -10 on fronts" clamp artefact (see docs/TODO.md).

        Args:
            tf: Transfer function from compute_transfer_function().
            freq_low: Lower bound in Hz. Defaults to the band for speaker_type.
            freq_high: Upper bound in Hz. Defaults to the band for speaker_type.
            speaker_type: 'full-range' or 'subwoofer'. Selects the default band.

        Returns:
            Average magnitude in dB over the band, or 0.0 if the band is empty.
        """
        band_low, band_high = LEVEL_BANDS.get(
            speaker_type, LEVEL_BANDS['full-range'])
        if freq_low is None:
            freq_low = band_low
        if freq_high is None:
            freq_high = band_high

        mask = (tf.freqs >= freq_low) & (tf.freqs <= freq_high)
        if not np.any(mask):
            logger.warning(
                "compute_relative_level: no measurement points in %.0f-%.0f Hz "
                "for speaker_type '%s'", freq_low, freq_high, speaker_type)
            return 0.0

        power = 10.0 ** (tf.magnitude_db[mask] / 10.0)
        return float(10.0 * np.log10(np.mean(power)))

    def _read_wav_24bit(self, path: str) -> np.ndarray:
        """Read a 24-bit WAV file into a float numpy array.

        Args:
            path: Path to the WAV file.

        Returns:
            Numpy array of shape (num_samples, channels), float64,
            normalized to [-1.0, 1.0].
        """
        with open(path, 'rb') as f:
            # Skip WAV header — find 'data' chunk
            header = f.read(44)
            # Simple WAV parser: look for 'data' subchunk
            f.seek(0)
            raw = f.read()

        # Find the 'data' marker
        data_idx = raw.find(b'data')
        if data_idx == -1:
            raise RuntimeError("No 'data' chunk found in WAV file")

        # 4 bytes after 'data' is the chunk size
        chunk_size = struct.unpack_from('<I', raw, data_idx + 4)[0]
        audio_start = data_idx + 8
        audio_bytes = raw[audio_start:audio_start + chunk_size]

        # 24-bit = 3 bytes per sample
        bytes_per_sample = 3
        total_samples = len(audio_bytes) // bytes_per_sample
        samples_per_channel = total_samples // self._mic_channels

        # Unpack 24-bit signed integers
        samples = np.zeros(total_samples, dtype=np.float64)
        for i in range(total_samples):
            offset = i * 3
            b0 = audio_bytes[offset]
            b1 = audio_bytes[offset + 1]
            b2 = audio_bytes[offset + 2]
            # Sign-extend 24-bit to 32-bit
            val = b0 | (b1 << 8) | (b2 << 16)
            if val & 0x800000:
                val -= 0x1000000
            samples[i] = val

        # Normalize to [-1.0, 1.0]
        samples /= 8388608.0  # 2^23

        # Reshape to (samples_per_channel, channels)
        samples = samples[:samples_per_channel * self._mic_channels]
        samples = samples.reshape(samples_per_channel, self._mic_channels)

        return samples

    def _compute_combined_peak_boost(
        self,
        bands: List[EQBand],
        freqs: np.ndarray,
    ) -> float:
        """Compute the maximum combined boost across all frequencies.

        Evaluates the combined magnitude response of all bands and returns
        the peak positive gain in dB. Used to enforce MAX_COMBINED_BOOST_DB.

        Args:
            bands: List of EQ bands to evaluate.
            freqs: Frequency array to evaluate on.

        Returns:
            Peak combined boost in dB (0.0 if no net boost anywhere).
        """
        if not bands:
            return 0.0

        combined = np.zeros_like(freqs)
        for band in bands:
            coeffs = biquad_coeffs(
                filter_type=band.type,
                freq=band.freq,
                gain=band.gain,
                q=band.q,
                sample_rate=self._sample_rate,
            )
            combined += apply_biquad_to_spectrum(
                coeffs, freqs, self._sample_rate
            )

        peak = float(np.max(combined))
        return max(0.0, peak)

    def _estimate_q_from_deviation(
        self,
        error: np.ndarray,
        peak_idx: int,
        freqs: np.ndarray,
    ) -> float:
        """Estimate Q factor from the width of a peak/dip in the error curve.

        Measures the -3dB width of the deviation around peak_idx and
        converts to Q. Falls back to DEFAULT_Q if measurement fails.

        Args:
            error: Error curve (smoothed).
            peak_idx: Index of the peak/dip.
            freqs: Frequency array.

        Returns:
            Estimated Q value, clamped to [0.5, 10.0].
        """
        peak_val = error[peak_idx]
        if abs(peak_val) < 0.1:
            return DEFAULT_Q

        # Find -3dB points (half the peak magnitude)
        half_level = abs(peak_val) * 0.5
        center_freq = freqs[peak_idx]

        # Search left
        left_freq = center_freq
        for i in range(peak_idx - 1, 0, -1):
            if abs(error[i]) <= half_level:
                left_freq = freqs[i]
                break

        # Search right
        right_freq = center_freq
        for i in range(peak_idx + 1, len(error)):
            if abs(error[i]) <= half_level:
                right_freq = freqs[i]
                break

        bandwidth = right_freq - left_freq
        if bandwidth <= 0 or center_freq <= 0:
            return DEFAULT_Q

        q = center_freq / bandwidth
        return float(np.clip(q, 0.5, MAX_Q))

    def _choose_filter_type(self, freq: float, speaker_type: str = 'full-range') -> str:
        """Choose filter type based on frequency and speaker type.

        Low frequencies get a Lowshelf, high frequencies get a Highshelf,
        everything else is a Peaking filter.

        Args:
            freq: Center frequency in Hz.
            speaker_type: 'full-range' or 'subwoofer'.

        Returns:
            Filter type string.
        """
        if speaker_type == 'subwoofer':
            if freq < 30.0:
                return 'Lowshelf'
            elif freq > 150.0:
                return 'Highshelf'
            return 'Peaking'

        if freq < 120.0:
            return 'Lowshelf'
        elif freq > 14000.0:
            return 'Highshelf'
        return 'Peaking'
