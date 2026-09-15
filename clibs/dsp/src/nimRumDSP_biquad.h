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

#ifndef NIM_RUM_DSP_BIQUAD_H
#define NIM_RUM_DSP_BIQUAD_H

#include <stdint.h>

#define NIM_RUM_DSP_MAX_BANDS    32
#define NIM_RUM_DSP_MAX_CHANNELS 8

// Filter types (matching CamillaDSP naming)
typedef enum {
    NIM_RUM_DSP_FILTER_PEAKING = 0,
    NIM_RUM_DSP_FILTER_LOWSHELF,
    NIM_RUM_DSP_FILTER_HIGHSHELF,
    NIM_RUM_DSP_FILTER_LOWPASS,
    NIM_RUM_DSP_FILTER_HIGHPASS,
    NIM_RUM_DSP_FILTER_RAW,  // Direct biquad coefficients (b0/b1/b2/a1/a2)
} nim_rum_dsp_filter_type_t;

// Single biquad filter state (per channel)
typedef struct {
    double b0, b1, b2;  // Numerator coefficients (normalized)
    double a1, a2;      // Denominator coefficients (normalized, a0=1)
    double z1, z2;      // Filter state (direct form II transposed)
} nim_rum_dsp_biquad_t;

// A filter band (one parametric EQ band, applied to all channels)
typedef struct {
    nim_rum_dsp_filter_type_t type;
    double freq;    // Center/corner frequency in Hz
    double gain;    // Gain in dB
    double q;       // Q factor
    // Per-channel biquad state
    nim_rum_dsp_biquad_t biquad[NIM_RUM_DSP_MAX_CHANNELS];
} nim_rum_dsp_band_t;

// Full filter bank
typedef struct {
    nim_rum_dsp_band_t bands[NIM_RUM_DSP_MAX_BANDS];
    int num_bands;
    int num_channels;
    int sample_rate;
    int enabled;
    double pre_gain;  // Linear gain applied before filter cascade (prevents clipping)
} nim_rum_dsp_filter_bank_t;

/**
 * Initialize a filter bank.
 */
void nimRumDSP_biquadInit(nim_rum_dsp_filter_bank_t *fb, int sample_rate,
                           int num_channels);

/**
 * Add a filter band. Computes biquad coefficients from parametric EQ parameters.
 *
 * @return 0 on success, -1 if max bands reached.
 */
int nimRumDSP_biquadAddBand(nim_rum_dsp_filter_bank_t *fb,
                             nim_rum_dsp_filter_type_t type,
                             double freq, double gain, double q);

/**
 * Add a filter band with raw biquad coefficients (pre-normalized, a0=1).
 *
 * Use this to import coefficients from external tools (REW, EQ APO, etc.).
 *
 * @return 0 on success, -1 if max bands reached.
 */
int nimRumDSP_biquadAddRawBand(nim_rum_dsp_filter_bank_t *fb,
                                double b0, double b1, double b2,
                                double a1, double a2);

/**
 * Clear all bands (reset to passthrough).
 */
void nimRumDSP_biquadClear(nim_rum_dsp_filter_bank_t *fb);

/**
 * Process audio through the filter bank (in-place).
 *
 * @param fb         The filter bank.
 * @param data       Array of channel pointers (int32_t samples).
 * @param num_frames Number of frames to process.
 */
void nimRumDSP_biquadProcess(nim_rum_dsp_filter_bank_t *fb,
                              int32_t *data[], int num_frames);

#endif /* NIM_RUM_DSP_BIQUAD_H */
