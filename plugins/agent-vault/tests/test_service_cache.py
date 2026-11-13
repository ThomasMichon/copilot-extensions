"""Protected service cache access is distinct from master-password retention."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
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
@pytest.mark.parametrize("entry", ["entry", "uncached"])
def test_malformed_revocation_metadata_is_not_replaced_or_treated_as_permission(
    cached_service, monkeypatch: pytest.MonkeyPatch, invalid: bytes, entry: str,
) -> None:
    from agent_vault.service_cache_epochs import FILENAME

    vault, persistent, _kpdb, _clock = cached_service
    assert _get(vault)["ok"]
    path = persistent._base_dir / FILENAME
    path.write_bytes(invalid)
    monkeypatch.setattr(
        vault, "ensure_unlocked", lambda *_a, **_kw: pytest.fail("invalid metadata bypassed policy"),
    )
    assert _get(vault, entry=entry) == {"ok": False, "error": service_cache.UNAVAILABLE}
    assert path.read_bytes() == invalid


def test_alias_population_cache_only_and_verification_use_the_same_field(
    cached_service, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    vault, persistent, _kpdb, _clock = cached_service
    monkeypatch.setattr(cache, "get_cache", lambda: persistent)
    monkeypatch.setattr(persistent, "prepare", lambda: {"state": "compatible"})
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(
        cli, "send_command",
        lambda request, **_kw: vault.handle_request({**request, "group": "Allowed"}),
    )
    arguments = SimpleNamespace(
        entry=["entry:Password"], manifest=None, machine=None, prompt=False, verify=False,
    )
    assert cli.cmd_cache_populate(arguments) == 0
    assert persistent.get("Allowed/entry", "password") == "fixture-value"
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("cache-only contacted service"))
    assert credential_reads.read_credential(
        SimpleNamespace(entry="entry", field="Password", cache_only=True),
    ).value == "fixture-value"
    assert cli.cmd_cache_verify(arguments) == 0
    assert "fixture-value" not in capsys.readouterr().out


def test_legacy_alias_remains_readable_without_migration_but_cannot_bypass_a_pending_canonical_field(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vault, persistent, _kpdb, _clock = cached_service
    assert persistent.put("Allowed/entry", "Password", "legacy-alias-value", 1)
    before = persistent._cache_file.read_bytes()
    monkeypatch.setattr(cache, "get_cache", lambda: persistent)
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("cache-only contacted service"))
    arguments = SimpleNamespace(entry="entry", field="Password", cache_only=True)
    assert credential_reads.read_credential(arguments).value == "legacy-alias-value"
    assert persistent._cache_file.read_bytes() == before
    assert persistent.begin_replace("Allowed/entry", "password", "candidate", 2)
    assert credential_reads.read_credential(arguments).code == 1


def test_revocation_compaction_and_explicit_clear_retain_fences_without_exhausting_capacity(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_vault import service_cache_epochs

    vault, persistent, kpdb, _clock = cached_service
    assert _get(vault)["ok"]
    monkeypatch.setattr(service_cache_epochs, "MAX_BYTES", 800)
    for index in range(20):
        assert _store(persistent, kpdb).invalidate(f"other-{index}", "password")
    epochs = service_cache_epochs.read(persistent._base_dir)
    assert "all" in epochs
    assert (persistent._base_dir / service_cache_epochs.FILENAME).stat().st_size <= 800
    assert _store(persistent, kpdb).record("entry") is None
    assert persistent.clear()
    assert set(service_cache_epochs.read(persistent._base_dir)) == {"all"}
    assert _store(persistent, kpdb).put("entry", "password", "fresh-value", 50)
    assert _store(persistent, kpdb).record("entry")["value"] == "fresh-value"


def test_refresh_and_password_commit_generations_advance_beyond_persisted_values_after_clock_rollback(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    monkeypatch.setattr(service.time, "time_ns", lambda: 500)
    first = _get(vault)
    assert persistent.put("Allowed/entry", "password", "old-client-value", 2000)
    clock[0] = 900
    monkeypatch.setattr(vault.cli, "get_entry", lambda *_args: "refreshed-value")
    refreshed = _get(vault, refresh=True)
    assert refreshed["value"] == "refreshed-value"
    assert refreshed["generation"] > max(first["generation"], 2000)
    monkeypatch.setattr(vault.cli, "edit_password", lambda *_args: (True, "updated"))
    changed = vault.handle_request({
        "action": "set-password", "entry": "entry", "group": "Allowed", "password": "changed-value",
    })
    assert changed["ok"] and changed["generation"] > refreshed["generation"]
    assert persistent._read_store()["entries"]["Allowed/entry"]["password"]["generation"] == changed["generation"]
    clock[0] = 1010
    final = _get(vault)
    assert final["value"] == "changed-value" and final["generation"] == changed["generation"]
    assert not vault.cli.has_password(kpdb)


@pytest.mark.parametrize("action", ["set-password", "remove"])
def test_backend_equivalent_leading_slash_records_are_revoked_together(
    cached_service, monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    assert _get(vault, entry="/Allowed/entry")["ok"]
    stored = persistent._read_store()
    retained = dict(next(iter(stored[COLLECTION].values())))
    retained["entry"] = "/Allowed/entry"
    stored[COLLECTION]["retained-alternate-spelling"] = retained
    assert persistent._write_store(stored)
    monkeypatch.setattr(vault.cli, "edit_password", lambda *_args: (True, "updated"))
    monkeypatch.setattr(vault.cli, "remove_entry", lambda *_args: (True, "removed"))
    response = vault.handle_request({
        "action": action, "entry": "Allowed/entry", "group": "Allowed", "password": "changed-value",
    })
    assert response["ok"]
    assert "retained-alternate-spelling" not in persistent._read_store()[COLLECTION]
    clock[0] += 10
    monkeypatch.setattr(vault, "ensure_unlocked", lambda *_a, **_kw: False)
    result = _get(vault, entry="/Allowed/entry")
    if action == "set-password":
        assert result["value"] == "changed-value"
    else:
        assert not result["ok"]
    assert not vault.cli.has_password(kpdb)


def test_absolute_bare_entry_is_not_reprefixed_by_service_cache_layers(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, _kpdb, clock = cached_service
    monkeypatch.setattr(vault.cli, "get_entry", lambda _db, entry, _field: "value-" + entry)
    assert _get(vault, entry="/token")["value"] == "value-token"
    assert _get(vault, entry="token")["value"] == "value-Allowed/token"
    clock[0] += 10
    assert _get(vault, entry="/token")["value"] == "value-token"
    assert _get(vault, entry="token")["value"] == "value-Allowed/token"


@pytest.mark.parametrize("compartments", [False, True])
@pytest.mark.parametrize("action", ["set-password", "remove"])
def test_shared_database_revocation_crosses_installation_bindings_and_selected_compartments(
    cached_service, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, compartments: bool, action: str,
) -> None:
    first, first_cache, kpdb, clock = cached_service
    second_cache = first_cache
    if compartments:
        second_cache = cache.PersistentCache(first_cache._root_dir)
        second_cache._select_base(first_cache._root_dir / "selected-second-compartment")
        second_cache._base_dir.mkdir()
        second_cache._key_file.write_bytes(b"fixture-key")
        second_cache._fernet = first_cache._fernet
        monkeypatch.setattr(second_cache, "_refresh_selection", lambda: None)
        monkeypatch.setattr(second_cache, "_get_fernet", lambda **_kw: second_cache._fernet)
    second = service.VaultService()
    second.cli.set_password(kpdb, "fixture-master")
    second._password_set_at[kpdb] = clock[0]
    monkeypatch.setattr(second.cli, "get_entry", lambda *_args: "fixture-value")
    monkeypatch.setattr(first.cli, "edit_password", lambda *_args: (True, "updated"))
    monkeypatch.setattr(first.cli, "remove_entry", lambda *_args: (True, "removed"))

    @contextlib.contextmanager
    def cell(name: str, selected: cache.PersistentCache) -> Iterator[None]:
        with monkeypatch.context() as context:
            context.setattr(config, "home_dir", lambda: tmp_path / name)
            context.setattr(config, "installation_id", lambda: name)
            context.setattr(service, "get_cache", lambda: selected)
            yield

    with cell("first", first_cache):
        assert _get(first)["ok"]
    with cell("second", second_cache):
        assert _get(second)["ok"]
    with cell("first", first_cache):
        assert first.handle_request({
            "action": action, "entry": "entry", "group": "Allowed", "password": "changed-value",
        })["ok"]
    clock[0] += 10
    monkeypatch.setattr(second, "ensure_unlocked", lambda *_a, **_kw: False)
    with cell("second", second_cache):
        assert not _get(second)["ok"]


@pytest.mark.parametrize("action", ["get", "lock", "add", "set-username", "remove", "move", "set-password"])
def test_cache_construction_failure_has_an_authoritative_service_response(
    cached_service, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, action: str,
) -> None:
    vault, _persistent, kpdb, _clock = cached_service
    assert _get(vault)["ok"]

    def unavailable() -> cache.PersistentCache:
        raise CacheProtectionError("private recovery selection diagnostic")

    for operation in ("add_entry", "edit_username", "remove_entry", "move_entry", "edit_password"):
        monkeypatch.setattr(vault.cli, operation, lambda *_a, **_kw: pytest.fail("backend mutation ran"))
    with monkeypatch.context() as context:
        context.setattr(service, "get_cache", service_cache.get_cache)
        context.setattr(service_cache, "_raw_get_cache", unavailable)
        response = vault.handle_request({
            "action": action, "kpdb": kpdb, "entry": "entry", "group": "Allowed",
            "username": "new", "password": "new", "dest": "Allowed/New",
        })
    assert response == {"ok": False, "error": service_cache.UNAVAILABLE}
    assert "private recovery selection diagnostic" not in caplog.text
    if action == "lock":
        assert not vault.cli.has_password(kpdb)
        assert _get(vault) == response


def test_shared_backend_custody_orders_a_refresh_after_another_service_mutation(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, _persistent, kpdb, clock = cached_service
    second = service.VaultService()
    second.cli.set_password(kpdb, "fixture-master")
    second._password_set_at[kpdb] = clock[0]
    value = ["old-value"]
    entered, release, read = threading.Event(), threading.Event(), threading.Event()

    def mutate(*_args: object) -> tuple[bool, str]:
        entered.set()
        assert release.wait(4)
        value[0] = "changed-value"
        return True, "updated"

    def refresh(*_args: object) -> str:
        read.set()
        return value[0]

    monkeypatch.setattr(first.cli, "edit_password", mutate)
    monkeypatch.setattr(second.cli, "get_entry", refresh)
    with ThreadPoolExecutor(max_workers=2) as workers:
        mutation = workers.submit(first.handle_request, {
            "action": "set-password", "entry": "entry", "group": "Allowed", "password": "changed-value",
        })
        try:
            assert entered.wait(2)
            refreshing = workers.submit(_get, second, refresh=True)
            assert not read.wait(0.1)
        finally:
            release.set()
        assert mutation.result(timeout=4)["ok"]
        assert refreshing.result(timeout=4)["value"] == "changed-value"


def test_mutation_deadline_still_prevents_backend_work_after_cache_journalling(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, _kpdb, _clock = cached_service
    monotonic = [0.0]
    original = vault._prepare_password_cache
    monkeypatch.setattr(service.time, "monotonic", lambda: monotonic[0])

    def prepare(*args: object) -> tuple:
        result = original(*args)
        monotonic[0] = 31.0
        return result

    monkeypatch.setattr(vault, "_prepare_password_cache", prepare)
    monkeypatch.setattr(vault.cli, "edit_password", lambda *_a: pytest.fail("late backend write"))
    response = vault.handle_request({
        "action": "set-password", "entry": "entry", "group": "Allowed", "password": "changed-value",
    })
    assert response["ok"] is False and "expired before the backend write" in response["error"]
    assert persistent.pending_replace("Allowed/entry", "password") is None


def test_alternate_spelling_pending_journal_is_preserved_instead_of_starting_another_mutation(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, _kpdb, _clock = cached_service
    assert persistent.begin_replace("/Allowed/entry", "password", "unknown-candidate", 5)
    monkeypatch.setattr(vault.cli, "edit_password", lambda *_a: pytest.fail("unknown mutation bypassed"))
    response = vault.handle_request({
        "action": "set-password", "entry": "Allowed/entry", "group": "Allowed", "password": "other",
    })
    assert response == {"ok": False, "error": service_cache.UNAVAILABLE}
    assert persistent.pending_replace("/Allowed/entry", "password")["operation"] == 5


def test_add_at_an_existing_path_cannot_leave_the_prior_service_value_cached(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, _kpdb, _clock = cached_service
    value = ["prior-value"]
    monkeypatch.setattr(vault.cli, "get_entry", lambda *_a: value[0])
    assert _get(vault)["value"] == "prior-value"

    def add(*_args: object, **_kwargs: object) -> tuple[bool, str]:
        value[0] = "added-value"
        return True, "added"

    monkeypatch.setattr(vault.cli, "add_entry", add)
    assert vault.handle_request({"action": "add", "entry": "entry", "group": "Allowed"})["ok"]
    assert _get(vault)["value"] == "added-value"


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("blocked", ["unlock", "entry-read"])
def test_inflight_backend_read_cannot_repopulate_the_service_cache_after_clear(
    cached_service, monkeypatch: pytest.MonkeyPatch, refresh: bool, blocked: str,
) -> None:
    vault, persistent, kpdb, _clock = cached_service
    if refresh:
        assert _get(vault)["ok"]
    entered, release = threading.Event(), threading.Event()

    def paused(*_args: object) -> str | bool:
        entered.set()
        assert release.wait(4)
        return True if blocked == "unlock" else "pre-clear-read-value"

    if blocked == "unlock":
        monkeypatch.setattr(vault, "ensure_unlocked", paused)
    else:
        monkeypatch.setattr(vault.cli, "get_entry", paused)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(_get, vault, refresh=refresh)
        try:
            assert entered.wait(2)
            assert persistent.clear()
        finally:
            release.set()
        response = pending.result(timeout=4)
    assert response == {"ok": False, "error": service_cache.UNAVAILABLE}
    assert not persistent._cache_file.exists()
    assert _store(persistent, kpdb).record("entry") is None


@pytest.mark.parametrize("journal_entry", ["Allowed/entry", "/Allowed/entry", "entry"])
def test_alias_pending_journal_blocks_an_existing_canonical_value_or_legacy_fallback(
    cached_service, monkeypatch: pytest.MonkeyPatch, journal_entry: str,
) -> None:
    _vault, persistent, _kpdb, _clock = cached_service
    canonical_entry = "entry" if journal_entry == "entry" else "Allowed/entry"
    assert persistent.put(canonical_entry, "password", "old-canonical", 1)
    assert persistent.put(journal_entry, "Password", "old-alias", 1)
    assert persistent.begin_replace(journal_entry, "Password", "unknown-candidate", 2)
    monkeypatch.setattr(cache, "get_cache", lambda: persistent)
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("cache-only contacted service"))
    for field in ("password", "Password"):
        response = credential_reads.read_credential(
            SimpleNamespace(entry="entry", field=field, cache_only=True),
        )
        assert response.code == 1 and response.value is None
    assert persistent.pending_replace(journal_entry, "Password")["operation"] == 2


def test_absolute_move_destination_invalidates_memory_when_persistent_caching_is_disabled(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, _clock = cached_service
    monkeypatch.delenv(cache.CACHE_ENABLE_ENV, raising=False)
    monkeypatch.delenv(cache.CACHE_DIR_ENV, raising=False)
    vault.cache[(kpdb, "Allowed/New/entry", "password")] = "prior-destination"
    monkeypatch.setattr(vault.cli, "move_entry", lambda *_args: (True, "moved"))
    monkeypatch.setattr(vault.cli, "get_entry", lambda *_args: "moved-value")
    response = vault.handle_request({
        "action": "move", "entry": "Allowed/Old/entry", "group": "Allowed",
        "dest": "/Allowed/New",
    })
    assert response["ok"]
    assert (kpdb, "Allowed/New/entry", "password") not in vault.cache
    assert _get(vault, entry="Allowed/New/entry")["value"] == "moved-value"


@pytest.mark.parametrize("invalid_field", ["password", "Password"])
def test_alias_journal_guard_preserves_strict_corruption_failure(
    cached_service, invalid_field: str,
) -> None:
    _vault, persistent, _kpdb, _clock = cached_service
    assert persistent.put("Allowed/entry", "password", "old-canonical", 1)
    assert persistent.put("Allowed/entry", "Password", "old-alias", 1)
    assert persistent.begin_replace("Allowed/entry", "password", "candidate", 2)
    store = persistent._read_store()
    store["entries"]["Allowed/entry"][invalid_field] = {"rotation": {}}
    assert persistent._write_store(store)
    before = persistent._cache_file.read_bytes()

    with pytest.raises(CacheProtectionError, match="record is invalid"):
        persistent.get("Allowed/entry", readonly=True)

    assert persistent._cache_file.read_bytes() == before
    assert persistent.get("Allowed/entry") is None


@pytest.mark.parametrize("invalid_field", ["password", "Password"])
@pytest.mark.parametrize("operation", ["record", "put"])
def test_service_store_rejects_malformed_journals_without_revoking_or_writing(
    cached_service, invalid_field: str, operation: str,
) -> None:
    _vault, persistent, kpdb, _clock = cached_service
    selected = _store(persistent, kpdb)
    assert type(selected.put("entry", "password", "service-value", 1)) is int
    for field in ("password", "Password"):
        assert persistent.begin_replace("Allowed/entry", field, "candidate", 2)
    store = persistent._read_store()
    store["entries"]["Allowed/entry"][invalid_field] = {"rotation": "invalid"}
    assert persistent._write_store(store)
    before = persistent._cache_file.read_bytes()
    revocation = selected.revocation_state("entry", "password")

    if operation == "record":
        with pytest.raises(CacheProtectionError, match="record is invalid"):
            selected.record("entry")
    else:
        assert selected.put("entry", "password", "replacement", 3) is None

    assert persistent._cache_file.read_bytes() == before
    assert selected.revocation_state("entry", "password") == revocation


def test_direct_client_cannot_reuse_a_root_credential_for_a_group_relative_lookup(
    cached_service, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    vault, persistent, _kpdb, _clock = cached_service
    monkeypatch.setattr(cache, "get_cache", lambda: persistent)
    monkeypatch.setattr(vault.cli, "get_entry", lambda _db, entry, _field: "value-" + entry)
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(
        cli, "send_command",
        lambda request, **_kw: vault.handle_request({**request, "group": "Allowed"}),
    )
    root = credential_reads.read_credential(SimpleNamespace(entry="/token", field="password"))
    assert root.code == 0 and root.value == "value-token"
    grouped = SimpleNamespace(entry="token", field="password", cache_only=True)
    with monkeypatch.context() as context:
        context.setattr(cli, "ensure_service", lambda: pytest.fail("cache-only contacted service"))
        refused = credential_reads.read_credential(grouped)
        before = persistent._cache_file.read_bytes()
        assert cli.cmd_cache_verify(SimpleNamespace(entry=["token"], json=True)) == 2
        assert json.loads(capsys.readouterr().out)["present"] == 0
        assert cli.cmd_cache_verify(SimpleNamespace(entry=["/token"], json=True)) == 0
        assert json.loads(capsys.readouterr().out)["present"] == 1
        assert persistent._cache_file.read_bytes() == before
    assert refused.code == 1 and refused.value is None

    relative = credential_reads.read_credential(SimpleNamespace(entry="token", field="password"))
    assert relative.code == 0 and relative.value == "value-Allowed/token"
    assert credential_reads.read_credential(grouped).value == "value-Allowed/token"
    assert credential_reads.read_credential(
        SimpleNamespace(entry="/token", field="password", cache_only=True),
    ).value == "value-token"


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("operation", ["failed-rotation", "population"])
def test_grouped_write_paths_preserve_root_values_and_journals(
    cached_service, monkeypatch: pytest.MonkeyPatch, pending: bool, operation: str,
) -> None:
    _vault, persistent, _kpdb, _clock = cached_service
    assert persistent.put("token", "password", "root-value", 1)
    if pending:
        assert persistent.begin_replace("token", "password", "root-candidate", 2)
    root = persistent._read_store()["entries"]["token"]
    monkeypatch.setattr(cache, "get_cache", lambda: persistent)
    monkeypatch.setattr(cli, "_ensure_unlocked_service", lambda: True)
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(persistent, "prepare", lambda: {"state": "ready"})

    def selected(request: dict, **_kwargs: object) -> dict:
        if operation == "failed-rotation":
            assert request["action"] == "set-password"
            return {"ok": False, "error": "rejected"}
        assert request["action"] == "get" and request["refresh"] is True
        return {"ok": True, "value": "group-value", "generation": 3}

    monkeypatch.setattr(cli, "send_command", selected)
    if operation == "failed-rotation":
        assert cli.cmd_set_password(SimpleNamespace(entry="token", password="group-candidate")) == 1
        assert persistent.get("Allowed/token") is None
    else:
        arguments = SimpleNamespace(
            entry=["token"], manifest=None, machine=None, verify=False, prompt=False,
        )
        assert cli.cmd_cache_populate(arguments) == 0
        assert persistent.get("Allowed/token") == "group-value"
    assert persistent._read_store()["entries"]["token"] == root


@pytest.mark.parametrize("journal_entry", ["Allowed/entry", "/Allowed/entry"])
@pytest.mark.parametrize("operation", ["refresh", "population"])
def test_confirmed_authority_reconciles_equivalent_alias_journals_atomically(
    cached_service, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    journal_entry: str, operation: str,
) -> None:
    _vault, persistent, _kpdb, _clock = cached_service
    assert persistent.begin_replace(journal_entry, "Password", "candidate", 2)
    monkeypatch.setattr(cache, "get_cache", lambda: persistent)
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(persistent, "prepare", lambda: {"state": "ready"})
    monkeypatch.setattr(cli, "send_command", lambda *_a, **_kw: {
        "ok": True, "value": "confirmed-value", "generation": 3,
    })
    if operation == "refresh":
        result = credential_reads.read_credential(SimpleNamespace(
            entry="entry", field="Password", refresh=True,
        ))
        assert result.code == 0 and result.value == "confirmed-value"
    else:
        assert cli.cmd_cache_populate(SimpleNamespace(
            entry=["entry:Password"], manifest=None, machine=None, verify=False, prompt=False,
        )) == 0
    assert persistent.pending_replace(journal_entry, "Password") is None
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("cache-only contacted service"))
    assert credential_reads.read_credential(SimpleNamespace(
        entry="entry", field="Password", cache_only=True,
    )).value == "confirmed-value"
    assert cli.cmd_cache_verify(SimpleNamespace(entry=["entry:Password"], json=True)) == 0
    assert "confirmed-value" not in capsys.readouterr().out


def test_missing_only_recovery_does_not_clear_an_alias_owned_journal(
    cached_service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vault, persistent, _kpdb, _clock = cached_service
    assert persistent.begin_replace("Allowed/entry", "Password", "candidate", 2)
    before = persistent._cache_file.read_bytes()
    monkeypatch.setattr("agent_vault.cache_recovery.require_source", lambda *_args: None)
    assert not persistent.recover_missing("Allowed/entry", "password", "confirmed", 3)
    assert persistent._cache_file.read_bytes() == before


def test_older_authority_cannot_claim_to_reconcile_a_newer_value_with_an_alias_journal(
    cached_service,
) -> None:
    _vault, persistent, _kpdb, _clock = cached_service
    assert persistent.put("Allowed/entry", "password", "newer", 50)
    assert persistent.begin_replace("Allowed/entry", "Password", "candidate", 51)
    before = persistent._cache_file.read_bytes()
    assert not persistent.reconcile_authoritative("Allowed/entry", "password", "older", 49)
    assert persistent._cache_file.read_bytes() == before
