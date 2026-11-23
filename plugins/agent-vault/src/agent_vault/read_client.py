"""Retain the readiness-selected peer for ordinary credential reads."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
import os
import sys
from typing import TYPE_CHECKING, Any

from .cache_warm_client import supported
from .cache_warm_selection import FIELD, VERSION, WarmSelection, acknowledged
from .command_transport import CommandRoute, Reply, use_read_sender
from .invocation import CACHE_TARGET_CAPABILITY, configuration_root, use_configuration_root

if TYPE_CHECKING:
    from .config import ResolvedVault

UNSUPPORTED = (
    "Caller-scoped automatic cache warming is unsupported; "
    "the selected service retains its legacy warming policy"
)
UNCONFIRMED = "Automatic cache warming not confirmed; no warming retry attempted"


@dataclass
class ReadSession:
    context: ResolvedVault | None = None
    selection: WarmSelection | None = None
    route: CommandRoute = field(default_factory=CommandRoute)
    health: dict[str, Any] | None = None
    warning: str | None = None
    initialized: bool = False

    @contextmanager
    def activate(self) -> Iterator[None]:
        from . import config

        if not self.initialized:
            # A pinned bootstrap names a vault, not an unrelated configuration owner.
            if config.PINNED_CONTEXT_ENV not in os.environ or configuration_root() is not None:
                try:
                    root, native_default = config.configuration_selection(strict=True)
                    self.selection = WarmSelection(root or os.getcwd(), native_default)
                except (OSError, RuntimeError, ValueError):
                    self.warning = UNSUPPORTED
            self.initialized = True
        root = self.selection.root if self.selection is not None else None
        native_default = self.selection.native_default if self.selection is not None else False
        with use_configuration_root(root, native_default=native_default):
            if self.context is None:
                self.context = config.resolve_context()
            with use_read_sender(self.send):
                yield

    def source_supported(self, action: str) -> bool:
        versions = self.health.get("cache_warm_read_selection_versions") if self.health else None
        actions = self.health.get("cache_warm_read_selection_actions") if self.health else None
        return bool(
            self.selection is not None and self.route.sender is not None
            and self.health is not None and supported(self.health)
            and isinstance(versions, list)
            and any(type(version) is int and version == VERSION for version in versions)
            and (action in {"get", "has"} or isinstance(actions, list) and action in actions)
        )

    def send(
        self, request: dict[str, Any], timeout: float | None, *,
        strict_endpoint: bool, require_endpoint: bool,
    ) -> Reply:
        from . import cli

        assert self.context is not None
        if request.get("action") == "ping":
            # A transitioning peer is not retained; readiness may discover its successor.
            route = self.route if self.health is not None else CommandRoute()
            response = cli.send_command(
                request, timeout, strict_endpoint=strict_endpoint, require_endpoint=require_endpoint,
                _context=self.context, _route=route,
            )
            if (
                isinstance(response, dict) and response.get("ok") is True
                and response.get("accepting_work", True) is True
            ):
                self.health, self.route = response, route
            return response
        if self.health is None:
            health = self.send(
                {"action": "ping"}, 5.0,
                strict_endpoint=strict_endpoint, require_endpoint=require_endpoint,
            )
            if (
                not isinstance(health, dict) or health.get("ok") is not True
                or health.get("accepting_work", True) is not True
            ):
                return None
        qualified = self.source_supported(request["action"])
        outgoing = dict(request)
        if qualified:
            assert self.selection is not None
            outgoing[FIELD] = self.selection.describe()
        self.warning = UNCONFIRMED if qualified else UNSUPPORTED
        response = cli.send_command(
            outgoing, timeout, strict_endpoint=strict_endpoint, require_endpoint=require_endpoint,
            _context=self.context, _route=self.route,
        )
        warm = response.get("cache_warm") if isinstance(response, dict) else None
        if (
            qualified and acknowledged(warm) and isinstance(warm, dict)
            and warm.get("target_policy") == CACHE_TARGET_CAPABILITY
            and isinstance(warm.get("state"), str)
            and warm.get("state") in {"idle", "queued", "running", "complete"}
        ):
            self.warning = None
        return response


def command_has(args: Any) -> int:
    from . import cli

    session = ReadSession()
    with session.activate():
        if not cli.ensure_service():
            print("Error: could not start vault service", file=sys.stderr)
            return 1
        response = cli.send_command({"action": "has", "entry": args.entry}, timeout=None)
    if session.warning:
        print(f"Warning: {session.warning}", file=sys.stderr)
    if isinstance(response, dict) and response.get("ok") is True and type(response.get("exists")) is bool:
        print("true" if response["exists"] else "false")
        return 0 if response["exists"] else 1
    error = response.get("error", "invalid service response") if isinstance(response, dict) else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1


def command_query(args: Any, action: str) -> int:
    from . import cli

    request: dict[str, Any] = {"action": action}
    if action == "search":
        request["query"] = args.query
        result_field = "results"
    elif action == "list":
        request["path"] = args.path or "/"
        if args.recursive:
            request["recursive"] = True
        if args.flatten:
            request["flatten"] = True
        result_field = "entries"
    elif action == "show":
        request["entry"] = args.entry
        if args.show_protected:
            request["show_protected"] = True
        result_field = "output"
    else:
        raise ValueError("unsupported query action")
    session = ReadSession()
    with session.activate():
        if not cli.ensure_service():
            print("Error: could not start vault service", file=sys.stderr)
            return 1
        response = cli.send_command(request, timeout=None)
    if session.warning:
        print(f"Warning: {session.warning}", file=sys.stderr)
    if isinstance(response, dict) and response.get("ok") is True:
        result = response.get(result_field)
        if action == "show" and isinstance(result, str):
            print(result, end="")
            return 0
        if action != "show" and isinstance(result, list) and all(
            isinstance(value, str) for value in result
        ):
            for value in result:
                print(value)
            return 0
        error = "invalid service response"
    else:
        error = response.get("error", "invalid service response") if isinstance(response, dict) else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1
