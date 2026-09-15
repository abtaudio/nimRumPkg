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

import os
import subprocess
import sys

from nimRum.rx import libNimRumRx_py as rxLib

from nimRum.common import nimRumPyCommon
from nimRum.rx import nimRumRxCfg
from nimRum.common import nimRumCheckHW
from nimRum.common import nimRumPyLed
from nimRum.rx import nimRumRxVolumeConversion
from nimRum.common import nonBlockingConsole

global mySelf


# Trick for callback function
def setMyself(din):
    global mySelf
    mySelf = din


# Trick for callback function
def callbackWrapper(newState,ppmCPU, ppmPCM, volumeIn):
    global mySelf
    # print("Address of mySelf = ", id(mySelf), "  ", mySelf)
    return nimRumRx.callBack(mySelf, newState, ppmCPU, ppmPCM, volumeIn)


class nimRumRx(nimRumPyCommon.nimRumPyCommon):
    def __init__(self, configFile="./rxConfig.yaml"):
        nimRumPyCommon.nimRumPyCommon.__init__(self, meName=os.path.basename(__file__))

        self.cfg = nimRumRxCfg.nimRumRxCfg(configFile=configFile)
        self.cfg.printCfg()

        self.hw = nimRumCheckHW.nimRumCheckHW(verbose=True)

        # Version check: ensure nimRumLib is compatible with this nimRumPkg
        lib_version = rxLib.c_libNimRumGetVersion()
        import nimRum
        self.lclPrint(f"nimRumPkg {nimRum.__version__}, nimRumLib {lib_version}")
        lib_parts = [int(x) for x in lib_version.split(".")]
        min_parts = [int(x) for x in nimRum.LIB_VERSION_MIN.split(".")]
        if lib_parts[0] != min_parts[0]:
            raise RuntimeError(
                f"nimRumLib major version mismatch: lib={lib_version}, "
                f"required>={nimRum.LIB_VERSION_MIN}"
            )
        if lib_parts < min_parts:
            raise RuntimeError(
                f"nimRumLib too old: lib={lib_version}, "
                f"required>={nimRum.LIB_VERSION_MIN}"
            )
        self.led = nimRumPyLed.nimRumPyLed()
        self.kthread = nonBlockingConsole.NonBlockingConsole()
        self.volCtrl = nimRumRxVolumeConversion.nimRumRxVolumeConversion(pcmType=self.cfg.get("volumeCtrl"))

        # Used by TX for identification (channel mapping)
        self.myName = self.getMyUniqueName()
        self.myId = self.getMyUniqueId()

        self.lclPrint(f"Device ID: {self.myId} (0x{self.myId:04X})")

        # Exit non-zero on failure so systemd's Restart=on-failure retries. The
        # previous code path called quit() here, which exits 0 — systemd recorded
        # status=0/SUCCESS and never restarted, leaving the receiver inactive until
        # someone started it by hand.
        try:
            self.bcastAddr, self.useLocalClock = self.getBCAddr(self.cfg.get("nic"))
        except RuntimeError as exc:
            self.lclPrint(f"FATAL: {exc}")
            sys.exit(1)
        self.state = -10

        # Trick for callback function
        self.setDelay = rxLib.c_libNimRumRxSetStaticDelay

    def callBack(self, newState, ppmCPU, ppmPCM, volumeIn):
        # state: Tells how accurate the synch is.
        # -2: Not even getting packets
        # -1: Collecting data
        #  0, 1: The higher the better
        # +2: Now you can measure =)

        keyRead = self.kthread.get_data()
        if keyRead:
            if keyRead == "+":
                self.cfg.alterDelay(+100)
            if keyRead == "-":
                self.cfg.alterDelay(-100)
            self.setDelay(self.cfg.get("staticDelay_us"))

        if self.state != newState:
            self.state = newState
            # self.lclPrint("State updated to: " + str(self.state))
            if self.state == -2:
                self.led.red()
            if self.state == -1:
                self.led.green()
            if self.state == 0:
                self.led.blue()
            if self.state == 1:
                self.led.white()
            if self.state == 2:
                self.led.off()

        softVolumeEnable, volumeOut = self.volCtrl.getVolVal(volumeIn)

        returnValue = 0 # Stops RX is 0
        if self.run != 0:
            returnValue = 1

        return returnValue, softVolumeEnable, volumeOut

    def runRx(self):
        # SSH user reported to TX via ping for fleet management.
        # Must be set in rxConfig.yaml (process runs as root, but SSH login is pi/nimrum).
        sshUser = self.cfg.get("ssh_user") or ""

        # Printout verbosity first, so init prints obey the configured level
        rxLib.c_libNimRumRxSetPrintLevel(self.cfg.getPrintLevel())

        try:
            while self.run == 1:
                rxLib.c_libNimRumRxInit(
                    self.bcastAddr,
                    self.cfg.get("nic"),
                    self.myId,
                    self.myName,
                    callbackWrapper,
                    self.useLocalClock,
                    self.cfg.getPcmMode(),
                    self.cfg.getForceS16(),
                    self.cfg.get("outputChannelEnable"),
                    self.cfg.get("pcmDevName"),
                    self.cfg.get("volumeDevName"),
                    sshUser=sshUser,
                )

                rxLib.c_libNimRumRxSetStaticDelay(self.cfg.get("staticDelay_us"))
                rxLib.c_libNimRumRxSetLogs(self.cfg.get("logEnable"))
                rxLib.c_libNimRumRxSetLogsPath(self.cfg.get("logPath"))

                # Disable DAC auto-mute (TAS5756M/pcm512x mutes on silence)
                # Silently ignored on devices without this control.
                subprocess.run(
                    ["amixer", "-c", "0", "sset", "Auto Mute", "off"],
                    capture_output=True,
                )

                res = rxLib.c_libNimRumRxStart()
                self.lclPrint("c_libNimRumRxStart returned " + str(res))
                rxLib.c_libNimRumRxClose()

                self.led.off()

                # Return code 0 = clean exit (e.g. signal-triggered shutdown).
                # Do not restart — let the process exit so atexit/dataLogger flushes.
                if res == 0:
                    self.run = 0

        except KeyboardInterrupt:
            self.lclPrint("Ctrl-C received, shutting down...")
            self.run = 0
            rxLib.c_libNimRumRxClose()
            self.led.off()

        self.cfg.dump()


###########################################################
#### MAIN ####
###########################################################
if __name__ == "__main__":

    r = nimRumRx(configFile="./rxConfig.yaml")
    r.runRx()
