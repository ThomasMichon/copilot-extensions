"""Native diff reads retain evidence and never select a legacy forge fallback."""

import json
import os
import subprocess
import sys

import pytest

from agent_pull_requests import __main__ as cli, native_diff as native


def report(**changes):
    return {
        "schema": "agent-worktrees.pr-diff-read", "version": 1,
        "repo": "example/repository", "pr": 7, "provider": "gitea",
        "supported": True, "complete": None, "error": "",
        "diff": "diff --git a/x b/x\r\n+--untrusted λ\t\n",
        "future": {"truncated": False, "revision": None}, **changes,
    }


def arguments(command, *extra):
    return ["diff", "--repo", "example/repository", "--number", "7",
            "--worktrees-command", command, "--json", *extra]


@pytest.fixture
def command(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    path = tmp_path / "selected-worktrees"
    path.touch()
    for name in ("_github_status", "_resolve_claimant_worktree_id", "_journal_pr_claim"):
        monkeypatch.setattr(cli, name, lambda *a, **k: pytest.fail("fallback or claim"))
    return str(path)


def stub(monkeypatch, value, *, code=0, stderr=""):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if isinstance(value, Exception):
            raise value
        return subprocess.CompletedProcess(
            argv, code, value if isinstance(value, str) else json.dumps(value), stderr,
        )

    monkeypatch.setattr(native, "run_command", run)
    return calls


@pytest.mark.parametrize("changes,exit_code", [
    ({}, 0), ({"complete": False}, 0), ({"complete": True, "diff": ""}, 0),
    ({"supported": False, "complete": False, "diff": ""}, 1),
    ({"error": "partial acquisition", "complete": False}, 1),
])
@pytest.mark.parametrize("json_mode", [False, True])
def test_complete_report_and_text_survive_independent_outcome(
    command, monkeypatch, capsys, changes, exit_code, json_mode,
):
    body = report(**changes)
    calls = stub(monkeypatch, body)
    args = arguments(command)
    if not json_mode:
        args.remove("--json")
    assert cli.main(args) == exit_code
    captured = capsys.readouterr()
    assert json.loads(captured.out) == body
    assert bool(captured.err) is bool(exit_code)
    assert calls == [([command, "pr-watch", "diff", "example/repository", "7", "--json"],
                      {"timeout": 60.0})]


def test_nonzero_owner_preserves_valid_report_but_cannot_claim_success(command, monkeypatch, capsys):
    calls = stub(monkeypatch, report(), code=2, stderr="owner acquisition failed")
    assert cli.main(arguments(command)) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == report()
    assert "owner acquisition failed" in captured.err and len(calls) == 1


@pytest.mark.parametrize("damage", [
    {"schema": "agent-worktrees.pr-diff"}, {"version": True}, {"version": 2},
    {"repo": "other/repo"}, {"pr": True}, {"pr": 8}, {"provider": ""},
    {"supported": 1}, {"complete": "unknown"}, {"error": None}, {"diff": None},
    {"future": float("nan")},
])
def test_bad_reports_retain_requested_identity_without_retry(command, monkeypatch, capsys, damage):
    calls = stub(monkeypatch, report(**damage))
    assert cli.main(arguments(command)) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["repo"] == "example/repository" and body["number"] == 7
    assert "error" in body and "diff" not in body and len(calls) == 1


@pytest.mark.parametrize("value", [
    "[]", "not-json", "x" * 1_048_577, "",
    OSError("selected runtime unavailable"), subprocess.TimeoutExpired(["owner"], 60),
    RuntimeError("native command output exceeds its byte budget"),
])
def test_lost_evidence_never_becomes_empty_supported_diff(command, monkeypatch, capsys, value):
    calls = stub(monkeypatch, value, stderr="selected owner refused")
    assert cli.main(arguments(command)) == 1
    body = json.loads(capsys.readouterr().out)
    assert "evidence unavailable" in body["error"] and "diff" not in body
    assert len(calls) == 1


def test_plain_refusal_retains_stderr_diagnostic(command, monkeypatch, capsys):
    stub(monkeypatch, "", code=2, stderr="diff unavailable in prior runtime")
    args = arguments(command)
    args.remove("--json")
    assert cli.main(args) == 1
    captured = capsys.readouterr()
    assert not captured.out and "diff unavailable in prior runtime" in captured.err


def test_oversized_text_refuses_before_utf8_allocation():
    class OversizedText(str):
        def encode(self, *args, **kwargs):
            pytest.fail("oversized text was encoded before refusal")

    with pytest.raises(ValueError, match="size budget"):
        native._report(OversizedText("x" * (native.MAX_REPORT_BYTES + 1)), "example/repository", 7)


@pytest.mark.parametrize("extra", [
    ["--number", "0"], ["--number", "-7"], ["--number", "true"],
    ["--worktrees-command", ""], ["--worktrees-command", " \t"],
    ["--repo", "wrong"], ["--resolve"], ["--num", "7"], ["--config", "worker.json"],
    ["--repo=--host=https://forge.example/foo"], ["--repo=--token=value/repo"],
    ["--repo=-leading/repo"], ["--repo=--config=path/file"],
])
@pytest.mark.parametrize("managed", [False, True])
def test_parser_refuses_before_direct_or_managed_selection(command, monkeypatch, extra, managed):
    from agent_pull_requests.watch_invocation import WatchInvocation

    if managed:
        monkeypatch.setattr(WatchInvocation, "capture", lambda **k: object())
    monkeypatch.setattr(cli, "_agent_worktrees_prefix", lambda: pytest.fail("peer selection"))
    calls = stub(monkeypatch, report())
    with pytest.raises(SystemExit) as exc:
        cli.main(arguments(command, *extra))
    assert exc.value.code == 2 and not calls


def test_native_selection_is_required(command, monkeypatch):
    calls = stub(monkeypatch, report())
    args = arguments(command)
    del args[args.index("--worktrees-command"):args.index("--worktrees-command") + 2]
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2 and not calls


def test_managed_peer_ignores_legacy_selector_hint(command, monkeypatch, capsys):
    from agent_pull_requests.watch_invocation import WatchInvocation

    monkeypatch.setattr(WatchInvocation, "capture", lambda **k: object())
    prefix = [sys.executable, "-I", "selected-peer.py"]
    monkeypatch.setattr(cli, "_agent_worktrees_prefix", lambda: prefix)
    calls = stub(monkeypatch, report())
    assert cli.main(arguments(command + "-missing")) == 0
    assert json.loads(capsys.readouterr().out) == report()
    assert calls[0][0][:3] == prefix


@pytest.mark.parametrize("peer", ["baseline", "prior", "rollback"])
def test_selected_process_and_supported_rollback(tmp_path, monkeypatch, capsys, peer):
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    expected = ["pr-watch", "diff", "example/repository", "7", "--json"]
    script = (
        f"import sys\nassert sys.argv[1:] == {expected!r}\n"
        + ("sys.stderr.write('diff unavailable in prior runtime'); sys.exit(2)\n"
           if peer == "prior" else f"print({json.dumps(report())!r})\n")
    )
    command = tmp_path / ("native.ps1" if os.name == "nt" else "native")
    if os.name == "nt":
        target = tmp_path / "peer.py"
        target.write_text(script, encoding="utf-8")
        python, path = (value.replace("'", "''") for value in (sys.executable, str(target)))
        script = f"& '{python}' '{path}' @args\nexit $LASTEXITCODE\n"
    else:
        script = f"#!{sys.executable}\n{script}"
    command.write_text(script, encoding="utf-8")
    if os.name != "nt":
        command.chmod(0o700)
    assert cli.main(arguments(str(command))) == (1 if peer == "prior" else 0)
    captured = capsys.readouterr()
    if peer == "prior":
        assert "diff unavailable in prior runtime" in json.loads(captured.out)["error"]
    else:
        assert json.loads(captured.out) == report()
