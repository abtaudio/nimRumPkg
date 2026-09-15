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

import math

"""
Convert volume value between 0-127 to match your speakers and enables/disables 'soft' volume
Return value must match your PCM/DAC API

*** DACs ***
IQ Audio, Pi-DAC Zero
  dtoverlay=iqaudio-dacplus
  hw:CARD=IQaudIODAC, volMin: 0, volMax: 207 
  -> PCM 5122 <-
  
HifiBerry DAC+ (+ADC)
  dtoverlay=hifiberry-dacplus
  hw:CARD=sndrpihifiberry, volMin: 0, volMax: 207
  -> PCM 5122 <-

Raspyplay
  dtoverlay=iqaudio-dacplus
  hw:CARD=IQaudIODAC, volMin: 0, volMax: 207 
  -> PCM 5122 <-

DM, DIY More
  dtoverlay=hifiberry-dac
  hw:CARD=sndrpihifiberry, volMin: 0, volMax: 255
  -> PCM5102A <-

*** AMPs ***
JustBoom Amp HAT, peak power output of 2 x 55 Watts *Note1
  dtoverlay=justboom-dac
  hw:CARD=sndrpijustboomd, volMin: 0, volMax: 207
  20dB gain (adj. between 20/26dB)
  -> TAS 5756M <-

IQ Audio, Pi-DigiAMP+, up to 2 x 35W *Note1
  dtoverlay=iqaudio-dacplus
  hw:CARD=IQaudIODAC, volMin: 0, volMax: 207 
  20dB gain (adj. between 20/26dB)
  -> TAS 5756M <-

pHAT-HIFI
  dtoverlay=hifiberry-dac
  hw:CARD=sndrpihifiberry, volMin: 0, volMax: 255
  20dB gain typically, 6+6W at 8ohm (at 10% THD+N From a 10V Supply)
  -> PCM5102A  + TPA3113-D2 <-

NOTE 1:
  TAS 5756M: 30-W stereo, 40-W mono

"""

class nimRumRxVolumeConversion():

  def __init__(self, pcmType="default"):
      self.pcmType = pcmType if pcmType else "default"
      self.nimRumRange = 127

      self.softVolumeEnable = 0
      self.volInLast = -1
      self.volOut = -1

  def getVolVal(self, volIn):
    if volIn != self.volInLast:
      self.volInLast = volIn

      if self.pcmType == "PCM_5122":
          self._pcm5122(volIn)
      elif self.pcmType == "TAS_5756M":
          self._pcm5122(volIn)
      elif self.pcmType == "PCM5102A":
          # Have no HW volume control.
          self._nimRumSftVol(volIn)
      elif self.pcmType == "ALSA_SFT_VOL":
          self._alsaSftVol(volIn)
      elif self.pcmType == "NIMRUM_SFT_VOL":
          self._nimRumSftVol(volIn)
      elif self.pcmType == "default":
          self._default(volIn)
      else:
          print("{}, Cannot convert volume. pcmType:{} volIn:{}".format(__file__, self.pcmType, volIn))
          self.softVolumeEnable = 1
          self.volInLast = 0
          self.volOut = 0

    return self.softVolumeEnable, self.volOut


  def _default(self, volIn):
    self.softVolumeEnable = 0
    self.volOut = volIn

  def _pcm5122(self, volIn):
    """
    Converts to register settings for PCM512x / TAS5756M.
    127 steps × 0.5 dB = 63.5 dB range.
    Register 207 = 0 dB, register 80 = -63.5 dB.
    """
    devSilent = 0
    devMin = 80
    devMax = 207
    devRange = devMax - devMin  # = 127, exactly 1:1 with nimRumRange

    self.volOut = devSilent
    if volIn > 0:
      self.volOut = devMin + volIn  # 1:1 mapping, 0.5 dB/step

    self.softVolumeEnable = 0

  def _alsaSftVol(self, volIn):
    """
    NOTE: this path is NOT on the 0.5 dB-per-step grid the rest of the volume
    system assumes — it hands a linear 0-255 value to ALSA softvol, which
    applies its own curve. Do not calibrate levels (volCal, stored in volume
    steps) on a device set to ALSA_SFT_VOL without converting the scale first.

    Expects ALSA to be configured like this:

    pcmDevName: 'default'
    volumeDevName: 'default'

    asound.conf:
    pcm.sftvol {
        type softvol
        slave.pcm "plughw:0"
        control {
            name "Master"
            card 0
        }
    }

    pcm.!default {
        type plug
        slave.pcm "sftvol"
    }

    ctl.!default {
        type hw
        card 0
    }
    """
    devSilent = 0
    devMin = 0
    devMax = 255
    devRange = devMax - devMin
    rate = float(devRange)/float(self.nimRumRange)

    self.volOut = devSilent
    if volIn > 0:
      self.volOut = int(devMin + round(rate * float(volIn)))

    self.softVolumeEnable = 0

  def _nimRumSftVol(self, volIn):
    """
    Using nimRum built in volume control, for DACs with no hardware mixer.

    Pass-through: the C side (_magpipe_setSoftVolumeVal) applies the same
    0-127 / 0.5 dB-per-step scale as the PCM512x/TAS5756M hardware mixer, so
    one volume step means 0.5 dB on every device in the fleet. Rescaling to
    some other range here would break that — and a volCal step would then be
    worth a different number of dB per board.

    Needs nimRumLib >= 1.7.0. Against an older .so this maps 127 to roughly
    +24 dB of requested gain, which clips to unity, i.e. no usable volume
    control. The LIB_VERSION_MIN gate is what prevents that pairing.
    """
    self.volOut = volIn
    self.softVolumeEnable = 1

####################################################################################################
# *********** MAIN ***********
####################################################################################################
if __name__ == '__main__':

  import matplotlib.pyplot as plt
  import numpy as np

  def pcm512x_TAS5756_regTodB(regIn):
    """
    PCM512x

    Default value: 00110000
    00000000: +24.0 dB = 0
    00000001: +23.5 dB
    . . .
    00101111: +0.5 dB
    00110000: 0.0 dB = 48
    00110001: -0.5 dB
    ...
    11111110: -103 dB = 254
    11111111: Mute

    Driver seems to stop at +0dB and invert the volume value
    """
    if regIn < 0 or regIn > 207:
      print("pcm512x_TAS5756_dB out of range")

    regVal = (255 - regIn)

    dB = 24 - 0.5 * regVal

    if regVal == 255: # Mute
        dB=-103

    return dB

  def gainTodB(gain):
    dB = -103
    if gain > 0:
      dB = 20*math.log10(gain)
    return dB

  def swVol(volume):
    """Model of _magpipe_setSoftVolumeVal — keep in sync with the C."""
    if volume <= 0:
      return 0
    if volume > 127:
      volume = 127
    dB = -63.0 + (volume - 1) * 0.5
    d = pow(10, dB / 20.0)
    return min(d, 1.0)



  NUMPY_VOL_RANGE = 127 # nimRUm implements a volume between 0 and 127
  volRange = range(NUMPY_VOL_RANGE +1)

  dev_512x = nimRumRxVolumeConversion(pcmType="PCM_5122")
  dev_aSft = nimRumRxVolumeConversion(pcmType="ALSA_SFT_VOL")
  dev_nSft = nimRumRxVolumeConversion(pcmType="NIMRUM_SFT_VOL")

  regVal_512x = []
  regVal_aSft = []
  regVal_nSft = []
  dB_512x = []
  dB_aSft = []
  dB_nSft = []

  for volIn in volRange:

    softVal, regVal = dev_512x.getVolVal(volIn)
    #regVal += 6
    regVal_512x.append(regVal)
    dB = pcm512x_TAS5756_regTodB(regVal)
    dB_512x.append(dB)

    softVal, regVal = dev_aSft.getVolVal(volIn)
    regVal_aSft.append(regVal)
    dB = gainTodB(float(regVal)/255.0)
    dB_aSft.append(dB)

    softVal, regVal = dev_nSft.getVolVal(volIn)
    regVal_nSft.append(regVal)
    nimRumGainCurve = swVol(regVal)
    dB = gainTodB(nimRumGainCurve)
    dB_nSft.append(dB)


  for idx, volIn in enumerate(volRange):
    print("VolIn:{} ".format(volIn), end='')
    print("512x:{}({}dB) ".format(regVal_512x[idx], dB_512x[idx]), end='')
    print("aSft:{}({}dB) ".format(regVal_aSft[idx], dB_aSft[idx]), end='')
    print("nSft:{}({}dB) ".format(regVal_nSft[idx], dB_nSft[idx]), end='')
    print("")

  figure, axis = plt.subplots(2, 1) 
  axis[0].plot(volRange, dB_512x, 'r', label='dB_512x')
  axis[0].plot(volRange, dB_aSft, 'g', label='dB_aSft')
  axis[0].plot(volRange, dB_nSft, 'b', label='dB_nSft')


  axis[1].plot(np.diff(dB_512x), 'r', label='dB_512x_diff')
  axis[1].plot(np.diff(dB_aSft), 'g', label='dB_aSft_diff')
  axis[1].plot(np.diff(dB_nSft), 'b', label='dB_nSft_diff')
  plt.ylim((-1,+4))

  print("dB_512x: dB / vol:{}".format(np.mean(np.diff(dB_512x)[2:])))
  print("dB_aSft: dB / vol:{}".format(np.mean(np.diff(dB_aSft)[2:])))
  print("dB_nSft: dB / vol:{}".format(np.mean(np.diff(dB_nSft)[2:])))

  axis[0].legend()
  axis[1].legend()
  plt.show()
