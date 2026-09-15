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

#include <string.h>

#include "nimrum_log.h"
#include <libavutil/error.h>
#include <libavutil/macros.h>
#include <libavutil/mem.h>

typedef struct {
  int bufSize;

  uint8_t *data;
  uint8_t *dataStart;
  int numOfBytes; ///< size left in the buffer

} decode_buf_in_t;

int decode_buf_in_init(decode_buf_in_t *b, int bufSize);

int decode_buf_in_close(decode_buf_in_t *b);

int decode_buf_in_reset(decode_buf_in_t *b);

int decode_buf_in_read(void *opaque, uint8_t *buf, int buf_size);

uint8_t *decode_buf_in_get_write_ptr(decode_buf_in_t *b);

uint8_t *decode_buf_in_get_start_ptr(decode_buf_in_t *b);

int decode_buf_in_get_used(decode_buf_in_t *b);

int decode_buf_in_get_size(decode_buf_in_t *b);

void decode_buf_in_advance(decode_buf_in_t *b, int incr);

void decode_buf_in_retreat(decode_buf_in_t *b, int decr);

void decode_buf_in_clear(decode_buf_in_t *b);

void decode_buf_in_compact(decode_buf_in_t *b);
