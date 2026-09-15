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

#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <pthread.h>
#include <signal.h>

#include "alsa_device.h"
#include "spdif_decode.h"
#include "nimrum_queue.h"
#include "decode_buf_in.h"

#define CAPTURE_MAX_CHANNELS         8
#define CAPTURE_ALSA_FRAMES_PER_MS   48
#define CAPTURE_ALSA_CHANNELS        2
#define CAPTURE_READ_TIME_MS         32   // [ms]
#define CAPTURE_READ_TIME_OPEN_MS    128  // [ms]
#define CAPTURE_PCM_NAME_MAX_LEN     32

#define CAPTURE_READ_FRAMES \
    (CAPTURE_ALSA_FRAMES_PER_MS * CAPTURE_READ_TIME_MS)

#define CAPTURE_READ_FRAMES_OPEN \
    (CAPTURE_ALSA_FRAMES_PER_MS * CAPTURE_READ_TIME_OPEN_MS)

#define CAPTURE_IN_BUF_SIZE \
    (CAPTURE_READ_FRAMES_OPEN * sizeof(int32_t) * CAPTURE_MAX_CHANNELS)

#define CAPTURE_OUT_BUF_SIZE \
    (CAPTURE_READ_FRAMES_OPEN * 2)

#define CAPTURE_Q_MAX_LATENCY \
    (2 * CAPTURE_READ_TIME_MS * CAPTURE_ALSA_FRAMES_PER_MS)

#define CAPTURE_Q_LEN   (4 * CAPTURE_Q_MAX_LATENCY)
#define CAPTURE_Q_WIDTH (sizeof(int32_t))

typedef struct {
    int run;
    pthread_t capThread;
    pthread_mutex_t QLockMtx;
    int devIsInitialized;
    alsa_device_t alsa_dev;
    spdif_decoder_t decoder;
    size_t inBufSize;
    nimrum_queue_t q[CAPTURE_MAX_CHANNELS];
    int qOutOfData;
    int sampleRate;
    int numOfActiveChannels;
    uint64_t layout;
    char layout_name[16];  // Detected layout name (from ffmpeg, e.g. "5.1")
    int longSleepTimeCnt;
    int fillRate;
    unsigned char qData[CAPTURE_MAX_CHANNELS][CAPTURE_Q_LEN * CAPTURE_Q_WIDTH];
    float txPPM;
    char pcmDevName[CAPTURE_PCM_NAME_MAX_LEN];
} capture_t;

// Public API
int capture_init(int frame_stretch_enable, char *pcm_input_name);
int capture_get_data(int32_t *data_out[], int num_of_frames, int *samples_queued,
                     int *active_channels, uint64_t *layout, float *tx_ppm,
                     int *data_valid);
int capture_get_fifo_count(void);
const char *capture_get_layout_name(void);
int capture_close(void);

// Used by spdif_decode as callback
int capture_push_to_queue(int32_t **bufPtr, int numOfSamples,
                          int numOfChannels, int rate, float txPPM,
                          uint64_t layout, void *userData);
