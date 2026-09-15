# device-config/

Configuration files to install on nimRum devices. Deployed once during device
setup via `scripts/install-systemd.sh`.

## Contents

```
systemd/          Systemd service units
journald/         Journald log size limits
avahi/            mDNS service advertisement (TX only)
```

## systemd/

| Service | Binary | Role |
|---------|--------|------|
| `nimrum-rx.service` | `runNimRumRx` | RX (speaker) |
| `nimrum-tx.service` | `runNimRumTx` | TX (transmitter) |
| `nimrum-webui.service` | `runNimRumWebUI` | TX (web interface) |
| `nimrum-src.service` | `runNimRumAudioSource` | SRC (audio source feeder) |
| `nimrum-meas.service` | `runNimRumMeas` | MEAS (spike measurement) |

All services run as `Type=simple` with `Restart=on-failure`, `Nice=-10`,
and working directory `/root/nimRum`.

### Useful commands (on device)

```bash
systemctl status nimrum-rx
sudo systemctl restart nimrum-rx
journalctl -u nimrum-rx -f            # live logs
journalctl -u nimrum-rx --since "1h ago"
```

## journald/

`nimrum-journald.conf` → `/etc/systemd/journald.conf.d/nimrum.conf`

Limits journal to 50MB on disk, 7-day retention. Prevents logs from filling
the SD card.

## avahi/

`nimrum-tx.service` → `/etc/avahi/services/nimrum.service` (TX only)

Advertises the WebUI via mDNS so users can browse to `http://<hostname>.local`.
RX and SRC devices do not need mDNS — they are auto-discovered by TX.

## Installation

```bash
./scripts/install-systemd.sh <hostname> [role]
```

The script copies the appropriate service files, installs journald config,
stops anything already running, and enables the services. It is safe to re-run,
which is how you change a device's role.

Full setup instructions: [`doc/INSTALL.md`](../doc/INSTALL.md), and
[`doc/headless-install.md`](../doc/headless-install.md) for a Raspberry Pi with
no monitor and no ethernet.
