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

#ifndef NIM_RUM_DSP_H
#define NIM_RUM_DSP_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define NIM_RUM_DSP_EXPORT __attribute__((visibility("default")))

/**
 * Initialize the DSP plugin.
 *
 * Called once by libNimRumRx at startup after dlopen().
 * The plugin should load its configuration from the given path.
 *
 * @param config_path  Path to the YAML configuration file (e.g. "./calibration/eq_config.yaml").
 *                     NULL means use default path.
 * @param sample_rate  Audio sample rate in Hz (e.g. 48000).
 * @param num_channels Number of output channels (typically 1 or 2).
 * @return 0 on success, -1 on error.
 */
NIM_RUM_DSP_EXPORT int nimRumDSP_init(const char *config_path, int sample_rate, int num_channels);

/**
 * Process audio data in-place.
 *
 * Called by libNimRumRx on every audio frame batch, right before the data
 * is written to ALSA (via magicPipe). The plugin must process the data
 * in-place and return immediately (real-time safe).
 *
 * @param data         Array of channel pointers. data[ch][i] = sample i of channel ch.
 *                     Samples are signed 32-bit integers (24-bit audio in upper bits).
 * @param num_channels Number of valid channel pointers in data[].
 * @param num_frames   Number of audio frames (samples per channel) in this batch.
 * @return 0 on success, -1 on error (audio passes through unmodified on error).
 */
NIM_RUM_DSP_EXPORT int nimRumDSP_process(int32_t *data[], int num_channels, int num_frames);

/**
 * Reload configuration from file.
 *
 * Called when TX deploys a new calibration profile. The plugin should
 * re-read its config file and update filter coefficients. Must be safe
 * to call while process() is running (use double-buffering or atomic swap).
 *
 * @param config_path  Path to the new config file. NULL = reload from original path.
 * @return 0 on success, -1 on error (keeps previous config on error).
 */
NIM_RUM_DSP_EXPORT int nimRumDSP_reload(const char *config_path);

/**
 * Enable or disable processing.
 *
 * When disabled, process() becomes a no-op (pass-through).
 * Can be toggled at runtime from the WebUI or RX config.
 *
 * @param enable  1 = active, 0 = bypass.
 */
NIM_RUM_DSP_EXPORT void nimRumDSP_setEnable(int enable);

/**
 * Check if the plugin is currently enabled.
 *
 * @return 1 if active, 0 if bypassed.
 */
NIM_RUM_DSP_EXPORT int nimRumDSP_getEnabled(void);

/**
 * Close and free all plugin resources.
 *
 * Called by libNimRumRx on shutdown.
 */
NIM_RUM_DSP_EXPORT void nimRumDSP_close(void);

/**
 * Get plugin version string.
 *
 * @return Null-terminated version string (e.g. "1.0.0").
 */
NIM_RUM_DSP_EXPORT const char *nimRumDSP_version(void);

#ifdef __cplusplus
}
#endif

#endif /* NIM_RUM_DSP_H */
