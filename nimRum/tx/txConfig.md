# txConfig.yaml — TX (transmitter) configuration

Everything lives under `nimRumTXConfig:`. This file is hand-edited (or edited from
the WebUI Config tab) and is the source of truth for which speakers exist, what
channels they play and how they are grouped.

TX does not write to this file during normal operation. Runtime state — the live
main volume, scene mute, groups — is either kept in memory or in the small
`nimRumSceneState.json` beside it, so a volume change can never risk the file that
holds your whole speaker configuration.

## Settings

| Key | Default | Description |
|-----|---------|-------------|
| startupVolume | 10 | Volume TX comes up with after a restart or power cut (0-127). Deliberately low so the system never wakes up loud. The live volume is runtime state and is never written back here. |
| latency_us | 100000 | Target playback latency in microseconds, applied to every speaker. Must exceed the worst-case network delay; the WebUI shows a recommended minimum per speaker. |
| codec | WAVE | Audio codec on the wire: `WAVE`, `FLAC`, `OPUS`. |
| ssh_key | id_nimrum | SSH private key filename, looked for next to this file. Used for fleet management. |
| remoteModel | LG_AKB72915207 | IR remote model for volume control. |
| keyUp | KEY_VOLUMEUP | IR remote key for volume up. Also clears mute. |
| keyDown | KEY_VOLUMEDOWN | IR remote key for volume down. Also clears mute. |
| keyMute | KEY_MUTE | IR remote key for mute. |
| logEnable | 0 | Enable TX data logging to `.dat` files. |

## Per-client settings

One entry per speaker under `clients:`. `name` must match the RX device hostname,
which is also how the device derives its own ID.

| Key | Description |
|-----|-------------|
| name | Hostname of the RX device. Must match. |
| enabled | `true`/`false`. A disabled client receives no audio. Defaults to true. |
| location | Display name in the UI, e.g. `frontLeft`. |
| layouts | Per-channel-layout mapping, see below. |
| offset_us | Per-speaker latency offset in microseconds. Use it to compensate for a speaker that is physically closer, or for a known delay difference in its audio path. |
| volStereoAdj | Volume offset applied in stereo layouts. |
| volMultiAdj | Volume offset applied in multi-channel layouts. |
| volCal | Calibration volume offset, normally written by the EQ calibration flow. |

`layouts` holds one block per channel layout announced by the audio source, so a
speaker can play a different channel and level depending on whether the source is
stereo or multi-channel:

    layouts:
      stereo:
        channel: 0
        level: -6
      5.1(side):
        channel: 4
        level: 0

## Virtual channels

Optional entries under `virtualChannels:` that synthesise a channel rather than
taking it from the audio source — a crossfade between two channels, or a test
signal. Assign a speaker to one by using its `channelNumber` in `layouts`.

| Key | Description |
|-----|-------------|
| channelNumber | Output channel number. Keep clear of the channels the source provides. |
| crossfaderChannelA | First source channel. |
| crossfaderChannelB | Second source channel. |
| crossfaderPosition | 0-100. 0 = all A, 100 = all B. |
| toneGeneratorMode | Optional test signal: `sinus`, `chirp`, `spikes`. |
| toneGeneratorFreq | Tone frequency in Hz. `sinus` only, default 1000. |
| toneGeneratorVolume | Tone volume, 0-127. |

The C encoder supports a limited number of channels, so channel numbers must stay
inside that range — the WebUI validates this and refuses out-of-range values.
