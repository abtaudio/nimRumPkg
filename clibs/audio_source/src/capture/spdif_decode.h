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

#include <unistd.h>

#include "nimrum_log.h"

#include "ffmpeg_hal.h"
#include "decode_buf_in.h"
#include "decode_buf_out.h"

typedef struct {

  decode_buf_out_t bOut;

  ffmpeg_hal_t ffmpeg;

  int tryPCM;
  int firstPCMDone;
  char layout_name[16];  // Detected layout from ffmpeg (e.g. "stereo", "5.1")

} spdif_decoder_t;

int spdif_decode_init(spdif_decoder_t *dec, size_t inBufSize,
                      size_t outBufChannels, size_t outBufDepth);

int spdif_decode_close(spdif_decoder_t *dec);

int spdif_decode_process(spdif_decoder_t *dec, int sampleRateAlsa,
                         int numOfChannelsAlsa, int bytesPerSampleAlsa,
                         int expectedNumOfSamples,
                         int (*wcb)(int32_t **bufPtr, int numOfSamples,
                                    int numOfChannels, int rate, float txPPM,
                                    uint64_t layout, void *userData),
                         void *userData);

void spdif_decode_is_pcm(spdif_decoder_t *dec, size_t headerSize, int *isPCM,
                         int *mute);
