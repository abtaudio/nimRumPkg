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
#include <ctype.h>

#include "nimRumDSP_config.h"
#include "nimRumDSP_biquad.h"

#define MAX_LINE_LEN 256

// Trim leading whitespace, return pointer into same buffer
static char *ltrim(char *s) {
    while (*s && isspace((unsigned char)*s)) {
        s++;
    }
    return s;
}

// Trim trailing whitespace/newline in-place
static void rtrim(char *s) {
    int len = (int)strlen(s);
    while (len > 0 && isspace((unsigned char)s[len - 1])) {
        s[--len] = '\0';
    }
}

// Parse "key: value" — returns pointer to value (trimmed), or NULL
static char *parse_kv(char *line, const char *key) {
    char *trimmed = ltrim(line);
    int key_len = (int)strlen(key);

    if (strncmp(trimmed, key, key_len) != 0) {
        return NULL;
    }
    char *after = trimmed + key_len;
    // Expect ':' (possibly with spaces around it)
    after = ltrim(after);
    if (*after != ':') {
        return NULL;
    }
    after++;
    after = ltrim(after);
    rtrim(after);
    return after;
}

// Convert filter type string to enum
static nim_rum_dsp_filter_type_t parse_filter_type(const char *s) {
    if (strcasecmp(s, "Peaking") == 0) {
        return NIM_RUM_DSP_FILTER_PEAKING;
    }
    if (strcasecmp(s, "Lowshelf") == 0) {
        return NIM_RUM_DSP_FILTER_LOWSHELF;
    }
    if (strcasecmp(s, "Highshelf") == 0) {
        return NIM_RUM_DSP_FILTER_HIGHSHELF;
    }
    if (strcasecmp(s, "Lowpass") == 0) {
        return NIM_RUM_DSP_FILTER_LOWPASS;
    }
    if (strcasecmp(s, "Highpass") == 0) {
        return NIM_RUM_DSP_FILTER_HIGHPASS;
    }
    if (strcasecmp(s, "Raw") == 0) {
        return NIM_RUM_DSP_FILTER_RAW;
    }
    return NIM_RUM_DSP_FILTER_PEAKING;  // Default
}

int nimRumDSP_configLoad(nim_rum_dsp_filter_bank_t *fb,
                          const char *config_path) {
    if (fb == NULL || config_path == NULL) {
        return -1;
    }

    FILE *f = fopen(config_path, "r");
    if (f == NULL) {
        return -1;
    }

    // Clear existing bands
    nimRumDSP_biquadClear(fb);

    char line[MAX_LINE_LEN];
    char *val;

    // State machine for parsing filter list items
    int in_filters = 0;
    int has_type = 0;
    nim_rum_dsp_filter_type_t cur_type = NIM_RUM_DSP_FILTER_PEAKING;
    double cur_freq = 0.0;
    double cur_gain = 0.0;
    double cur_q = 1.0;
    // Raw coefficient fields
    double cur_b0 = 1.0, cur_b1 = 0.0, cur_b2 = 0.0;
    double cur_a1 = 0.0, cur_a2 = 0.0;

    while (fgets(line, sizeof(line), f) != NULL) {
        rtrim(line);
        char *trimmed = ltrim(line);

        // Skip empty lines and comments
        if (*trimmed == '\0' || *trimmed == '#') {
            continue;
        }

        // Check for top-level "enabled:" key
        val = parse_kv(line, "enabled");
        if (val != NULL) {
            if (strcasecmp(val, "true") == 0 || strcmp(val, "1") == 0) {
                fb->enabled = 1;
            } else {
                fb->enabled = 0;
            }
            continue;
        }

        // Check for "filters:" section start
        if (strstr(trimmed, "filters:") != NULL) {
            in_filters = 1;
            continue;
        }

        // Inside filters section — look for list items
        if (in_filters) {
            // New list item starts with "- "
            if (trimmed[0] == '-') {
                // Save previous band if we had one
                if (has_type) {
                    if (cur_type == NIM_RUM_DSP_FILTER_RAW) {
                        nimRumDSP_biquadAddRawBand(fb, cur_b0, cur_b1,
                                                   cur_b2, cur_a1, cur_a2);
                    } else {
                        nimRumDSP_biquadAddBand(fb, cur_type, cur_freq,
                                                cur_gain, cur_q);
                    }
                }
                // Reset for new band
                has_type = 0;
                cur_type = NIM_RUM_DSP_FILTER_PEAKING;
                cur_freq = 0.0;
                cur_gain = 0.0;
                cur_q = 1.0;
                cur_b0 = 1.0; cur_b1 = 0.0; cur_b2 = 0.0;
                cur_a1 = 0.0; cur_a2 = 0.0;

                // Parse the rest of this line (after "- ")
                char *rest = ltrim(trimmed + 1);
                // Could have "type: Peaking" on same line as "-"
                val = parse_kv(rest, "type");
                if (val != NULL) {
                    cur_type = parse_filter_type(val);
                    has_type = 1;
                }
                continue;
            }

            // Continuation of a list item (indented key: value)
            val = parse_kv(trimmed, "type");
            if (val != NULL) {
                cur_type = parse_filter_type(val);
                has_type = 1;
                continue;
            }

            val = parse_kv(trimmed, "freq");
            if (val != NULL) {
                cur_freq = atof(val);
                continue;
            }

            val = parse_kv(trimmed, "gain");
            if (val != NULL) {
                cur_gain = atof(val);
                continue;
            }

            val = parse_kv(trimmed, "q");
            if (val != NULL) {
                cur_q = atof(val);
                continue;
            }

            val = parse_kv(trimmed, "b0");
            if (val != NULL) { cur_b0 = atof(val); continue; }
            val = parse_kv(trimmed, "b1");
            if (val != NULL) { cur_b1 = atof(val); continue; }
            val = parse_kv(trimmed, "b2");
            if (val != NULL) { cur_b2 = atof(val); continue; }
            val = parse_kv(trimmed, "a1");
            if (val != NULL) { cur_a1 = atof(val); continue; }
            val = parse_kv(trimmed, "a2");
            if (val != NULL) { cur_a2 = atof(val); continue; }

            // If we hit a non-indented line, we're out of filters section
            if (line[0] != ' ' && line[0] != '\t' && line[0] != '-') {
                in_filters = 0;
            }
        }
    }

    // Don't forget the last band
    if (has_type) {
        if (cur_type == NIM_RUM_DSP_FILTER_RAW) {
            nimRumDSP_biquadAddRawBand(fb, cur_b0, cur_b1, cur_b2,
                                        cur_a1, cur_a2);
        } else {
            nimRumDSP_biquadAddBand(fb, cur_type, cur_freq, cur_gain, cur_q);
        }
    }

    fclose(f);
    return 0;
}
