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

#include "decode_buf_in.h"

int decode_buf_in_init(decode_buf_in_t *b, int bufSize) {

  memset(b, 0, sizeof(decode_buf_in_t));

  b->bufSize = bufSize;
  b->dataStart = (uint8_t *)av_mallocz(bufSize);
  b->data = b->dataStart;

  if (b->data == NULL) {
    printf("decode_buf_in: ERROR, failed malloc \n");
    return -1;
  }

  return 0;
}

int decode_buf_in_close(decode_buf_in_t *b) {
  av_freep(&b->dataStart);
  b->dataStart = NULL;
  b->data = b->dataStart;
  b->numOfBytes = 0;
  return 0;
}

// Only init if been closed, and never trust ffmpeg
int decode_buf_in_reset(decode_buf_in_t *b) {

  if ((b->dataStart == NULL) || (b->data == NULL)) {
    int bufSize = b->bufSize;
    return decode_buf_in_init(b, bufSize);
  }
  return 0;
}

int decode_buf_in_read(void *opaque, uint8_t *buf, int buf_size) {

  decode_buf_in_t *dinBuf = (decode_buf_in_t *)opaque;

  buf_size = FFMIN(buf_size, dinBuf->numOfBytes);

  if (!buf_size)
    return AVERROR_EOF;

  memcpy(buf, dinBuf->data, buf_size);
  dinBuf->data += buf_size;
  dinBuf->numOfBytes -= buf_size;

  return buf_size;
}

uint8_t *decode_buf_in_get_write_ptr(decode_buf_in_t *b) {
  return b->data + b->numOfBytes;
}

uint8_t *decode_buf_in_get_start_ptr(decode_buf_in_t *b) {
  return b->dataStart;
}

int decode_buf_in_get_used(decode_buf_in_t *b) {
  return b->numOfBytes;
}

int decode_buf_in_get_size(decode_buf_in_t *b) { return b->bufSize; }

void decode_buf_in_advance(decode_buf_in_t *b, int incr) {
  b->numOfBytes += incr;
  if (((int)(b->data - b->dataStart) + b->numOfBytes) >= b->bufSize) {
    printf("decode_buf_in: ERROR, incr too much \n");
    decode_buf_in_clear(b);
  }
}

void decode_buf_in_retreat(decode_buf_in_t *b, int decr) {
  if (decr <= b->numOfBytes) {
    b->data += decr;
    b->numOfBytes -= decr;
  } else {
    printf("decode_buf_in: ERROR, decr too much \n");
    decode_buf_in_clear(b);
  }
}

void decode_buf_in_clear(decode_buf_in_t *b) {
  b->data = b->dataStart;
  b->numOfBytes = 0;
}

void decode_buf_in_compact(decode_buf_in_t *b) {
  // return data pointer to start
  memmove(b->dataStart, b->data, b->numOfBytes);
  b->data = b->dataStart;
}
