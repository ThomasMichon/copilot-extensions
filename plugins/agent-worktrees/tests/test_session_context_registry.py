"""Synthetic tests for bounded state-pair and related-repository context."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_worktrees import related
from agent_worktrees import session_context
from agent_worktrees import state_root


def _record(path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        repo="control",
        worktree_id="wt-control",
        pair_role="harness",
        pair_kind="worktree",
        status="active",
        worktree_path=str(path),
    )


def test_context_includes_pair_and_bounded_related_topology(
    tmp_path: Path,
    monkeypatch,
) -> None:
    checkout = tmp_path / "control"
    sibling = tmp_path / "state"
    checkout.mkdir()
    sibling.mkdir()
    topology = related.RelatedConfig(
        primary="application",
        related={
            "application": related.RelatedEntry(
                name="application",
                role="product",
                locus=related.Locus(preferred="local"),
                delegate="none",
            ),
            "remote-tool": related.RelatedEntry(
                name="remote-tool",
                role="tooling",
                locus=related.Locus(preferred="machine:builder"),
                delegate="agent-bridge",
            ),
            "cloud-docs": related.RelatedEntry(
                name="cloud-docs",
                role="docs",
                locus=related.Locus(preferred="codespace"),
                delegate="agent-codespaces",
            ),
            "ordinary-local": related.RelatedEntry(
                name="ordinary-local",
                role="sibling",
                locus=related.Locus(preferred="local"),
                delegate="none",
            ),
        },
    )
    monkeypatch.setattr(
        session_context.state_root,
        "resolve_state_root",
        lambda *_a, **_k: state_root.StateRoot(
            str(tmp_path / "state-anchor"),
            "knowledge_repo",
            "state",
            True,
            True,
            True,
        ),
    )
    monkeypatch.setattr(
        session_context.state_root,
        "resolve_pair",
        lambda *_a, **_k: state_root.StatePair(
            paired=True,
            pair_id="pair-1",
            current=state_root.PairCheckout(
                role="harness",
                path=str(checkout),
                repo="control",
                worktree_id="wt-control",
                status="active",
            ),
            sibling=state_root.PairCheckout(
                role="knowledge",
                path=str(sibling),
                repo="state",
                worktree_id="wt-state",
                status="ready",
            ),
        ),
    )
    monkeypatch.setattr(
        session_context.state_root,
        "config_source_anchors",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        session_context.related,
        "installed_plugin_related_anchors",
        lambda: [],
    )
    monkeypatch.setattr(
        session_context.related,
        "read_related_grafted",
        lambda _anchors: topology,
    )

    context = session_context.render_registry_context(
        SimpleNamespace(),
        _record(checkout),
        cwd=str(checkout),
        pane_id="%1",
        mux_session="wt-control",
    )

    assert "role=harness; kind=worktree; status=active" in context
    assert f"status=ready; path={sibling}" in context
    assert "primary=application" in context
    assert "remote-tool(role=tooling,locus=machine:builder" in context
    assert "cloud-docs(role=docs,locus=codespace" in context
    assert "ordinary-local" not in context
    assert "`agent-worktrees state-root --pair --json`" in context
    assert "`agent-worktrees related list`" in context
    assert "`agent-worktrees related resolve <name>`" in context
    assert len(context.encode("utf-8")) <= session_context.MAX_TOPOLOGY_BYTES


def test_context_fails_closed_when_state_and_pair_are_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    checkout = tmp_path / "ordinary"
    checkout.mkdir()
    monkeypatch.setattr(
        session_context.state_root,
        "resolve_state_root",
        lambda *_a, **_k: state_root.StateRoot(
            None,
            "knowledge_repo",
            "state",
            True,
            True,
            False,
            error="registered checkout is unavailable",
        ),
    )
    monkeypatch.setattr(
        session_context.state_root,
        "resolve_pair",
        lambda *_a, **_k: state_root.StatePair(
            paired=False,
            error="current worktree is not paired",
        ),
    )
    monkeypatch.setattr(session_context, "_related_summary", lambda *_a: ("-", ""))

    context = session_context.render_registry_context(
        SimpleNamespace(),
        _record(checkout),
        cwd=str(checkout),
    )

    assert "State: source=knowledge_repo; repo=state; status=unavailable; path=-." in context
    assert "Pair: unavailable (current worktree is not paired)." in context
    assert str(tmp_path / "state-anchor") not in context


def test_context_preserves_fresh_queries_under_path_pressure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    checkout = tmp_path / ("checkout-" + ("x" * 500))
    record = _record(checkout)
    record.worktree_path = str(checkout)
    monkeypatch.setattr(
        session_context.state_root,
        "resolve_state_root",
        lambda *_a, **_k: state_root.StateRoot(
            str(tmp_path / ("anchor-" + ("y" * 500))),
            "knowledge_repo",
            "state",
            True,
            True,
            True,
        ),
    )
    monkeypatch.setattr(
        session_context.state_root,
        "resolve_pair",
        lambda *_a, **_k: state_root.StatePair(
            paired=False,
            error="not paired",
        ),
    )
    monkeypatch.setattr(session_context, "_related_summary", lambda *_a: ("-", ""))

    context = session_context.render_registry_context(
        SimpleNamespace(),
        record,
        cwd=str(checkout),
    )

    assert len(context.encode("utf-8")) <= session_context.MAX_TOPOLOGY_BYTES
    assert "[agent-worktrees context truncated]" in context
    assert "`agent-worktrees related resolve <name>`" in context
