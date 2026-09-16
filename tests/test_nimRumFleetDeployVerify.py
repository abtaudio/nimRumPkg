"""Tests for the fleet-update fixes.

Two silent failures let a UI 'Update' report success while the fleet stayed on
an old build:

1. deploy_single_device trusted an unconditional `echo DEPLOY_OK`, so a no-op
   install (same version reinstalled, or a pip that changed nothing) reported
   ok. It now reads the version actually importable on the device and requires
   it to match the wheel.
2. A PyPI-based TX update left no wheel on disk, so the later fleet update had
   nothing correct to push. update_tx_from_pypi now caches every published
   wheel for the release via _fetch_wheels_to_cache.
"""

import os
import sys

import pytest

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PKG_ROOT)

from nimRum.common import nimRumFleetDeploy as fd  # noqa: E402
from nimRum.tx_fleet import pypkg_deploy as pd  # noqa: E402


class _SSHRecorder:
    """Stand-in for _run_ssh that answers by command content.

    Returns (rc, stdout, stderr). The install step's stdout is configurable so
    each test can simulate a landed or a no-op deploy.
    """

    def __init__(self, arch: str = "aarch64", install_stdout: str = "",
                 reported_version: str = None):
        self.arch = arch
        # Install no longer carries the version; it just needs to look like a
        # successful pip run (or not). Default to a clean install line.
        self.install_stdout = install_stdout or "Successfully installed\n"
        # What `importlib.metadata.version` reports on the separate verify call.
        # None means the verify command produced nothing (import/pip failure).
        self.reported_version = reported_version
        self.calls = []

    def __call__(self, target, command, ssh_opts, timeout=60):
        self.calls.append(command)
        if command.strip() == "uname -m":
            return (0, self.arch + "\n", "")
        if "pip3 install" in command:
            return (0, self.install_stdout, "")
        if "importlib.metadata" in command:
            if self.reported_version is None:
                return (0, "", "")
            return (0, f"\nNIMRUM_VER={self.reported_version}\n", "")
        # restart step
        return (0, "", "")


def _ok_scp(src, dst, ssh_opts, timeout=30):
    return (0, "")


@pytest.fixture
def wheels(tmp_path):
    """Two arch wheels for version 2.8.5, as published on PyPI."""
    names = [
        "nimrum-2.8.5-py3-none-manylinux_2_27_aarch64.whl",
        "nimrum-2.8.5-py3-none-linux_armv7l.whl",
    ]
    paths = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"PK\x03\x04fake wheel")
        paths.append(str(p))
    return paths


class TestDeployVerifiesVersion:
    def test_landed_deploy_reports_ok_with_version(self, monkeypatch, wheels):
        ssh = _SSHRecorder(arch="aarch64", reported_version="2.8.5")
        monkeypatch.setattr(fd, "_run_ssh", ssh)
        monkeypatch.setattr(fd, "_run_scp", _ok_scp)

        result = fd.deploy_single_device("spkr8", wheels, [])
        assert result.get("ok") is True
        assert result.get("version") == "2.8.5"

    def test_noop_install_is_caught_as_mismatch(self, monkeypatch, wheels):
        """The device still reports the old version — the exact bug that shipped:
        a fleet update said ok while every RX stayed on the previous build."""
        ssh = _SSHRecorder(arch="aarch64", reported_version="1.1.3")
        monkeypatch.setattr(fd, "_run_ssh", ssh)
        monkeypatch.setattr(fd, "_run_scp", _ok_scp)

        result = fd.deploy_single_device("spkr8", wheels, [])
        assert "error" in result
        assert "1.1.3" in result["error"]
        assert "2.8.5" in result["error"]

    def test_unreadable_version_is_an_error(self, monkeypatch, wheels):
        """No NIMRUM_VER line (import failed, pip failed) must not read as ok."""
        ssh = _SSHRecorder(arch="aarch64", reported_version=None)
        monkeypatch.setattr(fd, "_run_ssh", ssh)
        monkeypatch.setattr(fd, "_run_scp", _ok_scp)

        result = fd.deploy_single_device("spkr8", wheels, [])
        assert "error" in result
        assert result.get("ok") is not True

    def test_armv7_device_gets_the_armv7_wheel(self, monkeypatch, wheels):
        """A 32-bit device must pick the armv7l wheel, not the aarch64 one."""
        ssh = _SSHRecorder(arch="armv7l", reported_version="2.8.5")
        monkeypatch.setattr(fd, "_run_ssh", ssh)

        scp_targets = []

        def _record_scp(src, dst, ssh_opts, timeout=30):
            scp_targets.append(src)
            return (0, "")

        monkeypatch.setattr(fd, "_run_scp", _record_scp)

        result = fd.deploy_single_device("kitchen", wheels, [])
        assert result.get("ok") is True
        assert scp_targets and scp_targets[0].endswith("linux_armv7l.whl")

    def test_missing_arch_wheel_is_an_error(self, monkeypatch):
        """No wheel for the device's arch is a real, reported failure."""
        ssh = _SSHRecorder(arch="armv7l")
        monkeypatch.setattr(fd, "_run_ssh", ssh)
        monkeypatch.setattr(fd, "_run_scp", _ok_scp)

        aarch64_only = ["nimrum-2.8.5-py3-none-manylinux_2_27_aarch64.whl"]
        result = fd.deploy_single_device("kitchen", aarch64_only, [])
        assert "error" in result
        assert "armv7l" in result["error"]


class TestFetchWheelsToCache:
    def test_downloads_all_published_wheels(self, monkeypatch, tmp_path):
        """A PyPI update must leave both arch wheels in wheels/, so the fleet
        update has a correct wheel for every device."""
        monkeypatch.chdir(tmp_path)

        meta = {
            "info": {"version": "2.8.5"},
            "urls": [
                {"packagetype": "bdist_wheel",
                 "filename": "nimrum-2.8.5-py3-none-manylinux_2_27_aarch64.whl",
                 "url": "https://files.pythonhosted.org/a.whl"},
                {"packagetype": "bdist_wheel",
                 "filename": "nimrum-2.8.5-py3-none-linux_armv7l.whl",
                 "url": "https://files.pythonhosted.org/b.whl"},
                {"packagetype": "sdist",
                 "filename": "nimrum-2.8.5.tar.gz",
                 "url": "https://files.pythonhosted.org/c.tar.gz"},
            ],
        }
        _install_fake_urllib(monkeypatch, meta)

        result = pd._fetch_wheels_to_cache("2.8.5")
        assert "error" not in result, result
        got = {os.path.basename(p) for p in result["wheels"]}
        assert got == {
            "nimrum-2.8.5-py3-none-manylinux_2_27_aarch64.whl",
            "nimrum-2.8.5-py3-none-linux_armv7l.whl",
        }
        for p in result["wheels"]:
            assert os.path.isfile(p)
        # sdist is not a wheel and must be skipped.
        assert not any(p.endswith(".tar.gz") for p in result["wheels"])

    def test_no_wheels_published_is_an_error(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        meta = {"info": {"version": "9.9.9"},
                "urls": [{"packagetype": "sdist",
                          "filename": "nimrum-9.9.9.tar.gz",
                          "url": "https://x/c.tar.gz"}]}
        _install_fake_urllib(monkeypatch, meta)

        result = pd._fetch_wheels_to_cache("9.9.9")
        assert "error" in result

    def test_partial_download_leaves_no_usable_wheel(self, monkeypatch, tmp_path):
        """A failed download must not leave a .whl that get_tx_wheel_path picks."""
        monkeypatch.chdir(tmp_path)
        meta = {
            "info": {"version": "2.8.5"},
            "urls": [
                {"packagetype": "bdist_wheel",
                 "filename": "nimrum-2.8.5-py3-none-linux_armv7l.whl",
                 "url": "https://x/b.whl"},
            ],
        }
        _install_fake_urllib(monkeypatch, meta, fail_download=True)

        result = pd._fetch_wheels_to_cache("2.8.5")
        assert "error" in result
        import glob
        assert glob.glob(os.path.join(str(tmp_path), "wheels", "*.whl")) == []


def _install_fake_urllib(monkeypatch, meta, fail_download=False):
    """Patch urllib.request in pypkg_deploy to serve metadata and wheel bytes
    without touching the network."""
    import io
    import json as _json
    import urllib.request

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()
            return False

    def _fake_urlopen(req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else req
        if "pypi.org" in url:
            return _Resp(_json.dumps(meta).encode())
        if fail_download:
            raise OSError("simulated network failure")
        return _Resp(b"PK\x03\x04fake wheel bytes")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
