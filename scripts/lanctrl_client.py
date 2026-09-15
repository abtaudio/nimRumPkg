#!/usr/bin/env python3

# Copyright (C) 2021-2026 AbtAudio AB
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# As a special exception, you may link this program with the nimRumLib
# shared libraries (libNimRumTx_ct.so, libNimRumRx_ct.so) provided by
# AbtAudio AB without those libraries being subject to the GPL-3.0.

"""nimRumTxLanCtrlClient — Minimal UDP client for TX remote control.

Sends simple commands to TX over UDP. No knowledge of speakers or system
topology needed — just volume up/down/set and mute toggle.

Can be used as:
  - Library: import and call send_cmd()
  - CLI: python3 lanctrl_client.py [--host <tx-hostname>] <command> [value]
  - Interactive: python3 nimRumTxLanCtrlClient.py (keyboard control)

Protocol: ASCII over UDP port 53474
  "nimRumCtrl,<command>[,<value>]"
"""

import os
import socket
import sys

DEFAULT_TX_HOST = os.environ.get("NIMRUM_TX_HOST", "nimrumtx")
DEFAULT_TX_PORT = int(os.environ.get("NIMRUM_TX_PORT", "53474"))
HEADER = "nimRumCtrl"


class NimRumCtrl:
    """UDP control client for nimRum TX."""

    def __init__(self, host: str = DEFAULT_TX_HOST, port: int = DEFAULT_TX_PORT):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._addr = (host, port)

    def send_cmd(self, cmd: str, val: int = 0) -> None:
        """Send a control command to TX.

        Args:
            cmd: Command name (volUp, volDown, volSet, muteToggle).
            val: Optional value (used by volSet).
        """
        msg = f"{HEADER},{cmd},{val}".encode()
        self._sock.sendto(msg, self._addr)

    def vol_up(self) -> None:
        self.send_cmd("volUp")

    def vol_down(self) -> None:
        self.send_cmd("volDown")

    def vol_set(self, volume: int) -> None:
        """Set absolute volume (0-127)."""
        self.send_cmd("volSet", max(0, min(127, volume)))

    def mute_toggle(self) -> None:
        self.send_cmd("muteToggle")


def _interactive(ctrl: NimRumCtrl) -> None:
    """Interactive keyboard control mode."""
    print("nimRum Remote Control (UDP → TX)")
    print("  +/-  : Volume up/down")
    print("  m    : Mute toggle")
    print("  0-9  : Set volume (×10)")
    print("  q    : Quit")
    print()

    while True:
        try:
            key = input("> ")
        except (EOFError, KeyboardInterrupt):
            break

        if key == '+':
            ctrl.vol_up()
            print("  vol+")
        elif key == '-':
            ctrl.vol_down()
            print("  vol-")
        elif key == 'm':
            ctrl.mute_toggle()
            print("  mute toggle")
        elif key == 'q':
            break
        elif key.isdigit():
            vol = int(key) * 10
            ctrl.vol_set(vol)
            print(f"  vol={vol}")
        else:
            print(f"  unknown: '{key}'")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Send control commands to nimRum TX.",
    )
    parser.add_argument(
        "--host", type=str, default=DEFAULT_TX_HOST,
        help=f"TX hostname or IP (default: {DEFAULT_TX_HOST})",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_TX_PORT,
        help=f"TX control port (default: {DEFAULT_TX_PORT})",
    )
    parser.add_argument(
        "command", nargs='?', default=None,
        help="Command: volUp, volDown, volSet, muteToggle (omit for interactive)",
    )
    parser.add_argument(
        "value", nargs='?', type=int, default=0,
        help="Value for volSet (0-100)",
    )
    args = parser.parse_args()

    ctrl = NimRumCtrl(host=args.host, port=args.port)

    if args.command is None:
        _interactive(ctrl)
    else:
        ctrl.send_cmd(args.command, args.value)
        print(f"Sent: {args.command} {args.value if args.value else ''}")


if __name__ == "__main__":
    main()
