"""Tests for the resident, coalescing ``status-monitor``.

The monitor consolidates the per-session ``status-updater`` loops into a single
process (the work-coalescing-singleton service tier): one coalesced sweep
refreshes every live ``wt-*`` session's bar, the per-session registry doubles as
a refcount, and the monitor idle-exits when the last session goes. It is opt-in
via ``AGENT_WORKTREES_STATUS_MONITOR``; unset, the per-session updater is
unchanged. These tests drive the pure sweep + registry + routing seams without a
real mux.
"""

from __future__ import annotations

import argparse
import re
import types

import agent_procutil
import pytest

from agent_worktrees import __main__ as m


def test_status_monitor_registered():
    assert m.COMMAND_MAP["status-monitor"] is m.cmd_status_monitor
    assert m._WORKTREE_VERBS["status-monitor"] == "status-monitor"
    # main() must not try to resolve a project for it (it resolves per-session),
    # and the launcher reap must never kill the resident tracker.
    assert "status-monitor" in m._NO_PROJECT_COMMANDS
    assert "status-monitor" in m._LAUNCHER_REAP_VETOES


def test_resident_lifecycle_requests_wait_for_their_deadline():
    assert m._resident_hook_lock_timeout("sessionStart", 4.75) == 3.75
    assert m._resident_hook_lock_timeout("sessionStart", 0.75) == 0.0
    assert m._resident_hook_lock_timeout("preToolUse", 1.75) == 0.05
    assert m._resident_hook_lock_timeout("postToolUse", 0.02) == 0.02


def test_ordinary_hooks_yield_to_waiting_lifecycle_request():
    priority = types.SimpleNamespace(is_set=lambda: True)

    assert m._resident_hook_should_yield("preToolUse", priority) is True
    assert m._resident_hook_should_yield("postToolUse", priority) is True
    assert m._resident_hook_should_yield("sessionStart", priority) is False


def test_identical_resident_lifecycle_requests_coalesce():
    payload = {
        "sessionId": "session-1",
        "cwd": str(m.Path.cwd()),
        "source": "resume",
        "timestamp": 1_800_000_000.25,
        "_agentWorktrees": {"pluginVersion": "1.5.3-dev759"},
    }
    claims = {}

    launch_key, claimed = m._claim_resident_lifecycle(
        payload, claims, now=10.0
    )
    duplicate_key, duplicate_claimed = m._claim_resident_lifecycle(
        payload, claims, now=10.0
    )

    assert launch_key
    assert duplicate_key == launch_key
    assert claimed is True
    assert duplicate_claimed is False


def test_distinct_resident_lifecycle_requests_do_not_coalesce():
    first = {
        "sessionId": "session-1",
        "cwd": str(m.Path.cwd()),
        "source": "resume",
        "timestamp": 1_800_000_000.25,
        "_agentWorktrees": {"pluginVersion": "1.5.3-dev759"},
    }
    second = {**first, "timestamp": 1_800_000_000.5}
    claims = {}

    first_key, first_claimed = m._claim_resident_lifecycle(
        first, claims, now=10.0
    )
    second_key, second_claimed = m._claim_resident_lifecycle(
        second, claims, now=10.0
    )

    assert first_key != second_key
    assert first_claimed is True
    assert second_claimed is True


def test_completed_resident_lifecycle_claim_expires():
    payload = {
        "sessionId": "session-1",
        "cwd": str(m.Path.cwd()),
        "source": "resume",
        "timestamp": 1_800_000_000.25,
        "_agentWorktrees": {"pluginVersion": "1.5.3-dev759"},
    }
    launch_key = m._session_lifecycle_launch_key(
        payload, "1.5.3-dev759"
    )
    claims = {launch_key: 20.0}

    duplicate_key, duplicate_claimed = m._claim_resident_lifecycle(
        payload, claims, now=19.0
    )
    retried_key, retried_claimed = m._claim_resident_lifecycle(
        payload, claims, now=20.0
    )

    assert duplicate_key == launch_key
    assert duplicate_claimed is False
    assert retried_key == launch_key
    assert retried_claimed is True


def test_completed_resident_lifecycle_claim_is_retained():
    claims = {"launch-key": float("inf")}

    m._release_resident_lifecycle(
        "launch-key", claims, completed=True, now=10.0
    )

    assert claims == {
        "launch-key": 10.0 + m._RESIDENT_LIFECYCLE_DEDUPE_S
    }


def test_failed_resident_lifecycle_claim_is_released():
    claims = {"launch-key": float("inf")}

    m._release_resident_lifecycle(
        "launch-key", claims, completed=False, now=10.0
    )

    assert claims == {}


def test_monitor_yields_to_waiting_lifecycle_request(monkeypatch):
    states = iter((True, True, False))
    priority = types.SimpleNamespace(is_set=lambda: next(states))
    sleeps = []
    monkeypatch.setattr(m.time, "sleep", sleeps.append)

    m._wait_for_lifecycle_priority(priority)

    assert sleeps == [0.01, 0.01]


def test_reconcile_sessions_registered():
    assert m.COMMAND_MAP["reconcile-sessions"] is m.cmd_reconcile_sessions
    assert m._WORKTREE_VERBS["reconcile-sessions"] == "reconcile-sessions"
    assert "reconcile-sessions" in m._NO_PROJECT_COMMANDS


def test_reconcile_sessions_emits_one_bounded_pass(monkeypatch):
    captured: dict = {}

    class _Reconciler:
        def __init__(self, **kwargs):
            captured["budgets"] = kwargs

        def observe_mux(self, names):
            captured["mux"] = names

        @property
        def has_mux_observation(self):
            return "mux" in captured

        def step(self):
            return {"projection_written": 1}

    monkeypatch.setattr(
        "agent_worktrees.session_catalog.ResidentSessionReconciler",
        _Reconciler,
    )
    monkeypatch.setattr(m.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        m,
        "_json_output",
        lambda value: captured.update({"output": value}),
    )

    assert m.cmd_reconcile_sessions(
        argparse.Namespace(
            record_budget=2,
            session_budget=3,
            projection_budget=4,
        )
    ) == 0
    assert captured["budgets"] == {
        "record_budget": 2,
        "session_budget": 3,
        "projection_budget": 4,
    }
    assert captured["mux"] == set()
    assert captured["output"] == {
        "projection_written": 1,
        "mux_observed": True,
    }


@pytest.mark.parametrize(
    "val,expected",
    [("1", True), ("true", True), ("YES", True), ("on", True), ("", True),
     ("nope", True), ("0", False), ("false", False), ("no", False),
     ("off", False)],
)
def test_enabled_env(monkeypatch, val, expected):
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", val)
    assert m._status_monitor_enabled() is expected


def test_enabled_env_unset(monkeypatch):
    """Default-on: absent the env var, the monitor is enabled (opt-out)."""
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    assert m._status_monitor_enabled() is True


def test_registry_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: tmp_path / "reg")
    assert m._register_session_for_monitor("wt-a", "/w/a") is True
    assert m._register_session_for_monitor("wt-b", "/w/b") is True
    assert m._register_session_for_monitor("", "/w/x") is False    # no session
    assert m._register_session_for_monitor("wt-c", None) is False  # no path
    reg = m._read_monitor_registry(tmp_path / "reg")
    assert reg == {"wt-a": "/w/a", "wt-b": "/w/b"}


@pytest.mark.parametrize(
    "bad", ["../evil", "wt-../../x", "/abs/path", "wt-a/b", "wt-a\\b", "notwt"])
def test_registry_rejects_unsafe_session(tmp_path, monkeypatch, bad):
    """``--session`` is untrusted: a traversal / absolute / non-wt name must be
    rejected (never escape the registry dir), so it writes nothing."""
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    assert m._valid_monitor_session(bad) is False
    assert m._register_session_for_monitor(bad, "/w/a") is False
    if reg.exists():
        assert list(reg.iterdir()) == []


def _capture_set(monkeypatch):
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        m, "_monitor_mux_set",
        lambda mux_bin, sess, opt, val: calls.append((sess, opt, val)) or True)
    return calls


def test_sweep_serves_live_registered_and_prunes_gone(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    m._register_session_for_monitor("wt-b", "/w/b")
    m._register_session_for_monitor("wt-gone", "/w/g")

    # wt-a + wt-b are live; wt-gone is not; a non-wt session is ignored.
    monkeypatch.setattr(
        m, "_monitor_list_sessions",
        lambda mux_bin: {"wt-a": 1, "wt-b": 0, "other": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    calls = _capture_set(monkeypatch)

    ctx_done: set[str] = set()
    served = m._monitor_sweep("tmux", "TOK", "PFX", ctx_done)

    assert served == 2                                  # wt-a, wt-b
    assert not (reg / "wt-gone").exists()               # pruned
    for sess in ("wt-a", "wt-b"):
        assert (sess, "@aw_updater", "TOK") in calls    # won the election
        assert (sess, "@aw_updater_prefix", "PFX") in calls
        assert (sess, "@aw_ctx", "CTX") in calls        # identity once
        assert (sess, "@aw_seg", "SEG") in calls        # disposition
    assert ctx_done == {"wt-a", "wt-b"}
    # no work for the gone or non-wt sessions
    assert not any(s in ("wt-gone", "other") for s, _, _ in calls)


def test_sweep_ctx_rendered_once(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    calls = _capture_set(monkeypatch)

    ctx_done: set[str] = set()
    m._monitor_sweep("tmux", "T", "P", ctx_done)
    m._monitor_sweep("tmux", "T", "P", ctx_done)        # second pass

    ctx_sets = [c for c in calls if c[1] == "@aw_ctx"]
    seg_sets = [c for c in calls if c[1] == "@aw_seg"]
    assert len(ctx_sets) == 1                            # identity: once
    assert len(seg_sets) == 2                            # disposition: every pass


def test_sweep_reuses_segment_and_skips_unchanged_mux_values(
    tmp_path, monkeypatch
):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(
        m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    calls = _capture_set(monkeypatch)

    class Cache:
        count = 0

        def get(self, path):
            self.count += 1
            return "SEG"

    cache = Cache()
    published = {}
    ctx_done = set()
    m._monitor_sweep(
        "tmux", "T", "P", ctx_done, segment_cache=cache,
        published=published)
    m._monitor_sweep(
        "tmux", "T", "P", ctx_done, segment_cache=cache,
        published=published)

    assert cache.count == 2
    assert [call for call in calls if call[1] == "@aw_seg"] == [
        ("wt-a", "@aw_seg", "SEG")
    ]


def test_sweep_retries_failed_mux_publish(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(
        m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    attempts = []

    def publish(mux, session, option, value):
        attempts.append((session, option, value))
        return len(attempts) > 4

    monkeypatch.setattr(m, "_monitor_mux_set", publish)
    published = {}
    m._monitor_sweep("tmux", "T", "P", set(), published=published)
    assert published == {}
    m._monitor_sweep("tmux", "T", "P", set(), published=published)
    assert len(attempts) == 8


def test_sweep_republishes_for_recreated_mux_session(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    incarnation = ["100"]
    monkeypatch.setattr(
        m, "_monitor_list_sessions",
        lambda mux_bin: {"wt-a": (1, incarnation[0])})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(
        m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    calls = _capture_set(monkeypatch)
    published = {}
    incarnations = {}
    ctx_done = set()

    m._monitor_sweep(
        "tmux", "T", "P", ctx_done, published=published,
        incarnations=incarnations)
    incarnation[0] = "200"
    m._monitor_sweep(
        "tmux", "T", "P", ctx_done, published=published,
        incarnations=incarnations)

    assert len([call for call in calls if call[1] == "@aw_seg"]) == 2
    assert len([call for call in calls if call[1] == "@aw_ctx"]) == 2


def test_segment_cache_throttles_and_invalidates(monkeypatch):
    renders = []
    monkeypatch.setattr(m, "_find_record_for_path", lambda path: None)
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(
        m, "_render_status_segment",
        lambda path, **kwargs: renders.append(path) or f"SEG-{len(renders)}",
    )
    cache = m._StatusSegmentCache(ttl=60)
    assert cache.get("/w/a") == "SEG-1"
    assert cache.get("/w/a") == "SEG-1"
    cache.invalidate("/w/a/src")
    assert cache.get("/w/a") == "SEG-2"
    assert renders == ["/w/a", "/w/a"]


def test_resident_hook_scopes_and_restores_project(monkeypatch):
    prior = m.cfg.active_project()
    seen = []

    class Policy:
        def pre(self, payload):
            seen.append(m.cfg.active_project())
            return {}

    monkeypatch.setattr(
        m,
        "_activate_project_for_path",
        lambda cwd, force: m.cfg.set_active_project("request-project"),
    )
    try:
        m.cfg.set_active_project("prior-project")
        result = m._resident_hook_decision(
            "preToolUse",
            {"cwd": "/w/a", "toolName": "view"},
            segment_cache=object(),
            policy=Policy(),
        )
        assert result == {}
        assert seen == ["request-project"]
        assert m.cfg.active_project() == "prior-project"
    finally:
        m.cfg.set_active_project(prior)


def test_hook_mutation_targets_are_target_aware():
    class Guard:
        _WRITE_VERBS = re.compile(r"Set-Content|git\s+commit", re.IGNORECASE)

    class Client:
        @staticmethod
        def _load_sibling(name):
            return Guard

    policy = m._ResidentHookPolicy(Client())
    assert policy.mutation_targets({
        "toolName": "edit",
        "cwd": "/w/a",
        "toolArgs": {"path": "/w/b/file.py"},
    }) == ["/w/b/file.py"]
    assert policy.mutation_targets({
        "toolName": "powershell",
        "cwd": "/w/a",
        "toolArgs": {"command": "git status --short"},
    }) == []
    assert policy.mutation_targets({
        "toolName": "powershell",
        "cwd": "/w/a",
        "toolArgs": {"command": "git commit -m test"},
    }) is None


def test_anchor_policy_cache_reloads_when_registry_changes(tmp_path):
    registry = tmp_path / "repos.yaml"
    registry.write_text("one", encoding="utf-8")

    class Anchor:
        calls = 0

        @staticmethod
        def _repos_yaml(home):
            return registry

        @classmethod
        def load_worktree_anchors(cls, home):
            cls.calls += 1
            return [{"name": str(cls.calls), "path": "/repo"}]

    class Client:
        @staticmethod
        def _load_sibling(name):
            return Anchor

    policy = m._ResidentHookPolicy(Client())
    assert policy.anchors()[0]["name"] == "1"
    assert policy.anchors()[0]["name"] == "1"
    registry.write_text("two-two", encoding="utf-8")
    assert policy.anchors()[0]["name"] == "2"


def test_resident_agent_bridge_policy_denies_guarded_write(
    tmp_path, monkeypatch
):
    from agent_worktrees import related, repos

    control = tmp_path / "control"
    guarded = tmp_path / "guarded"
    (control / ".git").mkdir(parents=True)
    (guarded / ".git").mkdir(parents=True)
    entry = types.SimpleNamespace(
        name="guarded",
        delegate="agent-bridge",
        locus=types.SimpleNamespace(
            preferred="machine:devbox",
            machines=["devbox"],
        ),
    )
    monkeypatch.setattr(
        m,
        "_related_config_source_anchors",
        lambda root, **_kwargs: [root],
    )
    monkeypatch.setattr(
        related, "list_related_grafted", lambda anchors: [entry])
    monkeypatch.setattr(
        repos, "resolve_path", lambda name: str(guarded))

    hook_client = m._load_hook_client_module()
    assert hook_client is not None
    policy = m._ResidentHookPolicy(hook_client)
    monkeypatch.setattr(policy, "anchors", lambda: [])
    decision = policy.pre({
        "toolName": "edit",
        "cwd": str(control),
        "toolArgs": {"path": str(guarded / "file.py")},
    })
    assert decision["permissionDecision"] == "deny"
    assert "agent-bridge send devbox" in decision["permissionDecisionReason"]


def test_sweep_warms_list_cache_once_per_project(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/p1/a")
    m._register_session_for_monitor("wt-b", "/p1/b")
    m._register_session_for_monitor("wt-c", "/p2/c")
    monkeypatch.setattr(
        m, "_monitor_list_sessions",
        lambda mux_bin: {"wt-a": 1, "wt-b": 1, "wt-c": 1})

    project_by_path = {"/p1/a": "p1", "/p1/b": "p1", "/p2/c": "p2"}

    def _activate(path, *, force):
        m.cfg.set_active_project(project_by_path[path])

    warmed: list[str] = []
    monkeypatch.setattr(m, "_activate_project_for_path", _activate)
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project",
                        lambda **kw: warmed.append(m.cfg.project_name()) or 1)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    _capture_set(monkeypatch)

    assert m._monitor_sweep("tmux", "T", "P", set()) == 3
    assert sorted(warmed) == ["p1", "p2"]


def test_sweep_publishes_each_served_session_to_pane_reconciler(
    tmp_path, monkeypatch
):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    m._register_session_for_monitor("wt-b", "/w/b")
    monkeypatch.setattr(
        m, "_monitor_list_sessions", lambda mux: {"wt-a": 1, "wt-b": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(
        m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    _capture_set(monkeypatch)
    observed: list[tuple[str, str]] = []

    assert m._monitor_sweep(
        "tmux",
        "T",
        "P",
        set(),
        pane_observer=lambda session, path: observed.append((session, path)),
    ) == 2
    assert sorted(observed) == [("wt-a", "/w/a"), ("wt-b", "/w/b")]


def test_sweep_picker_root_keeps_project_warm_without_sessions(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: tmp_path / "reg")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {})
    warmed: list[str] = []
    monkeypatch.setattr(
        m, "_warm_list_cache_for_active_project",
        lambda **kw: warmed.append(m.cfg.project_name()) or 1,
    )

    served = m._monitor_sweep(
        "tmux", "T", "P", set(), picker_projects={"picker-project"})

    assert served == 0
    assert warmed == ["picker-project"]


def test_sweep_without_mux_preserves_registered_sessions(
    tmp_path, monkeypatch
):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(
        m, "_warm_list_cache_for_active_project", lambda **kw: 0)

    assert m._monitor_sweep(None, "T", "P", set()) == 0
    assert (reg / "wt-a").exists()


def test_sweep_transient_mux_failure_holds(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    # None == the mux couldn't be enumerated -> transient; must NOT prune/exit.
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: None)
    calls = _capture_set(monkeypatch)

    assert m._monitor_sweep("tmux", "T", "P", set()) == -1
    assert calls == []
    assert (reg / "wt-a").exists()                       # registry untouched


def test_ensure_monitor_noop_when_live(tmp_path, monkeypatch):
    """A live, current-runtime monitor lock suppresses a duplicate spawn."""
    from agent_worktrees import locks
    lock = tmp_path / "status-monitor.lock"
    monkeypatch.setattr(m, "_monitor_lock_path", lambda: lock)
    spawned: list[list[str]] = []
    monkeypatch.setattr(m, "_spawn_detached", lambda argv: spawned.append(argv))

    # This test process is a live pid; its sys.prefix is not under a versions/
    # slot, so _runtime_superseded() is False -> treated as a live current owner.
    locks.write_lock(lock, extra={"prefix": m.os.path.realpath(m.sys.prefix)})
    m._ensure_status_monitor()
    assert spawned == []                                 # no duplicate

    locks.remove_lock(lock)
    m._ensure_status_monitor()
    assert len(spawned) == 1                             # spawned when absent


def test_ensure_replaces_muxless_owner_when_mux_is_available(
    tmp_path, monkeypatch
):
    from agent_worktrees import locks
    import shutil

    lock = tmp_path / "status-monitor.lock"
    monkeypatch.setattr(m, "_monitor_lock_path", lambda: lock)
    locks.write_lock(
        lock,
        extra={"prefix": m.os.path.realpath(m.sys.prefix), "mux": False},
    )
    monkeypatch.setattr(
        shutil, "which", lambda name: "psmux" if name == "psmux" else None)
    spawned: list[list[str]] = []
    monkeypatch.setattr(m, "_spawn_detached", lambda argv: spawned.append(argv) or True)

    assert m._ensure_status_monitor() is True
    assert len(spawned) == 1


def test_status_updater_delegates_when_enabled(monkeypatch):
    """With the monitor opted in, the per-session updater registers + ensures the
    monitor and returns without running its own loop."""
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "1")
    registered: list[tuple[str, str]] = []
    ensured: list[bool] = []
    monkeypatch.setattr(
        m, "_register_session_for_monitor",
        lambda sess, path: registered.append((sess, path)) or True)
    monkeypatch.setattr(
        m, "_ensure_status_monitor", lambda: ensured.append(True) or True)
    # If it fell through to the real loop it would call _render_status_segment;
    # make that explode so a regression is loud.
    monkeypatch.setattr(m, "_render_status_segment", _boom)

    rc = m.cmd_status_updater(
        argparse.Namespace(session="wt-a", mux="tmux", path="/w/a", interval=5))
    assert rc == 0
    assert registered == [("wt-a", "/w/a")]
    assert ensured == [True]


def test_status_updater_falls_back_when_monitor_cannot_start(monkeypatch):
    """If the monitor can't be ensured, the per-session updater must still run --
    a session is never left without a status bar (a-la-carte inline fallback)."""
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "1")
    monkeypatch.setattr(m, "_register_session_for_monitor", lambda s, p: True)
    monkeypatch.setattr(m, "_ensure_status_monitor", lambda: False)  # spawn failed
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    # Prove we REACHED the per-session loop (did not early-return via the monitor
    # path) by short-circuiting it at its first self-retire check.
    reached = []
    monkeypatch.setattr(
        m, "_runtime_superseded", lambda *a, **k: bool(reached.append(True)) or True)

    rc = m.cmd_status_updater(
        argparse.Namespace(session="wt-a", mux="tmux", path="/w/a", interval=5))
    assert rc == 0
    assert reached  # fell through into the per-session loop


def test_activate_force_clears_prior_project_on_unresolved(monkeypatch):
    """Under force, an unresolved path must NOT leave a prior session's project
    active (else the monitor renders one session with another's context)."""
    from agent_worktrees import config as cfg
    monkeypatch.setattr(m, "_git_toplevel", lambda p: None)  # unresolved
    try:
        cfg.set_active_project("prev")
        m._activate_project_for_path("/no/repo", force=True)
        assert cfg.active_project() is None                 # cleared under force
        # without force, an already-active project is left untouched
        cfg.set_active_project("prev")
        m._activate_project_for_path("/no/repo", force=False)
        assert cfg.active_project() == "prev"
    finally:
        cfg.set_active_project(None)


def _boom(*a, **k):  # pragma: no cover - only fires on regression
    raise AssertionError("per-session loop ran despite monitor being enabled")


# ---------------------------------------------------------------------------
# _restart_status_monitor -- the auto-update cutover seam (consolidated-status-
# daemon Phase 1, dotfiles#1696): reap a superseded monitor + spawn the current
# one so a deploy never leaves live sessions' bars frozen.
# ---------------------------------------------------------------------------

def _wire_restart(monkeypatch, *, lock_data, live, superseded, spawn_ok=True):
    """Stub the lock read/liveness/supersession/spawn/terminate seams."""
    monkeypatch.setattr(m, "_monitor_lock_path", lambda: "/tmp/mon.lock")
    import agent_worktrees.locks as _locks
    monkeypatch.setattr(_locks, "read_lock", lambda p: lock_data)
    monkeypatch.setattr(_locks, "lock_is_live", lambda d: live)
    removed = {"n": 0}

    def _rm(p):
        removed["n"] += 1
    monkeypatch.setattr(_locks, "remove_lock", _rm)
    monkeypatch.setattr(m, "_runtime_superseded", lambda **k: superseded)
    spawned = {"argv": None}
    def _spawn(argv):
        spawned["argv"] = argv
        return spawn_ok
    monkeypatch.setattr(m, "_spawn_detached", _spawn)
    import agent_worktrees.procs as _procs
    reaped = {"pid": None}
    def _term(pid):
        reaped["pid"] = pid
        return True
    monkeypatch.setattr(_procs, "terminate_pid", _term)
    return spawned, reaped, removed


def test_restart_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "0")
    spawned, _r, _rm = _wire_restart(monkeypatch, lock_data=None, live=False, superseded=False)
    r = m._restart_status_monitor()
    assert r["enabled"] is False
    assert r["spawned"] is False
    assert spawned["argv"] is None  # never spawned when opted out


def test_restart_reaps_superseded_and_spawns(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    spawned, reaped, removed = _wire_restart(
        monkeypatch, lock_data={"pid": 4242, "prefix": "/old/slot"},
        live=True, superseded=True)
    r = m._restart_status_monitor()
    assert r["reaped"] == 4242          # old monitor reaped
    assert reaped["pid"] == 4242
    assert removed["n"] >= 1            # stale lock cleared
    assert r["spawned"] is True
    assert spawned["argv"][-1] == "status-monitor"  # current one spawned


def test_restart_leaves_current_monitor_alone(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    spawned, reaped, _rm = _wire_restart(
        monkeypatch, lock_data={"pid": 999, "prefix": "/cur/slot"},
        live=True, superseded=False)
    r = m._restart_status_monitor()
    assert r["already_current"] is True
    assert r["spawned"] is False        # no duplicate spawn
    assert reaped["pid"] is None        # never reap a current monitor
    assert spawned["argv"] is None


def test_restart_clears_dead_lock_then_spawns(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    spawned, reaped, removed = _wire_restart(
        monkeypatch, lock_data={"pid": 1, "prefix": "/x"},
        live=False, superseded=False)  # lock present but dead
    r = m._restart_status_monitor()
    assert removed["n"] >= 1            # dead lock cleared
    assert reaped["pid"] is None        # nothing live to reap
    assert r["spawned"] is True
    assert spawned["argv"][-1] == "status-monitor"


def test_cmd_restart_always_exits_zero(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    _wire_restart(monkeypatch, lock_data=None, live=False, superseded=False)
    rc = m.cmd_status_monitor_restart(argparse.Namespace())
    assert rc == 0
    assert "status-monitor:" in capsys.readouterr().out


def test_installers_invoke_monitor_restart_at_cutover():
    # Consolidated-status-daemon Phase 1 contract: BOTH runtime installers must
    # invoke `status-monitor-restart` at the version cutover, or a deploy silently
    # regresses to frozen bars. Pin it so an installer refactor can't drop it.
    from pathlib import Path
    scripts = Path(m.__file__).resolve().parents[2] / "scripts"
    for name in ("install.ps1", "install.sh"):
        text = (scripts / name).read_text("utf-8")
        assert "status-monitor-restart" in text, (
            f"{name} must invoke `status-monitor-restart` after activating the "
            "new runtime slot (consolidated-status-daemon Phase 1, dotfiles#1696)")


# --- windowless daemon spawn (the "headed status-monitor" DefTerm bug) --------


def test_windowless_python_prefers_pythonw_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_procutil,
        "os",
        types.SimpleNamespace(**{**vars(agent_procutil.os), "name": "nt"}),
    )
    (tmp_path / "python.exe").write_text("")
    pyw = tmp_path / "pythonw.exe"
    pyw.write_text("")
    monkeypatch.setattr(m.sys, "executable", str(tmp_path / "python.exe"))
    assert m._windowless_python() == str(pyw)


def test_windowless_python_falls_back_without_pythonw(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_procutil,
        "os",
        types.SimpleNamespace(**{**vars(agent_procutil.os), "name": "nt"}),
    )
    py = tmp_path / "python.exe"
    py.write_text("")
    monkeypatch.setattr(m.sys, "executable", str(py))
    assert m._windowless_python() == str(py)  # no pythonw sibling -> fall back


def test_windowless_python_noop_off_windows(monkeypatch):
    monkeypatch.setattr(
        agent_procutil,
        "os",
        types.SimpleNamespace(**{**vars(agent_procutil.os), "name": "posix"}),
    )
    monkeypatch.setattr(m.sys, "executable", "/usr/bin/python3")
    assert m._windowless_python() == "/usr/bin/python3"


def test_spawn_detached_swaps_in_windowless_python(monkeypatch):
    seen: dict = {}

    def _fake_popen(argv, **kwargs):
        seen["argv"] = argv
        return object()

    monkeypatch.setattr(m.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(m, "_windowless_python", lambda: "PYTHONW")
    assert m._spawn_detached(
        [m.sys.executable, "-m", "agent_worktrees", "status-monitor"]) is True
    assert seen["argv"][0] == "PYTHONW"
    assert seen["argv"][1:] == ["-m", "agent_worktrees", "status-monitor"]


def test_headless_child_guard_ors_no_window(monkeypatch):
    import subprocess as _sp
    orig = _sp.Popen.__init__
    try:
        monkeypatch.setattr(m, "no_window_flags", lambda: 0x08000000)
        seen: dict = {}
        monkeypatch.setattr(
            _sp.Popen, "__init__",
            lambda self, *a, **k: seen.__setitem__(
                "flags", k.get("creationflags", 0)))
        m._install_headless_child_guard()
        _sp.Popen(["x"])
        assert seen["flags"] & 0x08000000  # CREATE_NO_WINDOW OR'd in
    finally:
        _sp.Popen.__init__ = orig


def test_headless_child_guard_respects_explicit_new_console(monkeypatch):
    import subprocess as _sp
    orig = _sp.Popen.__init__
    try:
        monkeypatch.setattr(m, "no_window_flags", lambda: 0x08000000)
        seen: dict = {}
        monkeypatch.setattr(
            _sp.Popen, "__init__",
            lambda self, *a, **k: seen.__setitem__(
                "flags", k.get("creationflags", 0)))
        m._install_headless_child_guard()
        _sp.Popen(["x"], creationflags=m._CREATE_NEW_CONSOLE)
        # An explicit new-console request is passed through, not silenced.
        assert not (seen["flags"] & 0x08000000)
        assert seen["flags"] & m._CREATE_NEW_CONSOLE
    finally:
        _sp.Popen.__init__ = orig
