"""One owner key, cache-only retrieval, and fail-closed source selection."""

from __future__ import annotations

import base64
import json
import sys
import time

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


@pytest.mark.parametrize("encoded", ["", "[]", '["relative-vault"]', "{}", '[""]'])
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


def test_empty_file_override_still_uses_common_vault_key(monkeypatch):
    monkeypatch.setenv(identity_marker.KEY_ENV_VAR, "")
    monkeypatch.setenv(identity_vault.ARGV_ENV, json.dumps([sys.executable]))
    monkeypatch.setattr(identity_vault, "_read_key_output", lambda argv: base64.b64encode(b"s"*32))
    assert identity_marker.load_identity_key() == b"s" * 32


def test_decode_reports_configured_vault_cache_miss(monkeypatch):
    monkeypatch.delenv(identity_marker.KEY_ENV_VAR, raising=False)
    monkeypatch.setenv(identity_vault.ARGV_ENV, json.dumps([sys.executable]))
    monkeypatch.setattr(identity_vault, "_read_key_output", lambda argv: b"")
    with pytest.raises(identity_marker.IdentityMarkerError, match="refresh its protected cache"):
        identity_marker.decrypt_identity_payload("unused")


def test_real_cache_adapter_output_boundary(caplog):
    value = base64.b64encode(b"s" * 32)
    assert identity_vault._read_key_output([
        sys.executable, "-c", f"import sys;sys.stdout.buffer.write({value!r})",
    ]) == value
    assert "cleanup failed" not in caplog.text
    with pytest.raises(ValueError):
        identity_vault._read_key_output([
            sys.executable, "-c", "print('x'*1000)",
        ])


@pytest.mark.parametrize("error", [ProcessLookupError, PermissionError])
def test_posix_cleanup_warns_only_on_actual_failure(monkeypatch, caplog, error):
    from io import BytesIO
    from types import SimpleNamespace
    from unittest.mock import Mock

    value = base64.b64encode(b"s" * 32)
    process = Mock(pid=123, stdout=BytesIO(value))
    process.wait.return_value = 0
    process.poll.return_value = 0
    monkeypatch.setattr(identity_vault, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(identity_vault, "signal", SimpleNamespace(SIGKILL=9))
    monkeypatch.setattr(
        identity_vault, "spawn_sync_in_kill_on_close_job",
        lambda *args, **kwargs: (process, None),
    )
    killpg = Mock(side_effect=error())
    monkeypatch.setattr(identity_vault.os, "killpg", killpg, raising=False)
    assert identity_vault._read_key_output(["fixture-vault"]) == value
    killpg.assert_called_once_with(process.pid, identity_vault.signal.SIGKILL)
    assert ("cleanup failed" in caplog.text) == (error is PermissionError)
    process.kill.assert_not_called()


def test_timeout_reaps_adapter_and_stdout_holding_descendant(monkeypatch, tmp_path):
    from agent_worktrees.locks import pid_alive

    pid_file = tmp_path / "descendant.pid"
    script = (
        "import subprocess,sys,time,pathlib;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid));"
        "time.sleep(60)"
    )
    monkeypatch.setattr(identity_vault, "_READ_TIMEOUT", 5)
    started = time.monotonic()
    with pytest.raises(ValueError, match="timed out"):
        identity_vault._read_key_output([sys.executable, "-c", script])
    assert time.monotonic() - started < 15
    assert pid_file.exists()
    child_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 3
    while pid_alive(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not pid_alive(child_pid)


def test_failed_cache_does_not_publish_secret_diagnostics(monkeypatch, caplog):
    monkeypatch.delenv(identity_marker.KEY_ENV_VAR, raising=False)
    monkeypatch.setenv(identity_vault.ARGV_ENV, json.dumps([sys.executable]))

    def failed(argv):
        raise ValueError("secret-value-from-backend")

    monkeypatch.setattr(identity_vault, "_read_key_output", failed)
    assert identity_marker.load_identity_key() is None
    assert "secret-value-from-backend" not in caplog.text
