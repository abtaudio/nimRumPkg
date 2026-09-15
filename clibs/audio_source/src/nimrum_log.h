// nimrum_log.h — Minimal logging macros for nimRumAudioSource
//
// Levels match libPrezoHalVar so the whole system reads the same:
//   0 = ERR, 1 = WARN, 2 = NOTE, 3 = DEBUG
//
// Default is WARN, not NOTE. These macros had no gating at all, so every note
// printed unconditionally: a measured S/PDIF source emitted a note per ALSA
// restart and per poll timeout, and TX's own journal was held to about an hour
// of history by a comparable flood. Devices retain only ~20 MB of journal, so
// an unconditional note costs the history you need to investigate anything.
// Notes are still one call to nimrum_log_set_level away.

#pragma once

#include <stdio.h>

#define NIMRUM_LOG_LEVEL_ERR 0
#define NIMRUM_LOG_LEVEL_WARN 1
#define NIMRUM_LOG_LEVEL_NOTE 2
#define NIMRUM_LOG_LEVEL_DEBUG 3

// Defined in nimRumAudioSource.c. Runtime, so a deployed device can be made
// verbose without a rebuild.
extern int nimrum_log_level;

void nimrum_log_set_level(int level);

#define nimrum_debug(...)                                                      \
  do {                                                                         \
    if (nimrum_log_level >= NIMRUM_LOG_LEVEL_DEBUG) {                          \
      printf("[nimRum DEBUG] " __VA_ARGS__);                                   \
    }                                                                          \
  } while (0)

#define nimrum_note(...)                                                       \
  do {                                                                         \
    if (nimrum_log_level >= NIMRUM_LOG_LEVEL_NOTE) {                           \
      printf("[nimRum] " __VA_ARGS__);                                         \
    }                                                                          \
  } while (0)

#define nimrum_warn(...)                                                       \
  do {                                                                         \
    if (nimrum_log_level >= NIMRUM_LOG_LEVEL_WARN) {                           \
      fprintf(stderr, "[nimRum WARN] " __VA_ARGS__);                           \
    }                                                                          \
  } while (0)

// err is never gated — if it happened, we want to know.
#define nimrum_err(...)                                                        \
  do {                                                                         \
    fprintf(stderr, "[nimRum ERR] " __VA_ARGS__);                              \
  } while (0)
