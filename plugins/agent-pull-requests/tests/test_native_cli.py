"""Native CLI routing keeps provider, policy and claimant authority in Worktrees."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_pull_requests import __main__ as cli
from agent_pull_requests import native_cli, native_command
from agent_pull_requests import native_author


def author_capabilities():
    return {
        "schema": "agent-worktrees.pr-author-capabilities", "version": 1,
        "repo": "project/repo",
        "provider": "azure-devops",
        "operations": {"metadata-update": True, "source-push": True, "qualified-create": True},
    }


def test_native_metadata_preserves_markdown_and_partial_identity(command, monkeypatch, capsys, tmp_path):
    body = '# Title\r\n\r\n"quotes", \u03bb and `code`\r\n'
    path = tmp_path / "body.md"
    path.write_bytes(body.encode("utf-8"))
    value = {"repo": "project/repo", "number": 7, "ok": False,
             "actions": {"body": {"state": "unknown"}}}
    calls = []

    def run(argv, *, timeout):
        calls.append((argv, timeout))
        return subprocess.CompletedProcess(
            argv, 0 if len(calls) == 1 else 1,
            json.dumps(author_capabilities() if len(calls) == 1 else value), "",
        )

    monkeypatch.setattr(native_author, "run_command", run)
    assert cli.main(arguments(
        "update", command, "--body-file", str(path),
        "--reviewer", "expert-id", "--work-item", "other/42",
    )) == 1
    assert json.loads(capsys.readouterr().out) == value
    assert f"--body-file={path}" in calls[1][0]
    assert all(body not in argument for argument in calls[1][0])
    assert "--reviewer=expert-id" in calls[1][0] and "--work-item=other/42" in calls[1][0]
    assert calls[0][1] == 20.0 and calls[1][1] == 90.0
    assert not any("pr-create" in call[0] or "pr-push" in call[0] for call in calls)


@pytest.mark.parametrize("capabilities", [{}, {"schema": "old"}, {
    **author_capabilities(), "operations": {"metadata-update": False},
}])
def test_native_metadata_old_peer_refuses_without_effect_or_fallback(
    command, monkeypatch, capsys, capabilities,
):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(capabilities), "")

    monkeypatch.setattr(native_author, "run_command", run)
    assert cli.main(arguments("update", command, "--title", "new")) == 1
    assert "does not advertise" in json.loads(capsys.readouterr().out)["error"]
    assert len(calls) == 1 and calls[0][1] == "pr-author-capabilities"


@pytest.mark.parametrize("supported", [False, True])
def test_native_worker_config_requires_selected_runtime_support(
    command, tmp_path, monkeypatch, capsys, supported,
):
    path = tmp_path / "worker policy.json"
    path.write_text("{}", "utf-8")
    capabilities = author_capabilities()
    if supported:
        capabilities["operations"]["worker-policy-config"] = True
    result = {"repo": "project/repo", "number": 7, "ok": True}
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, json.dumps(capabilities if len(calls) == 1 else result), "",
        )

    monkeypatch.setattr(native_author, "run_command", run)
    assert cli.main(arguments("update", command, "--config", str(path), "--title", "new")) == (
        0 if supported else 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert len(calls) == (2 if supported else 1)
    assert all(f"--config={path}" in call for call in calls)
    if supported:
        assert payload == result
    else:
        assert "does not advertise" in payload["error"]


@pytest.mark.parametrize("path", ["", "missing.json"])
def test_native_worker_config_never_falls_back_when_unavailable(
    command, monkeypatch, capsys, path,
):
    monkeypatch.setattr(native_author, "run_command", lambda *a, **k: pytest.fail("spawn"))
    assert cli.main(arguments("update", command, "--config", path)) == 1
    assert "configuration file is unavailable" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("config", ["", "worker.json"])
def test_author_config_without_native_selection_never_uses_github(
    monkeypatch, capsys, config,
):
    monkeypatch.setattr(cli, "_github_create", lambda *a, **k: pytest.fail("forge fallback"))
    assert cli.main([
        "create", "--repo", "project/repo", "--head", "topic", "--title", "Fix",
        "--config", config, "--json",
    ]) == 2
    assert "explicit --worktrees-command" in json.loads(capsys.readouterr().out)["error"]


def test_native_frontend_declares_its_own_worker_config_support(command, monkeypatch, capsys):
    monkeypatch.setattr(
        native_author, "run_command",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, json.dumps(author_capabilities()), ""),
    )
    assert cli.main([
        "author-capabilities", "--repo", "project/repo", "--worktrees-command", command, "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["frontend"]["worker-policy-config"] is True
    assert "worker-policy-config" not in payload["operations"]


def test_native_qualified_create_forwards_only_after_capability(command, monkeypatch, capsys):
    value = {"repo": "project/repo", "number": 7, "pr_opened": True, "url": "https://forge.invalid/7"}
    calls = stub(monkeypatch, value)
    monkeypatch.setattr(
        native_author, "run_command",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, json.dumps(author_capabilities()), ""),
    )
    assert cli.main(arguments(
        "create", command, "--source-workspace", str(Path(command).parent), "--expected-head", "a" * 40,
    )) == 0
    assert json.loads(capsys.readouterr().out)["number"] == 7
    assert f"--expected-head={'a' * 40}" in calls[0][0]


def test_native_source_push_preserves_lease_and_unknown_effect(command, monkeypatch, capsys, tmp_path):
    value = {"ok": False, "repo": "project/repo", "head": "a" * 40,
             "publication_state": "unknown", "expected_remote_head": ""}
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0 if len(calls) == 1 else 1,
            json.dumps(author_capabilities() if len(calls) == 1 else value), "",
        )

    monkeypatch.setattr(native_author, "run_command", run)
    assert cli.main([
        "push", "--repo", "project/repo", "--head", "users/demo/fix",
        "--workspace", str(tmp_path), "--expected-head", "a" * 40, "--expected-remote-head=",
        "--worktrees-command", command, "--json",
    ]) == 1
    assert json.loads(capsys.readouterr().out) == value
    assert calls[1][1] == "pr-push" and "--expected-remote-head=" in calls[1]
    assert "--branch=users/demo/fix" in calls[1]


def ready_result(status="confirmed"):
    result = {
        "schema": "agent-worktrees.pr-ready", "version": 1,
        "repo": "project/repo", "number": 7, "provider": "azure-devops",
        "ok": status == "confirmed", "actions": {"draft": {"status": status}},
    }
    if status != "confirmed":
        result["error"] = "Readiness not confirmed; inspect this PR before another attempt"
    return result


@pytest.mark.parametrize("status", ["confirmed", "failed", "unknown", "not-attempted"])
def test_native_ready_qualifies_and_preserves_exact_outcome(command, monkeypatch, capsys, status):
    support = author_capabilities()
    support["operations"]["draft-ready"] = True
    value = ready_result(status)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0 if len(calls) == 1 or value["ok"] else 1,
            json.dumps(support if len(calls) == 1 else value), "",
        )

    monkeypatch.setattr(native_author, "run_command", run)
    assert cli.main(arguments("ready", command)) == (0 if value["ok"] else 1)
    assert json.loads(capsys.readouterr().out) == value
    assert calls[1] == [command, "pr-author-ready", "--repo=project/repo", "--json", "--number=7"]


@pytest.mark.parametrize("value", [None, False, "true", 1])
def test_native_ready_old_or_malformed_support_refuses_before_mutation(command, monkeypatch, capsys, value):
    support = author_capabilities()
    if value is not None:
        support["operations"]["draft-ready"] = value
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(support), "")

    monkeypatch.setattr(native_author, "run_command", run)
    assert cli.main(arguments("ready", command)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["number"] == 7 and result["actions"]["draft"]["status"] == "not-attempted"
    assert result["schema"] == "agent-worktrees.pr-ready" and result["version"] == 1
    assert result["provider"] is None
    assert len(calls) == 1


@pytest.mark.parametrize("provider", ["missing", None, "", "   ", 7, False])
def test_native_ready_invalid_capability_provider_refuses_before_dispatch(
    command, monkeypatch, capsys, provider,
):
    support = author_capabilities()
    support["operations"]["draft-ready"] = True
    if provider == "missing":
        del support["provider"]
    else:
        support["provider"] = provider
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(support), "")

    monkeypatch.setattr(native_author, "run_command", run)
    assert cli.main(arguments("ready", command)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["actions"]["draft"]["status"] == "not-attempted"
    assert result["schema"] == "agent-worktrees.pr-ready" and result["version"] == 1
    assert result["provider"] is None and len(calls) == 1


@pytest.mark.parametrize("damage", [
    {"schema": "old"}, {"version": True}, {"number": 8}, {"provider": "github"},
    {"ok": True, "actions": {"draft": {"status": "unknown"}}}, {"actions": []},
    {"ok": False, "actions": {"draft": {"status": "unknown"}}}, {"error": "contradictory failure"},
])
def test_native_ready_unconfirmed_result_never_falls_back(command, monkeypatch, capsys, damage):
    support = author_capabilities()
    support["operations"]["draft-ready"] = True
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(
            support if len(calls) == 1 else {**ready_result(), **damage},
        ), "")

    monkeypatch.setattr(native_author, "run_command", run)
    assert cli.main(arguments("ready", command)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["number"] == 7 and result["actions"]["draft"]["status"] == "unknown"
    assert result["schema"] == "agent-worktrees.pr-ready" and result["version"] == 1
    assert result["provider"] == "azure-devops"
    assert len(calls) == 2


def test_native_ready_lost_result_preserves_target_without_retry(command, monkeypatch, capsys):
    support = author_capabilities()
    support["operations"]["draft-ready"] = True
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if len(calls) > 1:
            raise subprocess.TimeoutExpired(argv, 90)
        return subprocess.CompletedProcess(argv, 0, json.dumps(support), "")

    monkeypatch.setattr(native_author, "run_command", run)
    assert cli.main(arguments("ready", command)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["number"] == 7 and result["actions"]["draft"]["status"] == "unknown"
    assert result["schema"] == "agent-worktrees.pr-ready" and result["version"] == 1
    assert result["provider"] == "azure-devops"
    assert len(calls) == 2


@pytest.mark.parametrize("error", [
    OSError("probe executable unavailable"), subprocess.TimeoutExpired(["capability-probe"], 20),
])
def test_native_ready_capability_failure_is_not_an_attempt(command, monkeypatch, capsys, error):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        raise error

    monkeypatch.setattr(native_author, "run_command", run)
    assert cli.main(arguments("ready", command)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["number"] == 7 and result["actions"]["draft"]["status"] == "not-attempted"
    assert "before dispatch" in result["error"]
    assert len(calls) == 1 and calls[0][1] == "pr-author-capabilities"


def test_native_create_body_file_is_not_expanded_into_process_arguments(command, monkeypatch, capsys, tmp_path):
    path = tmp_path / "long-description.md"
    body = '# "quoted"\r\n\r\n\u03bb\r\n' * 4000
    path.write_bytes(body.encode("utf-8"))
    value = {"repo": "project/repo", "number": 7, "pr_opened": True, "url": "https://forge.invalid/7"}
    calls = stub(monkeypatch, value)
    monkeypatch.setattr(
        native_author, "run_command",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, json.dumps(author_capabilities()), ""),
    )
    assert cli.main(arguments("create", command, "--body-file", str(path))) == 0
    assert json.loads(capsys.readouterr().out)["number"] == 7
    assert f"--body-file={path}" in calls[0][0]
    assert all(len(argument) < 32768 for argument in calls[0][0])


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
    assert calls == [([command, "pr-watch", "snapshot", "project/repo", "7", "--json"], 60.0)]


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
        command, "pr-create", "--repo=project/repo", "--from-branch=feature/x",
        "--title=Example", "--body=body", "--json", "--draft",
    ], 90.0)]


@pytest.mark.parametrize("head", ["", " ", "\t", "\n"])
def test_native_create_empty_head_never_spawns_central_publication(head, command, monkeypatch, capsys):
    calls = stub(monkeypatch, {})
    assert cli.main(arguments("create", command, "--head", head)) == 1
    assert "nonempty pushed head" in json.loads(capsys.readouterr().out)["error"]
    assert not calls


@pytest.mark.parametrize("title,body", [("--draft", "---\nDetails"), ("--json", "")])
def test_native_create_option_like_values_survive_reparsing(title, body, command, monkeypatch, capsys):
    seen = []

    def run(argv, **kwargs):
        parser = argparse.ArgumentParser()
        parser.add_argument("verb", choices=["pr-create"])
        for name in ("repo", "from-branch", "title", "body"):
            parser.add_argument("--" + name, required=True)
        parser.add_argument("--json", action="store_true")
        seen.append(parser.parse_args(argv[1:]))
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "repo": "project/repo", "number": 7, "pr_opened": True,
            "url": "https://dev.azure.com/example/project/_git/repo/pullrequest/7",
        }), "")

    monkeypatch.setattr(native_cli, "run_command", run)
    assert cli.main(arguments("create", command, f"--title={title}", f"--body={body}")) == 0
    assert json.loads(capsys.readouterr().out)["number"] == 7
    assert len(seen) == 1
    assert (seen[0].title, seen[0].body, seen[0].from_branch) == (title, body, "feature/x")


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
    ("merge", ["--squash"]), ("merge", ["--merge"]), ("merge", ["--rebase"]),
    ("merge", ["--delete-branch"]),
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
    missing_hint = command + "-missing"
    assert not Path(missing_hint).exists()
    assert cli.main(arguments("status", missing_hint)) == 0
    assert json.loads(capsys.readouterr().out)["provider"] == "azure-devops"
    assert calls[0][0][:3] == prefix
    assert command not in calls[0][0]


@pytest.mark.parametrize("verb", ["status", "wait", "create", "merge"])
@pytest.mark.parametrize("selector", ["", "   "])
def test_explicit_empty_native_selection_never_uses_github(verb, selector, command, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(arguments(verb, selector))
    assert exc.value.code == 2
    assert "must not be empty" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell boundary")
def test_native_powershell_arguments_are_data_across_real_process_boundary(tmp_path):
    script = tmp_path / "echo-arguments.py"
    script.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    command = tmp_path / "worktrees with space.ps1"
    python = sys.executable.replace("'", "''")
    script_path = str(script).replace("'", "''")
    command.write_text(f"& '{python}' '{script_path}' @args\nexit $LASTEXITCODE\n", encoding="utf-8")
    values = [
        "pr-create", "", "--title", 'quotes " and apostrophe \' and & echo bad',
        "--body", "first\nsecond $(& exit 99) %PATH% !literal! \u00e9",
    ]
    result = native_command.run_command([str(command), *values], timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == values


@pytest.mark.skipif(os.name != "nt", reason="native batch boundary")
@pytest.mark.parametrize("values", [["pr-status"], ["--body", "line\nsecond & echo bad"]])
def test_native_batch_refuses_before_spawn(values, tmp_path, monkeypatch):
    command = tmp_path / "worktrees.cmd"
    command.write_text("@exit /b 61\n", encoding="utf-8")
    monkeypatch.setattr(native_command, "run_caller_owned", lambda *a, **k: pytest.fail("spawned"))
    with pytest.raises(ValueError, match="PowerShell"):
        native_command.run_command([str(command), *values], timeout=15)


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell boundary")
def test_native_does_not_fall_back_to_windows_powershell(tmp_path, monkeypatch):
    command = tmp_path / "worktrees.ps1"
    monkeypatch.setattr(native_command.shutil, "which", lambda name: None)
    monkeypatch.setattr(native_command, "run_caller_owned", lambda *a, **k: pytest.fail("spawned"))
    with pytest.raises(ValueError, match="7.3"):
        native_command.run_command([str(command), "pr-status"], timeout=15)


@pytest.mark.skipif(os.name != "nt", reason="native old PowerShell refusal boundary")
def test_native_rejects_old_powershell_before_target_execution(tmp_path, monkeypatch):
    interpreter = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    marker = tmp_path / "must-not-exist"
    command = tmp_path / "worktrees.ps1"
    marker_text = str(marker).replace("'", "''")
    command.write_text(f"[IO.File]::WriteAllText('{marker_text}', 'executed')\n", encoding="utf-8")
    monkeypatch.setattr(native_command.shutil, "which", lambda name: str(interpreter))
    result = native_command.run_command([str(command), "pr-status"], timeout=15)
    assert result.returncode == 126
    assert "7.3 or newer" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize(("verb", "with_config"), [
    ("status", False), ("wait", False), ("create", False), ("merge", False), ("create", True),
    ("ready", False), ("ready", True), ("nudge", False), ("nudge", True),
])
def test_native_front_door_crosses_real_worktrees_command_boundary(
    verb, with_config, tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    value = report(merged=True)
    expected = ["pr-watch", "snapshot", "project/repo", "7", "--json"]
    if verb == "create":
        expected = [
            "pr-create", "--repo=project/repo", "--from-branch=feature/x",
            "--title=Example", "--body=", "--json",
        ]
        value = {
            "repo": "project/repo", "number": 7, "pr_opened": True, "claimed": True,
            "url": "https://dev.azure.com/example/project/_git/repo/pullrequest/7",
        }
    elif verb == "merge":
        expected = ["pr-merge", "project/repo", "7", "--now", "--json"]
        value = {"repo": "project/repo", "pr": 7, "applied": True, "merged": False}
    elif verb == "ready":
        expected = ["pr-author-ready", "--repo=project/repo", "--json", "--number=7"]
        value = ready_result()
    elif verb == "nudge":
        expected = ["pr-author-nudge", "--repo=project/repo", "--json", "--number=7"]
        value = {
            "schema": "agent-worktrees.pr-review-request", "version": 1,
            "repo": "project/repo", "number": 7, "provider": "github",
            "ok": True, "supported": False, "requested": False, "reviewer": "",
            "actions": {"review_request": {"status": "no-op"}},
        }
    extra = []
    if with_config:
        policy = tmp_path / "worker policy.json"
        policy.write_text("{}", "utf-8")
        if verb == "ready":
            expected.insert(3, f"--config={policy}")
        else:
            expected.append(f"--config={policy}")
        extra = ["--config", str(policy)]
    command = tmp_path / ("native command.ps1" if os.name == "nt" else "native-command")
    python_script = (
        f"import sys\nassert sys.argv[1:] == {expected!r}\n"
        f"print({json.dumps(value)!r})\n"
    )
    if with_config or verb in ("ready", "nudge"):
        capabilities = author_capabilities()
        capabilities["operations"]["worker-policy-config"] = True
        capabilities["operations"]["draft-ready"] = True
        if verb == "nudge":
            capabilities["provider"] = "github"
            capabilities["operations"]["reviewer-request"] = True
        probe = ["pr-author-capabilities", "--repo=project/repo", "--json"]
        if with_config:
            probe.append(f"--config={policy}")
        python_script = (
            "import sys\n"
            f"if sys.argv[1:] == {probe!r}:\n    print({json.dumps(capabilities)!r})\n"
            f"else:\n    assert sys.argv[1:] == {expected!r}\n    print({json.dumps(value)!r})\n"
        )
    if os.name == "nt":
        target = tmp_path / "native_command.py"
        target.write_text(python_script, encoding="utf-8")
        python, target_path = (value.replace("'", "''") for value in (sys.executable, str(target)))
        script = f"& '{python}' '{target_path}' @args\nexit $LASTEXITCODE\n"
    else:
        script = f"#!{sys.executable}\n{python_script}"
    command.write_text(script, encoding="utf-8")
    if os.name != "nt":
        command.chmod(0o700)
    assert cli.main(arguments(verb, str(command), *extra)) == 0
    assert json.loads(capsys.readouterr().out) == (
        {**value, "timedOut": False} if verb == "wait" else value
    )
