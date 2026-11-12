"""A cache-only miss never provisions machine-local key material."""

from __future__ import annotations

import io
import json
import sys
from types import ModuleType, SimpleNamespace

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


@pytest.mark.parametrize("failure", [
    "missing-key", "protection-error", "wrapped-key", "unavailable-wrapping",
])
def test_batch_cache_protection_failure_preserves_order_and_legacy_get(
    monkeypatch, tmp_path, capsys, failure,
):
    from agent_vault import cache as cache_module, cli, kek

    monkeypatch.delenv("AGENT_VAULT_PINNED_CONTEXT", raising=False)
    monkeypatch.setenv("AGENT_VAULT_CACHE", "1")
    monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", str(tmp_path))
    cache_file = tmp_path / "credential-cache.enc"
    key_file = tmp_path / "credential-cache.key"
    cache_file.write_bytes(b"fixture-ciphertext")
    cache = PersistentCache()
    key = kek.WRAP_MAGIC + b"Xfixture-key" if failure == "wrapped-key" else b"fixture-key"
    if failure == "unavailable-wrapping":
        key = kek.WRAP_MAGIC + b"Dfixture-key"
        monkeypatch.setattr(cache_module, "IS_WINDOWS", False)

        def unavailable(*_args):
            raise AttributeError("private-wrapping-backend-diagnostic")

        monkeypatch.setattr(kek, "_dpapi", unavailable)
    if failure != "missing-key":
        key_file.write_bytes(key)
    if failure in ("wrapped-key", "unavailable-wrapping"):
        module = ModuleType("agent_vault.protected_crypto")
        module.Fernet = lambda _key: pytest.fail("invalid wrapping reached encryption")
        monkeypatch.setitem(sys.modules, "agent_vault.protected_crypto", module)
    if failure == "protection-error":

        class UnavailableCipher:
            def decrypt(self, _value):
                raise CacheProtectionError("private-protection-diagnostic")

        module = ModuleType("agent_vault.protected_crypto")
        module.Fernet = lambda _key: UnavailableCipher()
        monkeypatch.setitem(sys.modules, "agent_vault.protected_crypto", module)
    monkeypatch.setattr(cache_module, "get_cache", lambda: cache)
    monkeypatch.setattr(
        cli, "ensure_service",
        lambda: pytest.fail("protected-cache failure fell back to the service"),
    )
    requests = [
        {"entry": "Example/one", "field": "password"},
        {"entry": "Example/two", "field": "username"},
        {"entry": "Example/one", "field": "password"},
    ]
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(requests)))

    assert cli.main(["batch-get"]) == 1
    captured = capsys.readouterr()
    results = json.loads(captured.out)["results"]
    assert [(row["entry"], row["field"]) for row in results] == [
        (row["entry"], row["field"]) for row in requests
    ]
    assert all(row["ok"] is False and row["value"] is None for row in results)
    assert all(row["source"] == "cache" for row in results)
    assert all(row["error"].startswith("cache_protection_failed;") for row in results)
    assert "private-protection-diagnostic" not in captured.out
    assert "private-wrapping-backend-diagnostic" not in captured.out
    assert "unknown wrap scheme" not in captured.out
    assert captured.err == ""
    assert cache_file.read_bytes() == b"fixture-ciphertext"
    if failure == "missing-key":
        assert not key_file.exists()
    else:
        assert key_file.read_bytes() == key
    expected_error = {
        "wrapped-key": kek.KekError,
    }.get(failure, CacheProtectionError)
    with pytest.raises(expected_error):
        cli.cmd_get(SimpleNamespace(entry="Example/one", field="password"))


def test_readonly_legacy_key_is_never_rewrapped(monkeypatch, tmp_path):
    from agent_vault import cache as cache_module

    monkeypatch.setattr(cache_module, "IS_WINDOWS", False)
    monkeypatch.setenv("AGENT_VAULT_CACHE", "1")
    monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", str(tmp_path))
    module = ModuleType("agent_vault.protected_crypto")

    class FixtureFernet:
        def __init__(self, key):
            assert key == b"fixture-wrapping-key"

        def decrypt(self, ciphertext):
            assert ciphertext == b"fixture-ciphertext"
            return json.dumps({
                "v": 1, "entries": {"Identity/x": {"password": {"value": "fixture-value"}}},
            }).encode()

    module.Fernet = FixtureFernet
    monkeypatch.setitem(sys.modules, "agent_vault.protected_crypto", module)
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

    module = ModuleType("agent_vault.protected_crypto")
    module.Fernet = lambda _key: BrokenCipher()
    monkeypatch.setitem(sys.modules, "agent_vault.protected_crypto", module)
    with pytest.raises(CacheProtectionError, match="invalid; not replaced"):
        cache.get("Identity/x", readonly=True)
