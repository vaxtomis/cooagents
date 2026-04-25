"""Settings tests for Phase 6 OSS config + load_settings fail-fast."""
from __future__ import annotations

import pytest

from src.config import Settings, load_settings
from src.exceptions import BadRequestError


def test_oss_config_defaults_are_disabled(monkeypatch):
    # Ensure no env vars contaminate construction.
    for key in (
        "OSS_BUCKET", "OSS_REGION", "OSS_ENDPOINT",
        "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    s = Settings()
    assert s.storage.oss.enabled is False
    assert s.storage.oss.bucket == ""
    assert s.storage.oss.prefix == ""


def test_oss_credentials_read_from_env_on_construction(monkeypatch):
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "env-id")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "env-secret")
    from src.config import OSSConfig
    c = OSSConfig()
    assert c.access_key_id == "env-id"
    assert c.access_key_secret == "env-secret"


def test_load_settings_oss_disabled_no_validation(tmp_path, monkeypatch):
    for key in ("OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET"):
        monkeypatch.delenv(key, raising=False)
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text("", encoding="utf-8")
    s = load_settings(yaml_path)
    assert s.storage.oss.enabled is False  # no raise


def test_load_settings_oss_enabled_missing_everything_raises(
    tmp_path, monkeypatch,
):
    for key in (
        "OSS_BUCKET", "OSS_REGION", "OSS_ENDPOINT",
        "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "storage:\n  oss:\n    enabled: true\n",
        encoding="utf-8",
    )
    with pytest.raises(BadRequestError) as excinfo:
        load_settings(yaml_path)
    msg = str(excinfo.value)
    for name in ("bucket", "region", "endpoint",
                 "access_key_id", "access_key_secret"):
        assert name in msg


def test_load_settings_oss_enabled_env_creds_yaml_rest_passes(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "env-id")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "env-secret")
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "storage:\n  oss:\n    enabled: true\n"
        "    bucket: b\n    region: r\n"
        "    endpoint: https://oss-cn-x.aliyuncs.com\n",
        encoding="utf-8",
    )
    s = load_settings(yaml_path)
    assert s.storage.oss.enabled is True
    assert s.storage.oss.bucket == "b"
    assert s.storage.oss.access_key_id == "env-id"


def test_load_settings_oss_enabled_missing_only_secret_raises(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "env-id")
    monkeypatch.delenv("OSS_ACCESS_KEY_SECRET", raising=False)
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "storage:\n  oss:\n    enabled: true\n"
        "    bucket: b\n    region: r\n    endpoint: https://x\n",
        encoding="utf-8",
    )
    with pytest.raises(BadRequestError) as excinfo:
        load_settings(yaml_path)
    assert "access_key_secret" in str(excinfo.value)
    assert "access_key_id" not in str(excinfo.value)
