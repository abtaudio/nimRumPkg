"""EQ math helpers: biquad coefficient computation and spectrum utilities.

Based on Robert Bristow-Johnson's Audio EQ Cookbook.
No scipy dependency — only numpy.
"""

import numpy as np
from typing import Tuple


def biquad_coeffs(
    filter_type: str,
    freq: float,
    gain: float,
    q: float,
    sample_rate: int,
) -> Tuple[float, float, float, float, float]:
    """Compute biquad filter coefficients (normalized).

    Uses the Audio EQ Cookbook formulas by Robert Bristow-Johnson.

    Args:
        filter_type: One of 'Peaking', 'Lowshelf', 'Highshelf',
                     'Highpass', 'Lowpass'.
        freq: Center/corner frequency in Hz.
        gain: Gain in dB (used by Peaking, Lowshelf, Highshelf).
        q: Q factor.
        sample_rate: Sample rate in Hz.

    Returns:
        Tuple of (b0, b1, b2, a1, a2) — normalized so that a0 = 1.

    Raises:
        ValueError: If filter_type is not recognized.
    """
    w0 = 2.0 * np.pi * freq / sample_rate
    cos_w0 = np.cos(w0)
    sin_w0 = np.sin(w0)
    alpha = sin_w0 / (2.0 * q)
    A = 10.0 ** (gain / 40.0)  # sqrt of linear gain

    if filter_type == 'Peaking':
        b0 = 1.0 + alpha * A
        b1 = -2.0 * cos_w0
        b2 = 1.0 - alpha * A
        a0 = 1.0 + alpha / A
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha / A

    elif filter_type == 'Lowshelf':
        two_sqrt_a_alpha = 2.0 * np.sqrt(A) * alpha
        b0 = A * ((A + 1.0) - (A - 1.0) * cos_w0 + two_sqrt_a_alpha)
        b1 = 2.0 * A * ((A - 1.0) - (A + 1.0) * cos_w0)
        b2 = A * ((A + 1.0) - (A - 1.0) * cos_w0 - two_sqrt_a_alpha)
        a0 = (A + 1.0) + (A - 1.0) * cos_w0 + two_sqrt_a_alpha
        a1 = -2.0 * ((A - 1.0) + (A + 1.0) * cos_w0)
        a2 = (A + 1.0) + (A - 1.0) * cos_w0 - two_sqrt_a_alpha

    elif filter_type == 'Highshelf':
        two_sqrt_a_alpha = 2.0 * np.sqrt(A) * alpha
        b0 = A * ((A + 1.0) + (A - 1.0) * cos_w0 + two_sqrt_a_alpha)
        b1 = -2.0 * A * ((A - 1.0) + (A + 1.0) * cos_w0)
        b2 = A * ((A + 1.0) + (A - 1.0) * cos_w0 - two_sqrt_a_alpha)
        a0 = (A + 1.0) - (A - 1.0) * cos_w0 + two_sqrt_a_alpha
        a1 = 2.0 * ((A - 1.0) - (A + 1.0) * cos_w0)
        a2 = (A + 1.0) - (A - 1.0) * cos_w0 - two_sqrt_a_alpha

    elif filter_type == 'Lowpass':
        b0 = (1.0 - cos_w0) / 2.0
        b1 = 1.0 - cos_w0
        b2 = (1.0 - cos_w0) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

    elif filter_type == 'Highpass':
        b0 = (1.0 + cos_w0) / 2.0
        b1 = -(1.0 + cos_w0)
        b2 = (1.0 + cos_w0) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

    else:
        raise ValueError(
            f"Unknown filter_type '{filter_type}'. "
            "Use: Peaking, Lowshelf, Highshelf, Highpass, Lowpass."
        )

    # Normalize by a0
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def apply_biquad_to_spectrum(
    coeffs: Tuple[float, float, float, float, float],
    freqs: np.ndarray,
    sample_rate: int,
) -> np.ndarray:
    """Compute magnitude response (dB) of a biquad at given frequencies.

    Evaluates H(z) = (b0 + b1*z^-1 + b2*z^-2) / (1 + a1*z^-1 + a2*z^-2)
    on the unit circle at the specified frequencies.

    Args:
        coeffs: Tuple (b0, b1, b2, a1, a2) from biquad_coeffs().
        freqs: Array of frequencies in Hz to evaluate.
        sample_rate: Sample rate in Hz.

    Returns:
        Magnitude response in dB at each frequency.
    """
    b0, b1, b2, a1, a2 = coeffs
    w = 2.0 * np.pi * freqs / sample_rate
    z = np.exp(1j * w)
    z_inv = 1.0 / z
    z_inv2 = z_inv * z_inv

    numerator = b0 + b1 * z_inv + b2 * z_inv2
    denominator = 1.0 + a1 * z_inv + a2 * z_inv2

    H = numerator / denominator
    magnitude_db = 20.0 * np.log10(np.abs(H) + 1e-30)
    return magnitude_db


def smooth_spectrum(
    magnitude_db: np.ndarray,
    octave_fraction: float = 3.0,
) -> np.ndarray:
    """Apply fractional-octave smoothing to a frequency response.

    Uses a variable-width rectangular window in the log-frequency domain.
    Each bin is averaged with neighbors within +/- half of 1/octave_fraction
    octave.

    Args:
        magnitude_db: Magnitude response in dB (linear frequency spacing
                      assumed, index 0 = DC or first bin).
        octave_fraction: Smoothing width as fraction of an octave.
                         3.0 = 1/3 octave smoothing (default).

    Returns:
        Smoothed magnitude array (same length as input).
    """
    n = len(magnitude_db)
    if n < 3:
        return magnitude_db.copy()

    smoothed = np.empty(n)
    # Half-width in octaves
    half_oct = 0.5 / octave_fraction

    for i in range(n):
        if i == 0:
            smoothed[i] = magnitude_db[i]
            continue

        # Compute frequency-proportional window bounds
        # Lower and upper bin indices for +/- half_oct octaves
        ratio_low = 2.0 ** (-half_oct)
        ratio_high = 2.0 ** half_oct
        i_low = max(1, int(np.floor(i * ratio_low)))
        i_high = min(n - 1, int(np.ceil(i * ratio_high)))

        smoothed[i] = np.mean(magnitude_db[i_low:i_high + 1])

    return smoothed
