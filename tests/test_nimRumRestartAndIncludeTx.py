"""Tests for the unified, role-agnostic service restart and TX-inclusive fleet deploy.

Two blind spots this covers, both traced from a co-located nimrum-src on the TX box
reporting a stale version after a TX self-update:

1. TX's own restart path (_restart_tx, reached by the "Restart TX" button, config
   save-and-restart, and both self-update paths) restarted only nimrum-tx/webui,
   leaving a co-located nimrum-src on its old image. It now runs the same
   role-agnostic loop the remote deploy path uses.
2. deploy_fleet always excluded the local TX host, so a fleet update never covered
   the TX box through the verified path. include_tx=True keeps it in.
"""

import os
import sys

import pytest

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PKG_ROOT)

from nimRum.common import nimRumFleetDeploy as fd  # noqa: E402
from nimRum.tx_fleet import pypkg_deploy as pd  # noqa: E402


class TestRestartIsRoleAgnostic:
    """The restart command must ask the device which nimrum-* units are enabled
    rather than naming a fixed set, so it covers a co-located src/tx/webui alike."""

    def test_shared_restart_command_is_generic(self):
        cmd = fd.RESTART_NIMRUM_UNITS_CMD
        # It enumerates enabled units and skips oneshots — it does not hardcode
        # the service names.
        assert "list-unit-files 'nimrum-*'" in cmd
        assert "oneshot" in cmd
        assert "enabled" in cmd
        assert "nimrum-tx nimrum-webui" not in cmd

    def test_deploy_restart_step_uses_the_shared_command(self, monkeypatch, tmp_path):
        """deploy_single_device's restart step must send RESTART_NIMRUM_UNITS_CMD,
        not a bespoke string, so there is one definition of "restart everything"."""
        names = [
            "nimrum-2.8.7-py3-none-manylinux_2_27_aarch64.whl",
            "nimrum-2.8.7-py3-none-linux_armv7l.whl",
        ]
        wheels = []
        for n in names:
            p = tmp_path / n
            p.write_bytes(b"PK\x03\x04")
            wheels.append(str(p))

        sent = []

        def _fake_ssh(target, command, ssh_opts, timeout=60):
            sent.append(command)
            if command.strip() == "uname -m":
                return (0, "aarch64\n", "")
            if "pip3 install" in command:
                return (0, "Successfully installed\n", "")
            if "importlib.metadata" in command:
                return (0, "\nNIMRUM_VER=2.8.7\n", "")
            return (0, "", "")

        monkeypatch.setattr(fd, "_run_ssh", _fake_ssh)
        monkeypatch.setattr(fd, "_run_scp", lambda src, dst, opts, timeout=30: (0, ""))

        result = fd.deploy_single_device("host-tx", wheels, [])
        assert result.get("ok") is True
        assert fd.RESTART_NIMRUM_UNITS_CMD in sent

    def test_restart_step_skipped_when_restart_false(self, monkeypatch, tmp_path):
        p = tmp_path / "nimrum-2.8.7-py3-none-manylinux_2_27_aarch64.whl"
        p.write_bytes(b"PK\x03\x04")

        sent = []

        def _fake_ssh(target, command, ssh_opts, timeout=60):
            sent.append(command)
            if command.strip() == "uname -m":
                return (0, "aarch64\n", "")
            if "pip3 install" in command:
                return (0, "Successfully installed\n", "")
            if "importlib.metadata" in command:
                return (0, "\nNIMRUM_VER=2.8.7\n", "")
            return (0, "", "")

        monkeypatch.setattr(fd, "_run_ssh", _fake_ssh)
        monkeypatch.setattr(fd, "_run_scp", lambda src, dst, opts, timeout=30: (0, ""))

        fd.deploy_single_device("host-tx", [str(p)], [], restart=False)
        assert fd.RESTART_NIMRUM_UNITS_CMD not in sent


class TestRestartTxRunsSharedLoopLocally:
    def test_restart_tx_runs_the_shared_command_via_bash(self, monkeypatch):
        """_restart_tx must run the role-agnostic loop locally, not the old
        hardcoded nimrum-tx/nimrum-webui pair that stranded a co-located src."""
        calls = []

        def _fake_run(argv, **kwargs):
            calls.append(argv)

            class _R:
                returncode = 0
            return _R()

        import subprocess
        monkeypatch.setattr(subprocess, "run", _fake_run)

        pd._restart_tx()

        assert len(calls) == 1
        argv = calls[0]
        assert argv[0] == "bash"
        assert argv[1] == "-c"
        assert argv[2] == fd.RESTART_NIMRUM_UNITS_CMD
        # The bug being prevented: a fixed service list.
        assert "nimrum-tx nimrum-webui" not in " ".join(argv)


class TestDeployFleetIncludeTx:
    def _registry(self):
        class _Reg:
            def get_devices_by_role(self, role):
                return {
                    "rx": ["host-rx1", "host-rx2"],
                    "src": ["host-tx", "host-src"],
                }.get(role, [])

        return _Reg()

    def _setup(self, monkeypatch, tmp_path):
        wheel = tmp_path / "nimrum-2.8.7-py3-none-manylinux_2_27_aarch64.whl"
        wheel.write_bytes(b"PK\x03\x04")
        monkeypatch.setattr(pd, "get_registry", self._registry)
        # host-tx is the local TX box.
        import platform
        monkeypatch.setattr(platform, "node", lambda: "host-tx")
        # Capture the device set at thread-construction time (synchronous, so no
        # race against the real background worker), and make the thread inert.
        captured = {}

        class _InertThread:
            def __init__(self, target=None, args=(), daemon=None):
                # args = (wheel_path, devices)
                captured["devices"] = list(args[1]) if len(args) > 1 else []

            def start(self):
                pass

        import threading
        monkeypatch.setattr(threading, "Thread", _InertThread)
        # Reset shared state so a prior test's run flag does not block us.
        with pd._fleet_lock:
            pd._fleet_state["running"] = False
        return str(wheel), captured

    def test_local_tx_excluded_by_default(self, monkeypatch, tmp_path):
        wheel, captured = self._setup(monkeypatch, tmp_path)
        result = pd.deploy_fleet(wheel)
        assert result.get("ok") is True
        assert "host-tx" not in captured["devices"]
        assert "host-src" in captured["devices"]

    def test_include_tx_keeps_local_host(self, monkeypatch, tmp_path):
        wheel, captured = self._setup(monkeypatch, tmp_path)
        result = pd.deploy_fleet(wheel, include_tx=True)
        assert result.get("ok") is True
        assert "host-tx" in captured["devices"]

    def test_explicit_devices_ignore_include_tx(self, monkeypatch, tmp_path):
        """An explicit list is taken as-is; include_tx only affects registry
        selection."""
        wheel, captured = self._setup(monkeypatch, tmp_path)
        result = pd.deploy_fleet(wheel, devices=["host-rx1"], include_tx=True)
        assert result.get("ok") is True
        assert captured["devices"] == ["host-rx1"]
