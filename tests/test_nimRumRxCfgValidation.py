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

"""Tests for RX config validation and numeric coercion.

Pins the fix for the crash-loop bug where the WebUI saved a numeric field with
a unit suffix (staticDelay_us: 710us). YAML parses that as the string "710us",
which is then passed straight into a ctypes call that raises
TypeError: 'str' object cannot be interpreted as an integer — restart-looping
the RX. Both layers are pinned: the pre-push validator (reject at save time)
and the loader accessors (degrade to a warning + default at runtime).
"""

import os
import tempfile

import pytest

from nimRum.rx.nimRumRxCfg import nimRumRxCfg, validate_rx_config_yaml


def _write_cfg(text: str) -> str:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False)
    tmp.write(text)
    tmp.close()
    return tmp.name


# --- validate_rx_config_yaml -------------------------------------------------

def test_validator_rejects_unit_suffix():
    ok, err = validate_rx_config_yaml(
        "nimRumRXConfig:\n  staticDelay_us: 710us\n")
    assert not ok
    assert "staticDelay_us" in err
    assert "710us" in err


def test_validator_accepts_plain_integer():
    ok, err = validate_rx_config_yaml(
        "nimRumRXConfig:\n  staticDelay_us: 710\n")
    assert ok
    assert err == ""


def test_validator_accepts_quoted_integer():
    ok, err = validate_rx_config_yaml(
        'nimRumRXConfig:\n  staticDelay_us: "710"\n')
    assert ok


@pytest.mark.parametrize("field", [
    "staticDelay_us", "outputChannelEnable", "logEnable", "forceS16",
])
def test_validator_checks_all_int_fields(field):
    ok, err = validate_rx_config_yaml(
        f"nimRumRXConfig:\n  {field}: 5x\n")
    assert not ok
    assert field in err


def test_validator_rejects_bad_yaml():
    ok, err = validate_rx_config_yaml("nimRumRXConfig:\n  a: [1, 2\n")
    assert not ok
    assert "YAML" in err


def test_validator_rejects_missing_section():
    ok, err = validate_rx_config_yaml("someOtherRoot:\n  x: 1\n")
    assert not ok
    assert "nimRumRXConfig" in err


# --- loader accessors (defense in depth) ------------------------------------

def test_accessor_coerces_string_integer():
    path = _write_cfg('nimRumRXConfig:\n  staticDelay_us: "710"\n')
    try:
        cfg = nimRumRxCfg(path)
        assert cfg.getStaticDelayUs() == 710
    finally:
        os.unlink(path)


def test_accessor_defaults_on_unit_suffix():
    # A value that slipped past validation must not crash: degrade to default.
    path = _write_cfg("nimRumRXConfig:\n  staticDelay_us: 710us\n")
    try:
        cfg = nimRumRxCfg(path)
        assert cfg.getStaticDelayUs() == 0
    finally:
        os.unlink(path)


def test_accessor_passes_real_integer():
    path = _write_cfg("nimRumRXConfig:\n  staticDelay_us: -125\n")
    try:
        cfg = nimRumRxCfg(path)
        assert cfg.getStaticDelayUs() == -125
    finally:
        os.unlink(path)


def test_log_enable_is_zero_or_one():
    path = _write_cfg("nimRumRXConfig:\n  logEnable: 1\n")
    try:
        cfg = nimRumRxCfg(path)
        assert cfg.getLogEnable() == 1
    finally:
        os.unlink(path)
