"""Operation-scoped source selection for named ordinary mutation commands."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from contextvars import ContextVar
from functools import wraps
import sys
from typing import Any, TypeVar

from .cache_warm_client import supported
from .cache_warm_selection import MUTATION_ACTIONS
from .command_transport import ReadSender, Reply, use_mutation_sender, use_unlock_sender
from .read_client import ReadSession, UNSUPPORTED
from .unlock_client import UnlockOutcomeUnknown, UnlockRefused, UnlockSession

_Args = TypeVar("_Args")
_ACTIVE_SESSION: ContextVar[MutationSession | None] = ContextVar("vault_mutation_session", default=None)


class MutationOutcomeUnknown(RuntimeError):
    """The selected connector did not confirm a mutation outcome."""


class MutationSession(ReadSession):
    def source_supported(self, action: str) -> bool:
        if action not in MUTATION_ACTIONS:
            return super().source_supported(action)
        actions = self.health.get("cache_warm_selection_actions") if self.health else None
        return bool(
            self.selection is not None and self.route.sender is not None
            and self.health is not None and supported(self.health)
            and isinstance(actions, list) and action in actions
        )

    def send(
        self, request: dict[str, Any], timeout: float | None, *,
        strict_endpoint: bool, require_endpoint: bool,
    ) -> Reply:
        response = super().send(
            request, timeout, strict_endpoint=strict_endpoint, require_endpoint=require_endpoint,
        )
        if request.get("action") in MUTATION_ACTIONS and (
            not isinstance(response, dict) or type(response.get("ok")) is not bool
        ):
            raise MutationOutcomeUnknown
        return response


def ordinary_mutation(command: Callable[[_Args], int]) -> Callable[[_Args], int]:
    """Keep preparation, existing prompts and the final unbound effect together."""
    @wraps(command)
    def run(args: _Args) -> int:
        return run_scoped_mutation(command, args, MutationSession(), use_mutation_sender)

    return run


def run_scoped_mutation(
    command: Callable[[_Args], int], args: _Args, session: MutationSession,
    operation_scope: Callable[[ReadSender], AbstractContextManager[None]],
) -> int:
    """Reuse unlock/outcome handling without taking a command's cache custody."""
    from . import cli

    unlock: UnlockSession | None = None

    def send_unlock(request: dict[str, Any], timeout: float | None) -> Reply:
        nonlocal unlock
        if session.context is None or session.route.sender is None:
            raise UnlockRefused
        if unlock is None:
            qualified = session.selection is not None and supported(session.health or {})
            if not qualified:
                session.warning = UNSUPPORTED
            unlock = UnlockSession(
                cli.send_command, session.context, session.route,
                session.selection, qualified,
            )
        return unlock.send(request, timeout)

    token = _ACTIVE_SESSION.set(session)
    try:
        with session.activate(), operation_scope(session.send), use_unlock_sender(send_unlock):
            return command(args)
    except UnlockOutcomeUnknown:
        print(
            "Error: unlock outcome is unknown; no fallback prompt or operation replay attempted",
            file=sys.stderr,
        )
    except UnlockRefused:
        print(
            "Error: selected service could not accept a retained unlock; no fallback prompt attempted",
            file=sys.stderr,
        )
    except MutationOutcomeUnknown:
        print(
            "Error: operation outcome is unknown; no retry or alternate transport attempted",
            file=sys.stderr,
        )
    finally:
        _ACTIVE_SESSION.reset(token)
        if session.warning:
            print(f"Warning: {session.warning}", file=sys.stderr)
    return 1


def _ensure_unlocked_service() -> bool:
    from . import cli

    if not cli.ensure_service():
        print("Error: could not start vault service", file=sys.stderr)
        return False
    response = cli.send_command({"action": "ping"})
    session = _ACTIVE_SESSION.get()
    unlocked = (
        cli._current_vault_unlocked(response, session.context)
        if session is not None else cli._current_vault_unlocked(response)
    )
    if unlocked:
        return True
    if cli.auto_unlock():
        return True
    print("Error: vault unavailable", file=sys.stderr)
    return False
