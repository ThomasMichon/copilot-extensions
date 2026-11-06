"""Finite read-only CLI composition over the existing authenticated workspace."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import run_caller_owned

from .review_workspace import MAX_GENERATION_BYTES
from .watch_replay_store import json_object
from .workspace_tools import FIELDS, WorkspaceTools, WorkspaceToolUnavailable

READ_COMMANDS = ("capabilities", "load", "sync", "materialize", "events")
INVOCATION_SECONDS = 60
OUTPUT_BYTES = 6 * MAX_GENERATION_BYTES + 4096


class _ObserverTools(WorkspaceTools):
    async def capabilities(self, *, operation=None):
        value = await super().capabilities(operation=operation)
        value["operations"] = [name for name in value["operations"] if name in READ_COMMANDS]
        if value["backend_contract"] is not None:
            backend = value["backend_contract"]
            backend["operations"] = [
                name for name in backend["operations"]
                if name in ("capabilities", "load", "sync", "lookup", "chunk", "events")
            ]
        return value

    async def invoke(self, command, request):
        if command not in READ_COMMANDS:
            raise PermissionError("read-only workspace client")
        return await super().invoke(command, request)


async def _observe(profile, command, request, remaining):
    from fleet_manager.credential_references import load_service_credentials
    from fleet_manager.gateway_service_caller import ServiceCaller
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
        async def request(self, *args, **kwargs):
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
    return await _ObserverTools(client).for_one_shot().invoke(command, request)


def _failure(error):
    return {"error": error, "effect_completion": "not_inferred"}


def _execute(selection, remaining):
    try:
        if selection["owner_conflict"] or selection["command"] not in READ_COMMANDS:
            raise PermissionError("read-only workspace client selection refused")
        request = {}
        if selection["command"] != "capabilities":
            remaining()
            if selection["request"] == "-":
                raw = sys.stdin.buffer.read(MAX_GENERATION_BYTES + 1)
            else:
                with Path(selection["request"]).open("rb") as stream:
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


def run(args, *, host=None):
    budget = OwnedExecutionBudget.from_timeout(INVOCATION_SECONDS, cleanup_reserve=5)
    if host is not None:
        return 4
    selection = {
        "profile": args.client_profile, "command": args.workspace_command,
        "request": getattr(args, "request", None), "owner_conflict": host is not None,
    }
    try:
        result = run_caller_owned(
            [sys.executable, "-m", __name__, repr(budget.execution_deadline),
             json.dumps(selection, allow_nan=False)],
            timeout=INVOCATION_SECONDS, budget=budget, capture=False,
            stdin_stream=sys.stdin.buffer if selection["request"] == "-" else None,
            stdout_stream=sys.stdout.buffer,
            max_stdout_bytes=OUTPUT_BYTES, max_stderr_bytes=65536,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        # Output may already be partial. A second JSON frame would fabricate delivery.
        return 1


def _main():
    deadline = float(sys.argv[1])
    selection = json.loads(sys.argv[2])

    def remaining():
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
