"""Calibration profile data structures and persistence (YAML)."""

from dataclasses import dataclass, field, asdict
from typing import List, Tuple

import yaml


@dataclass
class EQBand:
    """A single parametric EQ band.

    Attributes:
        type: Filter type ('Peaking', 'Lowshelf', 'Highshelf',
              'Highpass', 'Lowpass').
        freq: Center/corner frequency in Hz.
        gain: Gain in dB (negative = cut, positive = boost).
        q: Q factor (bandwidth).
    """

    type: str
    freq: float
    gain: float
    q: float


@dataclass
class CalibrationProfile:
    """Full calibration profile for a speaker.

    Attributes:
        speaker: Speaker/client name (matches txConfig client name).
        sample_rate: Sample rate used during measurement.
        enabled: Whether this profile should be applied.
        speaker_type: Type of speaker ('full-range' or 'subwoofer').
        filters: List of EQ bands to apply.
    """

    speaker: str
    sample_rate: int = 48000
    enabled: bool = True
    speaker_type: str = 'full-range'
    filters: List[EQBand] = field(default_factory=list)


def load_profile(path: str) -> CalibrationProfile:
    """Load a calibration profile from a YAML file.

    Args:
        path: Path to the YAML profile file.

    Returns:
        Parsed CalibrationProfile instance.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the YAML structure is invalid.
    """
    with open(path, 'r') as f:
        data = yaml.safe_load(f)

    if data is None or 'calibration' not in data:
        raise ValueError(f"Invalid profile format in {path}")

    cal = data['calibration']
    bands = []
    for filt in cal.get('filters', []):
        bands.append(EQBand(
            type=filt['type'],
            freq=float(filt['freq']),
            gain=float(filt['gain']),
            q=float(filt['q']),
        ))

    return CalibrationProfile(
        speaker=cal['speaker'],
        sample_rate=int(cal.get('sample_rate', 48000)),
        enabled=bool(cal.get('enabled', True)),
        speaker_type=cal.get('speaker_type', 'full-range'),
        filters=bands,
    )


def save_profile(
    profile: CalibrationProfile,
    path: str,
    relative_level_db: float = None,
) -> None:
    """Save a calibration profile to a YAML file.

    Args:
        profile: The CalibrationProfile to persist.
        path: Destination file path.
        relative_level_db: Optional measured broadband level (dB) for
                           volume calibration reference.
    """
    filters_list = []
    for band in profile.filters:
        filters_list.append({
            'type': band.type,
            'freq': round(band.freq, 1),
            'gain': round(band.gain, 2),
            'q': round(band.q, 3),
        })

    data = {
        'speaker': profile.speaker,
        'sample_rate': profile.sample_rate,
        'enabled': profile.enabled,
        'speaker_type': profile.speaker_type,
        'filters': filters_list,
    }

    with open(path, 'w') as f:
        # Write measurement metadata as comments at top of file
        if relative_level_db is not None:
            f.write("# Calibration measurement data:\n")
            f.write(f"# relative_level_db: {relative_level_db:.2f}\n")
            f.write("#\n")
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def profile_to_biquad_coeffs(
    profile: CalibrationProfile,
) -> List[Tuple[float, float, float, float, float]]:
    """Convert all filters in a profile to biquad coefficients.

    Args:
        profile: A CalibrationProfile with filter bands.

    Returns:
        List of (b0, b1, b2, a1, a2) tuples, one per filter band.
    """
    # Import here to avoid circular dependency at module level
    from nimRum.calibration.nimRumEQMath import biquad_coeffs

    coeffs = []
    for band in profile.filters:
        coeffs.append(biquad_coeffs(
            filter_type=band.type,
            freq=band.freq,
            gain=band.gain,
            q=band.q,
            sample_rate=profile.sample_rate,
        ))
    return coeffs
