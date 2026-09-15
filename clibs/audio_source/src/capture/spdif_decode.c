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

#include "spdif_decode.h"

int spdif_decode_init(spdif_decoder_t *dec, size_t inBufSize,
                      size_t outBufChannels, size_t outBufDepth) {

  memset(dec, 0, sizeof(spdif_decoder_t));

  decode_buf_out_init(&dec->bOut, outBufChannels, outBufDepth);

  int res = ffmpeg_hal_init(&dec->ffmpeg, inBufSize, &dec->bOut);

  return res;
}

int spdif_decode_close(spdif_decoder_t *dec) {

  _ffmpeg_close_stream(&dec->ffmpeg);

  decode_buf_out_close(&dec->bOut);

  return 0;
}

int spdif_decode_process(spdif_decoder_t *dec, int sampleRateAlsa,
                         int numOfChannelsAlsa, int bytesPerSampleAlsa,
                         int expectedNumOfSamples,
                         int (*wcb)(int32_t **bufPtr, int numOfSamples,
                                    int numOfChannels, int rate, float txPPM,
                                    uint64_t layout, void *userData),
                         void *userData) {

  int res = 0;
  int channels = 0;
  uint64_t layout = 0;
  int rate = 0;
  int consumedNumOfFrames = 0;

  if (dec->tryPCM > 0) {
    /*
    UNCODED -> DIRECT TRANSFER FROM ALSA
    */

    int mute = 0;
    spdif_decode_is_pcm(dec, 0, &dec->tryPCM, &mute);
    if (dec->tryPCM < 1) {
      nimrum_note("spdif_decode_process: Detected NON-PCM \n");
      goto end_error;
    }

    if (dec->firstPCMDone == 0) {
      dec->firstPCMDone = 1;
      nimrum_note("spdif_decode_process: PCM decoding, Rate:%iHz "
                          "Channels:%i Sample size:%i bits\n",
                          sampleRateAlsa, numOfChannelsAlsa,
                          8 * bytesPerSampleAlsa);
    }

    if (bytesPerSampleAlsa != 2) {
      nimrum_err(
          "spdif_decode_process: Only 16 bit conversion implemented for PCM data \n");
      goto end_error;
    }

    int framesSize = bytesPerSampleAlsa * numOfChannelsAlsa;

    int avail = decode_buf_in_get_used(&dec->ffmpeg.bufIn);
    int numOfSamples = (int)floor((float)avail / (float)framesSize);
    if (avail != (numOfSamples * framesSize)) {
      nimrum_err(
          "spdif_decode_process: I suggest to read a more even number of samples \n");
      goto end_error;
    }

    int ch = 0;
    int inCnt = 0;
    int32_t fIn[numOfChannelsAlsa];

    while (inCnt < numOfSamples) {
      for (ch = 0; ch < numOfChannelsAlsa; ch++) {
        if (mute == 0) {

          size_t pos = framesSize * inCnt + bytesPerSampleAlsa * ch;
          uint8_t *bPtr = decode_buf_in_get_start_ptr(&dec->ffmpeg.bufIn);

          int32_t sI = (((int32_t)((int8_t)bPtr[pos + 1]) << 8) |
                        ((uint32_t)((uint8_t)bPtr[pos + 0]) << 0));

          fIn[ch] = sI;
        } else {
          fIn[ch] = 0;
        }
      }

      inCnt +=
          decode_buf_out_add_frame(&dec->bOut, numOfChannelsAlsa, fIn);
    }

    decode_buf_in_clear(&dec->ffmpeg.bufIn);

    channels = numOfChannelsAlsa;
    layout = AV_CH_LAYOUT_STEREO;
    rate = sampleRateAlsa;
    consumedNumOfFrames = inCnt;
    // Clear ffmpeg layout name — PCM mode doesn't use ffmpeg
    dec->layout_name[0] = '\0';

  } else {

    /*
    CODED -> USE FFMPEG
    */
    consumedNumOfFrames = ffmpeg_hal_read(&dec->ffmpeg, &channels, &layout, &rate);
    if (consumedNumOfFrames < 0) {
      dec->tryPCM = 1;
      dec->firstPCMDone = 0;
      goto end_error;
    }
    if (consumedNumOfFrames == 0) {
      goto end; // Opened stream, probably
    }
    // Copy detected layout name for upstream (e.g. "stereo", "5.1")
    memcpy(dec->layout_name, dec->ffmpeg.layout_name, sizeof(dec->layout_name));
  }

  float txPPM = decode_buf_out_update_ppm(
      &dec->bOut, consumedNumOfFrames, rate, expectedNumOfSamples);

  if (wcb(dec->bOut.outputBuffer, dec->bOut.outputBufferCnt, channels, rate,
          txPPM, layout, userData) < 0) {
    nimrum_err("spdif_decode_process: User writeCallBack failed \n");
    goto end_error;
  }

  goto end;

end_error:
  decode_buf_in_clear(&dec->ffmpeg.bufIn);
  decode_buf_out_restart_ppm(&dec->bOut);
  res = -1;

end:
  // Prepare for next load of data
  decode_buf_in_compact(&dec->ffmpeg.bufIn);
  dec->bOut.outputBufferCnt = 0;

  if (decode_buf_in_get_used(&dec->ffmpeg.bufIn) >
      decode_buf_in_get_size(&dec->ffmpeg.bufIn) / 2) {
    nimrum_warn(
        "spdif_decode_process: ffmpeg has a fishy amount of data left in bufIn \n");
  }
  if (decode_buf_in_get_used(&dec->ffmpeg.bufIn) > 0) {
    nimrum_warn("spdif_decode_process: ffmpeg bufIn is not 0 \n");
  }

  return res;
}

void spdif_decode_is_pcm(spdif_decoder_t *dec, size_t headerSize, int *isPCM,
                         int *mute) {

  int64_t totalAbsDiff = 0;
  int64_t totalSum = 0;

  int sampleSize = 2;
  int channels = 2;
  int frameSize = sampleSize * channels;

  int startPos = headerSize;
  int stopPos = decode_buf_in_get_used(&dec->ffmpeg.bufIn);
  int framesToRead = (stopPos - startPos) / frameSize;

  *mute = 0;

  if (framesToRead < 1000) {
    nimrum_warn("spdif_decode_is_pcm: Too few values to make a decision. "
                        "(Have only %i, need at least 1000?) \n",
                        framesToRead);
    *isPCM = 0;
    *mute = 1;
    return;
  }

  int32_t sI_prev = 0;

  int frameCnt = 0;
  int diffCnt = 1; // To avoid div 0
  for (frameCnt = 0; frameCnt < framesToRead; frameCnt++) {

    // Only channel 0 used
    size_t pos = startPos + (frameSize * frameCnt);
    uint8_t *bPtr = decode_buf_in_get_start_ptr(&dec->ffmpeg.bufIn);

    int32_t sI = (((int32_t)((int8_t)bPtr[pos + 1]) << 8) |
                  ((uint32_t)((uint8_t)bPtr[pos + 0]) << 0));

    if (frameCnt == 0)
      sI_prev = sI;

    int64_t absDiff = (int64_t)llabs((int64_t)(sI_prev - sI));

    if ((abs(sI_prev) > 2) && (abs(sI) > 2)) { // Ignoring 'silence'
      totalAbsDiff += absDiff;
      diffCnt++;
    }

    totalSum += (int64_t)(sI);
    sI_prev = sI;
  }

  int absDiffAvarage = (int)(totalAbsDiff / (int64_t)diffCnt);
  int valueAvarage = (int)(totalSum / (int64_t)framesToRead);

  int absDiffAvarageLimit = 12000;
  int isPCMNew = 0;
  if ((absDiffAvarage < absDiffAvarageLimit)) {
    isPCMNew = 1;
  }

  // debug
  if (*isPCM != isPCMNew) {
    nimrum_note(
        "spdif_decode_is_pcm: \n"
        " totalAbsDiff / diffCnt = absDiffAvarage: %i/%i=%i >= %i !\n"
        " totalSum / framesToRead = valueAvarage: %i/%i=%i \n",
        (int)totalAbsDiff, diffCnt, absDiffAvarage, absDiffAvarageLimit,
        (int)totalSum, framesToRead, valueAvarage);
  }

  *isPCM = isPCMNew;

  return;
}
