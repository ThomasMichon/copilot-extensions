"""Retain specialized key owners without moving their transactions or prompts."""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Callable
import json
import sys
from typing import TYPE_CHECKING, Any

from .cache_warm_client import supported
from .cache_warm_selection import KEY_ACTIONS
from .command_transport import Reply, use_key_sender
from .identity_key import IdentityKeyError
from .read_client import ReadSession

if TYPE_CHECKING:
    from .config import ResolvedVault


class KeySession(ReadSession):
    def __init__(self, operation: str) -> None:
        if operation not in KEY_ACTIONS:
            raise ValueError("unsupported key operation")
        super().__init__(strict_context=True)
        self.operation = operation

    def source_supported(self, action: str) -> bool:
        actions = self.health.get("cache_warm_selection_actions") if self.health else None
        return bool(
            action in {self.operation, "get"}
            and self.selection is not None and self.route.sender is not None
            and self.health is not None and supported(self.health)
            and isinstance(actions, list) and self.operation in actions
            and (
                self.operation != "identity-key-setup"
                or super().source_supported("get")
            )
        )

    def _source_request(self, request: dict[str, Any]) -> dict[str, Any]:
        assert self.context is not None
        outgoing = super()._source_request(request)
        # The caller already canonicalized the key. Group selects warming, not a new key.
        outgoing.update(entry="/" + request["entry"], group=self.context.group or "")
        return outgoing

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
                raise IdentityKeyError(
                    "service_unavailable",
                    "Key operations require a retained service connector; no key operation attempted.",
                )
            if self.route.host_owned_selection:
                raise IdentityKeyError(
                    "service_unavailable",
                    "Host-owned key selection cannot use the guest's key/cache context; "
                    "run the key command on the host.",
                )
        if request.get("action") in KEY_ACTIONS | {"get"}:
            if not isinstance(response, dict) or type(response.get("ok")) is not bool:
                return None
        return response


def run_key_command(
    args: Namespace, operation: str,
    command: Callable[[Namespace, ResolvedVault], int], *,
    context_error: str, context_message: str,
    validate: Callable[[Namespace], None] | None = None,
) -> int:
    session = KeySession(operation)
    try:
        if validate is not None:
            validate(args)
        with session.activate(), use_key_sender(session.send):
            assert session.context is not None
            return command(args, session.context)
    except IdentityKeyError as exc:
        payload = {"version": 1, "error": exc.code, "message": str(exc)}
    except (OSError, RuntimeError, ValueError):
        payload = {"version": 1, "error": context_error, "message": context_message}
    finally:
        if session.warning:
            print(f"Warning: {session.warning}", file=sys.stderr)
    print(json.dumps(payload, sort_keys=True))
    return 1
