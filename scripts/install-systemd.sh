#!/usr/bin/env bash
# install-systemd.sh — Install nimRum systemd services on a device
#
# Usage (from dev machine):
#   ./scripts/install-systemd.sh speaker1       # RX device (default role)
#   ./scripts/install-systemd.sh mytx tx src    # TX with SRC
#   ./scripts/install-systemd.sh mysrc src      # SRC only
#   ./scripts/install-systemd.sh mymeas meas    # Measurement device
#
# This script:
#   1. Copies the appropriate service unit files to the target device
#   2. Installs journald size limits
#   3. Stops any nimRum process already running on the device
#   4. Enables and starts the new systemd services

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_DIR="$PKG_DIR/device-config/systemd"
JOURNALD_DIR="$PKG_DIR/device-config/journald"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
step() { echo -e "${CYAN}►${NC} $1"; }

SSH_OPTS="-n -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new"
SCP_OPTS="-o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new"

# ---------------------------------------------------------------------------
# Determine roles for a device
# ---------------------------------------------------------------------------
get_roles() {
    local host="$1"
    local forced_roles="$2"

    if [ -n "$forced_roles" ]; then
        echo "$forced_roles"
        return
    fi

    # Default to rx
    echo "rx"
}

# ---------------------------------------------------------------------------
# Install on one device
# ---------------------------------------------------------------------------
install_one() {
    local host="$1"
    local roles="$2"

    step "${host} (roles: ${roles})"

    # Test SSH connectivity
    if ! ssh ${SSH_OPTS} "${host}" "true" 2>/dev/null; then
        fail "SSH connection failed"
        return 1
    fi

    # --- 1. Install journald config ---
    scp ${SCP_OPTS} -q "$JOURNALD_DIR/nimrum-journald.conf" "${host}:/tmp/nimrum-journald.conf"
    ssh ${SSH_OPTS} "${host}" "sudo mkdir -p /etc/systemd/journald.conf.d && \
        sudo cp /tmp/nimrum-journald.conf /etc/systemd/journald.conf.d/nimrum.conf && \
        sudo systemctl restart systemd-journald && \
        rm -f /tmp/nimrum-journald.conf" 2>/dev/null
    ok "journald config installed (50M limit)"

    # --- 2. Stop any running nimRum processes ---
    # Needed when re-running on a live device: a unit cannot take over a device
    # while the old process still holds the PCM. Also clears a manual `screen`
    # session, which is how a data-logging capture is started by hand.
    ssh ${SSH_OPTS} "${host}" "sudo pkill -9 -f 'runNimRumTx|runNimRumRx|runNimRumAudioSource|runNimRumWebUI|runNimRumMeas' 2>/dev/null; \
        sudo killall -9 screen 2>/dev/null; \
        sudo screen -wipe 2>/dev/null; true" 2>/dev/null
    ok "stopped running nimRum processes"

    # --- 3. Disable any old nimrum services (in case of re-run) ---
    ssh ${SSH_OPTS} "${host}" "sudo systemctl disable nimrum-rx nimrum-tx nimrum-webui nimrum-src nimrum-meas 2>/dev/null; true" 2>/dev/null

    # --- 4. Copy and enable service files based on roles ---
    local services_enabled=""

    if echo "$roles" | grep -qw "tx"; then
        scp ${SCP_OPTS} -q "$SYSTEMD_DIR/nimrum-tx.service" "${host}:/tmp/"
        scp ${SCP_OPTS} -q "$SYSTEMD_DIR/nimrum-webui.service" "${host}:/tmp/"
        ssh ${SSH_OPTS} "${host}" "sudo cp /tmp/nimrum-tx.service /etc/systemd/system/ && \
            sudo cp /tmp/nimrum-webui.service /etc/systemd/system/ && \
            rm -f /tmp/nimrum-tx.service /tmp/nimrum-webui.service" 2>/dev/null
        services_enabled="nimrum-tx nimrum-webui"
    fi

    if echo "$roles" | grep -qw "src"; then
        scp ${SCP_OPTS} -q "$SYSTEMD_DIR/nimrum-src.service" "${host}:/tmp/"
        ssh ${SSH_OPTS} "${host}" "sudo cp /tmp/nimrum-src.service /etc/systemd/system/ && \
            rm -f /tmp/nimrum-src.service" 2>/dev/null
        services_enabled="${services_enabled} nimrum-src"
    fi

    if echo "$roles" | grep -qw "rx"; then
        scp ${SCP_OPTS} -q "$SYSTEMD_DIR/nimrum-rx.service" "${host}:/tmp/"
        ssh ${SSH_OPTS} "${host}" "sudo cp /tmp/nimrum-rx.service /etc/systemd/system/ && \
            rm -f /tmp/nimrum-rx.service" 2>/dev/null
        services_enabled="${services_enabled} nimrum-rx"
    fi

    if echo "$roles" | grep -qw "meas"; then
        scp ${SCP_OPTS} -q "$SYSTEMD_DIR/nimrum-meas.service" "${host}:/tmp/"
        ssh ${SSH_OPTS} "${host}" "sudo cp /tmp/nimrum-meas.service /etc/systemd/system/ && \
            rm -f /tmp/nimrum-meas.service" 2>/dev/null
        services_enabled="${services_enabled} nimrum-meas"
    fi

    # --- 5. Reload systemd, enable and start ---
    ssh ${SSH_OPTS} "${host}" "sudo systemctl daemon-reload && \
        sudo systemctl enable ${services_enabled} && \
        sudo systemctl start ${services_enabled}" 2>/dev/null

    if [ $? -eq 0 ]; then
        ok "enabled & started: ${services_enabled}"
    else
        fail "failed to start services"
        return 1
    fi

    # --- 6. Verify ---
    sleep 2
    local all_ok=true
    for svc in ${services_enabled}; do
        local status
        status=$(ssh ${SSH_OPTS} "${host}" "systemctl is-active ${svc}" 2>/dev/null)
        if [ "$status" = "active" ]; then
            ok "${svc} is running"
        else
            fail "${svc} status: ${status}"
            all_ok=false
        fi
    done

    if [ "$all_ok" = true ]; then
        return 0
    else
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if [ $# -lt 1 ]; then
    echo "Usage: $0 <hostname> [roles...]"
    echo ""
    echo "Examples:"
    echo "  $0 speaker1            # Install RX service (default role)"
    echo "  $0 mytx tx src         # Install TX + SRC services"
    echo "  $0 mysrc src           # Install SRC service"
    echo "  $0 mymeas meas         # Install MEAS service"
    exit 1
fi

host="$1"
shift
forced_roles="$*"
roles=$(get_roles "$host" "$forced_roles")

echo ""
install_one "$host" "$roles"
echo ""
