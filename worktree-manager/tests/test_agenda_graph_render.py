"""Display-graph admission, negotiation and git-graph rendering of the Companion agenda."""

from copy import deepcopy
import sys
from threading import Event

import pytest

from worktree_manager import agenda_transport as transport
from worktree_manager.agenda_contract import (
    AgendaFeed, AgendaPublicationFence, AgendaUnavailable, validate_snapshot,
)
from worktree_manager.companion_agenda import agenda_text
from test_agenda_contract import publication, publication_v2


def sample_graph():
    return {
        "version": 1, "revision": 1,
        "cursor": {"frame": "f-sub", "item": "i-transport", "stack": ["f-root", "f-sub"]},
        "frames": [
            {"id": "f-root", "parent": None, "via": None, "state": "active",
             "label": "effort:agenda-graph", "returns": None},
            {"id": "f-spike", "parent": "f-root", "via": "i-provider", "state": "closed",
             "label": "subqueue: Carry the provider graph", "returns": "Wire the contract"},
            {"id": "f-sub", "parent": "f-root", "via": "i-render", "state": "active",
             "label": "subqueue: Companion render", "returns": "Docs and changefiles"},
        ],
        "items": [
            {"id": "i-provider", "frame": "f-root", "label": "Carry the provider graph",
             "state": "done", "attempts": 1},
            {"id": "i-schema", "frame": "f-spike", "label": "Draft the schema",
             "state": "done", "attempts": 1},
            {"id": "i-bound", "frame": "f-spike", "label": "Bound the body",
             "state": "done", "attempts": 2},
            {"id": "i-render", "frame": "f-root", "label": "Companion render",
             "state": "running", "attempts": 1},
            {"id": "i-admit", "frame": "f-sub", "label": "Contract admission",
             "state": "done", "attempts": 1},
            {"id": "i-transport", "frame": "f-sub", "label": "Transport negotiation",
             "state": "queued", "attempts": 0},
            {"id": "i-golden", "frame": "f-sub", "label": "Golden tests",
             "state": "waiting", "attempts": 0},
            {"id": "i-docs", "frame": "f-root", "label": "Docs and changefiles",
             "state": "queued", "attempts": 0},
        ],
        "history": [
            {"at": "2026-11-07T04:10:00Z", "kind": "result", "frame": "f-root",
             "item": "i-provider", "text": "outcome success -> next"},
            {"at": "2026-11-07T04:40:00Z", "kind": "expansion", "frame": "f-sub",
             "item": "i-render", "text": "expanded: Companion render"},
        ],
        "omitted": {"frames": 0, "items": 1, "history": 3},
    }


def graph_publication():
    frame = publication_v2()
    frame["observation"]["graph"] = sample_graph()
    frame["observation"]["snapshot"].update(
        status="working", next_action="Transport negotiation",
        exit_criteria=["Render lands", "Tests green"],
        stack=["agenda-graph / Companion render", "subqueue / Transport negotiation"],
    )
    return frame


GOLDEN = """\
Agenda (display only)
r1 · 11-01 07:00 · working
◎ Goal  Finish the example
▸ Next  Transport negotiation
⚑ Exit  Render lands · Tests green
⎇ Stack agenda-graph / Companion render ▸ subqueue / Transport negotiation

◆ effort:agenda-graph
● Carry the provider graph
├─╮ subqueue: Carry the provider graph  closed
│ ● Draft the schema
│ ● Bound the body ×2
├─╯ ↩ Wire the contract
◐ Companion render  running
├─╮ subqueue: Companion render  active
│ ● Contract admission
│ ◉ Transport negotiation  ◀ cursor
│ ◇ Golden tests  waiting
│ ┆ ↩ then Docs and changefiles
○ Docs and changefiles
… 1 items / 0 frames not shown

History  (+3 earlier)
  11-07 04:40 ╮ expanded: Companion render
  11-07 04:10 ● outcome success -> next
Publication is not acceptance, permission, or an action."""


def test_graph_renders_as_git_graph_lanes_with_cursor_and_history():
    frame = graph_publication()
    validate_snapshot(frame, "example", "selected", "head-a", contract_version=2)
    text = agenda_text(frame, None)
    assert text.plain == GOLDEN
    assert text.no_wrap and text.overflow == "ellipsis"


def test_last_open_branch_closes_parent_lane_and_labels_are_clipped():
    frame = graph_publication()
    graph = frame["observation"]["graph"]
    graph["items"] = [item for item in graph["items"] if item["id"] != "i-docs"]
    graph["items"][0]["label"] = "x" * 200
    lines = agenda_text(frame, None).plain.splitlines()
    assert "╰─╮ subqueue: Companion render  active" in lines
    assert "  ◉ Transport negotiation  ◀ cursor" in lines
    assert any(line.startswith("● xxx") and line.endswith("…") and len(line) <= 80 for line in lines)


def test_observation_without_graph_keeps_the_field_listing():
    plain = agenda_text(publication_v2(), None).plain
    assert "Active stack: " in plain and "◆" not in plain


def test_graph_is_admitted_only_on_cursor_four_and_contract_two():
    frame = graph_publication()
    assert AgendaFeed("example", "selected", "head-a", contract_version=2).receive(frame) == frame
    legacy = publication()
    legacy["observation"]["graph"] = sample_graph()
    for version in (1, 2):
        candidate = deepcopy(legacy)
        candidate["contract_version"] = version
        with pytest.raises(ValueError):
            validate_snapshot(candidate, "example", "selected", "head-a", contract_version=version)


@pytest.mark.parametrize("upgrade", [True, False])
def test_graph_upgrade_or_rollback_does_not_trip_the_publication_fence(upgrade):
    graphed = graph_publication()
    plain = deepcopy(graphed)
    del plain["observation"]["graph"]
    before, after = (plain, graphed) if upgrade else (graphed, plain)
    fence = AgendaPublicationFence()

    def feed():
        return AgendaFeed("example", "selected", "head-a", fence=fence, contract_version=2)

    feed().receive(before)
    # Reconnect at the same resident sequence, now with (or without) the graph.
    assert feed().receive(deepcopy(after)) == after
    # Live update: the resident republished the same revision with the other projection.
    live = feed()
    live.receive(deepcopy(after))
    advanced = {**deepcopy(before), "type": "update", "sequence": 1}
    assert live.receive(advanced) == advanced
    rewritten = deepcopy(after)
    rewritten.update(type="update", sequence=2)
    rewritten["observation"]["snapshot"]["next_action"] = "Different action"
    with pytest.raises(ValueError, match="store revision"):
        live.receive(rewritten)
    same_sequence = {**deepcopy(advanced), "type": "snapshot"}
    same_sequence["observation"]["snapshot"]["next_action"] = "Different action"
    with pytest.raises(ValueError, match="without advancing"):
        feed().receive(same_sequence)


def _frame(graph, index):
    return graph["frames"][index]


@pytest.mark.parametrize("mutate", [
    lambda graph: graph.update(version=2),
    lambda graph: graph.update(revision=2),
    lambda graph: graph.update(extra=True),
    lambda graph: graph["frames"].reverse(),
    lambda graph: _frame(graph, 1).update(via=None),
    lambda graph: _frame(graph, 1).update(state="merged"),
    lambda graph: graph["items"][0].update(frame="missing"),
    lambda graph: graph["items"][0].update(state="pending"),
    lambda graph: graph["items"][0].update(attempts=-1),
    lambda graph: graph["items"].append(dict(graph["items"][0])),
    lambda graph: graph["cursor"].update(frame="f-root"),
    lambda graph: graph["cursor"].update(item="i-missing"),
    lambda graph: graph["history"][0].update(at="yesterday"),
    lambda graph: graph["omitted"].update(items=-1),
    lambda graph: _frame(graph, 0).update(state=[]),
    lambda graph: _frame(graph, 1).update(parent={"id": "f-root"}),
    lambda graph: graph["items"][0].update(state={}),
    lambda graph: graph["items"][0].update(frame=["f-root"]),
    lambda graph: graph["cursor"].update(stack=[["f-root"]]),
    lambda graph: graph["cursor"].update(item=["i-missing"]),
])
def test_malformed_graph_is_rejected(mutate):
    frame = graph_publication()
    mutate(frame["observation"]["graph"])
    with pytest.raises(ValueError):
        validate_snapshot(frame, "example", "selected", "head-a", contract_version=2)


def test_feed_argv_opts_in_only_for_contract_two():
    base = [sys.executable, "-m", "agent_worktrees"]
    argv = transport.feed_argv(
        "example", "selected", "head-a", engine_command=base, contract_version=2, graph=1,
    )
    assert argv[-4:] == ["--contract-version", "2", "--agenda-graph", "1"]
    assert "--agenda-graph" not in transport.feed_argv(
        "example", "selected", "head-a", engine_command=base, contract_version=2,
    )
    for version, graph in ((1, 1), (2, 2), (2, True)):
        with pytest.raises(AgendaUnavailable):
            transport.feed_argv(
                "example", "selected", "head-a", engine_command=base,
                contract_version=version, graph=graph,
            )


@pytest.mark.parametrize("capabilities,declaration,expected", [
    (["worktree-agendas-v1", "worktree-agendas-v2", "worktree-agendas-graph-v1"],
     "CONTRACT_VERSIONS=(1,2)\nGRAPH_VERSIONS=(1,)\n", 1),
    (["worktree-agendas-v1", "worktree-agendas-v2"],
     "CONTRACT_VERSIONS=(1,2)\nGRAPH_VERSIONS=(1,)\n", None),
    (["worktree-agendas-v1", "worktree-agendas-v2", "worktree-agendas-graph-v1"],
     "CONTRACT_VERSIONS=(1,2)\n", None),
    (["worktree-agendas-v1", "worktree-agendas-graph-v1"],
     "CONTRACT_VERSIONS=(1,)\nGRAPH_VERSIONS=(1,)\n", None),
])
def test_actual_probe_reports_graph_only_with_cli_and_resident_support(
    monkeypatch, tmp_path, capabilities, declaration, expected,
):
    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "worktrees_client.py").write_text(
        f"def read_health():\n    return {{'capabilities':{capabilities!r},"
        "'worktrees':{'status':'ready','generation':'a'*32}}\n",
        encoding="utf-8",
    )
    (package / "agenda_contract.py").write_text(declaration, encoding="utf-8")
    monkeypatch.setattr(
        transport, "_HEALTH_PROBE",
        f"import sys; sys.path.insert(0, {str(tmp_path)!r})\n" + transport._HEALTH_PROBE,
    )
    selected = []
    version = transport.require_capability(
        [sys.executable, "-m", "agent_worktrees"], cancelled=Event(), cwd=str(tmp_path),
        on_graph=selected.append,
    )
    assert version == (2 if "worktree-agendas-v2" in capabilities else 1)
    assert selected == [expected]
