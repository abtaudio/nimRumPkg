# rxConfig.yaml — RX (speaker) configuration

Every setting lives under a single `nimRumRXConfig:` key. Any value you leave out
gets a default, and the defaults are printed at startup.

Where to put the file:

1. the same folder you found `rxConfig.last` in, renamed to `rxConfig.yaml`, or
2. `$HOME/nimRum/` — `/root/nimRum/` when running as root

On exit the RX writes `rxConfig.last` containing the settings it actually used,
plus this text. Rename it to `rxConfig.yaml` to adopt those settings.

## Settings

| Key | Default | Description |
|-----|---------|-------------|
| nic | wlan0 | Network interface used to reach TX and carry audio. Every socket is bound to it, TX is found by broadcasting on it, and WiFi link stats are read from it. `lo`, `eth0`, `wlan0`, ... |
| pcmDevName | default | ALSA playback device. Prefer an `hw:...` device such as `hw:CARD=MyDAC,DEV=0` so no resampling or mixing is inserted. Empty string picks the first `hw:` device found. |
| volumeDevName | default | ALSA mixer device for hardware volume. Empty string tries to match `pcmDevName`, which is the recommended setting. |
| volumeCtrl | NIMRUM_SFT_VOL | Volume curve for your DAC: `NIMRUM_SFT_VOL` (software volume, safe on any card), `PCM_5122`, `PCM5102A`, `TAS_5756M`. See `nimRumRxVolumeConversion.py`. |
| outputChannelEnable | 3 | Bit mask of enabled outputs: 1 = left, 2 = right, 3 = both. Use 1 or 2 to power down the unused amplifier channel. |
| pcmMode | auto | ALSA access method: `auto`, `mmap`, `writei`, `dummy`. See below. |
| forceS16 | 0 | 1 forces ALSA format S16_LE instead of the widest format the card advertises. Workaround for cards that claim S32/S24 support but output garbage. |
| staticDelay_us | 0 | Static latency offset in microseconds, added to this speaker only. |
| printLevel | note | Printout verbosity: `err`, `warn`, `note`, `debug`. `debug` adds the periodic sync status line. |
| logEnable | 0 | Data logging to `.dat` files for analysis. 0 = off, 1 = core logs, 2 = core plus filter internals (CPU expensive, buffers wrap in minutes). |
| logPath | folder of the config file | Where `.dat` files are written. |
| ssh_user | "" | SSH username for this device, reported to TX for fleet management. The RX process itself usually runs as root while SSH logs in as a normal user. |

## pcmMode

`auto` picks the access method from the ALSA library version: mmap below 1.2.10,
`snd_pcm_writei` from 1.2.10 onwards. Both are non-blocking — the PCM is opened
with `SND_PCM_NONBLOCK` and they differ only in the write call and in how
readiness is awaited.

Some sound cards only work properly with one of the two. The wrong one shows up
as constant XRUNs, or worse, as a device that reports itself locked while
drifting. If you see either, force the other one. The effective choice is printed
at startup:

    HalPlay: pcmMode 3 (forced) -> ALSA access writei, forceS16 0, dev 'hw:CARD=...'

`dummy` runs the whole pipeline without a sound card, for development.

## printLevel and logEnable are different things

`printLevel` controls text printed to stdout or the journal. `logEnable` controls
binary `.dat` files written for offline analysis. Raising `printLevel` costs
almost nothing; `logEnable: 2` costs real CPU and its buffers wrap after a few
minutes.
