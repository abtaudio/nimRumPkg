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

#include "alsa_device.h"

int alsa_device_start(alsa_device_t *dev) {

  nimrum_note("alsa_device_(re)start: State:%s ",
                      snd_pcm_state_name(snd_pcm_state(dev->pcm_handle)));

  snd_pcm_drop(dev->pcm_handle);
  snd_pcm_reset(dev->pcm_handle);
  snd_pcm_prepare(dev->pcm_handle);
  snd_pcm_start(dev->pcm_handle);

  nimrum_note("-> %s \n",
                      snd_pcm_state_name(snd_pcm_state(dev->pcm_handle)));

  return 0;
}

int alsa_device_close(alsa_device_t *dev) {
  if (dev->isOpen == 0)
    return 1;

  snd_pcm_status_free(dev->stat.pcmStatus);
  snd_pcm_hw_params_free(dev->HWParams);
  snd_pcm_sw_params_free(dev->SWParams);

  snd_pcm_close(dev->pcm_handle);
  free(dev->pollFds);
  dev->isOpen = 0;
  return 1;
}

/*
  !Breif Init an ALSA device for capture

  param rate Set campling rate (THIS IS NOT NEEDED FOR SPDIF)
*/
int alsa_device_init(alsa_device_t *dev, char *deviceName, int rate,
                     int channels) {

  int res = 0;

  memset(dev, 0, sizeof(alsa_device_t));

  dev->deviceName = deviceName;
  dev->pcm_access = SND_PCM_ACCESS_RW_INTERLEAVED;
  dev->pcm_format = SND_PCM_FORMAT_S16_LE;
  dev->rate = rate;
  dev->channels = channels;

  dev->frameSize = (snd_pcm_format_width(dev->pcm_format) / 8) * dev->channels;

  snd_pcm_hw_params_malloc(&dev->HWParams);
  snd_pcm_sw_params_malloc(&dev->SWParams);

  // OPEN PCM
  int mode = 0;
  res = snd_pcm_open(&dev->pcm_handle, dev->deviceName, SND_PCM_STREAM_CAPTURE,
                     mode);
  _ALSA_DEVICE_ERR_CHK((res >= 0),
                   "ERROR: snd_pcm_open failed, %s. Tried open: %s\n",
                   snd_strerror(res), dev->deviceName);

  res = _alsa_device_init_hw_params(dev);
  _ALSA_DEVICE_ERR_CHK((res >= 0), "ERROR: _alsa_device_init_hw_params failed \n");

  res = _alsa_device_init_sw_params(dev);
  _ALSA_DEVICE_ERR_CHK((res >= 0), "ERROR: _alsa_device_init_sw_params failed \n");

  res = snd_pcm_prepare(dev->pcm_handle);
  _ALSA_DEVICE_ERR_CHK((res >= 0), "ERROR: snd_pcm_prepare failed \n");

  // PREPARE POLL
  res = _alsa_device_init_poll(dev);
  _ALSA_DEVICE_ERR_CHK((res >= 0), "ERROR: _alsa_device_init_poll failed \n");

  // PREPARE for status
  snd_pcm_status_malloc(&dev->stat.pcmStatus);
  dev->stat.availPrev = dev->bufferSize;
  dev->stat.samplesAdded = 0;
  dev->stat.consumedTot = 0;
  dev->stat.audioTSPrev = 0;
  dev->stat.statusTSPrev = 0;
  dev->stat.pcmTS = 0;
  dev->stat.pcmTSvsSamplesDrift = 0;
  dev->stat.valid = 0;

  dev->bytesPerSample = snd_pcm_hw_params_get_sbits(dev->HWParams) / 8;

  dev->isOpen = 1;

  return 0;
}

int _alsa_device_init_hw_params(alsa_device_t *dev) {

  int res = 0;
  int dir = 0;

  // GET CONFIG HW PARAMS
  res = snd_pcm_hw_params_any(dev->pcm_handle, dev->HWParams);
  _ALSA_DEVICE_ERR_CHK((res >= 0), "Failed getting all possible HW params: %s \n",
                   snd_strerror(res));

  // ACCESS TYPE
  res = snd_pcm_hw_params_set_access(dev->pcm_handle, dev->HWParams,
                                     dev->pcm_access);
  _ALSA_DEVICE_ERR_CHK((res >= 0), "ERROR: snd_pcm_hw_params_set_access: %s \n",
                   snd_strerror(res));

  // FORMAT
  res = snd_pcm_hw_params_set_format(dev->pcm_handle, dev->HWParams,
                                     dev->pcm_format);
  _ALSA_DEVICE_ERR_CHK((res >= 0), "ERROR: snd_pcm_hw_params_set_format: %s \n",
                   snd_strerror(res));

  // CHANNELS
  res = snd_pcm_hw_params_set_channels(dev->pcm_handle, dev->HWParams,
                                       dev->channels);
  _ALSA_DEVICE_ERR_CHK((res >= 0), "ERROR: snd_pcm_hw_params_set_channels: %s \n",
                   snd_strerror(res));

  // RATE
  unsigned int expectedRate = dev->rate;
  res = snd_pcm_hw_params_set_rate_near(dev->pcm_handle, dev->HWParams,
                                        &dev->rate, &dir);
  _ALSA_DEVICE_ERR_CHK((res >= 0 && expectedRate == dev->rate && dir == 0),
                   "ERROR: snd_pcm_hw_params_set_rate_near failed. Got rate "
                   "%i, asked for %i (dir=%i) \n",
                   dev->rate, expectedRate, dir);

  // APPLY HW SETTINGS
  res = snd_pcm_hw_params(dev->pcm_handle, dev->HWParams);
  _ALSA_DEVICE_ERR_CHK((res >= 0), "ERROR: snd_pcm_hw_params failed. %s \n",
                   snd_strerror(res));

  return 0;
}

int _alsa_device_init_sw_params(alsa_device_t *dev) {

  int res = 0;

  dev->whenToStop = dev->periodSize;
  dev->whenToLoad = dev->periodSize;

  // GET CONFIG SW PARAMS
  res = snd_pcm_sw_params_current(dev->pcm_handle, dev->SWParams);
  _ALSA_DEVICE_ERR_CHK((res >= 0), "ERROR: Failed get current SW params: %s \n",
                   snd_strerror(res));

// TIME STAMPING
#ifdef RPI
  res = snd_pcm_sw_params_set_tstamp_type(dev->pcm_handle, dev->SWParams,
                                          SND_PCM_TSTAMP_TYPE_MONOTONIC_RAW);
  _ALSA_DEVICE_ERR_CHK((res >= 0),
                   "ERROR: snd_pcm_sw_params_set_tstamp_type failed: %s \n",
                   snd_strerror(res));

#endif
  res = snd_pcm_sw_params_set_tstamp_mode(dev->pcm_handle, dev->SWParams,
                                          SND_PCM_TSTAMP_ENABLE);
  _ALSA_DEVICE_ERR_CHK((res >= 0),
                   "ERROR: snd_pcm_sw_params_set_tstamp_mode failed: %s \n",
                   snd_strerror(res));

  // APPLY SW SETTINGS
  res = snd_pcm_sw_params(dev->pcm_handle, dev->SWParams);
  _ALSA_DEVICE_ERR_CHK((res >= 0), "ERROR: nd_pcm_sw_params failed: %s \n",
                   snd_strerror(res));

  return 0;
}

int _alsa_device_init_poll(alsa_device_t *dev) {
  int res = 0;

  dev->pollFdsCnt = snd_pcm_poll_descriptors_count(dev->pcm_handle);
  _ALSA_DEVICE_ERR_CHK((dev->pollFdsCnt > 0),
                   "ERROR: snd_pcm_poll_descriptors_count \n");

  dev->pollFds = malloc(sizeof(struct pollfd) * dev->pollFdsCnt);
  _ALSA_DEVICE_ERR_CHK((dev->pollFds != NULL), "ERROR: malloc pollFds failed \n");

  res =
      snd_pcm_poll_descriptors(dev->pcm_handle, dev->pollFds, dev->pollFdsCnt);
  _ALSA_DEVICE_ERR_CHK((res >= 0), "ERROR: snd_pcm_poll_descriptors failed: %s \n",
                   snd_strerror(res));

  return 0;
}

/* !Brief  Blocking function until alsa device has data

    This is a classic poll with timeout.

  \return 1 if device wants attention. 0 if timeout. Neg. if error or null
  event.
*/
int alsa_device_poll(alsa_device_t *dev, int timeout) {
  int res;

  res = poll(dev->pollFds, dev->pollFdsCnt, timeout);
  _ALSA_DEVICE_ERR_CHK((res >= 0), "ERROR: poll failed: %i \n", res);

  if (res == 0)
    return 0; // Timeout

  unsigned short alsaRevents;
  snd_pcm_poll_descriptors_revents(dev->pcm_handle, dev->pollFds,
                                   dev->pollFdsCnt, &alsaRevents);
  _ALSA_DEVICE_ERR_CHK(((alsaRevents & POLLERR) == 0), "ERROR: POLLERR \n");

  if (alsaRevents & POLLIN)
    return 1;

  nimrum_err("ERROR: alsa_device_poll gave false alarm \n");
  return -1;
}

/* !Brief  Blocking function until alsa device has data

    This is a classic poll with timeout + read.

  \return number of bytes read. Neg. if error or 0 if timeout
*/
int alsa_device_wait_and_read(alsa_device_t *dev, int timeout, uint8_t *bufPtr,
                              int framesToRead) {
  int res = 0;

  res = alsa_device_poll(dev, timeout);
  if (res == 0) {
    nimrum_warn("alsa_device_poll timeout \n");
    return 0;
  }
  if (res != 1) {
    nimrum_err("ERROR: alsa_device_poll failed, got:%i. \n", res);
    return -1;
  }

  int numOfFrames = snd_pcm_readi(dev->pcm_handle, bufPtr, framesToRead);
  if (numOfFrames <= 0) {
    nimrum_err("ERROR: alsa_device read failed, (%i)%s \n", numOfFrames,
                       snd_strerror(numOfFrames));
    return -1;
  }

  return dev->frameSize * numOfFrames;
}

void alsa_device_print_state(alsa_device_t *dev) {
  unsigned int val1, val2, currU;

  nimrum_note("alsa_device: ");
  nimrum_note("%s, ", snd_pcm_name(dev->pcm_handle));

  if (0 > snd_pcm_hw_params_get_channels(dev->HWParams, &currU)) {
    nimrum_note("? Ch, ");
  } else {
    nimrum_note("%i Ch, ", currU);
  }

  nimrum_note("%i Bits, ", snd_pcm_hw_params_get_sbits(dev->HWParams));

  if (0 > snd_pcm_hw_params_get_rate_numden(dev->HWParams, &val1, &val2)) {
    nimrum_note("? Hz, ");
  } else {
    nimrum_note("%i/%i Hz, ", val1, val2);
  }

  nimrum_note("\n");
}

int alsa_device_get_first_hw(char *devName) {
  /* !Brief
    Will return device name of FIRST hw device found.
      Expect this to be 'Direct hardware device without any conversions'
      Also expects this not to be HDMI =)
    Outputs:
      devName:  A string suitable for alsa_device_init
    Returns:
      0 if found
      -1 if failed
  */

  char keyStr[] = "hw:";

  int res = 0;
  void **hints;

  res = snd_device_name_hint(-1, "pcm", &hints);
  if (res < 0) {
    nimrum_err("ERROR: snd_device_name_hint failed, %s\n",
                       snd_strerror(res));
    exit(EXIT_SUCCESS); // Success to get logs
  }

  res = -1;
  char *name = NULL;
  char *ioid = NULL;
  void **n = hints;
  while (*n != NULL) {
    name = snd_device_name_get_hint(*n, "NAME");
    ioid = snd_device_name_get_hint(*n, "IOID");

    if (ioid != NULL && strcmp(ioid, "Input") == 0)
      continue;

    if (name != NULL) {
      int found = 1;
      size_t keyCnt = 0;
      while (keyCnt < strlen(keyStr)) {
        if ((name[keyCnt] != keyStr[keyCnt])) {
          found = -1;
          break;
        }
        keyCnt++;
      }

      if (found > 0) {
        strcpy(devName, name);
        res = 0;
        break;
      }
    }

    n++;
  }
  free(name);
  free(ioid);
  snd_device_name_free_hint(hints);
  return res;
}
