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
import os

from nimRum.tx import libNimRumTx_py as txLib
from nimRum.common import nimRumPyCommon
from nimRum.tx import nimRumTxCfg
from nimRum.common import nimRumPyLed
from nimRum.tonegen import ToneGenChannel
from nimRum.tx.nimRumTxStatusApi import NimRumTxStatusApi


class nimRumTx(nimRumPyCommon.nimRumPyCommon):
    def __init__(self, configFile="./txConfig.yaml"):
        nimRumPyCommon.nimRumPyCommon.__init__(self, meName=os.path.basename(__file__))

        # Version check: ensure nimRumLib is compatible with this nimRumPkg
        lib_version = txLib.c_libNimRumGetVersion()
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

        self.cfg = nimRumTxCfg.nimRumTxCfg(
            self.volumeSet, self.latencySet, configFile=configFile
        )
        self.led = nimRumPyLed.nimRumPyLed()
        self.tonGen = {}
        self._injection = None  # Signal injection state (set by calibration commands)

        # Status API for WebUI
        self.statusApi = NimRumTxStatusApi()
        self.statusApi.set_status_fn(self.getStatus)
        self.statusApi.set_command_fn(self.handleStatusCmd)
        self.statusApi.start()

    def volumeSet(self, cliId=None, val=0):
        if cliId is None:
            for cliId in self.cfg.cliIds:
                txLib.c_libNimRumTxSetVolume(
                    cliId, self.cfg.get_effective_volume(cliId)
                )
        else:
            txLib.c_libNimRumTxSetVolume(cliId, val)

    def latencySet(self, cliId=None, error=0):
        """Apply per-client latency exactly as configured.

        The source FIFO dwell is deliberately NOT subtracted here, though it does
        add to the end-to-end figure (the FIFO sits upstream of TX's timestamping,
        so 100 ms configured with a 4-packet FIFO delivers ~132 ms).

        Subtracting it was tried and rejected on 2026-09-09: the configured latency
        is the TX->RX jitter budget, sized from how good that link is, and TX
        validates it against each client's measured network minimum. Taking source
        dwell out of it would silently erode every client's margin because someone
        connected a wobbly source on a leg where lip sync does not even matter -
        coupling two independent concerns.

        Both numbers and their sum are reported instead: srcFifoLatencyUs,
        latency_us and totalLatencyUs in the status API.
        """
        if cliId is None:
            for cliId in self.cfg.cliIds:
                txLib.c_libNimRumTxSetLatency(cliId, self.cfg.getLatency(cliId) + error)
        else:
            txLib.c_libNimRumTxSetLatency(cliId, self.cfg.getLatency(cliId) + error)

    def getStatus(self):
        """Return current system status for WebUI."""
        st = txLib.c_libNimRumTxGetStatus()
        # Enrich with config data
        for i, cli in enumerate(st.get("clients", [])):
            if i < len(self.cfg.cliNames):
                cli["name"] = self.cfg.cliNames[i]
            if i < len(self.cfg.cliLocation):
                cli["location"] = self.cfg.cliLocation[i]
        st["latency_us"] = self.cfg.latency
        # Latency is reported as two independent legs plus their sum, deliberately
        # rather than folded into one number:
        #   latency_us        — the TX->RX budget, sized from how good that link is
        #                       and validated per client against its measured
        #                       network minimum.
        #   srcFifoLatencyUs  — what the source-side FIFO adds. Upstream of TX's
        #                       timestamping, so it is additive, and it depends on
        #                       the source's own transport rather than on the fleet.
        # Subtracting the second from the first was tried and rejected: it would
        # erode every client's jitter margin because one source happens to be
        # unstable. Show both and let the number that matters be visible.
        st["totalLatencyUs"] = self.cfg.latency + st.get("srcFifoLatencyUs", 0)
        st["sampleRate"] = getattr(self, '_sampleRate', 0)
        st["activeSourceId"] = txLib.c_nimRumAudioSourceRx_getActiveSourceId()
        st["activeSourceName"] = txLib.c_nimRumAudioSourceRx_getActiveSourceName()
        st["activeLayout"] = self.cfg.activeLayout
        st["defaultLayout"] = self.cfg.defaultLayout
        st["channelLayouts"] = self.cfg.channelLayouts
        st["sources"] = txLib.c_nimRumAudioSourceRx_getKnownSources()
        st["txLinkStats"] = self._get_tx_link_stats()
        st["srcLinkStats"] = txLib.c_nimRumAudioSourceRx_getSourceLinkStats()
        # Two-layer mute state
        st["temporaryMute"] = self.cfg.temporary_mute
        st["sceneMute"] = {str(k): v for k, v in self.cfg.scene_mute.items()}
        st["groups"] = self.cfg.groups
        st["mainVolume"] = self.cfg.volume
        # Wire volume the acoustic measurement must play at — main volume only,
        # no per-speaker trim. See nimRumTxCfg.getMeasurementVolume.
        st["measVolume"] = self.cfg.getMeasurementVolume()
        return st

    def _get_tx_link_stats(self):
        """Read TX device WiFi link stats from /proc/net/wireless."""
        result = {"signal": 0, "quality": 0, "txRetries": 0, "rxErrors": 0}
        try:
            with open("/proc/net/wireless", "r") as f:
                lines = f.readlines()
            # Skip 2 header lines, take first interface
            for line in lines[2:]:
                if ":" not in line:
                    continue
                parts = line.split(":")[1].split()
                if len(parts) >= 7:
                    level = float(parts[1].rstrip("."))
                    # Positive = relative, convert to dBm approx
                    if level > 0:
                        level = level - 256
                    result["signal"] = int(level)
                    result["quality"] = min(70, int(float(parts[0])))
                    result["txRetries"] = int(parts[6])
                break
        except (OSError, ValueError, IndexError):
            pass
        try:
            # Find first wireless iface for rx_errors
            with open("/proc/net/wireless", "r") as f:
                lines = f.readlines()
            for line in lines[2:]:
                if ":" not in line:
                    continue
                iface = line.split(":")[0].strip()
                err_path = f"/sys/class/net/{iface}/statistics/rx_errors"
                with open(err_path, "r") as ef:
                    result["rxErrors"] = int(ef.read().strip())
                break
        except (OSError, ValueError, IndexError):
            pass
        return result

    def handleStatusCmd(self, req):
        """Handle commands from WebUI. Always returns full status in response."""
        cmd = req.get("cmd", "")
        if cmd == "setVolume":
            cliId = req.get("cliId")
            val = req.get("val", 0)
            if val == -1:
                # Unmute: restore configured volume
                self.volumeSet(cliId=cliId, val=self.cfg.get_effective_volume(cliId))
            else:
                self.volumeSet(cliId=cliId, val=val)
        elif cmd == "setMainVolume":
            # Live only. Persisting here rewrote the whole txConfig.yaml on every
            # slider step, which is both flash wear and a corruption window on a
            # file that holds the entire client configuration. The Levels tab
            # writes startupVolume explicitly instead.
            self.cfg.volume = req.get("val", self.cfg.volume)
            self.cfg.temporary_mute = False
            self.volumeSet()
        elif cmd == "muteToggle":
            # Same action as mute on the IR remote / LanCtrl: temporary mute,
            # which silences everything and is cleared by any volume change.
            self.cfg.volumeMuteToggle()
        elif cmd == "setSource":
            txLib.c_nimRumAudioSourceRx_setActiveSourceId(req.get("sourceId", 0))
        elif cmd == "setLatency":
            self.cfg.latency = req.get("val", self.cfg.latency)
            self.latencySet()
        elif cmd == "setSceneMute":
            cli_ids = req.get("cliIds", [])
            mute = req.get("mute", True)
            self.cfg.scene_mute_set(cli_ids, mute)
        elif cmd == "setSceneMuteToggle":
            cli_ids = req.get("cliIds", [])
            self.cfg.scene_mute_toggle(cli_ids)
        elif cmd == "setGroups":
            groups = req.get("groups", [])
            self.cfg.set_groups(groups)
        elif cmd == "setVolAdj":
            cli_id = req.get("cliId")
            if cli_id is not None and 0 <= cli_id < self.cfg.numOfCli:
                # Write through to layouts.*.level, which is what getVolume
                # reads. Writing only cliVolStereoAdj/cliVolMultiAdj would move
                # the slider and change nothing audible.
                if "volStereoAdj" in req:
                    self.cfg.setLevelForLayout(
                        cli_id, nimRumTxCfg.STEREO_LAYOUT_KEYS,
                        req["volStereoAdj"]
                    )
                if "volMultiAdj" in req:
                    self.cfg.setLevelForLayout(
                        cli_id, nimRumTxCfg.MULTI_LAYOUT_KEYS,
                        req["volMultiAdj"]
                    )
                self.cfg.cliVolCal[cli_id] = req.get(
                    "volCal", self.cfg.cliVolCal[cli_id]
                )
                self.volumeSet()
        elif cmd == "injectSignal":
            self._start_injection(req)
        elif cmd == "stopInjection":
            self._stop_injection()
        elif cmd == "resetCounters":
            # Lets a user mark a baseline. The accumulating counters cannot recover on
            # their own - one transient leaves a non-zero total for the life of the TX
            # process - so without this the Status tab can only say "has ever happened".
            txLib.c_libNimRumTx_resetCounters()
        else:
            return {"error": "unknown cmd"}

        # Always return full status so UI can update in one round-trip
        return self.getStatus()

    def _start_injection(self, req):
        """Start signal injection for calibration.

        Args (from req dict):
            mode: Signal type — 'chirp', 'sinus', 'spikes' or 'buffer'.
            targetIdx: Client index to inject signal on (others get silence).
                       If list, inject on all listed indices.
                       If -1 or absent, inject on all clients.
            volume: Volume 0-127 (default 100).
            freq: Frequency in Hz (default 1000).
            duration_s: Chirp duration in seconds (default 5).
            buffer: Pre-built sample list for 'buffer' mode (16-bit range).
            oneShot: If True, stop after buffer is exhausted (default True
                     for chirp/buffer, False for sinus/spikes).
        """
        from nimRum.tonegen import ToneGenChannel
        import numpy as np

        mode = req.get("mode", "chirp")
        volume = req.get("volume", 100)
        freq = req.get("freq", 1000.0)
        sample_rate = getattr(self, '_sampleRate', 48000)

        # Build the buffer
        if mode == "buffer":
            buf = np.array(req.get("buffer", []), dtype=np.int32)
            channel = ToneGenChannel(
                mode="buffer", buffer=buf, sample_rate=sample_rate,
                one_shot=True,
            )
        else:
            duration_s = req.get("duration_s", 0)
            is_one_shot = req.get("oneShot", mode in ("chirp", "buffer"))
            channel = ToneGenChannel(
                mode=mode, freq=freq, volume=volume,
                sample_rate=sample_rate, duration_s=duration_s,
                one_shot=is_one_shot,
            )

        # Determine target indices
        target = req.get("targetIdx", -1)
        if target == -1:
            targets = list(range(self.cfg.numOfCli))
        elif isinstance(target, list):
            targets = target
        else:
            targets = [target]

        # Temporarily remap target speakers to channel 0 for injection.
        # All other speakers keep their mapping but get zeroed data.
        for cli_id in targets:
            txLib.c_libNimRumTxSetChannel(cli_id, [0])

        one_shot = req.get("oneShot", mode in ("chirp", "buffer"))

        self._injection = {
            "channel": channel,
            "targets": targets,
            "targetChannels": [0],  # Always inject on channel 0
            "oneShot": one_shot,
            "framesLeft": len(channel._buf) if one_shot else None,
        }

    def _stop_injection(self):
        """Stop signal injection and restore channel mapping."""
        self._injection = None
        # Restore normal channel assignments
        self.assignChannels(
            getattr(self, '_lastActiveChannels', 2)
        )

    def assignChannels(self, activeChannels):
        """Assign channels based on active layout."""
        layout = self.cfg.activeLayout
        for cliId in self.cfg.cliIds:
            ch = self.cfg.getChannelForLayout(cliId, layout)
            if ch is None:
                # Client not mapped in this layout — assign ch 0 (will get silence
                # because volume is 0, handled in getVolume via level)
                ch = 0
            txLib.c_libNimRumTxSetChannel(cliId, [ch])

    def runTx(self):
        bytePerSample = 2
        sampleRate = 48000
        self._sampleRate = sampleRate

        # INIT AUDIO SOURCE RECEIVER
        audioSourcePort = 53473
        self.lclPrint("Reading audio from nimRumAudioSourceRx")
        if txLib.c_nimRumAudioSourceRx_init(audioSourcePort) != 0:
            self.lclPrint("AudioSourceRx init failed")
            quit()

        # INIT TONEGENERATOR(S)
        for virtCh in self.cfg.virtualChannels:
            if "toneGeneratorMode" in virtCh:
                volume = virtCh.get("toneGeneratorVolume", 127)
                freq = virtCh.get("toneGeneratorFreq", 1000.0)
                mode = virtCh["toneGeneratorMode"]
                self.tonGen[virtCh["channelNumber"]] = ToneGenChannel(
                    mode=mode,
                    freq=freq,
                    volume=volume,
                    sample_rate=sampleRate,
                )

        # Set verbosity before init so startup notes are gated too.
        txLib.c_libNimRumTxSetPrintLevel(self.cfg.getPrintLevel())

        # INIT TX
        if txLib.c_libNimRumTxInit(self.getMyUniqueId(), self.cfg.cliNames) != 0:
            self.lclPrint("TX Init failed")
            quit()

        (res, framesPerInterval) = txLib.c_libNimRumTxConfigure(
            bytePerSample, sampleRate, self.cfg.codec
        )
        if res != 0:
            self.lclPrint("TX Cfg failed")
            quit()

        txLib.c_libNimRumTxLogs(self.cfg.logEnable)
        txLib.c_libNimRumTxSetLogsPath(self.cfg.logPath)

        self.latencySet()
        self.volumeSet()

        ###########################################################
        # LOOP FOREVER (Until Ctrl-C, or severe error)
        ###########################################################
        activeChannels = -1  # -1 to force initial configuration
        activeChannelsNew = 0
        dataValid = 0

        # INIT ARRAY FOR DATA TRANSFER
        dataOut = []
        for _ in range(32):
            dataOut.append(bytearray(4 * 2400))

        while self.run == 1:

            # GET DATA FROM AUDIO SOURCE
            (
                res,
                activeChannelsNew,
                txPPM,
                sampleRateNew,
                bytesPerSampleNew,
            ) = txLib.c_nimRumAudioSourceRx_getData(dataOut, framesPerInterval)
            dataValid = 0 if res >= 0 else -1

            # Default to stereo mode when no audio source is active.
            # Ensures volume settings and tone gen work even without a source.
            if activeChannelsNew == 0:
                activeChannelsNew = 2

            # Reconfigure TX if source sample rate changed
            if res >= 0 and sampleRateNew != sampleRate:
                sampleRate = sampleRateNew
                self._sampleRate = sampleRate
                (res2, framesPerInterval) = txLib.c_libNimRumTxConfigure(
                    bytePerSample, sampleRate, self.cfg.codec)
                self.lclPrint("Reconfigured: {}Hz fpi={}".format(
                    sampleRate, framesPerInterval))


            # Ctrl LED, if any
            if (dataValid < 0) or (self.cfg.latencyError != 0):
                self.led.red()
            else:
                if activeChannelsNew > 2:
                    self.led.blue()
                else:
                    self.led.green()

            # CREATE VIRTUAL CHANNELS, IF ANY
            for virtCh in self.cfg.virtualChannels:
                if "crossfaderPosition" in virtCh:
                    txLib.c_libNimRumChannelMixer(
                        dataOut,
                        framesPerInterval,
                        virtCh["channelNumber"],
                        virtCh["crossfaderChannelA"],
                        virtCh["crossfaderChannelB"],
                        virtCh["crossfaderPosition"],
                    )

                if "toneGeneratorMode" in virtCh:
                    chNumb = virtCh["channelNumber"]
                    self.tonGen[chNumb].read(framesPerInterval, dataOut[chNumb])

            # SIGNAL INJECTION (calibration override)
            if self._injection is not None:
                inj = self._injection
                ch = inj["channel"]
                # Zero all 16 dataOut channels first
                for i in range(16):
                    dataOut[i][:framesPerInterval * 4] = b'\x00' * (framesPerInterval * 4)
                # Write signal to the target channel(s)
                for target_ch in inj["targetChannels"]:
                    ch.read(framesPerInterval, dataOut[target_ch])
                # One-shot: count down frames remaining
                if inj["oneShot"]:
                    inj["framesLeft"] -= framesPerInterval
                    if inj["framesLeft"] <= 0:
                        self._stop_injection()

            # UPDATE CHANNEL MAPPING IF NEEDED
            # Check if SRC announced a new channel layout
            srcLayout = txLib.c_nimRumAudioSourceRx_getActiveChannelLayout()
            if srcLayout:
                # Use default_layout if SRC layout is not configured
                if srcLayout not in self.cfg._clientLayouts[0]:
                    # Check if any client has this layout defined
                    layout_known = any(
                        srcLayout in cl for cl in self.cfg._clientLayouts
                    )
                    if not layout_known:
                        srcLayout = self.cfg.defaultLayout
                layoutChanged = self.cfg.setActiveLayout(srcLayout)
            else:
                layoutChanged = False

            if activeChannels != activeChannelsNew or layoutChanged:
                activeChannels = activeChannelsNew
                self._lastActiveChannels = activeChannels
                self.assignChannels(activeChannels)
                self.cfg.setVolumeMode(activeChannels)
                self.volumeSet()

            # SEND DATA
            res, lclPortNumOfCh = txLib.c_libNimRumTxProcess(dataOut, framesPerInterval, txPPM)

            # CHECK REMOTE(s)
            self.cfg.lircCheck()
            self.cfg.lanCtrlCheck()

        ###########################################################
        # CLEAN UP
        ###########################################################
        self.led.off()
        txLib.c_nimRumAudioSourceRx_close()
        txLib.c_libNimRumTxClose()

        self.lclPrint("TX Done")


###########################################################
#### MAIN ####
###########################################################
if __name__ == "__main__":
    t = nimRumTx(configFile="./txConfig.yaml")
    t.runTx()
