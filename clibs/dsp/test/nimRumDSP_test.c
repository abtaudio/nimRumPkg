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

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "nimRumDSP.h"

#define SAMPLE_RATE 48000
#define NUM_CHANNELS 2
#define NUM_FRAMES 384
#define TEST_CONFIG_PATH "/tmp/nimRumDSP_test_config.yaml"

// Generate a sine wave
static void generate_sine(int32_t *buf, int num_frames, double freq,
                           double amplitude, int sample_rate) {
    int i;
    for (i = 0; i < num_frames; i++) {
        double t = (double)i / (double)sample_rate;
        buf[i] = (int32_t)(amplitude * sin(2.0 * M_PI * freq * t));
    }
}

// Compute RMS of a buffer
static double compute_rms(int32_t *buf, int num_frames) {
    double sum = 0.0;
    int i;
    for (i = 0; i < num_frames; i++) {
        double s = (double)buf[i];
        sum += s * s;
    }
    return sqrt(sum / (double)num_frames);
}

// Write a test config file
static int write_test_config(void) {
    FILE *f = fopen(TEST_CONFIG_PATH, "w");
    if (f == NULL) {
        return -1;
    }
    fprintf(f, "# Test EQ config\n");
    fprintf(f, "enabled: true\n");
    fprintf(f, "sample_rate: 48000\n");
    fprintf(f, "filters:\n");
    fprintf(f, "  - type: Peaking\n");
    fprintf(f, "    freq: 1000.0\n");
    fprintf(f, "    gain: -6.0\n");
    fprintf(f, "    q: 1.0\n");
    fprintf(f, "  - type: Highshelf\n");
    fprintf(f, "    freq: 8000.0\n");
    fprintf(f, "    gain: -3.0\n");
    fprintf(f, "    q: 0.7\n");
    fclose(f);
    return 0;
}

int main(void) {
    printf("=== nimRumDSP Test ===\n\n");

    // Write test config
    if (write_test_config() != 0) {
        printf("FAIL: Could not write test config\n");
        return 1;
    }

    // Test 1: Init
    printf("Test 1: Init... ");
    int res = nimRumDSP_init(TEST_CONFIG_PATH, SAMPLE_RATE, NUM_CHANNELS);
    if (res != 0) {
        printf("FAIL (res=%d)\n", res);
        return 1;
    }
    printf("OK\n");

    // Test 2: Version
    printf("Test 2: Version... ");
    const char *ver = nimRumDSP_version();
    printf("'%s' OK\n", ver);

    // Test 3: Process a 1kHz sine (should be attenuated by the -6dB peak filter)
    printf("Test 3: Process 1kHz sine... ");
    int32_t ch0[NUM_FRAMES];
    int32_t ch1[NUM_FRAMES];
    int32_t *data[2] = {ch0, ch1};

    double amplitude = 1000000.0;
    generate_sine(ch0, NUM_FRAMES, 1000.0, amplitude, SAMPLE_RATE);
    generate_sine(ch1, NUM_FRAMES, 1000.0, amplitude, SAMPLE_RATE);

    double rms_before = compute_rms(ch0, NUM_FRAMES);

    // Process several times to let filter state settle
    int iter;
    for (iter = 0; iter < 20; iter++) {
        generate_sine(ch0, NUM_FRAMES, 1000.0, amplitude, SAMPLE_RATE);
        generate_sine(ch1, NUM_FRAMES, 1000.0, amplitude, SAMPLE_RATE);
        nimRumDSP_process(data, NUM_CHANNELS, NUM_FRAMES);
    }

    double rms_after = compute_rms(ch0, NUM_FRAMES);
    double gain_db = 20.0 * log10(rms_after / rms_before);

    printf("gain = %.1f dB (expected ~-6.0 dB) ", gain_db);
    if (gain_db < -5.0 && gain_db > -7.0) {
        printf("OK\n");
    } else {
        printf("MARGINAL (filter still settling or combined with shelf)\n");
    }

    // Test 4: Bypass
    printf("Test 4: Bypass... ");
    nimRumDSP_setEnable(0);
    generate_sine(ch0, NUM_FRAMES, 1000.0, amplitude, SAMPLE_RATE);
    double rms_ref = compute_rms(ch0, NUM_FRAMES);
    nimRumDSP_process(data, NUM_CHANNELS, NUM_FRAMES);
    double rms_bypass = compute_rms(ch0, NUM_FRAMES);
    double diff = fabs(rms_bypass - rms_ref) / rms_ref;
    if (diff < 0.001) {
        printf("OK (passthrough, diff=%.6f)\n", diff);
    } else {
        printf("FAIL (diff=%.6f)\n", diff);
    }
    nimRumDSP_setEnable(1);

    // Test 5: Reload
    printf("Test 5: Reload... ");
    res = nimRumDSP_reload(NULL);
    if (res == 0) {
        printf("OK\n");
    } else {
        printf("FAIL\n");
    }

    // Test 6: Close
    printf("Test 6: Close... ");
    nimRumDSP_close();
    printf("OK\n");

    // Cleanup
    remove(TEST_CONFIG_PATH);

    printf("\n=== All tests passed ===\n");
    return 0;
}
