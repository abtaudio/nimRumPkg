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

#include "capture.h"
#include "nimrum_log.h"

static capture_t g_cap;
static void *capture_run(void *actVoid);

static int _capture_init(capture_t *act, char *pcmInputName) {

  memset(act, 0, sizeof(capture_t));
  strncpy(act->pcmDevName, pcmInputName, CAPTURE_PCM_NAME_MAX_LEN - 1);
  act->run = 1;

  /* INIT Q */
  int qCh = 0;
  for (qCh = 0; qCh < CAPTURE_MAX_CHANNELS; qCh++) {
    nimrum_q_init(&act->q[qCh], CAPTURE_Q_LEN, CAPTURE_Q_WIDTH, act->qData[qCh]);
    act->q[qCh].noMutex = 1; // handle mutex on this level instead
  }

  // Initial values, used before any real data exists
  // ..to get TX started at once.
  act->numOfActiveChannels = CAPTURE_ALSA_CHANNELS;
  act->sampleRate = CAPTURE_ALSA_FRAMES_PER_MS * 1000;

  /* START THREAD */
  if (pthread_mutex_init(&act->QLockMtx, NULL) != 0) {
    nimrum_err("_capture_init, pthread_mutex_init failed \n");
    exit(EXIT_FAILURE);
  }

  pthread_create(&act->capThread, NULL, capture_run, act);

  return 0;
}

static int _capture_close(capture_t *act) {
  act->run = 0; // This is not protected by mutex, ..but closing down anyway

  pthread_cancel(act->capThread);
  pthread_join(act->capThread, NULL);

  pthread_mutex_unlock(&act->QLockMtx);
  pthread_mutex_destroy(&act->QLockMtx);

  /* CLOSE Q */
  int qCh = 0;
  for (qCh = 0; qCh < CAPTURE_MAX_CHANNELS; qCh++) {
    nimrum_q_close(&act->q[qCh]);
  }

  return 0;
}

static int _capture_get_fifo_count(capture_t *act) {
  pthread_mutex_lock(&act->QLockMtx);
  // Expects all channels to have same number of data
  int numOfFrames = nimrum_q_get_count(&act->q[0]);
  pthread_mutex_unlock(&act->QLockMtx);
  return numOfFrames;
}

static int _capture_get_data(capture_t *act, int32_t *dataOut[],
                            int numOfFrames, int *samplesQueued,
                            int *activeChannels, uint64_t *layout,
                            float *txPPM, int *dataValid) {

  int ch = 0;
  int numOfFramesAvail = _capture_get_fifo_count(act);
  *dataValid = 0;

  int framesToRetrieve = numOfFrames;
  if (framesToRetrieve > numOfFramesAvail) {
    *dataValid = -1;

    framesToRetrieve = numOfFramesAvail;

    /* Return 0 if not enough frames avalable */
    int bytesToReset = numOfFrames * sizeof(int32_t);
    for (ch = 0; ch < CAPTURE_MAX_CHANNELS; ch++) {
      memset(dataOut[ch], 0, bytesToReset);
    }

    if (act->qOutOfData == 0) {
      nimrum_note("_capture_get_data: Too few data START\n");
    }
    act->qOutOfData = 1;
  } else {
    if (act->qOutOfData == 1) {
      nimrum_note("_capture_get_data: Too few data STOP\n");
      *dataValid = 1;
    }
    act->qOutOfData = 0;
  }

  pthread_mutex_lock(&act->QLockMtx);

  /* GET DATA FROM Q */
  for (ch = 0; ch < CAPTURE_MAX_CHANNELS; ch++) {
    nimrum_q_peek_top_many(&act->q[ch], (unsigned char *)dataOut[ch], framesToRetrieve);
    nimrum_q_remove_top_many(&act->q[ch], framesToRetrieve);
  }

  *samplesQueued =
      numOfFramesAvail - framesToRetrieve; // This is just for investigation?
  *activeChannels = act->numOfActiveChannels;
  *layout = act->layout;
  *txPPM = act->txPPM;

  pthread_mutex_unlock(&act->QLockMtx);

  return 0;
}

static void *capture_run(void *actVoid) {
  capture_t *act = (capture_t *)actVoid;

  /* INIT DECODER */
  pthread_mutex_lock(&act->QLockMtx);
  if (spdif_decode_init(
          &act->decoder, CAPTURE_IN_BUF_SIZE, CAPTURE_MAX_CHANNELS,
          CAPTURE_OUT_BUF_SIZE) < 0) {
    pthread_mutex_unlock(&act->QLockMtx);
    return NULL;
  }
  pthread_mutex_unlock(&act->QLockMtx);

  while (act->run == 1) {

    int res = 0;

    /* INIT PCM DEV */
    if (strlen(act->pcmDevName) < 1) {
      alsa_device_get_first_hw(act->pcmDevName);
    }

    int pcmRate =
        CAPTURE_ALSA_FRAMES_PER_MS * 1000; // Any value since using SPDIF
    int pcmChannels =
        CAPTURE_ALSA_CHANNELS; // Alsa has no clue what it is receiving
    if (alsa_device_init(&act->alsa_dev, act->pcmDevName, pcmRate, pcmChannels) <
        0) {
      nimrum_err(
          "ERROR: capture_thread: alsa_device_init failed, PCM Dev: %s \n",
          act->pcmDevName);
      goto end;
    }
    act->devIsInitialized = 1;

    alsa_device_print_state(&act->alsa_dev);

    /* (RE)START ALSA */
    alsa_device_start(&act->alsa_dev);

    /* CLEAR Q */
    pthread_mutex_lock(&act->QLockMtx);
    int qCh = 0;
    for (qCh = 0; qCh < CAPTURE_MAX_CHANNELS; qCh++) {
      nimrum_q_remove_top_many(&act->q[qCh], nimrum_q_get_count(&act->q[qCh]));
    }
    pthread_mutex_unlock(&act->QLockMtx);

    /* read LARGE amount of data first time */
    int timeout = CAPTURE_READ_TIME_OPEN_MS + 100;
    int framesToRead = CAPTURE_READ_FRAMES_OPEN;

    /* CLEAR FFMPEG INPUT BUFFER */
    decode_buf_in_reset(&act->decoder.ffmpeg.bufIn);

    /* READ AND DECODE DATA, 'FOREVER' */
    while (1) {

      /* READ FROM DEVICE */
      res = alsa_device_wait_and_read(
          &act->alsa_dev, timeout,
          decode_buf_in_get_write_ptr(&act->decoder.ffmpeg.bufIn),
          framesToRead);
      if (res == 0) {
        break;
      }
      if (res < 0) {
        nimrum_err(
            "capture_thread: Read ALSA failed. ALSA State: %s\n",
            snd_pcm_state_name(snd_pcm_state(act->alsa_dev.pcm_handle)));
        break;
      }
      decode_buf_in_advance(&act->decoder.ffmpeg.bufIn, res);

      /* read NORMAL amount of data */
      timeout = CAPTURE_READ_TIME_MS + 1;
      framesToRead = CAPTURE_READ_FRAMES;

      /* DECODE - First time with more data for analysis */
      if (spdif_decode_process(&act->decoder, act->alsa_dev.rate,
                               act->alsa_dev.channels,
                               act->alsa_dev.bytesPerSample, framesToRead,
                               &capture_push_to_queue, (void *)act) != 0) {
        break;
      }

      // Seems to work
      act->longSleepTimeCnt = 0;
    }

  end:

    /* CLOSE PCM DEV */
    if (act->devIsInitialized == 1) {
      alsa_device_close(&act->alsa_dev);
      act->devIsInitialized = 0;
    }

    /* SLEEP UNTIL NEXT */
    if (act->longSleepTimeCnt < 10) {
      act->longSleepTimeCnt++;
    }

    struct timespec timeSleep;
    if (act->longSleepTimeCnt >= 10) {
      timeSleep.tv_sec = 2;
      timeSleep.tv_nsec = 0;

    } else {
      timeSleep.tv_sec = 0;
      timeSleep.tv_nsec = 1000000;
    }

    clock_nanosleep(CLOCK_MONOTONIC, 0, &timeSleep, NULL);

  } // While (act->run == 1)

  /* CLOSE DECODER */
  spdif_decode_close(&act->decoder);

  return NULL;
}

int capture_push_to_queue(int32_t **bufPtr, int numOfSamples,
                                 int numOfChannels, int rate, float txPPM,
                                 uint64_t layout, void *userData) {

  capture_t *act = (capture_t *)userData;

  int ch = 0;
  int fifoCnt = _capture_get_fifo_count(act);

  pthread_mutex_lock(&act->QLockMtx);

  /* CHECK SAMPLE PARAMETERS */
  if (numOfChannels > CAPTURE_MAX_CHANNELS) {
    nimrum_err("capture_push_to_queue, too many channels. Got %i, "
                       "currently only prepared for %i \n",
                       numOfChannels, CAPTURE_MAX_CHANNELS);

    pthread_mutex_unlock(&act->QLockMtx);
    return -1;
  }

  if ((numOfChannels != act->numOfActiveChannels) || (layout != act->layout)) {
    act->numOfActiveChannels = numOfChannels;
    act->layout = layout;
  }

  // Always update layout_name from decoder (may change without channel count change)
  if (act->decoder.layout_name[0] != '\0' &&
      memcmp(act->layout_name, act->decoder.layout_name, sizeof(act->layout_name)) != 0) {
    memcpy(act->layout_name, act->decoder.layout_name, sizeof(act->layout_name));
  } else if (act->decoder.layout_name[0] == '\0' && act->layout_name[0] != '\0') {
    // Decoder cleared layout (switched to PCM mode)
    act->layout_name[0] = '\0';
  }

  if (rate != act->sampleRate) {
    act->sampleRate = rate;
  }

  act->txPPM = txPPM;

  /* KEEP Q STORAGE BELOW A MAX VALUE
      This to keep this q as short as possible by not allowing storage of too
      old values
  */
  act->fillRate = numOfSamples + fifoCnt;
  int maxFillRate = CAPTURE_Q_MAX_LATENCY;
  int samplesToRemove = act->fillRate - maxFillRate;

  if (samplesToRemove > fifoCnt)
    samplesToRemove = fifoCnt;

  if (samplesToRemove > 0) {
    for (ch = 0; ch < CAPTURE_MAX_CHANNELS; ch++) {
      nimrum_q_remove_top_many(&act->q[ch], samplesToRemove);
    }
  }

  /* PUSH */
  for (ch = 0; ch < CAPTURE_MAX_CHANNELS; ch++) {
    if (ch < numOfChannels) {
      unsigned char *chPtr = (unsigned char *)bufPtr[ch];
      nimrum_q_push_many(&act->q[ch], chPtr, numOfSamples);
    } else {
      int32_t zeros[numOfSamples];
      memset(zeros, 0, numOfSamples * sizeof(int32_t));
      nimrum_q_push_many(&act->q[ch], (unsigned char *)zeros, numOfSamples);
    }
  }

  pthread_mutex_unlock(&act->QLockMtx);

  return numOfSamples; // Neg if failure
}

// Public API (uses static instance)
int capture_init(int frame_stretch_enable, char *pcm_input_name) {
    (void)frame_stretch_enable;
    return _capture_init(&g_cap, pcm_input_name);
}

int capture_get_data(int32_t *data_out[], int num_of_frames, int *samples_queued,
                     int *active_channels, uint64_t *layout, float *tx_ppm,
                     int *data_valid) {
    return _capture_get_data(&g_cap, data_out, num_of_frames,
                                     samples_queued, active_channels, layout,
                                     tx_ppm, data_valid);
}

int capture_get_fifo_count(void) {
    return _capture_get_fifo_count(&g_cap);
}

const char *capture_get_layout_name(void) {
    if (g_cap.layout_name[0] == '\0') return NULL;
    return g_cap.layout_name;
}

int capture_close(void) {
    return _capture_close(&g_cap);
}
