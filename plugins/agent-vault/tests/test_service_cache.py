"""Protected service cache access is distinct from master-password retention."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_vault import cache, cli, config, credential_reads, extensions, service, service_cache
from agent_vault.cache_dpapi import CacheDecryptionUnavailable, CacheProtectionError
from agent_vault.extensions import ExtensionRegistry


@pytest.fixture
def cached_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> tuple[service.VaultService, cache.PersistentCache, str, list[float]]:
    monkeypatch.setenv("AGENT_VAULT_CACHE", "1")
    monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AGENT_VAULT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(config.PINNED_CONTEXT_ENV, raising=False)
    monkeypatch.setattr(cache.PersistentCache, "_crypto_available", lambda _self: True)
    registry = ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr(extensions, "_REGISTRY", registry)
    clock = [1000.0]
    monkeypatch.setattr(service.time, "time", lambda: clock[0])
    monkeypatch.setattr(service, "PASSWORD_TTL", 10)
    kpdb = str(tmp_path / "fixture.kdbx")
    Path(kpdb).touch()
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace(group="Allowed"))
    monkeypatch.setattr(service, "resolve_kpdb", lambda **_kw: kpdb)
    monkeypatch.setattr(config, "resolve_context", lambda **_kw: SimpleNamespace(group="Allowed"))

    class FixtureCipher:
        def encrypt(self, data: bytes) -> bytes:
            return b"fixture-ciphertext:" + data

        def decrypt(self, data: bytes) -> bytes:
            assert data.startswith(b"fixture-ciphertext:")
            return data[len(b"fixture-ciphertext:"):]

    persistent = cache.PersistentCache()
    persistent._fernet = FixtureCipher()
    (persistent._base_dir).mkdir()
    persistent._key_file.write_bytes(b"fixture-key")
    monkeypatch.setattr(
        persistent, "_get_fernet", lambda **_kw: persistent._fernet,
    )
    monkeypatch.setattr(
        "agent_vault.kek.is_wrapped", lambda _blob: True,
    )
    monkeypatch.setattr(service, "get_cache", lambda: persistent)
    vault = service.VaultService()
    vault._default_kpdb = kpdb
    vault.cli._cli_path = "synthetic-keepass"
    vault.cli.set_password(kpdb, "fixture-master")
    vault._password_set_at[kpdb] = clock[0]
    monkeypatch.setattr(vault.cli, "get_entry", lambda *_args: "fixture-value")
    return vault, persistent, kpdb, clock


def _get(vault: service.VaultService, **overrides: object) -> dict:
    return vault.handle_request({
        "action": "get", "entry": "entry", "group": "Allowed", **overrides,
    })


def test_service_cache_retains_attributed_hit_and_generation_after_password_expiry(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    first = _get(vault)
    assert first["ok"] and first["value"] == "fixture-value"
    assert persistent.get_record("Allowed/entry")["service_binding"] == service_cache.binding_for(
        kpdb, "Allowed",
    )
    clock[0] += 10
    monkeypatch.setattr(
        vault, "ensure_unlocked", lambda *_a, **_kw: pytest.fail("cached read tried to unlock"),
    )
    second = _get(vault)
    assert second == first
    assert not vault.cli.has_password(kpdb)
    assert kpdb not in vault._password_set_at


def test_replacement_service_reuses_protected_record_without_master_or_memory_cache(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, _clock = cached_service
    expected = _get(vault)
    replacement = service.VaultService()
    replacement._default_kpdb = kpdb
    monkeypatch.setattr(
        replacement, "ensure_unlocked", lambda *_a, **_kw: pytest.fail("replacement tried to unlock"),
    )
    assert not replacement.cache and not replacement.cli.has_password(kpdb)
    assert _get(replacement) == expected


@pytest.mark.parametrize("mismatch", ["database", "group", "field", "installation", "unattributed"])
def test_cache_binding_mismatch_is_not_relabelled_or_served(
    cached_service, monkeypatch: pytest.MonkeyPatch, mismatch: str,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    assert _get(vault)["ok"]
    before = persistent._cache_file.read_bytes()
    request: dict[str, object] = {}
    if mismatch == "database":
        request["kpdb"] = str(Path(kpdb).with_name("other.kdbx"))
    elif mismatch == "group":
        request.update(group="Other", entry="Allowed/entry")
    elif mismatch == "field":
        request["field"] = "username"
    elif mismatch == "installation":
        monkeypatch.setattr(config, "installation_id", lambda: "other-cell")
    else:
        store = persistent._read_store()
        del store["entries"]["Allowed/entry"]["password"]["service_binding"]
        assert persistent._write_store(store)
        before = persistent._cache_file.read_bytes()
    clock[0] += 10
    monkeypatch.setattr(vault, "ensure_unlocked", lambda *_a, **_kw: False)
    assert _get(vault, **request)["ok"] is False
    assert persistent._cache_file.read_bytes() == before


def test_dpapi_unavailable_client_uses_actual_locked_service_without_cache_writeback(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, clock = cached_service
    first = _get(vault)
    clock[0] += 10
    requests = []

    class UnreadableClientCache:
        enabled = True

        def get(self, *_a: object, **_kw: object) -> None:
            raise CacheDecryptionUnavailable("private DPAPI diagnostic")

        def put(self, *_a: object, **_kw: object) -> bool:
            pytest.fail("caller attempted cache writeback")

    monkeypatch.setattr(cache, "get_cache", UnreadableClientCache)
    monkeypatch.setattr(cli, "ensure_service", lambda: True)

    def selected(request: dict, timeout: object = None) -> dict:
        requests.append(request)
        return vault.handle_request({**request, "group": "Allowed"})

    monkeypatch.setattr(cli, "send_command", selected)
    result = credential_reads.read_credential(SimpleNamespace(entry="entry", field="password"))
    assert result.code == 0 and result.value == first["value"]
    assert result.warning.startswith("cache_decryption_unavailable;")
    assert len(requests) == 1 and not vault.cli.has_password(kpdb)
    requests.clear()
    refused = credential_reads.read_credential(
        SimpleNamespace(entry="entry", field="password", cache_only=True),
    )
    assert refused.code == 1 and not requests


def test_authoritative_refresh_never_serves_cached_value_and_invalidates_old_record(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, _kpdb, clock = cached_service
    assert _get(vault)["ok"]
    clock[0] += 10
    monkeypatch.setattr(vault, "ensure_unlocked", lambda *_a, **_kw: False)
    assert _get(vault, refresh=True)["ok"] is False
    assert persistent.get_record("Allowed/entry") is None
    assert _get(vault)["ok"] is False


def test_freshness_clear_and_pending_rotation_cannot_return_stale_service_memory(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, _kpdb, clock = cached_service
    first = _get(vault)
    assert first["ok"]
    clock[0] += 10
    assert persistent.get_record("Allowed/entry", max_age_seconds=5) is None
    assert persistent.begin_replace("Allowed/entry", "password", "candidate", first["generation"] + 1)
    monkeypatch.setattr(vault, "ensure_unlocked", lambda *_a, **_kw: False)
    assert _get(vault)["ok"] is False
    assert persistent.clear()
    assert _get(vault)["ok"] is False


def test_explicit_lock_revokes_cache_but_retains_unrelated_database_records(
    cached_service,
) -> None:
    vault, persistent, kpdb, _clock = cached_service
    assert _get(vault)["ok"]
    other = str(Path(kpdb).with_name("other.kdbx"))
    assert persistent.put(
        "Allowed/other", "password", "other-value", 1,
        service_binding=service_cache.binding_for(other, "Allowed"),
    )
    assert vault.handle_request({"action": "lock", "kpdb": kpdb})["ok"]
    assert not vault.cli.has_password(kpdb)
    assert persistent.get_record("Allowed/entry") is None
    assert persistent.get("Allowed/other") == "other-value"


def test_revocation_failure_still_locks_master_and_refuses_retained_cache(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, _clock = cached_service
    assert _get(vault)["ok"]
    monkeypatch.setattr(persistent, "invalidate_service_bindings", lambda _prefix: False)
    response = vault.handle_request({"action": "lock", "kpdb": kpdb})
    assert response == {"ok": False, "error": service_cache.UNAVAILABLE}
    assert not vault.cli.has_password(kpdb) and not vault.cache
    assert _get(vault) == response


def test_protection_failure_has_fixed_credential_free_response(
    cached_service, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    vault, persistent, _kpdb, _clock = cached_service

    def refused(*_args: object, **_kwargs: object) -> None:
        raise CacheProtectionError("private credential-bearing diagnostic")

    monkeypatch.setattr(persistent, "get_record", refused)
    assert _get(vault) == {"ok": False, "error": service_cache.UNAVAILABLE}
    assert "private credential-bearing diagnostic" not in caplog.text


def test_protected_snapshot_value_and_generation_are_returned_together(cached_service) -> None:
    _vault, persistent, _kpdb, _clock = cached_service
    assert persistent.put("Allowed/entry", "password", "old", 5)
    record = persistent.get_record("Allowed/entry")
    assert persistent.replace("Allowed/entry", "password", "new", 6)
    assert (record["value"], record["generation"]) == ("old", 5)
    assert persistent.get_record("Allowed/entry")["generation"] == 6


def test_service_cache_write_failure_is_not_reported_as_cached(cached_service, monkeypatch) -> None:
    vault, persistent, _kpdb, _clock = cached_service
    monkeypatch.setattr(persistent, "put", lambda *_a, **_kw: False)
    response = _get(vault)
    assert response["ok"] and response["cache_warning"] == "service_cache_write_failed"


@pytest.mark.parametrize("operation", ["put", "replace", "complete", "reconcile"])
def test_client_writeback_retains_exact_service_attribution_and_original_age(
    cached_service, operation: str,
) -> None:
    vault, persistent, _kpdb, clock = cached_service
    response = _get(vault)
    original = persistent.get_record("Allowed/entry")
    clock[0] += 5
    args = ("Allowed/entry", "password", response["value"])
    generation = response["generation"]
    if operation == "complete":
        assert persistent.complete_replace(*args, 1, generation)
    else:
        method = {
            "put": persistent.put,
            "replace": persistent.replace,
            "reconcile": persistent.reconcile_authoritative,
        }[operation]
        assert method(*args, generation)
    assert persistent.get_record("Allowed/entry") == original


@pytest.mark.parametrize("action", ["set-username", "remove", "move"])
def test_backend_mutation_invalidates_service_cache_before_an_uncertain_result(
    cached_service, monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    vault, persistent, _kpdb, _clock = cached_service
    assert _get(vault)["ok"]

    def uncertain(*_args: object) -> tuple[bool, str]:
        assert persistent.get_record("Allowed/entry") is None
        return False, "backend outcome unavailable"

    backend = {"set-username": "edit_username", "remove": "remove_entry", "move": "move_entry"}[action]
    monkeypatch.setattr(vault.cli, backend, uncertain)
    response = vault.handle_request({
        "action": action, "entry": "entry", "group": "Allowed",
        "username": "fixture-user", "dest": "Allowed/New",
    })
    assert response["ok"] is False
    assert persistent.get_record("Allowed/entry") is None


def test_password_rotation_remains_available_from_service_cache_after_expiry(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, _kpdb, clock = cached_service
    assert _get(vault)["ok"]
    monkeypatch.setattr(vault.cli, "edit_password", lambda *_args: (True, "updated"))
    changed = vault.handle_request({
        "action": "set-password", "entry": "entry", "group": "Allowed",
        "password": "fixture-updated",
    })
    assert changed["ok"]
    clock[0] += 10
    assert _get(vault)["value"] == "fixture-updated"
    assert persistent.get_record("Allowed/entry")["generation"] == changed["generation"]


@pytest.mark.parametrize("operation", ["refresh", "mutation", "lock"])
def test_unknown_password_replacement_remains_journalled_and_cannot_restore_revoked_binding(
    cached_service, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    vault, persistent, kpdb, _clock = cached_service
    first = _get(vault)
    pending = first["generation"] + 1
    assert persistent.begin_replace("Allowed/entry", "password", "candidate", pending)
    if operation == "refresh":
        monkeypatch.setattr(vault, "ensure_unlocked", lambda *_a, **_kw: False)
        assert _get(vault, refresh=True)["ok"] is False
    elif operation == "mutation":
        monkeypatch.setattr(vault.cli, "edit_username", lambda *_a: pytest.fail("unknown rotation lost"))
        assert vault.handle_request({
            "action": "set-username", "entry": "entry", "group": "Allowed", "username": "new",
        })["ok"] is False
    else:
        assert vault.handle_request({"action": "lock", "kpdb": kpdb})["ok"]
    assert persistent.pending_replace("Allowed/entry", "password")["operation"] == pending
    assert persistent.rollback_replace("Allowed/entry", "password", pending)
    if operation == "lock":
        assert "service_binding" not in persistent.get_record("Allowed/entry")
