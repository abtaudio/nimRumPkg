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

"""nimRumTxLanCtrl — UDP control interface for TX.

Provides a simple, system-independent control API for external hardware
controllers (e.g., a single volume knob, IR remote, GPIO buttons).

The controller device does NOT need to know about clients, speakers, or
any system topology. It only sends high-level commands:
  - volUp / volDown / volSet / muteToggle

Protocol (UDP, port 53474):
  ASCII message: "nimRumCtrl,<command>[,<value>]"

Commands:
  nimRumCtrl,volUp          — Increase main volume one step
  nimRumCtrl,volDown        — Decrease main volume one step
  nimRumCtrl,volSet,<0-127> — Set main volume to absolute value
  nimRumCtrl,muteToggle     — Toggle mute on/off

The TX applies the command to ALL clients uniformly (main volume).
No per-speaker knowledge needed on the controller side.
"""

import socket

LANCTRL_PORT = 53474
_VOL_MAX = 127  # Must match VOL_MAX in nimRumTxCfg / LIB_PREZO_VOL_MAX in C
LANCTRL_HEADER = "nimRumCtrl"


class nimRumTxLanCtrl:
    """UDP control server — mixed into nimRumTxCfg."""

    def __init__(self):
        self.lanCtrlAvailable = False
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(('', LANCTRL_PORT))
            self.sock.setblocking(False)
            self.lanCtrlAvailable = True
        except Exception:
            print("nimRumLanCtrl: NOT enabled (port %d unavailable)" % LANCTRL_PORT)

    def lanCtrlCheck(self):
        """Non-blocking check for incoming control commands. Call from TX loop."""
        if not self.lanCtrlAvailable:
            return

        try:
            msg, addr = self.sock.recvfrom(64)
        except BlockingIOError:
            return
        except Exception:
            return

        try:
            parts = msg.decode('ascii').strip().split(',')
        except Exception:
            return

        if len(parts) < 2 or parts[0] != LANCTRL_HEADER:
            # Also accept legacy "nimRumLanCtrl" header for backwards compat
            if len(parts) >= 2 and parts[0] == "nimRumLanCtrl":
                self._handle_legacy(parts)
            return

        cmd = parts[1]
        val = int(parts[2]) if len(parts) > 2 else 0

        if cmd == "volUp":
            self.volumeUp()
        elif cmd == "volDown":
            self.volumeDown()
        elif cmd == "volSet":
            self.volume = max(0, min(_VOL_MAX, val))
            self.volumeCallback()
        elif cmd == "muteToggle":
            self.volumeMuteToggle()

    def _handle_legacy(self, parts):
        """Handle old-format messages for backwards compatibility."""
        if len(parts) < 4:
            return
        cmd = parts[1]
        # Legacy format: nimRumLanCtrl,<cmd>,<cliLocation>,<val>
        val = int(parts[3]) if len(parts) > 3 else 0

        if cmd == "volUp":
            self.volumeUp()
        elif cmd == "volDown":
            self.volumeDown()
        elif cmd == "muteToggle":
            self.volumeMuteToggle()
        elif cmd == "volAbs":
            self.volume = max(0, min(_VOL_MAX, val))
            self.volumeCallback()
