"""venue_copilot.supervisor: the worktree that supervises a venue session."""

from __future__ import annotations

import types

from venue_copilot.supervisor import supervisor_ref, with_supervisor, worktree_ref


def test_worktree_ref_drops_the_session_and_requires_a_qualified_ref():
    assert worktree_ref("host/proj/wt-1#sess-9") == "host/proj/wt-1"
    assert worktree_ref("wt-1") == ""
    assert worktree_ref(None) == ""


def test_supervisor_prefers_the_owner_ref_env():
    ref = supervisor_ref(env={"AGENT_WORKTREES_OWNER_REF": "host/proj/wt-1#s"},
                         which=lambda name: None)
    assert ref == "host/proj/wt-1"


def test_supervisor_falls_back_to_agent_worktrees():
    calls = []

    def _run(argv, **kw):
        calls.append(argv)
        return types.SimpleNamespace(returncode=0, stdout="host/proj/wt-2#abc\n")

    ref = supervisor_ref(env={}, which=lambda name: "/bin/agent-worktrees", run=_run)
    assert ref == "host/proj/wt-2"
    assert calls == [["/bin/agent-worktrees", "get", "owner-ref"]]


def test_supervisor_is_empty_when_unresolvable():
    assert supervisor_ref(env={}, which=lambda name: None) == ""
    failing = lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="")  # noqa: E731
    assert supervisor_ref(env={}, which=lambda name: "aw", run=failing) == ""


def test_with_supervisor_adds_only_a_resolved_ref():
    venue = {"kind": "ssh", "target": "box", "mux_session_name": "wt-a"}
    assert with_supervisor(venue, "host/proj/wt-1#s")["supervisor_ref"] == "host/proj/wt-1"
    assert "supervisor_ref" not in with_supervisor(venue, "not-qualified")
    assert "supervisor_ref" not in venue