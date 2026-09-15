"""
nimRumToneGen — Signal generation functions for test tones.

Generates sinus, chirp, spike, and silence waveforms as numpy int32 arrays
suitable for the nimRumAudioSource packet protocol (32-bit samples).
"""

import numpy as np


# Maximum amplitude for 32-bit signed audio (with safety margin)
_AMPLITUDE_MAX_32BIT = (2**31) - 2


def get_sinus(
    freq: float = 1000.0,
    duration_s: float = 1.0,
    sample_rate: int = 48000,
    volume: float = 1.0,
) -> np.ndarray:
    """Generate a sine wave as int32 samples.

    Produces an integer number of periods so the signal loops cleanly.

    Args:
        freq: Frequency in Hz.
        duration_s: Approximate duration in seconds.
        sample_rate: Sample rate in Hz.
        volume: Volume fraction (0.0 to 1.0).

    Returns:
        Numpy int32 array of audio samples.
    """
    amp = int(round(_AMPLITUDE_MAX_32BIT * _clamp_volume(volume)))
    samples_per_period = float(sample_rate) / float(freq)
    total_periods = int(round(freq * duration_s))
    total_samples = int(round(samples_per_period * total_periods))

    t = np.arange(0, total_samples)
    val = amp * np.sin(2.0 * np.pi * t * freq / sample_rate)

    # Dither: randomly round up/down to hide quantization artifacts
    noise = np.random.random(total_samples)
    return np.rint(noise + val).astype(np.int32)


def get_chirp(
    freq_start: float = 20.0,
    freq_stop: float = 20000.0,
    duration_s: float = 60.0,
    sample_rate: int = 48000,
    volume: float = 1.0,
) -> np.ndarray:
    """Generate a logarithmic chirp sweep as int32 samples.

    Uses the Farina exponential swept sine formula for correct energy
    distribution across frequencies (equal energy per octave).

    Args:
        freq_start: Start frequency in Hz.
        freq_stop: End frequency in Hz.
        duration_s: Duration in seconds.
        sample_rate: Sample rate in Hz.
        volume: Volume fraction (0.0 to 1.0).

    Returns:
        Numpy int32 array of audio samples.
    """
    amp = int(round(_AMPLITUDE_MAX_32BIT * _clamp_volume(volume)))
    total_samples = int(round(sample_rate * duration_s))

    t = np.linspace(0.0, duration_s, total_samples)
    L = duration_s / np.log(freq_stop / freq_start)

    # Farina log sweep: phase = 2*pi*f1*L * (exp(t/L) - 1)
    phi = 2.0 * np.pi * freq_start * L * (np.exp(t / L) - 1.0)
    raw = np.sin(phi) * amp

    noise = np.random.random(total_samples)
    return np.rint(noise + raw).astype(np.int32)


def get_spike(
    freq: float = 1000.0,
    sample_rate: int = 48000,
    volume: float = 1.0,
) -> np.ndarray:
    """Generate a single bipolar spike (one half-cycle low, one half-cycle high).

    Args:
        freq: Frequency that determines pulse width in Hz.
        sample_rate: Sample rate in Hz.
        volume: Volume fraction (0.0 to 1.0).

    Returns:
        Numpy int32 array of audio samples.
    """
    amp = int(round(_AMPLITUDE_MAX_32BIT * _clamp_volume(volume)))
    half_width = int(sample_rate / (2 * freq))

    low = np.full(half_width, -amp, dtype=np.int32)
    high = np.full(half_width, amp, dtype=np.int32)
    return np.concatenate([low, high])


def get_silence(
    duration_s: float = 1.0,
    sample_rate: int = 48000,
) -> np.ndarray:
    """Generate silence as int32 samples.

    Args:
        duration_s: Duration in seconds.
        sample_rate: Sample rate in Hz.

    Returns:
        Numpy int32 array of zeros.
    """
    total_samples = int(round(sample_rate * duration_s))
    return np.zeros(total_samples, dtype=np.int32)


def _clamp_volume(volume: float) -> float:
    """Clamp volume to [0.0, 1.0] range."""
    return max(0.0, min(1.0, volume))
