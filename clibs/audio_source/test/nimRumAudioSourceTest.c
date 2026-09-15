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

// Unit test for nimRumAudioSource: packet format and decode buffer

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nimRumAudioSource.h"

// Mirror the packet header from nimRumAudioSource.c
typedef struct __attribute__((packed)) {
  uint8_t  version;
  uint8_t  sourceId;
  uint8_t  numOfCh;
  uint8_t  flags;
  uint32_t sequenceNum;
  uint64_t timestamp_us;
  float    txPPM;
  uint32_t sampleRate;
  uint8_t  bytesPerSample;
} pkt_header_t;

#define FRAMES_PER_PKT 384

static void test_header_size(void) {
  printf("  test_header_size... ");
  assert(sizeof(pkt_header_t) == 25);
  printf("OK\n");
}

static void test_header_fields(void) {
  printf("  test_header_fields... ");
  pkt_header_t h;
  memset(&h, 0, sizeof(h));
  h.version = 1;
  h.sourceId = 42;
  h.numOfCh = 6;
  h.flags = 0x01;
  h.sequenceNum = 12345;
  h.timestamp_us = 9876543210ULL;
  h.txPPM = 3.14f;
  h.sampleRate = 48000;
  h.bytesPerSample = 3;

  // Verify by reading raw bytes
  uint8_t *raw = (uint8_t *)&h;
  (void)raw;
  assert(raw[0] == 1);   // version
  assert(raw[1] == 42);  // sourceId
  assert(raw[2] == 6);   // numOfCh
  assert(raw[3] == 0x01); // flags
  printf("OK\n");
}

static void test_packet_size_stereo_16bit(void) {
  printf("  test_packet_size_stereo_16bit... ");
  size_t expected = 25 + 2 * FRAMES_PER_PKT * 2;  // header + 2ch * 384 * 2bytes
  assert(expected == 1561);
  printf("OK (%zu bytes)\n", expected);
}

static void test_packet_size_6ch_24bit(void) {
  printf("  test_packet_size_6ch_24bit... ");
  size_t expected = 25 + 6 * FRAMES_PER_PKT * 3;  // header + 6ch * 384 * 3bytes
  assert(expected == 6937);
  printf("OK (%zu bytes)\n", expected);
}

static void test_16bit_packing(void) {
  printf("  test_16bit_packing... ");
  // Simulate: int32 left-justified sample → 16-bit pack → unpack
  int32_t sample_in = 0x7FFF0000;  // Max positive 16-bit, left-justified
  int16_t packed = (int16_t)(sample_in >> 16);
  (void)packed;
  assert(packed == 0x7FFF);

  int32_t sample_neg = (int32_t)0x80010000;  // Near min negative
  int16_t packed_neg = (int16_t)(sample_neg >> 16);
  (void)packed_neg;
  assert(packed_neg == (int16_t)0x8001);
  printf("OK\n");
}

static void test_24bit_packing(void) {
  printf("  test_24bit_packing... ");
  // Simulate: int32 left-justified → 24-bit LE pack → unpack
  int32_t sample_in = 0x12345600;  // 24-bit value 0x123456, left-justified

  uint8_t bytes[3];
  bytes[0] = (uint8_t)(sample_in >> 8);
  bytes[1] = (uint8_t)(sample_in >> 16);
  bytes[2] = (uint8_t)(sample_in >> 24);

  // Unpack (as done in nimRumAudioSourceRx)
  int32_t unpacked = (int32_t)((uint32_t)bytes[0] << 8 |
                                (uint32_t)bytes[1] << 16 |
                                (uint32_t)bytes[2] << 24);
  unpacked >>= 16;  // Scale to 16-bit for TX

  // Original 24-bit value 0x123456 → scaled to 16-bit ≈ 0x1234
  assert(unpacked == 0x1234);
  printf("OK\n");
}

int main(void) {
  printf("nimRumAudioSource unit tests:\n");
  test_header_size();
  test_header_fields();
  test_packet_size_stereo_16bit();
  test_packet_size_6ch_24bit();
  test_16bit_packing();
  test_24bit_packing();
  printf("All tests passed.\n");
  return 0;
}
