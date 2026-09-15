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

"""
Hardware and software detection for nimRum devices.

Detects board type (Raspberry Pi / Orange Pi), architecture, OS version,
and kernel info. Use as a single source of truth for platform-dependent
decisions throughout the codebase.

Usage:
    from nimRum import nimRumCheckHW
    hw = nimRumCheckHW.nimRumCheckHW()

    if hw.is_rpi:
        ...
    if hw.is_opi:
        ...
"""

import os
import platform
import re
import subprocess


# Board type constants
BOARD_RPI = "rpi"
BOARD_OPI = "opi"
BOARD_UNKNOWN = "unknown"


class nimRumCheckHW:
    def __init__(self, verbose: bool = False):
        # Architecture
        self.arch = platform.machine()       # aarch64, armv7l, armv6l
        self.kernel = platform.release()     # e.g. 6.18.34+rpt-rpi-v8
        self.hostname = platform.node()

        # OS info
        self.os_name = ""       # e.g. "Debian GNU/Linux"
        self.os_version = ""    # e.g. "13"
        self.os_codename = ""   # e.g. "trixie", "bullseye"
        self._detect_os()

        # Board info
        self.board_model = ""   # Full model string from device-tree
        self.board_type = BOARD_UNKNOWN
        self._detect_board()

        # ALSA info
        self.alsa_lib_version = ""   # e.g. "1.2.4", "1.2.14"
        self._detect_alsa()

        # Convenience flags
        self.is_rpi = (self.board_type == BOARD_RPI)
        self.is_opi = (self.board_type == BOARD_OPI)
        self.is_aarch64 = (self.arch == "aarch64")
        self.is_armv7 = (self.arch == "armv7l")
        self.is_armv6 = (self.arch == "armv6l")
        self.is_trixie = (self.os_codename == "trixie")

        if verbose:
            self.print_info()

    def _detect_os(self):
        """Parse /etc/os-release for distribution info."""
        try:
            with open("/etc/os-release", "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("NAME="):
                        self.os_name = line.split("=", 1)[1].strip('"')
                    elif line.startswith("VERSION_ID="):
                        self.os_version = line.split("=", 1)[1].strip('"')
                    elif line.startswith("VERSION_CODENAME="):
                        self.os_codename = line.split("=", 1)[1].strip('"')
        except FileNotFoundError:
            pass

    def _detect_board(self):
        """Detect board type from /proc/device-tree/model."""
        try:
            with open("/proc/device-tree/model", "r") as f:
                self.board_model = f.read().strip().rstrip("\x00")
        except FileNotFoundError:
            self.board_model = ""

        model_lower = self.board_model.lower()
        if "raspberry pi" in model_lower:
            self.board_type = BOARD_RPI
        elif "orange pi" in model_lower:
            self.board_type = BOARD_OPI

    def _detect_alsa(self):
        """Detect the ALSA library version.

        Which access method to use (mmap or rw/writei) is decided by the C
        library from this same version at runtime, and can be overridden per
        device with pcmMode in rxConfig.yaml. Nothing to decide here — this is
        reporting only.

        Background: ALSA >= 1.2.10 (Trixie) returns POLLERR from poll() and
        EBADFD from snd_pcm_start() on several pcm512x DACs, so those systems
        need the rw path. Older ALSA (Bullseye 1.2.4, Bookworm 1.2.8) works
        with mmap access.
        """
        self.alsa_lib_version = self._get_alsa_lib_version()

    def _get_alsa_lib_version(self) -> str:
        """Get ALSA library version string.

        Tries multiple approaches:
        1. Parse dpkg info for libasound2
        2. Run 'aplay --version' and parse output
        3. Read /proc/asound/version
        """
        # Try dpkg (most reliable for installed version)
        for pkg in ("libasound2t64", "libasound2"):
            ver = self._dpkg_version(pkg)
            if ver:
                return ver

        # Try aplay --version
        try:
            result = subprocess.run(
                ["aplay", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            # Output: "aplay: version 1.2.14 by Jaroslav Kysela..."
            match = re.search(r"version\s+([\d.]+)", result.stdout)
            if match:
                return match.group(1)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

        # Try /proc/asound/version
        try:
            with open("/proc/asound/version", "r") as f:
                # "Advanced Linux Sound Architecture Driver Version k6.18.34."
                # Not the lib version but better than nothing
                pass
        except FileNotFoundError:
            pass

        return ""

    def _dpkg_version(self, pkg_name: str) -> str:
        """Get version of an installed dpkg package, empty string if not found."""
        try:
            result = subprocess.run(
                ["dpkg-query", "-W", "-f", "${Version}", pkg_name],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                # Version like "1.2.14-1+rpt1" -> extract "1.2.14"
                ver_str = result.stdout.strip()
                match = re.match(r"([\d.]+)", ver_str)
                return match.group(1) if match else ver_str
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return ""

    def is_supported(self) -> bool:
        """Check if this platform is supported by nimRum."""
        if self.arch not in ("aarch64", "armv7l", "armv6l"):
            return False
        if self.board_type == BOARD_UNKNOWN:
            return False
        return True

    def print_info(self):
        """Print detected hardware/software info."""
        print("*" * 60)
        print("Detected HW:")
        print(f"  Board:    {self.board_model}")
        print(f"  Type:     {self.board_type}")
        print(f"  Arch:     {self.arch}")
        print(f"  Kernel:   {self.kernel}")
        print(f"  OS:       {self.os_name} {self.os_version} ({self.os_codename})")
        print(f"  Hostname: {self.hostname}")
        print(f"  ALSA lib: {self.alsa_lib_version or '(unknown)'}")
        print("*" * 60)


if __name__ == "__main__":
    hw = nimRumCheckHW(verbose=True)
    print()
    print(f"is_rpi={hw.is_rpi}, is_opi={hw.is_opi}, "
          f"is_aarch64={hw.is_aarch64}, is_armv7={hw.is_armv7}, "
          f"is_trixie={hw.is_trixie}")
    if not hw.is_supported():
        print("WARNING: Platform not supported")
