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

#include <math.h>
#include <string.h>

#include "nimRumDSP_biquad.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// Compute biquad coefficients from parametric EQ parameters (Audio EQ Cookbook)
static void compute_coefficients(nim_rum_dsp_biquad_t *bq,
                                  nim_rum_dsp_filter_type_t type,
                                  double freq, double gain, double q,
                                  int sample_rate) {
    double w0 = 2.0 * M_PI * freq / (double)sample_rate;
    double cos_w0 = cos(w0);
    double sin_w0 = sin(w0);
    double alpha = sin_w0 / (2.0 * q);
    double A = pow(10.0, gain / 40.0);  // sqrt of linear gain

    double b0, b1, b2, a0, a1, a2;

    switch (type) {
        case NIM_RUM_DSP_FILTER_PEAKING:
            b0 = 1.0 + alpha * A;
            b1 = -2.0 * cos_w0;
            b2 = 1.0 - alpha * A;
            a0 = 1.0 + alpha / A;
            a1 = -2.0 * cos_w0;
            a2 = 1.0 - alpha / A;
            break;

        case NIM_RUM_DSP_FILTER_LOWSHELF: {
            double two_sqrt_a_alpha = 2.0 * sqrt(A) * alpha;
            b0 = A * ((A + 1.0) - (A - 1.0) * cos_w0 + two_sqrt_a_alpha);
            b1 = 2.0 * A * ((A - 1.0) - (A + 1.0) * cos_w0);
            b2 = A * ((A + 1.0) - (A - 1.0) * cos_w0 - two_sqrt_a_alpha);
            a0 = (A + 1.0) + (A - 1.0) * cos_w0 + two_sqrt_a_alpha;
            a1 = -2.0 * ((A - 1.0) + (A + 1.0) * cos_w0);
            a2 = (A + 1.0) + (A - 1.0) * cos_w0 - two_sqrt_a_alpha;
            break;
        }

        case NIM_RUM_DSP_FILTER_HIGHSHELF: {
            double two_sqrt_a_alpha = 2.0 * sqrt(A) * alpha;
            b0 = A * ((A + 1.0) + (A - 1.0) * cos_w0 + two_sqrt_a_alpha);
            b1 = -2.0 * A * ((A - 1.0) + (A + 1.0) * cos_w0);
            b2 = A * ((A + 1.0) + (A - 1.0) * cos_w0 - two_sqrt_a_alpha);
            a0 = (A + 1.0) - (A - 1.0) * cos_w0 + two_sqrt_a_alpha;
            a1 = 2.0 * ((A - 1.0) - (A + 1.0) * cos_w0);
            a2 = (A + 1.0) - (A - 1.0) * cos_w0 - two_sqrt_a_alpha;
            break;
        }

        case NIM_RUM_DSP_FILTER_LOWPASS:
            b0 = (1.0 - cos_w0) / 2.0;
            b1 = 1.0 - cos_w0;
            b2 = (1.0 - cos_w0) / 2.0;
            a0 = 1.0 + alpha;
            a1 = -2.0 * cos_w0;
            a2 = 1.0 - alpha;
            break;

        case NIM_RUM_DSP_FILTER_HIGHPASS:
            b0 = (1.0 + cos_w0) / 2.0;
            b1 = -(1.0 + cos_w0);
            b2 = (1.0 + cos_w0) / 2.0;
            a0 = 1.0 + alpha;
            a1 = -2.0 * cos_w0;
            a2 = 1.0 - alpha;
            break;

        default:
            // Passthrough
            bq->b0 = 1.0;
            bq->b1 = 0.0;
            bq->b2 = 0.0;
            bq->a1 = 0.0;
            bq->a2 = 0.0;
            return;
    }

    // Normalize by a0
    bq->b0 = b0 / a0;
    bq->b1 = b1 / a0;
    bq->b2 = b2 / a0;
    bq->a1 = a1 / a0;
    bq->a2 = a2 / a0;
}

void nimRumDSP_biquadInit(nim_rum_dsp_filter_bank_t *fb, int sample_rate,
                           int num_channels) {
    memset(fb, 0, sizeof(*fb));
    fb->sample_rate = sample_rate;
    fb->num_channels = num_channels;
    fb->enabled = 1;
    fb->pre_gain = 1.0;
}

// Compute the maximum combined boost across frequencies and set pre_gain
// to compensate. Evaluates 200 log-spaced points from 20Hz to Nyquist.
static void update_pre_gain(nim_rum_dsp_filter_bank_t *fb) {
    if (fb->num_bands == 0) {
        fb->pre_gain = 1.0;
        return;
    }

    double max_gain_db = 0.0;
    double nyquist = (double)fb->sample_rate / 2.0;
    int num_points = 200;
    int i, b;

    for (i = 0; i < num_points; i++) {
        // Log-spaced frequency from 20 Hz to Nyquist
        double freq = 20.0 * pow(nyquist / 20.0, (double)i / (double)(num_points - 1));
        double w = 2.0 * M_PI * freq / (double)fb->sample_rate;
        double cos_w = cos(w);
        double sin_w = sin(w);

        // Evaluate combined magnitude of all bands at this frequency
        double combined_db = 0.0;
        for (b = 0; b < fb->num_bands; b++) {
            nim_rum_dsp_biquad_t *bq = &fb->bands[b].biquad[0];
            // H(z) = (b0 + b1*z^-1 + b2*z^-2) / (1 + a1*z^-1 + a2*z^-2)
            // At z = e^(jw): z^-1 = cos(w) - j*sin(w)
            double num_re = bq->b0 + bq->b1 * cos_w + bq->b2 * (2.0 * cos_w * cos_w - 1.0);
            double num_im = -(bq->b1 * sin_w + bq->b2 * 2.0 * sin_w * cos_w);
            double den_re = 1.0 + bq->a1 * cos_w + bq->a2 * (2.0 * cos_w * cos_w - 1.0);
            double den_im = -(bq->a1 * sin_w + bq->a2 * 2.0 * sin_w * cos_w);

            double num_mag2 = num_re * num_re + num_im * num_im;
            double den_mag2 = den_re * den_re + den_im * den_im;

            if (den_mag2 > 1e-30) {
                combined_db += 10.0 * log10(num_mag2 / den_mag2);
            }
        }

        if (combined_db > max_gain_db) {
            max_gain_db = combined_db;
        }
    }

    // Only apply pre-gain if there's net boost somewhere
    if (max_gain_db > 0.5) {
        // Add 1 dB safety margin
        fb->pre_gain = pow(10.0, -(max_gain_db + 1.0) / 20.0);
    } else {
        fb->pre_gain = 1.0;
    }
}

int nimRumDSP_biquadAddBand(nim_rum_dsp_filter_bank_t *fb,
                             nim_rum_dsp_filter_type_t type,
                             double freq, double gain, double q) {
    if (fb->num_bands >= NIM_RUM_DSP_MAX_BANDS) {
        return -1;
    }

    nim_rum_dsp_band_t *band = &fb->bands[fb->num_bands];
    band->type = type;
    band->freq = freq;
    band->gain = gain;
    band->q = q;

    // Compute coefficients and clear state for each channel
    int ch;
    for (ch = 0; ch < fb->num_channels; ch++) {
        compute_coefficients(&band->biquad[ch], type, freq, gain, q,
                             fb->sample_rate);
        band->biquad[ch].z1 = 0.0;
        band->biquad[ch].z2 = 0.0;
    }

    fb->num_bands++;
    update_pre_gain(fb);
    return 0;
}

int nimRumDSP_biquadAddRawBand(nim_rum_dsp_filter_bank_t *fb,
                                double b0, double b1, double b2,
                                double a1, double a2) {
    if (fb->num_bands >= NIM_RUM_DSP_MAX_BANDS) {
        return -1;
    }

    nim_rum_dsp_band_t *band = &fb->bands[fb->num_bands];
    band->type = NIM_RUM_DSP_FILTER_RAW;
    band->freq = 0.0;
    band->gain = 0.0;
    band->q = 0.0;

    // Set coefficients directly for each channel (same coefficients)
    int ch;
    for (ch = 0; ch < fb->num_channels; ch++) {
        band->biquad[ch].b0 = b0;
        band->biquad[ch].b1 = b1;
        band->biquad[ch].b2 = b2;
        band->biquad[ch].a1 = a1;
        band->biquad[ch].a2 = a2;
        band->biquad[ch].z1 = 0.0;
        band->biquad[ch].z2 = 0.0;
    }

    fb->num_bands++;
    update_pre_gain(fb);
    return 0;
}

void nimRumDSP_biquadClear(nim_rum_dsp_filter_bank_t *fb) {
    fb->num_bands = 0;
    fb->pre_gain = 1.0;
}

// Process a single sample through one biquad (Direct Form II Transposed)
static inline double biquad_tick(nim_rum_dsp_biquad_t *bq, double in) {
    double out = bq->b0 * in + bq->z1;
    bq->z1 = bq->b1 * in - bq->a1 * out + bq->z2;
    bq->z2 = bq->b2 * in - bq->a2 * out;

    // Guard against filter instability (NaN/Inf from extreme coefficients)
    if (!isfinite(out) || !isfinite(bq->z1) || !isfinite(bq->z2)) {
        bq->z1 = 0.0;
        bq->z2 = 0.0;
        return in;  // Pass through on instability
    }

    return out;
}

void nimRumDSP_biquadProcess(nim_rum_dsp_filter_bank_t *fb,
                              int32_t *data[], int num_frames) {
    if (!fb->enabled || fb->num_bands == 0) {
        return;  // Bypass
    }

    int ch, band_idx, i;
    int num_ch = fb->num_channels;
    static const double MAX_SAMPLE = 2147483647.0;
    static const double SOFT_KNEE = 2000000000.0;  // Start soft-limiting at ~93% of max

    for (ch = 0; ch < num_ch; ch++) {
        int32_t *ch_data = data[ch];

        for (i = 0; i < num_frames; i++) {
            double sample = (double)ch_data[i];

            // Apply pre-gain to prevent intermediate clipping from boost bands
            sample *= fb->pre_gain;

            // Cascade through all bands
            for (band_idx = 0; band_idx < fb->num_bands; band_idx++) {
                sample = biquad_tick(&fb->bands[band_idx].biquad[ch], sample);
            }

            // Soft saturation: tanh-style limiting above knee
            if (sample > SOFT_KNEE) {
                double excess = (sample - SOFT_KNEE) / (MAX_SAMPLE - SOFT_KNEE);
                sample = SOFT_KNEE + (MAX_SAMPLE - SOFT_KNEE) * tanh(excess);
            } else if (sample < -SOFT_KNEE) {
                double excess = (-sample - SOFT_KNEE) / (MAX_SAMPLE - SOFT_KNEE);
                sample = -(SOFT_KNEE + (MAX_SAMPLE - SOFT_KNEE) * tanh(excess));
            }
            if (sample > 2147483647.0) {
                sample = 2147483647.0;
            } else if (sample < -2147483648.0) {
                sample = -2147483648.0;
            }

            ch_data[i] = (int32_t)sample;
        }
    }
}
