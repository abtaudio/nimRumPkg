# Headless install on Raspberry Pi OS (no monitor, no ethernet)

Prepare an SD card so a Raspberry Pi joins Wi-Fi and accepts SSH on first boot,
with nothing attached but power. Written against Raspberry Pi OS Trixie
(64-bit Lite); the Wi-Fi parts differ on Bullseye, noted where relevant.

Every step is done on the card, offline. Nothing here needs a console or a cable.

## Why the obvious approach fails

Dropping `ssh` and a Wi-Fi config onto the boot partition is necessary but not
sufficient. Three separate things will each independently leave the device up but
unreachable, and none of them announce themselves:

1. **Wi-Fi is soft-blocked until the regulatory domain is set.** The block is in
   place before any userspace network configuration runs, so fixing it from
   `cloud-init` `runcmd` or any late script is too late — the network stage has
   already tried and failed.
2. **NetworkManager keeps its own persistent `WirelessEnabled` flag**, separate
   from kernel rfkill. The shipped image sets it to `false`. Clearing rfkill does
   not help while this is false; the log line is
   `rfkill: Wi-Fi disabled by radio killswitch; disabled by state file`.
3. **The clock starts at the image build date**, so `apt` rejects repository
   signatures as "not yet valid" and you cannot install anything.

Each one is individually fatal to a headless bring-up.

## Steps

Below, `sdX` is the card device and `BOOT`/`ROOT` are its two mounted
partitions. **Confirm the device is the card**, not a system disk:

```bash
lsblk -o NAME,SIZE,TYPE,MODEL,TRAN,RM
cat /sys/block/sdX/removable      # must be 1
```

### 1. Write the image and verify it

```bash
curl -O https://downloads.raspberrypi.com/raspios_lite_arm64/images/<dir>/<img>.img.xz
curl -O https://downloads.raspberrypi.com/raspios_lite_arm64/images/<dir>/<img>.img.xz.sha256
sha256sum -c <img>.img.xz.sha256          # do not skip this
xzcat <img>.img.xz | sudo dd of=/dev/sdX bs=4M conv=fsync status=progress
sudo sync
```

### 2. Expand the root partition before first boot

Optional but saves a reboot, and avoids `apt` filling a 2.3 GB filesystem:

```bash
sudo parted /dev/sdX --script resizepart 2 100%
sudo e2fsck -fy /dev/sdX2
sudo resize2fs /dev/sdX2
```

### 3. Enable SSH

```bash
sudo touch $BOOT/ssh
```

`sshswitch.service` looks for `ssh` or `ssh.txt` on the firmware partition and
**deletes it** after enabling `ssh.service`. So an absent flag on a card that has
booted does not mean it was never written.

### 4. Set the regulatory domain as a kernel parameter

This is the fix for the soft block, and it must be a kernel parameter so it
applies before userspace:

```bash
sudo sed -i -e "s/\s*cfg80211.ieee80211_regdom=\S*//" \
            -e "s/\(.*\)/\1 cfg80211.ieee80211_regdom=<CC>/" $BOOT/cmdline.txt
```

`<CC>` is an ISO 3166-1 alpha-2 code. This is exactly what
`raspi-config nonint do_wifi_country` does.

### 5. Clear rfkill with a udev rule, not a boot-ordered unit

```bash
sudo tee $ROOT/etc/udev/rules.d/90-rfkill-unblock.rules <<'EOF'
SUBSYSTEM=="rfkill", ACTION=="add|change", ATTR{soft}="0"
EOF
```

Use udev, not a systemd unit. A unit ordered `Before=network-pre.target` runs at
roughly 10 s, while radios appear later — around 13 s for built-in SDIO Wi-Fi and
20 s+ for a USB dongle. It will fail with
`rfkill: cannot open /dev/rfkill: No such file or directory`. udev is
event-driven and fires per device at the right moment.

Also clear any saved block, since a restored state re-blocks the radio:

```bash
for f in $ROOT/var/lib/systemd/rfkill/*wlan*; do echo 0 | sudo tee "$f"; done
```

### 6. Enable Wi-Fi in NetworkManager's state file

```bash
sudo tee $ROOT/var/lib/NetworkManager/NetworkManager.state <<'EOF'
[main]
NetworkingEnabled=true
WirelessEnabled=true
WWANEnabled=true
EOF
sudo chmod 600 $ROOT/var/lib/NetworkManager/NetworkManager.state
```

All three keys. This is the single most commonly missed step.

### 7. Write the Wi-Fi credentials as a NetworkManager keyfile

Prefer a native keyfile over `cloud-init` `network-config`:

```bash
sudo tee $ROOT/etc/NetworkManager/system-connections/wifi.nmconnection <<'EOF'
[connection]
id=wifi
type=wifi
autoconnect=true
autoconnect-retries=0

[wifi]
mode=infrastructure
ssid=<SSID>

[wifi-security]
key-mgmt=wpa-psk
psk=<PASSPHRASE>

[ipv4]
method=auto

[ipv6]
method=disabled
EOF
sudo chmod 600 $ROOT/etc/NetworkManager/system-connections/wifi.nmconnection
sudo chown root:root $ROOT/etc/NetworkManager/system-connections/wifi.nmconnection
```

The 0600 mode is required — NetworkManager ignores keyfiles with looser
permissions.

Deliberately **no `interface-name`**. Binding to `wlan0` means a rename or a
different enumeration order leaves the device unreachable with no console.
Unbound, NetworkManager associates on whichever radio exists.

Why not `cloud-init`'s `network-config`? On Raspberry Pi OS it is rendered
through netplan with `bringup=False` and only `netplan generate` is run, so the
profile exists but is never activated. The image also lists a
`netplan_nm_patch` module in `cloud.cfg` that is not installed, producing
`Could not find module named cc_netplan_nm_patch`. A keyfile skips all of it.

On **Bullseye** none of this applies: use
`$BOOT/wpa_supplicant.conf` with a `country=` line instead.

### 8. Make the clock roughly right so `apt` works

```bash
sudo touch $ROOT/usr/lib/clock-epoch
```

systemd (v257 here) uses the **mtime** of `/usr/lib/clock-epoch` as the earliest
permitted boot time. Recent Lite images do not ship the file at all, so creating
it now stamps it with the current time. Without this, `apt` fails on signature
validity and the usual workaround is to plug in ethernet — which is what we are
trying to avoid.

If NTP is intentionally disabled, install `fake-hwclock` after first boot so the
time survives reboots; otherwise the clock resets to this floor every time.

### 9. Enable a persistent journal

```bash
sudo mkdir -p $ROOT/var/log/journal
sudo mkdir -p $ROOT/etc/systemd/journald.conf.d
sudo tee $ROOT/etc/systemd/journald.conf.d/persistent.conf <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=64M
EOF
```

Do this **before** the first boot. The default is volatile, so a headless device
that fails to join the network discards the only evidence of why at power-off.

### 10. Leave every radio enabled for the first boot

If the final configuration disables the built-in Wi-Fi in favour of a USB dongle,
do that **after** the device is confirmed reachable. Disabling it up front makes
the dongle a single point of failure with no console to diagnose it. Boot with
both, then switch, then verify the interface names.

### 11. Amend `config.txt`, never replace it

Edit the shipped file. Replacing it wholesale drops directives the board needs,
including:

| Directive | Consequence if dropped |
|---|---|
| `arm_64bit=1` | Firmware looks for a 32-bit kernel; 64-bit images contain none |
| `auto_initramfs=1` | `initramfs8` is not loaded |
| `disable_fw_kms_setup=1`, `max_framebuffers=2` | Display/KMS misconfiguration |

Append your overlays at the end and leave the stock content above intact.

## Optional: a first-boot diagnostic report

The boot partition is FAT, so anything written there can be read on any machine
by plugging the card in. That turns a silent failure into a readable one:

```bash
sudo tee $ROOT/usr/local/sbin/netreport <<'EOF'
#!/bin/sh
OUT=/boot/firmware/netreport.txt
{
  echo "=== $(date -Is) ==="
  ip -br link; ip -br addr
  rfkill list
  iw reg get 2>/dev/null | head -5
  lsusb; ls /sys/class/net
  nmcli -t device status; nmcli -t connection show
  nmcli -t -f SSID,SIGNAL device wifi list | head -15
  journalctl -b --no-pager -u NetworkManager -u wpa_supplicant | tail -40
  dmesg | grep -iE "wlan|cfg80211|rfkill" | tail -30
} > "$OUT" 2>&1
sync
EOF
sudo chmod +x $ROOT/usr/local/sbin/netreport
```

Run it from a `oneshot` unit ordered `After=NetworkManager.service` with an
`ExecStartPre=/bin/sleep 45` so NetworkManager has settled or failed first.
Confirm `ip`, `rfkill`, `iw`, `lsusb`, `nmcli` are present in the image — Lite
images may omit `rfkill` and `iw`.

## Verifying, and finding the device

`ping` may be filtered or unreliable, so absence of a ping reply proves nothing.
Probe TCP 22 instead:

```bash
SUBNET=192.168.1        # set to your own /24
for i in $(seq 2 254); do
  (timeout 1 bash -c "echo > /dev/tcp/$SUBNET.$i/22" 2>/dev/null && echo "$i") &
done
```

A reflashed device presents **new SSH host keys**, so `ssh` will refuse with
`REMOTE HOST IDENTIFICATION HAS CHANGED`. That is expected after a reflash;
clear the stale entry with `ssh-keygen -R <host>`. Only treat it as suspicious if
you did not just reimage the card.

Identify an unexpected host before assuming it is yours — the SSH banner version
is a quick discriminator, since OS releases ship distinct OpenSSH versions.

## Checklist

- [ ] Image checksum verified
- [ ] `ssh` flag on boot partition
- [ ] `cfg80211.ieee80211_regdom=<CC>` in `cmdline.txt`
- [ ] udev rfkill rule, and saved rfkill blocks cleared
- [ ] `NetworkManager.state` has all three `*Enabled=true` keys
- [ ] Keyfile present, 0600, root-owned, no `interface-name`
- [ ] `/usr/lib/clock-epoch` created
- [ ] Persistent journal configured
- [ ] All radios enabled for first boot
- [ ] `config.txt` amended, `arm_64bit`/`auto_initramfs` still present
