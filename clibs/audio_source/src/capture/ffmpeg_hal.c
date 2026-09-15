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

/*
Initial copy of: demuxing_decoding.c
*/

#include "ffmpeg_hal.h"

/**
 * @file
 * Demuxing and decoding example.
 *
 * Show how to use the libavformat and libavcodec API to demux and
 * decode audio and video data.
 * @example demuxing_decoding.c
 */

int _ffmpeg_output_audio_frame(ffmpeg_hal_t *f, AVFrame *frame) {

  int channels = f->frame->ch_layout.nb_channels;
  int numOfSamplesIn = frame->nb_samples;

  int inCnt = 0;
  int32_t fIn[channels];

  // dec->decoded_frame->format:
  // 1: s16
  // 8: fltp
  // Expects fltp format: -1.0, 1.0
  // Converts to: Signed 16 bits contained in Signed 32 bits
  if (f->frame->format == 8) { // fltp
    int ch = 0;

    while (inCnt < numOfSamplesIn) {
      for (ch = 0; ch < channels; ch++) {
        f->typeConv.I[0] = f->frame->data[ch][4 * inCnt + 0];
        f->typeConv.I[1] = f->frame->data[ch][4 * inCnt + 1];
        f->typeConv.I[2] = f->frame->data[ch][4 * inCnt + 2];
        f->typeConv.I[3] = f->frame->data[ch][4 * inCnt + 3];
        int32_t sI = (int32_t)round(f->typeConv.F * (INT16_MAX - 1));
        fIn[ch] = sI;
      }

      inCnt += decode_buf_out_add_frame(f->extBufOut, channels, fIn);
    }
  }

  else {
    printf("FFMPEG: ERROR, Codec resulted in unknown format: %i \n",
           f->frame->format);
    return -1;
  }

  return inCnt;
}

int _ffmpeg_decode_packet(ffmpeg_hal_t *f, AVCodecContext *dec,
                          const AVPacket *pkt) {
  int ret = 0;
  int consumedFrames = 0;

  // submit the packet to the decoder
  ret = avcodec_send_packet(dec, pkt);
  if (ret < 0) {
    printf("FFMPEG: ERROR submitting a packet for decoding (%s)\n",
           av_err2str(ret));
    return -1;
  }

  // get all the available frames from the decoder
  while (1) {

    ret = avcodec_receive_frame(dec, f->frame);
    if (ret < 0) {
      // those two return values are special and mean there is no output
      // frame available, but there were no errors during decoding
      if (ret == AVERROR_EOF || ret == AVERROR(EAGAIN)) {
        // pass
      } else {
        printf("FFMPEG: ERROR during decoding (%s)\n", av_err2str(ret));
        consumedFrames = -1; // Force decoder re-init
      }
      break;
    } else {
      f->layout = f->frame->ch_layout.order;         // channel_layout;
      f->channels = f->frame->ch_layout.nb_channels; // channels;

      // Get human-readable layout name (e.g. "stereo", "5.1", "5.1(side)")
      av_channel_layout_describe(&f->frame->ch_layout,
                                 f->layout_name, sizeof(f->layout_name));

      int cf = _ffmpeg_output_audio_frame(f, f->frame);
      if (cf >= 0)
        consumedFrames += cf;
      else {
        consumedFrames = -1;
        break;
      }

      av_frame_unref(f->frame);
    }
  }

  return consumedFrames;
}

int _ffmpeg_open_codec_context(int *stream_idx, AVCodecContext **dec_ctx,
                               AVFormatContext *fmt_ctx,
                               enum AVMediaType type) {
  int ret, stream_index;
  AVStream *st;
  const AVCodec *dec = NULL;

  ret = av_find_best_stream(fmt_ctx, type, -1, -1, NULL, 0);
  if (ret < 0) {
    printf("FFMPEG: ERROR, Could not find %s stream \n",
           av_get_media_type_string(type));
    return ret;
  } else {
    stream_index = ret;
    st = fmt_ctx->streams[stream_index];

    /* find decoder for the stream */
    dec = avcodec_find_decoder(st->codecpar->codec_id);
    if (dec == NULL) {
      printf("FFMPEG: ERROR, Failed to find %s codec\n",
             av_get_media_type_string(type));
      return AVERROR(EINVAL);
    }

    /* Allocate a codec context for the decoder */
    *dec_ctx = avcodec_alloc_context3(dec);
    if (!*dec_ctx) {
      printf("FFMPEG: ERROR, Failed to allocate the %s codec context\n",
             av_get_media_type_string(type));
      return AVERROR(ENOMEM);
    }

    /* Copy codec parameters from input stream to output codec context */
    if ((ret = avcodec_parameters_to_context(*dec_ctx, st->codecpar)) < 0) {
      printf("FFMPEG: ERROR, Failed to copy %s codec parameters to decoder "
             "context\n",
             av_get_media_type_string(type));
      return ret;
    }

    /* Init the decoders */
    if ((ret = avcodec_open2(*dec_ctx, dec, NULL)) < 0) {
      printf("FFMPEG: ERROR, Failed to open %s codec\n",
             av_get_media_type_string(type));
      return ret;
    }
    *stream_idx = stream_index;
  }

  return 0;
}

int _ffmpeg_get_format_from_sample_fmt(const char **fmt,
                                       enum AVSampleFormat sample_fmt) {
  int i;
  struct sample_fmt_entry {
    enum AVSampleFormat sample_fmt;
    const char *fmt_be, *fmt_le;
  } sample_fmt_entries[] = {
      {AV_SAMPLE_FMT_U8, "u8", "u8"},
      {AV_SAMPLE_FMT_S16, "s16be", "s16le"},
      {AV_SAMPLE_FMT_S32, "s32be", "s32le"},
      {AV_SAMPLE_FMT_FLT, "f32be", "f32le"},
      {AV_SAMPLE_FMT_DBL, "f64be", "f64le"},
  };
  *fmt = NULL;

  for (i = 0; i < (int)FF_ARRAY_ELEMS(sample_fmt_entries); i++) {
    struct sample_fmt_entry *entry = &sample_fmt_entries[i];
    if (sample_fmt == entry->sample_fmt) {
      *fmt = AV_NE(entry->fmt_be, entry->fmt_le);
      return 0;
    }
  }

  printf("FFMPEG: ERROR, sample format %s is not supported as output format\n",
         av_get_sample_fmt_name(sample_fmt));
  return -1;
}

int _ffmpeg_open_stream(ffmpeg_hal_t *f) {

  int ret = 0;
  f->audio_stream_idx = -1;

  decode_buf_in_reset(&f->bufIn);

  /* Map input buffer as source */
  f->avio_ctx_buffer = (unsigned char *)av_malloc(f->avio_ctx_buffer_size);
  if (f->avio_ctx_buffer == NULL) {
    printf("FFMPEG: ERROR, av_malloc failed \n");
    _ffmpeg_close_stream(f);
    return -1;
  }

  int writeFlag = 0; // Read only buffer
  f->avio_ctx =
      avio_alloc_context(f->avio_ctx_buffer, f->avio_ctx_buffer_size, writeFlag,
                         &f->bufIn, &decode_buf_in_read, NULL, NULL);
  if (f->avio_ctx == NULL) {
    printf("FFMPEG: ERROR, avio_alloc_context failed \n");
    _ffmpeg_close_stream(f);
    return -1;
  }

  f->fmt_ctx = avformat_alloc_context();
  if (!f->fmt_ctx) {
    printf("FFMPEG: ERROR, avformat_alloc_context failed \n");
    _ffmpeg_close_stream(f);
    return -1;
  }
  f->fmt_ctx_allocated = 1;

  f->fmt_ctx->pb = f->avio_ctx;

  /* Force spdif demuxer — ffmpeg 7.x no longer auto-detects from buffer */
  const AVInputFormat *spdif_fmt = av_find_input_format("spdif");

  /* "open input", and allocate format context */
  if ((ret = avformat_open_input(&f->fmt_ctx, NULL, spdif_fmt, NULL)) < 0) {
    printf("FFMPEG: ERROR, avformat_open_input failed: %s(%i) \n",
           av_err2str(ret), ret);
    _ffmpeg_close_stream(f);
    return -1;
  }
  f->fmt_ctx_opened = 1;

  /* Help ffmpeg 7.x find stream info from limited buffer data */
  f->fmt_ctx->probesize = 32768;
  f->fmt_ctx->max_analyze_duration = 500000;  /* 0.5 seconds */

  /* retrieve stream information */
  if ((ret = avformat_find_stream_info(f->fmt_ctx, NULL)) < 0) {
    printf("FFMPEG: ERROR, Could not find stream information\n");
    _ffmpeg_close_stream(f);
    return -1;
  }

  if (_ffmpeg_open_codec_context(&f->audio_stream_idx, &f->audio_dec_ctx,
                                 f->fmt_ctx, AVMEDIA_TYPE_AUDIO) < 0) {
    printf("FFMPEG: ERROR, _ffmpeg_open_codec_context failed\n");
    _ffmpeg_close_stream(f);
    return -1;
  }

  f->audio_stream = f->fmt_ctx->streams[f->audio_stream_idx];

  /* dump input information to stdout (err?) */
  av_dump_format(f->fmt_ctx, 0, "ffmpegHal", 0);

  if (!f->audio_stream) {
    printf("FFMPEG: ERROR, Could not find audio in the input, aborting\n");
    _ffmpeg_close_stream(f);
    return -1;
  }

  f->frame = av_frame_alloc();
  if (!f->frame) {
    printf("FFMPEG: ERROR, Could not allocate frame: %i \n", AVERROR(ENOMEM));
    _ffmpeg_close_stream(f);
    return -1;
  }

  f->pkt = av_packet_alloc();
  if (!f->pkt) {
    printf("FFMPEG: ERROR, Could not allocate packet: %i \n", AVERROR(ENOMEM));
    _ffmpeg_close_stream(f);
    return -1;
  }

  if (f->audio_stream) {
    enum AVSampleFormat sfmt = f->audio_dec_ctx->sample_fmt;

    // Overwritten later by decoded frame info
    f->channels =
        f->audio_dec_ctx->ch_layout.nb_channels;
    f->rate = f->audio_dec_ctx->sample_rate;

    if (!av_sample_fmt_is_planar(sfmt)) {
      printf("FFMPEG: ERROR, Sample decoder produced is NOT planar \n");
      _ffmpeg_close_stream(f);
      return -1;
    }

  } else {
    f->channels = 0;
    f->rate = 0;
    printf("FFMPEG: ERROR, No audio stream found \n");
    _ffmpeg_close_stream(f);
    return -1;
  }

  f->streamIsOpen = 1;

  return 0;
}

void _ffmpeg_close_stream(ffmpeg_hal_t *f) {

  if (f->audio_dec_ctx != NULL)
    avcodec_free_context(&f->audio_dec_ctx);

  if (f->pkt != NULL)
    av_packet_free(&f->pkt);

  if (f->frame != NULL)
    av_frame_free(&f->frame);

  if (f->fmt_ctx_opened == 1)
    avformat_close_input(&f->fmt_ctx);
  f->fmt_ctx_opened = 0;

  if (f->fmt_ctx_allocated == 1)
    avformat_free_context(f->fmt_ctx);
  f->fmt_ctx_allocated = 0;

  // f->avio_ctx->buffer = avio_ctx_buffer
  if (f->avio_ctx)
    av_freep(&f->avio_ctx->buffer);
  avio_context_free(&f->avio_ctx);

  decode_buf_in_close(&f->bufIn);

  f->streamIsOpen = 0;
}

int ffmpeg_hal_read(ffmpeg_hal_t *f, int *channels, uint64_t *layout, int *rate) {
  int consumedFrames = 0;

  if (f->streamIsOpen == 0) {
    if (_ffmpeg_open_stream(f) < 0) {
      return -1;
    };
    return 0;
  }

  /* read frames from the file */
  while (1) {
    int ret = 0;

    f->ffmpeg_read_fail_cnt++;
    ret = av_read_frame(f->fmt_ctx, f->pkt);
    if (ret < 0) {

      if ((consumedFrames == 0) && (f->ffmpeg_read_fail_cnt > 5)) {
        printf("FFMPEG: Got: %s \n", av_err2str(ret));
        consumedFrames = -1; // Really EOF, force decoder re-init
        break;
      }

      // Normal exit
      if (ret == AVERROR_EOF) {
        break;
      }

      printf("FFMPEG: ERROR, av_read_frame got: %s \n", av_err2str(ret));
      consumedFrames = -1; // Force decoder re-init
      break;

    } else {
      // check if the packet belongs to a stream we are interested in,
      // otherwise skip it
      if (f->pkt->stream_index == f->audio_stream_idx) {
        ret = _ffmpeg_decode_packet(f, f->audio_dec_ctx, f->pkt);
        consumedFrames += ret;
      }

      av_packet_unref(f->pkt);

      if (ret < 0) {
        consumedFrames = -1; // Force decoder re-init
        break;
      }
    }
  }

  if (consumedFrames < 0) {
    _ffmpeg_close_stream(f);
    return -1;
  }

  if (consumedFrames > 0) {
    f->ffmpeg_read_fail_cnt = 0;
  }

  *channels = f->channels;
  *layout = f->layout;
  *rate = f->rate;

  return consumedFrames;
}

int ffmpeg_hal_init(ffmpeg_hal_t *f, size_t bufInSize,
                    decode_buf_out_t *extBufOut) {

  memset(f, 0, sizeof(ffmpeg_hal_t));

  f->extBufOut = extBufOut;
  f->avio_ctx_buffer_size = 4096;

  int ret = decode_buf_in_init(&f->bufIn, bufInSize);

  return ret;
}
