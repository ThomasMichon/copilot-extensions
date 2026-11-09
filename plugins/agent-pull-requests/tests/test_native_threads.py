"""Native discussion evidence remains lossless and never becomes effect authority."""
import json
import os
import subprocess
import sys

import pytest

from agent_pull_requests import __main__ as cli, native_threads as native


def report(**changes):
    return {
        "schema": "agent-worktrees.pr-threads", "version": 1,
        "repo": "example/repository", "pr": 7, "provider": "azure-devops",
        "supported": True, "complete": True, "error": "",
        "threads": [{"id": "5", "comments": [{"text": "--untrusted\nλ", "parent_id": "1"}],
                     "iteration_context": {"secondComparingIteration": 2}}],
        "observed_head_sha": "a" * 40, "head_sha": "b" * 40, "candidate_changed": True,
        "future": {"evidence": None}, **changes,
    }


@pytest.fixture
def command(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    path = tmp_path / "selected-worktrees"
    path.touch()
    for name in ("_github_status", "_resolve_claimant_worktree_id", "_journal_pr_claim"):
        monkeypatch.setattr(cli, name, lambda *a, **k: pytest.fail("fallback or claim"))
    return str(path)


def arguments(command, *extra):
    return ["threads", "--repo", "example/repository", "--number", "7",
            "--worktrees-command", command, "--json", *extra]


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
    ({}, 0), ({"complete": False}, 0), ({"complete": None}, 0),
    ({"supported": False, "complete": None}, 1), ({"error": "partial page"}, 1),
])
@pytest.mark.parametrize("json_mode", [False, True])
def test_full_report_survives_independent_outcome(
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
    assert calls == [([command, "pr-watch", "threads", "example/repository", "7", "--json"],
                      {"timeout": 60.0})]


def test_nonzero_owner_cannot_turn_valid_evidence_into_success(command, monkeypatch, capsys):
    body = report()
    calls = stub(monkeypatch, body, code=2, stderr="owner acquisition failed")
    assert cli.main(arguments(command)) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == body
    assert "owner acquisition failed" in captured.err
    assert len(calls) == 1


@pytest.mark.parametrize("damage", [
    {"schema": "old"}, {"version": True}, {"version": 2}, {"repo": "other/repo"},
    {"pr": True}, {"pr": 8}, {"provider": ""}, {"supported": 1},
    {"complete": "unknown"}, {"error": None}, {"threads": [None]},
    {"future": float("nan")},
])
def test_bad_envelope_retains_requested_target_without_retry(command, monkeypatch, capsys, damage):
    calls = stub(monkeypatch, report(**damage))
    assert cli.main(arguments(command)) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["repo"] == "example/repository" and body["number"] == 7
    assert "error" in body and "threads" not in body
    assert len(calls) == 1


@pytest.mark.parametrize("value", [
    "[]", "not-json", "x" * 1_048_577, "",
    OSError("selected runtime unavailable"), subprocess.TimeoutExpired(["owner"], 60),
    RuntimeError("native command output exceeds its byte budget"),
])
def test_unavailable_evidence_is_never_an_empty_supported_result(command, monkeypatch, capsys, value):
    calls = stub(monkeypatch, value, stderr="selected owner refused")
    assert cli.main(arguments(command)) == 1
    body = json.loads(capsys.readouterr().out)
    assert "evidence unavailable" in body["error"] and "threads" not in body
    assert len(calls) == 1


def test_plain_bad_report_preserves_literal_diagnostic(command, monkeypatch, capsys):
    stub(monkeypatch, "", code=2, stderr="this runtime has no threads verb")
    args = arguments(command)
    args.remove("--json")
    assert cli.main(args) == 1
    captured = capsys.readouterr()
    assert not captured.out and "this runtime has no threads verb" in captured.err


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


def test_managed_peer_uses_its_actual_selection_not_legacy_hint(command, monkeypatch, capsys):
    from agent_pull_requests.watch_invocation import WatchInvocation

    monkeypatch.setattr(WatchInvocation, "capture", lambda **k: object())
    prefix = [sys.executable, "-I", "selected-peer.py"]
    monkeypatch.setattr(cli, "_agent_worktrees_prefix", lambda: prefix)
    calls = stub(monkeypatch, report())
    assert cli.main(arguments(command + "-missing")) == 0
    assert json.loads(capsys.readouterr().out) == report()
    assert calls[0][0][:3] == prefix


@pytest.mark.parametrize("peer", ["baseline", "prior", "rollback"])
def test_real_selected_process_preserves_baseline_and_truthful_incompatibility(
    tmp_path, monkeypatch, capsys, peer,
):
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    expected = ["pr-watch", "threads", "example/repository", "7", "--json"]
    script = (
        f"import sys\nassert sys.argv[1:] == {expected!r}\n"
        + ("sys.stderr.write('threads unavailable in prior runtime'); sys.exit(2)\n"
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
        assert "threads unavailable in prior runtime" in json.loads(captured.out)["error"]
    else:
        assert json.loads(captured.out) == report()
