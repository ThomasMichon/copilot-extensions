"""Pure topology rendering with bounded synthetic observations."""

import copy

import pytest
from rich.console import Console

from worktree_manager.companion_topology import MAX_ROWS, topology_view


def bundle():
    return {
        "worktree_id": "selected",
        "facts": {
            "disposition": {
                "confirmed": True, "observed_at": 100,
                "value": {
                    "title": "Implementation done", "summary": "[bold]literal[/bold]",
                    "activity": "Reviewing", "status": "active", "follow_up": True,
                    "paused": False, "history": [
                        {"at": "first", "changed": ["title"], "title": "Design",
                         "summary": "Plan", "follow_up": False, "kind": "bind", "session": "s1"},
                        {"at": "second", "changed": ["activity"], "activity": "Implement",
                         "summary": "Working", "paused": True},
                    ],
                },
            },
            "claims": {
                "confirmed": False, "observed_at": 90,
                "value": {
                    "owner_ref": "host/project/owner",
                    "resources": [
                        {"kind": "bridge", "ref": "bridge:worker", "state": "held"},
                        {"kind": "worktree", "ref": "host/project/downstream", "state": "released"},
                        {"kind": "pr", "ref": "https://example.test/pr/7", "state": "active"},
                    ],
                },
            },
        },
    }


def observation():
    return {
        "contract": "agent-worktrees.session-observations", "version": 1,
        "supported_versions": [1], "session_id": "s1", "capabilities": ["tools", "subagents"],
        "availability": "partial", "as_of": 101,
        "source": {"kind": "copilot.events.jsonl", "snapshot_bytes": 250, "observed_bytes": 180},
        "history": {"scope": "retained_file", "complete": False,
                    "session_start_records": 0, "valid_records": 3},
        "issues": {"read_error": 1},
        "tools": {
            "started_records": 5, "completed_records": 2, "conflicting_tool_call_ids": 1,
            "unattributed_records": {"started": 1, "completed": 0},
            "by_name": {"[red]read[/red]": {"started_records": 4, "completed_records": 2}},
        },
        "subagents": {
            "availability": "observed", "runtime_continuity": "unknown",
            "lifecycle_records": {"started": 1, "completed": 1, "failed": 0},
            "unattributed_records": 0,
            "agents": [{
                "agent_id": "actual-agent", "name": "task", "display_name": "Reader",
                "model": "reported-model", "last_reported_lifecycle": "completed",
                "parent_tool_call_id": "call-not-agent", "reported_duration_ms": 10,
                "reported_tool_calls": None,
                "lifecycle_records": 2,
            }],
        },
        "pending_input": {"availability": "unknown"},
    }


def labels(tree):
    return "\n".join([tree.label.plain, *(labels(child) for child in tree.children)])


def render(tree, width):
    console = Console(width=width, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(tree)
    return capture.get()


def test_disposition_is_reported_not_inferred_from_title():
    text = labels(topology_view({"id": "selected", "path": "fixture-path"}, bundle()))
    assert "Selected worktree: selected" in text
    assert "Activity: Reviewing" in text and "Status: active" in text
    assert "Title: Implementation done" in text
    assert "phase:" not in text.lower()
    assert "follow_up: true" in text and "paused: false" in text
    assert "At: first" in text and text.index("At: first") < text.index("At: second")
    assert "kind: bind" in text and "session: s1" in text
    assert "not complete phase history" in text


def test_independent_facts_and_directed_typed_claims():
    text = labels(topology_view({"id": "selected"}, bundle()))
    assert "Confirmed; observed_at: 100" in text
    assert "Stale / last known; unconfirmed; observed_at: 90" in text
    assert "host/project/owner -> selected worktree" in text
    assert "Outbound: selected worktree -> recorded resources" in text
    for kind, state in (("bridge", "held"), ("worktree", "released"), ("pr", "active")):
        assert f"Resource [{kind}]" in text and f"claim state: {state}" in text
    assert "PR openness unknown" in text
    assert "open PR" not in text


def test_reported_agents_not_claims_or_parent_edges():
    text = labels(topology_view({"id": "selected"}, bundle(), observation()))
    assert "Reported subagents (distinct from allocated resource claims)" in text
    assert "Agent ID: actual-agent" in text and "Agent ID: call-not-agent" not in text
    assert "Parent tool-call reference (metadata): call-not-agent" in text
    assert "last_reported_lifecycle: completed" in text
    assert "not retirement, release or deallocation" in text
    assert "Runtime continuity: unknown" in text
    assert "Reported duration ms: 10; reported tool calls: unknown" in text
    assert "Pending input: unknown / unsupported" in text


def test_partial_record_totals_preserve_read_failure_boundary():
    text = labels(topology_view({}, {}, observation()))
    assert "Availability: partial; as_of: 101" in text
    assert "observed bytes: 180; snapshot bytes: 250" in text
    assert "Retained snapshot complete: false; not lifetime history" in text
    assert "Issue read_error: 1" in text
    assert "not unique calls or lifetime totals" in text
    assert "Recorded starts: 5; recorded completions: 2" in text
    assert "Unattributed starts: 1" in text
    assert "Conflicting tool-call IDs: 1" in text


@pytest.mark.parametrize("value", [None, {}, [], {"contract": "wrong"}])
def test_absence_malformed_and_error_are_unknown(value):
    text = labels(topology_view(None, None, value, "observation-read-failed"))
    assert "Observation error: observation-read-failed" in text
    assert "Tool records: unknown" in text and "Reported subagents: unknown" in text
    assert "recorded starts: 0" not in text.lower()


def test_unavailable_does_not_render_zero_or_spurious_agents():
    value = observation()
    value["availability"] = "unavailable"
    text = labels(topology_view({}, {}, value, "provider-unavailable"))
    assert "Availability: unavailable" in text
    assert "Reported subagents: unknown" in text and "Agent ID:" not in text
    value.update(availability="available", tools=None, subagents=None)
    text = labels(topology_view({}, {}, value))
    assert "Starts / completions: unknown" in text and "Subagent evidence: unknown" in text


def test_missing_snapshot_field_renders_unknown_instead_of_raising():
    value = observation()
    value["source"].pop("snapshot_bytes")
    text = labels(topology_view({}, {}, value))
    assert "malformed-observation" in text
    assert "Tool records: unknown" in text


def test_observed_empty_agents_are_not_runtime_absence():
    value = observation()
    value["subagents"]["agents"] = []
    text = labels(topology_view({}, {}, value))
    assert "No identified agent records in retained observation; runtime agents unknown" in text


def test_bounds_duplicates_cycles_and_no_reference_expansion():
    data, value = bundle(), observation()
    row = {"kind": "worktree", "ref": "selected", "state": "held"}
    data["facts"]["claims"]["value"].update(owner_ref="selected", resources=[row] * 10000)
    data["facts"]["disposition"]["value"]["history"] = [None] * 10000
    value["subagents"]["agents"] *= 10000
    data["cycle"] = data
    row["cycle"] = row
    tree = topology_view({"id": "selected"}, data, value)
    text = labels(tree)
    assert text.count("Resource [worktree]") == 1
    assert text.count("Agent ID: actual-agent") == 1
    assert "9999 resource row(s) omitted" in text
    assert "9999 agent row(s) omitted" in text
    assert "10000 history row(s) omitted" in text
    assert "self reference; not expanded" in text
    assert len(text) < 10000
    data["facts"]["claims"]["value"]["resources"] = [data]
    assert "1 resource row(s) omitted" in labels(topology_view({"id": "selected"}, data))


def test_tool_and_issue_fanout_bounds():
    value = observation()
    value["tools"]["by_name"] = {
        f"tool-{i}": {"started_records": 1, "completed_records": 0} for i in range(200)
    }
    value["issues"] = {f"reason-{i}": 1 for i in range(200)}
    text = labels(topology_view({}, {}, value))
    assert text.count("Tool tool-") == MAX_ROWS
    assert "176 tool row(s) omitted" in text and "176 issue row(s) omitted" in text


@pytest.mark.parametrize("width", [38, 160])
def test_rich_plain_text_long_labels_and_terminal_width(width):
    data = bundle()
    data["facts"]["disposition"]["value"]["title"] = "[red]literal[/red]" + "x" * 10000
    tree = topology_view({"id": "selected"}, data, observation())
    text = labels(tree)
    output = render(tree, width)
    assert "[red]literal[/red]" in text and "[bold]literal[/bold]" in text
    assert "Tool [red]read[/red]" in text
    assert "[truncated]" in text
    assert "Selected worktree" in output and "Reported" in output
    assert max(len(line) for line in output.splitlines()) <= width


@pytest.mark.parametrize("facts", [None, [], {"claims": None}, {"claims": {"value": []}}])
def test_invalid_fact_shapes(facts):
    text = labels(topology_view({"id": "selected"}, {"facts": facts}))
    assert "Unavailable" in text


def test_mismatch_and_inputs_are_not_mutated():
    data, value = bundle(), observation()
    original = copy.deepcopy((data, value))
    topology_view({"id": "selected"}, data, value)
    assert (data, value) == original
    assert "worktree mismatch" in labels(topology_view({"id": "another"}, data, value))
