"""Finite read-only CLI composition over the existing authenticated workspace."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, cast

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import run_caller_owned

from .review_workspace import MAX_GENERATION_BYTES
from .watch_replay_store import json_object
from .workspace_tools import FIELDS, WorkspaceTools, WorkspaceToolUnavailable
from .workspace_cli import _ToolView

if TYPE_CHECKING:
    from .workspace_client import WorkspaceClient

READ_COMMANDS = ("capabilities", "load", "sync", "materialize", "events")
INVOCATION_SECONDS = 60
OUTPUT_BYTES = 6 * MAX_GENERATION_BYTES + 4096


class _Arguments(Protocol):
    client_profile: str
    workspace_command: str


class _Selection(TypedDict):
    profile: str
    command: str
    request: str | None
    owner_conflict: bool


class _ObserverTools(WorkspaceTools):
    def __init__(self, client: WorkspaceClient) -> None:
        initialize: Callable[[WorkspaceClient], None] = super().__init__
        initialize(client)

    async def capabilities(self, *, operation: Any = None) -> dict[str, Any]:
        value = await cast(_ToolView, super()).capabilities(operation=operation)
        value["operations"] = [name for name in value["operations"] if name in READ_COMMANDS]
        if value["backend_contract"] is not None:
            backend = value["backend_contract"]
            backend["operations"] = [
                name for name in backend["operations"]
                if name in ("capabilities", "load", "sync", "lookup", "chunk", "events")
            ]
        return value

    async def invoke(self, command: str, request: dict[str, Any]) -> dict[str, Any]:
        if command not in READ_COMMANDS:
            raise PermissionError("read-only workspace client")
        return await cast(_ToolView, super()).invoke(command, request)


async def _observe(
    profile: str, command: str, request: dict[str, Any], remaining: Callable[[], float],
) -> dict[str, Any]:
    from fleet_manager.credential_references import load_service_credentials
    from fleet_manager.gateway_service_caller import ServiceCaller, ServiceResponse
    from fleet_manager.gateway_service_channel import DEADLINE
    from .workspace_client import WorkspaceClient
    from .workspace_client_profile import read_profile

    selected, binding, scope = read_profile(profile, remaining)
    remaining()
    credentials = load_service_credentials(
        proof_key_file=Path(selected["proof_key_file"]),
        access_token_file=Path(selected["access_token_file"]),
    )

    class InvocationCaller(ServiceCaller):
        async def request(self, *args: Any, **kwargs: Any) -> ServiceResponse:
            self._timeout = min(self._timeout, remaining())
            return await super().request(*args, **kwargs)

    remaining()
    caller = InvocationCaller(
        gateway_origin=selected["gateway_origin"], proof_key=credentials.proof_key,
        access_token_callback=credentials.access_token, gateway_ca=selected["gateway_ca"],
        timeout=min(DEADLINE, remaining()),
    )
    client = WorkspaceClient(
        caller, route_prefix=selected["route_prefix"], binding=binding, scope=scope,
    )
    return await cast(_ToolView, _ObserverTools(client)).for_one_shot().invoke(command, request)


def _failure(error: str) -> dict[str, str]:
    return {"error": error, "effect_completion": "not_inferred"}


def _execute(
    selection: _Selection, remaining: Callable[[], float],
) -> tuple[int, dict[str, Any]]:
    try:
        if selection["owner_conflict"] or selection["command"] not in READ_COMMANDS:
            raise PermissionError("read-only workspace client selection refused")
        request = {}
        if selection["command"] != "capabilities":
            remaining()
            request_path = selection["request"]
            if request_path is None:
                raise ValueError("workspace request is required")
            if request_path == "-":
                raw = sys.stdin.buffer.read(MAX_GENERATION_BYTES + 1)
            else:
                with Path(request_path).open("rb") as stream:
                    raw = stream.read(MAX_GENERATION_BYTES + 1)
            if len(raw) > MAX_GENERATION_BYTES:
                raise ValueError("workspace request limit")
            request = json_object(raw)
        if set(request) != set(FIELDS[selection["command"]]):
            raise ValueError("invalid workspace tool request fields")
        remaining()
        result = asyncio.run(_observe(selection["profile"], selection["command"], request, remaining))
        remaining()
        return 0, result
    except PermissionError:
        return 4, _failure("workspace_owner_refused")
    except WorkspaceToolUnavailable:
        return 3, _failure("workspace_owner_or_operation_unavailable")
    except ImportError:
        return 3, _failure("workspace_client_dependencies_unavailable")
    except TimeoutError:
        return 1, _failure("workspace_invocation_deadline")
    except Exception as error:
        from agent_procutil.owned_process import OwnedCleanupError
        if isinstance(error, OwnedCleanupError):
            raise
        return 1, _failure("workspace_input_or_result_unavailable")


def run(args: _Arguments, *, host: object | None = None) -> int:
    budget = OwnedExecutionBudget.from_timeout(INVOCATION_SECONDS, cleanup_reserve=5)
    if host is not None:
        return 4
    selection: _Selection = {
        "profile": args.client_profile, "command": args.workspace_command,
        "request": getattr(args, "request", None), "owner_conflict": host is not None,
    }
    from agent_procutil._native_types import DescriptorStream
    from ._process_entry import streams
    incoming, outgoing = streams()
    try:
        result = run_caller_owned(
            [sys.executable, "-I", "-m", __name__, repr(budget.execution_deadline),
             json.dumps(selection, allow_nan=False)],
            timeout=INVOCATION_SECONDS, budget=budget, capture=False,
            stdin_stream=cast(DescriptorStream, incoming)
            if selection["request"] == "-" else None,
            stdout_stream=cast(DescriptorStream, outgoing),
            max_stdout_bytes=OUTPUT_BYTES, max_stderr_bytes=65536,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        # Output may already be partial. A second JSON frame would fabricate delivery.
        return 1
    except ValueError:
        return 4


def _main() -> int:
    deadline = float(sys.argv[1])
    selection = json.loads(sys.argv[2])

    def remaining() -> float:
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError("workspace invocation deadline")
        return seconds

    remaining()
    code, value = _execute(selection, remaining)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii") + b"\n"
    if len(raw) > OUTPUT_BYTES:
        code = 1
        raw = json.dumps(_failure("workspace_input_or_result_unavailable")).encode("ascii") + b"\n"
    remaining()
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()
    return code


if __name__ == "__main__":
    raise SystemExit(_main())
