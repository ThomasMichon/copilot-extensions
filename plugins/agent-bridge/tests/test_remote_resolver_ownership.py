"""Remote resolver error and ownership-loss regression probes."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent_bridge.resolver_diagnostics import (
    can_retry_legacy_resolve,
    resolver_failure_detail,
    structured_resolver_error,
)
from agent_bridge.transport import (
    SpawnTarget, _resolve_worktree, _resolve_worktree_remote,
)


def _result(*, exit_code: int, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        exit_code=exit_code, stdout=stdout, stderr=stderr, timed_out=False,
    )


@pytest.mark.asyncio
@pytest.mark.contract("agent_bridge.transport.remote_resolver")
async def test_remote_structured_error_is_not_hidden_by_fetch_diagnostics():
    manager = SimpleNamespace(
        exec_command=AsyncMock(return_value=_result(
            exit_code=1,
            stdout=json.dumps({"error": "caller ledger belongs to another registry"}),
            stderr="Fetching latest source...",
        )),
    )
    with pytest.raises(RuntimeError, match="caller ledger belongs to another registry"):
        await _resolve_worktree_remote(
            manager, SpawnTarget(type="ssh", host="workstation", project="project"),
        )
    assert manager.exec_command.await_count == 1


@pytest.mark.asyncio
@pytest.mark.contract("agent_bridge.transport.remote_resolver")
async def test_owned_remote_allocation_never_retries_after_discarding_ownership():
    manager = SimpleNamespace(
        exec_command=AsyncMock(side_effect=[
            _result(
                exit_code=2, stderr="unrecognized arguments: --owner-ref",
            ),
            _result(
                exit_code=0,
                stdout=json.dumps({
                    "launch": {"worktree_id": "wt-child", "work_dir": "/example"},
                }),
            ),
        ]),
    )
    with pytest.raises(RuntimeError, match="owner"):
        await _resolve_worktree_remote(
            manager,
            SpawnTarget(
                type="ssh", host="workstation", project="project",
                caller_worktree=r"C:\example\wt-caller",
                caller_owner_ref="workstation/project/wt-caller",
            ),
        )
    assert manager.exec_command.await_count == 1


@pytest.mark.asyncio
@pytest.mark.contract("agent_bridge.transport.remote_resolver")
@pytest.mark.parametrize("metadata", [
    {"caller_owner_ref": "workstation/project/wt-caller"},
    {"caller_worktree": r"C:\example\wt-caller"},
])
@pytest.mark.parametrize("unknown_flag", ["--bridge", "--caller-worktree", "--owner-ref"])
async def test_skew_cannot_drop_either_caller_metadata(metadata, unknown_flag):
    manager = SimpleNamespace(exec_command=AsyncMock(return_value=_result(
        exit_code=2, stderr=f"unrecognized arguments: {unknown_flag}",
    )))
    target = SpawnTarget(
        type="ssh", host="workstation", project="project", **metadata,
    )
    with pytest.raises(RuntimeError, match="exit 2"):
        await _resolve_worktree_remote(manager, target)
    assert manager.exec_command.await_count == 1
    command = manager.exec_command.call_args.args[1]
    for key in metadata:
        assert "--" + key.replace("caller_owner_ref", "owner_ref").replace("_", "-") in command


@pytest.mark.contract("agent_bridge.transport.resolver_diagnostics")
@pytest.mark.parametrize(("stdout", "expected"), [
    ('{"error":"ledger unavailable"}', "ledger unavailable"),
    ('banner\n{"error":"ledger unavailable"}\ntrailer', "ledger unavailable"),
    ('noise {broken}\n{"error":"ledger unavailable"}', "ledger unavailable"),
    ('{"banner":"ok"}\n{"error":"ledger unavailable"}', "ledger unavailable"),
    ('{"error":"ledger unavailable","code":"owner_missing","stage":"worktree"}',
     "ledger unavailable (stage=worktree, code=owner_missing)"),
    ('{"error":{"message":"ledger unavailable","code":"owner_missing"}}',
     "ledger unavailable (code=owner_missing)"),
    ('{"error":null}', None),
    ('{"error":{"env":{"PASSWORD":"should-not-print"}}}', None),
    ('{"launch":{"env":{"PASSWORD":"should-not-print"}}}', None),
    ('{malformed', None),
    ('', None),
])
def test_structured_errors_are_extracted_without_dumping_envelopes(stdout, expected):
    assert structured_resolver_error(stdout) == expected


@pytest.mark.contract("agent_bridge.transport.resolver_diagnostics")
@pytest.mark.parametrize(("stdout", "stderr", "expected"), [
    ('{"error":"actual error"}', "fetch warning", "actual error; stderr: fetch warning"),
    ('{"error":"actual error"}', "", "actual error"),
    ('{broken', "stderr only", "stderr only"),
    ('', "stderr only", "stderr only"),
    ('{"launch":{"env":{"PASSWORD":"should-not-print"}}}', "",
     "resolver returned no structured error or stderr diagnostics"),
    ('{broken', "", "resolver returned no structured error or stderr diagnostics"),
])
def test_error_fallbacks(stdout, stderr, expected):
    assert resolver_failure_detail(stdout, stderr) == expected


@pytest.mark.contract("agent_bridge.transport.resolver_diagnostics")
def test_diagnostics_are_bounded_sanitized_and_do_not_echo_environments(monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_TOKEN", "example-env-credential")
    detail = resolver_failure_detail(
        json.dumps({
            "error": (
                "example-env-credential PASSWORD=example-password "
                "Bearer example-bearer https://user:example-url-password@host/repo "
                "github_pat_exampleprovidercredential\n\x1b[31m" + "x" * 4000
            ),
            "env": {"OTHER": "never-dump-this-field"},
        }),
        'TOKEN="example-stderr-token" ' + "y" * 4000,
    )
    for secret in (
        "example-env-credential", "example-password", "example-bearer",
        "example-url-password", "github_pat_exampleprovidercredential",
        "example-stderr-token", "never-dump-this-field",
    ):
        assert secret not in detail
    assert "[REDACTED]" in detail
    assert "\x1b" not in detail
    assert "\n" not in detail
    assert len(detail) < 1400


@pytest.mark.contract("agent_bridge.transport.resolver_diagnostics")
@pytest.mark.parametrize(("text", "secret"), [
    ("TO\x1b[31mKEN=pattern-secret", "pattern-secret"),
    ("PASS\x00WORD=pattern-secret", "pattern-secret"),
    ("Bearer pat\x1b[31mtern-secret", "pattern-secret"),
    ("https://user:pat\x1b[31mtern-secret@host/repo", "pattern-secret"),
    ("known-env-\x1b[31msecret", "known-env-secret"),
])
def test_control_cleanup_cannot_reveal_credentials_after_redaction(text, secret, monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_TOKEN", "known-env-secret")
    detail = resolver_failure_detail(json.dumps({"error": text}), text)
    assert secret not in detail
    assert "[REDACTED]" in detail
    assert "\x1b" not in detail
    assert "\x00" not in detail


@pytest.mark.asyncio
@pytest.mark.contract("agent_bridge.transport.resolver_diagnostics")
@pytest.mark.parametrize("stdout", [
    '{"launch":{"env":{"PASSWORD":"example-secret"}}',
    "banner PASSWORD=example-secret",
])
async def test_successful_exit_without_json_never_echoes_stdout(stdout):
    manager = SimpleNamespace(exec_command=AsyncMock(return_value=_result(
        exit_code=0, stdout=stdout,
    )))
    with pytest.raises(RuntimeError, match="no JSON object") as caught:
        await _resolve_worktree_remote(
            manager, SpawnTarget(type="ssh", host="workstation", project="project"),
        )
    assert "example-secret" not in str(caught.value)
    assert "PASSWORD" not in str(caught.value)
    assert manager.exec_command.await_count == 1


@pytest.mark.contract("agent_bridge.transport.resolver_diagnostics")
def test_oversized_or_deeply_nested_stdout_is_not_echoed():
    for stdout in (
        json.dumps({"error": "x" * 100000}),
        '{"error":' + "[" * 2000 + '"secret"' + "]" * 2000 + "}",
    ):
        assert structured_resolver_error(stdout) is None
        assert resolver_failure_detail(stdout, "") == (
            "resolver returned no structured error or stderr diagnostics"
        )


@pytest.mark.contract("agent_bridge.transport.resolver_diagnostics")
@pytest.mark.parametrize(("exit_code", "stdout", "stderr", "expected"), [
    (2, "", "unrecognized arguments: --bridge", True),
    (2, "", "no such option: --bridge", True),
    (1, "", "unrecognized arguments: --bridge", False),
    (2, "", "owner --bridge could not be resolved", False),
    (2, '{"error":"--bridge ownership rejected"}',
     "unrecognized arguments: --bridge", False),
    (2, "", "unrecognized arguments: --something-else", False),
])
def test_only_ownerless_argparse_bridge_skew_can_retry(exit_code, stdout, stderr, expected):
    assert can_retry_legacy_resolve(
        exit_code=exit_code, stdout=stdout, stderr=stderr,
        caller_owner_ref=None, caller_worktree=None,
    ) is expected


@pytest.mark.asyncio
@pytest.mark.contract("agent_bridge.transport.resolver_diagnostics")
async def test_local_and_remote_report_identical_structured_failure():
    stdout = json.dumps({"error": "owner ledger missing", "stage": "allocation"})
    stderr = "Fetching latest source..."
    process = SimpleNamespace(
        returncode=7,
        communicate=AsyncMock(return_value=(stdout.encode(), stderr.encode())),
    )
    with patch("agent_bridge.transport._agent_worktrees_python", return_value="python"), \
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        with pytest.raises(RuntimeError, match="exit 7") as local:
            await _resolve_worktree(SpawnTarget(type="local", project="project"), {})
    manager = SimpleNamespace(exec_command=AsyncMock(return_value=_result(
        exit_code=7, stdout=stdout, stderr=stderr,
    )))
    with pytest.raises(RuntimeError, match="exit 7") as remote:
        await _resolve_worktree_remote(
            manager, SpawnTarget(type="ssh", host="workstation", project="project"),
        )
    assert str(local.value).split(": ", 1)[1] == str(remote.value).split(": ", 1)[1]
    assert "stage=allocation" in str(remote.value)
