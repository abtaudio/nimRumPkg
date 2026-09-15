# audioSourceConfig.yaml — audio source (feeder) configuration

An audio source captures audio and sends it to TX over UDP. Several sources can
exist at once; TX plays one at a time and you choose which, from the WebUI or with
a button on the source itself.

On exit the feeder writes `audioSourceConfig.last` containing the settings it
actually used, plus this text. Copy it to `audioSourceConfig.yaml` to adopt them.

The source ID is derived from the hostname, so every source needs a unique
hostname but no ID has to be configured.

## Settings

| Key | Default | Description |
|-----|---------|-------------|
| sourceName | "" | Human-readable name shown in the UI, e.g. `TV`, `Vinyl`, `DJ Mixer`. |
| alsaDevice | "" | ALSA capture device. `arecord -l` lists them. Examples: `default`, `hw:1,0`, `plughw:CARD=USB,DEV=0`. Empty auto-detects the first `hw:` device. |
| captureMode | spdif | `spdif` = S/PDIF input, auto-detecting PCM versus encoded (AC3, DTS) and adapting the channel count to the stream. `pcm` = analog or USB input. `file` = play an audio file. |
| pcmSampleRate | 48000 | Capture rate in Hz for `pcm` mode: 44100, 48000, 96000, 192000. Ignored in `spdif` mode, where the rate follows the ALSA capture clock, and in `file` mode, where it comes from the file header. |
| targetHost | "" | TX address. Empty auto-discovers TX by broadcast. Use `127.0.0.1` when the feeder runs on the same device as TX, for lower latency. |
| channelLayoutFallback | stereo | Layout announced when the stream's layout cannot be detected, which is the normal case for analog input. E.g. `stereo`, `5.1`. |
| volumeDb | 0 | Gain in dB, -6 to +6. 0 is unity. Saved and restored when a rotary encoder is used. |
| txFifoTarget | 0 | How many packets TX should pre-fill in its input FIFO for this source, announced in the ping. This buffer absorbs arrival jitter on the source-to-TX hop and its depth is added to the latency of every speaker, so a feeder on the TX box itself wants it low and one across Wi-Fi wants headroom — and TX cannot tell which it has. `0` leaves the choice to TX. One packet is 8 ms at 44.1/48 kHz and 6 ms above that. TX clamps the value to what its FIFO can hold and logs the value it used, so check the TX log after changing it. |

## Optional GPIO hardware

Each of these is a block with an `enabled` flag, all disabled by default. Pin
numbers are BCM numbering.

| Key | Default | Description |
|-----|---------|-------------|
| rotaryEncoder.enabled | false | Rotary encoder for volume. |
| rotaryEncoder.gpioA | 17 | Encoder pin A. |
| rotaryEncoder.gpioB | 27 | Encoder pin B. |
| button.enabled | false | Button that asks TX to make this source active, and to release it again. |
| button.gpio | 16 | Button pin. Active low with internal pull-up, 300 ms debounce. Wire the button between this pin and ground. |
| led.enabled | false | Status LED. |
| led.gpioBlue | 26 | Blue LED pin. Blinks briefly once per second while streaming. |

## How source selection works

A source announces itself to TX with a ping every 30 seconds and TX replies
telling it whether to stream. Nothing takes over automatically while another
source is active — selection is always explicit, from the UI or from the button.
On a freshly started TX with no selection yet, the first source to ping becomes
active.

Pressing the button on an active source releases it, and TX hands back to the
source that was active before, if it is still present.
