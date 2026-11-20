"""Foreground-only terminal offer and actual selected Manager admission."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

CAPABILITY = "ce-terminal-launch-v1"
ENVIRONMENT = "CE_TERMINAL_LAUNCH"
QUERY_ENVIRONMENT = "CE_TERMINAL_CAPABILITY_QUERY"
_channel = os.environ.pop(ENVIRONMENT, None)


class _UnsafeBatchArguments(ValueError):
    pass


def _batch_probe_command(arguments: list[str], environment: dict[str, str]) -> list[str]:
    from plugin_resolve.execution import windows_batch_command

    # Native argv quoting is not cmd syntax escaping or batch-parameter
    # quoting (which also splits comma/semicolon/equals). Spaced trailing
    # backslashes are doubled by native quoting, which cmd does not undo.
    if any(
        any(character in '&|<>^()%!",;=' or ord(character) < 32 or ord(character) == 127
            for character in argument)
        or (" " in argument and argument.endswith("\\"))
        for argument in arguments[1:]
    ):
        raise _UnsafeBatchArguments(
            "Selected Manager batch prefix cannot be passed literally; "
            "use a non-batch provider command. No launch was admitted."
        )
    return windows_batch_command(arguments, environment)


def available() -> bool:
    return bool(_channel)


def validate_or_exit() -> None:
    from agent_procutil._terminal_offer import validate_or_exit as require_offer

    require_offer(_channel)


def exit_for_query(arguments: list[str] | None = None) -> None:
    if (sys.argv[1:] if arguments is None else arguments) != ["--version"]:
        return
    if os.environ.pop(QUERY_ENVIRONMENT, "") != "1":
        return
    print(json.dumps({"package": "agent_worktrees", "capability": CAPABILITY}))
    raise SystemExit(0)


def select_receiver(
    command: list[str], environment: dict[str, str], *, timeout: float | None = 10,
) -> tuple[list[str], dict[str, str]]:
    environment = dict(environment)
    environment.pop(ENVIRONMENT, None)
    if not _channel or os.name != "nt":
        return list(command), environment
    validate_or_exit()
    selector = command[-1:] if command[-1:] == ["--resident-picker"] else []
    query_command = command[:-1] if selector else command
    from agent_procutil.owned_process import require_caller_owned_support, run_caller_owned

    query_environment = {
        **environment, QUERY_ENVIRONMENT: "1",
        "UV_NO_SYNC": "1", "UV_OFFLINE": "1", "WORKTREE_NO_UPDATE": "1",
        "AGENT_WORKTREES_NO_SELFPROVISION": "1",
    }
    query_arguments = [*query_command, "--version"]
    if query_arguments[0].lower().endswith((".cmd", ".bat")):
        query_arguments = _batch_probe_command(query_arguments, query_environment)
    try:
        require_caller_owned_support()
    except (OSError, RuntimeError):
        raise RuntimeError(
            "Selected Manager terminal capability query requires unavailable caller-owned "
            "process support; no launch was admitted"
        ) from None
    try:
        result = run_caller_owned(
            query_arguments, env=query_environment, timeout=timeout,
            max_stdout_bytes=4096, max_stderr_bytes=4096,
        )
    except UnicodeError:
        raise RuntimeError(
            "Selected Manager terminal capability query returned non-UTF-8 output; "
            "no launch was admitted"
        ) from None
    if result.returncode != 0 or result.stderr:
        raise RuntimeError("Selected Manager terminal capability query failed; no launch was admitted")
    text = result.stdout.strip()
    if re.fullmatch(r"worktree-manager [0-9]+\.[0-9]+\.[0-9]+(?:-dev[0-9]+)?", text):
        return list(command), environment
    try:
        response = json.loads(text)
    except ValueError as error:
        raise RuntimeError("Selected Manager returned an invalid terminal capability") from error
    if (
        not isinstance(response, dict)
        or set(response) != {"package", "capability", "runtime"}
        or response["package"] != "worktree_manager" or response["capability"] != CAPABILITY
    ):
        raise RuntimeError("Selected Manager returned an invalid terminal capability")
    runtime = response["runtime"]
    if not isinstance(runtime, dict) or set(runtime) != {"command", "root"}:
        raise RuntimeError("Selected Manager returned an invalid runtime binding")
    selected, root = runtime["command"], runtime["root"]
    if (
        not isinstance(selected, list) or len(selected) not in (7, 9)
        or any(not isinstance(part, str) or not part or "\0" in part for part in selected)
        or selected[1:5] != ["-I", "-B", "-X", "utf8"]
        or selected[-2:] != ["-m", "worktree_manager"]
        or not Path(selected[0]).is_absolute() or not Path(selected[0]).is_file()
        or (root is not None and (
            not isinstance(root, str) or not root or "\0" in root or not Path(root).is_absolute()
        ))
    ):
        raise RuntimeError("Selected Manager returned an invalid runtime binding")
    if len(selected) == 9:
        bootstrap = Path(selected[6])
        slot = bootstrap.parent.parent
        if (
            selected[5] != "-S" or not bootstrap.is_absolute() or not bootstrap.is_file()
            or bootstrap.name != "runtime_launcher.py"
            or bootstrap.parent.name != ".runtime-bootstrap" or slot.parent.name != "versions"
            or not (slot / ".install-complete.json").is_file()
        ):
            raise RuntimeError("Selected Manager runtime is no longer available")
    if root is None:
        environment.pop("WORKTREE_MANAGER_ROOT", None)
    else:
        environment["WORKTREE_MANAGER_ROOT"] = root
    environment[ENVIRONMENT] = _channel
    return [*selected, *selector], environment
