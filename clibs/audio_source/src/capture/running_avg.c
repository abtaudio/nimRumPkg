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

#include "running_avg.h"

void running_avg_reset(running_avg_t *f, int size) {
    f->size = size;
    running_avg_restart(f);
}

void running_avg_restart(running_avg_t *f) {
    f->num_values = 0;
    f->mean = 0.0f;
    f->variance = 0.0f;
}

void running_avg_add(running_avg_t *f, float val) {
    if (f->num_values < f->size) {
        f->num_values++;
    }

    int i = f->num_values;
    f->mean += (val - f->mean) / i;

    if (i > 1) {
        float diff = val - f->mean;
        f->variance = (i - 1) * f->variance / i + diff * diff / (i - 1);
    }
}

float running_avg_get_mean(running_avg_t *f) {
    return f->mean;
}

int running_avg_get_count(running_avg_t *f) {
    return f->num_values;
}
