"""Caller creation identity is independent of transport and attempt identity."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
import time
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import creation_requests as requests
from agent_worktrees import tracking


@pytest.fixture
def allocation(tmp_path, monkeypatch):
    records = tmp_path / "state" / "worktrees"
    records.mkdir(parents=True)
    monkeypatch.setattr(requests.cfg, "tracking_dir", lambda _name=None: records)
    config = SimpleNamespace(repo_name="worker")
    facts = {"project": "worker", "anchor": str(tmp_path / "anchor")}
    calls = []

    def allocate(reserve):
        worktree = {
            "id": f"generated-{len(calls)}", "repo": "worker",
            "path": str(tmp_path / f"checkout-{len(calls)}"),
            "branch": f"worktree/generated-{len(calls)}",
        }
        reserve(worktree)
        calls.append(worktree)
        Path(worktree["path"]).mkdir()
        (records / f"{worktree['id']}.yaml").write_text(json.dumps({
            "worktree_id": worktree["id"], "repo": worktree["repo"],
            "worktree_path": worktree["path"], "branch": worktree["branch"],
            "status": "active", "creation_nonce": f"nonce-{worktree['id']}",
        }))
        return {"worktree": worktree}

    return config, facts, allocate, calls, records


@pytest.mark.guard
def test_completed_creation_replays_original_generated_identity(allocation):
    config, facts, allocate, calls, _ = allocation
    first = requests.create(config, "logical:1", facts, allocate)
    second = requests.create(config, "logical:1", facts, allocate)
    assert first["worktree"] == second["worktree"]
    assert first["worktree"]["id"] != "logical:1"
    assert second["creation"]["replayed"] is True
    assert len(calls) == 1
    replacement = requests.create(config, "logical:2", facts, allocate)
    assert replacement["worktree"]["id"] != first["worktree"]["id"]
    assert len(calls) == 2


@pytest.mark.guard
def test_concurrent_creation_reports_pending_without_another_effect(allocation):
    config, facts, allocate, calls, _ = allocation
    entered, finish = Event(), Event()

    def slow(reserve):
        entered.set()
        assert finish.wait(5)
        return allocate(reserve)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(requests.create, config, "logical", facts, slow)
        assert entered.wait(5)
        try:
            with pytest.raises(requests.CreationRequestFailure) as caught:
                requests.create(config, "logical", facts, allocate)
            assert caught.value.status == "pending"
            assert calls == []
        finally:
            finish.set()
        assert future.result()["creation"]["status"] == "complete"
    assert len(calls) == 1


@pytest.mark.guard
def test_interrupted_effect_preserves_original_recovery_identity(allocation):
    config, facts, allocate, calls, _ = allocation

    def interrupted(reserve):
        allocate(reserve)
        raise OSError("lost allocation completion")

    with pytest.raises(OSError, match="lost allocation"):
        requests.create(config, "logical", facts, interrupted)
    with pytest.raises(requests.CreationRequestFailure) as caught:
        requests.create(config, "logical", facts, allocate)
    assert caught.value.status == "pending"
    assert caught.value.worktree == calls[0]
    assert caught.value.payload()["creation"]["repeat_new"] is False
    assert len(calls) == 1

@pytest.mark.parametrize("failure", [RuntimeError, ValueError, OSError])
@pytest.mark.guard
def test_refused_pre_effect_creation_can_retry_only_the_same_facts(allocation, failure):
    config, facts, allocate, calls, _ = allocation

    def refused(_reserve):
        raise failure("pre-effect validation refused")

    with pytest.raises(failure, match="pre-effect validation"):
        requests.create(config, "logical", facts, refused)
    assert calls == []
    with pytest.raises(requests.CreationRequestFailure) as changed:
        requests.create(config, "logical", {**facts, "anchor": "different"}, allocate)
    assert changed.value.status == "conflict"
    assert calls == []
    result = requests.create(config, "logical", facts, allocate)
    replay = requests.create(config, "logical", facts, allocate)
    assert result["worktree"] == replay["worktree"]
    assert len(calls) == 1

@pytest.mark.guard
def test_failure_after_allocation_reservation_never_resets_the_key(allocation):
    config, facts, allocate, calls, _ = allocation
    original = {"id": "original", "repo": "worker", "path": "original-path", "branch": "worktree/original"}

    def failed(reserve):
        reserve(original)
        raise RuntimeError("after the effects boundary")

    with pytest.raises(RuntimeError, match="effects boundary"):
        requests.create(config, "logical", facts, failed)
    with pytest.raises(requests.CreationRequestFailure) as pending:
        requests.create(config, "logical", facts, allocate)
    assert pending.value.status == "pending" and pending.value.worktree == original
    assert calls == []


@pytest.mark.guard
def test_changed_facts_conflict_before_allocating(allocation):
    config, facts, allocate, calls, _ = allocation
    requests.create(config, "logical", facts, allocate)
    with pytest.raises(requests.CreationRequestFailure) as caught:
        requests.create(config, "logical", {**facts, "anchor": "different"}, allocate)
    assert caught.value.status == "conflict"
    assert len(calls) == 1


@pytest.mark.parametrize("terminal", ["finalized", "archived", "missing"])
def test_terminal_key_never_allocates_replacement(allocation, terminal):
    config, facts, allocate, calls, records = allocation
    result = requests.create(config, "logical", facts, allocate)
    path = records / f"{result['worktree']['id']}.yaml"
    if terminal == "missing":
        path.unlink()
    else:
        record = json.loads(path.read_text())
        record["status"] = terminal
        path.write_text(json.dumps(record))
    with pytest.raises(requests.CreationRequestFailure) as caught:
        requests.create(config, "logical", facts, allocate)
    assert caught.value.status == "terminal"
    assert len(calls) == 1


def test_terminal_history_survives_reappearance_of_original_record(allocation):
    config, facts, allocate, calls, records = allocation
    result = requests.create(config, "logical", facts, allocate)
    path = records / f"{result['worktree']['id']}.yaml"
    original = path.read_bytes()
    path.unlink()
    with pytest.raises(requests.CreationRequestFailure, match="no longer tracked"):
        requests.create(config, "logical", facts, allocate)
    path.write_bytes(original)
    with pytest.raises(requests.CreationRequestFailure) as caught:
        requests.create(config, "logical", facts, allocate)
    assert caught.value.status == "terminal"
    assert len(calls) == 1


def test_owner_record_drift_refuses_replay(allocation):
    config, facts, allocate, calls, records = allocation
    result = requests.create(config, "logical", facts, allocate)
    path = records / f"{result['worktree']['id']}.yaml"
    record = json.loads(path.read_text())
    record["repo"] = "other"
    path.write_text(json.dumps(record))
    with pytest.raises(requests.CreationRequestFailure) as caught:
        requests.create(config, "logical", facts, allocate)
    assert caught.value.status == "conflict"
    assert len(calls) == 1


def test_reused_record_identity_does_not_revive_terminal_creation(allocation):
    config, facts, allocate, calls, records = allocation
    result = requests.create(config, "logical", facts, allocate)
    path = records / f"{result['worktree']['id']}.yaml"
    record = json.loads(path.read_text())
    record["creation_nonce"] = "replacement-creation"
    path.write_text(json.dumps(record))
    with pytest.raises(requests.CreationRequestFailure) as caught:
        requests.create(config, "logical", facts, allocate)
    assert caught.value.status == "conflict"
    assert len(calls) == 1


def test_attempt_changes_do_not_change_allocation_facts(tmp_path):
    config = SimpleNamespace(
        repo_name="worker",
        default_repo=SimpleNamespace(
            anchor=str(tmp_path), worktree_root=str(tmp_path / "worktrees"),
            remote="origin", default_branch="main",
        ),
    )
    options = dict(
        kind="session", interface="acp", origin="delegate", owner=None, owner_ref=None,
        name=None, bound_agent="worker", no_pair=True, pending_seed=None,
        dispatch_attempt={"task_id": "task", "attempt": 1, "reservation_key": "first"},
    )
    first = requests.allocation_facts(config, **options)
    options["dispatch_attempt"] = {
        "task_id": "task", "attempt": 2, "reservation_key": "second",
    }
    assert requests.allocation_facts(config, **options) == first
    options["owner_ref"] = "machine/controller/worktree#session-1"
    original_owner = requests.allocation_facts(config, **options)
    options["owner_ref"] = "machine/controller/worktree#session-2"
    assert requests.allocation_facts(config, **options) == original_owner


@pytest.mark.parametrize("key", ["", " ", "x" * 1025])
def test_invalid_key_has_no_allocation_effect(allocation, key):
    config, facts, allocate, calls, _ = allocation
    with pytest.raises(requests.CreationRequestFailure) as caught:
        requests.create(config, key, facts, allocate)
    assert caught.value.status == "invalid"
    assert calls == []


def test_core_keyed_creation_replays_without_running_create_again(tmp_path, monkeypatch):
    import agent_worktrees.__main__ as main
    from test_worktree_creation_seed import (
        _create_config, _stub_create_worktree_core_internals,
    )

    config = _create_config(tmp_path)
    records = tmp_path / "tracking"
    monkeypatch.setattr(main.cfg, "tracking_dir", lambda _name=None: records)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    created = []

    def create(_anchor, path, *_args):
        created.append(path)
        Path(path).mkdir()

    monkeypatch.setattr(main.git_ops, "create_worktree", create)
    first = main._create_worktree_core(config, creation_key="caller", no_pair=True)
    second = main._create_worktree_core(config, creation_key="caller", no_pair=True)
    assert first["worktree"] == second["worktree"]
    assert len(created) == len(tracking.list_records(records)) == 1

@pytest.mark.guard
def test_core_profile_refusal_does_not_reserve_a_phantom_allocation(tmp_path, monkeypatch):
    import sqlite3
    import agent_worktrees.__main__ as main
    from test_worktree_creation_seed import _create_config, _stub_create_worktree_core_internals

    config = _create_config(tmp_path)
    records = tmp_path / "tracking"
    monkeypatch.setattr(main.cfg, "tracking_dir", lambda _name=None: records)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    created = []
    monkeypatch.setattr(main.git_ops, "create_worktree",
        lambda _anchor, path, *_args: created.append(path) or Path(path).mkdir())

    def invalid_profile(_config):
        raise RuntimeError("invalid profile")

    monkeypatch.setattr(main, "_validate_profile_assignment_config", invalid_profile)
    with pytest.raises(RuntimeError, match="invalid profile"):
        main._create_worktree_core(config, creation_key="caller", no_pair=True)
    with sqlite3.connect(records.parent / "workspace-creation.sqlite3") as db:
        assert db.execute("SELECT status,worktree FROM creations").fetchall() == [("refused", None)]
    assert created == [] and tracking.list_records(records) == []
    monkeypatch.setattr(main, "_validate_profile_assignment_config", lambda _config: None)
    first = main._create_worktree_core(config, creation_key="caller", no_pair=True)
    second = main._create_worktree_core(config, creation_key="caller", no_pair=True)
    assert first["worktree"] == second["worktree"]
    assert len(created) == len(tracking.list_records(records)) == 1

@pytest.mark.guard
def test_core_codename_policy_refusal_does_not_reserve_a_phantom_allocation(tmp_path, monkeypatch):
    import sqlite3
    from dataclasses import replace
    import agent_worktrees.__main__ as main
    from agent_worktrees import codename_tracking
    from test_worktree_creation_seed import _create_config, _stub_create_worktree_core_internals

    config = _create_config(tmp_path)
    records = tmp_path / "tracking"
    monkeypatch.setattr(main.cfg, "tracking_dir", lambda _name=None: records)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    config = replace(config, repos={
        config.repo_name: replace(
            config.default_repo,
            pr=replace(config.default_repo.pr, enabled=True, source_attribution_configured=False),
            codename=main.cfg.CodenameConfig(
                wordlist_path=str(tmp_path / "custom-wordlist.json"), wordlist_path_configured=True,
            ),
        ),
    })
    monkeypatch.setattr(main.cfg, "load_config", lambda *a, **k: config)
    created = []
    monkeypatch.setattr(main.git_ops, "create_worktree",
        lambda _anchor, path, *_args: created.append(path) or Path(path).mkdir())
    with pytest.raises(codename_tracking.CodenameAttributionPolicyError):
        main._create_worktree_core(config, creation_key="caller", no_pair=True)
    with sqlite3.connect(records.parent / "workspace-creation.sqlite3") as db:
        assert db.execute("SELECT status,worktree FROM creations").fetchall() == [("refused", None)]
    assert created == [] and tracking.list_records(records) == []
    config = replace(config, repos={
        config.repo_name: replace(config.default_repo, codename=main.cfg.CodenameConfig()),
    })
    first = main._create_worktree_core(config, creation_key="caller", no_pair=True)
    second = main._create_worktree_core(config, creation_key="caller", no_pair=True)
    assert first["worktree"] == second["worktree"]
    assert len(created) == len(tracking.list_records(records)) == 1


def test_cli_creation_key_and_pending_response(monkeypatch, capfd):
    import agent_worktrees.__main__ as main
    from agent_worktrees import worktree_ops_cli

    args = main.build_parser().parse_args(["create", "--creation-key", "logical", "--json", "--no-owner"])
    captured = {}
    monkeypatch.setattr(main.cfg, "load_config", lambda: SimpleNamespace())

    def pending(_config, **options):
        captured.update(options)
        raise requests.CreationRequestFailure(
            "pending", "reconcile original", {"id": "original", "repo": "worker"},
        )

    monkeypatch.setattr(main, "_create_worktree_core", pending)
    assert worktree_ops_cli.cmd_create(args) == 3
    assert captured["creation_key"] == "logical"
    response = json.loads(capfd.readouterr().out)
    assert response["creation"]["version"] == 1
    assert response["creation"]["status"] == "pending"
    assert response["worktree"]["id"] == "original"


@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_CREATION_NATIVE") != "1",
    reason="opt-in native fresh-owner creation boundary",
)
@pytest.mark.timeout(120)
@pytest.mark.parametrize("refusal", ["profile", "codename"])
def test_native_pre_effect_refusal_then_same_key_repair(tmp_path, refusal):
    import sqlite3

    anchor = tmp_path / "anchor"
    anchor.mkdir()
    for arguments in (
        ["init", "-q", "--initial-branch=main"],
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "--allow-empty", "-qm", "fixture"],
        ["remote", "add", "origin", str(anchor)],
    ):
        subprocess.run(["git", "-C", str(anchor), *arguments], check=True,
                       capture_output=True, text=True, timeout=15, **no_window_kwargs())
    argv = [
        sys.executable, str(Path(__file__).with_name("workspace_owner_fixture.py")), str(tmp_path),
        "create", "--system", "--name", "fixture", "--no-owner", "--no-pair",
        "--creation-key", "logical:1", "--json",
    ]
    refused = subprocess.run(
        argv, capture_output=True, text=True, timeout=40,
        env={**os.environ, f"FIXTURE_REFUSE_{refusal.upper()}": "1"}, **no_window_kwargs(),
    )
    assert refused.returncode == 1, refused.stdout
    assert ("fixture profile refused" if refusal == "profile" else "custom codename") in refused.stdout
    with sqlite3.connect(tmp_path / "state" / "workspace-creation.sqlite3") as db:
        assert db.execute("SELECT status,worktree FROM creations").fetchall() == [("refused", None)]
    assert not (tmp_path / "worktrees").exists() or not list((tmp_path / "worktrees").iterdir())
    first = subprocess.run(argv, capture_output=True, text=True, timeout=40, **no_window_kwargs())
    assert first.returncode == 0, first.stderr
    replay = subprocess.run(argv, capture_output=True, text=True, timeout=40, **no_window_kwargs())
    assert replay.returncode == 0, replay.stderr
    assert json.loads(first.stdout)["worktree"] == json.loads(replay.stdout)["worktree"]
    assert len(tracking.list_records(tmp_path / "state" / "worktrees")) == 1


@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_CREATION_NATIVE") != "1",
    reason="opt-in native fresh-owner creation boundary",
)
@pytest.mark.timeout(120)
def test_native_fresh_owner_cli_replay_survives_process_restart(tmp_path):
    """Real parser/handler, Git allocation, tracking, and durable key journal."""
    anchor = tmp_path / "anchor"
    anchor.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(anchor), *args], check=True,
            capture_output=True, text=True, timeout=15, **no_window_kwargs(),
        ).stdout

    git("init", "-q", "--initial-branch=main")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--allow-empty", "-qm", "fixture")
    git("remote", "add", "origin", str(anchor))
    driver = Path(__file__).with_name("workspace_owner_fixture.py")
    argv = [
        sys.executable, str(driver), str(tmp_path),
        "create", "--system", "--name", "fixture", "--no-owner", "--no-pair",
        "--creation-key", "logical:1", "--json",
    ]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")

    def invoke(expected_exit=0):
        result = subprocess.run(
            argv,
            capture_output=True, text=True, env=env, timeout=40,
            **no_window_kwargs(),
        )
        assert result.returncode == expected_exit, result.stderr
        return json.loads(result.stdout)

    leader = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**env, "FIXTURE_PAUSE_CREATION": "1"}, **no_window_kwargs(),
    )
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / "creating").exists():
            assert leader.poll() is None, "creation leader exited before the intent gate"
            assert time.monotonic() < deadline, "creation leader did not reach the intent gate"
            time.sleep(0.05)
        pending = invoke(expected_exit=3)
        assert pending["creation"]["status"] == "pending"
        assert pending["worktree"]["repo"] == "fixture"
        assert not Path(pending["worktree"]["path"]).exists()
    finally:
        (tmp_path / "release").write_text("continue")
        try:
            stdout, stderr = leader.communicate(timeout=40)
        except subprocess.TimeoutExpired:
            leader.kill()
            leader.communicate(timeout=5)
            raise
    assert leader.returncode == 0, stderr
    first, replay = json.loads(stdout), invoke()
    assert first["worktree"]["id"] == pending["worktree"]["id"]
    assert first["worktree"] == replay["worktree"]
    assert replay["creation"]["replayed"] is True
    assert len(list((tmp_path / "worktrees").iterdir())) == 1
    assert len(list((tmp_path / "state" / "worktrees").glob("*.yaml"))) == 1
    assert len(git("for-each-ref", "--format=%(refname)", "refs/heads/worktree").splitlines()) == 1
    record = tracking.load_record(
        tmp_path / "state" / "worktrees" / f"{first['worktree']['id']}.yaml",
    )
    assert record.repo == "fixture"
    assert record.sessions == []
