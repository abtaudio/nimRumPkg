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

import yaml

from nimRum.common import nimRumConfigDoc

try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper


# pcmMode — playback backend. Values match LIB_PREZO_HAL_PLAY_MODE_* in
# libPrezoHalPlay.h and are passed straight to the C library.
PCM_MODE_DUMMY = 0
PCM_MODE_AUTO = 1
PCM_MODE_MMAP = 2
PCM_MODE_WRITEI = 3

PCM_MODE_NAMES = {
    "dummy": PCM_MODE_DUMMY,
    "auto": PCM_MODE_AUTO,
    "mmap": PCM_MODE_MMAP,
    "writei": PCM_MODE_WRITEI,
}

# Old numeric pcmMode values, kept working so existing device configs are not
# silently reinterpreted: 1 meant mmap access, 2 meant rw access (writei).
PCM_MODE_LEGACY_INTS = {
    0: PCM_MODE_DUMMY,
    1: PCM_MODE_MMAP,
    2: PCM_MODE_WRITEI,
    3: PCM_MODE_WRITEI,
}

# printLevel — printout verbosity. Matches LIB_PREZO_LOG_LEVEL_* in
# libNimRumTargetSettings.h. Not to be confused with logEnable (.dat files).
PRINT_LEVELS = {
    "err": 0,
    "warn": 1,
    "note": 2,
    "debug": 3,
}


class nimRumRxCfg:
    def __init__(self, configFile="./rxConfig.yaml"):

        try:
            cfgFile = open(configFile)
            self.cfg = yaml.load(cfgFile, Loader=Loader)
            cfgFile.close()
            self.usefulPath = os.path.dirname(os.path.abspath(configFile))
            print("Read: " + configFile)
        except:
            print("****************************************************************")
            print("Failed opening RX configuration file: " + configFile)
            print("  No worries. There will be one written when lib is closed down")
            print("  You just need to rename it to rxConfig.yaml")
            print("****************************************************************")
            self.cfg = {}
            self.usefulPath = os.getcwd()

        # Guard against empty/malformed config (YAML parses "key:" as None)
        if not isinstance(self.cfg, dict):
            self.cfg = {}
        if not isinstance(self.cfg.get("nimRumRXConfig"), dict):
            self.cfg["nimRumRXConfig"] = {}

        self._setDefault("nic", default="wlan0")
        self._setDefault("staticDelay_us", default=0)
        self._setDefault("outputChannelEnable", default=3)
        self._setDefault("logEnable", default=0)
        self._setDefault("logPath", default=self.usefulPath)
        # NOTE by default. The per-cycle RX diagnostics that would flood the
        # ~20 MB journal (about an hour on a chatty unit) sit at debug, so note
        # now carries only the low-rate 'alive' heartbeat and sparse lifecycle
        # lines — cheap enough to keep on, and it makes a synced-but-silent RX
        # visible. Set printLevel: warn to quieten, debug for the full sync line.
        self._setDefault("printLevel", default="note")
        self._setDefault("pcmDevName", default="default")
        self._setDefault("volumeDevName", default="default")
        self._setDefault("volumeCtrl", default="NIMRUM_SFT_VOL")
        self._setDefault("pcmMode", default="auto")
        self._setDefault("forceS16", default=0)
        self._setDefault("ssh_user", default="")

    def _setDefault(self, keyName, default=None):
        if not keyName in self.cfg["nimRumRXConfig"]:
            print("Using default value for: " + keyName + " = " + str(default))
            self.cfg["nimRumRXConfig"][keyName] = default

    def get(self, keyName):
        return self.cfg["nimRumRXConfig"][keyName]

    def getPcmMode(self) -> int:
        """Resolve pcmMode to the value expected by the C library.

        Accepts the names dummy/auto/mmap/writei, or the old numeric values
        (1=mmap, 2=writei) which are still honoured but warned about.

        Returns:
            0=dummy, 1=ALSA auto, 2=ALSA force mmap+poll, 3=ALSA force writei.
        """
        val = self.get("pcmMode")

        if isinstance(val, str):
            mode = PCM_MODE_NAMES.get(val.strip().lower())
            if mode is None:
                print(
                    f"WARNING: pcmMode '{val}' is unknown. "
                    f"Use one of {sorted(PCM_MODE_NAMES)}. Falling back to 'auto'."
                )
                return PCM_MODE_AUTO
            return mode

        if isinstance(val, int) and not isinstance(val, bool):
            mode = PCM_MODE_LEGACY_INTS.get(val)
            if mode is None:
                print(
                    f"WARNING: pcmMode {val} is out of range. Falling back to 'auto'."
                )
                return PCM_MODE_AUTO
            name = [k for k, v in PCM_MODE_NAMES.items() if v == mode][0]
            print(
                f"WARNING: numeric pcmMode {val} is deprecated. "
                f"Interpreted as '{name}' — write pcmMode: {name} in rxConfig.yaml."
            )
            return mode

        print(f"WARNING: pcmMode has unsupported type {type(val)}. Using 'auto'.")
        return PCM_MODE_AUTO

    def getPrintLevel(self) -> int:
        """Resolve printLevel to the value expected by the C library.

        Accepts err/warn/note/debug or 0-3.

        Returns:
            0=ERR, 1=WARN, 2=NOTE, 3=DEBUG.
        """
        val = self.get("printLevel")

        if isinstance(val, str):
            level = PRINT_LEVELS.get(val.strip().lower())
            if level is None:
                print(
                    f"WARNING: printLevel '{val}' is unknown. "
                    f"Use one of {sorted(PRINT_LEVELS)}. Falling back to 'note'."
                )
                return PRINT_LEVELS["note"]
            return level

        if isinstance(val, int) and not isinstance(val, bool) and 0 <= val <= 3:
            return val

        print(f"WARNING: printLevel {val!r} is invalid. Falling back to 'note'.")
        return PRINT_LEVELS["note"]

    def getForceS16(self) -> int:
        """Return forceS16 as 0 or 1."""
        return 1 if self.get("forceS16") else 0

    def _getInt(self, keyName: str, default: int) -> int:
        """Coerce a config field to int, warning and defaulting on failure.

        The numeric RX fields are passed straight into the C library via
        ctypes, which rejects a str with a TypeError and crash-loops the RX.
        A stray unit suffix in the YAML editor (e.g. staticDelay_us: 710us,
        parsed by YAML as the string "710us") is enough to take a device down.
        Coerce here so a bad value degrades to a warning, mirroring
        getPcmMode/getPrintLevel.
        """
        val = self.get(keyName)
        if isinstance(val, bool):
            # bool is an int subclass; treat True/False as 1/0 explicitly.
            return 1 if val else 0
        if isinstance(val, int):
            return val
        try:
            # Accept "710", "710.0" and float 710.0; reject "710us".
            return int(str(val).strip())
        except (TypeError, ValueError):
            print(
                f"WARNING: {keyName} {val!r} is not an integer "
                f"(check for a stray unit suffix like 'us'). "
                f"Falling back to {default}."
            )
            return default

    def getStaticDelayUs(self) -> int:
        """Return staticDelay_us as an int (default 0 on a bad value)."""
        return self._getInt("staticDelay_us", 0)

    def getOutputChannelEnable(self) -> int:
        """Return outputChannelEnable as an int (default 3 = both channels)."""
        return self._getInt("outputChannelEnable", 3)

    def getLogEnable(self) -> int:
        """Return logEnable as 0 or 1."""
        return 1 if self._getInt("logEnable", 0) else 0

    def printCfg(self):
        """Print all config values."""
        print("*" * 60)
        print("Config:")
        for key, val in self.cfg["nimRumRXConfig"].items():
            print(f"  {key}: {val}")
        print("*" * 60)

    def alterDelay(self, diff):
        val = self.get("staticDelay_us") + diff
        self.cfg["nimRumRXConfig"]["staticDelay_us"] = val

    def dump(self):

        helpStr = nimRumConfigDoc.as_yaml_comments("rxConfig")

        fileName = os.path.join(self.usefulPath, "rxConfig.last")

        try:
            file = open(fileName, "w")
            file.write(helpStr)
            yaml.dump(self.cfg, file)
            file.close()
        except:
            print("Failed writing: " + fileName)


# Fields passed straight into the C library via ctypes, which rejects a
# non-integer with a TypeError and crash-loops the RX. Validated before a
# config is pushed to a device so a bad value is rejected at save time rather
# than taking the device down. Keep in sync with the getInt accessors above.
_RX_INT_FIELDS = ("staticDelay_us", "outputChannelEnable", "logEnable", "forceS16")


def _is_intlike(val) -> bool:
    """True if val is an int or a string/float that converts cleanly to int."""
    if isinstance(val, bool):
        return True
    if isinstance(val, int):
        return True
    try:
        int(str(val).strip())
        return True
    except (TypeError, ValueError):
        return False


def validate_rx_config_yaml(content: str):
    """Validate rxConfig.yaml text before it is written to a device.

    Returns (ok, error_message). error_message is "" when ok is True.

    Catches the failure class that crash-loops an RX: valid YAML whose numeric
    fields carry a non-integer value (e.g. staticDelay_us: 710us, which YAML
    parses as the string "710us"). Those fields are passed straight to the C
    library via ctypes, so a str takes the device down on every restart.
    """
    try:
        parsed = yaml.load(content, Loader=Loader)
    except yaml.YAMLError as e:
        return False, f"YAML syntax error: {e}"

    if not isinstance(parsed, dict):
        got = type(parsed).__name__
        return False, f"Config must be a mapping (dict), got {got}"

    section = parsed.get("nimRumRXConfig")
    if not isinstance(section, dict):
        return False, "Missing or malformed 'nimRumRXConfig' section"

    for field in _RX_INT_FIELDS:
        if field in section and not _is_intlike(section[field]):
            return False, (
                f"'{field}' must be an integer, got {section[field]!r}. "
                f"Remove any unit suffix (e.g. write 710, not 710us)."
            )

    return True, ""
