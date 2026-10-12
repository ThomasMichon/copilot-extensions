"""A cache-only miss never provisions machine-local key material."""

from __future__ import annotations

import pytest

from agent_vault.cache import PersistentCache
from agent_vault.cache_dpapi import CacheProtectionError


@pytest.fixture(autouse=True)
def available_cache_backend(monkeypatch):
    # These tests stop before encryption; exercise read-only filesystem guards
    # even on POSIX installations without the optional crypto extra.
    monkeypatch.setattr(PersistentCache, "_crypto_available", lambda self: True)


def test_fresh_cache_miss_creates_no_files(monkeypatch, tmp_path):
    root = tmp_path / "absent-cache"
    monkeypatch.setenv("AGENT_VAULT_CACHE", "1")
    monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", str(root))
    assert PersistentCache().get("Identity/shared") is None
    assert not root.exists()


def test_existing_cache_without_key_is_not_rekeyed(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_VAULT_CACHE", "1")
    monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", str(tmp_path))
    path = tmp_path / "credential-cache.enc"
    path.write_bytes(b"old-cache")
    with pytest.raises(CacheProtectionError, match="key missing"):
        PersistentCache().get("Identity/shared")
    assert path.read_bytes() == b"old-cache"
    assert not (tmp_path / "credential-cache.key").exists()
