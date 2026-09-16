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
  ssh_user: pi            # login user TX uses to deploy to this device
                          # (see "Fleet SSH setup" below)
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

## Fleet SSH setup

The TX Web UI can deploy a new wheel and restart or reboot RX/SRC devices for
you ("Fleet update"). To do that, TX opens an SSH connection to each device and
runs `pip` and `systemctl` under `sudo`. This needs three things on **every**
RX/SRC device:

1. **A non-root login user** — typically `pi` on Raspberry Pi OS, or a user you
   create such as `nimrum`. It must have passwordless `sudo`:
   ```bash
   # on the RX/SRC device, as an admin user
   sudo usermod -aG sudo <user>
   echo '<user> ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/<user>
   sudo chmod 440 /etc/sudoers.d/<user>
   ```

2. **The TX's SSH public key installed for that user.** Generate a key on TX
   once (if you do not already have one), then copy it to each device:
   ```bash
   # on the TX device, once
   ssh-keygen -t ed25519 -f ~/.ssh/id_nimrum -N ""

   # for each RX/SRC device
   ssh-copy-id -i ~/.ssh/id_nimrum.pub <user>@<device-hostname-or-ip>
   ```
   Point the Web UI at this private key (it is configured at TX startup; see the
   TX service/config). Verify it works with no password prompt:
   ```bash
   ssh -i ~/.ssh/id_nimrum <user>@<device> 'sudo -n true && echo OK'
   ```

3. **`ssh_user` set in that device's `rxConfig.yaml`** (or `audioSourceConfig.yaml`
   for a SRC) to the user from step 1. The device reports this to TX in its
   discovery ping, and TX remembers it — so this file is the single place you set
   it. If you leave it empty, TX assumes `pi`.

Notes:
- **Do not use `root`.** It works, but the deploy only needs a normal user with
  `sudo`, and running fleet operations as root is unnecessary risk.
- If a fleet update reports an authentication failure for a device, it almost
  always means step 2 or 3 is missing or the user in `ssh_user` cannot log in
  with TX's key.
- The RX/SRC **service** still runs as configured (often root) — this user is
  only for TX-initiated SSH management, not for running the audio process.

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
