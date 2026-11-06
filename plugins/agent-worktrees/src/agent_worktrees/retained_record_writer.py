"""Operation-scoped execution of qualified session writes, not a daemon opt-out."""

from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import json

from . import resource_relationship as relationship
from .execution_spaces import ExecutionSpaceError

_current: ContextVar[dict | None] = ContextVar("retained_record_writer", default=None)
VERBS = frozenset({"session_register", "session_link_succession"})


def active() -> bool:
    return _current.get() is not None


def _validate(value: object) -> dict:
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


def command(method):
    """Admit only explicit session commands; ordinary invocations stay unchanged."""
    @wraps(method)
    def run(args):
        raw = getattr(args, "retained_record_writer", None)
        if raw is None:
            return method(args)
        from . import output

        try:
            scope = _validate(json.loads(raw))
            if getattr(args, "worktree_id", None) != scope["worktree_id"]:
                raise ValueError("retained record writer requires its explicit worktree")
            if hasattr(args, "session_id") and not args.session_id:
                raise ValueError("retained record writer requires an explicit session")
            for key in ("cwd", "worktree_dir"):
                if getattr(args, key, None) not in (None, scope["worktree_path"]):
                    raise ValueError("retained record writer requires its original workspace path")
            token = _current.set(scope)
            try:
                return method(args)
            finally:
                _current.reset(token)
        except (ValueError, ExecutionSpaceError) as exc:
            return output._json_error(str(exc))
    return run


def require_record(record) -> None:
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
