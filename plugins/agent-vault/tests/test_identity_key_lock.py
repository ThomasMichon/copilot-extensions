"""OS-backed owner-initialization coordination is scoped to the database."""

from __future__ import annotations

import time

import pytest
from single_instance_lease import SingleInstance

from agent_vault import identity_key_lock
from agent_vault.identity_key import IdentityKeyError


def test_database_lease_is_stable_across_cells_and_released(monkeypatch, tmp_path):
    database = tmp_path / "fixture.kdbx"
    database.write_bytes(b"disposable fixture")
    with identity_key_lock.database_creation_lock(str(database), time.monotonic() + 2):
        paths = list(tmp_path.glob(".agent-vault-identity-*.lock"))
        assert len(paths) == 1
        monkeypatch.setenv("AGENT_VAULT_HOME", str(tmp_path / "different-cell"))
        monkeypatch.setenv("AGENT_VAULT_INSTALLATION_ID", "different-cell")
        with pytest.raises(IdentityKeyError) as failure:
            with identity_key_lock.database_creation_lock(str(database), time.monotonic() + 0.1):
                pytest.fail("same database acquired a second lease")
        assert failure.value.code == "service_busy"
    with identity_key_lock.database_creation_lock(str(database), time.monotonic() + 2):
        assert list(tmp_path.glob(".agent-vault-identity-*.lock")) == paths


def test_unavailable_database_coordination_is_sanitized(monkeypatch, tmp_path):
    database = tmp_path / "fixture.kdbx"
    database.write_bytes(b"disposable fixture")

    def fail(self):
        raise PermissionError("fixture-backend-diagnostic")

    monkeypatch.setattr(SingleInstance, "acquire", fail)
    with pytest.raises(IdentityKeyError) as failure:
        with identity_key_lock.database_creation_lock(str(database), time.monotonic() + 2):
            pytest.fail("permission failure acquired lease")
    assert failure.value.code == "vault_read_failed"
    assert "fixture-backend-diagnostic" not in str(failure.value)


def test_expired_initialization_never_acquires_a_lease(monkeypatch, tmp_path):
    database = tmp_path / "fixture.kdbx"
    database.write_bytes(b"disposable fixture")
    monkeypatch.setattr(SingleInstance, "acquire", lambda *args: pytest.fail("expired lease"))
    with pytest.raises(IdentityKeyError) as failure:
        with identity_key_lock.database_creation_lock(str(database), time.monotonic() - 1):
            pytest.fail("expired initialization ran")
    assert failure.value.code == "service_busy"
    assert list(tmp_path.glob("*.lock")) == []


def test_canonical_alias_and_atomic_database_replacement_keep_one_lease(tmp_path):
    database = tmp_path / "fixture.kdbx"
    database.write_bytes(b"first disposable snapshot")
    alias_dir = tmp_path / "alias"
    alias_dir.mkdir()
    alias = alias_dir / ".." / database.name
    with identity_key_lock.database_creation_lock(str(database), time.monotonic() + 2):
        replacement = tmp_path / "replacement.kdbx"
        replacement.write_bytes(b"second disposable snapshot")
        replacement.replace(database)
        with pytest.raises(IdentityKeyError) as failure:
            with identity_key_lock.database_creation_lock(str(alias), time.monotonic() + 0.1):
                pytest.fail("atomic replacement or path alias split database coordination")
        assert failure.value.code == "service_busy"
        assert len(list(tmp_path.glob(".agent-vault-identity-*.lock"))) == 1
