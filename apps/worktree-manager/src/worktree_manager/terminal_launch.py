"""A finite launch result for a foreground shell that outlives Picker Python."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ENVIRONMENT = "CE_TERMINAL_LAUNCH"
CAPABILITY = "ce-terminal-launch-v1"
_channel = os.environ.pop(ENVIRONMENT, None)


def available() -> bool:
    return bool(_channel)


def answer_query(arguments: list[str] | None = None) -> bool:
    if (sys.argv[1:] if arguments is None else arguments) != ["--version"]:
        return False
    if os.environ.pop("CE_TERMINAL_CAPABILITY_QUERY", "") != "1":
        return False
    from .warm_picker_contract import runtime_command

    root = os.environ.get("WORKTREE_MANAGER_ROOT")
    print(json.dumps({
        "package": "worktree_manager", "capability": CAPABILITY,
        "runtime": {
            "command": runtime_command("worktree_manager"),
            "root": str(Path(root).resolve()) if root else None,
        },
    }))
    return True


def validate() -> tuple[Path, dict[str, object]] | None:
    from agent_procutil._terminal_offer import validate as validate_offer

    return validate_offer(_channel)


def validate_or_exit() -> None:
    from agent_procutil._terminal_offer import validate_or_exit as require_offer

    require_offer(_channel)


def prepare(script: Path, arguments: list[str], attempt_id: str | None) -> bool:
    validated = validate()
    if validated is None:
        return False
    request, data = validated
    from . import engine_client, launch_observations
    from .warm_picker_contract import runtime_command

    root = os.environ.get("WORKTREE_MANAGER_ROOT")
    environment = {
        "WORKTREE_MANAGER_ENGINE_ARGV": json.dumps(engine_client.engine_base_command()),
        "WORKTREE_MANAGER_ROOT": str(Path(root).resolve()) if root else None,
        "WORKTREE_NO_MUX": os.environ.get("WORKTREE_NO_MUX"),
    }
    completion = (
        runtime_command("worktree_manager.terminal_launch", attempt_id)
        if attempt_id is not None else []
    )
    launch_observations.observe(attempt_id, "launch-requested")
    result = {
        "version": 1, "nonce": data["nonce"], "script": str(script.resolve(strict=True)),
        "arguments": arguments, "cwd": str(Path.cwd()),
        "environment": environment, "completion": completion,
    }
    # Exclusive publication cannot overwrite or replay an admitted launch.
    with (request.parent / "result.json").open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False)
    return True


def main(arguments: list[str]) -> int:
    from .launch_observations import observe

    if len(arguments) != 2:
        print("Invalid terminal launch completion", file=sys.stderr)
        return 2
    try:
        code = int(arguments[1])
        observe(arguments[0], "launcher-returned", exit_code=code)
    except (ValueError, RuntimeError) as error:
        print(f"Could not retain terminal launch outcome: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
