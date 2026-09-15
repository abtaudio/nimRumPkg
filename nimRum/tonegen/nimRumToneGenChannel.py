"""
nimRumToneGenChannel — Stateful tone generator for TX virtual channel injection.

Wraps the signal generation functions from nimRumToneGen into a looping
channel reader that fills TX dataOut bytearrays each interval.
"""

import numpy as np

from nimRum.tonegen.nimRumToneGen import (
    get_chirp,
    get_silence,
    get_sinus,
    get_spike,
)

# TX internal sample range is 16-bit (±32767) stored in int32 containers.
# Signal generation functions produce full 32-bit range (±2^31).
# Scale factor to convert 32-bit amplitude to 16-bit range.
_SCALE_32_TO_16 = 1.0 / 65536.0  # >>16


class ToneGenChannel:
    """Looping tone generator that fills a TX dataOut bytearray each interval.

    Generates a signal buffer at init, then .read() cycles through it
    indefinitely, wrapping around for continuous playback.

    Args:
        mode: Signal type — 'sinus', 'spikes', 'chirp', or 'buffer'.
        freq: Frequency in Hz (sinus: tone freq, spikes: pulse width).
        volume: Volume 0-127 (matches TX virtual channel config convention).
        sample_rate: Sample rate in Hz.
        buffer: Pre-built int32 numpy array for 'buffer' mode (16-bit range).
    """

    def __init__(
        self,
        mode: str = "sinus",
        freq: float = 1000.0,
        volume: int = 127,
        sample_rate: int = 48000,
        duration_s: float = 0,
        buffer: "np.ndarray | None" = None,
        one_shot: bool = False,
    ) -> None:
        self._sample_rate = sample_rate
        self._pos = 0
        self._one_shot = one_shot

        # Clamp volume to 0-127 range, convert to fraction
        vol_clamped = max(0, min(127, volume))
        vol_frac = float(vol_clamped) / 127.0

        self._buf = self._generate(mode, freq, vol_frac, duration_s, buffer)

    def read(self, frames: int, data_out_buf: bytearray) -> None:
        """Read next frames into a TX dataOut bytearray, looping.

        Fills data_out_buf with int32 samples (native byte order).
        Wraps around the internal buffer for continuous playback.
        If buffer is exhausted and _one_shot is set, zero-pads the rest.

        Args:
            frames: Number of frames to produce.
            data_out_buf: Target bytearray (must be >= frames * 4 bytes).
        """
        import ctypes

        buf_len = len(self._buf)
        out = np.zeros(frames, dtype=np.int32)

        remaining = frames
        out_pos = 0

        while remaining > 0:
            avail = buf_len - self._pos
            if avail <= 0:
                if self._one_shot:
                    break  # Zero-pad the rest (out is already zeros)
                self._pos = 0
                avail = buf_len
            to_copy = min(remaining, avail)
            out[out_pos:out_pos + to_copy] = self._buf[self._pos:self._pos + to_copy]
            self._pos += to_copy
            out_pos += to_copy
            remaining -= to_copy

        # Fast copy into bytearray using ctypes (avoids Python int unpacking)
        ctypes.memmove(
            (ctypes.c_char * len(data_out_buf)).from_buffer(data_out_buf),
            out.ctypes.data,
            frames * 4,
        )

    def _generate(
        self,
        mode: str,
        freq: float,
        vol_frac: float,
        duration_s: float,
        buffer: "np.ndarray | None",
    ) -> np.ndarray:
        """Generate the audio buffer based on mode.

        All signal functions produce 32-bit range. We scale down to 16-bit
        range to match TX internal representation.

        Returns:
            Numpy int32 array with samples in 16-bit amplitude range.
        """
        mode_lower = mode.lower()

        if mode_lower == "buffer":
            if buffer is None:
                raise ValueError("mode='buffer' requires a 'buffer' argument")
            # Assume caller provides 16-bit range already
            return buffer.astype(np.int32)
        elif mode_lower == "sinus":
            raw = get_sinus(
                freq=freq if freq else 1000.0,
                duration_s=duration_s if duration_s > 0 else 10.0,
                sample_rate=self._sample_rate,
                volume=vol_frac,
            )
        elif mode_lower == "spikes":
            raw = np.concatenate([
                get_silence(0.5, self._sample_rate),
                get_spike(freq=500.0, sample_rate=self._sample_rate, volume=vol_frac),
                get_silence(0.5, self._sample_rate),
            ])
        elif mode_lower == "chirp":
            chirp_dur = duration_s if duration_s > 0 else 60.0
            raw = np.concatenate([
                get_silence(0.5, self._sample_rate),
                get_chirp(
                    freq_start=20.0,
                    freq_stop=20000.0,
                    duration_s=chirp_dur,
                    sample_rate=self._sample_rate,
                    volume=vol_frac,
                ),
                get_silence(0.5, self._sample_rate),
            ])
        else:
            raise ValueError(f"Unknown tone generator mode: {mode}")

        # Scale from 32-bit range to 16-bit range
        scaled = (raw.astype(np.float64) * _SCALE_32_TO_16)
        return np.rint(scaled).astype(np.int32)
