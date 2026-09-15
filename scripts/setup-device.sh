#!/usr/bin/env bash
# setup-device.sh — prepare a Raspberry Pi for nimRum
#
# EXPERIMENTAL, AND WE WOULD LIKE YOUR FEEDBACK.
#
# We can only test this on the boards and sound cards we happen to own, and the
# combinations are many: board revision, OS release, HAT. If it misidentifies your
# hardware, picks the wrong overlay, or does something you did not expect, please open
# an issue with the summary it printed and the contents of your config.txt. That is more
# useful to us than you quietly fixing it by hand.
#
# It is deliberately conservative: it prints everything it intends to change and asks
# before touching anything, it never overwrites your original config.txt backup, and
# --dry-run shows the whole plan without writing.
#
# Run it ON THE DEVICE, not from your workstation:
#
#     sudo ./scripts/setup-device.sh
#     sudo ./scripts/setup-device.sh --dry-run
#
# What it does:
#   1. Sets the hostname — nimRum derives the device ID from it, so it must be unique
#   2. Disables NTP, which would fight nimRum's own clock synchronisation
#   3. Turns off onboard analog and HDMI audio, which are noisy and get in the way
#   4. Enables I2S and the overlay for your DAC HAT
#
# What it does NOT do: install the Python package (use pip), install the systemd
# services (use install-systemd.sh), or write any nimRum config file. Playback settings
# are left at their defaults, which auto-detect.

set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
step() { echo -e "${CYAN}►${NC} $1"; }

DRY_RUN=false
[ "${1:-}" = "--dry-run" ] && DRY_RUN=true

BACKUP_SUFFIX=".nimrum-original"

# Overlay names as shipped by Raspberry Pi OS. Read off real devices rather than from
# memory; `ls /boot/firmware/overlays | grep -i <vendor>` is the authority if yours is
# missing. The list is deliberately short — common HATs plus an escape hatch.
DAC_LABELS=(
    "HiFiBerry DAC / DAC Zero / MiniAmp (PCM5102A)"
    "HiFiBerry DAC+ / DAC+ Pro / Amp2 (PCM512x)"
    "HiFiBerry DAC+ ADC"
    "HiFiBerry Digi / Digi+"
    "HiFiBerry Digi+ Pro"
    "IQaudio DAC (PCM5122)"
    "IQaudio DAC+ / DAC Pro / DigiAMP+"
    "JustBoom DAC / Amp"
    "JustBoom Digi"
    "Other — I will type the overlay name"
    "None — USB card or onboard audio, leave audio alone"
)
DAC_OVERLAYS=(
    "hifiberry-dac"
    "hifiberry-dacplus"
    "hifiberry-dacplusadc"
    "hifiberry-digi"
    "hifiberry-digi-pro"
    "iqaudio-dac"
    "iqaudio-dacplus"
    "justboom-dac"
    "justboom-digi"
    "__other__"
    "__none__"
)

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
detect_config_path() {
    # Bookworm and later moved the boot partition. Both paths can exist, with
    # /boot/config.txt a leftover that the firmware no longer reads — so prefer
    # the new location whenever it is present.
    for p in /boot/firmware/config.txt /boot/config.txt; do
        [ -f "$p" ] && { echo "$p"; return 0; }
    done
    return 1
}

detect_model() {
    if [ -r /proc/device-tree/model ]; then
        tr -d '\0' < /proc/device-tree/model
    else
        echo ""
    fi
}

# A HAT that carries an ID EEPROM describes itself, and the firmware loads its overlay
# from that EEPROM at boot — no dtoverlay line is needed, and adding one is redundant at
# best. Verified on our own boards: a HiFiBerry DAC+ with an EEPROM runs with zero
# dtoverlay lines in config.txt, while two boards without one each need theirs declared.
detect_hat() {
    HAT_VENDOR=""
    HAT_PRODUCT=""
    [ -d /proc/device-tree/hat ] || return 0
    [ -r /proc/device-tree/hat/vendor ] && \
        HAT_VENDOR=$(tr -d '\0' < /proc/device-tree/hat/vendor)
    [ -r /proc/device-tree/hat/product ] && \
        HAT_PRODUCT=$(tr -d '\0' < /proc/device-tree/hat/product)
}

show_environment() {
    step "Detected"
    local os="unknown"
    [ -r /etc/os-release ] && os=$(. /etc/os-release && echo "$PRETTY_NAME")
    echo "    board:    ${MODEL:-<unknown>}"
    echo "    os:       ${os}"
    echo "    config:   ${CONFIG_TXT}"
    echo "    hostname: $(hostname)"
    if [ -n "$HAT_PRODUCT" ]; then
        echo "    hat:      ${HAT_VENDOR:-unknown vendor} ${HAT_PRODUCT} (self-describing)"
    else
        echo "    hat:      none reported — a DAC overlay must be declared"
    fi

    case "$MODEL" in
        *"Raspberry Pi 5"*)
            warn "Pi 5 is not something we have been able to test. Audio and networking"
            warn "should be fine; the optional RGB LED and rotary encoder will not work,"
            warn "as they drive GPIO in a way the Pi 5 changed. Reports welcome."
            ;;
        *"Raspberry Pi"*) ;;
        "") warn "No /proc/device-tree/model — this does not look like a Pi." ;;
        *)  warn "Not a Raspberry Pi. This script only knows Raspberry Pi OS layouts." ;;
    esac
}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
ask_hostname() {
    local current suggested reply
    current=$(hostname)
    suggested="$current"
    echo ""
    step "Hostname"
    echo "    nimRum derives each device's ID from its hostname, so two devices sharing"
    echo "    one hostname will collide. Pick something unique per speaker."
    read -r -p "    hostname [${suggested}]: " reply
    NEW_HOSTNAME="${reply:-$suggested}"

    if ! [[ "$NEW_HOSTNAME" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$ ]]; then
        fail "'${NEW_HOSTNAME}' is not a valid hostname (letters, digits, hyphens)."
        exit 1
    fi
}

ask_dac() {
    local i reply
    echo ""
    step "Sound card"

    # A self-describing HAT is already configured by the firmware. Offer that as the
    # default rather than asking the user to name an overlay it does not need — and if
    # they pick one anyway, that is their call, since a blank or wrong EEPROM happens
    # (one of our own reads product_id 0x0000).
    if [ -n "$HAT_PRODUCT" ]; then
        echo "    This board reports a HAT that describes itself:"
        echo "        ${HAT_VENDOR:-unknown vendor} ${HAT_PRODUCT}"
        echo "    The firmware loads its overlay from the HAT's own EEPROM, so no"
        echo "    dtoverlay line is needed and adding one would be redundant."
        read -r -p "    Keep the auto-detected HAT? [Y/n]: " reply
        case "$reply" in
            [nN]|[nN][oO]) ;;
            *) DAC_OVERLAY="__auto__"; return 0 ;;
        esac
    fi

    for i in "${!DAC_LABELS[@]}"; do
        printf "    %2d) %s\n" "$((i + 1))" "${DAC_LABELS[$i]}"
    done

    # Re-prompt rather than exit. A typo costing you the whole run is a poor first
    # experience, and nothing has been written at this point anyway.
    local attempts=0
    while true; do
        read -r -p "    choice [1-${#DAC_LABELS[@]}]: " reply
        if [[ "$reply" =~ ^[0-9]+$ ]] && [ "$reply" -ge 1 ] && [ "$reply" -le "${#DAC_LABELS[@]}" ]; then
            break
        fi
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 3 ]; then
            fail "Still not one of the choices. Nothing has been changed."
            exit 1
        fi
        warn "Enter a number between 1 and ${#DAC_LABELS[@]}."
    done
    DAC_OVERLAY="${DAC_OVERLAYS[$((reply - 1))]}"

    if [ "$DAC_OVERLAY" = "__other__" ]; then
        echo "    Available overlays: ls $(dirname "$CONFIG_TXT")/overlays"
        read -r -p "    overlay name: " DAC_OVERLAY
        [ -z "$DAC_OVERLAY" ] && { fail "No overlay given."; exit 1; }
    fi

    if [ "$DAC_OVERLAY" != "__none__" ]; then
        local dtbo="$(dirname "$CONFIG_TXT")/overlays/${DAC_OVERLAY}.dtbo"
        if [ ! -f "$dtbo" ]; then
            warn "${DAC_OVERLAY}.dtbo not found in $(dirname "$CONFIG_TXT")/overlays."
            warn "Continuing anyway — your OS may name it differently — but if there is"
            warn "no sound afterwards, this is the first thing to check."
        fi
    fi
}

# ---------------------------------------------------------------------------
# config.txt editing — idempotent, one setting at a time
# ---------------------------------------------------------------------------
plan_add() { PLAN+=("$1"); }

build_plan() {
    PLAN=()
    [ "$NEW_HOSTNAME" != "$(hostname)" ] && \
        plan_add "hostname: $(hostname) -> ${NEW_HOSTNAME}"
    plan_add "disable NTP: systemctl disable --now systemd-timesyncd"

    if [ "$DAC_OVERLAY" = "__none__" ]; then
        plan_add "audio: left untouched"
    else
        plan_add "${CONFIG_TXT}: dtparam=audio=off           (onboard analog off)"
        plan_add "${CONFIG_TXT}: append ,noaudio to the vc4 overlay  (HDMI audio off)"
        plan_add "${CONFIG_TXT}: dtparam=i2s=on"
        if [ "$DAC_OVERLAY" = "__auto__" ]; then
            plan_add "no dtoverlay line — the HAT's EEPROM already provides it"
        else
            plan_add "${CONFIG_TXT}: dtoverlay=${DAC_OVERLAY}"
        fi
    fi
}

# Replace a `key=...` line if present, otherwise append. Appending blindly is what
# leaves a config.txt with three contradictory dtparam=audio lines, where the last one
# silently wins and the user cannot tell which is in effect.
set_config_param() {
    local key="$1" line="$2"
    if grep -qE "^\s*#?\s*${key}=" "$CONFIG_TXT"; then
        sed -i -E "s|^\s*#?\s*${key}=.*|${line}|" "$CONFIG_TXT"
    else
        printf '%s\n' "$line" >> "$CONFIG_TXT"
    fi
}

apply_audio_config() {
    # vc4-kms-v3d is present by default and drives HDMI audio. It must be edited in
    # place rather than duplicated, so the `,noaudio` suffix is added to whatever
    # variant is already there (vc4-kms-v3d or vc4-fkms-v3d).
    if grep -qE "^\s*dtoverlay=vc4-f?kms-v3d" "$CONFIG_TXT"; then
        if ! grep -qE "^\s*dtoverlay=vc4-f?kms-v3d.*noaudio" "$CONFIG_TXT"; then
            sed -i -E "s|^(\s*dtoverlay=vc4-f?kms-v3d[^\n]*)|\1,noaudio|" "$CONFIG_TXT"
        fi
        ok "HDMI audio disabled (,noaudio on the vc4 overlay)"
    else
        warn "No vc4-kms-v3d line found; skipping the HDMI audio change."
    fi

    set_config_param "dtparam=audio" "dtparam=audio=off"
    ok "onboard analog audio disabled"

    set_config_param "dtparam=i2s" "dtparam=i2s=on"
    ok "I2S enabled"

    # The HAT overlay is appended rather than replacing any dtoverlay= line, since
    # config.txt legitimately holds several unrelated overlays.
    if [ "$DAC_OVERLAY" = "__auto__" ]; then
        ok "no dtoverlay added — the HAT's EEPROM provides it"
    elif grep -qE "^\s*dtoverlay=${DAC_OVERLAY}\s*$" "$CONFIG_TXT"; then
        ok "dtoverlay=${DAC_OVERLAY} already present"
    else
        printf 'dtoverlay=%s\n' "$DAC_OVERLAY" >> "$CONFIG_TXT"
        ok "dtoverlay=${DAC_OVERLAY} added"
    fi
}

apply_hostname() {
    [ "$NEW_HOSTNAME" = "$(hostname)" ] && { ok "hostname already ${NEW_HOSTNAME}"; return; }
    local old
    old=$(hostname)
    hostnamectl set-hostname "$NEW_HOSTNAME" 2>/dev/null || \
        printf '%s\n' "$NEW_HOSTNAME" > /etc/hostname
    # Without the matching /etc/hosts entry, sudo and other tools stall on name lookup.
    if grep -qE "^127\.0\.1\.1\s" /etc/hosts; then
        sed -i -E "s|^(127\.0\.1\.1\s+).*|\1${NEW_HOSTNAME}|" /etc/hosts
    else
        printf '127.0.1.1\t%s\n' "$NEW_HOSTNAME" >> /etc/hosts
    fi
    ok "hostname ${old} -> ${NEW_HOSTNAME}"
}

apply_ntp() {
    # NTP would step the clock underneath nimRum's own synchronisation, which is the
    # one thing that must not happen while audio is playing.
    systemctl disable --now systemd-timesyncd 2>/dev/null
    ok "systemd-timesyncd disabled"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
echo ""
echo "nimRum device setup — experimental, feedback wanted."
echo "Report anything surprising: https://github.com/abtaudio/nimRumPkg/issues"
echo ""

if ! CONFIG_TXT=$(detect_config_path); then
    fail "Found neither /boot/firmware/config.txt nor /boot/config.txt."
    fail "This script is for Raspberry Pi OS. Nothing has been changed."
    exit 1
fi
MODEL=$(detect_model)
detect_hat

if [ "$(id -u)" -ne 0 ] && [ "$DRY_RUN" = false ]; then
    fail "Needs root to change the hostname and ${CONFIG_TXT}. Re-run with sudo,"
    fail "or use --dry-run to see the plan without root."
    exit 1
fi

show_environment
ask_hostname
ask_dac
build_plan

echo ""
step "Planned changes"
for p in "${PLAN[@]}"; do echo "    - $p"; done
echo ""
echo "    ${CONFIG_TXT} will be backed up to ${CONFIG_TXT}${BACKUP_SUFFIX}"
echo "    (kept from the first run only, so it stays your pristine original)"

if [ "$DRY_RUN" = true ]; then
    echo ""
    warn "Dry run — nothing written."
    exit 0
fi

echo ""
read -r -p "Apply these changes? [y/N]: " confirm
case "$confirm" in
    [yY]|[yY][eE][sS]) ;;
    *) echo ""; warn "Nothing changed."; exit 0 ;;
esac

echo ""
step "Applying"
cp -n "$CONFIG_TXT" "${CONFIG_TXT}${BACKUP_SUFFIX}" && \
    ok "backup at ${CONFIG_TXT}${BACKUP_SUFFIX}" || \
    ok "backup already exists, keeping the original one"

apply_hostname
apply_ntp
[ "$DAC_OVERLAY" != "__none__" ] && apply_audio_config

echo ""
step "Next"
echo "    1. Reboot — the overlay and hostname only take effect then:  sudo reboot"
echo "    2. Check the card appears:                                   aplay -l"
echo "    3. Install the package, if you have not already:             pip3 install nimRum"
echo "    4. From your workstation, install the services:"
echo "       ./scripts/install-systemd.sh ${NEW_HOSTNAME}"
echo ""
echo "    If something is wrong, restore with:"
echo "       sudo cp ${CONFIG_TXT}${BACKUP_SUFFIX} ${CONFIG_TXT} && sudo reboot"
echo ""
