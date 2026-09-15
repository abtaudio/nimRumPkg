/******************************************************************************
 * Copyright (C) 2025-2026 AbtAudio AB
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

#ifndef NIM_RUM_DSP_CONFIG_H
#define NIM_RUM_DSP_CONFIG_H

#include "nimRumDSP_biquad.h"

/**
 * Load EQ configuration from a YAML file into a filter bank.
 *
 * Expected format:
 *
 *   enabled: true
 *   sample_rate: 48000
 *   filters:
 *     - type: Peaking
 *       freq: 250.0
 *       gain: -4.2
 *       q: 2.1
 *     - type: Highshelf
 *       freq: 8000.0
 *       gain: -2.0
 *       q: 0.7
 *
 * @param fb          Filter bank to populate (must be initialized).
 * @param config_path Path to the YAML file.
 * @return 0 on success, -1 on error (file not found, parse error).
 */
int nimRumDSP_configLoad(nim_rum_dsp_filter_bank_t *fb,
                          const char *config_path);

#endif /* NIM_RUM_DSP_CONFIG_H */
