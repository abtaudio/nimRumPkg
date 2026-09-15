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

import sys
import signal
import socket
import hashlib
import os
from nimRum.common import nimRumCheckHW

import netifaces as ni


def _device_id_from_hostname(hostname: str) -> int:
    """Derive a stable 16-bit device ID from the hostname using MD5."""
    digest = hashlib.md5(hostname.encode()).digest()
    return int.from_bytes(digest[:2], 'little')


class nimRumPyCommon:
    def __init__(self, meName=os.path.basename(__file__)):

        self.meStr = "**" + meName + "**: "

        self.run = 1
        signal.signal(signal.SIGINT, self.signal_handler)

        try:
            self.myName = socket.gethostname()
        except:
            self.myName = "Unknown1"

        self.myId = _device_id_from_hostname(self.myName)

        t = nimRumCheckHW.nimRumCheckHW()

    def lclPrint(self, pStr):
        print(self.meStr + pStr)
        sys.stdout.flush()

    def signal_handler(self, sig, frame):
        self.lclPrint("Catched Ctrl-C, stopping")
        self.run = 0

    def getMyUniqueName(self):
        return self.myName

    def getMyUniqueId(self):
        return self.myId

    def getBCAddr(self, nicName: str) -> tuple:
        """Resolve the IPv4 broadcast address of an interface.

        Fails fast and lets systemd retry. There is deliberately no wait loop
        here: systemd already owns restart policy, it does so with backoff and
        logging, and a wait inside the process just hides the condition from
        `systemctl status` while pinning a slot that cannot serve audio anyway.
        The unit must therefore carry an unlimited start limit — see
        device-config/nimrum-rx.service.

        Background. This used to catch every exception, print "'nic' must be one
        of the following", and call `quit()`. Two consequences, both bad:
          * The message blamed the config, which was typically correct and
            unchanged. The interface existed; it had no DHCP lease yet, because
            `network-online.target` is satisfied before an interface is configured.
          * `quit()` exits 0, so systemd logged status=0/SUCCESS and
            `Restart=on-failure` never fired. A receiver that lost this race
            stayed inactive until started by hand.

        Args:
            nicName: Interface name, or "lo" for local-clock operation.

        Returns:
            Tuple of (broadcast address, useLocalClock flag).

        Raises:
            RuntimeError: Interface absent, or present without an IPv4 address.
                          The caller must exit non-zero so systemd restarts us.
        """
        if nicName == "lo":
            return "127.0.0.255", 1

        interfaces = ni.interfaces()
        if nicName not in interfaces:
            raise RuntimeError(
                f"'nic' is {nicName!r}, which does not exist. "
                f"Available: {interfaces}. This is a config error if the name is "
                f"wrong, or the driver has not attached yet."
            )

        try:
            bcastAddr = ni.ifaddresses(nicName)[ni.AF_INET][0]["broadcast"]
        except (KeyError, IndexError, ValueError) as exc:
            # Distinct from the case above, and the distinction is the whole point:
            # the interface is there, so the nic setting is right and waiting for
            # DHCP is what is needed. Restarting is the wait.
            raise RuntimeError(
                f"{nicName} exists but has no IPv4 broadcast address yet "
                f"({exc.__class__.__name__}). Normal for the first seconds after a "
                f"cold boot — exiting so systemd restarts us. Check "
                f"DHCP/wpa_supplicant if it persists, not the nic setting."
            ) from exc

        self.lclPrint("BCAddr:" + str(bcastAddr))
        return bcastAddr, 0
