"""The native cancellation boundary never guesses support or repeats an effect."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_pull_requests import __main__ as cli, native_creation_cancel as native

WORKTREE, CREATION = "example-20261108-123456-abcd", "immutable-attempt"
REASON = '--declined "quotes"\nRetain source; no remaining useful contribution'


def discovery():
    return {
        "worktree_id": WORKTREE, "creation_cancellation_contract": native.CONTRACT,
        "pr_id": "different-active-attempt", "prs": [
            {"pr_id": CREATION, "pr_revision": 9}, {"pr_id": "different-active-attempt"},
        ],
    }


def receipt(**changes):
    return {
        "contract": native.CONTRACT, "worktree_id": WORKTREE, "pr_id": CREATION,
        "pr_revision": 10, "success": True, "state": "abandoned", "creation_cancelled": True,
        "already_cancelled": False, "provider_closed": False, "claim_released": False,
        **changes,
    }


@pytest.fixture
def command(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    path = tmp_path / "selected-worktrees"
    path.touch()
    return str(path)


def arguments(command, *extra):
    return [
        "cancel-creation", "--worktree", WORKTREE, "--creation", CREATION,
        "--expected-revision", "4", f"--reason={REASON}",
        "--worktrees-command", command, "--json", *extra,
    ]


def stub(monkeypatch, support, value, *, code=0):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        body = support if len(calls) == 1 else value
        if isinstance(body, Exception):
            raise body
        return subprocess.CompletedProcess(
            argv, code if len(calls) == 2 else 0,
            body if isinstance(body, str) else json.dumps(body), "",
        )

    monkeypatch.setattr(native, "run_command", run)
    return calls


def test_no_confirmation_or_bad_input_precedes_even_runtime_selection(command, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("runtime selection or discovery before admission")
    monkeypatch.setattr(native, "command_path", forbidden)
    monkeypatch.setattr(native, "run_command", forbidden)
    assert cli.main(arguments(command)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["frontend"]["cancellation_status"] == "not-attempted"
    assert "actual operator approval" in result["error"]
    assert "rebase, conflict or delay" in result["error"]


@pytest.mark.parametrize("extra", [
    ["--worktree", ""], ["--worktree", " "], ["--worktree", " shortened "],
    ["--creation", ""], ["--creation", "\0"], ["--expected-revision", "-1"],
    ["--reason", " \t"],
])
def test_invalid_values_do_not_discover(command, monkeypatch, capsys, extra):
    calls = stub(monkeypatch, {}, {})
    assert cli.main(arguments(command, "--confirm", *extra)) == 1
    assert json.loads(capsys.readouterr().out)["frontend"]["cancellation_status"] == "not-attempted"
    assert not calls


@pytest.mark.parametrize("extra", [
    ["--repo", "other/repo"], ["--number", "7"], ["--pr", "7"],
    ["--config", "worker.json"], ["--worker-policy", "worker.json"],
    ["--expected-revision", "4.5"], ["--conf"], ["--creaton", CREATION],
])
def test_strict_native_parser_never_ignores_external_or_unknown_selection(
    command, monkeypatch, extra,
):
    calls = stub(monkeypatch, {}, {})
    with pytest.raises(SystemExit) as exc:
        cli.main(arguments(command, "--confirm", *extra))
    assert exc.value.code == 2 and not calls


@pytest.mark.parametrize("value", ["", " ", "\t"])
def test_managed_peer_cannot_bypass_blank_command_selector(command, monkeypatch, value):
    from agent_pull_requests.watch_invocation import WatchInvocation

    peer = SimpleNamespace(snapshot_peer_prefix=lambda: [command])
    monkeypatch.setattr(WatchInvocation, "capture", lambda **kwargs: peer)
    calls = stub(monkeypatch, discovery(), receipt())
    with pytest.raises(SystemExit) as exc:
        cli.main(arguments(command, "--confirm", "--worktrees-command", value))
    assert exc.value.code == 2 and not calls


@pytest.mark.parametrize("support", [
    {}, [], "not-json", {**discovery(), "error": "record unavailable"},
    {**discovery(), "worktree_id": "other"},
    {**discovery(), "creation_cancellation_contract": "prior"},
    {**discovery(), "prs": []}, {**discovery(), "prs": [{}, None]},
    {**discovery(), "prs": [{"pr_id": CREATION}, {"pr_id": CREATION}]},
    subprocess.TimeoutExpired(["status"], 20), OSError("receiver unavailable"),
])
def test_failed_discovery_never_dispatches(command, monkeypatch, capsys, support):
    calls = stub(monkeypatch, support, receipt())
    assert cli.main(arguments(command, "--confirm")) == 1
    assert json.loads(capsys.readouterr().out)["frontend"]["cancellation_status"] == "not-attempted"
    assert calls == [[command, "pr-status", WORKTREE, "--all", "--no-live", "--json"]]


@pytest.mark.parametrize("damage", [
    {"contract": "prior"}, {"worktree_id": "other"}, {"pr_id": "other"},
    {"pr_revision": True}, {"pr_revision": 4}, {"pr_revision": "10"},
    {"state": "closed"}, {"success": False}, {"creation_cancelled": 1},
    {"already_cancelled": "true"}, {"provider_closed": True}, {"claim_released": True},
    {"error": "owner refused after observation"},
])
def test_postdispatch_invalid_result_is_unknown_never_replayed(command, monkeypatch, capsys, damage):
    calls = stub(monkeypatch, discovery(), receipt(**damage))
    assert cli.main(arguments(command, "--confirm")) == 1
    value = json.loads(capsys.readouterr().out)
    assert value["frontend"]["cancellation_status"] == "unknown"
    assert "original revision and reason" in value["frontend"]["diagnostic"]
    assert value["frontend"]["selected_worktree_id"] == WORKTREE
    assert value["frontend"]["selected_pr_id"] == CREATION
    assert value["frontend"]["selected_expected_revision"] == 4
    assert value["frontend"]["selected_reason"] == REASON
    if "error" in damage:
        assert value["error"] == damage["error"]
    assert len(calls) == 2 and calls[1][1] == "pr-abandon"


@pytest.mark.parametrize("value,code", [
    ("not-json", 0), ([], 0), (receipt(), 1),
    (subprocess.TimeoutExpired(["abandon"], 90), 0), (OSError("response lost"), 0),
    ({"error": "save failed; reconcile original attempt", "success": False}, 1),
])
def test_lost_or_failed_dispatch_is_unknown(command, monkeypatch, capsys, value, code):
    calls = stub(monkeypatch, discovery(), value, code=code)
    assert cli.main(arguments(command, "--confirm")) == 1
    assert json.loads(capsys.readouterr().out)["frontend"]["cancellation_status"] == "unknown"
    assert len(calls) == 2


def test_plain_owner_error_also_exposes_unknown_outcome_and_recovery(command, monkeypatch, capsys):
    error = "Local creation cancellation refused: test-owned save failure"
    calls = stub(monkeypatch, discovery(), {"success": False, "error": error}, code=1)
    args = [value for value in arguments(command, "--confirm") if value != "--json"]
    assert cli.main(args) == 1
    captured = capsys.readouterr()
    assert not captured.out and error in captured.err
    assert "cancellation outcome is unknown" in captured.err
    assert "original revision and reason" in captured.err
    assert "no retry or fallback was attempted" in captured.err
    assert len(calls) == 2


def test_plain_local_refusal_does_not_duplicate_the_same_diagnostic(command, monkeypatch, capsys):
    calls = stub(monkeypatch, {}, {})
    assert cli.main([value for value in arguments(command) if value != "--json"]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "actual operator approval" in captured.err
    assert captured.err.count("agent-pull-requests:") == 1 and not calls


def test_plain_success_retains_complete_owner_receipt_like_other_native_verbs(command, monkeypatch, capsys):
    value = receipt(future={"evidence": "retain"})
    calls = stub(monkeypatch, discovery(), value)
    assert cli.main([v for v in arguments(command, "--confirm") if v != "--json"]) == 0
    captured = capsys.readouterr()
    assert not captured.err
    assert json.loads(captured.out) == {**value, "frontend": {"cancellation_status": "confirmed"}}
    assert len(calls) == 2


@pytest.mark.parametrize("stage", ["pr-status", "pr-abandon"])
def test_missing_stdout_preserves_receiver_diagnostic_and_stage_outcome(
    command, monkeypatch, capsys, stage,
):
    calls = stub(monkeypatch, discovery(), receipt())
    run = native.run_command
    def missing(argv, **kwargs):
        response = run(argv, **kwargs)
        if argv[1] == stage:
            response.stdout, response.stderr = None, f"{stage}: result stream unavailable"
        return response
    monkeypatch.setattr(native, "run_command", missing)
    assert cli.main(arguments(command, "--confirm")) == 1
    value = json.loads(capsys.readouterr().out)
    assert f"{stage}: result stream unavailable" in value["error"]
    assert value["frontend"]["cancellation_status"] == (
        "not-attempted" if stage == "pr-status" else "unknown"
    )
    assert len(calls) == (1 if stage == "pr-status" else 2)


def test_malformed_owner_error_is_preserved_with_separate_frontend_diagnostic(command, monkeypatch, capsys):
    calls = stub(monkeypatch, discovery(), {"success": False, "error": ["owner", "evidence"]})
    assert cli.main(arguments(command, "--confirm")) == 1
    value = json.loads(capsys.readouterr().out)
    assert value["error"] == ["owner", "evidence"]
    assert "outcome is unknown" in value["frontend"]["diagnostic"]
    assert len(calls) == 2


@pytest.mark.parametrize("stage", ["decode", "discovery", "dispatch"])
def test_missing_stderr_never_escapes_outcome_classification(command, monkeypatch, capsys, stage):
    calls = stub(monkeypatch, discovery(), receipt(success=False))
    run = native.run_command
    def missing(argv, **kwargs):
        response = run(argv, **kwargs)
        if stage != "dispatch" or argv[1] == "pr-abandon":
            response.stderr = None
            response.returncode = 1
            if stage == "decode":
                response.stdout = None
        return response
    monkeypatch.setattr(native, "run_command", missing)
    assert cli.main(arguments(command, "--confirm")) == 1
    value = json.loads(capsys.readouterr().out)
    assert value["frontend"]["cancellation_status"] == (
        "unknown" if stage == "dispatch" else "not-attempted"
    )
    assert isinstance(value["frontend"]["diagnostic"], str)
    assert len(calls) == (2 if stage == "dispatch" else 1)


@pytest.mark.parametrize("already", [False, True])
def test_nonactive_exact_attempt_preserves_original_revision_reason_and_additions(
    command, monkeypatch, capsys, already,
):
    value = receipt(already_cancelled=already, future={"value": 1}, frontend={"other": True})
    calls = stub(monkeypatch, discovery(), value)
    assert cli.main(arguments(command, "--confirm")) == 0
    assert json.loads(capsys.readouterr().out) == {
        **value, "frontend": {"other": True, "cancellation_status": "confirmed"},
    }
    assert calls[1] == [
        command, "pr-abandon", WORKTREE, f"--creation={CREATION}", "--expected-revision=4",
        f"--reason={REASON}", "--confirm", "--json",
    ]


@pytest.mark.parametrize("receiver", ["current", "prior", "rollback"])
def test_selected_receiver_real_parser_process_and_supported_rollback(
    receiver, tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    calls = tmp_path / "calls.jsonl"
    support = discovery()
    if receiver != "current":
        support.pop("creation_cancellation_contract")
    script = (
        "import argparse, json, pathlib, sys\n"
        "p = argparse.ArgumentParser(allow_abbrev=False)\n"
        "s = p.add_subparsers(dest='verb', required=True)\n"
        "r = s.add_parser('pr-status', allow_abbrev=False)\n"
        "r.add_argument('worktree_id')\n"
        "for flag in ('all', 'no-live', 'json'): r.add_argument('--' + flag, action='store_true', required=True)\n"
        "a = s.add_parser('pr-abandon', allow_abbrev=False)\n"
        "a.add_argument('worktree_id')\n"
        "for flag in ('creation', 'expected-revision', 'reason'): a.add_argument('--' + flag, required=True)\n"
        "for flag in ('confirm', 'json'): a.add_argument('--' + flag, action='store_true', required=True)\n"
        "args = p.parse_args()\n"
        f"assert args.worktree_id == {WORKTREE!r}\n"
        f"with pathlib.Path({str(calls)!r}).open('a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if args.verb == 'pr-status':\n"
        f"    print({json.dumps(support)!r})\n"
        "else:\n"
        f"    assert (args.creation, args.expected_revision, args.reason) == {(CREATION, '4', REASON)!r}\n"
        f"    print({json.dumps(receipt())!r})\n"
    )
    command = tmp_path / ("selected receiver.ps1" if os.name == "nt" else "selected-receiver")
    if os.name == "nt":
        target = tmp_path / "receiver.py"
        target.write_text(script, encoding="utf-8")
        python, target_path = (v.replace("'", "''") for v in (sys.executable, str(target)))
        script = f"& '{python}' '{target_path}' @args\nexit $LASTEXITCODE\n"
    else:
        script = f"#!{sys.executable}\n{script}"
    command.write_text(script, encoding="utf-8")
    if os.name != "nt":
        command.chmod(0o700)
    assert cli.main(arguments(str(command), "--confirm")) == (0 if receiver == "current" else 1)
    value = json.loads(capsys.readouterr().out)
    assert value["frontend"]["cancellation_status"] == (
        "confirmed" if receiver == "current" else "not-attempted"
    )
    assert len(calls.read_text().splitlines()) == (2 if receiver == "current" else 1)
