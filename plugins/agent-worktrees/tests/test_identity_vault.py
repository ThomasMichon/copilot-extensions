"""One owner key, cache-only retrieval, and fail-closed source selection."""

from __future__ import annotations

import base64
import json
import sys

import pytest

from agent_worktrees import identity_marker, identity_vault


def test_common_vault_key_decodes_multiple_machine_tokens(monkeypatch):
    key = b"s" * 32
    monkeypatch.delenv(identity_marker.KEY_ENV_VAR, raising=False)
    monkeypatch.setenv(identity_vault.ARGV_ENV, json.dumps([sys.executable, "vault-adapter"]))
    calls = []

    def cached(argv):
        calls.append(argv)
        return base64.b64encode(key) + b"\n"

    monkeypatch.setattr(identity_vault, "_read_key_output", cached)
    tokens = [
        identity_marker.identity_marker_field(worktree_id=f"tree-{machine}", machine=machine)
        for machine in ("source-a", "source-b")
    ]
    assert [identity_marker.decrypt_identity_payload(token)["machine"] for token in tokens] == [
        "source-a", "source-b",
    ]
    assert all(call[-4:] == [
        "get", "Identity/PR-attribution", "password", "--cache-only",
    ] for call in calls)
    assert all("--prompt" not in call for call in calls)


@pytest.mark.parametrize("encoded", ["[]", '["relative-vault"]', "{}", '[""]'])
def test_invalid_vault_command_does_not_fall_back(monkeypatch, encoded, caplog):
    monkeypatch.delenv(identity_marker.KEY_ENV_VAR, raising=False)
    monkeypatch.setenv(identity_vault.ARGV_ENV, encoded)
    assert identity_marker.load_identity_key() is None
    assert "shared vault key unavailable" in caplog.text


def test_explicit_file_override_preserves_shared_key_compatibility(monkeypatch, tmp_path):
    path = tmp_path / "shared.key"
    path.write_bytes(base64.b64encode(b"s" * 32))
    monkeypatch.setenv(identity_marker.KEY_ENV_VAR, str(path))
    monkeypatch.setenv(identity_vault.ARGV_ENV, "invalid")
    assert identity_marker.load_identity_key() == b"s" * 32
    with pytest.raises(identity_marker.IdentityMarkerError, match="shared owner key"):
        identity_marker.generate_identity_key(tmp_path / "different.key")
    assert not (tmp_path / "different.key").exists()


def test_real_cache_adapter_output_boundary():
    value = base64.b64encode(b"s" * 32)
    assert identity_vault._read_key_output([
        sys.executable, "-c", f"import sys;sys.stdout.buffer.write({value!r})",
    ]) == value
    with pytest.raises(ValueError):
        identity_vault._read_key_output([
            sys.executable, "-c", "print('x'*1000)",
        ])


def test_failed_cache_does_not_publish_secret_diagnostics(monkeypatch, caplog):
    monkeypatch.delenv(identity_marker.KEY_ENV_VAR, raising=False)
    monkeypatch.setenv(identity_vault.ARGV_ENV, json.dumps([sys.executable]))

    def failed(argv):
        raise ValueError("secret-value-from-backend")

    monkeypatch.setattr(identity_vault, "_read_key_output", failed)
    assert identity_marker.load_identity_key() is None
    assert "secret-value-from-backend" not in caplog.text
