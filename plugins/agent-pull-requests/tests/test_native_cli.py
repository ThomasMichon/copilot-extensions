"""Native CLI routing keeps provider, policy and claimant authority in Worktrees."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agent_pull_requests import __main__ as cli
from agent_pull_requests import native_cli, native_command


@pytest.fixture
def command(tmp_path, monkeypatch):
    path = tmp_path / "worktrees"
    path.touch()
    for name in (
        "_github_status", "_github_create", "_github_merge",
        "_resolve_claimant_worktree_id", "_journal_pr_claim",
    ):
        monkeypatch.setattr(cli, name, lambda *a, **k: pytest.fail("legacy fallback or duplicate claim"))
    return str(path)


def report(*, merged=False, closed=False):
    return {
        "schema": "agent-worktrees.pr-snapshot", "version": 1,
        "repo": "project/repo", "pr": 7, "provider": "azure-devops",
        "pr_state": "closed" if merged or closed else "open", "merged": merged,
        "head_sha": "head", "base_ref": "main", "mergeable": True,
        "checks_state": "pending", "title": "Example", "draft": False,
        "observation": {
            "schema": "agent-worktrees.pr-observation", "version": 1,
            "provider": "azure-devops", "repo": "project/repo", "number": 7,
            "head_sha": "head", "complete": True, "errors": [],
            "votes": [{"actor_id": "author", "vote": 10}],
            "approval_events": [], "discussions": [], "policies": [],
            "gates": [{"kind": "human", "state": "pending"}],
            "fingerprints": {
                name: "fingerprint" for name in ("votes", "approvals", "discussions", "policies", "gates")
            },
        },
    }


def arguments(verb, command, *extra):
    target = ["--head", "feature/x", "--title", "Example"] if verb == "create" else ["--number", "7"]
    return [verb, "--repo", "project/repo", *target, "--worktrees-command", command, "--json", *extra]


def stub(monkeypatch, payload, *, code=0):
    calls = []

    def run(argv, *, timeout):
        calls.append((argv, timeout))
        return subprocess.CompletedProcess(argv, code, json.dumps(payload), "")

    monkeypatch.setattr(native_cli, "run_command", run)
    return calls


def test_native_status_retains_votes_and_distinct_human_gates(command, monkeypatch, capsys):
    value = report()
    calls = stub(monkeypatch, value)
    assert cli.main(arguments("status", command)) == 0
    assert json.loads(capsys.readouterr().out) == value
    assert calls == [([command, "pr-watch", "snapshot", "project/repo", "7", "--json"], 20.0)]


def test_native_create_delegates_publication_and_claim_once(command, monkeypatch, capsys):
    value = {
        "repo": "project/repo", "number": 7, "pr_opened": True,
        "url": "https://dev.azure.com/example/project/_git/repo/pullrequest/7",
        "claimed": False, "claim_warning": "PR opened but the claim needs recovery",
        "reused": True, "draft": True,
    }
    calls = stub(monkeypatch, value)
    assert cli.main(arguments("create", command, "--draft", "--body", "body")) == 0
    assert json.loads(capsys.readouterr().out) == value
    assert calls == [([
        command, "pr-create", "--repo", "project/repo", "--from-branch", "feature/x",
        "--title", "Example", "--body", "body", "--json", "--draft",
    ], 90.0)]


def test_native_merge_preserves_pending_completion(command, monkeypatch, capsys):
    value = {
        "repo": "project/repo", "pr": 7, "action": "auto-complete",
        "applied": True, "merged": False, "policy_bypass": False,
    }
    calls = stub(monkeypatch, value)
    assert cli.main(arguments("merge", command)) == 0
    assert json.loads(capsys.readouterr().out) == value
    assert calls == [([command, "pr-merge", "project/repo", "7", "--now", "--json"], 90.0)]


@pytest.mark.parametrize("verb", ["create", "merge"])
def test_native_refusal_does_not_retry_or_drop_policy_evidence(verb, command, monkeypatch, capsys):
    value = {"repo": "project/repo", "error": "publication or role refused", "applied": False}
    calls = stub(monkeypatch, value, code=2)
    assert cli.main(arguments(verb, command)) == 2
    assert json.loads(capsys.readouterr().out) == value
    assert len(calls) == 1


@pytest.mark.parametrize("verb,extra", [
    ("create", ["--base", "other"]), ("merge", ["--auto"]),
    ("merge", ["--merge"]), ("merge", ["--rebase"]), ("merge", ["--delete-branch"]),
])
def test_native_unexpressible_flags_refuse_before_mutation(verb, extra, command, monkeypatch, capsys):
    calls = stub(monkeypatch, {})
    assert cli.main(arguments(verb, command, *extra)) == 1
    assert "not supported" in json.loads(capsys.readouterr().out)["error"]
    assert not calls


@pytest.mark.parametrize("payload", [
    [], {"repo": "other/repo", "pr": 7}, {"repo": "project/repo", "pr": 8},
    {"repo": "project/repo", "pr": True},
])
def test_native_mutation_rejects_invalid_identity(payload, command, monkeypatch, capsys):
    stub(monkeypatch, payload)
    assert cli.main(arguments("merge", command)) == 1
    assert "reconcile" in json.loads(capsys.readouterr().out)["error"]


def test_native_mutation_timeout_reports_uncertainty(command, monkeypatch, capsys):
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 90)

    monkeypatch.setattr(native_cli, "run_command", timeout)
    assert cli.main(arguments("create", command)) == 1
    assert "may have applied" in json.loads(capsys.readouterr().out)["error"]


def test_native_non_json_refusal_retains_the_literal_cause(command, monkeypatch, capsys):
    monkeypatch.setattr(
        native_cli, "run_command",
        lambda argv, **k: subprocess.CompletedProcess(argv, 2, "", "no claimant worktree"),
    )
    assert cli.main(arguments("create", command)) == 1
    assert "no claimant worktree" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("value,code,timed_out", [
    (report(merged=True), 0, False), (report(closed=True), 1, False), (report(), 3, True),
])
def test_native_wait_terminal_and_timeout(value, code, timed_out, command, monkeypatch, capsys):
    stub(monkeypatch, value)
    assert cli.main(arguments("wait", command, "--timeout", "0")) == code
    assert json.loads(capsys.readouterr().out) == {**value, "timedOut": timed_out}


def test_native_wait_polls_without_spawning_native_waiter(command, monkeypatch, capsys):
    values = iter([report(), report(merged=True)])
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(next(values)), "")

    monkeypatch.setattr(native_cli, "run_command", run)
    monkeypatch.setattr(native_cli.time, "sleep", lambda _: None)
    assert cli.main(arguments("wait", command)) == 0
    assert not json.loads(capsys.readouterr().out)["timedOut"]
    assert len(calls) == 2
    assert all(argv[1:3] == ["pr-watch", "snapshot"] for argv in calls)


@pytest.mark.parametrize("extra", [["--timeout", "nan"], ["--interval", "-1"]])
def test_native_wait_refuses_invalid_timing(extra, command, monkeypatch, capsys):
    calls = stub(monkeypatch, report())
    assert cli.main(arguments("wait", command, *extra)) == 1
    assert "finite" in json.loads(capsys.readouterr().out)["error"]
    assert not calls


def test_native_same_cell_selection_never_uses_legacy_hint(command, monkeypatch, capsys):
    from agent_pull_requests.watch_invocation import WatchInvocation

    invocation = object()
    prefix = [sys.executable, "-I", "immutable-peer.py"]
    monkeypatch.setattr(WatchInvocation, "capture", lambda **k: invocation)
    monkeypatch.setattr(cli, "_agent_worktrees_prefix", lambda: prefix)
    calls = stub(monkeypatch, report())
    assert cli.main(arguments("status", command)) == 0
    assert json.loads(capsys.readouterr().out)["provider"] == "azure-devops"
    assert calls[0][0][:3] == prefix
    assert command not in calls[0][0]


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell boundary")
def test_native_powershell_arguments_are_data_across_real_process_boundary(tmp_path):
    command = tmp_path / "worktrees with space.ps1"
    command.write_text("ConvertTo-Json -InputObject @($args) -Compress\n", encoding="utf-8")
    values = [
        "pr-create", "--title", 'quotes " and apostrophe \' and & echo bad',
        "--body", "first\nsecond $(& exit 99) %PATH% !literal! \u00e9",
    ]
    result = native_command.run_command([str(command), *values], timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == values


@pytest.mark.skipif(os.name != "nt", reason="native batch boundary")
def test_native_batch_refuses_unsafe_values_before_spawn(tmp_path, monkeypatch):
    command = tmp_path / "worktrees.cmd"
    monkeypatch.setattr(native_command, "run_caller_owned", lambda *a, **k: pytest.fail("spawned"))
    with pytest.raises(ValueError, match="PowerShell"):
        native_command.run_command([str(command), "--body", "line\nsecond & echo bad"], timeout=15)


@pytest.mark.parametrize("verb", ["status", "wait", "create", "merge"])
def test_native_front_door_crosses_real_worktrees_command_boundary(
    verb, tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    value = report(merged=True)
    expected = ["pr-watch", "snapshot", "project/repo", "7", "--json"]
    if verb == "create":
        expected = [
            "pr-create", "--repo", "project/repo", "--from-branch", "feature/x",
            "--title", "Example", "--body", "", "--json",
        ]
        value = {
            "repo": "project/repo", "number": 7, "pr_opened": True, "claimed": True,
            "url": "https://dev.azure.com/example/project/_git/repo/pullrequest/7",
        }
    elif verb == "merge":
        expected = ["pr-merge", "project/repo", "7", "--now", "--json"]
        value = {"repo": "project/repo", "pr": 7, "applied": True, "merged": False}
    command = tmp_path / ("native command.ps1" if os.name == "nt" else "native-command")
    if os.name == "nt":
        encoded = json.dumps(expected).replace("'", "''")
        response = json.dumps(value).replace("'", "''")
        script = (
            f"$expected = '{encoded}' | ConvertFrom-Json\n"
            "if ($args.Count -ne $expected.Count) { exit 61 }\n"
            "for ($i = 0; $i -lt $args.Count; $i++) {\n"
            "  if ($args[$i] -cne $expected[$i]) { exit 62 }\n"
            "}\n"
            f"[Console]::WriteLine('{response}')\n"
            "exit 0\n"
        )
    else:
        script = (
            f"#!{sys.executable}\nimport sys\n"
            f"assert sys.argv[1:] == {expected!r}\n"
            f"print({json.dumps(value)!r})\n"
        )
    command.write_text(script, encoding="utf-8")
    if os.name != "nt":
        command.chmod(0o700)
    assert cli.main(arguments(verb, str(command))) == 0
    assert json.loads(capsys.readouterr().out) == (
        {**value, "timedOut": False} if verb == "wait" else value
    )
