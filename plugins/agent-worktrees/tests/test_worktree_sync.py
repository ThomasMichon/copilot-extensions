"""Paired Sync advances both checkouts before changing their plugin setup."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg, git_ops, knowledge_plugins, local_cache_refresh
from agent_worktrees import state_root, tracking, worktree_ops_cli, worktree_sync
from test_squash_abort import _git, _make_repo


def _head(path: Path) -> str:
    # These private fixtures use loose refs; assertions need no extra Git child.
    directory = path / ".git"
    head = (directory / "HEAD").read_text().strip()
    if head.startswith("ref: "):
        return (directory / Path(*head[5:].split("/"))).read_text().strip()
    return head


def _pair(tmp_path, monkeypatch):
    configs, records, paths, targets = {}, {}, {}, {}
    template = _make_repo(tmp_path)
    for role in ("harness", "knowledge"):
        parent = tmp_path / role
        parent.mkdir()
        path = parent / "repo"
        shutil.copytree(template, path)
        old = _head(path)
        (path / "f.txt").write_text("new\n")
        _git(path, "add", "f.txt")
        _git(path, "commit", "-qm", "advance base")
        targets[role] = _head(path)
        _git(path, "update-ref", "refs/remotes/origin/base", targets[role])
        _git(path, "remote", "add", "origin", str(path))
        _git(path, "checkout", "-qb", f"worktree/{role}", old)
        paths[role] = path
        records[role] = tracking.WorktreeRecord(
            worktree_id=role, branch=f"worktree/{role}", worktree_path=str(path),
            repo=role, machine="test", platform=cfg.detect_platform(),
            started_at="2026-01-01T00:00:00", last_resumed_at=None, resume_count=0,
            title=None, status="active", completed_at=None, pair_id="pair",
            pair_role=role, pair_kind="worktree",
            pair_ref=tracking.format_claim_ref(
                "test", "knowledge" if role == "harness" else "harness",
                "knowledge" if role == "harness" else "harness",
            ),
        )
        configs[role] = cfg.Config(
            srcroot=str(tmp_path), machine="test", platform=cfg.detect_platform(),
            repo_name=role, knowledge_repo="knowledge",
            repos={role: cfg.RepoConfig(
                anchor=str(path), worktree_root=str(parent), remote="origin", default_branch="base",
            )},
        )
        directory = tmp_path / "tracking" / role / "worktrees"
        directory.mkdir(parents=True)
        tracking.save_record(records[role], directory / f"{role}.yaml")
    pair = state_root.StatePair(
        paired=True, pair_id="pair", pair_kind="worktree",
        current=state_root.PairCheckout("harness", str(paths["harness"]), "harness", "harness"),
        sibling=state_root.PairCheckout("knowledge", str(paths["knowledge"]), "knowledge", "knowledge"),
    )
    monkeypatch.setattr(cfg, "load_project_config", configs.__getitem__)
    monkeypatch.setattr(cfg, "load_config", lambda: configs["harness"])
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "harness")
    monkeypatch.setattr(cfg, "project_dir",
                        lambda name=None: tmp_path / "tracking" / (name or cfg.active_project()))
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: cfg.project_dir(name) / "worktrees")
    monkeypatch.setattr(state_root, "resolve_pair", lambda *args, **kwargs: pair)
    monkeypatch.setattr("agent_worktrees.execution_spaces.require_record_mutation", lambda *args: None)
    monkeypatch.setattr(worktree_ops_cli, "_build_active_paths", lambda *args: set())
    monkeypatch.setattr(worktree_ops_cli, "_apply_tracking_override", lambda record, info: info)
    monkeypatch.setattr(worktree_sync.sessions, "scan_sessions_fast", lambda *args: None)
    overlay = paths["harness"] / "settings.local.json"
    (paths["harness"] / ".git" / "info" / "exclude").write_text("settings.local.json\n")
    overlay.write_text("existing setup")
    calls = []

    def compose(**kwargs):
        assert cfg.active_project() == "harness"
        assert all(_head(path) == targets[role] for role, path in paths.items())
        calls.append("compose")
        overlay.write_text("updated setup")
        return {"action": "composed", "paired": True}

    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", compose)
    def prepare(*args, **kwargs):
        assert cfg.active_project() == "harness"
        calls.append("instructions")
        return SimpleNamespace(status="ready")

    monkeypatch.setattr(local_cache_refresh, "prepare_for_launch", prepare)
    return records, paths, targets, overlay, calls


def test_sync_advances_both_repos_before_preparing_plugins(tmp_path, monkeypatch, capfd):
    records, paths, targets, overlay, calls = _pair(tmp_path, monkeypatch)
    from agent_worktrees import worktree_identity
    import json

    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda value: value)
    assert worktree_ops_cli.cmd_sync(SimpleNamespace(
        worktree_id="harness", json=True, paired=True,
    )) == 0
    result = json.loads(capfd.readouterr().out)
    assert result["ok"] and result["updated"] and result["plugins"] == "ready"
    assert len(result["pair_results"]) == 2
    assert calls == ["compose", "instructions"]
    assert overlay.read_text() == "updated setup"
    assert all(_head(path) == targets[role] for role, path in paths.items())
    calls.clear()
    result = worktree_sync.sync_pair(records["harness"])
    assert result["ok"] and result["reason"] == "up-to-date"
    assert calls == ["compose", "instructions"]


@pytest.mark.parametrize("deleted", [False, True])
def test_sync_fetches_the_selected_target_despite_a_narrow_refspec(tmp_path, monkeypatch, deleted):
    records, paths, targets, overlay, calls = _pair(tmp_path, monkeypatch)
    heads = {role: _head(path) for role, path in paths.items()}
    for role, path in paths.items():
        _git(path, "config", "remote.origin.fetch",
             "+refs/heads/worktree/*:refs/remotes/origin/worktree/*")
        _git(path, "update-ref", "refs/remotes/origin/base", heads[role])
    if deleted:
        _git(paths["knowledge"], "branch", "-D", "base")
    result = worktree_sync.sync_pair(records["harness"])
    if deleted:
        assert not result["ok"] and result["reason"] == "fetch-failed"
        assert all(_head(path) == heads[role] for role, path in paths.items())
        assert overlay.read_text() == "existing setup" and calls == []
    else:
        assert result["ok"], result
        assert all(_head(path) == targets[role] for role, path in paths.items())
        assert calls == ["compose", "instructions"]


def test_legacy_sync_still_changes_only_the_selected_repo(tmp_path, monkeypatch):
    from agent_worktrees import worktree_identity

    _records, paths, targets, overlay, calls = _pair(tmp_path, monkeypatch)
    original_knowledge = _head(paths["knowledge"])
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda value: value)
    assert worktree_ops_cli.cmd_sync(SimpleNamespace(
        worktree_id="harness", json=True, paired=False,
    )) == 0
    assert _head(paths["harness"]) == targets["harness"]
    assert _head(paths["knowledge"]) == original_knowledge
    assert overlay.read_text() == "existing setup" and calls == []


@pytest.mark.parametrize("missing", ["pair_id", "pair_ref"])
def test_incomplete_pair_cannot_fall_back_to_single_repository_sync(tmp_path, monkeypatch, capfd, missing):
    from agent_worktrees import worktree_identity
    import json
    import yaml

    resolve_pair = state_root.resolve_pair
    records, paths, _targets, overlay, calls = _pair(tmp_path, monkeypatch)
    monkeypatch.setattr(state_root, "resolve_pair", resolve_pair)
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda value: value)
    record_path = records["harness"].yaml_path
    document = yaml.safe_load(record_path.read_text())
    document[missing] = None
    record_path.write_text(yaml.safe_dump(document))
    heads = {role: _head(path) for role, path in paths.items()}
    assert worktree_ops_cli.cmd_sync(SimpleNamespace(
        worktree_id="harness", json=True, paired=True,
    )) != 0
    result = json.loads(capfd.readouterr().out)
    assert not result["ok"] and result["reason"] == "pair-unavailable"
    assert all(_head(path) == heads[role] for role, path in paths.items())
    assert overlay.read_text() == "existing setup" and calls == []


@pytest.mark.parametrize("blocker", ["dirty", "ahead", "fetch", "active", "missing-remote"])
def test_blocked_member_preserves_both_heads_and_plugin_setup(tmp_path, monkeypatch, blocker):
    records, paths, targets, overlay, calls = _pair(tmp_path, monkeypatch)
    if blocker == "dirty":
        (paths["knowledge"] / "f.txt").write_text("local edits\n")
    elif blocker == "ahead":
        (paths["knowledge"] / "local.txt").write_text("local commit\n")
        _git(paths["knowledge"], "add", "local.txt")
        _git(paths["knowledge"], "commit", "-qm", "preserved local work")
    elif blocker == "fetch":
        monkeypatch.setattr(git_ops, "has_remote", lambda *args, **kwargs: True)
        def fetch(*args, **kwargs):
            raise git_ops.GitError(["git", "fetch"], 1, "unreachable")
        monkeypatch.setattr(git_ops, "fetch", fetch)
    elif blocker == "active":
        monkeypatch.setattr(worktree_ops_cli, "_build_active_paths", lambda *args: {
            str(paths["knowledge"]), str(paths["knowledge"]).replace("\\", "/"),
        })
    else:
        _git(paths["knowledge"], "remote", "remove", "origin")
        _git(paths["knowledge"], "update-ref", "refs/remotes/origin/base", targets["knowledge"])
    heads = {role: _head(path) for role, path in paths.items()}
    result = worktree_sync.sync_pair(records["harness"])
    assert not result["ok"] and result["plugins"] == "skipped"
    assert overlay.read_text() == "existing setup" and calls == []
    assert all(_head(path) == heads[role] for role, path in paths.items())


@pytest.mark.parametrize("state", [git_ops.WorktreeState.UNKNOWN, git_ops.WorktreeState.ORPHAN])
def test_tracking_completion_cannot_mask_raw_git_failure(tmp_path, monkeypatch, state):
    from agent_worktrees import worktree_git_facts

    records, paths, _targets, overlay, calls = _pair(tmp_path, monkeypatch)
    records["knowledge"].status = "finalized"
    tracking.save_record(records["knowledge"], records["knowledge"].yaml_path)
    monkeypatch.setattr(git_ops, "_windows_observation", lambda: False)
    monkeypatch.setattr(worktree_ops_cli, "_apply_tracking_override",
                        worktree_git_facts.apply_tracking_override)
    original = git_ops.classify_worktree

    def classify(path, *args, **kwargs):
        if str(path) == str(paths["knowledge"]):
            return git_ops.WorktreeStateInfo(state, current_branch=records["knowledge"].branch)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(git_ops, "classify_worktree", classify)
    heads = {role: _head(path) for role, path in paths.items()}
    result = worktree_sync.sync_pair(records["harness"])
    assert not result["ok"] and state.value in result["detail"]
    assert overlay.read_text() == "existing setup" and calls == []
    assert all(_head(path) == heads[role] for role, path in paths.items())


def test_knowledge_side_sync_resolves_real_pair_in_harness_context(tmp_path, monkeypatch):
    resolve_pair = state_root.resolve_pair
    records, paths, _targets, _overlay, calls = _pair(tmp_path, monkeypatch)
    monkeypatch.setattr(state_root, "resolve_pair", resolve_pair)
    compose = knowledge_plugins.compose_from_pair

    def compose_checked(*, cwd, config):
        pair = knowledge_plugins.resolve_pair(cwd=cwd, config=config)
        assert pair.harness_path == paths["harness"].resolve()
        assert pair.knowledge_path == paths["knowledge"].resolve()
        return compose(cwd=cwd, config=config)

    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", compose_checked)
    cfg.set_active_project("knowledge")
    result = worktree_sync.sync_pair(records["knowledge"])
    assert result["ok"], result
    assert cfg.active_project() == "knowledge"
    assert calls == ["compose", "instructions"]


@pytest.mark.parametrize("policy", ["disabled", "different-knowledge"])
def test_knowledge_side_sync_reloads_fetched_policy_before_writing(tmp_path, monkeypatch, policy):
    from dataclasses import replace
    import yaml

    resolve_pair = knowledge_plugins.resolve_pair
    records, paths, targets, overlay, calls = _pair(tmp_path, monkeypatch)
    load_project = cfg.load_project_config
    original_config = load_project("harness")
    path = paths["harness"]
    old = _head(path)
    _git(path, "checkout", "-q", "base")
    policy_path = path / ".agent-worktrees.yaml"
    policy_path.write_text(yaml.safe_dump({
        "compose_knowledge_plugins": policy != "disabled",
        "knowledge_repo": "other" if policy == "different-knowledge" else "knowledge",
    }))
    _git(path, "add", policy_path.name)
    _git(path, "commit", "-qm", "change incoming composition policy")
    targets["harness"] = _head(path)
    _git(path, "checkout", "-q", records["harness"].branch)
    assert _head(path) == old

    def load(name):
        config = load_project(name)
        if name != "harness" or not policy_path.exists():
            return config
        incoming = yaml.safe_load(policy_path.read_text())
        return replace(
            config, knowledge_repo=incoming["knowledge_repo"],
            repos={"harness": replace(
                config.default_repo, compose_knowledge_plugins=incoming["compose_knowledge_plugins"],
            )},
        )

    monkeypatch.setattr(cfg, "load_project_config",
                        lambda name: cfg.memoize_in_scope(load, name))
    monkeypatch.setattr(knowledge_plugins, "resolve_pair", resolve_pair)

    def compose(*, cwd, config):
        assert not config.default_repo.compose_knowledge_plugins
        calls.append("disabled")
        return {"action": "disabled", "paired": True}

    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", compose)
    cfg.set_active_project("knowledge")
    with cfg.cached_load_config_scope():
        assert cfg.load_project_config("harness") is original_config
        result = worktree_sync.sync_pair(records["knowledge"])
        assert cfg.load_project_config("harness") is not original_config
    assert cfg.active_project() == "knowledge"
    assert all(_head(path) == targets[role] for role, path in paths.items())
    assert overlay.read_text() == "existing setup"
    if policy == "disabled":
        assert result["ok"], result
        assert calls == ["disabled", "instructions"]
    else:
        assert not result["ok"] and result["plugins"] == "failed"
        assert calls == []


def test_late_fast_forward_failure_never_recomposes_plugins(tmp_path, monkeypatch):
    records, paths, _targets, overlay, calls = _pair(tmp_path, monkeypatch)
    original = git_ops.fast_forward_worktree
    def advance(path, **kwargs):
        if str(path) == str(paths["knowledge"]):
            return git_ops.FastForwardResult(False, "ff-failed")
        return original(path, **kwargs)
    monkeypatch.setattr(git_ops, "fast_forward_worktree", advance)
    result = worktree_sync.sync_pair(records["harness"])
    assert not result["ok"] and result["pair_results"][0]["updated"]
    assert overlay.read_text() == "existing setup" and calls == []


@pytest.mark.parametrize("role,target", [
    ("harness", "default_branch"), ("knowledge", "default_branch"), ("knowledge", "remote"),
])
def test_incoming_sync_target_change_requires_retry_before_plugin_writes(tmp_path, monkeypatch, role, target):
    from dataclasses import replace

    records, paths, targets, overlay, calls = _pair(tmp_path, monkeypatch)
    load_project = cfg.load_project_config
    path = paths[role]
    _git(path, "checkout", "-q", "base")
    (path / "sync-target.txt").write_text("new-target\n")
    _git(path, "add", "sync-target.txt")
    _git(path, "commit", "-qm", "select a new synchronization target")
    targets[role] = _head(path)
    _git(path, "checkout", "-q", records[role].branch)

    def load(name):
        config = load_project(name)
        if name == role and (path / "sync-target.txt").exists():
            return replace(config, repos={name: replace(config.default_repo, **{target: "new-target"})})
        return config

    monkeypatch.setattr(cfg, "load_project_config", lambda name: cfg.memoize_in_scope(load, name))
    with cfg.cached_load_config_scope():
        for name in paths:
            cfg.load_project_config(name)
        result = worktree_sync.sync_pair(records["knowledge"])
    assert not result["ok"] and result["reason"] == "pair-sync-policy-changed"
    assert "retry Sync" in result["detail"]
    assert all(_head(path) == targets[name] for name, path in paths.items())
    assert len(result["pair_results"]) == 2
    assert overlay.read_text() == "existing setup" and calls == []


def test_pair_sync_cannot_race_a_member_launch(tmp_path, monkeypatch):
    from agent_worktrees import launch_admission

    records, paths, _targets, overlay, calls = _pair(tmp_path, monkeypatch)
    heads = {role: _head(path) for role, path in paths.items()}
    with launch_admission.guard(records["knowledge"].yaml_path):
        result = worktree_sync.sync_pair(records["harness"])
    assert not result["ok"] and "admission is busy" in result["detail"]
    assert overlay.read_text() == "existing setup" and calls == []
    assert all(_head(path) == heads[role] for role, path in paths.items())


@pytest.mark.parametrize("failure", ["composition", "instructions"])
def test_local_preparation_failure_is_not_reported_as_success_or_skipped(tmp_path, monkeypatch, failure):
    records, paths, targets, _overlay, calls = _pair(tmp_path, monkeypatch)
    if failure == "composition":
        monkeypatch.setattr(knowledge_plugins, "compose_from_pair", lambda **kwargs: {
            "paired": False, "action": "no-op", "pair_error": "pair changed",
        })
    else:
        monkeypatch.setattr(local_cache_refresh, "prepare_for_launch",
                            lambda *args, **kwargs: SimpleNamespace(status="failed", detail="copy refused"))
    result = worktree_sync.sync_pair(records["harness"])
    assert not result["ok"] and result["plugins"] == "failed"
    assert all(_head(path) == targets[role] for role, path in paths.items())
    assert calls == ([] if failure == "composition" else ["compose"])


@pytest.mark.parametrize("blocked", [False, True])
def test_streamed_sync_retains_complete_pair_result_and_reports_before_fetch(
    tmp_path, monkeypatch, capfd, blocked,
):
    from agent_worktrees import worktree_identity

    _records, paths, targets, overlay, calls = _pair(tmp_path, monkeypatch)
    if blocked:
        monkeypatch.setattr(local_cache_refresh, "prepare_for_launch",
                            lambda *args, **kwargs: SimpleNamespace(
                                status="failed", detail="instruction copy refused",
                            ))
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda value: value)
    fetch = git_ops.fetch
    before_fetch = []

    def observe_fetch(*args, **kwargs):
        before_fetch.extend(json.loads(line) for line in capfd.readouterr().out.splitlines())
        assert any(frame.get("type") == "progress" for frame in before_fetch)
        assert not any(frame.get("type") in {"done", "error"} for frame in before_fetch)
        return fetch(*args, **kwargs)

    monkeypatch.setattr(git_ops, "fetch", observe_fetch)
    result_code = worktree_ops_cli.cmd_sync(SimpleNamespace(
        worktree_id="harness", json=True, paired=True, stream=True,
    ))
    frames = before_fetch + [json.loads(line) for line in capfd.readouterr().out.splitlines()]
    terminal = frames[-1]
    assert terminal["type"] == ("error" if blocked else "done")
    assert result_code == (1 if blocked else 0)
    result = terminal["result"]
    assert result["ok"] is not blocked
    assert result["updated"] is not blocked
    assert len(result["pair_results"]) == 2
    assert all(member["updated"] for member in result["pair_results"])
    assert result["plugins"] == ("failed" if blocked else "ready")
    if blocked:
        assert result["reason"] == "plugin-preparation-failed"
        assert result["detail"] == "instruction copy refused"
    assert all(_head(path) == targets[role] for role, path in paths.items())
    assert overlay.read_text() == "updated setup"
    assert calls == (["compose"] if blocked else ["compose", "instructions"])


def test_sync_stream_requires_json_before_loading_configuration(monkeypatch, capfd):
    def unexpected():
        pytest.fail("invalid progress syntax must fail before loading configuration")

    monkeypatch.setattr(cfg, "load_config", unexpected)
    assert worktree_ops_cli.cmd_sync(SimpleNamespace(stream=True, json=False)) == 2
    output = capfd.readouterr()
    assert output.out == "" and "--stream requires --json" in output.err


def test_sync_stream_parser_remains_strict():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    worktree_ops_cli.add_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["sync", "--stream", "--json", "--worktree-id", "example"])
    assert args.stream and args.json and args.worktree_id == "example"
    for option in ("--streaming", "--str"):
        with pytest.raises(SystemExit) as failure:
            parser.parse_args(["sync", option, "--json"])
        assert failure.value.code == 2


def test_sync_stream_matches_the_complete_baseline_json_envelope(tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(cfg, "load_config", lambda: SimpleNamespace(default_repo=object()))
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    assert worktree_ops_cli.cmd_sync(SimpleNamespace(
        stream=False, json=True, worktree_id="missing",
    )) == 1
    baseline = json.loads(capfd.readouterr().out)
    assert worktree_ops_cli.cmd_sync(SimpleNamespace(
        stream=True, json=True, worktree_id="missing",
    )) == 1
    frames = [json.loads(line) for line in capfd.readouterr().out.splitlines()]
    assert frames[-1]["type"] == "error"
    assert frames[-1]["result"] == baseline


def test_sync_stream_malformed_tracking_has_a_terminal_result(tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(cfg, "load_config", lambda: SimpleNamespace(default_repo=object()))
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    (tmp_path / "selected.yaml").write_text("[]")
    assert worktree_ops_cli.cmd_sync(SimpleNamespace(
        stream=True, json=True, worktree_id="selected",
    )) == 1
    frames = [json.loads(line) for line in capfd.readouterr().out.splitlines()]
    assert frames[-1]["type"] == "error"
    assert frames[-1]["result"]["ok"] is False
    assert "YAML mapping" in frames[-1]["result"]["detail"]


def test_sync_stream_does_not_retry_a_failed_terminal_write(tmp_path, monkeypatch):
    import io
    import sys

    class Output(io.StringIO):
        terminal_attempts = 0

        def write(self, text):
            if '"type": "error"' in text:
                self.terminal_attempts += 1
                raise OSError("fixture stdout write is unconfirmed")
            return super().write(text)

    output = Output()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "__stdout__", output)
    monkeypatch.setattr(cfg, "load_config", lambda: SimpleNamespace(default_repo=object()))
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    with pytest.raises(OSError, match="stdout write is unconfirmed"):
        worktree_ops_cli.cmd_sync(SimpleNamespace(
            stream=True, json=True, worktree_id="missing",
        ))
    assert output.terminal_attempts == 1


def test_sync_stream_shutdown_flush_never_replays_a_failed_frame(monkeypatch):
    import io
    import sys

    from agent_worktrees import output, sync_progress

    class Raw(io.RawIOBase):
        failing = True

        def __init__(self):
            super().__init__()
            self.attempts = []

        def writable(self):
            return True

        def write(self, data):
            self.attempts.append(bytes(data))
            if self.failing:
                raise OSError("fixture buffered flush failed")
            return len(data)

    raw = Raw()
    stream = io.TextIOWrapper(io.BufferedWriter(raw), encoding="utf-8")
    native_attempts = []
    native_write = output.os.write

    def fail_native_stdout(fd, payload):
        if fd == 1:
            native_attempts.append(payload)
            raise OSError("fixture unbuffered write failed")
        return native_write(fd, payload)

    frame = {"type": "error", "message": "fixture refusal"}
    try:
        with monkeypatch.context() as patch:
            patch.setattr(sys, "stdout", stream)
            patch.setattr(sys, "__stdout__", stream)
            patch.setattr(output.os, "write", fail_native_stdout)
            progress = sync_progress.SyncProgress()
            with pytest.raises((OSError, SystemExit)):
                progress._write(frame)
            raw.failing = False
            stream.flush()
            assert raw.attempts == []
            with pytest.raises((OSError, SystemExit)):
                progress.stage("must not write after failure")
            assert len(native_attempts) == 1
            assert json.loads(native_attempts[0]) == frame
    finally:
        raw.failing = False
        stream.close()


@pytest.mark.parametrize("selector", ["tail", "../invalid"])
def test_streamed_sync_selector_refusal_is_a_terminal_result(tmp_path, monkeypatch, capfd, selector):
    monkeypatch.setattr(cfg, "load_config", lambda: SimpleNamespace(default_repo=object()))
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    for name in ("first-tail", "second-tail"):
        (tmp_path / f"{name}.yaml").write_text("{}")
    assert worktree_ops_cli.cmd_sync(SimpleNamespace(
        stream=True, json=True, worktree_id=selector,
    )) == 1
    frames = [json.loads(line) for line in capfd.readouterr().out.splitlines()]
    assert frames[-1]["type"] == "error"
    result = frames[-1]["result"]
    assert result["worktree_id"] == selector and result["ok"] is False
    assert result["reason"] == "worktree-id-rejected"
    assert ("Ambiguous short ID" if selector == "tail" else "Invalid worktree ID") in result["detail"]
    assert result["outcome"] == "not-started"


def test_streamed_sync_batch_exception_retains_completed_results(tmp_path, monkeypatch, capfd):
    records = []
    for name in ("first", "interrupted", "unstarted"):
        path = tmp_path / name
        path.mkdir()
        records.append(SimpleNamespace(
            worktree_id=name, repo="example", kind="session", worktree_path=str(path),
            pair_id=None, pair_ref=None,
        ))
    monkeypatch.setattr(cfg, "load_config", lambda: SimpleNamespace(
        default_repo=SimpleNamespace(remote="origin", anchor=str(tmp_path)),
    ))
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(tracking, "list_records", lambda *args, **kwargs: records)
    monkeypatch.setattr(git_ops, "has_remote", lambda *args, **kwargs: False)
    monkeypatch.setattr(worktree_sync.sessions, "scan_sessions_fast", lambda *args: None)
    monkeypatch.setattr(worktree_ops_cli, "_build_active_paths", lambda *args: set())
    completed = {"worktree_id": "first", "updated": True, "reason": "updated", "behind": 1}
    calls = []

    def synchronize(record, *_args):
        calls.append(record.worktree_id)
        if record.worktree_id != "first":
            raise OSError("fixture checkout disappeared")
        return completed

    monkeypatch.setattr(worktree_ops_cli._core(), "_sync_one_record", synchronize)
    assert worktree_ops_cli.cmd_sync(SimpleNamespace(
        stream=True, json=True, worktree_id=None,
    )) == 1
    frames = [json.loads(line) for line in capfd.readouterr().out.splitlines()]
    assert frames[-1]["type"] == "error"
    results = frames[-1]["result"]["results"]
    assert results[0] == completed
    assert results[1] == {
        "worktree_id": "interrupted", "ok": False, "reason": "sync-interrupted",
        "detail": "fixture checkout disappeared", "outcome": "unknown",
    }
    assert calls == ["first", "interrupted"]


def test_sync_stream_waiting_frames_stop_before_the_terminal(monkeypatch):
    import io
    import sys
    import threading

    from agent_worktrees import sync_progress

    received = threading.Event()

    class Output(io.StringIO):
        def write(self, text):
            written = super().write(text)
            if "Waiting: fixture fetch" in text:
                received.set()
            return written

    output = Output()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "__stdout__", output)
    monkeypatch.setattr(sync_progress, "HEARTBEAT_SECONDS", 0.01)
    with sync_progress.SyncProgress() as progress:
        progress.stage("fixture fetch")
        try:
            assert received.wait(2), "the operation owner must report its current wait"
        finally:
            progress.finish({"updated": False, "reason": "up-to-date"})
    frames = [json.loads(line) for line in output.getvalue().splitlines()]
    assert any(frame.get("msg") == "Waiting: fixture fetch" for frame in frames)
    assert frames[-1]["type"] == "done"
    assert progress._thread is not None and not progress._thread.is_alive()


def test_sync_stream_real_parser_process_retains_pair_result(tmp_path):
    import sys
    import textwrap

    from agent_procutil.owned_process import run_caller_owned

    script = textwrap.dedent("""
        import argparse
        import sys
        from pathlib import Path
        import pytest
        from test_worktree_sync import _pair
        from agent_worktrees import worktree_identity, worktree_ops_cli
        parser = argparse.ArgumentParser(allow_abbrev=False)
        worktree_ops_cli.add_parsers(parser.add_subparsers(dest="command"))
        args = parser.parse_args(sys.argv[2:])
        with pytest.MonkeyPatch.context() as patch:
            _pair(Path(sys.argv[1]), patch)
            patch.setattr(worktree_identity, "_resolve_worktree_id", lambda value: value)
            code = worktree_ops_cli.cmd_sync(args)
        raise SystemExit(code)
    """)
    result = run_caller_owned(
        [sys.executable, "-c", script, str(tmp_path),
         "sync", "--stream", "--json", "--paired", "--worktree-id", "harness"],
        cwd=str(Path(__file__).parent), timeout=30,
        max_stdout_bytes=128 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert result.returncode == 0, result.stderr
    frames = [json.loads(line) for line in result.stdout.splitlines()]
    assert frames[0]["type"] == "progress"
    terminal = frames[-1]
    assert terminal["type"] == "done"
    assert terminal["result"]["ok"] and terminal["result"]["plugins"] == "ready"
    assert len(terminal["result"]["pair_results"]) == 2
