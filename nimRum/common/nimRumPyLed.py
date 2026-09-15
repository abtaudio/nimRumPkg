#!/usr/bin/env python3

# Copyright (C) 2021-2026 AbtAudio AB
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# As a special exception, you may link this program with the nimRumLib
# shared libraries (libNimRumTx_ct.so, libNimRumRx_ct.so) provided by
# AbtAudio AB without those libraries being subject to the GPL-3.0.

# RGB LED control with platform-specific backends:
#
#   - RPi aarch64 (Trixie): Direct /dev/mem register access.
#     Needed because kernel pinmux (HifiBerry I2S) blocks lgpio/gpiozero.
#     Active-high: OUTPUT+HIGH = on, INPUT = off.
#     BCM pins: R=12, G=13, B=26.
#
#   - RPi armv7l (older Raspbian): gpiozero, active-high.
#     BCM pins: R=12, G=13, B=26.
#
#   - Allwinner-based boards (aarch64): wiringOP-Python, INPUT/OUTPUT switching.
#     Common-anode: OUTPUT+LOW = on, INPUT = off.
#     WPI pins: R=24, G=26, B=25.

import os
import mmap
import struct

from nimRum.common.nimRumCheckHW import nimRumCheckHW, BOARD_RPI, BOARD_OPI

# BCM2835/2836/2837 GPIO register constants
_GPIO_LEN = 0xB4
_GPFSEL_OFFSET = 0x00
_GPSET_OFFSET = 0x1C
_GPCLR_OFFSET = 0x28


def _detect_gpio_base():
    """Detect GPIO base address from device-tree.

    The 'ranges' file maps child (SoC-internal) addresses to parent (CPU bus)
    addresses. Format varies by Pi model:
      Pi 3: child(4) parent(4) size(4) → parent at bytes 4:8
      Pi 4: child(4) parent_hi(4) parent_lo(4) size(4) → parent at bytes 8:12
    We detect Pi 4 by checking if bytes 4:8 are zero (high 32-bit of 64-bit addr).
    """
    try:
        with open('/proc/device-tree/soc/ranges', 'rb') as f:
            data = f.read(16)
            if len(data) >= 12:
                maybe_hi = struct.unpack('>I', data[4:8])[0]
                if maybe_hi == 0:
                    # Pi 4+: parent address is 64-bit, low word at bytes 8:12
                    base = struct.unpack('>I', data[8:12])[0]
                else:
                    # Pi 3: parent address is 32-bit at bytes 4:8
                    base = maybe_hi
                return base + 0x200000
    except (FileNotFoundError, struct.error):
        pass
    return 0x3F200000  # Default for Pi 3


class _DirectGPIO:
    """Direct /dev/mem GPIO register access (replicates wiringpi behavior)."""

    def __init__(self):
        gpio_base = _detect_gpio_base()
        self._fd = os.open('/dev/mem', os.O_RDWR | os.O_SYNC)
        self._map = mmap.mmap(self._fd, _GPIO_LEN, mmap.MAP_SHARED,
                              mmap.PROT_READ | mmap.PROT_WRITE,
                              offset=gpio_base)

    def _read_reg(self, offset):
        self._map.seek(offset)
        return struct.unpack('<I', self._map.read(4))[0]

    def _write_reg(self, offset, value):
        self._map.seek(offset)
        self._map.write(struct.pack('<I', value))

    def set_input(self, pin):
        """Set pin to INPUT mode (hi-Z)."""
        reg_offset = _GPFSEL_OFFSET + (pin // 10) * 4
        shift = (pin % 10) * 3
        val = self._read_reg(reg_offset)
        val &= ~(7 << shift)
        self._write_reg(reg_offset, val)

    def set_output_high(self, pin):
        """Set pin to OUTPUT driving HIGH."""
        reg_offset = _GPFSEL_OFFSET + (pin // 10) * 4
        shift = (pin % 10) * 3
        val = self._read_reg(reg_offset)
        val &= ~(7 << shift)
        val |= (1 << shift)
        self._write_reg(reg_offset, val)
        reg_idx = pin // 32
        bit = pin % 32
        self._write_reg(_GPSET_OFFSET + reg_idx * 4, 1 << bit)

    def set_output_low(self, pin):
        """Set pin to OUTPUT driving LOW."""
        reg_offset = _GPFSEL_OFFSET + (pin // 10) * 4
        shift = (pin % 10) * 3
        val = self._read_reg(reg_offset)
        val &= ~(7 << shift)
        val |= (1 << shift)
        self._write_reg(reg_offset, val)
        reg_idx = pin // 32
        bit = pin % 32
        self._write_reg(_GPCLR_OFFSET + reg_idx * 4, 1 << bit)

    def close(self):
        if self._map:
            self._map.close()
            self._map = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


# Backend identifiers
_BACKEND_DIRECT_RPI = "direct_rpi"
_BACKEND_GPIOZERO = "gpiozero"
_BACKEND_WIRINGOP = "wiringop"


class nimRumPyLed:
    def __init__(self, Rpin=None, Gpin=None, Bpin=None):

        self.ledAvailable = False
        self._gpio = None
        self._backend = None

        hw = nimRumCheckHW()

        if hw.is_rpi and hw.is_aarch64:
            # RPi on Trixie: direct register access, active-high
            if Rpin is None:
                Rpin, Gpin, Bpin = 12, 13, 26
            self._pins = (Rpin, Gpin, Bpin)
            self._init_direct_rpi()

        elif hw.is_opi:
            # Orange Pi: wiringOP-Python, common-anode (OUTPUT+LOW = on)
            self._wpi_pins = (24, 26, 25)  # R, G, B in WPI numbering
            self._pins = self._wpi_pins
            self._init_wiringop()

        elif hw.is_rpi:
            # RPi armv7l (older Raspbian): gpiozero, active-high
            if Rpin is None:
                Rpin, Gpin, Bpin = 12, 13, 26
            self._pins = (Rpin, Gpin, Bpin)
            self._init_gpiozero()

        else:
            print("nimRumPyLed NOT enabled: unsupported platform")
            return

        self.off()

    # --- Backend init ---

    def _init_direct_rpi(self):
        try:
            self._gpio = _DirectGPIO()
            self._backend = _BACKEND_DIRECT_RPI
            self.ledAvailable = True
        except Exception as e:
            print("nimRumPyLed NOT enabled (direct GPIO): " + str(e))

    def _init_wiringop(self):
        try:
            import wiringpi
            self._wp = wiringpi
            self._wp.wiringPiSetup()
            self._backend = _BACKEND_WIRINGOP
            self.ledAvailable = True
        except Exception as e:
            print("nimRumPyLed NOT enabled (wiringOP): " + str(e))

    def _init_gpiozero(self):
        try:
            from gpiozero import LED
            Rpin, Gpin, Bpin = self._pins
            self._ledR = LED(Rpin, active_high=True)
            self._ledG = LED(Gpin, active_high=True)
            self._ledB = LED(Bpin, active_high=True)
            self._backend = _BACKEND_GPIOZERO
            self.ledAvailable = True
        except Exception as e:
            print("nimRumPyLed NOT enabled (gpiozero): " + str(e))

    # --- Pin control helpers ---

    def _pin_on(self, idx):
        if self._backend == _BACKEND_DIRECT_RPI:
            self._gpio.set_output_high(self._pins[idx])
        elif self._backend == _BACKEND_WIRINGOP:
            self._wp.pinMode(self._pins[idx], self._wp.GPIO.OUTPUT)
            self._wp.digitalWrite(self._pins[idx], self._wp.GPIO.LOW)
        elif self._backend == _BACKEND_GPIOZERO:
            [self._ledR, self._ledG, self._ledB][idx].on()

    def _pin_off(self, idx):
        if self._backend == _BACKEND_DIRECT_RPI:
            self._gpio.set_input(self._pins[idx])
        elif self._backend == _BACKEND_WIRINGOP:
            self._wp.pinMode(self._pins[idx], self._wp.GPIO.INPUT)
        elif self._backend == _BACKEND_GPIOZERO:
            [self._ledR, self._ledG, self._ledB][idx].off()

    # --- Public API ---

    def off(self):
        if not self.ledAvailable:
            return
        self._pin_off(0)
        self._pin_off(1)
        self._pin_off(2)

    def red(self):
        if not self.ledAvailable:
            return
        self._pin_on(0)
        self._pin_off(1)
        self._pin_off(2)

    def green(self):
        if not self.ledAvailable:
            return
        self._pin_off(0)
        self._pin_on(1)
        self._pin_off(2)

    def blue(self):
        if not self.ledAvailable:
            return
        self._pin_off(0)
        self._pin_off(1)
        self._pin_on(2)

    def white(self):
        if not self.ledAvailable:
            return
        self._pin_on(0)
        self._pin_on(1)
        self._pin_on(2)

    def close(self):
        """Release GPIO resources."""
        if self._gpio is not None:
            self.off()
            self._gpio.close()
            self._gpio = None
        self.ledAvailable = False
