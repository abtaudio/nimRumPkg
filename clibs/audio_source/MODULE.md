# nimRumAudioSource

## Purpose
Standalone audio source feeder that captures audio from a sound card (S/PDIF)
or reads from a file, and sends it over UDP to nimRumTx. Multiple instances
can run simultaneously (TV, vinyl, DJ mixer, file player). TX selects the
active source via UI or hardware button — no auto-takeover.

## Public API (C, via nimRumAudioSource_ct.so)
- `nimRumAudioSource_start(sourceName, targetHost, alsaDevice, captureMode, filePath)` — Start capture→send thread
- `nimRumAudioSource_stop()` — Stop and clean up
- `nimRumAudioSource_setVolumeDb(volumeDb)` — Set volume gain (-40 to +6 dB)
- `nimRumAudioSource_setStreaming(enable)` — Pause/resume (sends STOP/START flags)
- `nimRumAudioSource_isStreaming()` — Check streaming state
- `nimRumAudioSource_isRunning()` — Check if capture thread is alive
- `nimRumAudioSource_requestActive()` — Request to become the active source
- `nimRumAudioSource_requestDeselect()` — Request to be deselected
- `nimRumAudioSource_setChannelLayout(layout)` — Set announced channel layout
- `nimRumAudioSource_setFifoTargetSuggestion(packets)` — Suggest TX's input-FIFO pre-fill depth for this source, announced in the ping. 0 = TX decides; TX clamps and logs what it used
- `nimRumAudioSource_getStatus(ppm, seqNum, channels)` — Get runtime status

## Capture Modes
- `spdif` (0) — S/PDIF digital input, auto-detect PCM vs encoded (AC3/DTS), multichannel
- `file` (2) — Audio file playback (WAV, FLAC, etc. via libsndfile). Loops forever.

## Python entry point
- `runNimRumAudioSource` — Reads YAML config, optional `--file <path>` for file mode.
  Sets up GPIO (rotary encoder for volume, button for stream on/off), delegates audio
  work to C. Source code: `nimRum/audio_source/run_audio_source.py`.
- `runNimRumToneGen` — Pure-Python tone generator that sends AudioSource UDP packets.
  Source code: `nimRum/audio_source/run_tonegen.py`.

## Packet Protocol (UDP, port 53473)
Header (25 bytes, packed) — defined in `src/nimRumAudioSourcePkt.h`:
- `version` (1B) — Wire compatibility gate, **pinned at `1`** and deliberately independent
  of the nimRumPkg version, so upgrading the package never breaks a working source. Read it
  from `NIM_RUM_AUDIO_SOURCE_PKT_VERSION` in `src/nimRumAudioSourcePkt.h` rather than
  hardcoding it. TX drops any packet whose version differs, and the check runs *before* the
  ping is handled, so a mismatched source does not register at all — it is invisible rather
  than visibly broken. TX logs the mismatch (rate-limited) naming both versions, so check
  TX's log first if you write your own source and TX never acknowledges it.
- `sourceId` (1B) — Unique source ID (0-254)
- `numOfCh` (1B) — Active channels in this packet
- `flags` (1B) — START=0x01, STOP=0x02, NO_DATA=0x04
- `sequenceNum` (4B) — Packet counter per source
- `timestamp_us` (8B) — Source monotonic clock, microseconds
- `txPPM` (4B) — Source clock drift measured at ALSA capture
- `sampleRate` (4B) — 44100, 48000, 96000, or 192000
- `bytesPerSample` (1B) — 2 (16-bit), 3 (24-bit), or 4 (32-bit)

Payload (variable):
- samples[numOfCh][384] at bytesPerSample width, channel-first

Packet size = 25 + numOfCh × 384 × bytesPerSample.

### SRC ping

Sent on the same port, distinguished by `flags & 0x08` (SRC_PING), and carrying the
source's identity, link stats and streaming state. Two things in it are worth knowing if
you are writing your own source:

- `pkg_version_major/minor/patch` — the version of *your* source, which TX displays in
  the Web UI's Status tab as "Source pkg". It is a package version, not a nimRumLib
  version, since a source links no nimRumLib. Purely informational: nothing gates on it.
  Report 0 for major if you have no meaningful version and TX will show "unknown".
- `fifoTargetSuggestion` — pre-fill depth in packets you would like TX to hold for you.
  A source knows its own transport and TX does not: a local S/PDIF feeder wants minimum
  latency, a Wi-Fi feeder wants headroom. 0 means no opinion. TX clamps it and logs what
  it used.

TX confirms registration with a reply packet, so a source that never sees a reply has
either the wrong `version` byte or the wrong port.

## Source Layout
```
src/
├── nimRumAudioSource.c/.h     — Main: thread, packet build, file reader, UDP send, SRC ping
├── nimRumAudioSourcePkt.h     — Packet header struct and protocol constants
├── nimrum_queue.c/.h          — Self-contained FIFO queue
├── nimrum_log.h               — Logging macros
└── capture/                   — S/PDIF capture subsystem (from ALSA via ffmpeg)
    ├── capture.c/.h           — Public API (init, getData, close), queue management
    ├── spdif_decode.c/.h      — PCM vs encoded detection, decode orchestration
    ├── decode_buf_in.c/.h     — Input ring buffer for ffmpeg
    ├── decode_buf_out.c/.h    — Output buffer, PPM estimation
    ├── ffmpeg_hal.c/.h        — ffmpeg abstraction (demux + decode)
    ├── alsa_device.c/.h       — ALSA PCM device open/close/read
    └── running_avg.c/.h       — Running average filter for PPM estimation
pyLib/
└── nimRumAudioSource_ctypes.c — ctypes-friendly C wrapper (no Python dependency)
```

## Dependencies
- `libsndfile` — Audio file reading (WAV, FLAC, OGG, etc.)
- `ffmpeg` (libavcodec, libavformat, libavutil) — S/PDIF stream decode
- `libasound` (ALSA) — Audio capture from sound card
- `nimrum_queue` / `nimrum_log` — Self-contained queue and logging utilities
- `<pthread.h>` — Capture→send runs in its own thread
- `PyYAML`, `RPi.GPIO` (optional) — Python config and GPIO

## Notes
- S/PDIF mode auto-detects PCM vs encoded (AC3/DTS) and decodes multichannel.
- File mode uses absolute-clock pacing (no drift). Loops on EOF.
- Dynamic channel count — only active channels are sent.
- ~5% CPU on Raspberry Pi for S/PDIF (all audio work in C).
- Saves settings to `.last` file on shutdown for persistence.

## Future
- PCM capture at higher sample rates/bit depths (USB audio interfaces)
- Variable naming cleanup in capture/ (rename old ALSACAPTH_ prefixes)
- Adapt frames-per-packet to sample rate (576 @ 96k, 1152 @ 192k) to match
  TX interval and minimize latency. Currently hardcoded to 384 for all rates.
  Note: ALSA may not support very small read sizes reliably — the capture
  thread currently reads 1536 frames (32ms) per ALSA read, which is what
  ffmpeg needs for reliable format detection.
