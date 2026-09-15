# Measurement Machine

Optional. A nimRum system works without one. This describes how to set up a
separate device that measures what the speakers **actually** do acoustically,
rather than what they believe they are doing.

Why that distinction matters: each RX aligns playback using its own estimate of
the transmitter's clock. If that estimate carries a constant bias, the RX plays
early or late **and still reports itself perfectly in sync** — the error is in the
belief being reported. Only an observer outside the speaker can see it. That is
what this machine is.

## What it can and cannot tell you

| Question | Answer |
|----------|--------|
| Are two speakers emitting sound simultaneously? | Yes — this is the main use |
| Does a speaker have a constant offset from its peers? | Yes, with the calibration rig below |
| Total end-to-end latency, for lip-sync | Yes, but see "Lip-sync" — you may not need this machine at all |
| Is a speaker's *reported* sync accuracy honest? | Yes, by comparing it with what is measured |
| Why a speaker is offset (clock bias vs DAC group delay) | Only if you measure two units of **identical** hardware |

## Hardware

- Any SBC nimRum already runs on.
- **A sound card with both input and output.** This is the one hard requirement.
  A combined DAC+ADC HAT is the usual choice; a USB audio interface with line
  inputs also works.
- Line cables from each speaker's line-out or amplifier-out to the measurement
  inputs. Two inputs means two speakers at a time.
- Optional: a small passive analog mixer, if you want more sources than inputs.

Wired is strongly preferable to a microphone. A microphone adds the speed of
sound (≈2.9 µs/mm, so a 10 cm position error is 290 µs — larger than the effect
being measured), plus room reflections and driver response. Cables remove all of
that.

## Install

```bash
sudo apt-get install libportaudio2 libopenblas-dev
sudo pip3 install sounddevice wavio      # as root if run from systemd
pip3 install nimrum-*.whl
```

Find the input device index, then start capture:

```bash
runNimRumMeas --listDevices
runNimRumMeas -d <index> -f 48000 -m synchErr -R /home/pi/LOGS
```

`-m synchErr` expects a **spike** signal on both inputs and reports the timing
difference between them. Generate it with the tone generator on a virtual channel
(see the Config tab in the Web UI), routed to the two speakers under test.

Output line format:

```
synchDiffTime_us:  527  Mean:  519  DistanceToMean:    8 Diff R/L:  -4/ -9 ...
```

`synchDiffTime_us` is the inter-speaker offset. Watch its **spread**, not its
absolute value — a constant offset is calibration, a varying one is a sync problem.

### Notes that will save you time

- If run from systemd, use `Environment=PYTHONUNBUFFERED=1` and write stdout to a
  file rather than the journal, or downstream tools see nothing.
- Devices with NTP disabled (which is normal for nimRum, since NTP would fight the
  clock sync) have wrong clocks. Timestamp measurement output on the machine
  collecting it, not on the device producing it.

## Two ways to use it

### 1. Relative check — passive, no disruption

Capture two speakers while the system plays normally. This gives the offset
*between* them and how stable it is. Good enough to answer "is my system in sync",
and it requires no change to the running system.

### 2. Static offset calibration — active rig, one speaker at a time

For finding each speaker's constant offset so it can be compensated. More work,
and not something most installations need.

The measurement machine temporarily becomes the transmitter and the audio source,
and you bring it to each speaker in turn.

1. **Loop one of the machine's own outputs back to its second input.** This is the
   zero-reference: it cancels the machine's own capture latency, which is otherwise
   an unknown constant.
2. **Run an RX on the measurement machine with `nic: lo`** in `rxConfig.yaml`.
   Loopback implies the RX and TX share one physical clock, so nimRum skips network
   clock sync entirely for that RX — its clock error is exactly zero by
   construction, not merely small. That is what makes it a valid reference.
3. **Stop the main transmitter.** Not optional: RX devices accept audio from
   whichever transmitter answers and do not filter by transmitter identity, so two
   running at once will interleave into the same speaker.
4. Start the temporary transmitter and source on the measurement machine. Within
   about half a second each RX notices it has no audio, broadcasts a discovery
   ping, and re-attaches to whichever transmitter replies. Expect one restart per
   RX as it picks up the new configuration; this is normal.
5. **List the speaker under test in the temporary transmitter's config**, with a
   channel mapping. A speaker that is not in the client list is never sent audio.
6. Connect the speaker under test to the first input, measure, then move on.

**Keep the machine at the same distance from each speaker.** The one-way network
delay varies between devices by a few hundred microseconds — the same order as the
offset being measured — so measuring every speaker from one fixed spot buries the
signal you are looking for. Constant, short distance is the entire point of walking
the rig around.

**Start with two units of identical hardware.** Two speakers with the same board,
DAC and driver have the same DAC group delay, so anything left over is clock bias.
That both validates the rig and separates the two possible causes. Skip this and you
cannot tell them apart.

### Applying the result

Per-speaker compensation goes in `rxConfig.yaml`:

```yaml
  staticDelay_us: 0        # positive delays this speaker
```

There is also a per-client `offset_us` on the transmitter side. Use one or the
other consistently, not both.

## Lip-sync

If all you want is the audio delay to set on a TV, you probably do **not** need
this machine.

Total latency is the sum of the source buffering, the transmitter buffering, the
network hop and the receiver pipeline. The receiver already computes its own
contribution continuously, and the dominant term is the receiver pipeline at
roughly 90 ms.

The clean way to measure the whole chain is to run a temporary RX **on the source
device itself**. Source and receiver then share one clock, so the difference
between "sample created" and "sample due out of the DAC" is a plain duration with
no clock synchronisation involved. Only the analog delay after the DAC is missed,
which is well under a millisecond.

Use a real output device for this, not the dummy PCM mode — dummy simulates a
buffer of a different size than a real card, so it will not give you a latency
figure you can trust.

## Interpreting results honestly

- **A constant offset is not a fault.** It is a hardware property — DAC filter
  group delay and network path asymmetry — and it reproduces across restarts. It
  is a calibration constant, not something to tune the sync filters for.
- **A varying offset is a fault**, and worth chasing.
- **Absolute latency is not a sync problem.** It is common to every speaker, so it
  affects lip-sync only, never inter-speaker timing.
- A single measurement session proves little. Single devices have been observed
  swinging widely from one session to the next with no configuration change at all,
  so repeat before concluding.
