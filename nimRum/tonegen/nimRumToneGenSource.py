"""
nimRumToneGenSource — AudioSource wrapper for the tone generator.

Sends standard nimRumAudioSource UDP packets to the TX process, matching
the exact binary protocol defined in nimRumAudioSourcePkt.h.
"""

import socket
import struct
import threading
import time
from typing import Optional

import numpy as np

from nimRum.tonegen.nimRumToneGen import (
    get_chirp,
    get_silence,
    get_sinus,
    get_spike,
)

# Protocol constants (from nimRumAudioSourcePkt.h)
#
# _PKT_VERSION must equal NIM_RUM_AUDIO_SOURCE_PKT_VERSION in that header. It is the
# wire compatibility gate, pinned and independent of the nimRumPkg version — bump it
# here only when the header bumps. TX drops a mismatch and the check runs before the
# ping branch, so a wrong value here makes this generator invisible rather than noisy.
_PKT_VERSION = 1
_FRAMES_PER_PKT = 384
_SOURCE_NAME_LEN = 32
_FLAG_START = 0x01
_FLAG_STOP = 0x02
_FLAG_NO_DATA = 0x04

# Header struct format (packed, little-endian):
#   version(B) sourceId(B) numOfCh(B) flags(B) sequenceNum(I)
#   timestamp_us(Q) txPPM(f) sampleRate(I) bytesPerSample(B)
#   sourceName(32s)
#   wifiSignalDbm(b) wifiLinkQuality(B) wifiTxRetries(H) wifiRxErrors(H)
#   hostName(16s)
# Total: 1+1+1+1+4+8+4+4+1+32+1+1+2+2+16 = 79 bytes
_HEADER_FMT = "<BBBBIQfIB32sbBHH16s"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)

assert _HEADER_SIZE == 79, f"Header size mismatch: {_HEADER_SIZE} != 79"


class ToneGenSource:
    """AudioSource that generates test tones and sends them via UDP.

    Sends packets to the nimRumTx AudioSourceRx input, using the standard
    nimRumAudioSource packet format.

    Args:
        mode: Signal mode — 'sinus', 'chirp', 'chirp_loop', 'spikes', or 'buffer'.
        freq: Frequency for sinus mode in Hz.
        duration_s: Duration for chirp mode in seconds.
        volume: Volume fraction (0.0 to 1.0).
        sample_rate: Sample rate in Hz.
        num_channels: Number of output channels (1 or 2).
        source_id: AudioSource ID (default 254, reserved for tonegen).
        source_name: Name embedded in packets.
        target_host: UDP target host.
        target_port: UDP target port.
        buffer: Pre-built int32 numpy array for 'buffer' mode.
        loop: If True, loop the buffer; if False, play once (one-shot).
    """

    def __init__(
        self,
        mode: str = "sinus",
        freq: float = 1000.0,
        duration_s: float = 60.0,
        volume: float = 1.0,
        sample_rate: int = 48000,
        num_channels: int = 1,
        source_id: int = 254,
        source_name: str = "tonegen",
        target_host: str = "127.0.0.1",
        target_port: int = 53473,
        buffer: Optional[np.ndarray] = None,
        loop: bool = False,
    ) -> None:
        self._mode = mode.lower()
        self._freq = freq
        self._duration_s = duration_s
        self._volume = max(0.0, min(1.0, volume))
        self._sample_rate = sample_rate
        self._num_channels = num_channels
        self._source_id = source_id
        self._source_name = source_name
        self._target_host = target_host
        self._target_port = target_port
        self._user_buffer = buffer
        self._loop = loop

        import platform
        self._host_name_bytes = platform.node().encode("utf-8")[:16].ljust(16, b"\x00")

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._sequence_num: int = 0

        self._sock: Optional[socket.socket] = None
        self._audio_buf: Optional[np.ndarray] = None
        self._buf_pos: int = 0

    def start(self) -> None:
        """Start the tone generator thread."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._sequence_num = 0
        self._buf_pos = 0
        self._audio_buf = self._generate_audio()

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="tonegen",
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the tone generator and send a STOP packet."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def is_running(self) -> bool:
        """Check if the tone generator thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def _generate_audio(self) -> np.ndarray:
        """Generate the audio buffer based on the configured mode."""
        if self._mode == "buffer":
            if self._user_buffer is None:
                raise ValueError("buffer mode requires a 'buffer' argument")
            return self._user_buffer.astype(np.int32)
        elif self._mode == "sinus":
            # Generate 10 seconds of sinus (loops)
            return get_sinus(
                freq=self._freq,
                duration_s=10.0,
                sample_rate=self._sample_rate,
                volume=self._volume,
            )
        elif self._mode in ("chirp", "chirp_loop"):
            # 1.5s lead-in silence: gives time for source switch to happen
            # while output is silent (avoids bump/click)
            silence_lead = get_silence(1.5, self._sample_rate)
            silence_tail = get_silence(0.5, self._sample_rate)
            chirp = get_chirp(
                freq_start=20.0,
                freq_stop=20000.0,
                duration_s=self._duration_s,
                sample_rate=self._sample_rate,
                volume=self._volume,
            )
            return np.concatenate([silence_lead, chirp, silence_tail])
        elif self._mode == "spikes":
            silence = get_silence(0.5, self._sample_rate)
            spike = get_spike(
                freq=self._freq,
                sample_rate=self._sample_rate,
                volume=self._volume,
            )
            return np.concatenate([silence, spike, silence])
        else:
            raise ValueError(f"Unknown mode: {self._mode}")

    def _run_loop(self) -> None:
        """Main send loop — paces packets using absolute clock."""
        interval_s = float(_FRAMES_PER_PKT) / float(self._sample_rate)
        buf_len = len(self._audio_buf)
        is_one_shot = (self._mode in ("chirp", "spikes", "buffer")) and not self._loop

        # Send START flag on first packet
        first_packet = True
        next_send_time = time.monotonic()

        while not self._stop_event.is_set():
            now = time.monotonic()
            if now < next_send_time:
                sleep_time = next_send_time - now
                if sleep_time > 0.0001:
                    time.sleep(sleep_time)
                continue

            # Check if one-shot mode has completed
            if is_one_shot and self._buf_pos >= buf_len:
                break

            # Extract frames for this packet
            frames = self._get_frames()

            # Build flags
            flags = 0
            if first_packet:
                flags |= _FLAG_START
                first_packet = False

            # Build and send packet
            timestamp_us = int(time.time() * 1_000_000)
            pkt = self._build_packet(frames, flags, timestamp_us)
            self._send_packet(pkt)

            self._sequence_num = (self._sequence_num + 1) & 0xFFFFFFFF
            next_send_time += interval_s

        # Send STOP packet
        self._send_stop_packet()
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def _get_frames(self) -> np.ndarray:
        """Get the next FRAMES_PER_PKT frames from the audio buffer.

        Wraps around for looping modes, zero-pads at end for one-shot.
        """
        buf_len = len(self._audio_buf)
        frames = np.zeros(_FRAMES_PER_PKT, dtype=np.int32)
        is_looping = self._loop or self._mode in ("sinus", "chirp_loop")

        remaining = _FRAMES_PER_PKT
        out_pos = 0

        while remaining > 0:
            avail = buf_len - self._buf_pos
            if avail <= 0:
                if is_looping:
                    self._buf_pos = 0
                    avail = buf_len
                else:
                    # One-shot: rest stays zero
                    break

            to_copy = min(remaining, avail)
            frames[out_pos:out_pos + to_copy] = (
                self._audio_buf[self._buf_pos:self._buf_pos + to_copy]
            )
            self._buf_pos += to_copy
            out_pos += to_copy
            remaining -= to_copy

        return frames

    def _build_packet(
        self, frames: np.ndarray, flags: int, timestamp_us: int
    ) -> bytes:
        """Build a complete AudioSource packet (header + payload).

        Payload layout: channel-first int32 (all frames for ch0, then ch1, ...).
        For mono, it's simply the frames array as little-endian int32.
        """
        # Encode source name (truncated/padded to 32 bytes)
        name_bytes = self._source_name.encode("utf-8")[:_SOURCE_NAME_LEN]
        name_bytes = name_bytes.ljust(_SOURCE_NAME_LEN, b"\x00")

        header = struct.pack(
            _HEADER_FMT,
            _PKT_VERSION,           # version
            self._source_id,        # sourceId
            self._num_channels,     # numOfCh
            flags,                  # flags
            self._sequence_num,     # sequenceNum
            timestamp_us,           # timestamp_us
            0.0,                    # txPPM (no drift correction for tonegen)
            self._sample_rate,      # sampleRate
            4,                      # bytesPerSample (int32)
            name_bytes,             # sourceName[32]
            0,                      # wifiSignalDbm
            0,                      # wifiLinkQuality
            0,                      # wifiTxRetries
            0,                      # wifiRxErrors
            self._host_name_bytes,  # hostName[16]
        )

        # Payload: channel-first layout
        # For multi-channel, duplicate mono to all channels
        if self._num_channels == 1:
            payload = frames.tobytes()
        else:
            # Duplicate the mono signal to each channel
            channel_data = np.tile(frames, self._num_channels)
            payload = channel_data.astype(np.int32).tobytes()

        return header + payload

    def _send_packet(self, pkt: bytes) -> None:
        """Send a UDP packet to the target."""
        if self._sock is not None:
            try:
                self._sock.sendto(pkt, (self._target_host, self._target_port))
            except OSError:
                pass  # Non-fatal: target may not be listening yet

    def _send_stop_packet(self) -> None:
        """Send a packet with FLAG_STOP to signal end of stream."""
        silence = np.zeros(_FRAMES_PER_PKT, dtype=np.int32)
        timestamp_us = int(time.time() * 1_000_000)
        pkt = self._build_packet(silence, _FLAG_STOP, timestamp_us)
        self._send_packet(pkt)
