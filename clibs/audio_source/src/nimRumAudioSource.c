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

#include "nimRumAudioSource.h"
#include "nimrum_log.h"

// Runtime print level for nimrum_log.h, WARN by default — see the note there.
// Defined here because this translation unit is always built.
int nimrum_log_level = NIMRUM_LOG_LEVEL_WARN;

void nimrum_log_set_level(int level) {
  if (level < NIMRUM_LOG_LEVEL_ERR) {
    level = NIMRUM_LOG_LEVEL_ERR;
  }
  if (level > NIMRUM_LOG_LEVEL_DEBUG) {
    level = NIMRUM_LOG_LEVEL_DEBUG;
  }
  nimrum_log_level = level;
}

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <math.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#include "capture/capture.h"
#include <nimRumAudioSourcePkt.h>
#include <sndfile.h>

// ******************
// **** SETTINGS ****
// ******************
#define FRAMES_PER_PKT   NIM_RUM_AUDIO_SOURCE_FRAMES_PER_PKT
#define MAX_CHANNELS     NIM_RUM_AUDIO_SOURCE_MAX_CHANNELS
#define PROTOCOL_VERSION NIM_RUM_AUDIO_SOURCE_PKT_VERSION

// Flags
#define FLAG_NO_DATA NIM_RUM_AUDIO_SOURCE_FLAG_NO_DATA

// ******************
// ***** TYPES  *****
// ******************

typedef nim_rum_audio_source_pkt_header_t pkt_header_t;

#define MAX_PKT_SIZE (sizeof(pkt_header_t) + MAX_CHANNELS * FRAMES_PER_PKT * 4)

// ******************
// ***** STATE  *****
// ******************

static volatile int g_running = 0;
static volatile int g_streaming = 1;
static volatile float g_volumeGain = 1.0f;
static volatile float g_statusPPM = 0.0f;
static volatile uint32_t g_statusSeqNum = 0;
static volatile int g_statusChannels = 0;
static volatile uint8_t g_pendingPingFlags = 0;  // Set by button, cleared by thread
static pthread_t g_thread;

static uint8_t g_sourceId;
static char g_targetHost[64];
static char g_alsaDevice[64];
static int g_captureMode;
static char g_filePath[256];
static char g_sourceName[NIM_RUM_AUDIO_SOURCE_NAME_LEN];
static char g_hostName[NIM_RUM_AUDIO_SOURCE_HOST_NAME_LEN];
static char g_channelLayout[NIM_RUM_AUDIO_SOURCE_CHANNEL_LAYOUT_LEN];
// Pre-fill depth this source asks TX to keep in its input FIFO, in packets.
// 0 = no opinion, TX uses its own default. Only this side knows whether the hop
// to TX is loopback or Wi-Fi, which is the whole reason the knob is here.
static volatile uint8_t g_fifoTargetSuggestion;
static volatile int g_txDiscovered;  // 1 once TX reply received
static struct in_addr g_txAddr;      // TX IP learned from ping reply

// ******************
// ***** HELPERS ****
// ******************

// WiFi link stats — read from /proc/net/wireless
static struct {
    int8_t   signal_dbm;
    uint8_t  link_quality;
    uint16_t tx_retries;
    uint16_t rx_errors;
    uint64_t last_update_us;
    uint64_t prev_retry;
    uint64_t prev_rx_errors;
    char     iface[32];
    int      initialized;
} g_linkStats;

static void _link_stats_update(uint64_t now_us) {
    // Only update every 2 seconds
    if (g_linkStats.initialized && (now_us - g_linkStats.last_update_us) < 2000000ULL) {
        return;
    }
    g_linkStats.last_update_us = now_us;

    FILE *f = fopen("/proc/net/wireless", "r");
    if (f == NULL) { g_linkStats.initialized = 1; return; }

    char line[256];
    // Skip 2 header lines
    if (fgets(line, sizeof(line), f) == NULL) { fclose(f); g_linkStats.initialized = 1; return; }
    if (fgets(line, sizeof(line), f) == NULL) { fclose(f); g_linkStats.initialized = 1; return; }

    if (fgets(line, sizeof(line), f) != NULL) {
        char *colon = strchr(line, ':');
        if (colon != NULL) {
            int status;
            float link_f, level_f, noise_f;
            unsigned int nwid, crypt, frag, retry, misc;
            if (sscanf(colon + 1, "%d %f %f %f %u %u %u %u %u",
                       &status, &link_f, &level_f, &noise_f,
                       &nwid, &crypt, &frag, &retry, &misc) >= 8) {
                g_linkStats.link_quality = (link_f > 70.0f) ? 70 : (uint8_t)link_f;
                int level_int = (int)level_f;
                if (level_int > 0) level_int = -level_int;
                g_linkStats.signal_dbm = (int8_t)level_int;

                uint64_t retry64 = (uint64_t)retry;
                if (g_linkStats.prev_retry > 0 && retry64 >= g_linkStats.prev_retry) {
                    uint64_t delta = retry64 - g_linkStats.prev_retry;
                    g_linkStats.tx_retries = (delta > 65535) ? 65535 : (uint16_t)delta;
                }
                g_linkStats.prev_retry = retry64;
            }

            // Store iface name for sysfs lookup
            if (!g_linkStats.initialized) {
                char *ns = line;
                while (*ns == ' ') ns++;
                size_t len = (size_t)(colon - ns);
                if (len > 0 && len < sizeof(g_linkStats.iface)) {
                    memcpy(g_linkStats.iface, ns, len);
                    g_linkStats.iface[len] = '\0';
                }
            }
        }
    }
    fclose(f);

    // Read rx_errors from sysfs
    if (g_linkStats.iface[0]) {
        char path[128];
        snprintf(path, sizeof(path), "/sys/class/net/%s/statistics/rx_errors",
                 g_linkStats.iface);
        FILE *ef = fopen(path, "r");
        if (ef) {
            uint64_t rx_err = 0;
            if (fscanf(ef, "%" SCNu64, &rx_err) == 1) {
                if (g_linkStats.prev_rx_errors > 0 && rx_err >= g_linkStats.prev_rx_errors) {
                    uint64_t delta = rx_err - g_linkStats.prev_rx_errors;
                    g_linkStats.rx_errors = (delta > 65535) ? 65535 : (uint16_t)delta;
                }
                g_linkStats.prev_rx_errors = rx_err;
            }
            fclose(ef);
        }
    }
    g_linkStats.initialized = 1;
}

// Derive sourceId from hostname (simple hash → 1 byte, avoids 0 and 0xFF)
static uint8_t _derive_source_id(const char *hostname) {
    uint32_t h = 5381;
    while (*hostname) {
        h = ((h << 5) + h) + (uint8_t)*hostname++;
    }
    uint8_t id = (uint8_t)(h & 0xFF);
    if (id == 0) id = 1;
    if (id == 0xFF) id = 0xFE;
    return id;
}

static uint64_t get_monotonic_us(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (uint64_t)ts.tv_sec * 1000000ULL + (uint64_t)ts.tv_nsec / 1000ULL;
}

static void apply_volume(int32_t *data[], int channels, int frames, float gain) {
  if (gain == 1.0f) return;
  int ch, f;
  for (ch = 0; ch < channels; ch++) {
    for (f = 0; f < frames; f++) {
      float s = (float)data[ch][f] * gain;
      if (s > 2147483647.0f) s = 2147483647.0f;
      if (s < -2147483648.0f) s = -2147483648.0f;
      data[ch][f] = (int32_t)s;
    }
  }
}

// ******************
// ***** THREAD *****
// ******************

static void *capture_send_thread(void *arg) {
  (void)arg;

  // Set real-time priority (just below TX at 99) to avoid starvation
  struct sched_param sp;
  sp.sched_priority = 80;
  if (pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp) != 0) {
    printf("nimRumAudioSource: WARNING — could not set RT priority "
           "(not running as root?)\n");
  }

  SNDFILE *sndFile = NULL;
  SF_INFO sfInfo;
  int fileChannels = 0;

  // Init source
  if (g_captureMode == 2) {
    // File mode
    memset(&sfInfo, 0, sizeof(sfInfo));
    sndFile = sf_open(g_filePath, SFM_READ, &sfInfo);
    if (!sndFile) {
      printf("nimRumAudioSource: failed to open file: %s (%s)\n",
             g_filePath, sf_strerror(NULL));
      g_running = 0;
      return NULL;
    }
    if (sfInfo.samplerate != 48000 && sfInfo.samplerate != 44100 &&
        sfInfo.samplerate != 96000 && sfInfo.samplerate != 192000) {
      printf("nimRumAudioSource: unsupported sample rate %d\n", sfInfo.samplerate);
      sf_close(sndFile);
      g_running = 0;
      return NULL;
    }
    fileChannels = sfInfo.channels;
    if (fileChannels > MAX_CHANNELS) fileChannels = MAX_CHANNELS;
    printf("nimRumAudioSource: file %s (%d ch, %d Hz)\n",
           g_filePath, fileChannels, sfInfo.samplerate);
  } else {
    // ALSA mode (spdif)
    int res = capture_init(0, g_alsaDevice);
    if (res != 0) {
      printf("nimRumAudioSource: capture_init failed\n");
      g_running = 0;
      return NULL;
    }
  }

  int32_t *data[MAX_CHANNELS];
  int ch;
  for (ch = 0; ch < MAX_CHANNELS; ch++) {
    data[ch] = malloc(FRAMES_PER_PKT * sizeof(int32_t));
  }

  // Interleaved buffer for file reading
  int32_t *interleavedBuf = NULL;
  if (g_captureMode == 2) {
    interleavedBuf = malloc(FRAMES_PER_PKT * fileChannels * sizeof(int32_t));
  }

  // UDP socket
  int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (sock < 0) {
    printf("nimRumAudioSource: socket failed\n");
    g_running = 0;
    return NULL;
  }

  struct sockaddr_in dstAddr;
  memset(&dstAddr, 0, sizeof(dstAddr));
  dstAddr.sin_family = AF_INET;
  dstAddr.sin_port = htons(NIM_RUM_AUDIO_SOURCE_PKT_PORT);
  if (g_targetHost[0] != '\0') {
    inet_aton(g_targetHost, &dstAddr.sin_addr);
  }

  uint8_t pktBuf[MAX_PKT_SIZE];
  pkt_header_t *hdr = (pkt_header_t *)pktBuf;
  uint32_t seqNum = 0;

  int samplesQueued, activeChannels, dataValid;
  uint64_t channelMapping;
  float txPPM;

  uint32_t sampleRate = (g_captureMode == 2) ? (uint32_t)sfInfo.samplerate : 48000;
  uint8_t bytesPerSample = 2;  // default for ALSA (16-bit)
  if (g_captureMode == 2) {
    int subformat = sfInfo.format & SF_FORMAT_SUBMASK;
    if (subformat == SF_FORMAT_PCM_24) bytesPerSample = 3;
    else if (subformat == SF_FORMAT_PCM_32 || subformat == SF_FORMAT_FLOAT ||
             subformat == SF_FORMAT_DOUBLE) bytesPerSample = 4;
    else bytesPerSample = 2;  // 8-bit and 16-bit → 2
  }
  memset(pktBuf, 0, MAX_PKT_SIZE);

  // --- SRC Ping infrastructure ---
  // Broadcast socket for SRC ping (separate from audio socket)
  int pingSock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (pingSock >= 0) {
    int bcast = 1;
    setsockopt(pingSock, SOL_SOCKET, SO_BROADCAST, &bcast, sizeof(bcast));
    // Non-blocking for reply reception
    int fl = fcntl(pingSock, F_GETFL, 0);
    fcntl(pingSock, F_SETFL, fl | O_NONBLOCK);
    // Bind to any port so we can receive replies
    struct sockaddr_in bindAddr;
    memset(&bindAddr, 0, sizeof(bindAddr));
    bindAddr.sin_family = AF_INET;
    bindAddr.sin_addr.s_addr = INADDR_ANY;
    bindAddr.sin_port = 0;  // Ephemeral port
    bind(pingSock, (struct sockaddr *)&bindAddr, sizeof(bindAddr));
  }

  // Broadcast address for ping (subnet broadcast)
  struct sockaddr_in pingDstAddr;
  memset(&pingDstAddr, 0, sizeof(pingDstAddr));
  pingDstAddr.sin_family = AF_INET;
  pingDstAddr.sin_port = htons(NIM_RUM_AUDIO_SOURCE_PKT_PORT);
  pingDstAddr.sin_addr.s_addr = INADDR_BROADCAST;

  // If targetHost is not localhost, derive subnet broadcast from it
  // e.g. 192.0.2.10 → 192.0.2.255
  {
    struct in_addr targetAddr;
    if (inet_aton(g_targetHost, &targetAddr)) {
      uint32_t ip = ntohl(targetAddr.s_addr);
      if ((ip & 0xFF) != 0x01) {  // Not 127.x.x.x
        ip = (ip & 0xFFFFFF00) | 0xFF;  // /24 broadcast
        pingDstAddr.sin_addr.s_addr = htonl(ip);
      }
    }
  }

  uint64_t lastPingTime_us = 0;
  uint32_t pingSeqNum = 0;
  struct in_addr txAddr;
  memset(&txAddr, 0, sizeof(txAddr));

  int wasStreaming = 1;

  // Send initial ping immediately
  lastPingTime_us = get_monotonic_us() - 30000000ULL;

  printf("nimRumAudioSource: running (id=%d target=%s:%d mode=%d)\n",
         g_sourceId, g_targetHost[0] ? g_targetHost : "auto",
         NIM_RUM_AUDIO_SOURCE_PKT_PORT, g_captureMode);

  while (g_running) {

    // --- SRC Ping: send every 30s, or immediately on button request ---
    uint64_t nowPing = get_monotonic_us();
    uint8_t pendingFlags = g_pendingPingFlags;
    if (pendingFlags != 0 || (nowPing - lastPingTime_us) >= 30000000ULL) {
      lastPingTime_us = nowPing;
      g_pendingPingFlags = 0;
      _link_stats_update(nowPing);

      nim_rum_audio_source_ping_header_t ping;
      memset(&ping, 0, sizeof(ping));
      ping.version = PROTOCOL_VERSION;
      ping.sourceId = g_sourceId;
      ping.numOfCh = 0;
      ping.flags = NIM_RUM_AUDIO_SOURCE_FLAG_SRC_PING | pendingFlags;
      ping.sequenceNum = pingSeqNum++;
      ping.timestamp_us = nowPing;
      memcpy(ping.sourceName, g_sourceName, NIM_RUM_AUDIO_SOURCE_NAME_LEN);
      memcpy(ping.hostName, g_hostName, NIM_RUM_AUDIO_SOURCE_HOST_NAME_LEN);
      memcpy(ping.channelLayout, g_channelLayout, NIM_RUM_AUDIO_SOURCE_CHANNEL_LAYOUT_LEN);
      // sshUser will be set by Python via a new API call (TODO)
      ping.pkg_version_major = NIM_RUM_PKG_VERSION_MAJOR;
      ping.pkg_version_minor = NIM_RUM_PKG_VERSION_MINOR;
      ping.pkg_version_patch = NIM_RUM_PKG_VERSION_PATCH;
      ping.fifoTargetSuggestion = g_fifoTargetSuggestion;
      ping.wifiSignalDbm = g_linkStats.signal_dbm;
      ping.wifiLinkQuality = g_linkStats.link_quality;
      ping.wifiTxRetries = htons(g_linkStats.tx_retries);
      ping.wifiRxErrors = htons(g_linkStats.rx_errors);
      ping.streaming = g_streaming ? 1 : 0;
      ping.captureMode = (uint8_t)g_captureMode;

      if (pingSock >= 0) {
        sendto(pingSock, &ping, sizeof(ping), MSG_DONTWAIT,
               (struct sockaddr *)&pingDstAddr, sizeof(pingDstAddr));
      }
    }

    // --- Check for TX→SRC reply ---
    if (pingSock >= 0) {
      nim_rum_audio_source_reply_header_t reply;
      struct sockaddr_in replySrcAddr;
      socklen_t replyAddrLen = sizeof(replySrcAddr);
      ssize_t rn = recvfrom(pingSock, &reply, sizeof(reply), MSG_DONTWAIT,
                            (struct sockaddr *)&replySrcAddr, &replyAddrLen);
      if (rn >= (ssize_t)sizeof(reply) &&
          reply.version == PROTOCOL_VERSION) {
        txAddr = replySrcAddr.sin_addr;

        // Update audio destination to TX address (auto-discovery)
        if (!g_txDiscovered || dstAddr.sin_addr.s_addr != txAddr.s_addr) {
          dstAddr.sin_addr = txAddr;
          g_txDiscovered = 1;
          g_txAddr = txAddr;
          char ipBuf[INET_ADDRSTRLEN];
          inet_ntop(AF_INET, &txAddr, ipBuf, sizeof(ipBuf));
          printf("nimRumAudioSource: TX discovered at %s\n", ipBuf);
        }

        // React to TX commands
        if (reply.flags & NIM_RUM_AUDIO_SOURCE_REPLY_STOP_STREAM) {
          if (g_streaming) {
            g_streaming = 0;
            printf("nimRumAudioSource: TX requested STOP (active=%d)\n",
                   reply.activeSourceId);
          }
        } else if (reply.flags & NIM_RUM_AUDIO_SOURCE_REPLY_START_STREAM) {
          if (!g_streaming) {
            g_streaming = 1;
            printf("nimRumAudioSource: TX requested START\n");
          }
        }
      }
    }

    if (!g_streaming) {
      if (wasStreaming) {
        wasStreaming = 0;
      }
      usleep(50000);
      continue;
    }

    if (!wasStreaming) {
      wasStreaming = 1;
    }

    if (g_captureMode == 2) {
      // FILE MODE: read frames, pace with absolute clock
      static uint64_t nextSendTime_us = 0;
      if (nextSendTime_us == 0) nextSendTime_us = get_monotonic_us();

      sf_count_t read = sf_readf_int(sndFile, interleavedBuf, FRAMES_PER_PKT);
      if (read < FRAMES_PER_PKT) {
        // EOF — loop back to start
        sf_seek(sndFile, 0, SEEK_SET);
        if (read <= 0) {
          nextSendTime_us += 1000000ULL * FRAMES_PER_PKT / sampleRate;
          uint64_t now = get_monotonic_us();
          if (nextSendTime_us > now) usleep((unsigned int)(nextSendTime_us - now));
          continue;
        }
        // Zero-pad remainder
        memset(interleavedBuf + read * fileChannels, 0,
               (FRAMES_PER_PKT - read) * fileChannels * sizeof(int32_t));
      }

      // De-interleave into channel-first layout
      int f;
      for (f = 0; f < FRAMES_PER_PKT; f++) {
        for (ch = 0; ch < fileChannels; ch++) {
          data[ch][f] = interleavedBuf[f * fileChannels + ch];
        }
      }
      activeChannels = fileChannels;
      txPPM = 0.0f;
      dataValid = 1;

      // Wait until target send time
      nextSendTime_us += 1000000ULL * FRAMES_PER_PKT / sampleRate;
      uint64_t now = get_monotonic_us();
      if (nextSendTime_us > now) {
        usleep((unsigned int)(nextSendTime_us - now));
      }
      // If we're late, don't accumulate debt — but don't reset either
      // (we'll catch up naturally by not sleeping next iteration)

    } else {
      // ALSA MODE: wait for data
      int res = 0;
      int wait_count = 0;
      while (res < FRAMES_PER_PKT && g_running) {
        res = capture_get_fifo_count();
        if (res < FRAMES_PER_PKT) {
          wait_count++;
          if (wait_count > 500) {
            // No data for >500ms — ALSA device likely not ready.
            // Back off to avoid spinning CPU and thrashing memory.
            usleep(100000);  // 100ms
          } else {
            usleep(1000);    // 1ms
          }
        }
      }
      if (!g_running) break;

      capture_get_data(data, FRAMES_PER_PKT, &samplesQueued,
                                   &activeChannels, &channelMapping,
                                   &txPPM, &dataValid);

      // Auto-update channelLayout from ffmpeg detection (spdif mode)
      const char *detected = capture_get_layout_name();
      if (detected == NULL && activeChannels >= 1 && activeChannels <= 8) {
        // Fallback: derive layout name from channel count if ffmpeg didn't provide
        static const char *ch_to_layout[] = {
          [1] = "mono", [2] = "stereo", [3] = "2.1",
          [4] = "quad", [5] = "5.0", [6] = "5.1",
          [7] = "6.1", [8] = "7.1"
        };
        detected = ch_to_layout[activeChannels];
      }
      if (detected && strncmp(g_channelLayout, detected, sizeof(g_channelLayout)) != 0) {
        printf("nimRumAudioSource: layout detected: %s\n", detected);
        fflush(stdout);
        memset(g_channelLayout, 0, sizeof(g_channelLayout));
        strncpy(g_channelLayout, detected, sizeof(g_channelLayout) - 1);
        // Force immediate ping so TX learns the new layout quickly
        lastPingTime_us = 0;
      }

      if (activeChannels < 1) activeChannels = 2;
      if (activeChannels > MAX_CHANNELS) activeChannels = MAX_CHANNELS;
    }

    // Apply volume
    apply_volume(data, activeChannels, FRAMES_PER_PKT, g_volumeGain);

    // Build packet (v2: slim header, identification moved to ping)
    hdr->version = PROTOCOL_VERSION;
    hdr->sourceId = g_sourceId;
    hdr->numOfCh = (uint8_t)activeChannels;
    hdr->flags = (dataValid < 0) ? FLAG_NO_DATA : 0;
    hdr->sequenceNum = seqNum++;
    hdr->timestamp_us = get_monotonic_us();
    hdr->txPPM = txPPM;
    hdr->sampleRate = sampleRate;
    hdr->bytesPerSample = bytesPerSample;

    // Pack samples at the correct byte width
    // Note: sf_readf_int returns samples scaled to full int32 range
    // For 16-bit transport: shift right by 16 to get the 16-bit value
    // For 24-bit transport: shift right by 8 to get the 24-bit value
    // For 32-bit transport: send as-is
    // ALSA mode: data is already 16-bit in lower bits (not left-justified)
    uint8_t *sampleDst = pktBuf + sizeof(pkt_header_t);
    int f;
    for (ch = 0; ch < activeChannels; ch++) {
      for (f = 0; f < FRAMES_PER_PKT; f++) {
        int32_t s = data[ch][f];
        if (bytesPerSample == 2) {
          int16_t s16;
          if (g_captureMode == 2) {
            s16 = (int16_t)(s >> 16);  // File: left-justified int32 → 16-bit
          } else {
            s16 = (int16_t)s;  // ALSA: already 16-bit in lower bits
          }
          memcpy(sampleDst, &s16, 2);
          sampleDst += 2;
        } else if (bytesPerSample == 3) {
          // File: left-justified int32 → 24-bit little-endian
          sampleDst[0] = (uint8_t)(s >> 8);
          sampleDst[1] = (uint8_t)(s >> 16);
          sampleDst[2] = (uint8_t)(s >> 24);
          sampleDst += 3;
        } else {
          memcpy(sampleDst, &s, 4);
          sampleDst += 4;
        }
      }
    }

    size_t pktSize = sizeof(pkt_header_t) + (size_t)activeChannels * FRAMES_PER_PKT * bytesPerSample;
    sendto(sock, pktBuf, pktSize, 0,
           (struct sockaddr *)&dstAddr, sizeof(dstAddr));

    g_statusPPM = txPPM;
    g_statusSeqNum = seqNum;
    g_statusChannels = activeChannels;
  }


  if (pingSock >= 0) close(pingSock);
  close(sock);
  for (ch = 0; ch < MAX_CHANNELS; ch++) {
    free(data[ch]);
  }
  free(interleavedBuf);
  if (g_captureMode == 2) {
    sf_close(sndFile);
  } else {
    capture_close();
  }

  printf("nimRumAudioSource: stopped.\n");
  return NULL;
}

// ******************
// ***** PUBLIC *****
// ******************

int nimRumAudioSource_start(const char *sourceName,
                            const char *targetHost,
                            const char *alsaDevice, int captureMode,
                            const char *filePath) {
  if (g_running) return -1;

  memset(g_hostName, 0, sizeof(g_hostName));
  gethostname(g_hostName, sizeof(g_hostName) - 1);
  g_sourceId = _derive_source_id(g_hostName);
  memset(g_sourceName, 0, sizeof(g_sourceName));
  strncpy(g_sourceName, sourceName ? sourceName : "", sizeof(g_sourceName) - 1);
  memset(g_channelLayout, 0, sizeof(g_channelLayout));
  strncpy(g_channelLayout, "stereo", sizeof(g_channelLayout) - 1);
  memset(g_targetHost, 0, sizeof(g_targetHost));
  if (targetHost && targetHost[0] != '\0') {
    strncpy(g_targetHost, targetHost, sizeof(g_targetHost) - 1);
  }
  strncpy(g_alsaDevice, alsaDevice ? alsaDevice : "", sizeof(g_alsaDevice) - 1);
  g_captureMode = captureMode;
  strncpy(g_filePath, filePath ? filePath : "", sizeof(g_filePath) - 1);
  g_running = 1;
  g_streaming = 0;  // Don't stream until TX says so
  g_txDiscovered = 0;
  memset(&g_txAddr, 0, sizeof(g_txAddr));
  g_volumeGain = 1.0f;

  // If targetHost was explicitly set, start streaming immediately (legacy compat)
  if (g_targetHost[0] != '\0') {
    g_streaming = 1;
    g_txDiscovered = 1;
  }

  pthread_create(&g_thread, NULL, capture_send_thread, NULL);
  return 0;
}

int nimRumAudioSource_stop(void) {
  if (!g_running) return -1;
  g_running = 0;
  pthread_join(g_thread, NULL);
  return 0;
}

void nimRumAudioSource_setVolumeDb(int volumeDb) {
  if (volumeDb < -40) volumeDb = -40;
  if (volumeDb > 6) volumeDb = 6;
  g_volumeGain = powf(10.0f, (float)volumeDb / 20.0f);
}

void nimRumAudioSource_setStreaming(int enable) {
  g_streaming = enable ? 1 : 0;
}

int nimRumAudioSource_isStreaming(void) {
  return g_streaming;
}

void nimRumAudioSource_requestActive(void) {
  // Trigger immediate ping with REQUEST_ACTIVE flag
  g_pendingPingFlags = NIM_RUM_AUDIO_SOURCE_FLAG_REQUEST_ACTIVE;
}

void nimRumAudioSource_requestDeselect(void) {
  // Trigger immediate ping with REQUEST_DESELECT flag
  g_pendingPingFlags = NIM_RUM_AUDIO_SOURCE_FLAG_REQUEST_DESELECT;
}

int nimRumAudioSource_isRunning(void) {
  return g_running;
}

void nimRumAudioSource_getStatus(float *ppm, uint32_t *seqNum, int *channels) {
  *ppm = g_statusPPM;
  *seqNum = g_statusSeqNum;
  *channels = g_statusChannels;
}

void nimRumAudioSource_setChannelLayout(const char *layout) {
  memset(g_channelLayout, 0, sizeof(g_channelLayout));
  if (layout) {
    strncpy(g_channelLayout, layout, sizeof(g_channelLayout) - 1);
  }
}

void nimRumAudioSource_setFifoTargetSuggestion(int packets) {
  // Out of range is treated as "no opinion" rather than clamped here: TX clamps
  // against its own FIFO depth, which this side does not know, and it logs what
  // it settled on. Sending a silently corrected value would hide the mistake in
  // two places instead of reporting it in one.
  if (packets < 0 || packets > 255) {
    packets = 0;
  }
  g_fifoTargetSuggestion = (uint8_t)packets;
}
