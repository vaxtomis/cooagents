"""Factory tests for Phase 6 build_file_store dispatch."""
from __future__ import annotations

from src.config import Settings
from src.storage import LocalFileStore, build_file_store
from src.storage.factory import build_file_store as factory_func


def test_factory_returns_local_when_oss_disabled(tmp_path, monkeypatch):
    for key in (
        "OSS_BUCKET", "OSS_REGION", "OSS_ENDPOINT",
        "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    s = Settings()  # oss.enabled defaults to False
    store = build_file_store(s, tmp_path)
    assert isinstance(store, LocalFileStore)
    assert store.workspaces_root == tmp_path.resolve()


def test_factory_builds_oss_when_enabled(tmp_path, monkeypatch):
    """Monkeypatches the OSS constructor to a sentinel to avoid SDK import
    side effects and prove wiring."""
    captured: dict = {}

    class _Sentinel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("src.storage.oss.OSSFileStore", _Sentinel)

    s = Settings()
    s.storage.oss.enabled = True
    s.storage.oss.bucket = "test-bucket"
    s.storage.oss.region = "cn-hangzhou"
    s.storage.oss.endpoint = "https://oss-cn-hangzhou.aliyuncs.com"
    s.storage.oss.prefix = "workspaces/"
    s.storage.oss.access_key_id = "AKID"
    s.storage.oss.access_key_secret = "SECRET"

    store = build_file_store(s, tmp_path)

    assert isinstance(store, _Sentinel)
    assert captured["bucket"] == "test-bucket"
    assert captured["region"] == "cn-hangzhou"
    assert captured["endpoint"].startswith("https://")
    assert captured["prefix"] == "workspaces/"
    assert captured["credentials_provider"] is not None


def test_factory_symbol_re_exported_from_package():
    # Proves `from src.storage import build_file_store` works.
    assert build_file_store is factory_func
