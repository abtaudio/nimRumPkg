"""Tests for scripts/deploy.py device selection.

Covers the two things that changed when the hardcoded host prefix came out:
the SSH target must come from the IP that TX learned, and -d must select
devices without knowing the site's naming scheme.
"""

import os
import sys

import pytest

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPT_DIR)

import deploy  # noqa: E402


def _registry() -> dict:
    """A TX registry with mixed naming, a missing address and a TX-only host."""
    return {
        "devices": {
            "spkr4": {"ip": "192.0.2.14", "ssh_user": "pi", "roles": ["rx"]},
            "spkr42": {"ip": "192.0.2.42", "ssh_user": "pi", "roles": ["rx"]},
            "kitchen": {"ip": "192.0.2.70", "ssh_user": "olle", "roles": ["rx"]},
            "noaddr9": {"ip": "", "ssh_user": "pi", "roles": ["rx"]},
            "livingTx": {"ip": "192.0.2.1", "ssh_user": "pi", "roles": ["tx"]},
            "srcbox": {"ip": "192.0.2.80", "ssh_user": "pi", "roles": ["tx", "src"]},
        }
    }


@pytest.fixture
def devices(monkeypatch) -> list:
    monkeypatch.setattr(deploy, "fetch_device_registry_from_tx",
                        lambda tx, opts: _registry())
    return deploy.get_devices_from_tx("anyTx", [])


@pytest.fixture
def devices_by_ip(monkeypatch) -> list:
    monkeypatch.setattr(deploy, "fetch_device_registry_from_tx",
                        lambda tx, opts: _registry())
    return deploy.get_devices_from_tx("anyTx", [], by_ip=True)


class TestTargetFromRegistry:
    def test_target_is_the_hostname_by_default(self, devices):
        """SSH must be given the hostname, so ~/.ssh/config can pick the User and
        IdentityFile. Targeting the IP instead failed on 14 of 15 real devices
        with 'Permission denied (publickey)' and, worse, succeeded on the 15th:
        a wire-breaking release landed on part of the fleet."""
        d = next(x for x in devices if x["hostname"] == "spkr4")
        assert d["target"] == "spkr4"
        assert d["ip"] == "192.0.2.14"

    def test_ssh_user_is_not_prepended_by_default(self, devices):
        """A registry ssh_user must not override the local SSH config."""
        d = next(x for x in devices if x["hostname"] == "kitchen")
        assert d["target"] == "kitchen"

    def test_by_ip_uses_user_at_ip(self, devices_by_ip):
        """--by-ip is for a machine with no SSH config, DNS or mDNS."""
        d = next(x for x in devices_by_ip if x["hostname"] == "spkr4")
        assert d["target"] == "pi@192.0.2.14"

    def test_by_ip_honours_per_device_ssh_user(self, devices_by_ip):
        d = next(x for x in devices_by_ip if x["hostname"] == "kitchen")
        assert d["target"] == "olle@192.0.2.70"

    def test_by_ip_without_an_address_falls_back_to_hostname(self, devices_by_ip):
        """No address learned yet must not produce 'pi@' with an empty host."""
        d = next(x for x in devices_by_ip if x["hostname"] == "noaddr9")
        assert d["target"] == "noaddr9"
        assert d["ip"] == ""

    def test_tx_only_host_is_skipped_but_src_kept(self, devices):
        names = {d["hostname"] for d in devices}
        assert "livingTx" not in names
        assert "srcbox" in names


class TestFilterDevices:
    def test_numeric_token_matches_trailing_number(self, devices):
        r = deploy.filter_devices(devices, "4")
        assert [d["hostname"] for d in r] == ["spkr4"]

    def test_numeric_token_is_not_a_substring_match(self, devices):
        """'4' must not pull in spkr42 — the old prefix build was exact too."""
        r = deploy.filter_devices(devices, "4")
        assert "spkr42" not in [d["hostname"] for d in r]

    def test_several_tokens(self, devices):
        r = deploy.filter_devices(devices, "4 42")
        assert [d["hostname"] for d in r] == ["spkr4", "spkr42"]

    def test_full_hostname_token(self, devices):
        r = deploy.filter_devices(devices, "kitchen")
        assert [d["hostname"] for d in r] == ["kitchen"]

    def test_mixed_name_and_number(self, devices):
        r = deploy.filter_devices(devices, "kitchen 42")
        assert [d["hostname"] for d in r] == ["kitchen", "spkr42"]

    def test_empty_selection_returns_everything(self, devices):
        assert deploy.filter_devices(devices, "") == devices

    def test_no_match_returns_empty(self, devices):
        assert deploy.filter_devices(devices, "999") == []

    def test_letter_ending_host_ignores_numeric_tokens(self, devices):
        """A host with no trailing digits is only reachable by full name."""
        r = deploy.filter_devices(devices, "70")
        assert r == []
