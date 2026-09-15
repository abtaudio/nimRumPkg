#!/usr/bin/env bash
# rx_eq_ctrl.sh — Control EQ on RX devices (enable/disable/update coefficients)
#
# Usage:
#   ./scripts/rx_eq_ctrl.sh <device> enable         Enable EQ
#   ./scripts/rx_eq_ctrl.sh <device> disable        Disable EQ
#   ./scripts/rx_eq_ctrl.sh <device> update <file>  Push new eq_config.yaml
#   ./scripts/rx_eq_ctrl.sh <device> status         Show current EQ state
#   ./scripts/rx_eq_ctrl.sh <device> reload         Reload config (no change)
#
# All operations use SIGUSR1 to hot-reload the DSP plugin — no RX restart,
# no sync loss. The device stays locked throughout.
#
# Examples:
#   ./scripts/rx_eq_ctrl.sh speaker1 enable
#   ./scripts/rx_eq_ctrl.sh speaker1 update calibration/speaker1_eq.yaml
#   ./scripts/rx_eq_ctrl.sh speaker1 disable

set -uo pipefail

SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new"
EQ_PATH="/root/nimRum/calibration/eq_config.yaml"

usage() {
    echo "Usage: $0 <device> <command> [args]"
    echo ""
    echo "Commands:"
    echo "  enable          Enable EQ processing"
    echo "  disable         Disable EQ (passthrough)"
    echo "  update <file>   Push new eq_config.yaml and reload"
    echo "  status          Show current EQ config"
    echo "  reload          Signal RX to reload current config"
    exit 1
}

if [ $# -lt 2 ]; then
    usage
fi

DEVICE="$1"
CMD="$2"

signal_reload() {
    ssh ${SSH_OPTS} "$DEVICE" "sudo pkill -USR1 -f runNimRumRx" 2>/dev/null
    echo "Signalled $DEVICE to reload DSP config"
}

case "$CMD" in
    enable)
        ssh ${SSH_OPTS} "$DEVICE" \
            "sudo sed -i 's/^enabled:.*/enabled: true/' ${EQ_PATH}" 2>/dev/null
        if [ $? -ne 0 ]; then
            echo "ERROR: Failed to update config on $DEVICE"
            exit 1
        fi
        signal_reload
        echo "EQ enabled on $DEVICE"
        ;;

    disable)
        ssh ${SSH_OPTS} "$DEVICE" \
            "sudo sed -i 's/^enabled:.*/enabled: false/' ${EQ_PATH}" 2>/dev/null
        if [ $? -ne 0 ]; then
            echo "ERROR: Failed to update config on $DEVICE"
            exit 1
        fi
        signal_reload
        echo "EQ disabled on $DEVICE"
        ;;

    update)
        if [ $# -lt 3 ]; then
            echo "ERROR: update requires a file argument"
            echo "Usage: $0 $DEVICE update <eq_config.yaml>"
            exit 1
        fi
        LOCAL_FILE="$3"
        if [ ! -f "$LOCAL_FILE" ]; then
            echo "ERROR: File not found: $LOCAL_FILE"
            exit 1
        fi

        # Ensure calibration directory exists
        ssh ${SSH_OPTS} "$DEVICE" "sudo mkdir -p /root/nimRum/calibration" 2>/dev/null

        # SCP to tmp then move with sudo
        scp ${SSH_OPTS} -q "$LOCAL_FILE" "${DEVICE}:/tmp/nimrum_eq_update.yaml"
        if [ $? -ne 0 ]; then
            echo "ERROR: SCP failed to $DEVICE"
            exit 1
        fi

        ssh ${SSH_OPTS} "$DEVICE" \
            "sudo cp /tmp/nimrum_eq_update.yaml ${EQ_PATH} && rm -f /tmp/nimrum_eq_update.yaml" 2>/dev/null
        if [ $? -ne 0 ]; then
            echo "ERROR: Failed to install config on $DEVICE"
            exit 1
        fi

        signal_reload
        echo "EQ config updated on $DEVICE from $LOCAL_FILE"
        ;;

    status)
        echo "=== EQ config on $DEVICE ==="
        ssh ${SSH_OPTS} "$DEVICE" "sudo cat ${EQ_PATH} 2>/dev/null || echo '(no config file)'"
        ;;

    reload)
        signal_reload
        ;;

    *)
        echo "ERROR: Unknown command '$CMD'"
        usage
        ;;
esac
