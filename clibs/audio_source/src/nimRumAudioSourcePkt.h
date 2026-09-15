/******************************************************************************
 *  COPYRIGHT (c) 2021-2026, AbtAudio AB, All rights reserved.
 ******************************************************************************/

// Shared packet definition for nimRumAudioSource → nimRumTx protocol

#pragma once

#include <stdint.h>

// Wire compatibility gate for the AudioSource protocol.
//
// PINNED, and deliberately NOT derived from the nimRumPkg version. It was
// `NIM_RUM_PKG_VERSION_MAJOR` until 2026-09-15, which was wrong in a way that hid
// itself for three weeks: the value only stayed at 1 through the whole 1.x -> 2.7.0
// history because clibs/build.sh skipped `cmake` configure whenever the build
// directory already existed, so the -D never reached the compiler. A clean clone —
// which is what any recipient of the public package does — would have compiled 2 and
// been dropped by every TX in existence, ping included, with no log line.
//
// A wire format should be versioned by the wire format. Bump this ONLY when the
// packet layout or semantics change incompatibly, and then expect to handle both
// values for a while. Every device deployed to date speaks 1.
#define NIM_RUM_AUDIO_SOURCE_PKT_VERSION     1

// Package version, reported in the SRC ping for display only — it gates nothing.
// The fallback is 0 on purpose: a build that forgets to define these should read as
// "unknown" (0.x.y) rather than as a plausible wrong version, which is exactly the
// trap the wire gate above fell into.
#ifndef NIM_RUM_PKG_VERSION_MAJOR
#define NIM_RUM_PKG_VERSION_MAJOR  0
#endif
#ifndef NIM_RUM_PKG_VERSION_MINOR
#define NIM_RUM_PKG_VERSION_MINOR  0
#endif
#ifndef NIM_RUM_PKG_VERSION_PATCH
#define NIM_RUM_PKG_VERSION_PATCH  0
#endif
#define NIM_RUM_AUDIO_SOURCE_PKT_PORT        53473
#define NIM_RUM_AUDIO_SOURCE_MAX_CHANNELS    32

// Frames per packet, matched to TX interval for lowest latency.
// Both SRC and TX share this header — always in sync after deploy.
#define NIM_RUM_AUDIO_SOURCE_FRAMES_PER_PKT_48K   384   // 8ms @ 48000/44100
#define NIM_RUM_AUDIO_SOURCE_FRAMES_PER_PKT_96K   576   // 6ms @ 96000
#define NIM_RUM_AUDIO_SOURCE_FRAMES_PER_PKT_192K  1152  // 6ms @ 192000

// Currently hardcoded to 384 for all rates
#define NIM_RUM_AUDIO_SOURCE_FRAMES_PER_PKT  384

#define NIM_RUM_AUDIO_SOURCE_FLAG_START      0x01
#define NIM_RUM_AUDIO_SOURCE_FLAG_STOP       0x02
#define NIM_RUM_AUDIO_SOURCE_FLAG_NO_DATA    0x04
#define NIM_RUM_AUDIO_SOURCE_FLAG_SRC_PING   0x10
#define NIM_RUM_AUDIO_SOURCE_FLAG_REQUEST_ACTIVE   0x20  // SRC wants to become active
#define NIM_RUM_AUDIO_SOURCE_FLAG_REQUEST_DESELECT 0x40  // SRC wants to be deselected

#define NIM_RUM_AUDIO_SOURCE_NAME_LEN     32
#define NIM_RUM_AUDIO_SOURCE_HOST_NAME_LEN 16
#define NIM_RUM_AUDIO_SOURCE_SSH_USER_LEN  16
#define NIM_RUM_AUDIO_SOURCE_CHANNEL_LAYOUT_LEN 16

// TX→SRC reply flags
#define NIM_RUM_AUDIO_SOURCE_REPLY_START_STREAM  0x01
#define NIM_RUM_AUDIO_SOURCE_REPLY_STOP_STREAM   0x02

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
} nim_rum_audio_source_pkt_header_t;

#define NIM_RUM_AUDIO_SOURCE_PKT_HEADER_SIZE sizeof(nim_rum_audio_source_pkt_header_t)

// SRC Ping packet — broadcast every 30s (or unicast if TX IP known)
// Carries identification, version, link stats, and streaming state.
// TX uses this to register the SRC in seenDevices and replies with
// a tx_to_src_reply packet.
typedef struct __attribute__((packed)) {
    uint8_t  version;        // Wire compat gate, pinned — see the #define above
    uint8_t  sourceId;       // Source ID (md5(hostname)[0])
    uint8_t  numOfCh;        // 0 (no audio payload)
    uint8_t  flags;          // NIM_RUM_AUDIO_SOURCE_FLAG_SRC_PING
    uint32_t sequenceNum;    // Ping counter
    uint64_t timestamp_us;   // Source monotonic clock
    // Identification
    char     sourceName[NIM_RUM_AUDIO_SOURCE_NAME_LEN];   // 32B
    char     hostName[NIM_RUM_AUDIO_SOURCE_HOST_NAME_LEN]; // 16B
    char     sshUser[NIM_RUM_AUDIO_SOURCE_SSH_USER_LEN];   // 16B
    // nimRumPkg version of the source, reported so TX can show which build a source
    // is running — useful mainly to someone writing their own source, who otherwise
    // has no confirmation of what TX thinks it is talking to.
    //
    // The `version` byte above is NOT part of this: it is the pinned wire gate and has
    // carried no package meaning since 2026-09-15. The major lives in what used to be
    // reserved_ver, so the packet size is unchanged and a source built before this
    // sends 0 there — which TX renders as "unknown" rather than as version 0.
    uint8_t  pkg_version_minor;
    uint8_t  pkg_version_patch;
    // Pre-fill depth, in packets, this source suggests for TX's input FIFO.
    // A source knows its own transport and TX does not: a local S/PDIF feeder
    // wants minimum latency, a Wi-Fi feeder wants headroom. 0 = no opinion, TX
    // keeps its default — which is also what an older source sends, since this
    // byte was reserved and zeroed. TX clamps the value and logs what it used.
    uint8_t  fifoTargetSuggestion;
    uint8_t  pkg_version_major;   // was reserved_ver[1]; 0 = source did not report
    // Link stats
    int8_t   wifiSignalDbm;
    uint8_t  wifiLinkQuality;
    uint16_t wifiTxRetries;
    uint16_t wifiRxErrors;
    // State
    uint8_t  streaming;      // 1=streaming, 0=idle
    uint8_t  captureMode;    // 0=spdif, 2=file
    // Channel layout announced by this source (e.g. "stereo", "5.1")
    char     channelLayout[NIM_RUM_AUDIO_SOURCE_CHANNEL_LAYOUT_LEN]; // 16B
} nim_rum_audio_source_ping_header_t;

#define NIM_RUM_AUDIO_SOURCE_PING_HEADER_SIZE sizeof(nim_rum_audio_source_ping_header_t)

// TX→SRC reply packet — unicast to SRC in response to ping or on source switch.
// SRC uses this to learn TX IP, confirm registration, and react to
// start/stop commands.
typedef struct __attribute__((packed)) {
    uint8_t  version;          // nimRumPkg major version (wire compat gate)
    uint8_t  flags;            // REPLY_START_STREAM / REPLY_STOP_STREAM / 0
    uint8_t  activeSourceId;   // Which sourceId is currently active on TX
    uint8_t  targetSourceId;   // Which sourceId TX wants active (0xFF = no preference)
    uint32_t reserved;
} nim_rum_audio_source_reply_header_t;

#define NIM_RUM_AUDIO_SOURCE_REPLY_HEADER_SIZE sizeof(nim_rum_audio_source_reply_header_t)
