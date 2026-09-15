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

#include "running_avg.h"
#include "nimrum_log.h"

typedef struct {

  size_t outputMaxChannels;
  size_t outputMaxSamples;
  int32_t **outputBuffer;
  size_t outputBufferCnt;

  uint64_t timeLast;
  int ppmFiltInitCnt;

  running_avg_t ppmFilt;

  float ppmErr;

  int printCnt;

} decode_buf_out_t;

int decode_buf_out_init(decode_buf_out_t *b, size_t maxChannels,
                        size_t maxSamples);

void decode_buf_out_restart_ppm(decode_buf_out_t *b);

int decode_buf_out_close(decode_buf_out_t *b);

int decode_buf_out_add_frame(decode_buf_out_t *b, int channels,
                             int32_t fIn[]);

float decode_buf_out_update_ppm(decode_buf_out_t *b, int numOfSamples,
                                int rate, int expectedNumOfSamples);
