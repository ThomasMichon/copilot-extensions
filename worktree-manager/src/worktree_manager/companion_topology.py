"""Bounded pure Companion presentation; recorded references are never followed."""

from __future__ import annotations

from itertools import islice

from rich.text import Text
from rich.tree import Tree

from .companion_observations import observation_error as validate_observation

MAX_ROWS = 24
MAX_HISTORY = 20
MAX_LABEL = 240


def _label(value, default="unknown") -> str:
    if isinstance(value, str):
        # Bound before whitespace normalization; never stringify nested/cyclic inputs.
        text = " ".join(value[:MAX_LABEL].split())
        return (text or default) + (" … [truncated]" if len(value) > MAX_LABEL else "")
    if type(value) in (int, float, bool):
        try:
            return str(value)[:MAX_LABEL]
        except ValueError:
            pass
    return default


def _add(parent: Tree, label: str) -> Tree:
    return parent.add(Text(label))


def _fact(bundle, name, worktree_id):
    if not isinstance(bundle, dict):
        return None, "Unavailable: no observation"
    if bundle.get("worktree_id") not in (None, worktree_id):
        return None, "Unavailable: worktree mismatch"
    facts = bundle.get("facts")
    fact = facts.get(name) if isinstance(facts, dict) else None
    if not isinstance(fact, dict):
        return None, "Unavailable: no observation"
    marker = "Confirmed" if fact.get("confirmed") is True else "Stale / last known; unconfirmed"
    marker += "; observed_at: " + _label(fact.get("observed_at"), "unavailable")
    if fact.get("stale") is True:
        marker += "; stale"
    value = fact.get("value")
    if not isinstance(value, dict):
        return None, "Unavailable; " + marker
    return value, marker


def _disposition(root, value, marker):
    group = _add(root, "Reported disposition")
    _add(group, marker)
    if value is None:
        return
    for key in ("title", "summary", "activity", "status"):
        _add(group, f"{key.capitalize()}: {_label(value.get(key), 'not reported')}")
    for key in ("follow_up", "paused"):
        flag = value.get(key)
        _add(group, f"{key}: {str(flag).lower() if type(flag) is bool else 'unknown'}")
    history = _add(group, "Retained disposition observations (oldest-first; not complete phase history)")
    rows = value.get("history")
    if not isinstance(rows, list):
        _add(history, "Unavailable: invalid or absent history")
        return
    if not rows:
        _add(history, "No retained observations reported")
    omitted = max(0, len(rows) - MAX_HISTORY)
    # Preserve the newest retained observations in their producer's oldest-first order.
    for row in rows[-MAX_HISTORY:]:
        if not isinstance(row, dict):
            omitted += 1
            continue
        entry = _add(history, f"At: {_label(row.get('at'))}; kind: {_label(row.get('kind'), 'status')}")
        for key in ("title", "summary", "activity", "session"):
            if key in row:
                _add(entry, f"{key}: {_label(row[key])}")
        for key in ("follow_up", "paused"):
            if key in row:
                flag = row[key]
                _add(entry, f"{key}: {str(flag).lower() if type(flag) is bool else 'unknown'}")
        changed = row.get("changed")
        if isinstance(changed, list):
            fields = ", ".join(_label(item) for item in changed[:8])
            _add(entry, "changed: " + fields + ("; additional fields omitted" if len(changed) > 8 else ""))
    if omitted:
        _add(history, f"{omitted} history row(s) omitted (limit or invalid shape)")


def _claims(root, value, marker, identity):
    group = _add(root, "Recorded resource claims")
    _add(group, marker)
    if value is None:
        return
    owner = value.get("owner_ref")
    if isinstance(owner, str) and owner:
        _add(group, f"Inbound owner reference: {_label(owner)} -> selected worktree"
             + (" [self reference; not expanded]" if owner in identity else ""))
    elif owner is None:
        _add(group, "Inbound owner: no owner recorded")
    else:
        _add(group, "Inbound owner: unavailable (invalid reference)")
    outbound = _add(group, "Outbound: selected worktree -> recorded resources")
    rows = value.get("resources")
    if not isinstance(rows, list):
        _add(outbound, "Unavailable: invalid resource ledger")
        return
    if not rows:
        _add(outbound, "No recorded resources in this observation")
    seen = set()
    omitted = max(0, len(rows) - MAX_ROWS)
    for row in rows[:MAX_ROWS]:
        if not isinstance(row, dict) or not all(
            isinstance(row.get(key), str) and row[key] for key in ("kind", "ref", "state")
        ):
            omitted += 1
            continue
        key = (row["kind"], row["ref"], row["state"])
        if key in seen:
            omitted += 1
            continue
        seen.add(key)
        entry = _add(
            outbound, f"Resource [{_label(row['kind'])}]: {_label(row['ref'])}; "
            f"claim state: {_label(row['state'])}"
            + (" [self reference; not expanded]" if row["ref"] in identity else ""),
        )
        if "note" in row:
            _add(entry, "Note: " + _label(row["note"]))
        if row["kind"].lower() in ("pr", "pull-request", "pull_request"):
            _add(entry, "PR openness unknown: a claim is not PR-state evidence")
    if omitted:
        _add(outbound, f"{omitted} resource row(s) omitted (limit, duplicate or invalid shape)")


def _observations(root, observation, error):
    group = _add(root, "Session metadata observations")
    if error is not None:
        _add(group, "Observation error: " + _label(error))
    invalid = validate_observation(observation)
    if invalid:
        _add(group, "Unavailable / unknown: " + (invalid if observation is not None else "no observation"))
        _add(group, "Tool records: unknown")
        _add(group, "Reported subagents: unknown (not resource claims)")
        _add(group, "Pending input: unknown / unsupported")
        return
    _add(group, f"Availability: {observation['availability']}; as_of: {_label(observation['as_of'])}")
    source, history = observation["source"], observation["history"]
    _add(group, f"Source: retained_file; observed bytes: {source['observed_bytes']}; "
         f"snapshot bytes: {source['snapshot_bytes']}")
    _add(group, f"Retained snapshot complete: {str(history['complete']).lower()}; "
         "not lifetime history")
    _add(group, f"Retained valid records: {history['valid_records']}; "
         f"session-start records: {history['session_start_records']}")
    issues = observation["issues"]
    for reason, count in islice(issues.items(), MAX_ROWS):
        _add(group, f"Issue {_label(reason)}: {count}")
    if len(issues) > MAX_ROWS:
        _add(group, f"{len(issues) - MAX_ROWS} issue row(s) omitted")
    if observation["availability"] == "unavailable":
        _add(group, "Tool records: unknown")
        _add(group, "Reported subagents: unknown (not resource claims)")
    else:
        _tools(group, observation["tools"])
        _subagents(group, observation["subagents"])
    _add(group, "Pending input: unknown / unsupported")


def _tools(parent, tools):
    group = _add(parent, "Retained-file tool records (not unique calls or lifetime totals)")
    if tools is None:
        _add(group, "Starts / completions: unknown")
        return
    _add(group, f"Recorded starts: {tools['started_records']}; "
         f"recorded completions: {tools['completed_records']}")
    unattributed = tools["unattributed_records"]
    _add(group, f"Unattributed starts: {unattributed['started']}; "
         f"unattributed completions: {unattributed['completed']}")
    _add(group, f"Conflicting tool-call IDs: {tools['conflicting_tool_call_ids']}")
    rows = tools["by_name"]
    for name, counts in islice(rows.items(), MAX_ROWS):
        _add(group, f"Tool {_label(name)}: recorded starts {counts['started_records']}; "
             f"recorded completions {counts['completed_records']}")
    if len(rows) > MAX_ROWS:
        _add(group, f"{len(rows) - MAX_ROWS} tool row(s) omitted")


def _subagents(parent, subagents):
    group = _add(parent, "Reported subagents (distinct from allocated resource claims)")
    _add(group, "Lifecycle reports are turn/invocation reports, not retirement, release or deallocation")
    _add(group, "Runtime continuity: unknown; parent call references are metadata, not hierarchy")
    if subagents is None:
        _add(group, "Subagent evidence: unknown")
        return
    _add(group, "Availability: " + subagents["availability"])
    counts = subagents["lifecycle_records"]
    _add(group, f"Lifecycle records: started {counts['started']}; completed {counts['completed']}; "
         f"failed {counts['failed']}; unattributed {subagents['unattributed_records']}")
    rows = subagents["agents"]
    if not rows:
        _add(group, "No identified agent records in retained observation; runtime agents unknown")
    seen = set()
    omitted = max(0, len(rows) - MAX_ROWS)
    for row in rows[:MAX_ROWS]:
        if row["agent_id"] in seen:
            omitted += 1
            continue
        seen.add(row["agent_id"])
        agent = _add(group, "Agent ID: " + _label(row["agent_id"]))
        for key in ("name", "display_name", "model", "last_reported_lifecycle"):
            _add(agent, f"{key}: {_label(row[key])}")
        _add(agent, "Parent tool-call reference (metadata): " + _label(row["parent_tool_call_id"]))
        _add(agent, f"Reported duration ms: {_label(row['reported_duration_ms'])}; "
             f"reported tool calls: {_label(row['reported_tool_calls'])}")
        _add(agent, f"Lifecycle records: {row['lifecycle_records']}")
    if omitted:
        _add(group, f"{omitted} agent row(s) omitted (limit or duplicate)")


def topology_view(
    worktree, bundle, session_observation=None, observation_error=None,
) -> Tree:
    """Render explicit snapshots only, with independent provenance and bounded fanout."""
    worktree = worktree if isinstance(worktree, dict) else {}
    worktree_id = worktree.get("id")
    root = Tree(Text("Selected worktree: " + _label(worktree_id)))
    _add(root, "Path: " + _label(worktree.get("path"), "not reported"))
    identity = {value for value in (worktree_id, worktree.get("path"), worktree.get("ref"))
                if isinstance(value, str) and value}
    disposition, marker = _fact(bundle, "disposition", worktree_id)
    _disposition(root, disposition, marker)
    claims, marker = _fact(bundle, "claims", worktree_id)
    _claims(root, claims, marker, identity)
    _observations(root, session_observation, observation_error)
    return root
