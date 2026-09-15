#!/usr/bin/env python3
"""nimRumTxStatusApi — Lightweight localhost UDP status server for TX.

Runs as a thread inside the TX process. Responds to JSON requests from
the WebUI process with current system status.
"""

import json
import socket
import threading

DEFAULT_PORT = 53475


class NimRumTxStatusApi:
    def __init__(self, port=DEFAULT_PORT):
        self._port = port
        self._status_fn = None
        self._command_fn = None
        self._thread = None
        self._running = False

    def set_status_fn(self, fn):
        """Set callback that returns current status dict."""
        self._status_fn = fn

    def set_command_fn(self, fn):
        """Set callback that handles commands. fn(cmd_dict) -> response_dict."""
        self._command_fn = fn

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", self._port))
        sock.settimeout(1.0)

        while self._running:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                continue

            try:
                req = json.loads(data.decode())
                cmd = req.get("cmd", "")

                if cmd == "getStatus":
                    resp = self._status_fn() if self._status_fn else {}
                elif self._command_fn:
                    resp = self._command_fn(req)
                else:
                    resp = {"error": "no handler"}

                sock.sendto(json.dumps(resp).encode(), addr)
            except Exception as e:
                try:
                    sock.sendto(json.dumps({"error": str(e)}).encode(), addr)
                except Exception:
                    pass

        sock.close()
