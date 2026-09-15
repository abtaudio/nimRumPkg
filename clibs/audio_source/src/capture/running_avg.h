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

#include <inttypes.h>

typedef struct {
    int size;
    int num_values;
    float mean;
    float variance;
} running_avg_t;

// Initialize filter with window size
void running_avg_reset(running_avg_t *f, int size);

// Reset state but keep window size
void running_avg_restart(running_avg_t *f);

// Add a value to the filter
void running_avg_add(running_avg_t *f, float val);

// Get current mean
float running_avg_get_mean(running_avg_t *f);

// Get number of values added (capped at window size)
int running_avg_get_count(running_avg_t *f);
