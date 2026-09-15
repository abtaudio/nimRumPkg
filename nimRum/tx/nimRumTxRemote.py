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

# So many hours wasted on various internet tips.
# Ended up using this one: https://forums.raspberrypi.com/viewtopic.php?t=235256

# cat /etc/os-release -> bullseye
#
# sudo apt update
# sudo apt install lirc
#
# Edit /etc/lirc/lirc_options.conf as follows by changing these two lines:
# driver = default
# device = /dev/lirc0
#
# sudo nano /boot/config.txt
# dtoverlay=gpio-ir,gpio_pin=17
#
# pip3 install lirc
# sudo nano  /usr/lib/arm-linux-gnueabihf/python3.9/site-packages/lirc/paths.py
# Comment out
# #   try:
# #       os.unlink(os.path.join(HERE, '_client.so'))
# #   except PermissionError:
# #       pass
#
# sudo systemctl stop lircd.service
# sudo systemctl start lircd.service
# sudo systemctl status lircd.service
# sudo reboot

try:
    import lirc
except BaseException as e:
    print('Loading Python package lirc for remote control failed') #: ' + str(e))

class nimRumTxRemote:
    def __init__(self, remote="", up="", down="", mute="", timeError=""):

        self.remoteModel=remote
        self.keyUp=up
        self.keyDown=down
        self.keyMute=mute
        self.keyTimeError=timeError

        self.lircAvailable = False
        try:
            self.lircConn = lirc.LircdConnection(timeout=0.0001)
            self.lircConn.connect()
            self.lircAvailable = True
        except:
            print("nimRumTxRemote NOT enabled, reading " + __file__ + " migth help")

    def lircCheck(self):
        if not self.lircAvailable:
            return None

        try:
            keypress = self.lircConn.readline()
        except:
            return None

        if keypress != "" and keypress != None:

            data = keypress.split()
            # hexcode = data[0]
            repeat = data[1]
            command = data[2]
            remote = data[3]
            # print("remote:{}, command:{}, repeat:{}".format(remote, command, repeat))
            # ignore command repeats

            if remote == self.remoteModel:
                if command == self.keyUp:
                    self.volumeUp()

                if command == self.keyDown:
                    self.volumeDown()

                if command == self.keyMute:
                    if repeat != "00":
                        return None
                    self.volumeMuteToggle()

                if command == self.keyTimeError:
                    if repeat != "00":
                        return None
                    self.latencyErrorToggle()

        return None


if __name__ == '__main__':
    """Simple debug tool: prints decoded remote key presses."""
    import time

    print("nimRumTxRemote debug — listening for IR key presses (Ctrl+C to stop)")
    print("Connecting to lircd...")

    try:
        conn = lirc.LircdConnection(timeout=0.5)
        conn.connect()
        print("Connected. Press remote buttons:")
    except Exception as e:
        print(f"Failed to connect to lircd: {e}")
        print("Check: sudo systemctl status lircd")
        exit(1)

    try:
        while True:
            try:
                keypress = conn.readline()
                if keypress:
                    data = keypress.split()
                    print(f"  remote={data[3]}  key={data[2]}  repeat={data[1]}")
            except Exception:
                pass
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\nDone.")
