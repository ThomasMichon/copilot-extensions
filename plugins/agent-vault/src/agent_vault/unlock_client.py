"""Conditional request-source warming for the explicit unlock command."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .cache_warm_client import ServiceSender, selected_service
from .cache_warm_selection import FIELD, WarmSelection, acknowledged
from .cache_warm_status import format_cache_warm
from .command_transport import CommandRoute, Reply, use_unlock_sender
from .invocation import CACHE_TARGET_CAPABILITY

if TYPE_CHECKING:
    from .config import ResolvedVault


class UnlockOutcomeUnknown(RuntimeError):
    """An accepted transport did not confirm the unlock's outcome."""


class UnlockRefused(RuntimeError):
    """A known refusal is not permission to start another prompt."""


@dataclass
class UnlockSession:
    """One health-qualified command's retained route, not a failover policy."""

    sender: ServiceSender
    context: ResolvedVault
    route: CommandRoute
    selection: WarmSelection | None
    source_supported: bool
    last_reply: Reply = None

    def send(self, request: dict[str, Any], timeout: float | None = 5.0) -> Reply:
        """Stop the prompt chain when a retained connector cannot confirm an outcome."""
        outgoing = dict(request)
        if self.source_supported and self.selection is not None:
            outgoing.setdefault(FIELD, self.selection.describe())
        try:
            response = self.sender(
                outgoing, timeout, _context=self.context, _route=self.route,
            )
        except Exception:
            raise UnlockOutcomeUnknown from None
        if not isinstance(response, dict) or type(response.get("ok")) is not bool:
            raise UnlockOutcomeUnknown
        self.last_reply = response
        if (
            response.get("ok") is not True
            and not request.get("password")
            and not request.get("prompt")
            and response.get("needs_unlock") is not True
        ):
            raise UnlockRefused
        return response

    def report(self) -> None:
        """Report only retained acceptance; never retry warming to improve its status."""
        if not self.source_supported or self.selection is None:
            return
        state = self.last_reply.get("cache_warm") if self.last_reply is not None else None
        formatted = (
            format_cache_warm(state)
            if acknowledged(state) and isinstance(state, dict)
            and state.get("target_policy") == CACHE_TARGET_CAPABILITY
            else "unknown"
        )
        if (
            formatted == "unknown" or not isinstance(state, dict)
            or state.get("state") not in {"queued", "running", "complete"}
        ):
            print(
                f"Automatic cache warming not confirmed: {formatted}; no warming retry attempted",
                file=sys.stderr,
            )


def command(args: Any) -> int:
    """Run an explicit unlock without conflating warming and unlock success."""
    from . import cli

    terminal = bool(getattr(args, "terminal", False) or os.environ.get("VAULT_UNLOCK_TERMINAL"))
    if not terminal and not cli.ensure_service():
        print("Error: could not start vault service", file=sys.stderr)
        return 1
    try:
        with selected_service() as selected:
            if selected is None:
                print("Error: selected vault service is unavailable; no unlock attempted", file=sys.stderr)
                return 1
            if not cli._ready_for_work(selected.health):
                print("Error: selected service is not accepting unlock work", file=sys.stderr)
                return 1
            if selected.route.sender is None:
                if not terminal and cli._current_vault_unlocked(selected.health, selected.context):
                    print("Vault available")
                    print("Automatic cache warming not confirmed: transport cannot retain its endpoint",
                          file=sys.stderr)
                    return 0
                print("Error: selected transport cannot retain a qualified endpoint; no unlock attempted",
                      file=sys.stderr)
                return 1
            session = UnlockSession(
                selected.sender, selected.context, selected.route,
                selected.selection, selected.source_supported,
            )
            if not session.source_supported:
                print(
                    "Caller-scoped automatic cache warming is unsupported; "
                    "the selected service retains its legacy warming policy",
                    file=sys.stderr,
                )
            with use_unlock_sender(session.send):
                if terminal:
                    unlocked = cli._terminal_unlock_local()
                elif not session.source_supported and cli._current_vault_unlocked(
                    selected.health, selected.context,
                ):
                    unlocked = True
                else:
                    unlocked = cli.auto_unlock()
            if not unlocked:
                return 1
            print("Vault unlocked (local service, via terminal)" if terminal else "Vault available")
            session.report()
            return 0
    except UnlockOutcomeUnknown:
        print(
            "Error: unlock outcome is unknown; no retry or alternate transport attempted",
            file=sys.stderr,
        )
    except UnlockRefused:
        print("Error: selected service refused unlock; no fallback prompt attempted", file=sys.stderr)
    except (OSError, RuntimeError, ValueError, TypeError, subprocess.SubprocessError):
        print("Error: selected vault context could not be qualified; no automatic retry", file=sys.stderr)
    return 1
