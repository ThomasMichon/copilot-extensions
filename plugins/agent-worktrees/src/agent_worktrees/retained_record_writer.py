"""Operation-scoped execution of qualified session writes, not a daemon opt-out."""

from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import json
from typing import TYPE_CHECKING, Any, Callable
from argparse import Namespace

from . import resource_relationship as relationship
from .execution_spaces import ExecutionSpaceError

if TYPE_CHECKING:
    from .tracking import WorktreeRecord

_current: ContextVar[dict[str, Any] | None] = ContextVar("retained_record_writer", default=None)
_hosted: ContextVar[dict[str, Any] | None] = ContextVar("retained_hosted_activation", default=None)
VERBS = frozenset({"session_register", "session_link_succession"})


def active() -> bool:
    return _current.get() is not None


def hosted_activation() -> dict[str, Any] | None:
    return _hosted.get()


def _validate(value: object) -> dict[str, Any]:
    if not (
        isinstance(value, dict)
        and set(value) == {"version", "identity", "creation_nonce", "worktree_id", "project", "worktree_path"}
        and type(value["version"]) is int and value["version"] == 1
        and relationship.valid_writer_identity(value["identity"])
        and all(isinstance(value[key], str) and value[key].strip() for key in (
            "creation_nonce", "worktree_id", "project", "worktree_path",
        ))
        and value["identity"] == relationship.writer_identity()
    ):
        raise ValueError("retained record writer does not match the executing installation")
    return value


def command(method: Callable[[Namespace], int]) -> Callable[[Namespace], int]:
    """Admit only explicit session commands; ordinary invocations stay unchanged."""
    @wraps(method)
    def run(args: Namespace) -> int:
        raw = getattr(args, "retained_record_writer", None)
        hosted_raw = getattr(args, "retained_hosted_activation", None)
        if raw is None and hosted_raw is None:
            return method(args)
        from . import output

        try:
            hosted = None
            if hosted_raw is not None:
                from .retained_hosted_activation import parse

                if raw is None or method.__name__ != "cmd_register_session" or any(
                    getattr(args, key, None) for key in (
                        "stdin", "emit_context", "handoff_token", "handoff_candidate_token",
                        "assignment_token", "launch_id", "pane",
                    )
                ):
                    raise ValueError("Hosted activation requires an explicit retained registration")
                hosted = parse(hosted_raw)
            if not isinstance(raw, str):
                raise ValueError("Retained registration requires its writer scope")
            scope = _validate(json.loads(raw))
            if getattr(args, "worktree_id", None) != scope["worktree_id"]:
                raise ValueError("retained record writer requires its explicit worktree")
            if hasattr(args, "session_id") and not args.session_id:
                raise ValueError("retained record writer requires an explicit session")
            for key in ("cwd", "worktree_dir"):
                if getattr(args, key, None) not in (None, scope["worktree_path"]):
                    raise ValueError("retained record writer requires its original workspace path")
            token = _current.set(scope)
            hosted_token = _hosted.set(hosted)
            try:
                return method(args)
            finally:
                _hosted.reset(hosted_token)
                _current.reset(token)
        except (ValueError, ExecutionSpaceError) as exc:
            return output._json_error(str(exc))
    return run


def require_record(record: WorktreeRecord) -> None:
    """Check the fresh incarnation inside the receiving handler's record lock."""
    scope = _current.get()
    if scope is None:
        return
    try:
        _validate(scope)
        if record.repo != scope["project"] or any(
            getattr(record, key) != scope[key]
            for key in ("worktree_id", "worktree_path", "creation_nonce")
        ):
            raise ValueError("retained record writer workspace incarnation changed")
    except ValueError as exc:
        raise ExecutionSpaceError(str(exc)) from exc
