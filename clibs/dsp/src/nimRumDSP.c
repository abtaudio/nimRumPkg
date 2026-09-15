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

#include "nimRumDSP.h"
#include "nimRumDSP_biquad.h"
#include "nimRumDSP_config.h"

#define NIM_RUM_DSP_VERSION "1.0.0"
#define DEFAULT_CONFIG_PATH "./calibration/eq_config.yaml"
#define MAX_PATH_LEN 512

// Plugin state
static nim_rum_dsp_filter_bank_t g_filter_bank;
static nim_rum_dsp_filter_bank_t g_filter_bank_staging;  // For hot-reload
static char g_config_path[MAX_PATH_LEN];
static int g_initialized = 0;

int nimRumDSP_init(const char *config_path, int sample_rate, int num_channels) {
    if (config_path != NULL) {
        strncpy(g_config_path, config_path, MAX_PATH_LEN - 1);
        g_config_path[MAX_PATH_LEN - 1] = '\0';
    } else {
        strncpy(g_config_path, DEFAULT_CONFIG_PATH, MAX_PATH_LEN - 1);
    }

    nimRumDSP_biquadInit(&g_filter_bank, sample_rate, num_channels);

    // Try loading config — not an error if file doesn't exist yet
    int res = nimRumDSP_configLoad(&g_filter_bank, g_config_path);
    if (res == 0) {
        printf("nimRumDSP: Loaded %d EQ bands from %s (pre_gain=%.3f)\n",
               g_filter_bank.num_bands, g_config_path, g_filter_bank.pre_gain);
    } else {
        printf("nimRumDSP: No config at %s (passthrough mode)\n", g_config_path);
    }

    g_initialized = 1;
    return 0;
}

int nimRumDSP_process(int32_t *data[], int num_channels, int num_frames) {
    if (!g_initialized || data == NULL || num_channels <= 0 || num_frames <= 0) {
        return -1;
    }

    // Guard: never process more channels than the caller provides
    int safe_channels = num_channels;
    if (safe_channels > g_filter_bank.num_channels) {
        safe_channels = g_filter_bank.num_channels;
    }

    // Validate channel pointers
    for (int ch = 0; ch < safe_channels; ch++) {
        if (data[ch] == NULL) {
            return -1;
        }
    }

    // Temporarily limit filter bank to safe channel count
    int orig_channels = g_filter_bank.num_channels;
    g_filter_bank.num_channels = safe_channels;
    nimRumDSP_biquadProcess(&g_filter_bank, data, num_frames);
    g_filter_bank.num_channels = orig_channels;

    return 0;
}

int nimRumDSP_reload(const char *config_path) {
    if (!g_initialized) {
        return -1;
    }

    const char *path = (config_path != NULL) ? config_path : g_config_path;

    // Load into staging buffer to avoid glitches if parse fails
    memcpy(&g_filter_bank_staging, &g_filter_bank, sizeof(g_filter_bank_staging));
    nimRumDSP_biquadClear(&g_filter_bank_staging);

    int res = nimRumDSP_configLoad(&g_filter_bank_staging, path);
    if (res != 0) {
        printf("nimRumDSP: Reload failed for %s (keeping previous config)\n", path);
        return -1;
    }

    // Swap — copy new config over (state reset is intentional on reload)
    memcpy(&g_filter_bank, &g_filter_bank_staging, sizeof(g_filter_bank));

    if (config_path != NULL) {
        strncpy(g_config_path, config_path, MAX_PATH_LEN - 1);
        g_config_path[MAX_PATH_LEN - 1] = '\0';
    }

    printf("nimRumDSP: Reloaded %d EQ bands from %s (pre_gain=%.3f)\n",
           g_filter_bank.num_bands, path, g_filter_bank.pre_gain);
    return 0;
}

void nimRumDSP_setEnable(int enable) {
    g_filter_bank.enabled = enable ? 1 : 0;
}

int nimRumDSP_getEnabled(void) {
    return g_filter_bank.enabled;
}

void nimRumDSP_close(void) {
    nimRumDSP_biquadClear(&g_filter_bank);
    g_initialized = 0;
    printf("nimRumDSP: Closed\n");
}

const char *nimRumDSP_version(void) {
    return NIM_RUM_DSP_VERSION;
}
