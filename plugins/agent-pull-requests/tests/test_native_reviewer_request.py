"""Native reviewer requests preserve capability, no-op and unknown-outcome semantics."""
import json
import subprocess

import pytest

from agent_pull_requests import __main__ as cli
from agent_pull_requests import native_author, native_reviewer_request


def support():
    return {
        "schema": "agent-worktrees.pr-author-capabilities", "version": 1,
        "repo": "example/repository", "provider": "github",
        "operations": {"reviewer-request": True, "worker-policy-config": True, "future": True},
    }


def result(status="accepted"):
    value = {
        "schema": "agent-worktrees.pr-review-request", "version": 1,
        "repo": "example/repository", "number": 7, "provider": "github",
        "ok": status in ("accepted", "no-op"),
        "supported": status in ("accepted", "failed", "unknown"), "requested": status == "accepted",
        "reviewer": "copilot-pull-request-reviewer[bot]",
        "actions": {"review_request": {"status": status}},
    }
    if not value["ok"]:
        value["error"] = "Inspect the same PR before another intentional request"
    return value


@pytest.fixture
def command(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    path = tmp_path / "selected-worktrees"
    path.touch()
    return str(path)


def arguments(command, *extra):
    return ["nudge", "--repo", "example/repository", "--number", "7",
            "--worktrees-command", command, "--json", *extra]


def stub(monkeypatch, declaration, value, *, code=None):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "pr-author-capabilities":
            return subprocess.CompletedProcess(argv, 0, json.dumps(declaration), "")
        if isinstance(value, Exception):
            raise value
        return subprocess.CompletedProcess(
            argv, code if code is not None else (0 if value["ok"] else 1), json.dumps(value), "",
        )

    monkeypatch.setattr(native_author, "run_command", run)
    monkeypatch.setattr(native_reviewer_request, "run_command", run)
    return calls


@pytest.mark.parametrize("status", ["accepted", "no-op", "not-attempted", "failed", "unknown"])
def test_native_request_preserves_every_versioned_owner_outcome(command, monkeypatch, capsys, status):
    value = result(status)
    if status == "not-attempted":
        value["provider"] = None
    calls = stub(monkeypatch, support(), value)
    assert cli.main(arguments(command)) == (0 if value["ok"] else 1)
    assert json.loads(capsys.readouterr().out) == value
    assert calls[1] == [command, "pr-author-nudge", "--repo=example/repository", "--json", "--number=7"]


@pytest.mark.parametrize("declaration", [
    {}, {**support(), "operations": {}}, {**support(), "provider": None},
    {**support(), "provider": "azure-devops"},
    {**support(), "operations": {"reviewer-request": "true"}},
    {**support(), "version": 2},
])
def test_old_or_rolled_back_owner_refuses_optional_feature_before_effect(
    command, monkeypatch, capsys, declaration,
):
    calls = stub(monkeypatch, declaration, result())
    assert cli.main(arguments(command)) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["actions"]["review_request"]["status"] == "not-attempted"
    assert len(calls) == 1


@pytest.mark.parametrize("damage", [
    {"schema": "old"}, {"version": True}, {"repo": "other/repository"}, {"number": True},
    {"provider": None}, {"requested": False}, {"supported": False}, {"ok": False},
    {"actions": []}, {"error": "contradictory failure"}, {"reviewer": ""},
])
def test_malformed_owner_result_is_unknown_never_noop_or_replayed(command, monkeypatch, capsys, damage):
    calls = stub(monkeypatch, support(), {**result(), **damage})
    assert cli.main(arguments(command)) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["actions"]["review_request"]["status"] == "unknown"
    assert body["provider"] == "github" and body["number"] == 7
    assert len(calls) == 2


@pytest.mark.parametrize("value", [subprocess.TimeoutExpired(["owner"], 90), OSError("response lost")])
def test_lost_owner_response_is_unknown_without_retry(command, monkeypatch, capsys, value):
    calls = stub(monkeypatch, support(), value)
    assert cli.main(arguments(command)) == 1
    assert json.loads(capsys.readouterr().out)["actions"]["review_request"]["status"] == "unknown"
    assert len(calls) == 2


def test_capability_timeout_is_not_an_attempt(command, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired(["capabilities"], 20)
    monkeypatch.setattr(native_author, "run_command", fail)
    monkeypatch.setattr(native_reviewer_request, "run_command", lambda *a, **k: pytest.fail("effect"))
    assert cli.main(arguments(command)) == 1
    assert json.loads(capsys.readouterr().out)["actions"]["review_request"]["status"] == "not-attempted"


def test_nonzero_success_claim_is_not_accepted(command, monkeypatch, capsys):
    calls = stub(monkeypatch, support(), result(), code=1)
    assert cli.main(arguments(command)) == 1
    assert json.loads(capsys.readouterr().out)["actions"]["review_request"]["status"] == "unknown"
    assert len(calls) == 2


def test_explicit_worker_policy_is_forwarded_to_both_owners(command, monkeypatch, tmp_path, capsys):
    policy = tmp_path / "worker policy.json"
    policy.write_text("{}", encoding="utf-8")
    calls = stub(monkeypatch, support(), result("no-op"))
    assert cli.main(arguments(command, "--config", str(policy))) == 0
    assert json.loads(capsys.readouterr().out)["requested"] is False
    assert all(f"--config={policy}" in call for call in calls)


@pytest.mark.parametrize("extra", [["--reviewer", "someone"], ["--auto"], ["--number", "0"]])
def test_invalid_arguments_never_dispatch(command, monkeypatch, capsys, extra):
    monkeypatch.setattr(native_author, "run_command", lambda *a, **k: pytest.fail("capability probe"))
    if extra[0] == "--number":
        assert cli.main(arguments(command, *extra)) == 1
    else:
        with pytest.raises(SystemExit) as caught:
            cli.main(arguments(command, *extra))
        assert caught.value.code == 2
