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

import json
import os
import sys
import yaml

try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper

from nimRum.tx import nimRumTxRemote
from nimRum.common import nimRumRotaryEnc
from nimRum.tx import nimRumTxLanCtrl

# Scene state file (groups + scene_mute), stored next to config
_SCENE_STATE_FILENAME = "nimRumSceneState.json"

# Volume range — must match LIB_PREZO_VOL_MAX in C (7-bit wire protocol)
VOL_MAX = 127

# One wire volume step in dB. The PCM512x/TAS5756M hardware mixer is 0.5 dB per
# register step, and the software volume curve is matched to it, so this holds
# for every DAC in the fleet. Anything that converts a measured dB into a
# volume value must go through this.
VOL_DB_PER_STEP = 0.5

# The two Levels-tab sliders each map onto one layout entry, with the fallback
# used when a client has no entry for that specific layout. Kept in one place
# because the read path (snapshots), the write path (setLevelForLayout) and
# routes_levels.py must agree, or a slider silently edits nothing.
STEREO_LAYOUT_KEYS = ("stereo", "default")
MULTI_LAYOUT_KEYS = ("5.1(side)", "default")


class nimRumTxCfg(nimRumTxRemote.nimRumTxRemote, nimRumTxLanCtrl.nimRumTxLanCtrl):
    def __init__(
        self,
        volumeCallback,
        latencyCallback,
        configFile="./txConfig.yaml",
    ):

        self.volumeCallback = volumeCallback
        self.latencyCallback = latencyCallback
        self.configFile = configFile

        nimRumTxLanCtrl.nimRumTxLanCtrl.__init__(self)

        try:
            print("Reading: " + configFile)
            cfgFile = open(configFile)
            self.cfg = yaml.load(cfgFile, Loader=Loader)["nimRumTXConfig"]
            cfgFile.close()
        except Exception as e:
            print("Failed opening TX configuration file: " + configFile)
            print("Error message:{}".format(e))
            whereAmI = os.path.dirname(os.path.abspath(__file__))
            print(" You can find an example here: " + whereAmI + "/txConfig.yaml")
            quit()

        # startupVolume is deliberately low-but-audible: after a restart or a
        # power cut TX comes up quiet rather than at whatever the last session
        # was using. The live volume is runtime state and is never written back.
        self.volume = self._getTxVal("startupVolume", default=10)

        # printLevel — printout verbosity, same names and values as rxConfig.yaml.
        # Defaults to "warn" rather than the library default of "note" because TX
        # emits a note per reply-queue event: measured 2026-09-09 at ~48000 journal
        # lines an hour, which held TX's journal to about one hour of history and
        # destroyed the evidence for a network outage 14 h earlier. Notes are still
        # available by setting printLevel: note when you want them.
        self.printLevel = self._getTxVal("printLevel", default="warn")
        self.latency = self._getTxVal("latency_us", default=100000)
        self.logEnable = self._getTxVal("logEnable", default=0)
        defTmp = os.path.dirname(os.path.abspath(configFile))
        self.logPath = self._getTxVal("logPath", default=defTmp)
        self.pcmDevInputName = self._getTxVal("pcmDevInputName", default="")
        self.clientData = self._getTxVal("clients", default=[])
        self.virtualChannels = self._getTxVal("virtualChannels", default=[])
        self.cliIdErrorInsertion = self._getTxVal("cliIdErrorInsertion", default=-1)

        # SSH key for fleet communication (relative to txConfig.yaml directory)
        ssh_key_name = self._getTxVal("ssh_key", default="id_nimrum")
        cfg_dir = os.path.dirname(os.path.abspath(configFile))
        self.ssh_key_path = os.path.join(cfg_dir, ssh_key_name)

        self.numOfCli = len(self.clientData)

        # Filter: only enabled clients are passed to the C layer
        allEnabled = self._getCliVals("enabled", default=True)
        self._enabledMask = [bool(e) for e in allEnabled]
        self._disabledNames = [
            self.clientData[i].get("name", "?")
            for i in range(self.numOfCli) if not self._enabledMask[i]
        ]
        if self._disabledNames:
            print("Disabled clients (not sent to C): {}".format(self._disabledNames))

        # Rebuild clientData with only enabled clients
        self.clientData = [self.clientData[i] for i in range(self.numOfCli) if self._enabledMask[i]]
        self.numOfCli = len(self.clientData)
        self.cliIds = list(range(0, self.numOfCli))

        self.cliNames = self._getCliVals("name", default="Missing")
        self.cliLocation = self._getCliVals("location")
        self.cliVolCal = self._getCliVals("volCal", default=0)
        self.cliLatencyOffset = self._getCliVals("offset_us", default=0)

        # Channel layout system
        self.channelLayouts = self._getTxVal("channelLayouts", default={})
        self.defaultLayout = self._getTxVal("default_layout", default="stereo")
        self.activeLayout = self.defaultLayout

        # Parse per-client layout mappings
        self._clientLayouts = []  # list of dicts: {layoutName: {channel, level}}
        for cli in self.clientData:
            layouts = cli.get("layouts", None)
            if layouts is None:
                print("ERROR: Client '{}' missing 'layouts' key in txConfig".format(
                    cli.get("name", "?")))
                quit()
            self._clientLayouts.append(layouts)

        # Snapshots of layouts.*.level, for the Levels tab and printCfg only.
        # getVolume reads the layouts dict directly — see getVolume for why
        # these must never be added on top of it.
        self.cliVolStereoAdj = [0] * self.numOfCli
        self.cliVolMultiAdj = [0] * self.numOfCli
        for cliId in self.cliIds:
            self._refreshVolAdjSnapshots(cliId)

        self.latencyError = 0
        self.activeChannels = 0

        # Two-layer mute state
        self.temporary_mute = False
        self.scene_mute = {cli_id: False for cli_id in self.cliIds}
        self.groups = [[cli_id] for cli_id in self.cliIds]

        # Load persisted scene state (groups + scene_mute)
        self._scene_state_file = os.path.join(
            os.path.dirname(os.path.abspath(configFile)),
            _SCENE_STATE_FILENAME,
        )
        self._load_scene_state()


        # #### Set CODEC for audio transport ####
        CODEC_NONE = 0 # 'Empty', dummy packets are sent
        CODEC_WAVE = 1 # RAW samples, probably just ONE channel per client reasonable
        CODEC_FLAC = 2 # Lossless, and 2 channels per client possible. NOTE: Be aware of CPU load on TX if using many channels!
        CODEC_OPUS = 3 # Potential packet loss healing, and 2 channels per client possible. NOTE: Be aware of CPU load on TX if using many channels!
        CODEC_MAP = {"WAVE": CODEC_WAVE, "FLAC": CODEC_FLAC, "OPUS": CODEC_OPUS, "NONE": CODEC_NONE}
        codec_val = self._getTxVal("codec", default=CODEC_WAVE)
        if isinstance(codec_val, str):
            codec_val = CODEC_MAP.get(codec_val.upper(), CODEC_WAVE)
        self.codec = codec_val

        testErrFound = False
        # WAVE codec: only 1 channel per client
        if self.codec == CODEC_WAVE:
            for idx, layouts in enumerate(self._clientLayouts):
                for layout_name, entry in layouts.items():
                    if entry is None:
                        continue
                    # New format uses integer channel, not list
                    ch = entry.get("channel")
                    if isinstance(ch, list) and len(ch) > 1:
                        print("Found > 1 channels assigned to client {}, in CODEC_WAVE mode".format(idx))
                        testErrFound = True
        if testErrFound:
            print("See {} for more info".format(__file__))
            quit()

        # Validate channel numbers are within input array bounds (0-31)
        MAX_CHANNEL_NUMBER = 31
        for idx, layouts in enumerate(self._clientLayouts):
            for layout_name, entry in layouts.items():
                if entry is None:
                    continue
                ch = entry.get("channel")
                if ch is not None and ch > MAX_CHANNEL_NUMBER:
                    print("ERROR: Client {} uses channel {}, max is {}".format(
                              idx, ch, MAX_CHANNEL_NUMBER))
                    quit()

        for virtCh in self.virtualChannels:
            chNum = virtCh.get("channelNumber", 0)
            if chNum > MAX_CHANNEL_NUMBER:
                print("ERROR: virtualChannel {} exceeds max channel number {}".format(
                          chNum, MAX_CHANNEL_NUMBER))
                quit()

        nimRumTxRemote.nimRumTxRemote.__init__(self,
            remote=self._getTxVal("remoteModel", default="LG_AKB72915207"), 
            up=self._getTxVal("keyUp", default="KEY_VOLUMEUP"), 
            down=self._getTxVal("keyDown", default="KEY_VOLUMEDOWN"), 
            mute=self._getTxVal("keyMute", default="KEY_MUTE"), 
            timeError=self._getTxVal("keyTimeError", default="KEY_RED")
            )

        # If Remote not avaiable, then enable a "volume knob" (currently used to insert fake timing error)
        if self.lircAvailable == False:
            self.nimRumRotaryEnc = nimRumRotaryEnc.nimRumRotaryEnc(callback=self.latencyErrorRotate)

        self.printCfg()
        sys.stdout.flush()

    def getPrintLevel(self) -> int:
        """Resolve printLevel to the value the C library expects.

        Accepts err/warn/note/debug or 0-3, matching nimRumRxCfg.getPrintLevel.

        Returns:
            0=ERR, 1=WARN, 2=NOTE, 3=DEBUG.
        """
        levels = {"err": 0, "warn": 1, "note": 2, "debug": 3}
        val = self.printLevel

        if isinstance(val, str):
            level = levels.get(val.strip().lower())
            if level is None:
                print(
                    f"WARNING: printLevel '{val}' is unknown. "
                    f"Use one of {sorted(levels)}. Falling back to 'warn'."
                )
                return levels["warn"]
            return level

        if isinstance(val, int) and not isinstance(val, bool) and 0 <= val <= 3:
            return val

        print(f"WARNING: printLevel {val!r} is invalid. Falling back to 'warn'.")
        return levels["warn"]

    def _getTxVal(self, keyName, default=None):
        res = default
        if keyName in self.cfg:
            res = self.cfg[keyName]
        else:
            print("Using default value for: " + str(keyName) + " = " + str(default))

        return res

    def _getCliVals(self, keyName, default=None):
        res = []
        for idx, d in enumerate(self.clientData):
            if keyName in d:
                res.append(d[keyName])
            else:
                print("Using default value for client number {}/{}: {}={}".format(idx, d["name"], keyName, default))

                res.append(default)
        return res

    def _printList(self, name, L):
        print("".join("{0:<17}".format(str(k)) for k in [name] + L))

    def printCfg(self):
        print("#### txConfig:")
        self._printList("Index:", self.cliIds)
        self._printList("Name:", self.cliNames)
        self._printList("Location:", self.cliLocation)
        self._printList("Vol.Adj. stereo:", self.cliVolStereoAdj)
        self._printList("Vol.Adj. multi:", self.cliVolMultiAdj)
        print("  Default layout: " + self.defaultLayout)
        print("####")

    def volumeUp(self):
        if self.temporary_mute:
            self.temporary_mute = False
        else:
            self.volume = min(self.volume + 2, VOL_MAX)
        self.volumeCallback()

    def volumeDown(self):
        if self.temporary_mute:
            self.temporary_mute = False
        else:
            self.volume = max(self.volume - 2, 0)
        self.volumeCallback()

    def volumeMuteToggle(self):
        self.temporary_mute = not self.temporary_mute
        self.volumeCallback()

    def latencyErrorToggle(self):
        if self.cliIdErrorInsertion == -1:
            print("cliIdErrorInsertion not set")
            return 

        if self.latencyError == 0:
            self.latencyError = 100
        elif self.latencyError == 100:
            self.latencyError = 500
        elif self.latencyError == 500:
            self.latencyError = 1000
        elif self.latencyError == 1000:
            self.latencyError = 2000
        elif self.latencyError == 2000:
            self.latencyError = 10000
        elif self.latencyError >= 10000:
            self.latencyError = 0

        print(
            "#### "
            + self.cliNames[self.cliIdErrorInsertion]
            + ", error insertion: "
            + str(self.latencyError)
        )
        self.latencyCallback(cliId=self.cliIdErrorInsertion, error=self.latencyError)

    def latencyErrorRotate(self, val, dir):
        if self.cliIdErrorInsertion == -1:
            print("cliIdErrorInsertion not set")
            return 

        self.latencyError = val * 20

        print(
            "#### "
            + self.cliNames[self.cliIdErrorInsertion]
            + ", error insertion: "
            + str(self.latencyError)
        )
        self.latencyCallback(cliId=self.cliIdErrorInsertion, error=self.latencyError)

    def getVolume(self, cliId):
        """Get configured volume for a client (ignoring mute layers).

        Three terms, all in wire volume steps: the main volume, the active
        layout's level, and volCal.

        The layout level is read via getLevelForLayout only. cliVolStereoAdj /
        cliVolMultiAdj are snapshots of the *same* layouts.*.level fields, kept
        for the Levels tab, so adding them here counted the layout level twice
        (fixed 2026-09-13 — it made every EQ level calibration wrong).
        """
        level = self.getLevelForLayout(cliId, self.activeLayout)
        volCal = self.cliVolCal[cliId]
        volRes = int(round(self.volume + level + volCal))

        if volRes <= 0:
            return 0
        elif volRes > VOL_MAX:
            return VOL_MAX
        else:
            return volRes

    def getMeasurementVolume(self):
        """Wire volume to use while acoustically measuring a single speaker.

        Deliberately excludes every per-speaker term — layout level and volCal
        — so the measurement reports the speaker's *physical* level rather than
        the level it happens to be trimmed to. Without this, a re-run measures
        the correction applied by the previous run and cancels it.

        Main volume is kept, unchanged, for two reasons: it is common to every
        speaker and therefore drops out of the between-speaker comparison, and
        it is the one knob the user controls. Deliberately no floor is applied —
        raising the level on the user's behalf could send an unexpectedly loud
        chirp into a speaker. Too quiet is caught and reported by the
        signal-level check instead.
        """
        return max(0, min(VOL_MAX, int(self.volume)))

    def getChannelForLayout(self, cliId, layoutName):
        """Get channel number for a client in a given layout.

        Returns None if client is silent (not mapped) in this layout.
        """
        layouts = self._clientLayouts[cliId]
        entry = layouts.get(layoutName)
        if entry is None:
            entry = layouts.get("default")
        if entry is None:
            return None
        return entry.get("channel")

    def getLevelForLayout(self, cliId, layoutName):
        """Get level (dB adj, in wire volume steps) for a client in a layout."""
        layouts = self._clientLayouts[cliId]
        entry = layouts.get(layoutName)
        if entry is None:
            entry = layouts.get("default")
        if entry is None:
            return 0
        return entry.get("level", 0)

    def _layoutEntry(self, cliId, layoutKeys):
        """First existing layout entry for a client, in fallback order."""
        layouts = self._clientLayouts[cliId]
        for key in layoutKeys:
            entry = layouts.get(key)
            if entry is not None:
                return entry
        return None

    def _refreshVolAdjSnapshots(self, cliId):
        """Re-read the Levels-tab snapshots from the layouts dict."""
        stereo = self._layoutEntry(cliId, STEREO_LAYOUT_KEYS) or {}
        multi = self._layoutEntry(cliId, MULTI_LAYOUT_KEYS) or {}
        self.cliVolStereoAdj[cliId] = stereo.get("level", 0)
        self.cliVolMultiAdj[cliId] = multi.get("level", 0)

    def setLevelForLayout(self, cliId, layoutKeys, level):
        """Set a client's layout level, in wire volume steps.

        Writes the layouts dict, because that is what getVolume reads, then
        refreshes the snapshots. Writing only the snapshot leaves the slider
        with no audible effect.

        Returns True if a layout entry was found and written.
        """
        entry = self._layoutEntry(cliId, layoutKeys)
        if entry is None:
            return False
        entry["level"] = int(level)
        self._refreshVolAdjSnapshots(cliId)
        return True

    def setActiveLayout(self, layoutName):
        """Switch to a new layout. Returns True if changed."""
        if layoutName == self.activeLayout:
            return False
        self.activeLayout = layoutName
        return True

    def get_effective_volume(self, cli_id: int) -> int:
        """Get resolved volume for a client, applying both mute layers."""
        if self.temporary_mute:
            return 0
        if self.scene_mute.get(cli_id, False):
            return 0
        # Client not mapped in active layout → silence
        if self.getChannelForLayout(cli_id, self.activeLayout) is None:
            return 0
        return self.getVolume(cli_id)

    def scene_mute_toggle(self, cli_ids: list) -> None:
        """Toggle scene mute for a list of clients (sync-then-toggle).

        If any client in the list is unmuted, mute all.
        If all are already muted, unmute all.
        """
        any_unmuted = any(not self.scene_mute.get(cid, False) for cid in cli_ids)
        new_state = True if any_unmuted else False
        for cid in cli_ids:
            self.scene_mute[cid] = new_state
        self._save_scene_state()
        self.volumeCallback()

    def scene_mute_set(self, cli_ids: list, mute: bool) -> None:
        """Explicitly set scene mute state for a list of clients."""
        for cid in cli_ids:
            self.scene_mute[cid] = mute
        self._save_scene_state()
        self.volumeCallback()

    def set_groups(self, groups: list) -> None:
        """Update group membership. groups is a list of lists of client IDs."""
        self.groups = groups
        self._save_scene_state()

    def setVolumeMode(self, activeChannels):
        self.activeChannels = activeChannels

    def getLatency(self, cliId):
        lat = self.latency + self.cliLatencyOffset[cliId]
        return lat

    def _load_scene_state(self) -> None:
        """Load persisted scene state (groups + scene_mute) from JSON."""
        try:
            with open(self._scene_state_file, "r") as f:
                data = json.load(f)
            # Restore groups if valid for current client count
            if "groups" in data:
                all_ids = set()
                for g in data["groups"]:
                    all_ids.update(g)
                if all_ids.issubset(set(self.cliIds)):
                    self.groups = data["groups"]
            # Restore scene_mute
            if "sceneMute" in data:
                for cli_id in self.cliIds:
                    self.scene_mute[cli_id] = data["sceneMute"].get(
                        str(cli_id), False
                    )
            print("Loaded scene state from: " + self._scene_state_file)
        except FileNotFoundError:
            pass
        except Exception as e:
            print("Failed loading scene state: {}".format(e))

    def _save_scene_state(self) -> None:
        """Persist scene state (groups + scene_mute) to JSON."""
        data = {
            "groups": self.groups,
            "sceneMute": {str(k): v for k, v in self.scene_mute.items()},
        }
        try:
            with open(self._scene_state_file, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print("Failed saving scene state: {}".format(e))
