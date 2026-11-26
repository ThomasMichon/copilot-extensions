"""Optional, content-free `worktrees.lifecycle.v1` records for finite CLI runs.

One instrumented invocation (create a worktree, take a lease, publish or land
a PR) offers exactly one closed record through the shared consumer-telemetry
command route, after its product work has returned.  Without the local
admission file `<install dir>/consumer-telemetry.json` the run is unchanged:
nothing is read beyond that one path, and no receiver starts.

Records carry only fixed enums and bounded integers.  Names, paths,
repositories, PR numbers, URLs, command lines and error text are never
offered (consumer-telemetry-command, `worktrees.lifecycle.v1`).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Any

SOURCE = "agent-worktrees"
SOURCE_SCHEMA = "worktrees.lifecycle.v1"

# First verb token -> (kind, stage-or-action).  Only mutating lifecycle verbs
# are observed; read-only verbs never pay for a policy lookup.
_VERBS: dict[str, tuple[str, str]] = {
    "create": ("stage", "worktree_create"),
    "bind-session": ("stage", "session_bind"),
    "lease": ("stage", "lease"),
    "create-pr": ("pr", "create"),
    "pr-create": ("pr", "create"),
    "push-changes": ("pr", "push"),
    "pr-publication-labels": ("pr", "label"),
    "pr-merge": ("pr", "merge"),
    "finalize": ("pr", "merge"),
}
_READ_ONLY_LEASE = frozenset({"list", "show", "status", "-h", "--help", "help"})
_HELP = frozenset({"-h", "--help"})

def classify(argv: list[str]) -> tuple[str, str] | None:
    """Map an invocation to its observed kind and stage/action, or None."""
    args = list(argv)
    if args and args[0] in ("agent-worktrees", "worktrees", "worktree"):
        args = args[1:]
    verb_index = None
    i = 0
    while i < len(args):
        token = args[i]
        if token in ("-p", "--project"):
            i += 2
            continue
        if token.startswith("--project="):
            i += 1
            continue
        if token.startswith("-"):
            return None
        verb_index = i
        break
    if verb_index is None:
        return None
    verb, rest = args[verb_index], args[verb_index + 1:]
    observed = _VERBS.get(verb)
    if observed is None or _HELP.intersection(rest):
        return None
    if verb == "lease" and (not rest or rest[0] in _READ_ONLY_LEASE):
        return None
    if verb == "finalize" and "--abandon" in rest:
        return ("pr", "abandon")
    return observed


def _exit_code(value: Any) -> int:
    if value is None:
        return 0
    if type(value) is int:
        return value
    return 1


def _label_result(action: str) -> str | None:
    """Closed label result from this run's publication-label outcome, if any."""
    module = sys.modules.get("agent_worktrees.pr_publication_labels")
    if module is None:
        return None
    try:
        result = module.last_result.get()
    except (AttributeError, LookupError):
        return None
    if type(result) is not dict:
        return None
    if result.get("skipped"):
        return "not_requested"
    if result.get("ok") is True:
        if result.get("added"):
            return "repaired" if action == "label" else "applied"
        return "already_present"
    error = result.get("error")
    if type(error) is str and "does not support publication labels" in error:
        return "unsupported"
    return "failed"


def record(
    kind: str, name: str, *, code: int, interrupted: bool, duration_ms: int,
) -> dict[str, Any]:
    """Build the one closed record for a finished invocation."""
    if interrupted or code == 130:
        outcome, reason = "failure", "interrupted"
    elif code == 0:
        outcome, reason = "success", "none"
    elif kind == "stage" and name == "lease" and code == 3:
        # The lease verb's documented conflict exit (held, custody or lost lease).
        outcome, reason = "failure", "lease_conflict"
    else:
        outcome, reason = "failure", "other"
    event: dict[str, Any] = {"kind": kind, "outcome": outcome, "reason": reason,
                             "duration_ms": max(0, duration_ms)}
    if kind == "stage":
        event["stage"] = name
        event["attempt"] = 1
        return event
    event["action"] = name
    label = _label_result(name)
    if label is not None:
        event["label_result"] = label
        if name == "label" and reason == "other":
            event["reason"] = "unavailable" if label == "unsupported" else (
                "provider_rejected" if label == "failed" else "other")
    return event


def _offer(event: dict[str, Any]) -> None:
    """Admit, offer and close within the contract's bounded shutdown; fail open."""
    try:
        from process_telemetry.finite_owner import FiniteOwner

        from .config import install_dir

        owner = FiniteOwner.from_directory(
            install_dir(), source=SOURCE, source_schema=SOURCE_SCHEMA,
        )
        if owner.legacy or owner.refusal is not None:
            return
        with owner:
            owner.emit(event)
    except Exception:
        return


def instrument(main: Callable[[], Any]) -> Callable[[], Any]:
    """Wrap the console `main`; unobserved verbs run it directly."""

    def run() -> Any:
        observed = classify(sys.argv[1:])
        if observed is None:
            return main()
        kind, name = observed
        started = time.monotonic()
        code, interrupted = 1, False
        try:
            result = main()
            code = _exit_code(result)
            return result
        except SystemExit as exc:
            code = _exit_code(exc.code)
            raise
        except KeyboardInterrupt:
            interrupted = True
            raise
        finally:
            try:
                _offer(record(
                    kind, name, code=code, interrupted=interrupted,
                    duration_ms=int((time.monotonic() - started) * 1000),
                ))
            except Exception:
                pass

    return run
