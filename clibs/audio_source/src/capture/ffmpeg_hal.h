/*
 * Copyright (c) 2012 Stefano Sabatini
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
 * THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

/**
 * @file
 * Demuxing and decoding example.
 *
 * Show how to use the libavformat and libavcodec API to demux and
 * decode audio and video data.
 * @example demuxing_decoding.c
 */

#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/avutil.h>
#include <libavutil/timestamp.h>

#include "decode_buf_in.h"
#include "decode_buf_out.h"

typedef union {
  float F;
  uint8_t I[4];
} ffmpeg_float_int_u_t;

typedef struct {

  int streamIsOpen;
  int ffmpeg_read_fail_cnt;

  AVFormatContext *fmt_ctx;
  AVCodecContext *audio_dec_ctx;
  AVStream *audio_stream;

  int audio_stream_idx;
  AVFrame *frame;
  AVPacket *pkt;

  // Format
  int channels;
  uint64_t layout;
  int rate;
  char layout_name[16];  // e.g. "stereo", "5.1", "7.1" from av_channel_layout_describe

  // Proper closing
  int fmt_ctx_opened;
  int fmt_ctx_allocated;

  // Input buffer
  decode_buf_in_t bufIn;

  // File buffer
  unsigned char *avio_ctx_buffer;
  int avio_ctx_buffer_size;
  AVIOContext *avio_ctx;

  // Buffer output
  ffmpeg_float_int_u_t typeConv;
  decode_buf_out_t *extBufOut;

} ffmpeg_hal_t;

int _ffmpeg_output_audio_frame(ffmpeg_hal_t *f, AVFrame *frame);

int _ffmpeg_decode_packet(ffmpeg_hal_t *f, AVCodecContext *dec,
                          const AVPacket *pkt);

int _ffmpeg_open_codec_context(int *stream_idx, AVCodecContext **dec_ctx,
                               AVFormatContext *fmt_ctx, enum AVMediaType type);

int _ffmpeg_get_format_from_sample_fmt(const char **fmt,
                                       enum AVSampleFormat sample_fmt);

int _ffmpeg_open_stream(ffmpeg_hal_t *f);

void _ffmpeg_close_stream(ffmpeg_hal_t *f);

int ffmpeg_hal_read(ffmpeg_hal_t *f, int *channels, uint64_t *layout, int *rate);

int ffmpeg_hal_init(ffmpeg_hal_t *f, size_t bufInSize,
                    decode_buf_out_t *extBufOut);
