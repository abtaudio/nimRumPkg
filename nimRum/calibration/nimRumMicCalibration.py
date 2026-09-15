"""Microphone calibration file parser.

Parses UMIK-1 / miniDSP calibration files (frequency/dB correction pairs)
and applies the correction to a measured transfer function.
"""

import logging
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def load_mic_calibration(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """Load a microphone calibration file.

    Supports the standard miniDSP/UMIK format:
    - Header lines in quotes (skipped)
    - Tab or space separated: frequency_hz  correction_db

    Args:
        path: Path to the calibration .txt file.

    Returns:
        Tuple of (frequencies_hz, correction_db) as numpy arrays.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If no valid data could be parsed.
    """
    freqs = []
    corrections = []

    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and quoted header lines
            if not line or line.startswith('"'):
                continue
            # Parse frequency and correction
            parts = line.split()
            if len(parts) >= 2:
                try:
                    freq = float(parts[0])
                    corr = float(parts[1])
                    freqs.append(freq)
                    corrections.append(corr)
                except ValueError:
                    continue

    if not freqs:
        raise ValueError(f"No valid calibration data in {path}")

    logger.info("Loaded mic calibration: %s (%d points, %.0f-%.0f Hz)",
                path, len(freqs), freqs[0], freqs[-1])

    return np.array(freqs), np.array(corrections)


def apply_mic_correction(
    freqs: np.ndarray,
    magnitude_db: np.ndarray,
    mic_freqs: np.ndarray,
    mic_correction_db: np.ndarray,
) -> np.ndarray:
    """Apply microphone calibration correction to a measured magnitude response.

    Interpolates the mic correction curve onto the measurement frequency
    grid and adds it to the measured magnitude (compensating for the
    mic's non-flat response).

    Args:
        freqs: Frequency array of the measurement (Hz).
        magnitude_db: Measured magnitude in dB.
        mic_freqs: Mic calibration frequency array (Hz).
        mic_correction_db: Mic calibration correction in dB.

    Returns:
        Corrected magnitude_db array (same shape as input).
    """
    # Interpolate mic correction onto measurement frequency grid
    # Extrapolate with edge values for frequencies outside cal range
    correction = np.interp(
        freqs,
        mic_freqs,
        mic_correction_db,
        left=mic_correction_db[0],
        right=mic_correction_db[-1],
    )

    return magnitude_db + correction
