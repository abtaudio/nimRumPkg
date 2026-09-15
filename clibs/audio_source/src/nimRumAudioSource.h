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

#include <stdint.h>

// captureMode: 0=spdif (auto-detect PCM/encoded), 2=file
// targetHost: TX IP or "" for auto-discovery via ping reply
int nimRumAudioSource_start(const char *sourceName,
                            const char *targetHost,
                            const char *alsaDevice, int captureMode,
                            const char *filePath);

int nimRumAudioSource_stop(void);

void nimRumAudioSource_setVolumeDb(int volumeDb);

void nimRumAudioSource_setStreaming(int enable);

int nimRumAudioSource_isStreaming(void);

// Request to become active source (sends immediate ping to TX)
void nimRumAudioSource_requestActive(void);

// Request to be deselected (sends immediate ping to TX)
void nimRumAudioSource_requestDeselect(void);

void nimRumAudioSource_getStatus(float *ppm, uint32_t *seqNum, int *channels);

// Set the channel layout name announced in SRC ping (e.g. "stereo", "5.1")
void nimRumAudioSource_setChannelLayout(const char *layout);

// Suggest, in the SRC ping, how many packets TX should pre-fill in its input
// FIFO for this source. 0 = no opinion, TX keeps its default. A feeder on the TX
// box wants this low (latency), one over Wi-Fi wants it higher (headroom); TX
// cannot tell which it has. TX clamps against its own FIFO depth and logs the
// value it used.
void nimRumAudioSource_setFifoTargetSuggestion(int packets);

int nimRumAudioSource_isRunning(void);
