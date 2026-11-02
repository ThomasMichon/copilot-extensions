"""A finite launch result for a foreground shell that outlives Picker Python."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

ENVIRONMENT = "CE_TERMINAL_LAUNCH"


def prepare(script: Path, arguments: list[str], attempt_id: str | None) -> bool:
    raw = os.environ.get(ENVIRONMENT)
    if not raw:
        return False
    request = Path(raw)
    info = request.lstat()
    if (
        not request.is_absolute() or request.name != "request.json"
        or not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or info.st_size > 4096
    ):
        raise ValueError("Invalid terminal launch request")
    data = json.loads(request.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Invalid terminal launch identity")
    nonce = data.get("nonce")
    if (
        data.get("version") != 1 or type(data.get("version")) is not int
        or not isinstance(nonce, str) or len(nonce) != 32
        or any(char not in "0123456789abcdef" for char in nonce)
        or type(data.get("host_pid")) is not int or data["host_pid"] <= 0
    ):
        raise ValueError("Invalid terminal launch identity")
    from . import engine_client, launch_observations
    from .warm_picker_contract import runtime_command

    environment = {
        "WORKTREE_MANAGER_ENGINE_ARGV": json.dumps(engine_client.engine_base_command()),
        "WORKTREE_MANAGER_ROOT": os.environ.get("WORKTREE_MANAGER_ROOT"),
        "WORKTREE_NO_MUX": os.environ.get("WORKTREE_NO_MUX"),
    }
    completion = (
        runtime_command("worktree_manager.terminal_launch", attempt_id)
        if attempt_id is not None else []
    )
    launch_observations.observe(attempt_id, "launch-requested")
    result = {
        "version": 1, "nonce": nonce, "script": str(script.resolve(strict=True)),
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
