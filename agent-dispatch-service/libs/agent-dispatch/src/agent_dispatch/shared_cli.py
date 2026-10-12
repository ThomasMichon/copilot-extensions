"""Shared CLI helpers re-exported by ``agent_dispatch.__main__``.

These utilities are intentionally resolved through the live root module where
their callers historically patched ``agent_dispatch.__main__`` directly. The
implementations live here to keep ``__main__.py`` focused on composition while
preserving the original compatibility surface.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .loop_commands import _resolve_cli_module


def _core():
    return _resolve_cli_module()


def _split_owner(owner: str | None) -> tuple[str | None, str | None]:
    """Split a ``machine/worktree`` worker id into its parts."""
    if not owner:
        return (None, None)
    machine, sep, worktree = owner.partition("/")
    if not sep:
        return (owner or None, None)
    return (machine or None, worktree or None)


def _simple(method: str, *arg_names: str):
    """Build a handler that forwards positional args to a client method."""

    def handler(args: argparse.Namespace) -> int:
        with _core()._client(args) as c:
            result = getattr(c, method)(*[getattr(args, n) for n in arg_names])
        return _core()._emit(result)

    return handler


def _owner_from_identity(args: argparse.Namespace) -> str | None:
    """Compose the canonical ``machine/worktree`` owner from the CWD identity."""
    machine, worktree = _core()._identity(args)
    if machine and worktree:
        return f"{machine}/{worktree}"
    return None


def _resolve_owner(args: argparse.Namespace, *, verb: str) -> str | None:
    """Resolve the acting worker's owner for a lease-holding verb."""
    worker_id = getattr(args, "worker_id", None) or _core()._owner_from_identity(args)
    if not worker_id:
        print(
            f"agent-dispatch: could not resolve the owner for {verb}. Pass the "
            f"owner positionally (`{verb} <id> <owner>`) or run inside the "
            "owning worktree so machine/worktree resolves.",
            file=sys.stderr,
        )
    return worker_id


def _hold_actor(args: argparse.Namespace) -> str:
    """Resolve the actor recorded for pause/unpause audit history."""
    return getattr(args, "actor", None) or _core()._owner_from_identity(args) or "operator"


def _read_result(args: argparse.Namespace) -> object | None:
    """Read and decode the complete command's optional JSON result."""
    raw = args.result_json
    if args.result_file is not None:
        if args.result_file == "-":
            raw = sys.stdin.read()
        else:
            path_cls = getattr(_core(), "Path", Path)
            path = path_cls(args.result_file).expanduser()
            raw = path.read_text(encoding="utf-8-sig")
    if raw is None:
        return None
    raw = raw.removeprefix("\ufeff")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if result is None:
        raise ValueError("result must be a JSON object or array, not null")
    from .queue import ResultTooLargeError, ResultValidationError, encode_result

    try:
        encode_result(result)
    except (ResultValidationError, ResultTooLargeError) as exc:
        raise ValueError(str(exc)) from exc
    return result


class _DashDashParser(argparse.ArgumentParser):
    """Capture a verbatim ``-- <command...>`` tail for the command families that need it."""

    def parse_known_args(self, args=None, namespace=None):  # type: ignore[override]
        args = list(sys.argv[1:] if args is None else args)
        if "--" in args:
            idx = args.index("--")
            head, tail = args[:idx], args[idx + 1 :]
            try:
                peek, _ = super().parse_known_args(head, None)
            except SystemExit:
                peek = None
            run_targets = (_core()._cmd_run, _core()._cmd_recipes_drive)
            if peek is not None and getattr(peek, "func", None) in run_targets:
                ns, extras = super().parse_known_args(head, namespace)
                ns._dashdash_tail = tail
                return ns, extras
        return super().parse_known_args(args, namespace)
