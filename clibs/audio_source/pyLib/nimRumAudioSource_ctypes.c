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

// ctypes-friendly wrapper for nimRumAudioSource.
// No Python.h dependency — builds a plain shared library (.so).

#include <stdint.h>

#include "nimRumAudioSource.h"

#include "nimrum_log.h"

#define EXPORT __attribute__((visibility("default")))

EXPORT void ct_nimRumAudioSource_setPrintLevel(int level) {
    nimrum_log_set_level(level);
}

EXPORT int ct_nimRumAudioSource_start(const char *sourceName,
                                      const char *targetHost,
                                      const char *alsaDevice, int captureMode,
                                      const char *filePath) {
    return nimRumAudioSource_start(sourceName, targetHost,
                                   alsaDevice, captureMode, filePath);
}

EXPORT int ct_nimRumAudioSource_stop(void) {
    return nimRumAudioSource_stop();
}

EXPORT void ct_nimRumAudioSource_setVolumeDb(int volumeDb) {
    nimRumAudioSource_setVolumeDb(volumeDb);
}

EXPORT void ct_nimRumAudioSource_setStreaming(int enable) {
    nimRumAudioSource_setStreaming(enable);
}

EXPORT int ct_nimRumAudioSource_isStreaming(void) {
    return nimRumAudioSource_isStreaming();
}

EXPORT void ct_nimRumAudioSource_requestActive(void) {
    nimRumAudioSource_requestActive();
}

EXPORT void ct_nimRumAudioSource_requestDeselect(void) {
    nimRumAudioSource_requestDeselect();
}

EXPORT int ct_nimRumAudioSource_isRunning(void) {
    return nimRumAudioSource_isRunning();
}

EXPORT void ct_nimRumAudioSource_getStatus(float *ppm_out, uint32_t *seqNum_out,
                                           int *channels_out) {
    nimRumAudioSource_getStatus(ppm_out, seqNum_out, channels_out);
}

EXPORT void ct_nimRumAudioSource_setChannelLayout(const char *layout) {
    nimRumAudioSource_setChannelLayout(layout);
}

EXPORT void ct_nimRumAudioSource_setFifoTargetSuggestion(int packets) {
    nimRumAudioSource_setFifoTargetSuggestion(packets);
}
