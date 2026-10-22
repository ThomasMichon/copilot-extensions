"""Tests for the ``session_register`` verb (agent-worktrees-authoritative-
daemon effort, Phase 3's seventh migrated call-site cluster -- the first
taken from the sessionStart-hook-critical path).

The extensive existing ``tracking.register_session`` test suites
(``test_register_session.py``, ``test_session_lifecycle.py``,
``test_tracking.py``, and others -- 700+ tests total) already exercise
every branch of this transaction end-to-end through the direct in-process
fallback path (the same code the verb now wraps), so this file does not
re-duplicate that coverage. It covers only what is new here: the verb's
own registration, one end-to-end proof that ``tracking_write.dispatch``
reaches it via a real, live daemon, and -- the operative concern for this
specific cluster -- that a cold/unreachable daemon never adds latency to
session registration, since ``register_session`` passes ``boot_wait_s=0``
precisely so a sessionStart hook is never made to wait on a daemon boot.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import (
    config as cfg,
    config_authority,
    tracking,
    tracking_session_registration_write,
    tracking_write,
)
from agent_worktrees.tracking import WorktreeRecord, save_record


@pytest.fixture(autouse=True)
def _clean_verb_registry():
    before_verbs = dict(tracking_write._VERBS)
    yield
    tracking_write._VERBS.clear()
    tracking_write._VERBS.update(before_verbs)


@pytest.fixture
def record_path(tmp_tracking_dir: Path, monkeypatch) -> Path:
    config = cfg.Config(
        srcroot=str(tmp_tracking_dir), machine="test", platform="wsl", repo_name="test-repo",
        repos={"test-repo": cfg.RepoConfig(
            anchor=str(tmp_tracking_dir), worktree_root=str(tmp_tracking_dir / "trees"),
        )},
    )
    monkeypatch.setattr(
        config_authority, "load_project_authority_config",
        lambda name, **_kwargs: config,
    )
    monkeypatch.setattr(cfg, "load_project_config", lambda name, **_kwargs: config)
    path = tmp_tracking_dir / "wt-reg.yaml"
    rec = WorktreeRecord(
        worktree_id="wt-reg", branch="worktree/wt-reg",
        worktree_path=str(tmp_tracking_dir / "trees" / "wt-reg"),
        repo="test-repo", machine="test", platform="wsl",
        started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
        resume_count=0, title=None, status="active", completed_at=None, sessions=[],
    )
    save_record(rec, path)
    return path


def test_importing_the_module_registers_the_verb():
    assert "session_register" in tracking_write.registered_verbs()


def test_apply_session_register_creates_a_new_session_entry(record_path):
    result = tracking_session_registration_write.apply_session_register(
        {
            "worktree_id": "wt-reg",
            "yaml_path": str(record_path),
            "session_id": "sess-1",
        }
    )
    assert result == {"ok": True, "linked_handoff": None}
    record = tracking.load_record(record_path)
    assert record.session_entry("sess-1") is not None


def test_source_bound_bind_is_idempotent_but_new_source_opens_interval(record_path):
    args = {
        "worktree_id": "wt-reg", "yaml_path": str(record_path),
        "session_id": "sess-1", "source": "bind", "started_at": "first",
        "source_identity": "native:first",
    }
    assert tracking_session_registration_write.apply_session_register(args)["ok"]
    assert tracking_session_registration_write.apply_session_register({
        **args, "started_at": "repeat",
    })["ok"]
    entry = tracking.load_record(record_path).session_entry("sess-1")
    assert len(entry.activations) == 1
    assert entry.activations[0].source_identity == "native:first"

    assert tracking_session_registration_write.apply_session_register({
        **args, "source_identity": "native:replacement",
    })["ok"]
    entry = tracking.load_record(record_path).session_entry("sess-1")
    assert len(entry.activations) == 2
    assert entry.activations[0].end_source == "inferred:next-start"
    assert entry.activations[1].source_identity == "native:replacement"

    before = record_path.read_bytes()
    result = tracking_session_registration_write.apply_session_register(args)
    assert result["error"] == "lifecycle"
    assert record_path.read_bytes() == before
    result = tracking_session_registration_write.apply_session_register({
        **args, "source_identity": None,
    })
    assert result["error"] == "lifecycle"
    assert record_path.read_bytes() == before


def test_source_registration_reactivates_claim_after_old_end(record_path):
    from agent_worktrees import obligations, tracking_session_deregistration_write

    args = {
        "worktree_id": "wt-reg", "yaml_path": str(record_path),
        "session_id": "sess-1", "source": "hook", "started_at": "first",
        "source_identity": "native:first",
    }
    assert tracking_session_registration_write.apply_session_register(args)["ok"]
    result = tracking_session_deregistration_write.apply_session_deregister({
        **args, "ended_at": "exit", "end_reason": "user_exit",
    })
    assert result["lifecycle"] == "ended"
    record = tracking.load_record(record_path)
    ref = tracking.format_claim_ref(
        record.machine, record.repo, record.worktree_id, session="sess-1",
    )
    assert next(item for item in record.resources if item.ref == ref).state == obligations.RELEASED
    assert tracking_session_registration_write.apply_session_register({
        **args, "started_at": "replacement", "source_identity": "native:replacement",
    })["ok"]
    record = tracking.load_record(record_path)
    assert record.session_entry("sess-1").ended_at is None
    assert next(item for item in record.resources if item.ref == ref).state == obligations.ACTIVE


def test_delayed_duplicate_start_does_not_reactivate_ended_claim(record_path):
    from agent_worktrees import obligations, tracking_session_deregistration_write

    args = {
        "worktree_id": "wt-reg", "yaml_path": str(record_path), "session_id": "sess-1",
        "source": "hook:new", "started_at": "first", "source_identity": "native:first",
    }
    assert tracking_session_registration_write.apply_session_register(args)["ok"]
    assert tracking_session_deregistration_write.apply_session_deregister({
        **args, "ended_at": "exit", "end_reason": "user_exit",
    })["lifecycle"] == "ended"
    before = record_path.read_bytes()
    assert tracking_session_registration_write.apply_session_register(args)["lifecycle"] == "duplicate"
    assert record_path.read_bytes() == before
    record = tracking.load_record(record_path)
    assert all(item.state == obligations.RELEASED for item in record.resources)


def test_reused_physical_source_ambiguity_survives_history_pruning(record_path, monkeypatch):
    from agent_worktrees import tracking_session_deregistration_write

    monkeypatch.setattr(tracking, "_MAX_SESSION_ACTIVATIONS", 1)
    args = {
        "worktree_id": "wt-reg", "yaml_path": str(record_path),
        "session_id": "sess-1", "source": "hook:resume", "started_at": "first",
        "source_identity": "native:physical",
    }
    assert tracking_session_registration_write.apply_session_register(args)["ok"]
    assert tracking_session_registration_write.apply_session_register({
        **args, "started_at": "second",
    })["ok"]
    entry = tracking.load_record(record_path).session_entry("sess-1")
    assert len(entry.activations) == 1
    assert entry.activations[0].ordinal == 2
    assert entry.activations[0].source_ambiguous is True
    before = record_path.read_bytes()
    result = tracking_session_deregistration_write.apply_session_deregister({
        **args, "end_reason": "user_exit",
    })
    assert result["lifecycle"] == "unconfirmed"
    assert record_path.read_bytes() == before


@pytest.mark.parametrize("timestamp", [None, "not-a-timestamp", True])
def test_uncertain_start_replay_cannot_reacquire_released_claim(record_path, timestamp):
    from agent_worktrees import __main__ as main, obligations, tracking_session_deregistration_write

    args = {
        "worktree_id": "wt-reg", "yaml_path": str(record_path), "session_id": "sess-1",
        "source": "hook:new", "started_at": main._hook_event_timestamp({"timestamp": timestamp}),
        "source_identity": "native:original",
    }
    assert args["started_at"] is None
    assert tracking_session_registration_write.apply_session_register(args)["ok"]
    assert tracking_session_deregistration_write.apply_session_deregister({
        **args, "ended_at": "2026-10-19T10:00:00", "end_reason": "user_exit",
    })["lifecycle"] == "ended"
    before = record_path.read_bytes()
    replay = tracking_session_registration_write.apply_session_register(args)
    assert replay.get("error") == "lifecycle"
    assert "timestamp" in replay["message"]
    assert record_path.read_bytes() == before
    record = tracking.load_record(record_path)
    assert len(record.sessions[0].activations) == 1
    assert record.sessions[0].ended_at == "2026-10-19T10:00:00"
    assert all(claim.state == obligations.RELEASED for claim in record.resources)


def test_timestamped_same_source_logical_resume_remains_explicitly_ambiguous(record_path):
    from agent_worktrees import obligations, tracking_session_deregistration_write

    args = {
        "worktree_id": "wt-reg", "yaml_path": str(record_path), "session_id": "sess-1",
        "source": "hook:resume", "started_at": "2026-10-19T10:00:00",
        "source_identity": "native:physical",
    }
    assert tracking_session_registration_write.apply_session_register(args)["ok"]
    assert tracking_session_deregistration_write.apply_session_deregister({
        **args, "ended_at": "2026-10-19T11:00:00", "end_reason": "user_exit",
    })["lifecycle"] == "ended"
    assert tracking_session_registration_write.apply_session_register({
        **args, "started_at": "2026-10-19T12:00:00",
    })["ok"]
    record = tracking.load_record(record_path)
    assert record.sessions[0].ended_at is None
    assert all(item.source_ambiguous for item in record.sessions[0].activations)
    assert all(claim.state == obligations.ACTIVE for claim in record.resources)


def test_source_changing_linked_handoff_advances_revision_and_survives_stale_save(record_path):
    assert tracking_session_registration_write.apply_session_register({
        "worktree_id": "wt-reg", "yaml_path": str(record_path),
        "session_id": "old", "source_identity": "native:old",
    })["ok"]
    record = tracking.load_record(record_path)
    tracking.open_handoff(record, "old", "token", save=False)
    tracking.save_record(record, record_path)
    args = {
        "worktree_id": "wt-reg", "yaml_path": str(record_path), "session_id": "sess-1",
        "source": "bind", "handoff_token": "token", "source_identity": "native:first",
    }
    assert tracking_session_registration_write.apply_session_register(args)["linked_handoff"] is not None
    stale = tracking.load_record(record_path)
    result = tracking_session_registration_write.apply_session_register({
        **args, "source_identity": "native:replacement",
    })
    assert result["ok"] and result["linked_handoff"] is None
    current = tracking.load_record(record_path)
    assert current.lifecycle_revision > stale.lifecycle_revision
    assert current.sessions[-1].activations[-1].source_identity == "native:replacement"
    tracking.save_record(stale, record_path)
    retained = tracking.load_record(record_path)
    assert retained.sessions[-1].activations[-1].source_identity == "native:replacement"
    assert tracking_session_registration_write.apply_session_register({
        **args, "source_identity": "native:replacement",
    })["ok"]
    assert tracking.load_record(record_path).lifecycle_revision == retained.lifecycle_revision


def test_pruned_physical_source_cannot_regain_unambiguous_end_authority(record_path, monkeypatch):
    from agent_worktrees import obligations, tracking_session_deregistration_write

    monkeypatch.setattr(tracking, "_MAX_SESSION_ACTIVATIONS", 2)
    args = {
        "worktree_id": "wt-reg", "yaml_path": str(record_path),
        "session_id": "sess-1", "source": "hook:resume",
    }
    for source, timestamp in (
        ("native:A", "2026-10-19T10:00:00"), ("native:A", "2026-10-19T11:00:00"),
        ("native:B", "2026-10-19T12:00:00"), ("native:B", "2026-10-19T13:00:00"),
    ):
        assert tracking_session_registration_write.apply_session_register({
            **args, "source_identity": source, "started_at": timestamp,
        })["ok"]
    record = tracking.load_record(record_path)
    assert len(record.sessions[0].activations) == 2
    assert all(item.source_identity == "native:B" for item in record.sessions[0].activations)
    replay = tracking_session_registration_write.apply_session_register({
        **args, "source_identity": "native:A", "started_at": "2026-10-19T14:00:00",
        "source": "bind",
    })
    assert replay.get("ok") or replay.get("error") == "lifecycle"
    stops = []
    monkeypatch.setattr(tracking_session_deregistration_write, "stop_fsmonitor_daemon", stops.append)
    old_end = tracking_session_deregistration_write.apply_session_deregister({
        **args, "source_identity": "native:A", "ended_at": "old-A-exit",
        "end_reason": "user_exit",
    })
    assert old_end["lifecycle"] == "unconfirmed"
    current = tracking.load_record(record_path)
    assert current.sessions[0].ended_at is None
    assert current.sessions[0].activations[-1].ended_at is None
    assert all(claim.state == obligations.ACTIVE for claim in current.resources)
    assert stops == []


@pytest.mark.parametrize("equal_revision", [False, True])
def test_pruning_fence_roundtrips_and_survives_stale_save(
    record_path, monkeypatch, equal_revision,
):
    monkeypatch.setattr(tracking, "_MAX_SESSION_ACTIVATIONS", 1)
    args = {
        "worktree_id": "wt-reg", "yaml_path": str(record_path),
        "session_id": "sess-1", "source": "bind", "source_identity": "native:A",
    }
    assert tracking_session_registration_write.apply_session_register(args)["ok"]
    stale = tracking.load_record(record_path)
    assert tracking_session_registration_write.apply_session_register({
        **args, "source_identity": "native:B",
    })["ok"]
    current = tracking.load_record(record_path)
    assert current.sessions[0].pruned_sources == ["native:A"]
    if equal_revision:
        stale.lifecycle_revision = current.lifecycle_revision
    tracking.save_record(stale, record_path)
    saved = tracking.load_record(record_path)
    assert "native:A" in saved.sessions[0].pruned_sources
    if equal_revision:
        assert "native:B" in saved.sessions[0].pruned_sources
    from agent_worktrees.tracking_session_activations import source_end_authoritative
    assert not source_end_authoritative(saved.sessions[0], "native:A")


def test_pruning_source_budget_saturates_stickily_and_refuses_before_claim(
    record_path, monkeypatch,
):
    from agent_worktrees import tracking_session_activations as history
    from agent_worktrees import tracking_session_deregistration_write

    monkeypatch.setattr(tracking, "_MAX_SESSION_ACTIVATIONS", 1)
    monkeypatch.setattr(history, "MAX_PRUNED_SOURCES", 2)
    args = {
        "worktree_id": "wt-reg", "yaml_path": str(record_path),
        "session_id": "sess-1", "source": "bind",
    }
    for source in ("A", "B", "C", "D"):
        assert tracking_session_registration_write.apply_session_register({
            **args, "source_identity": source,
        })["ok"]
    current = tracking.load_record(record_path)
    assert len(current.sessions[0].pruned_sources) == 2
    assert current.sessions[0].source_history_saturated is True
    stale = tracking.load_record(record_path)
    stale.sessions[0].source_history_saturated = False
    stale.sessions[0].pruned_sources = []
    tracking.save_record(stale, record_path)
    assert tracking.load_record(record_path).sessions[0].source_history_saturated is True
    before = record_path.read_bytes()
    for source in ("A", "D", "E", None):
        assert tracking_session_registration_write.apply_session_register({
            **args, "source_identity": source,
        })["error"] == "lifecycle"
        assert record_path.read_bytes() == before
        assert tracking_session_deregistration_write.apply_session_deregister({
            **args, "source_identity": source, "end_reason": "user_exit",
        })["lifecycle"] == "unconfirmed"
        assert record_path.read_bytes() == before


@pytest.mark.parametrize("metadata", [
    {"pruned_sources": "source"}, {"pruned_sources": ["source", "source"]},
    {"pruned_sources": [None]}, {"pruned_sources": [True]},
    {"source_history_saturated": 1}, {"source_history_saturated": "true"},
])
def test_invalid_pruning_fence_is_rejected(metadata):
    from agent_worktrees.tracking_session_activations import parse_pruning_fence
    with pytest.raises(ValueError):
        parse_pruning_fence(metadata)


@pytest.mark.parametrize("source_identity", [None, "native:replacement"])
def test_v2_daemon_cannot_downgrade_registration(
    record_path, monkeypatch, monkeypatch_config, source_identity,
):
    from agent_worktrees import locks, status_monitor_runtime

    assert tracking_session_registration_write.apply_session_register({
        "worktree_id": "wt-reg", "yaml_path": str(record_path),
        "session_id": "sess-1", "source_identity": "native:original",
    })["ok"]
    requests = []
    server = tracking_write.start_server(
        lambda kind, payload: requests.append(payload) or {"ok": True},
    )
    server.start()
    try:
        fields = tracking_write.rendezvous_fields(server)
        fields["tracking_write_verb_versions"]["session_register"] = 2
        monkeypatch.setattr(locks, "read_lock", lambda path: fields)
        monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)
        if source_identity is None:
            before = record_path.read_bytes()
            with pytest.raises(tracking.SessionLifecycleError, match="source"):
                tracking.register_session("wt-reg", "sess-1")
            assert record_path.read_bytes() == before
        else:
            tracking.register_session("wt-reg", "sess-1", source_identity=source_identity)
            entry = tracking.load_record(record_path).session_entry("sess-1")
            assert entry.activations[-1].source_identity == source_identity
        assert requests == []
    finally:
        server.close()


def test_apply_session_register_new_session_branch_feeds_claim_history(
    record_path,
):
    """worktree-claims-transitive-finalization Phase 3b: linking a
    handoff_token for a session NOT yet tracked (the "new session" branch)
    must feed claim_history for any ACTIVE pr-kind claim the worktree
    holds, after -- never before -- the verb's own durable save."""
    from agent_worktrees import claim_history, obligations

    record = tracking.load_record(record_path)
    record.sessions.append(tracking.SessionEntry("old", "2026-06-01T10:00:00"))
    record.resources = [
        tracking.ResourceClaim(kind="pr", ref="o/r#1", state=obligations.ACTIVE),
    ]
    tracking.open_handoff(record, "old", "tok", save=False)
    tracking.save_record(record, record_path)

    result = tracking_session_registration_write.apply_session_register(
        {
            "worktree_id": "wt-reg",
            "yaml_path": str(record_path),
            "session_id": "new",
            "handoff_token": "tok",
        }
    )
    assert result["ok"] is True
    events = claim_history.history_for_ref("o/r#1")
    assert [e["event"] for e in events] == ["reassigned"]
    assert events[0]["session_id"] == "new"


def test_apply_session_register_existing_session_branch_feeds_claim_history(
    record_path,
):
    """Same Phase 3b wiring, for the "existing session" branch (the
    successor is already tracked, e.g. a pre-associated handoff
    candidate) -- the loop branch in apply_session_register, distinct from
    the new-SessionEntry branch covered above."""
    from agent_worktrees import claim_history, obligations

    record = tracking.load_record(record_path)
    record.sessions.append(tracking.SessionEntry("old", "2026-06-01T10:00:00"))
    record.sessions.append(tracking.SessionEntry("new", "2026-06-01T10:00:00"))
    record.resources = [
        tracking.ResourceClaim(kind="pr", ref="o/r#1", state=obligations.ACTIVE),
    ]
    tracking.open_handoff(record, "old", "tok", save=False)
    tracking.save_record(record, record_path)

    result = tracking_session_registration_write.apply_session_register(
        {
            "worktree_id": "wt-reg",
            "yaml_path": str(record_path),
            "session_id": "new",
            "handoff_token": "tok",
        }
    )
    assert result["ok"] is True
    events = claim_history.history_for_ref("o/r#1")
    assert [e["event"] for e in events] == ["reassigned"]
    assert events[0]["session_id"] == "new"


def test_apply_session_register_rejects_a_terminal_managed_worktree(
    tmp_tracking_dir: Path, monkeypatch,
):
    import agent_worktrees.config as cfg

    config = cfg.Config(
        srcroot=str(tmp_tracking_dir), machine="test", platform="wsl", repo_name="test-repo",
        repos={"test-repo": cfg.RepoConfig(
            anchor=str(tmp_tracking_dir), worktree_root=str(tmp_tracking_dir / "trees"),
        )},
    )
    monkeypatch.setattr(
        config_authority, "load_project_authority_config",
        lambda name, **_kwargs: config,
    )
    path = tmp_tracking_dir / "wt-terminal.yaml"
    rec = WorktreeRecord(
        worktree_id="wt-terminal", branch="worktree/wt-terminal",
        worktree_path="/tmp/wt-terminal", repo="test-repo", machine="test",
        platform="wsl", started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00", resume_count=0, title=None,
        status="finalized", completed_at=None, sessions=[], kind="bridge",
    )
    save_record(rec, path)
    result = tracking_session_registration_write.apply_session_register(
        {"worktree_id": "wt-terminal", "yaml_path": str(path), "session_id": "sess-1"}
    )
    assert result["error"] == "lifecycle"
    record = tracking.load_record(path)
    assert record.session_entry("sess-1") is None


@pytest.mark.parametrize(
    (
        "default_branch", "machine_remote", "available_remotes",
        "remote_ref_available", "declared_branch", "committed_remote",
        "expected_refs", "expected_probes",
    ),
    [
        ("main", "origin", (), True, "", "", ("refs/remotes/origin/main",), 4),
        ("", "origin", (), True, "", "", ("refs/remotes/origin/main",), 4),
        (
            "main", "upstream", (), True, "", "",
            ("refs/remotes/upstream/main",), 4,
        ),
        ("main", "origin", (), False, "", "", ("refs/heads/main",), 5),
        ("main", None, (), False, "", "", ("refs/heads/main",), 5),
        (
            "main", None, ("upstream",), True, "", "upstream",
            ("refs/remotes/upstream/main",), 5,
        ),
        (
            "registry", "origin", (), True, "dev", "",
            ("refs/remotes/origin/main", "refs/remotes/origin/dev"), 7,
        ),
    ],
)
def test_session_register_uses_knowledge_backed_receiving_authority(
    tmp_path: Path, monkeypatch, default_branch, machine_remote,
    available_remotes, remote_ref_available, declared_branch,
    committed_remote, expected_refs, expected_probes,
):
    """Registration skips full project policy while preserving its authority."""
    from agent_worktrees import git_ops, repos as repos_mod

    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    (harness / ".git").mkdir(parents=True)
    cfg.inrepo_config_path(harness).parent.mkdir(parents=True)
    cfg.inrepo_config_path(harness).write_text(
        "stateless: false\nrequires_external_state_root: false\n",
        encoding="utf-8",
    )
    (knowledge / ".agent-worktrees").mkdir(parents=True)
    (knowledge / ".agent-worktrees" / "machines.yaml").write_text(
        "machines:\n"
        "  workstation-windows:\n"
        "    execution_platform: windows\n"
        "    physical_host: workstation\n"
        "  workstation-wsl:\n"
        "    execution_platform: wsl\n"
        "    physical_host: workstation\n",
        encoding="utf-8",
    )
    registry = repos_mod.ReposRegistry(
        repos={
            name: repos_mod.RepoEntry(
                name=name,
                repo_class="worktree",
                default_branch=default_branch,
                paths={
                    "windows": str(anchor),
                    "wsl": str(anchor),
                    "linux": str(anchor),
                },
            )
            for name, anchor in (
                ("harness", harness),
                ("knowledge", knowledge),
            )
        }
    )
    monkeypatch.setattr(repos_mod, "read_registry", lambda: registry)
    committed_path = cfg.inrepo_config_path(Path()).as_posix()
    git_calls = []
    shown_refs = []
    selected_remote = (
        machine_remote
        if machine_remote is not None
        else (
            "origin" if "origin" in available_remotes
            else available_remotes[0] if len(available_remotes) == 1
            else ""
        )
    )

    def committed_config(*args, **kwargs):
        git_calls.append(args)
        assert "timeout" not in kwargs
        assert kwargs["isolated_repository"] is True
        if args[0] == "remote":
            return SimpleNamespace(
                returncode=0,
                stdout="".join(f"{name}\n" for name in available_remotes),
            )
        if args[0] == "symbolic-ref":
            ref = (
                f"refs/remotes/{selected_remote}/main"
                if selected_remote else "refs/heads/main"
            )
            return SimpleNamespace(
                returncode=0,
                stdout=f"{ref}\n",
            )
        if args[0] == "show-ref":
            ref = args[3]
            return SimpleNamespace(
                returncode=(
                    1 if ref.startswith("refs/remotes/")
                    and not remote_ref_available else 0
                ),
                stdout="",
            )
        if args[0] == "ls-tree":
            ref = args[3]
            return SimpleNamespace(returncode=0, stdout=f"{committed_path}\n")
        if args[0] == "show":
            ref = args[1].split(":", 1)[0]
            shown_refs.append(ref)
            if declared_branch and ref.endswith("/main"):
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        f"default_branch: {declared_branch}\n"
                        + (f"remote: {committed_remote}\n" if committed_remote else "")
                        + "stateless: false\n"
                        "requires_external_state_root: false\n"
                    ),
                )
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    (f"remote: {committed_remote}\n" if committed_remote else "")
                    + "stateless: true\nrequires_external_state_root: true\n"
                ),
            )
        raise AssertionError(args)

    monkeypatch.setattr(git_ops, "git", committed_config)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    project_root = tmp_path / ".harness"
    project_root.mkdir()
    (project_root / "config.yaml").write_text(
        "machine: workstation-windows\n"
        "platform: windows\n"
        "knowledge_repo: knowledge\n"
        f"repos:\n  harness:\n    anchor: {harness}\n"
        + (
            f"    remote: '{machine_remote}'\n"
            if machine_remote is not None else ""
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project_root)
    monkeypatch.setattr(
        cfg, "global_config_path", lambda: tmp_path / "missing-global.yaml",
    )
    monkeypatch.setattr(
        cfg,
        "load_project_config",
        lambda *_args, **_kwargs: pytest.fail(
            "registration must not resolve full project configuration"
        ),
    )

    local_path = tmp_path / "local.yaml"
    foreign_path = tmp_path / "foreign.yaml"
    for path, worktree_id, machine, platform in (
        (local_path, "local", "workstation-windows", "windows"),
        (foreign_path, "foreign", "workstation-wsl", "wsl"),
    ):
        save_record(
            WorktreeRecord(
                worktree_id=worktree_id,
                branch=f"worktree/{worktree_id}",
                worktree_path=str(tmp_path / worktree_id),
                repo="harness",
                machine=machine,
                platform=platform,
                started_at="2026-10-13T12:00:00",
                last_resumed_at="2026-10-13T12:00:00",
                resume_count=0,
                title=None,
                status="active",
                completed_at=None,
                sessions=[],
            ),
            path,
        )

    started = time.monotonic()
    accepted = tracking_session_registration_write.apply_session_register(
        {
            "worktree_id": "local",
            "yaml_path": str(local_path),
            "session_id": "sess-local",
        }
    )
    elapsed = time.monotonic() - started
    assert accepted == {"ok": True, "linked_handoff": None}
    assert elapsed < 1.0
    assert len(git_calls) == expected_probes
    assert shown_refs == list(expected_refs)
    assert tracking.load_record(local_path).session_entry("sess-local") is not None

    before = foreign_path.read_bytes()
    rejected = tracking_session_registration_write.apply_session_register(
        {
            "worktree_id": "foreign",
            "yaml_path": str(foreign_path),
            "session_id": "sess-foreign",
        }
    )
    assert rejected["error"] == "execution_space"
    assert foreign_path.read_bytes() == before


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("nonzero", "cannot read committed authority"),
        ("absent", "committed authority config is absent"),
        ("remote_error", "cannot verify committed authority ref"),
        ("head_error", "cannot resolve committed authority HEAD"),
        ("head_malformed", "committed authority HEAD is malformed"),
        ("tree_error", "cannot list committed authority config"),
    ],
)
def test_registration_authority_fails_closed_when_committed_config_is_unreadable(
    tmp_path: Path, monkeypatch, failure, message,
):
    from agent_worktrees import git_ops, repos
    from agent_worktrees.execution_spaces import ExecutionSpaceError

    anchor = tmp_path / "harness"
    (anchor / ".git").mkdir(parents=True)
    registry = repos.ReposRegistry(
        repos={
            "harness": repos.RepoEntry(
                name="harness",
                repo_class="worktree",
                default_branch="main",
                paths={
                    "windows": str(anchor),
                    "wsl": str(anchor),
                    "linux": str(anchor),
                },
            )
        }
    )
    monkeypatch.setattr(repos, "read_registry", lambda: registry)
    project_root = tmp_path / ".harness"
    project_root.mkdir()
    (project_root / "config.yaml").write_text(
        "machine: workstation-windows\n"
        "platform: windows\n"
        "repos:\n"
        "  harness:\n"
        f"    anchor: {anchor}\n"
        "    remote: origin\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project_root)
    monkeypatch.setattr(
        cfg, "global_config_path", lambda: tmp_path / "missing-global.yaml",
    )
    def failed_git(*_args, **kwargs):
        assert kwargs["isolated_repository"] is True
        assert "timeout" not in kwargs
        if _args[0] == "symbolic-ref":
            if failure == "head_error":
                return SimpleNamespace(returncode=128, stdout="")
            if failure == "head_malformed":
                return SimpleNamespace(returncode=0, stdout="refs/tags/main\n")
            if failure == "absent":
                return SimpleNamespace(
                    returncode=0, stdout="refs/remotes/origin/main\n",
                )
            if failure == "tree_error":
                return SimpleNamespace(
                    returncode=0, stdout="refs/remotes/origin/main\n",
                )
        if failure == "remote_error" and _args[0] == "show-ref":
            return SimpleNamespace(returncode=2, stdout="")
        if failure == "tree_error":
            if _args[0] == "show-ref":
                return SimpleNamespace(returncode=0, stdout="")
            if _args[0] == "ls-tree":
                return SimpleNamespace(returncode=2, stdout="")
        if failure == "absent":
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(git_ops, "git", failed_git)

    with pytest.raises(ExecutionSpaceError, match=message):
        config_authority.require_registration_record_mutation(
            SimpleNamespace(repo="harness"),
        )


def test_register_session_reaches_a_live_daemon(record_path, monkeypatch, monkeypatch_config):
    """End-to-end: proves ``tracking.register_session`` reaches
    ``apply_session_register`` via an actual ``CoalescingServer`` (the
    two-process-shaped path real production traffic will use)."""
    from agent_worktrees import locks as _locks
    from agent_worktrees import status_monitor_runtime as _smr

    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    try:
        lock_data = tracking_write.rendezvous_fields(server)
        monkeypatch.setattr(_locks, "read_lock", lambda path: lock_data)
        monkeypatch.setattr(_smr, "_status_monitor_enabled", lambda: False)

        linked = tracking.register_session("wt-reg", "sess-daemon", pid=123)
    finally:
        server.close()

    assert linked is None
    record = tracking.load_record(record_path)
    entry = record.session_entry("sess-daemon")
    assert entry is not None
    assert entry.pid == 123


def test_register_session_never_waits_for_a_cold_daemon_boot(
    record_path, monkeypatch, monkeypatch_config,
):
    """The operative concern for this cluster (operator direction,
    2026-09-27): a sessionStart hook must never block on
    ``tracking_write``'s normal 4s boot-wait. Simulates the cold/
    unreachable-daemon case (no rendezvous data at all) with
    ``ensure_monitor`` wired to a real, timed spy -- proving the call
    returns near-instantly rather than waiting anywhere close to
    ``tracking_write.BOOT_WAIT_S``."""
    from agent_worktrees import locks as _locks
    from agent_worktrees import status_monitor_runtime as _smr

    ensure_monitor_calls = []

    def _spy_ensure_monitor() -> bool:
        ensure_monitor_calls.append(True)
        return False  # simulate: spawn attempted, still not up yet

    monkeypatch.setattr(_locks, "read_lock", lambda path: None)
    monkeypatch.setattr(_smr, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(_smr, "_ensure_status_monitor", _spy_ensure_monitor)

    started = time.time()
    linked = tracking.register_session("wt-reg", "sess-cold", pid=456)
    elapsed = time.time() - started

    assert linked is None
    assert elapsed < 1.0, (
        f"register_session took {elapsed:.2f}s -- must never approach "
        f"tracking_write.BOOT_WAIT_S ({tracking_write.BOOT_WAIT_S}s) on a "
        "sessionStart hook"
    )
    # ensure_monitor is still called (fire-and-forget: warms the daemon for
    # the NEXT session) -- boot_wait_s=0 only skips the spin-wait, not the
    # spawn attempt itself.
    assert ensure_monitor_calls == [True]
    record = tracking.load_record(record_path)
    entry = record.session_entry("sess-cold")
    assert entry is not None
    assert entry.pid == 456
