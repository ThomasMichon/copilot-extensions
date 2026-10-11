"""Local cache access without cryptography or vault unlock on Windows."""

from __future__ import annotations

import builtins
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_vault import cache, kek
from agent_vault.cache_dpapi import MAGIC, CacheProtectionError


@pytest.fixture
def native_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "IS_WINDOWS", True)
    monkeypatch.setenv(cache.CACHE_ENABLE_ENV, "1")
    monkeypatch.setenv(cache.CACHE_DIR_ENV, str(tmp_path))
    original = builtins.__import__

    def without_crypto(name, *args, **kwargs):
        if name == "cryptography" or name.startswith("cryptography."):
            raise ImportError("cryptography unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_crypto)
    return tmp_path


@pytest.mark.skipif(os.name != "nt", reason="real Windows DPAPI contract")
def test_native_cache_survives_reopen_without_vault_unlock(native_cache):
    first = cache.PersistentCache()
    assert first.enabled
    assert first.put("Identity/shared", "password", "owner-level-key")
    assert cache.PersistentCache().get("Identity/shared", "password") == "owner-level-key"
    blob = (native_cache / "credential-cache.enc").read_bytes()
    assert blob.startswith(MAGIC)
    assert b"owner-level-key" not in blob
    assert not (native_cache / "credential-cache.key").exists()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(cache.__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-c", """
import builtins
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == 'cryptography' or name.startswith('cryptography.'):
        raise ImportError('unavailable crypto')
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
from agent_vault.cache import PersistentCache
assert PersistentCache().get('Identity/shared','password') == 'owner-level-key'
"""],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_legacy_cache_is_not_replaced_without_crypto(native_cache):
    path = native_cache / "credential-cache.enc"
    path.write_bytes(b"legacy-fernet-token")
    with pytest.raises(CacheProtectionError, match="not replaced"):
        cache.PersistentCache().get("Identity/shared", "password")
    assert path.read_bytes() == b"legacy-fernet-token"


def test_wrong_user_or_corrupt_native_cache_is_not_overwritten(native_cache, monkeypatch):
    path = native_cache / "credential-cache.enc"
    path.write_bytes(MAGIC + b"unreadable")

    def fail(*args):
        raise kek.KekError("wrong user")

    monkeypatch.setattr(kek, "_dpapi", fail)
    with pytest.raises(CacheProtectionError):
        cache.PersistentCache().get("Identity/shared", "password")
    assert path.read_bytes() == MAGIC + b"unreadable"
    assert not cache.PersistentCache().put("Identity/shared", "password", "new-key")
    assert path.read_bytes() == MAGIC + b"unreadable"
