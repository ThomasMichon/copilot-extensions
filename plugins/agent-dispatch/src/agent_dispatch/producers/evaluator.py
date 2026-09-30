"""Evaluators -- a producer's lifecycle handler (the *judgment* half of
emitters-and-evaluators).

A **producer** puts work on the queue; an **evaluator** is its companion handler
that decides what happens *next* as that work progresses. Like a hook, an
evaluator receives a task's **lifecycle event** -- the shape the coordinator
publishes, ``{"type": "task.submitted", "task": {...}}`` -- and returns
**decisions**: emit a follow-up task, confirm the originating task's completion
claim, or do nothing. Producers wire a domain's *world* into the queue;
evaluators wire its *judgment* into the loop, so a standing domain can automate
a whole cycle (reviewer done -> open a conflict-resolution follow-up; a goal
completed -> emit the next goal; a completion claim corroborated -> confirm it,
per the agent-dispatch vision's *verify-the-completion-claim*) without a
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

import json
import logging
import subprocess
import string
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

from . import UNTRUSTED_EXTERNAL_CONTENT_NOTE

# Lifecycle event types the coordinator publishes (see coordinator._emit).
EVENT_QUEUED = "task.queued"
EVENT_SUBMITTED = "task.submitted"
EVENT_ABANDONED = "task.abandoned"
EVENT_PROGRESS = "task.progress"

log = logging.getLogger("agent-dispatch.evaluator")
MAX_SCRIPT_EVALUATOR_TIMEOUT = 1800.0


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


@dataclass(frozen=True)
class Confirm:
    """A decision to confirm the *originating* task's completion claim --
    the automatic half of *verify-the-completion-claim* for emitter-driven
    work: the rule's own ``when`` predicate is the corroboration judgment
    (e.g. requiring a specific label/status combination believed reliable
    for this domain); this decision itself performs no additional
    corroboration check of its own."""

    reason: str | None = None

    def to_dict(self) -> dict:
        return {"decision": "confirm", "reason": self.reason}


@dataclass(frozen=True)
class Abandon:
    """A decision to abandon the originating task's completion claim."""

    reason: str | None = None

    def to_dict(self) -> dict:
        return {"decision": "abandon", "reason": self.reason}


Decision = Emit | NoOp | Confirm | Abandon


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
        # The event context is the originating task's own fields (title,
        # labels, source, ...); that task may itself trace back to an
        # untrusted external source (a webhook, an issue title). Append the
        # shared framing regardless of what the rule author's own template
        # says, so this guardrail doesn't depend on every rule remembering it.
        fields["prompt"] = (
            _safe_fmt(spec["prompt_template"], ctx) + " " + UNTRUSTED_EXTERNAL_CONTENT_NOTE
        )
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
    minting a follow-up task or confirming the originating one.

    Spec shape (JSON)::

        {"rules": [
          {"on": "task.submitted",
           "when": {"labels_any": ["recipe:reviewer"], "status": "submitted"},
           "emit": {"title_template": "unstick {origin_ref}",
                    "labels": ["recipe:conflict-resolution"],
                    "dedup_template": "evaluator:followup:{task_id}"}},
          {"on": "task.submitted",
           "when": {"labels_any": ["recipe:goal-driven"], "status": "submitted"},
           "confirm": true}
        ]}

    ``on`` is an event type or a list of them; ``when`` is an optional predicate
    (see :func:`_matches`); ``emit`` templates a follow-up task; ``confirm``
    (a bare ``true``) closes the *originating* task's completion claim instead
    (see :class:`Confirm` -- the rule's own ``when`` clause is the
    corroboration judgment). The first matching rule with an ``emit`` or
    ``confirm`` wins per event (rules are ordered); a rule with neither is
    simply never matched to a decision.
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
            if rule.get("confirm"):
                return [Confirm(reason=rule.get("confirm_reason"))]
        return [NoOp(reason="no matching rule")]


def load_spec(path: str | Path) -> dict[str, Any]:
    """Load an evaluator spec JSON file."""
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise EvaluatorError("evaluator spec must be a JSON object")
    return data


def decision_from_dict(value: object) -> Decision:
    """Validate and decode one JSON decision object."""
    if not isinstance(value, dict):
        raise EvaluatorError("script evaluator output must be a JSON object")
    decision = value.get("decision")
    if decision == "emit":
        extra = sorted(set(value) - {"decision", "title", "fields"})
        if extra:
            raise EvaluatorError(f"emit decision has unknown key(s): {extra}")
        title = value.get("title")
        fields = value.get("fields", {})
        if not isinstance(title, str) or not title:
            raise EvaluatorError("emit decision needs a non-empty 'title'")
        if not isinstance(fields, dict):
            raise EvaluatorError("emit decision 'fields' must be an object")
        return Emit(title=title, fields=dict(fields))
    if decision == "noop":
        extra = sorted(set(value) - {"decision", "reason"})
        if extra:
            raise EvaluatorError(f"noop decision has unknown key(s): {extra}")
        reason = value.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise EvaluatorError("noop decision 'reason' must be a string")
        return NoOp(reason=reason)
    if decision == "confirm":
        extra = sorted(set(value) - {"decision", "reason"})
        if extra:
            raise EvaluatorError(f"confirm decision has unknown key(s): {extra}")
        reason = value.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise EvaluatorError("confirm decision 'reason' must be a string")
        return Confirm(reason=reason)
    if decision == "abandon":
        extra = sorted(set(value) - {"decision", "reason"})
        if extra:
            raise EvaluatorError(f"abandon decision has unknown key(s): {extra}")
        reason = value.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise EvaluatorError("abandon decision 'reason' must be a string")
        return Abandon(reason=reason)
    raise EvaluatorError(
        "decision must be one of emit/noop/confirm/abandon"
    )


class ScriptEvaluator:
    """A trusted registered evaluator script, selected by opaque name."""

    def __init__(
        self,
        name: str,
        argv: Sequence[str],
        *,
        timeout_seconds: float = 30.0,
        runner: Callable[..., Any] = subprocess.run,
    ):
        self.name = name
        self.argv = [str(part) for part in argv]
        self.timeout_seconds = float(timeout_seconds)
        self._runner = runner

    def evaluate(self, event: dict) -> list[Decision]:
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        try:
            completed = self._runner(
                self.argv,
                input=payload,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
                **no_window_kwargs(),
            )
        except subprocess.TimeoutExpired:
            log.warning("script evaluator %s timed out after %.1fs", self.name, self.timeout_seconds)
            return [NoOp(reason="script evaluator timed out")]
        except OSError as exc:
            log.warning("script evaluator %s failed to start: %s", self.name, exc)
            return [NoOp(reason=f"script evaluator failed to start: {exc}")]
        if int(completed.returncode) != 0:
            log.warning(
                "script evaluator %s exited %s: %s",
                self.name,
                completed.returncode,
                str(completed.stderr or "").strip()[:400],
            )
            return [NoOp(reason=f"script evaluator exited {completed.returncode}")]
        stdout = str(completed.stdout or "").strip()
        if not stdout:
            log.warning("script evaluator %s produced no output", self.name)
            return [NoOp(reason="script evaluator produced no decision")]
        try:
            return [decision_from_dict(json.loads(stdout))]
        except (EvaluatorError, ValueError) as exc:
            log.warning("script evaluator %s returned malformed output: %s", self.name, exc)
            return [NoOp(reason=f"script evaluator output invalid: {exc}")]


class ScriptEvaluatorRegistry:
    """Trusted selector -> fixed argv mapping for script evaluators."""

    def __init__(self, scripts: dict[str, Sequence[str]], *, timeout_seconds: float = 30.0):
        if not scripts:
            raise EvaluatorError("script evaluator registry requires a non-empty 'scripts' object")
        normalized: dict[str, tuple[str, ...]] = {}
        for name, argv in scripts.items():
            if not isinstance(name, str) or not name:
                raise EvaluatorError("script evaluator names must be non-empty strings")
            if (
                not isinstance(argv, (list, tuple))
                or not argv
                or any(not isinstance(part, str) or not part for part in argv)
            ):
                raise EvaluatorError(
                    f"script evaluator {name!r} must map to a non-empty argv list"
                )
            normalized[name] = tuple(argv)
        self.scripts = normalized
        self.timeout_seconds = float(timeout_seconds)

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> ScriptEvaluatorRegistry:
        if "rules" in spec:
            raise EvaluatorError("script evaluator registry cannot also define declarative 'rules'")
        extra = sorted(set(spec) - {"scripts", "timeout_seconds"})
        if extra:
            raise EvaluatorError(f"unknown script evaluator key(s): {extra}")
        timeout = spec.get("timeout_seconds", 30.0)
        try:
            timeout_seconds = float(timeout)
        except (TypeError, ValueError) as exc:
            raise EvaluatorError("'timeout_seconds' must be a number > 0") from exc
        if timeout_seconds <= 0:
            raise EvaluatorError("'timeout_seconds' must be > 0")
        if timeout_seconds > MAX_SCRIPT_EVALUATOR_TIMEOUT:
            raise EvaluatorError(
                f"'timeout_seconds' must be <= {MAX_SCRIPT_EVALUATOR_TIMEOUT:g}"
            )
        scripts = spec.get("scripts")
        if not isinstance(scripts, dict):
            raise EvaluatorError("script evaluator registry requires a 'scripts' object")
        return cls(scripts, timeout_seconds=timeout_seconds)

    def evaluator_for(
        self,
        selector: str,
        *,
        runner: Callable[..., Any] = subprocess.run,
    ) -> ScriptEvaluator:
        argv = self.scripts.get(selector)
        if argv is None:
            raise EvaluatorError(f"no trusted script evaluator is registered as {selector!r}")
        return ScriptEvaluator(
            selector,
            argv,
            timeout_seconds=self.timeout_seconds,
            runner=runner,
        )


def load_evaluator(
    spec: dict[str, Any],
    *,
    evaluator_ref: str | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> Any:
    """Load a declarative or trusted-script evaluator from JSON spec data."""
    if "rules" in spec and "scripts" in spec:
        raise EvaluatorError("evaluator spec cannot define both 'rules' and 'scripts'")
    if "rules" in spec:
        return SpecEvaluator(spec)
    registry = ScriptEvaluatorRegistry.from_spec(spec)
    if evaluator_ref is None:
        raise EvaluatorError("script evaluator specs require an evaluator_ref selector")
    return registry.evaluator_for(evaluator_ref, runner=runner)


def load_registration_evaluator(
    registration_spec: dict[str, Any],
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> Any:
    """Load the evaluator described by one registration spec."""
    raw = registration_spec.get("evaluator_spec")
    if raw is None:
        path = registration_spec.get("evaluator")
        if not isinstance(path, str) or not path:
            raise EvaluatorError(
                "evaluator registration needs 'evaluator_spec' (inline) or 'evaluator' (a path)"
            )
        raw = load_spec(path)
    if not isinstance(raw, dict):
        raise EvaluatorError("evaluator 'evaluator_spec' must be a JSON object")
    return load_evaluator(
        raw,
        evaluator_ref=registration_spec.get("evaluator_ref"),
        runner=runner,
    )


def apply_decisions(
    decisions: Sequence[Decision],
    *,
    creator: Callable[..., dict],
    repo: str | None = None,
    task_id: str | None = None,
    confirmer: Callable[..., dict] | None = None,
    abandoner: Callable[..., dict] | None = None,
) -> list[dict]:
    """Execute decisions. ``creator`` is ``client.create``-shaped
    ``(title, **fields) -> task``; an :class:`Emit` calls it (stamping ``repo``
    when given). ``confirmer`` is ``client.confirm``-shaped
    ``(task_id, *, actor=...) -> task``; a :class:`Confirm` calls it against
    ``task_id`` (the originating task -- required for any :class:`Confirm`
    decision, since unlike :class:`Emit` there is no new task to derive an id
    from). A :class:`Confirm` with no ``confirmer`` or no ``task_id`` wired is
    recorded as a skipped decision rather than raising -- a caller that never
    wires confirmation simply never gets it, exactly like the degenerate
    no-evaluator case. ``abandoner`` is ``client.abandon``-shaped
    ``(task_id, *, actor=..., reason=...) -> task`` and is handled the same way
    for :class:`Abandon`. A :class:`NoOp` records the skip. Returns a
    per-decision report.
    """
    results: list[dict] = []
    for d in decisions:
        if isinstance(d, Emit):
            fields = dict(d.fields)
            if repo is not None and "repo" not in fields:
                fields["repo"] = repo
            task = creator(d.title, **fields)
            results.append({"decision": "emit", "created": task})
        elif isinstance(d, Confirm):
            if confirmer is None or task_id is None:
                results.append(
                    {
                        "decision": "confirm",
                        "skipped": True,
                        "reason": "no confirmer/task_id wired",
                    }
                )
            else:
                confirmed = confirmer(task_id, actor="evaluator")
                results.append({"decision": "confirm", "completed": confirmed})
        elif isinstance(d, Abandon):
            if abandoner is None or task_id is None:
                results.append(
                    {
                        "decision": "abandon",
                        "skipped": True,
                        "reason": "no abandoner/task_id wired",
                    }
                )
            else:
                abandoned = abandoner(task_id, actor="evaluator", reason=d.reason)
                results.append({"decision": "abandon", "abandoned": abandoned})
        else:
            results.append(d.to_dict())
    return results


def evaluate_and_apply(
    evaluator: Any,
    event: dict,
    *,
    creator: Callable[..., dict],
    repo: str | None = None,
    confirmer: Callable[..., dict] | None = None,
    abandoner: Callable[..., dict] | None = None,
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
        task_id = (event.get("task") or {}).get("id")
        report["applied"] = apply_decisions(
            decisions,
            creator=creator,
            repo=repo,
            task_id=task_id,
            confirmer=confirmer,
            abandoner=abandoner,
        )
    return report
