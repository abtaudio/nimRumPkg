/******************************************************************************
 * Copyright (C) 2021-2026 AbtAudio AB
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program. If not, see <https://www.gnu.org/licenses/>.
 *
 * As a special exception, you may link this program with the nimRumLib
 * shared libraries (libNimRumTx_ct.so, libNimRumRx_ct.so) provided by
 * AbtAudio AB without those libraries being subject to the GPL-3.0.
 ******************************************************************************/

#pragma once

#include <stdio.h>
#include <inttypes.h>
#include <alsa/asoundlib.h>
#include <alsa/pcm.h>

#define _ALSA_DEVICE_ERR_CHK(notTrue, ...) if(notTrue == 0) { nimrum_err(__VA_ARGS__); return -1; }


#include "nimrum_log.h"

typedef struct {
/*
  The important outcome of this is to know what delay at a known time
*/

  snd_pcm_status_t *pcmStatus;

  int valid;                  // 0 if not updated, 1 is updated

  int delay;
  int avail;
  int availPrev;
  int availMax;
  int overrange;

  uint64_t triggerTS;
  uint64_t statusTS;
  uint64_t statusTSPrev;
  uint64_t audioTS;
  uint64_t audioTSPrev;
  uint64_t driverTS;

  // All of these are 0, ...so not supported yet in ALSA 1.1.3?
  unsigned int audioTSValid;  // for backwards compatibility
  unsigned int audioTSType;   // actual type if hardware could not support requested timestamp
  unsigned int audioTSAccRpt; // 0 if accuracy unknown, 1 if accuracy field is valid
  unsigned int audioTSAcc;    // up to 4.29s, will be packed in separate field [ns]

  int samplesAdded;
  int consumedTot;

  int64_t pcmTS;              // Timestamp for avail & delay values
  float pcmTSvsSamplesDrift;
  int64_t aVSs;

  int aVSsPipeSize;
  int64_t aVSsFiltered;
  int aVSsFilteredStdDev;

  int aVSsInitPhaseDone;

  // For test
  int64_t   audioVSsamples;
  int64_t   statusVSsamples;

} alsa_device_stat_t;

typedef struct {
  char *deviceName;
  char mixerHWName[100]; // = first part of deviceName
	snd_pcm_t *pcm_handle;
  int isOpen; // To automatically avoid 'close' if never opened

  snd_pcm_access_t pcm_access;
 	snd_pcm_format_t pcm_format;

	unsigned int rate;
  unsigned int channels;

  snd_pcm_uframes_t framesPerPacket;

	snd_pcm_hw_params_t *HWParams;
  snd_pcm_sw_params_t *SWParams;

  int frameSize;

  unsigned int      bufferPeriodRate;
  unsigned int      bytesPerSample;

  unsigned int      bufferTime;
  snd_pcm_uframes_t bufferSize;

  unsigned int      periodTime;
  snd_pcm_uframes_t periodSize;

  snd_pcm_uframes_t whenToLoad;
  snd_pcm_uframes_t whenToStop;


  // Poll
  struct pollfd     *pollFds;
  int               pollFdsCnt;

  // For status
  alsa_device_stat_t stat;

} alsa_device_t;

int   alsa_device_start           (alsa_device_t *dev);
int   alsa_device_close           (alsa_device_t *dev);
int   alsa_device_init            (alsa_device_t *dev, char *deviceName, int rate, int channels);
int   alsa_device_poll            (alsa_device_t *dev, int timeout);
int   alsa_device_wait_and_read   (alsa_device_t *dev, int timeout, uint8_t *bufPtr, int framesToRead);

void  alsa_device_print_state     (alsa_device_t *dev);

int   _alsa_device_init_hw_params (alsa_device_t *dev);
int   _alsa_device_init_sw_params (alsa_device_t *dev);
int   _alsa_device_init_poll      (alsa_device_t *dev);

int   alsa_device_get_first_hw    (char* devName);
