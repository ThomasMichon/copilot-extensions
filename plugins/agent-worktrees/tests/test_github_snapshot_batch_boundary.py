"""Batch transport refusal, quota semantics and strict mixed-version CLI."""

from __future__ import annotations

from dataclasses import replace
from collections.abc import Callable
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, cast

import pytest

from agent_worktrees import pr_cli, pr_credential_command, pr_inventory_cli
from agent_worktrees.providers import github_batch as batch
from agent_worktrees.providers.github_batch_decode import timestamp
from agent_worktrees.providers.github_batch_query import PageRequest, query
from agent_worktrees.providers.observation_credentials import CredentialSource
from agent_worktrees.providers.observation_http import MAX_BODY
from test_github_snapshot_batch import candidate, connection, envelope, review, transport
from test_pr_observation_credentials import Source


def test_exhausted_primary_preserves_current_result_and_blocks_next_page(monkeypatch: pytest.MonkeyPatch) -> None:
    value = candidate()
    value["reviews"] = connection([review()], total=2, after="next")
    response = envelope(value, candidate(8))
    response["data"]["rateLimit"]["remaining"] = 0
    calls = transport(monkeypatch, [subprocess.CompletedProcess(
        [], 0, "HTTP/1.1 200 OK\r\nX-RateLimit-Remaining: 0\r\n"
        "X-RateLimit-Reset: 4070908800\r\n\r\n" + json.dumps(response), "",
    )])
    result = batch.snapshots("owner/repository", [7, 8], credential_source=Source())
    assert result["results"][0]["snapshot"] is None
    assert result["results"][1]["outcome"] == "complete" and len(calls) == 1
    assert result["backoff"]["resource"] == "graphql-primary"
    assert result["backoff"]["not_before"] == timestamp("2099-01-01T00:00:00Z")


@pytest.mark.parametrize("kind", ["secondary", "primary", "graphql"])
def test_throttle_stops_whole_operation_and_distinguishes_resources(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    replies: list[Any]
    if kind == "graphql":
        value = envelope(candidate())
        value["errors"] = [{"type": "RATE_LIMITED", "message": "private-detail"}]
        replies = [value]
    else:
        headers = (
            "Retry-After: 120\r\n" if kind == "secondary"
            else "X-RateLimit-Remaining: 0\r\nX-RateLimit-Reset: 4070908800\r\n"
        )
        replies = [subprocess.CompletedProcess([], 0, "HTTP/1.1 403 Forbidden\r\n" + headers + "\r\n{}", "")]
    calls = transport(monkeypatch, replies)
    before = time.time()
    result = batch.snapshots("owner/repository", [7], credential_source=Source())
    assert result["results"][0]["snapshot"] is None and len(calls) == 1
    assert result["backoff"]["resource"] == ("graphql-primary" if kind == "primary" else "shared-secondary")
    assert result["backoff"]["not_before"] >= before + 60
    assert "private-detail" not in json.dumps(result)


@pytest.mark.parametrize("kind", ["oversize", "duplicate", "redirect", "exit", "expiry"])
def test_capture_and_transport_failures_are_sanitized_and_never_retried(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    source: CredentialSource = Source()
    reply: object
    if kind == "oversize":
        reply = subprocess.CompletedProcess([], 0, "x" * (MAX_BODY + 1), "")
    elif kind == "duplicate":
        reply = subprocess.CompletedProcess([], 0, 'HTTP/1.1 200 OK\r\n\r\n{"data": {}, "data": {}}', "")
    elif kind == "redirect":
        reply = subprocess.CompletedProcess([], 0, "HTTP/1.1 302 Found\r\nLocation: https://other.example\r\n\r\n{}", "")
    elif kind == "exit":
        reply = subprocess.CompletedProcess([], 1, "private-detail", "private-detail")
    else:
        material = replace(Source()(), expires_at=int(time.time()) + 10)
        source = lambda: material

        def expire() -> dict[str, Any]:
            monkeypatch.setattr(time, "time", lambda: material.expires_at + 1)
            return envelope(candidate())

        reply = expire
    calls = transport(monkeypatch, [reply])
    result = batch.snapshots("owner/repository", [7], credential_source=source)
    assert result["results"][0]["snapshot"] is None and len(calls) == 1
    assert "private-detail" not in json.dumps(result)


def test_fixed_query_rejects_cursor_injection_and_has_no_raw_document_input() -> None:
    with pytest.raises(ValueError):
        query("owner/repository", (PageRequest(7, reviews="next\nmutation {}"),))
    with pytest.raises(ValueError):
        query("owner/repository", (PageRequest(7, reviews=True),))
    with pytest.raises(TypeError):
        cast(Callable[..., object], batch.snapshots)(
            "owner/repository", [7], credential_source=Source(), query="mutation {}",
        )
    assert query("owner/repository", (PageRequest(7),))["query"] == (
        query("other/project", (PageRequest(9),))["query"]
    )


@pytest.mark.parametrize("at", ["credential", "query"])
def test_unconfirmed_child_cleanup_keeps_its_canonical_exception(
    monkeypatch: pytest.MonkeyPatch, at: str,
) -> None:
    from agent_procutil.owned_process import OwnedCleanupError

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise OwnedCleanupError(None)

    source = fail if at == "credential" else Source()
    monkeypatch.setattr(batch, "run_cli", fail)
    with pytest.raises(OwnedCleanupError):
        batch.snapshots("owner/repository", [7], credential_source=source)


def config_file(
    tmp_path: Path, *, provider: str = "github", endpoint: str = "https://api.github.com",
) -> Path:
    path = tmp_path / "selected.yaml"
    path.write_text(
        "repos:\n  selected:\n    pr:\n"
        f"      provider: {provider}\n      api_base: {endpoint}\n"
        "      repository: owner/repository\n", encoding="utf-8",
    )
    return path


def test_passive_capability_addition_keeps_legacy_contract_and_does_not_acquire(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(pr_credential_command, "run_caller_owned",
                        lambda *_a, **_kw: pytest.fail("passive discovery acquired credentials"))
    assert pr_inventory_cli.dispatch(["--contract", "--json"]) == 0
    declaration = json.loads(capsys.readouterr().out)
    assert declaration["protocol"] == "agent-worktrees.pr-inventory/v1"
    assert declaration["snapshot_request_budget"] == 64
    feature = declaration["snapshot_batch"]
    assert feature["protocol"] == "agent-worktrees.pr-snapshot-batch/v1"
    assert feature["max_members"] == 10 and feature["scalar_default"] == "unchanged"
    # A legacy consumer reads only its pre-existing descriptor members.
    assert {key: declaration[key] for key in ("providers", "effects")} == {
        "providers": ["github", "gitea"], "effects": "none",
    }


def test_real_cli_routes_from_neutral_directory_and_emits_batch_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees import __main__ as main, config
    selected = config_file(tmp_path)
    monkeypatch.chdir(tmp_path)
    config.set_active_project(None)
    monkeypatch.setattr(main, "_ensure_cluster_loaded",
                        lambda: pytest.fail("batch loaded lifecycle cluster"))
    monkeypatch.setattr(main, "_resolve_active_project",
                        lambda *_: pytest.fail("batch resolved a project"))
    source = Source()
    monkeypatch.setattr(pr_credential_command, "command_source", lambda *_a, **_kw: source)
    calls = transport(monkeypatch, [envelope(candidate(), candidate(8))])
    assert main.main([
        "pr-watch", "snapshot-batch", "owner/repository", "7", "8",
        "--config", str(selected), "--credential-command", json.dumps([sys.executable]), "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "agent-worktrees.pr-snapshot-batch"
    assert all(row["outcome"] == "complete" for row in result["results"])
    assert len(calls) == source.calls == 1


def test_real_credential_and_bounded_native_children_preserve_stdin_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from agent_worktrees.providers.base import run_cli
    from agent_worktrees.pr_credential_command import command_source
    material = {
        "schema": "fleet-manager.forge-credential", "version": 1,
        "token": "synthetic-child", "expires_at": time.time() + 30,
        "forge": {"provider": "github", "api_base": "https://api.github.com",
                  "repository": "owner/repository", "permission": "observe",
                  "authentication": "bearer"},
    }
    source = command_source(json.dumps([
        sys.executable, "-c", "print(" + repr(json.dumps(material)) + ")",
    ]), max_acquisitions=1)
    response = "HTTP/1.1 200 OK\r\n\r\n" + json.dumps(envelope(candidate()))
    child = tmp_path / "query_child.py"
    child.write_text(
        "import json, sys\n"
        "lines = sys.stdin.read().splitlines()\n"
        "header = json.loads(lines[0].partition(' = ')[2])\n"
        "assert header.endswith('synthetic-child')\n"
        "value = json.loads(json.loads(lines[1].partition(' = ')[2]))\n"
        "assert value['variables']['n0'] == 7\n"
        "assert value['query'].startswith('query PRSnapshotBatch(')\n"
        "assert 'synthetic-child' not in repr(sys.argv)\n"
        "sys.stdout.write(" + repr(response) + ")\n", encoding="utf-8",
    )
    calls: list[list[str]] = []

    def invoke(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv[0] == "curl" and argv[argv.index("-X") + 1] == "POST"
        calls.append(argv)
        return run_cli([sys.executable, str(child)], **kwargs)

    monkeypatch.setattr(batch, "run_cli", invoke)
    result = batch.snapshots("owner/repository", [7], credential_source=source)
    assert result["results"][0]["outcome"] == "complete"
    assert len(calls) == result["credential_acquisitions"] == 1
    assert "synthetic-child" not in json.dumps(result)


@pytest.mark.parametrize("change", ["unknown", "abbreviation", "enterprise", "provider", "duplicates", "budget"])
def test_selected_unsupported_or_malformed_cli_refuses_before_acquisition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, change: str,
) -> None:
    selected = config_file(
        tmp_path, provider="gitea" if change == "provider" else "github",
        endpoint="https://enterprise.example/api/v3" if change == "enterprise" else "https://api.github.com",
    )
    monkeypatch.setattr(pr_credential_command, "command_source",
                        lambda *_a, **_kw: pytest.fail("invalid selection reached credentials"))
    argv = [
        "snapshot-batch", "owner/repository", "7", "--config", str(selected),
        "--credential-command", json.dumps([sys.executable]), "--json",
    ]
    if change == "unknown":
        argv.extend(["--query", "mutation {}"])
    elif change == "abbreviation":
        argv[argv.index("--config")] = "--conf"
    elif change == "duplicates":
        argv.insert(3, "7")
    elif change == "budget":
        argv.extend(["--max-credential-requests", "0"])
    assert pr_cli.cmd_pr_watch_dispatch(argv) == 2


def test_elapsed_http_date_retry_after_still_persists_secondary_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = transport(monkeypatch, [subprocess.CompletedProcess(
        [], 0, "HTTP/1.1 429 Too Many Requests\r\n"
        "Retry-After: Thu, 01 Jan 1970 00:00:00 GMT\r\n\r\n{}", "",
    )])
    before = time.time()
    result = batch.snapshots("owner/repository", [7], credential_source=Source())
    assert result["backoff"]["resource"] == "shared-secondary"
    assert result["backoff"]["not_before"] >= before + 60 and len(calls) == 1


def test_non_json_constant_cannot_become_a_null_complete_rollup(monkeypatch: pytest.MonkeyPatch) -> None:
    value = envelope(candidate())
    value["data"]["repository"]["p0"]["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = None
    raw = json.dumps(value).replace('"statusCheckRollup": null', '"statusCheckRollup": NaN')
    calls = transport(monkeypatch, [subprocess.CompletedProcess(
        [], 0, "HTTP/1.1 200 OK\r\n\r\n" + raw, "",
    )])
    result = batch.snapshots("owner/repository", [7], credential_source=Source())
    assert result["results"][0]["snapshot"] is None and len(calls) == 1
    assert result["stop_reason"] == "invalid-provider-evidence"
