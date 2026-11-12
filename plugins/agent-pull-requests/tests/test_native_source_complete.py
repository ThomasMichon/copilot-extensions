"""Native source completion preserves selected-runtime and unknown-outcome custody."""
import json
import subprocess

import pytest

from agent_pull_requests import __main__ as cli, native_author, native_source_complete as native

HEAD, BRANCH, REPO = "a" * 40, "feature/topic", "example/project"


@pytest.fixture
def command(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    path = tmp_path / "selected-worktrees"
    path.touch()
    return str(path)


def arguments(command, *extra):
    return [
        "complete", f"--repo={REPO}", f"--expected-branch={BRANCH}", f"--expected-head={HEAD}",
        "--worktrees-command", command, "--json", *extra,
    ]


def capability():
    return {
        "schema": "agent-worktrees.pr-author-capabilities", "version": 1,
        "repo": REPO, "provider": "gitea", "operations": {"local-source-complete": True, "extra": True},
    }


def receipt(**changes):
    return {
        "schema": native.SCHEMA, "version": 1, "repo": REPO, "dry_run": False,
        "source": {"worktree_id": "managed-source", "branch": BRANCH, "expected_head": HEAD},
        "ok": True, "status": "confirmed",
        "result": {
            "success": True, "action": "rebased", "worktree_id": "managed-source",
            "branch": BRANCH, "backup_ref": "refs/pre-complete-backups/source/operation",
            "head": "b" * 12, "kept": 1, "extra": {"opaque": True},
        }, **changes,
    }


def responses(monkeypatch, support, result, code=0):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        value = support if len(calls) == 1 else result
        if isinstance(value, Exception):
            raise value
        return subprocess.CompletedProcess(
            argv, 0 if len(calls) == 1 else code,
            value if isinstance(value, str) else json.dumps(value), "",
        )
    monkeypatch.setattr(native, "run_command", run)
    monkeypatch.setattr(native_author, "run_command", run)
    return calls


@pytest.mark.parametrize("support", [
    {}, {**capability(), "operations": {}}, {**capability(), "version": True},
    {**capability(), "repo": "other/repo"},
    {**capability(), "operations": {"local-source-complete": "true"}},
    OSError("old owner unavailable"),
])
def test_missing_or_rolled_back_capability_never_sends_new_effectful_syntax(
    command, monkeypatch, capsys, support,
):
    calls = responses(monkeypatch, support, receipt())
    assert cli.main(arguments(command)) == 1
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "not-attempted" and len(calls) == 1
    assert calls[0][1] == "pr-author-capabilities"


@pytest.mark.parametrize("extra", [
    ["--expected-head=HEAD"], ["--expected-head="], ["--expected-head=" + "A" * 40],
    ["--expected-branch="], ["--expected-branch=refs/heads/topic"],
])
def test_bad_candidate_precedes_even_capability_lookup(command, monkeypatch, capsys, extra):
    calls = responses(monkeypatch, capability(), receipt())
    assert cli.main(arguments(command, *extra)) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "not-attempted"
    assert not calls


@pytest.mark.parametrize("extra", [
    ["--config=worker.json"], ["--workspace=/foreign"], ["--worktree=short"],
    ["--number=7"], ["--expected-headd=x"], ["--dry-r"],
])
def test_unknown_or_foreign_selection_is_strict(command, monkeypatch, extra):
    calls = responses(monkeypatch, capability(), receipt())
    with pytest.raises(SystemExit) as exc:
        cli.main(arguments(command, *extra))
    assert exc.value.code == 2 and not calls


def test_success_preserves_all_original_owner_evidence(command, monkeypatch, capsys):
    value = receipt(extra={"future": True})
    calls = responses(monkeypatch, capability(), value)
    assert cli.main(arguments(command)) == 0
    assert json.loads(capsys.readouterr().out) == value
    assert calls[1] == [
        command, "pr-complete", f"--repo={REPO}", f"--expected-branch={BRANCH}",
        f"--expected-head={HEAD}", "--json",
    ]


def test_preview_is_not_confirmed_reconciliation(command, monkeypatch, capsys):
    value = receipt(status="preview", dry_run=True)
    calls = responses(monkeypatch, capability(), value)
    assert cli.main(arguments(command, "--dry-run")) == 0
    assert json.loads(capsys.readouterr().out) == value and calls[1][-1] == "--dry-run"


@pytest.mark.parametrize("damage", [
    {"version": True}, {"repo": "other/repo"}, {"dry_run": 0}, {"ok": 1},
    {"status": "preview"}, {"source": None},
    {"source": {"worktree_id": "managed", "branch": "other", "expected_head": HEAD}},
    {"source": {"worktree_id": "managed", "branch": BRANCH, "expected_head": "b" * 40}},
    {"result": {"success": True, "action": "finalized"}},
])
def test_malformed_or_wrong_source_result_stays_unknown_without_replay(
    command, monkeypatch, capsys, damage,
):
    calls = responses(monkeypatch, capability(), receipt(**damage))
    assert cli.main(arguments(command)) == 1
    captured = capsys.readouterr()
    value = json.loads(captured.out)
    assert value["status"] == "unknown" and value["repo"] == REPO
    assert value["frontend"]["expected_head"] == HEAD
    assert "no replay" in captured.err and len(calls) == 2


@pytest.mark.parametrize("result", [
    "not-json", [], subprocess.TimeoutExpired(["pr-complete"], 90),
    RuntimeError("native output budget exceeded"), OSError("response lost"),
])
def test_missing_output_never_becomes_a_noop_or_retry(command, monkeypatch, capsys, result):
    calls = responses(monkeypatch, capability(), result)
    assert cli.main(arguments(command)) == 1
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "unknown" and not value["ok"] and len(calls) == 2


def test_owner_error_retains_original_recovery_ref(command, monkeypatch, capsys):
    result = {**receipt()["result"], "success": False, "action": "error", "error": "conflict"}
    value = receipt(ok=False, status="unknown", result=result, error="conflict")
    calls = responses(monkeypatch, capability(), value, code=1)
    assert cli.main(arguments(command)) == 1
    assert json.loads(capsys.readouterr().out) == value and len(calls) == 2


def test_plain_unknown_outcome_names_retained_recovery_without_replay(command, monkeypatch, capsys):
    result = {**receipt()["result"], "success": False, "action": "error", "error": "conflict"}
    calls = responses(monkeypatch, capability(), receipt(
        ok=False, status="unknown", result=result, error="conflict",
    ), code=1)
    assert cli.main([word for word in arguments(command) if word != "--json"]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "no replay" in captured.err
    assert result["backup_ref"] in captured.err and len(calls) == 2


def test_nonzero_success_shaped_owner_is_not_frontend_success(command, monkeypatch, capsys):
    calls = responses(monkeypatch, capability(), receipt(), code=1)
    assert cli.main(arguments(command)) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == receipt()
    assert "unconfirmed" in captured.err and len(calls) == 2


def test_managed_peer_uses_same_binding_and_blank_selector_cannot_bypass_parser(
    command, monkeypatch, capsys,
):
    from agent_pull_requests.watch_invocation import WatchInvocation
    from types import SimpleNamespace

    peer = SimpleNamespace(snapshot_peer_prefix=lambda: [command, "peer-prefix"])
    monkeypatch.setattr(WatchInvocation, "capture", lambda **kwargs: peer)
    calls = responses(monkeypatch, capability(), receipt())
    assert cli.main(arguments(command)) == 0
    assert json.loads(capsys.readouterr().out) == receipt()
    assert all(call[:2] == [command, "peer-prefix"] for call in calls)
    with pytest.raises(SystemExit) as exc:
        cli.main(arguments(command, "--worktrees-command", " "))
    assert exc.value.code == 2 and len(calls) == 2
