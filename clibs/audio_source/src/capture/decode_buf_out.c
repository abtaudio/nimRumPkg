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

#include "decode_buf_out.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

int decode_buf_out_init(decode_buf_out_t *b, size_t maxChannels,
                        size_t maxSamples) {

  memset(b, 0, sizeof(decode_buf_out_t));

  b->outputMaxChannels = maxChannels;
  b->outputMaxSamples = maxSamples;

  b->outputBuffer =
      (int32_t **)malloc(b->outputMaxChannels * sizeof(int32_t *));

  size_t ch;
  for (ch = 0; ch < b->outputMaxChannels; ch++) {
    b->outputBuffer[ch] =
        (int32_t *)malloc(b->outputMaxSamples * sizeof(int32_t));
    if (b->outputBuffer[ch] == NULL)
      printf("decode_buf_out: Failed malloc \n");
  }

  decode_buf_out_restart_ppm(b);

  return 0;
}

void decode_buf_out_restart_ppm(decode_buf_out_t *b) {
  running_avg_reset(&b->ppmFilt, 10000);
  b->timeLast = 0;
}

int decode_buf_out_close(decode_buf_out_t *b) {
  size_t ch;
  for (ch = 0; ch < b->outputMaxChannels; ch++) {
    free(b->outputBuffer[ch]);
  }
  free(b->outputBuffer);
  return 0;
}

int decode_buf_out_add_frame(decode_buf_out_t *b, int channels,
                             int32_t fIn[]) {

  if ((size_t)channels > b->outputMaxChannels) {
    nimrum_err(
        "decode_buf_out: channels out of hardcoded limit: %i > %i \n",
        channels, (int)b->outputMaxChannels);
    channels = b->outputMaxChannels;
  }

  size_t ch;
  for (ch = 0; ch < (size_t)channels; ch++) {
    b->outputBuffer[ch][b->outputBufferCnt] = fIn[ch];
  }
  b->outputBufferCnt++;

  if (b->outputBufferCnt >= b->outputMaxSamples) {
    nimrum_err("decode_buf_out: outputBufferCnt overflow: %zu >= %zu\n",
                       b->outputBufferCnt, b->outputMaxSamples);
    return 1;
  }

  return 1;
}

// Estimate PPM drift from ALSA buffer arrival timing
float decode_buf_out_update_ppm(decode_buf_out_t *b, int numOfSamples,
                                int rate, int expectedNumOfSamples) {

  if (numOfSamples != expectedNumOfSamples) {
    b->timeLast = 0;
    return 0;
  }

  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
  uint64_t now = (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
  float timeDiff = (float)(now - b->timeLast);

  float sampleTimeExpected = (float)1000000000 / (float)rate;
  float sampleTimeMeas = timeDiff / (float)numOfSamples;
  float sampleTimeDiffRaw = (sampleTimeMeas - sampleTimeExpected);
  float sampleTimeDiff = sampleTimeDiffRaw * 1000000.0f;

  if (b->timeLast == 0) {
    b->timeLast = now;
    return 0;
  }
  b->timeLast = now;

  running_avg_add(&b->ppmFilt, sampleTimeDiff);

  if (b->ppmFiltInitCnt < 5) {
    b->ppmFiltInitCnt++;
    running_avg_restart(&b->ppmFilt);
  }

  float sampleTimeDiffFilt = 0;
  if (running_avg_get_count(&b->ppmFilt) > 500) {
    sampleTimeDiffFilt = running_avg_get_mean(&b->ppmFilt);
  }

  if (sampleTimeDiffFilt != 0) {
    b->ppmErr = sampleTimeDiffFilt / sampleTimeExpected;

    if (fabs(b->ppmErr) > 500.0f) {
      printf("decode_buf_out: ppm estimate >500, restarting\n");
      decode_buf_out_restart_ppm(b);
    }
  }

  return b->ppmErr;
}
