"""Protected service cache access is distinct from master-password retention."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_vault import cache, cli, config, credential_reads, extensions, service, service_cache
from agent_vault.cache_dpapi import CacheDecryptionUnavailable, CacheProtectionError
from agent_vault.extensions import ExtensionRegistry
from agent_vault.service_cache_store import COLLECTION, ServiceCacheStore


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


def _store(
    persistent: cache.PersistentCache, kpdb: str, group: str = "Allowed",
) -> ServiceCacheStore:
    return ServiceCacheStore(persistent, kpdb, group)


def test_service_cache_retains_attributed_hit_and_generation_after_password_expiry(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    first = _get(vault)
    assert first["ok"] and first["value"] == "fixture-value"
    assert _store(persistent, kpdb).record("entry")["binding"] == service_cache.binding_for(
        kpdb, "Allowed", "Allowed/entry", "password",
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
        store[COLLECTION].clear()
        store["entries"]["Allowed/entry"] = {"password": {"value": "legacy", "generation": 1}}
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


def test_authoritative_refresh_never_serves_cached_value_or_destroys_current_record(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    assert _get(vault)["ok"]
    before = _store(persistent, kpdb).record("entry")
    clock[0] += 10
    monkeypatch.setattr(vault, "ensure_unlocked", lambda *_a, **_kw: False)
    assert _get(vault, refresh=True)["ok"] is False
    assert _store(persistent, kpdb).record("entry") == before
    assert _get(vault)["ok"]


def test_freshness_clear_and_pending_rotation_cannot_return_stale_service_memory(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    first = _get(vault)
    assert first["ok"]
    clock[0] += 10
    assert _store(persistent, kpdb).record("entry", max_age_seconds=5) is None
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
    assert _store(persistent, other).put("other", "password", "other-value", 1)
    assert vault.handle_request({"action": "lock", "kpdb": kpdb})["ok"]
    assert not vault.cli.has_password(kpdb)
    assert _store(persistent, kpdb).record("entry") is None
    assert _store(persistent, other).record("other")["value"] == "other-value"


def test_revocation_failure_still_locks_master_and_refuses_retained_cache(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, _clock = cached_service
    assert _get(vault)["ok"]
    monkeypatch.setattr(service_cache, "revoke_records", lambda *_args: False)
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

    monkeypatch.setattr(ServiceCacheStore, "record", refused)
    assert _get(vault) == {"ok": False, "error": service_cache.UNAVAILABLE}
    assert "private credential-bearing diagnostic" not in caplog.text


def test_protected_snapshot_value_and_generation_are_returned_together(cached_service) -> None:
    _vault, persistent, kpdb, _clock = cached_service
    scoped = _store(persistent, kpdb)
    assert scoped.put("entry", "password", "old", 5)
    record = scoped.record("entry")
    assert scoped.put("entry", "password", "new", 6)
    assert (record["value"], record["generation"]) == ("old", 5)
    assert scoped.record("entry")["generation"] == 6


def test_service_cache_write_failure_is_not_reported_as_cached(cached_service, monkeypatch) -> None:
    vault, persistent, _kpdb, _clock = cached_service
    monkeypatch.setattr(ServiceCacheStore, "put", lambda *_a, **_kw: False)
    response = _get(vault)
    assert response["ok"] and response["cache_warning"] == "service_cache_write_failed"


@pytest.mark.parametrize("operation", ["put", "replace", "complete", "reconcile"])
def test_client_writeback_retains_exact_service_attribution_and_original_age(
    cached_service, operation: str,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    response = _get(vault)
    original = _store(persistent, kpdb).record("entry")
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
    assert _store(persistent, kpdb).record("entry") == original


@pytest.mark.parametrize("action", ["set-username", "remove", "move"])
def test_backend_mutation_invalidates_service_cache_before_an_uncertain_result(
    cached_service, monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    vault, persistent, kpdb, _clock = cached_service
    assert _get(vault)["ok"]

    def uncertain(*_args: object) -> tuple[bool, str]:
        assert _store(persistent, kpdb).record("entry") is None
        return False, "backend outcome unavailable"

    backend = {"set-username": "edit_username", "remove": "remove_entry", "move": "move_entry"}[action]
    monkeypatch.setattr(vault.cli, backend, uncertain)
    response = vault.handle_request({
        "action": action, "entry": "entry", "group": "Allowed",
        "username": "fixture-user", "dest": "Allowed/New",
    })
    assert response["ok"] is False
    assert _store(persistent, kpdb).record("entry") is None


def test_password_rotation_remains_available_from_service_cache_after_expiry(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    assert _get(vault)["ok"]
    monkeypatch.setattr(vault.cli, "edit_password", lambda *_args: (True, "updated"))
    changed = vault.handle_request({
        "action": "set-password", "entry": "entry", "group": "Allowed",
        "password": "fixture-updated",
    })
    assert changed["ok"]
    clock[0] += 10
    assert _get(vault)["value"] == "fixture-updated"
    assert _store(persistent, kpdb).record("entry")["generation"] == changed["generation"]


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
    assert _store(persistent, kpdb).record("entry") is None


def test_service_database_reads_do_not_poison_a_legacy_direct_readers_entry(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, first_db, clock = cached_service
    second_db = str(Path(first_db).with_name("second.kdbx"))
    assert persistent.put("Allowed/entry", "password", "legacy-first-value", 1)
    vault.cli.set_password(second_db, "synthetic-other-master")
    vault._password_set_at[second_db] = clock[0]
    monkeypatch.setattr(
        vault.cli, "get_entry",
        lambda database, _entry, _field: "first-value" if database == first_db else "second-value",
    )
    assert _get(vault, kpdb=first_db)["value"] == "first-value"
    assert _get(vault, kpdb=second_db)["value"] == "second-value"
    monkeypatch.setattr(cache, "get_cache", lambda: persistent)
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("direct hit contacted service"))
    result = credential_reads.read_credential(
        SimpleNamespace(entry="entry", field="password", cache_only=True),
    )
    assert result.code == 0 and result.value == "legacy-first-value"
    clock[0] += 10
    assert _get(vault, kpdb=first_db)["value"] == "first-value"
    assert _get(vault, kpdb=second_db)["value"] == "second-value"


@pytest.mark.parametrize("subject", ["entry", "field"])
def test_migration_or_record_rekeying_cannot_change_a_service_records_subject(
    cached_service, monkeypatch: pytest.MonkeyPatch, subject: str,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    assert _get(vault, entry="token", group="")["ok"]
    assert not persistent.migrate_entry("token", "Allowed/token", "password")
    store = persistent._read_store()
    original = next(iter(store[COLLECTION]))
    entry, field = ("Allowed/token", "password") if subject == "entry" else ("token", "username")
    destination = service_cache.binding_for(kpdb, "", entry, field)
    store[COLLECTION][destination] = store[COLLECTION].pop(original)
    assert persistent._write_store(store)
    clock[0] += 10
    monkeypatch.setattr(vault, "ensure_unlocked", lambda *_a, **_kw: False)
    assert not _get(vault, entry=entry, field=field, group="")["ok"]


def test_standard_field_aliases_share_password_rotation_and_pending_journal(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    assert _get(vault, field="Password")["ok"]
    monkeypatch.setattr(vault.cli, "edit_password", lambda *_args: (True, "updated"))
    changed = vault.handle_request({
        "action": "set-password", "entry": "entry", "group": "Allowed", "password": "updated-value",
    })
    assert changed["ok"]
    clock[0] += 10
    assert _get(vault, field="Password")["value"] == "updated-value"
    operation = changed["generation"] + 1
    assert persistent.begin_replace("Allowed/entry", "password", "candidate", operation)
    monkeypatch.setattr(vault, "ensure_unlocked", lambda *_a, **_kw: False)
    assert not _get(vault, field="Password")["ok"]
    assert persistent.rollback_replace("Allowed/entry", "password", operation)
    assert _store(persistent, kpdb).record("entry", "Password") is None


@pytest.mark.parametrize("locked", [False, True])
def test_actual_service_recovery_read_preserves_the_callers_existing_record(
    cached_service, monkeypatch: pytest.MonkeyPatch, locked: bool,
) -> None:
    from agent_vault import cache_recovery

    vault, persistent, _kpdb, clock = cached_service
    assert persistent.put("Allowed/entry", "password", "current-recovery-value", 50)
    before = persistent._read_store()["entries"]
    if locked:
        clock[0] += 10
        monkeypatch.setattr(vault, "ensure_unlocked", lambda *_a, **_kw: False)
    response = _get(vault, refresh=True)
    if not locked:
        assert response["ok"]
        monkeypatch.setattr(cache_recovery, "require_source", lambda *_args: None)
        assert persistent.recover_missing(
            "Allowed/entry", "password", response["value"], response["generation"],
        )
    else:
        assert not response["ok"]
    assert persistent._read_store()["entries"] == before


@pytest.mark.parametrize("action", ["lock", "set-username", "remove", "move", "set-password"])
def test_unavailable_protection_never_reports_revocation_or_mutation_success(
    cached_service, monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    assert _get(vault)["ok"]
    for operation in ("edit_username", "remove_entry", "move_entry", "edit_password"):
        monkeypatch.setattr(vault.cli, operation, lambda *_a: pytest.fail("backend mutation ran"))
    with monkeypatch.context() as unavailable:
        unavailable.setattr(persistent, "_crypto_available", lambda: False)
        response = vault.handle_request({
            "action": action, "entry": "entry", "kpdb": kpdb, "group": "Allowed",
            "username": "new", "password": "new", "dest": "Allowed/New",
        })
    assert response == {"ok": False, "error": service_cache.UNAVAILABLE}
    if action == "lock":
        assert not vault.cli.has_password(kpdb)
    clock[0] += 10
    replacement = service.VaultService()
    monkeypatch.setattr(replacement, "ensure_unlocked", lambda *_a, **_kw: False)
    assert not _get(replacement, kpdb=kpdb)["ok"]


def test_custom_field_case_remains_distinct_while_standard_aliases_are_canonical(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, _kpdb, clock = cached_service
    monkeypatch.setattr(vault.cli, "get_entry", lambda _db, _entry, field: "value-" + field)
    assert _get(vault, field="Token")["value"] == "value-Token"
    assert _get(vault, field="token")["value"] == "value-token"
    assert _get(vault, field="UserName")["value"] == "value-username"
    clock[0] += 10
    assert _get(vault, field="Token")["value"] == "value-Token"
    assert _get(vault, field="token")["value"] == "value-token"
    assert _get(vault, field="username")["value"] == "value-username"


@pytest.mark.parametrize("invalid", [
    b'{"version":99,"epochs":{}}', b'{"version":99,"version":1,"epochs":{}}',
])
def test_malformed_revocation_metadata_is_not_replaced_or_treated_as_permission(
    cached_service, monkeypatch: pytest.MonkeyPatch, invalid: bytes,
) -> None:
    from agent_vault.service_cache_epochs import FILENAME

    vault, persistent, _kpdb, _clock = cached_service
    assert _get(vault)["ok"]
    path = persistent._base_dir / FILENAME
    path.write_bytes(invalid)
    monkeypatch.setattr(
        vault, "ensure_unlocked", lambda *_a, **_kw: pytest.fail("invalid metadata bypassed policy"),
    )
    assert _get(vault) == {"ok": False, "error": service_cache.UNAVAILABLE}
    assert path.read_bytes() == invalid
