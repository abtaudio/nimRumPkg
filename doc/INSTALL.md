# Installation Guide

## Overview

nimRum runs on Raspberry Pi devices over WiFi. You need at minimum:
- **1 TX device** — transmitter + web interface (wired Ethernet recommended, or acting as AP itself)
- **1+ RX devices** — speakers (WiFi, 5GHz recommended)
- **1 SRC device** — audio source (can run on same Pi as TX)

Each device runs a nimRum service managed by systemd.

## Hardware Requirements

- Raspberry Pi 3B, 4B, or Zero 2W
- I2S DAC sound card (HiFiBerry, IQaudio, JustBoom, or similar)
- WiFi: 5GHz USB adapter (MT7612U recommended) or built-in
- SD card (8GB sufficient)

## Raspberry Pi OS Setup

Start with **Raspberry Pi OS Lite (64-bit, Trixie)** or Bullseye.

### Essential configuration

1. Enable SSH
2. Set a unique hostname for each device
3. Connect to your WiFi network (5GHz band)
4. Disable NTP (required for sync accuracy):
   ```bash
   sudo systemctl disable systemd-timesyncd.service
   sudo systemctl stop systemd-timesyncd.service
   ```
5. Configure your I2S DAC overlay in `/boot/firmware/config.txt`

## Install nimRum

### System packages (all devices)

```bash
sudo apt update
sudo apt install -y python3-pip libflac-dev libopus-dev libasound2-dev \
    libavcodec-dev libavformat-dev libavutil-dev libsndfile1-dev
```

### Install the wheel

Copy the appropriate wheel to the device and install:

```bash
sudo pip3 install --break-system-packages nimrum-*.whl
sudo ldconfig
```

For upgrades:
```bash
sudo pip3 install --break-system-packages --force-reinstall --no-deps nimrum-*.whl
sudo ldconfig
```

Or deploy from your dev machine (after building):
```bash
export NIMRUM_TX_HOST=<your-tx-hostname>
./scripts/deploy.py -d "<device-number>" -b --with-deps
```

## Configuration

### RX device (speaker)

Create `/root/nimRum/rxConfig.yaml`:
```yaml
nimRumRXConfig:
  pcmDevice: "default"    # ALSA playback device (aplay -l to list)
  pcmMode: auto           # auto | mmap | writei (see below)
  printLevel: note        # err | warn | note | debug
```

`pcmMode` selects the ALSA access method. `auto` picks mmap on ALSA < 1.2.10 and
the rw path (`snd_pcm_writei`) on newer ALSA, which is right for most boards.
Both are non-blocking; they differ in the write call and how readiness is waited
for. Some DACs only work with one of them: if you get constant XRUNs, or a device
that claims to be locked but drifts, force the other one (`mmap` or `writei`).
The startup line `HalPlay: pcmMode ... -> ALSA access ...` shows what is
actually in use.

On first run, `runNimRumRx` saves a `rxConfig.last` with all available
settings — rename to `rxConfig.yaml` to customize.

### TX device (transmitter)

Create `/root/nimRum/txConfig.yaml`:
```yaml
nimRumTXConfig:
  mainVolume: 20
  latency_us: 100000
  default_layout: stereo

  channelLayouts:
    stereo:
      channels: {0: FL, 1: FR}

  clients:
    - name: <rx-hostname>
      location: living-room
      layouts:
        stereo: {channel: 0, level: 0}
```

Add one entry per RX speaker under `clients`. The `name` must match the
RX device's hostname.

### SRC device (audio source)

Create `/root/nimRum/audioSourceConfig.yaml`:
```yaml
sourceName: "My Source"
captureMode: spdif
targetHost: ""            # empty = auto-discover TX
```

Use `captureMode: file` and `filePath: /path/to/music.flac` for file playback.

## Start Services

### Install systemd services (from dev machine)

```bash
./scripts/install-systemd.sh <hostname> rx      # RX device
./scripts/install-systemd.sh <hostname> tx      # TX + WebUI
./scripts/install-systemd.sh <hostname> src     # Audio source
```

### Manual start (for testing)

```bash
runNimRumRx              # On RX device
runNimRumTx              # On TX device
runNimRumWebUI           # On TX device (port 80)
runNimRumAudioSource     # On SRC device
```

## Access the Web UI

If avahi/mDNS is installed on the TX device:
```
http://<tx-hostname>.local
```

Or use the TX device's IP address directly:
```
http://<tx-ip-address>
```

The WebUI provides: topology view, volume control, channel mapping,
EQ calibration, fleet management, and configuration editing.

## Building from Source

### Prerequisites

- Docker (for cross-compilation of C libraries)
- Python 3.9+, `python3-build` package

### Build

```bash
cd nimRumPkg

# Build Docker images (one-time, pulls base images from ghcr.io automatically)
./clibs/docker/rebuildDocker.sh

# Build everything (C libs + Python wheels)
./build.sh
```

Output: `dist/nimrum-*-linux_aarch64.whl` and `dist/nimrum-*-linux_armv7l.whl`

### Deploy to fleet

```bash
export NIMRUM_TX_HOST=<your-tx-hostname>
./build.sh -d "4 8 9" -b    # Build + deploy to specific devices + restart
./build.sh -b                # Build + deploy to all + restart
```

## Important Notes

- All devices must run the **same nimRum version** (wire protocol is versioned)
- NTP must be **disabled** on all devices — nimRum handles its own clock sync
- Use **5GHz WiFi** — 2.4GHz has too much interference for tight sync
- Prefer a **non-DFS 5GHz channel**. On a DFS channel the access point must vacate
  within seconds if it detects radar, moving every client at once — which shows up
  as all speakers losing sync simultaneously. Note non-DFS channels may permit
  lower transmit power in your region, so expect to trade a few dB of margin.
- Optional: see `MEAS_MACHINE.md` for setting up a device that measures what the
  speakers actually do acoustically, rather than what they report.
