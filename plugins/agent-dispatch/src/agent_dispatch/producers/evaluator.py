"""Evaluators -- a producer's lifecycle handler (the *judgment* half of
emitters-and-evaluators).

A **producer** puts work on the queue; an **evaluator** is its companion handler
that decides what happens *next* as that work progresses. Like a hook, an
evaluator receives a task's **lifecycle event** -- the shape the coordinator
publishes, ``{"type": "task.completed", "task": {...}}`` -- and returns
**decisions**: emit a follow-up task, or do nothing. Producers wire a domain's
*world* into the queue; evaluators wire its *judgment* into the loop, so a
standing domain can automate a whole cycle (reviewer done -> open a
conflict-resolution follow-up; a goal completed -> emit the next goal) without a
bespoke module.

This module is the pure contract plus a declarative :class:`SpecEvaluator`, in
the same spirit as the webhook/schedule producers: rules match on the event and
mint follow-up tasks from templates. :func:`apply_decisions` executes them
through an ordinary :class:`~agent_dispatch.client.DispatchClient` (injected, so
the whole path is testable without a live coordinator). The **degenerate case is
the ad-hoc kick**: a one-off task with no evaluator still runs -- an evaluator is
opt-in judgment, never required.
"""

from __future__ import annotations

import string
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

# Lifecycle event types the coordinator publishes (see coordinator._emit).
EVENT_QUEUED = "task.queued"
EVENT_COMPLETED = "task.completed"
EVENT_ABANDONED = "task.abandoned"
EVENT_PROGRESS = "task.progress"


class EvaluatorError(ValueError):
    """Raised for a malformed evaluator spec."""


@dataclass(frozen=True)
class Emit:
    """A decision to create a follow-up task. ``fields`` are ``create`` kwargs
    (prompt, labels, requires, source, origin_ref, dedup_key, goal, ...)."""

    title: str
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"decision": "emit", "title": self.title, "fields": dict(self.fields)}


@dataclass(frozen=True)
class NoOp:
    """A decision to do nothing (recorded so the trace is explicit)."""

    reason: str | None = None

    def to_dict(self) -> dict:
        return {"decision": "noop", "reason": self.reason}


Decision = Emit | NoOp


def _safe_fmt(template: str, values: dict[str, Any]) -> str:
    """``str.format``-style fill that leaves an unknown ``{placeholder}`` intact."""

    class _Safe(dict):
        def __missing__(self, key: str) -> str:  # pragma: no cover - via format_map
            return "{" + key + "}"

    return string.Formatter().vformat(template, (), _Safe(values))


def _event_context(event: dict) -> dict[str, Any]:
    """Flatten an event into a template/predicate context: the task's own fields,
    plus ``event_type`` and the task id under ``task_id``."""
    task = event.get("task") or {}
    ctx: dict[str, Any] = dict(task)
    ctx["event_type"] = event.get("type")
    ctx["task_id"] = task.get("id")
    return ctx


def _matches(when: dict, event: dict) -> bool:
    """Evaluate a rule's ``when`` predicate against an event. All present clauses
    must hold (AND). Supported clauses: ``labels_any``, ``labels_all``,
    ``status``, ``source``."""
    task = event.get("task") or {}
    labels = set(task.get("labels") or [])
    if "labels_any" in when and not (labels & set(when["labels_any"])):
        return False
    if "labels_all" in when and not set(when["labels_all"]).issubset(labels):
        return False
    if "status" in when and task.get("status") != when["status"]:
        return False
    if "source" in when and task.get("source") != when["source"]:
        return False
    return True


def _emit_from_rule(rule: dict, event: dict) -> Emit:
    """Build an :class:`Emit` from a rule's ``emit`` block and the event context."""
    spec = rule.get("emit") or {}
    ctx = _event_context(event)
    title_t = spec.get("title_template")
    if not title_t:
        raise EvaluatorError("an emit rule requires a 'title_template'")
    fields: dict[str, Any] = {}
    if spec.get("prompt_template"):
        fields["prompt"] = _safe_fmt(spec["prompt_template"], ctx)
    if spec.get("goal_template"):
        fields["goal"] = _safe_fmt(spec["goal_template"], ctx)
    if spec.get("origin_ref_template"):
        fields["origin_ref"] = _safe_fmt(spec["origin_ref_template"], ctx)
    if spec.get("dedup_template"):
        fields["dedup_key"] = _safe_fmt(spec["dedup_template"], ctx)
    if spec.get("labels"):
        fields["labels"] = list(spec["labels"])
    if spec.get("requires"):
        fields["requires"] = list(spec["requires"])
    fields["source"] = spec.get("source", "evaluator")
    if spec.get("proposed"):
        fields["proposed"] = True
    return Emit(title=_safe_fmt(title_t, ctx), fields=fields)


class SpecEvaluator:
    """A declarative evaluator: a list of rules, each matching an event and
    minting a follow-up task.

    Spec shape (JSON)::

        {"rules": [
          {"on": "task.completed",
           "when": {"labels_any": ["recipe:reviewer"], "status": "completed"},
           "emit": {"title_template": "unstick {origin_ref}",
                    "labels": ["recipe:conflict-resolution"],
                    "dedup_template": "evaluator:followup:{task_id}"}}
        ]}

    ``on`` is an event type or a list of them; ``when`` is an optional predicate
    (see :func:`_matches`); ``emit`` templates the follow-up task. The first
    matching rule with an ``emit`` wins per event (rules are ordered).
    """

    def __init__(self, spec: dict):
        if not isinstance(spec, dict):
            raise EvaluatorError("evaluator spec must be a JSON object")
        rules = spec.get("rules")
        if rules is None or not isinstance(rules, list):
            raise EvaluatorError("evaluator spec requires a 'rules' list")
        self.rules = rules

    def evaluate(self, event: dict) -> list[Decision]:
        etype = event.get("type")
        for rule in self.rules:
            on = rule.get("on")
            on_set = {on} if isinstance(on, str) else set(on or ())
            if on_set and etype not in on_set:
                continue
            if not _matches(rule.get("when") or {}, event):
                continue
            if "emit" in rule:
                return [_emit_from_rule(rule, event)]
        return [NoOp(reason="no matching rule")]


def apply_decisions(
    decisions: Sequence[Decision],
    *,
    creator: Callable[..., dict],
    repo: str | None = None,
) -> list[dict]:
    """Execute decisions. ``creator`` is ``client.create``-shaped
    ``(title, **fields) -> task``; an :class:`Emit` calls it (stamping ``repo``
    when given), a :class:`NoOp` records the skip. Returns a per-decision report.
    """
    results: list[dict] = []
    for d in decisions:
        if isinstance(d, Emit):
            fields = dict(d.fields)
            if repo is not None and "repo" not in fields:
                fields["repo"] = repo
            task = creator(d.title, **fields)
            results.append({"decision": "emit", "created": task})
        else:
            results.append(d.to_dict())
    return results


def evaluate_and_apply(
    evaluator: SpecEvaluator,
    event: dict,
    *,
    creator: Callable[..., dict],
    repo: str | None = None,
    apply: bool = True,
) -> dict:
    """Evaluate ``event`` and (optionally) apply the decisions. Returns a report
    with the decisions and, when applied, their results."""
    decisions = evaluator.evaluate(event)
    report: dict[str, Any] = {
        "event_type": event.get("type"),
        "decisions": [d.to_dict() for d in decisions],
    }
    if apply:
        report["applied"] = apply_decisions(decisions, creator=creator, repo=repo)
    return report
