"""A cache-only miss never provisions machine-local key material."""

from __future__ import annotations

import json
import sys
from types import ModuleType

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


def test_readonly_legacy_key_is_never_rewrapped(monkeypatch, tmp_path):
    from agent_vault import cache as cache_module

    monkeypatch.setattr(cache_module, "IS_WINDOWS", False)
    monkeypatch.setenv("AGENT_VAULT_CACHE", "1")
    monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", str(tmp_path))
    module = ModuleType("cryptography.fernet")

    class FixtureFernet:
        def __init__(self, key):
            assert key == b"fixture-wrapping-key"

        def decrypt(self, ciphertext):
            assert ciphertext == b"fixture-ciphertext"
            return json.dumps({
                "v": 1, "entries": {"Identity/x": {"password": {"value": "fixture-value"}}},
            }).encode()

    module.Fernet = FixtureFernet
    monkeypatch.setitem(sys.modules, "cryptography.fernet", module)
    (tmp_path / "credential-cache.enc").write_bytes(b"fixture-ciphertext")
    key_file = tmp_path / "credential-cache.key"
    key_file.write_bytes(b"fixture-wrapping-key")
    cache = PersistentCache()
    monkeypatch.setattr(cache, "_write_key", lambda *args: pytest.fail("readonly key migration"))
    before = sorted(path.name for path in tmp_path.iterdir())
    assert cache.get("Identity/x", readonly=True) == "fixture-value"
    assert key_file.read_bytes() == b"fixture-wrapping-key"
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_readonly_corrupt_legacy_store_never_becomes_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_VAULT_CACHE", "1")
    monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", str(tmp_path))
    (tmp_path / "credential-cache.enc").write_bytes(b"fixture-ciphertext")
    (tmp_path / "credential-cache.key").write_bytes(b"fixture-key")
    cache = PersistentCache()

    class BrokenCipher:
        def decrypt(self, value):
            raise ValueError("fixture-backend-diagnostic")

    cache._fernet = BrokenCipher()
    with pytest.raises(CacheProtectionError, match="invalid; not replaced"):
        cache.get("Identity/x", readonly=True)
