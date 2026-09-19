#!/usr/bin/env python3
"""
nimRumAudioSource — Standalone audio source feeder.

Thin Python wrapper: reads config, handles GPIO UI, delegates
capture+send to C library (nimRumAudioSource_ct.so via ctypes).
"""

import signal
import time
import os
import logging
import argparse

import yaml

from nimRum.audio_source import audio_source_ctypes as srcLib
from nimRum.common import nimRumConfigDoc

# Optional: rotary encoder + button (RPi GPIO)
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)


class NimRumAudioSource:
    def __init__(self, config):
        self.cfg = config
        self.running = True
        self._volume_steps = config.get('volumeDb', 0)
        self._streaming = True

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        self._setup_hardware_ui()

    def _shutdown(self, signum, frame):
        log.info("Shutting down (signal %d)", signum)
        self.running = False

    def _setup_hardware_ui(self):
        """Init rotary encoder, button, and LED if GPIO available."""
        self._led = None
        self._blink_thread = None

        if not GPIO_AVAILABLE:
            return

        GPIO.setmode(GPIO.BCM)

        # LED (same pins as common lib nimRumPyLed)
        led_cfg = self.cfg.get('led', {})
        if led_cfg.get('enabled', False):
            self._led_pin = led_cfg.get('gpioBlue', 26)
            try:
                GPIO.setup(self._led_pin, GPIO.OUT)
                GPIO.output(self._led_pin, GPIO.LOW)  # off (active high)
                self._start_blink_thread()
            except Exception as e:
                log.warning("LED setup failed: %s", e)

        # Button (toggle stream on/off, active low with internal pull-up)
        btn_cfg = self.cfg.get('button', {})
        if btn_cfg.get('enabled', False):
            gpio_btn = btn_cfg.get('gpio', 16)
            try:
                GPIO.setup(gpio_btn, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                GPIO.add_event_detect(
                    gpio_btn, GPIO.FALLING,
                    callback=self._on_button_press, bouncetime=300
                )
                log.info("Button on GPIO %d (pull-up, active low, 300ms debounce)", gpio_btn)
            except Exception as e:
                log.warning("Button setup failed: %s", e)

        # Rotary encoder (volume)
        enc_cfg = self.cfg.get('rotaryEncoder', {})
        if enc_cfg.get('enabled', False):
            from nimRum.common.nimRumRotaryEnc import nimRumRotaryEnc

            gpio_a = enc_cfg.get('gpioA', 17)
            gpio_b = enc_cfg.get('gpioB', 27)
            self._encoder = nimRumRotaryEnc(
                leftPin=gpio_a, rightPin=gpio_b, callback=self._on_volume_change
            )
            self._encoder.value = self._volume_steps
            log.info("Rotary encoder on GPIO %d/%d", gpio_a, gpio_b)

    def _start_blink_thread(self):
        """Blink blue LED briefly once per second while streaming."""
        import threading

        def _blink_loop():
            while self.running:
                # Check actual streaming state from C (controlled by TX replies)
                is_streaming = srcLib.c_nimRumAudioSource_isStreaming()
                self._streaming = bool(is_streaming)
                if self._streaming:
                    GPIO.output(self._led_pin, GPIO.HIGH)  # on
                    time.sleep(0.05)
                    GPIO.output(self._led_pin, GPIO.LOW)  # off
                    time.sleep(0.95)
                else:
                    GPIO.output(self._led_pin, GPIO.LOW)  # off
                    time.sleep(0.2)

        self._blink_thread = threading.Thread(target=_blink_loop, daemon=True)
        self._blink_thread.start()

    def _on_volume_change(self, value, direction):
        self._volume_steps = value
        db = max(-6, min(6, value))
        srcLib.c_nimRumAudioSource_setVolumeDb(db)
        log.info("Volume: %d dB", db)

    def _on_button_press(self, channel):
        self._streaming = not self._streaming
        if self._streaming:
            srcLib.c_nimRumAudioSource_requestActive()
            log.info("Button: requesting active (stream ON)")
        else:
            srcLib.c_nimRumAudioSource_requestDeselect()
            log.info("Button: requesting deselect (stream OFF)")

    def run(self):
        cfg = self.cfg
        target_host = cfg.get('targetHost', '')
        alsa_device = cfg.get('alsaDevice', '')
        mode_str = cfg.get('captureMode', 'spdif')
        if mode_str == 'file':
            capture_mode = 2
        else:
            capture_mode = 0  # spdif (default)
        file_path = cfg.get('filePath', '')

        log.info("Source (%s) mode=%s device=%s target=%s",
                 cfg.get('sourceName', ''),
                 mode_str, alsa_device if capture_mode != 2 else file_path,
                 target_host if target_host else "auto")

        # Verbosity of the C side, same names and values as rx/txConfig.yaml.
        # Defaults to note (matching RX/TX); warn stays the safe fallback for a
        # malformed value. Set before start so startup notes are gated too.
        levels = {"err": 0, "warn": 1, "note": 2, "debug": 3}
        print_level = cfg.get('printLevel', 'note')
        if isinstance(print_level, str):
            resolved = levels.get(print_level.strip().lower())
            if resolved is None:
                log.warning("printLevel %r unknown, using warn", print_level)
                resolved = levels["warn"]
        elif isinstance(print_level, int) and not isinstance(print_level, bool) \
                and 0 <= print_level <= 3:
            resolved = print_level
        else:
            log.warning("printLevel %r invalid, using warn", print_level)
            resolved = levels["warn"]
        srcLib.c_nimRumAudioSource_setPrintLevel(resolved)

        # Suggested pre-fill depth for TX's input FIFO. Only this side knows
        # whether the hop to TX is loopback or Wi-Fi. 0 means "TX decides", and
        # TX clamps and logs whatever it is given, so no range check here beyond
        # rejecting a non-integer.
        fifo_target = cfg.get('txFifoTarget', 0)
        if not isinstance(fifo_target, int) or isinstance(fifo_target, bool) \
                or fifo_target < 0:
            log.warning("txFifoTarget %r invalid, letting TX decide", fifo_target)
            fifo_target = 0
        srcLib.c_nimRumAudioSource_setFifoTargetSuggestion(fifo_target)

        res = srcLib.c_nimRumAudioSource_start(
            cfg.get('sourceName', ''), target_host,
            alsa_device, capture_mode, file_path)
        if res != 0:
            log.error("Failed to start audio source")
            return

        # Set initial volume
        srcLib.c_nimRumAudioSource_setVolumeDb(self._volume_steps)

        # Wait until stopped
        last_status = time.time()
        while self.running and srcLib.c_nimRumAudioSource_isRunning():
            time.sleep(0.1)
            now = time.time()
            if now - last_status >= 5.0:
                last_status = now
                st = srcLib.c_nimRumAudioSource_getStatus()
                log.info("ppm=%.2f seq=%d ch=%d streaming=%s",
                         st['ppm'], st['seqNum'], st['channels'],
                         "ON" if self._streaming else "OFF")

        srcLib.c_nimRumAudioSource_stop()
        self._save_config()
        log.info("Done.")

    def _save_config(self):
        """Write current settings to .last file."""
        helpStr = nimRumConfigDoc.as_yaml_comments("audioSourceConfig")
        cfg_out = {
            'sourceName': self.cfg.get('sourceName', ''),
            'alsaDevice': self.cfg.get('alsaDevice', ''),
            'pcmSampleRate': self.cfg.get('pcmSampleRate', 48000),
            'captureMode': self.cfg.get('captureMode', 'spdif'),
            'targetHost': self.cfg.get('targetHost', ''),
            'channelLayoutFallback': self.cfg.get('channelLayoutFallback', 'stereo'),
            'txFifoTarget': self.cfg.get('txFifoTarget', 0),
            'volumeDb': max(-6, min(6, self._volume_steps)),
            'rotaryEncoder': {
                'enabled': self.cfg.get('rotaryEncoder', {}).get('enabled', False),
                'gpioA': self.cfg.get('rotaryEncoder', {}).get('gpioA', 17),
                'gpioB': self.cfg.get('rotaryEncoder', {}).get('gpioB', 27),
            },
            'button': {
                'enabled': self.cfg.get('button', {}).get('enabled', False),
                'gpio': self.cfg.get('button', {}).get('gpio', 16),
            },
            'led': {
                'enabled': self.cfg.get('led', {}).get('enabled', False),
                'gpioBlue': self.cfg.get('led', {}).get('gpioBlue', 26),
            },
        }

        base = self.cfg.get('_configPath', 'audioSourceConfig.yaml')
        path = os.path.splitext(base)[0] + '.last'
        try:
            with open(path, 'w') as f:
                f.write(helpStr)
                yaml.dump(cfg_out, f, default_flow_style=False)
            log.info("Config saved to %s", path)
        except Exception as e:
            log.warning("Failed to save config: %s", e)


def load_config(path):
    with open(path, 'r') as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description='nimRumAudioSource feeder')
    parser.add_argument('-c', '--config', default='audioSourceConfig.yaml',
                        help='Path to YAML config file')
    parser.add_argument('--device', help='Override alsaDevice')
    parser.add_argument('--host', help='Override targetHost (empty=auto-discover)')
    parser.add_argument('--file', help='Play audio file (sets captureMode=file)')
    args = parser.parse_args()

    # Defaults
    cfg = {
        'sourceName': '',
        'alsaDevice': '',
        'pcmSampleRate': 48000,
        'captureMode': 'spdif',
        'filePath': '',
        'targetHost': '',
        'channelLayoutFallback': 'stereo',
        'txFifoTarget': 0,
    }

    # Load YAML if exists
    if os.path.isfile(args.config):
        cfg.update(load_config(args.config))

    cfg['_configPath'] = args.config

    # CLI overrides
    if args.device:
        cfg['alsaDevice'] = args.device
    if args.host:
        cfg['targetHost'] = args.host
    if args.file:
        cfg['captureMode'] = 'file'
        cfg['filePath'] = args.file

    # Default sourceName: hostname + captureMode
    import socket
    hostname = socket.gethostname()

    if not cfg.get('sourceName'):
        cfg['sourceName'] = hostname + '_' + cfg['captureMode']

    src = NimRumAudioSource(cfg)
    src.run()


if __name__ == '__main__':
    main()
