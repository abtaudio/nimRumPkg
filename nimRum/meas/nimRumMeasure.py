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

import argparse
import signal
import time
import numpy as np

import sounddevice
import wavio
from pathlib import Path
import os 

from nimRum.meas import nimRumMeasSpikes

"""
sudo pip3 install sounddevice
sudo apt-get install libportaudio2
sudo apt-get install libopenblas-dev
"""

class NIMRUM_MEAS(): 

    def __init__(self, cfg, maxTime_sec=5*60):
        self.cfg = cfg
        self.maxTime_sec = float(maxTime_sec)
        self.dataEmpty = True

        self.dataFileName = os.path.join(self.cfg['resultFolder'], "nimRumMeas.wav") 

        if self.cfg['measMode'] == 'synchErr':
            self.measType = nimRumMeasSpikes.NIMRUM_MEAS_SPIKES(capRate=self.cfg['sampleRate'], maxTime_sec=60*60, maxInt=self.cfg['maxInt'], resultFolder=self.cfg['resultFolder'], verbose=self.cfg.get('verbose', False))
        else:
            print("ERROR: Unknown measure type: {}".format(capRate=self.cfg['measMode']))

    def _limitdata(self):
        blockSize = int(self.cfg['sampleRate'] * self.cfg['interval'])

        while (float(self.data.shape[0]) / float(self.cfg['sampleRate'])) > self.maxTime_sec:
            self.data = np.delete(self.data, np.s_[0:blockSize], axis=0)

    def add(self, indata):
        if self.dataEmpty or not self.cfg['storeData']:
            self.data = indata
            self.dataEmpty = False
        else:
            self.data = np.concatenate((self.data, indata), axis=0)
            self._limitdata()

        # TODO: This behavior depends on measType...
        self.measType.add(indata)

    def storeData(self):
        wavio.write(self.dataFileName, self.data, self.cfg['sampleRate'], sampwidth=self.cfg['sampW'])
        print("Stored: {} (Rate:{} SampleSize:{} Shape:{} )".format(self.dataFileName, self.cfg['sampleRate'], self.cfg['sampW'], self.data.shape))

    def storeResult(self):
        self.measType.storeResult()

def getCfg():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument('--listDevices', '-l', action="store_true", help='List audio devices')
    parser.add_argument('--device', '-d', default=0, help='Input device ID')
    parser.add_argument('--interval', '-i', type=float, default=1.0, help='Capture interval: Seconds')
    parser.add_argument('--sampleRate', '-f', type=float, default=48000, help='Sample rate: 0=Use device default')
    parser.add_argument('--sampleSize', '-s', type=str, default='int32', help='Sample size: float32|int32|int16|int8|uint8')
    parser.add_argument('--measMode', '-m', type=str, default='synchErr', help='Meaure mode: synchErr|chirp|...')
    parser.add_argument('--verbose', '-v', action="store_true", help='Print every rejected spike candidate. Off by default: it was 25%% of one 72h log, and the counts are summarised at each store anyway')
    parser.add_argument('--storeData', '-S',  action="store_true", help='Store all collected data to file')
    parser.add_argument('--resultFolder', '-R',  default='/home/pi/LOGS', help='Folder where measurements are stored')

    args = parser.parse_args()

    if args.listDevices:
        print(sounddevice.query_devices())
        quit()

    if args.sampleRate == 0:
        args.sampleRate = sounddevice.query_devices(args.device, 'input')['default_samplerate']

    cfg = vars(args) # Convert to dict

    cfg['sampW'] = 0
    if args.sampleSize == 'int32':
        cfg['sampW'] = 4
        cfg['maxInt'] = 2147483647
    elif args.sampleSize == 'int16':
        cfg['sampW'] = 2
        cfg['maxInt'] = 32767
    else:
        print("ERROR: Only supports int32 or int16, not {}".format(args.sampleSize))
        quit()

    return cfg


def signal_handler(sig, frame):
    print("Catched Ctrl-C")
    global storeData
    global measObj
    global run
    global alreadyClosing

    run = False

    if not alreadyClosing:
        alreadyClosing = True

        if storeData:
            measObj.storeData()

        measObj.storeResult()


def callBack(indata, frames, time, status):
    global measObj

    if status:
        print("Got callback status:{}".format(status))

    #print("CAPTURES: ", indata.shape, frames, time)
    measObj.add(indata)

def main():
    signal.signal(signal.SIGINT, signal_handler)

    global storeData
    global measObj
    global run
    global alreadyClosing

    cfg = getCfg()
    storeData = cfg['storeData']

    measObj = NIMRUM_MEAS(cfg)
    run = True
    alreadyClosing = False

    blockSize = int(cfg['sampleRate'] * cfg['interval'])

    with sounddevice.InputStream(device=cfg['device'], channels=2, dtype=cfg['sampleSize'], callback=callBack,
                        blocksize=blockSize, samplerate=cfg['sampleRate']):
        while run:
            # NOT `pass`. A bare spin here burns a full core and, worse, holds
            # the GIL - which the PortAudio callback thread needs in order to
            # run callBack() at all. That starves the capture: the process sits
            # at 100% CPU while the log stops advancing, which is exactly the
            # "nimrum-meas hangs at 100% CPU with a frozen log" symptom.
            time.sleep(0.1)


if __name__ == '__main__':
    main()
