"""Shell-written device-channel config: validated, private, fail-closed."""

import os

from backend.core.handshake import (
    DEVICE_CHANNEL_VERSION,
    default_device_channel_config,
    device_channel_config_path,
    read_device_channel_config,
    write_device_channel_config,
)


def _disabled_payload(path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("not json")


def test_missing_config_uses_current_default(tmp_path, monkeypatch):
    monkeypatch.setenv("SYTHION_DATA_DIR", str(tmp_path))
    assert read_device_channel_config() == default_device_channel_config()
    assert not os.path.exists(device_channel_config_path())


def test_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SYTHION_DATA_DIR", str(tmp_path))
    payload = write_device_channel_config(True, True, False)
    assert payload == {
        "version": DEVICE_CHANNEL_VERSION,
        "enabled": True,
        "relay_enabled": True,
        "ws_enabled": False,
    }
    assert read_device_channel_config() == payload
    assert os.path.exists(device_channel_config_path())


def test_corrupt_config_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("SYTHION_DATA_DIR", str(tmp_path))
    _disabled_payload(device_channel_config_path())
    result = read_device_channel_config()
    assert result["enabled"] is False
    assert result["relay_enabled"] is False
    assert result["ws_enabled"] is False


def test_unknown_version_fails_closed(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("SYTHION_DATA_DIR", str(tmp_path))
    with open(device_channel_config_path(), "w", encoding="utf-8") as fh:
        json.dump({"version": 99, "enabled": True,
                   "relay_enabled": True, "ws_enabled": True}, fh)
    assert read_device_channel_config()["enabled"] is False


def test_non_boolean_flags_fail_closed(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("SYTHION_DATA_DIR", str(tmp_path))
    with open(device_channel_config_path(), "w", encoding="utf-8") as fh:
        json.dump({"version": DEVICE_CHANNEL_VERSION, "enabled": "yes",
                   "relay_enabled": True, "ws_enabled": True}, fh)
    assert read_device_channel_config()["enabled"] is False


def test_write_rejects_non_boolean(tmp_path, monkeypatch):
    monkeypatch.setenv("SYTHION_DATA_DIR", str(tmp_path))
    try:
        write_device_channel_config("true", True, True)
        raise AssertionError("non-boolean accepted")
    except ValueError:
        pass
