"""Redundant machine copies never weaken primary selection or revive stale data."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from agent_vault import cache, cache_machine, cache_machine_cli, config, kek
from agent_vault.cache_dpapi import MAGIC, CacheDecryptionUnavailable, CacheProtectionError


class PrivateFiles:
    def __init__(self):
        self.private = set()
        self.refuse = False

    def create(self, path, *, directory):
        if self.refuse:
            raise PermissionError("fixture refused")
        if directory:
            path.mkdir(exist_ok=True)
            descriptor = None
        else:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        info = path.stat()
        self.private.add((info.st_dev, info.st_ino))
        return descriptor

    def require(self, path, *, directory):
        info = path.stat()
        if self.refuse or (info.st_dev, info.st_ino) not in self.private:
            raise PermissionError("fixture unsafe ACL")


@pytest.fixture(params=["native", "wrapped-key"])
def protected(request, monkeypatch, tmp_path):
    state = SimpleNamespace(user_unavailable=False, machine_unavailable=False, calls=[])

    def dpapi(protect, value, *, machine=False):
        state.calls.append((protect, machine))
        if protect:
            return (b"M" if machine else b"U") + value
        if value[:1] == b"U" and state.user_unavailable:
            raise kek.KekError("private user diagnostic")
        if value[:1] == b"M" and state.machine_unavailable:
            raise kek.KekError("private machine diagnostic")
        if value[:1] not in (b"U", b"M"):
            raise kek.KekError("fixture corrupt")
        return value[1:]

    monkeypatch.setattr(kek, "_dpapi", dpapi)
    monkeypatch.setattr(cache, "IS_WINDOWS", True)
    monkeypatch.setenv(cache.CACHE_ENABLE_ENV, "1")
    monkeypatch.setenv(cache.CACHE_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(cache.PersistentCache, "_file_lock", lambda *_a, **_kw: contextlib.nullcontext())
    security = PrivateFiles()
    monkeypatch.setattr(cache_machine, "_security", lambda: security)
    store = {"v": 1, "entries": {"Allowed/token": {"password": {"value": "fixture-value"}}}}
    data = json.dumps(store).encode()
    if request.param == "native":
        (tmp_path / "credential-cache.enc").write_bytes(MAGIC + dpapi(True, data))
    else:
        class Fernet:
            def __init__(self, _key):
                pass

            def encrypt(self, plaintext):
                return b"F" + plaintext

            def decrypt(self, ciphertext):
                assert ciphertext.startswith(b"F")
                return ciphertext[1:]

        module = ModuleType("agent_vault.protected_crypto")
        module.Fernet = Fernet
        monkeypatch.setitem(sys.modules, "agent_vault.protected_crypto", module)
        (tmp_path / "credential-cache.key").write_bytes(kek.WRAP_MAGIC + b"D" + dpapi(True, b"key"))
        (tmp_path / "credential-cache.enc").write_bytes(b"F" + data)
    return SimpleNamespace(
        root=tmp_path, state=state, security=security, dpapi=dpapi,
        open=lambda: cache.PersistentCache(tmp_path),
        primary=tmp_path / "credential-cache.enc",
        machine=tmp_path / cache_machine.DIRECTORY / cache_machine.FILENAME,
    )


def prepare(protected):
    assert cache_machine.prepare(protected.open()) == {"enabled": True, "populated": True}


def test_default_off_keeps_original_decryption_failure(protected):
    protected.state.user_unavailable = True
    before = protected.primary.read_bytes()
    with pytest.raises(CacheDecryptionUnavailable):
        protected.open().get("Allowed/token", readonly=True)
    assert protected.primary.read_bytes() == before
    assert not protected.machine.parent.exists()


def test_additive_preparation_and_primary_first_reads(protected):
    before = {path.name: path.read_bytes() for path in protected.root.iterdir() if path.is_file()}
    prepare(protected)
    assert {
        name: (protected.root / name).read_bytes() for name in before
    } == before
    assert protected.machine.read_bytes().startswith(cache_machine.MAGIC + b"M")
    assert (True, True) in protected.state.calls
    protected.machine.write_bytes(b"corrupt redundant data")
    assert protected.open().get("Allowed/token", readonly=True) == "fixture-value"


@pytest.mark.parametrize("readonly", [False, True])
def test_fallback_is_read_only_even_for_ordinary_callers(protected, readonly):
    prepare(protected)
    before = protected.primary.read_bytes()
    protected.state.user_unavailable = True
    persistent = protected.open()
    assert persistent.get("Allowed/token", readonly=readonly) == "fixture-value"
    assert not persistent.put("Allowed/token", "password", "replacement")
    assert not persistent.invalidate("Allowed/token")
    assert not persistent.begin_replace("Allowed/token", "password", "candidate", 1)
    assert protected.primary.read_bytes() == before


@pytest.mark.parametrize("damage", [
    "primary", "key", "missing-primary", "ciphertext", "acl", "machine-dpapi", "schema",
])
def test_unqualified_redundant_data_is_never_served(protected, damage):
    prepare(protected)
    if damage == "primary":
        protected.primary.write_bytes(protected.primary.read_bytes() + b"changed")
    elif damage == "key":
        key = protected.root / "credential-cache.key"
        key.write_bytes(kek.WRAP_MAGIC + b"DUdifferent")
    elif damage == "missing-primary":
        protected.primary.unlink()
    elif damage == "ciphertext":
        protected.machine.write_bytes(b"not a machine envelope")
    elif damage == "acl":
        protected.security.refuse = True
    elif damage == "schema":
        envelope = json.loads(protected.machine.read_bytes()[len(cache_machine.MAGIC) + 1:])
        envelope["store"]["v"] = 99
        protected.machine.write_bytes(
            cache_machine.MAGIC + protected.dpapi(True, json.dumps(envelope).encode(), machine=True),
        )
    else:
        protected.state.machine_unavailable = True
    protected.state.user_unavailable = True
    with pytest.raises(CacheProtectionError):
        protected.open().get("Allowed/token", readonly=True, machine_only=True)


def test_old_writer_change_invalidates_the_machine_copy(protected):
    prepare(protected)
    original = protected.primary.read_bytes()
    assert protected.open().put("Allowed/token", "password", "new-value")
    current_copy = protected.machine.read_bytes()
    protected.primary.write_bytes(original)
    protected.state.user_unavailable = True
    with pytest.raises(CacheProtectionError, match="does not match"):
        protected.open().get("Allowed/token", readonly=True)
    assert protected.machine.read_bytes() == current_copy


def test_partial_primary_publication_refuses_both_stale_and_future_copy(protected, monkeypatch):
    prepare(protected)
    original = protected.primary.read_bytes()
    replace = os.replace

    def interrupted(source, target):
        if Path(target) == protected.primary:
            raise OSError("fixture primary publication interrupted")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", interrupted)
    assert not protected.open().put("Allowed/token", "password", "candidate")
    assert protected.primary.read_bytes() == original
    protected.state.user_unavailable = True
    with pytest.raises(CacheProtectionError, match="does not match"):
        protected.open().get("Allowed/token", readonly=True)


def test_machine_publication_failure_preserves_the_primary(protected):
    prepare(protected)
    original = protected.primary.read_bytes()
    protected.security.refuse = True
    assert not protected.open().put("Allowed/token", "password", "candidate")
    assert protected.primary.read_bytes() == original


def test_rotation_invalidation_and_clear_share_the_redundant_writer(protected):
    prepare(protected)
    assert protected.open().begin_replace("Allowed/token", "password", "candidate", 1)
    assert protected.open().get("Allowed/token", readonly=True, machine_only=True) is None
    assert protected.open().complete_replace("Allowed/token", "password", "candidate", 1, 2)
    assert protected.open().get("Allowed/token", readonly=True, machine_only=True) == "candidate"
    assert protected.open().invalidate("Allowed/token")
    assert protected.open().get("Allowed/token", readonly=True, machine_only=True) is None
    assert protected.open().put("Allowed/token", "password", "fresh")
    assert protected.open().clear()
    assert not protected.machine.exists()
    assert protected.machine.parent.is_dir()
    assert protected.open().get("Allowed/token", readonly=True) is None
    with pytest.raises(CacheProtectionError):
        protected.open().get("Allowed/token", readonly=True, machine_only=True)


def test_service_collection_is_included_without_promoting_it_to_direct_entries(protected):
    prepare(protected)
    persistent = protected.open()
    store = persistent._read_store(readonly=True, strict=True)
    store["service_entries"] = {"bound-subject": {"value": "service-fixture"}}
    assert persistent._write_store(store)
    actual = cache_machine.read_store(protected.open())
    assert actual["service_entries"] == store["service_entries"]
    assert "bound-subject" not in actual["entries"]


def test_corrupt_primary_schema_does_not_authorize_fallback(protected):
    prepare(protected)
    persistent = protected.open()
    cipher = persistent._get_fernet(readonly=True)
    protected.primary.write_bytes(cipher.encrypt(b'{"v":99,"entries":{}}'))
    with pytest.raises(CacheProtectionError, match="schema"):
        protected.open().get("Allowed/token", readonly=True)


def test_verify_machine_path_exposes_only_counts_and_never_uses_user_dpapi(protected, monkeypatch, capsys):
    prepare(protected)
    protected.state.user_unavailable = True
    protected.state.calls.clear()
    monkeypatch.setattr(cache, "get_cache", protected.open)
    monkeypatch.setattr(cache_machine_cli, "command_entries", lambda _args: [("token", "password")])
    monkeypatch.setattr(config, "resolve_context", lambda: SimpleNamespace(group="Allowed"))
    assert cache_machine_cli.cmd_cache_verify(
        SimpleNamespace(machine_fallback=True, json=True),
    ) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"present": 1, "total": 1, "missing": []}
    assert captured.err == ""
    assert protected.state.calls == [(False, False)]
    assert "fixture-value" not in captured.out


def test_prepare_requires_explicit_risk_acceptance(protected, monkeypatch, capsys):
    monkeypatch.setattr(cache, "get_cache", lambda: pytest.fail("unaccepted cache access"))
    assert cache_machine_cli.cmd_cache_machine_prepare(SimpleNamespace()) == 2
    assert "--accept-machine-scope" in capsys.readouterr().err
    assert not protected.machine.exists()


def test_empty_store_can_opt_in_before_population(protected, monkeypatch):
    from agent_vault.cache_dpapi import DpapiCacheCipher

    protected.primary.unlink()
    (protected.root / "credential-cache.key").unlink(missing_ok=True)
    persistent = protected.open()
    assert cache_machine.prepare(persistent) == {"enabled": True, "populated": False}
    assert not protected.primary.exists()
    assert not protected.machine.exists()
    monkeypatch.setattr(cache.PersistentCache, "_get_fernet", lambda *_a, **_kw: DpapiCacheCipher())
    assert persistent.put("Allowed/token", "password", "first-value")
    protected.state.user_unavailable = True
    assert protected.open().get("Allowed/token", readonly=True) == "first-value"
