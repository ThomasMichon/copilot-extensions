"""Source-scoped password replacement with the existing caller cache journal."""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING, Any

from .cache_warm_selection import PASSWORD_ACTIONS
from .command_transport import Reply, use_password_sender
from .mutation_client import MutationOutcomeUnknown, MutationSession, run_scoped_mutation

if TYPE_CHECKING:
    from .config import ResolvedVault


class PasswordRecipientUnavailable(RuntimeError):
    """Readiness could not retain the caller's password transaction owner."""


class PasswordSession(MutationSession):
    def source_supported(self, action: str) -> bool:
        if action not in PASSWORD_ACTIONS and action != "get":
            return super().source_supported(action)
        actions = self.health.get("cache_warm_selection_actions") if self.health else None
        return bool(
            super().source_supported("get")
            and isinstance(actions, list) and "set-password" in actions
        )

    def send(
        self, request: dict[str, Any], timeout: float | None, *,
        strict_endpoint: bool, require_endpoint: bool,
    ) -> Reply:
        response = super().send(
            request, timeout, strict_endpoint=strict_endpoint, require_endpoint=require_endpoint,
        )
        if (
            request.get("action") == "ping" and isinstance(response, dict)
            and response.get("ok") is True and response.get("accepting_work", True) is True
        ):
            if self.route.sender is None:
                raise PasswordRecipientUnavailable(
                    "password replacement requires a retained service connector; no operation attempted"
                )
            if self.route.host_owned_selection:
                raise PasswordRecipientUnavailable(
                    "host-owned password selection cannot use the guest's cache journal; "
                    "run password replacement on the host"
                )
        if request.get("action") in PASSWORD_ACTIONS:
            if not isinstance(response, dict) or type(response.get("ok")) is not bool:
                raise MutationOutcomeUnknown
            if any(
                name in response and type(response[name]) is not bool
                for name in ("committed", "cache_pending")
            ):
                raise MutationOutcomeUnknown
        elif request.get("action") == "get":
            if not isinstance(response, dict) or type(response.get("ok")) is not bool:
                return None
            if response["ok"] and (
                not isinstance(response.get("value"), str) or _generation(response) is None
            ):
                return None
        return response


def command(args: Any) -> int:
    """Retain one recipient without moving the caller's replacement transaction."""
    session = PasswordSession()

    def execute(args: Any) -> int:
        assert session.context is not None
        return _transaction(args, session.context)

    try:
        return run_scoped_mutation(execute, args, session, use_password_sender)
    except PasswordRecipientUnavailable as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _generation(response: dict[str, Any]) -> int | None:
    generation = response.get("generation", 0)
    return generation if type(generation) is int and generation >= 0 else None


def _transaction(args: Any, context: ResolvedVault) -> int:
    from . import cli, config
    from .cache import get_cache
    from .credential_reads import legacy_cache_entry

    if not cli._ensure_unlocked_service():
        return 1
    password = args.password
    if not password:
        import getpass

        password = getpass.getpass("New password: ")
        if not password:
            print("Error: no password provided", file=sys.stderr)
            return 1
    persistent = get_cache()
    cache_entry = config.normalize_entry(args.entry, context.group or "")
    operation_entry = "/" + cache_entry
    group = context.group or ""
    with persistent.replacement_lock():
        legacy = legacy_cache_entry(args.entry, cache_entry)
        if legacy is not None:
            persistent.migrate_entry(legacy, cache_entry, "password")
        pending = persistent.pending_replace(cache_entry, "password")
        if pending is not None:
            recovery = cli.send_command({
                "action": "get", "entry": operation_entry, "group": group,
                "field": "password", "refresh": True,
            }, timeout=cli.PASSWORD_MUTATION_TIMEOUT)
            generation = _generation(recovery) if recovery else None
            if (
                not recovery
                or not recovery.get("ok")
                or generation is None
                or not persistent.replace(
                    cache_entry, "password", recovery["value"], generation,
                )
            ):
                print(
                    "Error: could not reconcile the caller's pending password rotation",
                    file=sys.stderr,
                )
                return 2
        operation = time.time_ns()
        if persistent.enabled and not persistent.begin_replace(
            cache_entry, "password", password, operation,
        ):
            print("Error: could not journal the caller's password rotation", file=sys.stderr)
            return 2
        response = cli.send_command({
            "action": "set-password", "entry": operation_entry, "group": group,
            "password": password,
        }, timeout=cli.PASSWORD_MUTATION_TIMEOUT)
        if response and (response.get("ok") or response.get("committed")):
            cache_updated = True
            if persistent.enabled:
                generation = response.get("generation")
                if generation is None:
                    authoritative = cli.send_command({
                        "action": "get", "entry": operation_entry, "group": group,
                        "field": "password", "refresh": True,
                    }, timeout=cli.PASSWORD_MUTATION_TIMEOUT)
                    confirmed_generation = _generation(authoritative) if authoritative else None
                    cache_updated = bool(
                        authoritative
                        and authoritative.get("ok")
                        and confirmed_generation is not None
                        and persistent.replace(
                            cache_entry, "password", authoritative["value"], confirmed_generation,
                        )
                    )
                else:
                    valid_generation = _generation(response)
                    cache_updated = bool(
                        valid_generation is not None
                        and persistent.complete_replace(
                            cache_entry, "password", password, operation, valid_generation,
                        )
                    )
            if not cache_updated:
                print(
                    "Error: password was updated, but the caller's offline cache could not be updated",
                    file=sys.stderr,
                )
                return 2
            if response.get("ok"):
                print(response.get("message", "Password updated"))
                return 0
            print(
                "Error: password was updated, but caching is incomplete: "
                f"{response.get('error', 'unknown cache failure')}",
                file=sys.stderr,
            )
            return 2
        if response and persistent.enabled and not response.get("cache_pending"):
            rolled_back = persistent.rollback_replace(cache_entry, "password", operation)
            if not rolled_back and persistent.pending_replace(cache_entry, "password") is not None:
                print(
                    "Error: password was not updated, but the caller cache remains "
                    "in a recoverable pending state",
                    file=sys.stderr,
                )
                return 2
        error = response.get("error", "unknown") if response else "service unreachable"
        print(f"Error: {error}", file=sys.stderr)
        return 1
