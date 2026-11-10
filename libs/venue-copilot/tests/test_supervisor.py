"""venue_copilot.supervisor: the worktree that supervises a venue session."""

from __future__ import annotations

import types
from typing import Any

import pytest

from venue_copilot.supervisor import supervisor_ref, with_supervisor, worktree_ref


def test_worktree_ref_drops_the_session_and_requires_a_qualified_ref() -> None:
    assert worktree_ref("host/proj/wt-1#sess-9") == "host/proj/wt-1"
    assert worktree_ref("wt-1") == ""
    assert worktree_ref(None) == ""


def test_supervisor_prefers_explicit_context() -> None:
    ref = supervisor_ref(explicit="host/proj/wt-1#s", which=lambda name: None)
    assert ref == "host/proj/wt-1"


def test_supervisor_resolves_cwd_despite_inherited_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "stale/proj/wt-1#parent")
    calls: list[list[str]] = []

    def _run(argv: list[str], **kw: Any) -> types.SimpleNamespace:
        calls.append(argv)
        return types.SimpleNamespace(returncode=0, stdout="host/proj/wt-2#abc\n")

    ref = supervisor_ref(which=lambda name: "/bin/agent-worktrees", run=_run)
    assert ref == "host/proj/wt-2"
    assert calls == [["/bin/agent-worktrees", "get", "owner-ref"]]


def test_supervisor_is_empty_when_unresolvable() -> None:
    assert supervisor_ref(which=lambda name: None) == ""
    failing = lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="")  # noqa: E731
    assert supervisor_ref(which=lambda name: "aw", run=failing) == ""


def test_with_supervisor_adds_only_a_resolved_ref() -> None:
    venue = {"kind": "ssh", "target": "box", "mux_session_name": "wt-a"}
    assert with_supervisor(venue, "host/proj/wt-1#s")["supervisor_ref"] == "host/proj/wt-1"
    assert "supervisor_ref" not in with_supervisor(venue, "not-qualified")
    assert "supervisor_ref" not in venue