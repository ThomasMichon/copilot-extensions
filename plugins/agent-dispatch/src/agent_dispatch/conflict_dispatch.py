"""conflict_dispatch -- build a "resolve the conflicts on this stuck PR" dispatch.

Generalizes the pattern proven by this facility's own private ``config-reflect``
system (a deterministic, non-agentic producer opens exactly one canonical PR per
domain; when only the far side moved, the PR merges clean and a bypass path
lands it with no agent involved; when the target *and* the producer's own
source both moved since the PR's base, the PR has genuine merge conflicts and
nobody should silently overwrite either side -- the producer instead dispatches
an agentic reconciler to take the PR the last mile).

This module is the small, pure seam any such producer uses to describe its
stuck PR and build the ``agent-dispatch create`` invocation: a domain-scoped
dedup key, a compact JSON descriptor, and the ``agent-dispatch create`` argv
that enqueues the task without spawning a worker (no ``--spawn``, matching
the private prior art: a label-supervisor claims and spawns it later).
It builds on top of -- rather than duplicating -- this plugin's own generic
``conflict-resolution`` loop recipe (:mod:`agent_dispatch.recipes`), reusing its
title/goal/done-criteria rendering and shared safety clauses (suspend/resume,
stagnation, resolution), and layers exactly one thing the recipe does not know
about: which named sub-agent owns the domain-specific resolution *policy* (e.g.
device-biased config resolution, or "never force-overwrite a hand-edited
managed file" for instruction-projection sync) that recipe is deliberately
policy-agnostic about.

Kept dependency-free and side-effect-free (no git, no network, no PR/task
creation) so it is trivially testable; a caller shells the built ``argv`` to
``agent-dispatch`` itself, exactly as any other CLI invocation would.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .recipes import render_recipe

#: stdout marker a caller may print for an observable, greppable daemon-log
#: line (the same convention the private config-reflect prior art uses).
DESCRIPTOR_MARKER = "CONFLICT-DESCRIPTOR:"

#: descriptor schema version (bump if the shape changes incompatibly).
SCHEMA = 1


def dedup_key(*, label: str, domain: str) -> str:
    """The agent-dispatch ``--dedup-key`` for a domain, scoped by dispatch label.

    One reconciler task per domain at a time: there is exactly one canonical
    stuck PR per domain, so a re-observed conflict on the same domain -- across
    retries, re-webhooks, or repeated polls while the PR is still stuck --
    collapses onto the single in-flight task rather than spawning a second agent
    fighting over the same PR. When that task completes (merged or declined) the
    key frees; the next genuine conflict on the domain dispatches afresh.
    """
    return f"{label}:{domain}"


def build_conflict_descriptor(
    *,
    kind: str,
    domain: str,
    repo: str,
    pr: int,
    branch: str,
    base: str,
    label: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the compact pointer a reconciler consumes.

    Points at the open, **conflicted** PR the agent must drive to mergeable --
    the PR *is* the state, so there is no three-way SHA descriptor to carry.
    ``kind`` names the producer's own conflict class (e.g.
    ``"config-conflict"``, ``"projection-conflict"``) so a shared consumer can
    tell descriptors from different producers apart; ``domain`` is that
    producer's own scoping unit (a config system name, a consumer-repo slug,
    ...). ``extra`` carries any additional producer-specific context a
    reconciler needs beyond the bare PR pointer (kept optional and opaque here
    -- this module does not know or validate its shape).
    """
    descriptor: dict[str, Any] = {
        "schema": SCHEMA,
        "kind": kind,
        "domain": domain,
        "repo": repo,
        "pr": int(pr),
        "branch": branch,
        "base": base,
        "dedup_key": dedup_key(label=label, domain=domain),
    }
    if extra:
        descriptor["extra"] = extra
    return descriptor


def descriptor_line(descriptor: dict[str, Any]) -> str:
    """Render the one-line, greppable stdout marker a daemon/log parses."""
    return f"{DESCRIPTOR_MARKER} {json.dumps(descriptor, separators=(',', ':'), sort_keys=True)}"


def parse_descriptor_line(line: str, *, kind: str) -> dict[str, Any] | None:
    """Parse a ``CONFLICT-DESCRIPTOR:`` stdout line back to a descriptor.

    Returns ``None`` for any line that is not a well-formed descriptor of the
    requested ``kind``, so a caller can scan a child's output and ignore
    ordinary log lines (and other producers' descriptors, if several share a
    log stream).
    """
    idx = line.find(DESCRIPTOR_MARKER)
    if idx < 0:
        return None
    payload = line[idx + len(DESCRIPTOR_MARKER) :].strip()
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict) or obj.get("kind") != kind:
        return None
    return obj


@dataclass(frozen=True)
class ConflictDispatch:
    """A built dispatch: the descriptor plus the ``agent-dispatch`` argv."""

    descriptor: dict[str, Any]
    argv: tuple[str, ...]


def build_dispatch(
    *,
    kind: str,
    domain: str,
    label: str,
    repo: str,
    pr: int,
    branch: str,
    base: str,
    reconciler_agent: str,
    extra: dict[str, Any] | None = None,
    target_machine: str | None = None,
) -> ConflictDispatch:
    """Render this plugin's own generic ``conflict-resolution`` recipe for
    ``repo``/``pr``/``base``, then layer the one thing that recipe is
    deliberately policy-agnostic about: which named sub-agent owns the
    domain-specific resolution policy. Returns the descriptor and the
    ``agent-dispatch create`` argv a caller shells out to -- this function
    never calls it itself.

    Reusing ``render_recipe("conflict-resolution", ...)`` rather than hand-
    authoring a goal/done-criteria here means every domain that uses this
    module automatically gets the recipe's shared safety clauses (suspend/
    resume on change/build/review events, the stagnation escalation, and the
    resolved-worktree-on-exit contract) instead of a producer re-deriving --
    and potentially drifting from -- them per domain.
    """
    rendered = render_recipe(
        "conflict-resolution", {"repo": repo, "pr": str(pr), "base": base}
    )
    descriptor = build_conflict_descriptor(
        kind=kind,
        domain=domain,
        repo=repo,
        pr=pr,
        branch=branch,
        base=base,
        label=label,
        extra=extra,
    )
    delegation = (
        f"\n\nDelegate the resolution to the `{reconciler_agent}` sub-agent "
        "via the task tool -- it carries the domain-specific resolution "
        "policy this generic recipe deliberately does not. The stuck-PR "
        "pointer is in the task payload (`agent-dispatch payload <id>`): "
        + json.dumps(descriptor, separators=(",", ":"), sort_keys=True)
    )
    # ``--prompt`` is the actual instruction text the worker executes (the
    # recipe's full charter, plus the domain delegation this recipe doesn't
    # know about); ``--goal``/``--done-criteria`` are the short durable-
    # objective metadata a resumed worker re-derives progress from -- the
    # same title/prompt/goal/done-criteria split ``recipes kick`` itself
    # uses, so a dispatched task here looks identical to one kicked directly.
    argv = [
        "agent-dispatch",
        "create",
        rendered.title,
        "--prompt",
        rendered.prompt + delegation,
        "--goal",
        rendered.goal,
        "--done-criteria",
        rendered.done_criteria,
        "--label",
        label,
        "--dedup-key",
        descriptor["dedup_key"],
        "--payload-inline",
        json.dumps(descriptor, separators=(",", ":"), sort_keys=True),
        "--repo",
        repo,
    ]
    if target_machine:
        argv += ["--target-machine", target_machine]
    return ConflictDispatch(descriptor=descriptor, argv=tuple(argv))
