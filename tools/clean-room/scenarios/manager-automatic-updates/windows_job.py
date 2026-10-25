"""Owned inner-Job caller: execute the exact Run command and leave a contained control."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True


def execute(spec: dict) -> dict:
    root = Path(spec["root"])
    slot = root / "versions" / (root / "current-version").read_text(encoding="utf-8").strip()
    library = slot / "payload/libs/agent-procutil/src"
    sys.path.insert(0, str(library))
    import agent_procutil as pu

    if Path(pu.__file__).resolve() != library / "agent_procutil/__init__.py":
        raise RuntimeError("inner Job process helper is not the selected fixture's canonical library")
    environment = dict(spec["environment"])
    control_environment = {**environment, "COPILOT_EXTENSIONS_TEST_CONTAINED": "1"}
    python, extra = pu.direct_python(sys.executable)
    control = pu.spawn_daemon(
        [python, "-I", "-S", "-B", "-c", "import time; time.sleep(900)"],
        cwd=root, log_path=root / "probe/control.log",
        env={**control_environment, **extra},
        console_children=True, breakaway=False, contained_fallback=False,
        display="contained native qualification control",
    )
    result = {"control_pid": control.pid, "gui_pid": None, "registered_command_executed": False}
    try:
        with (root / "probe/registered-command.log").open("ab") as output:
            # REG_SZ is already a complete CreateProcess command line, not a shell expression.
            bootstrap = subprocess.Popen(
                spec["registered_command"], cwd=root, env=environment, shell=False,
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                **pu.no_window_kwargs(),
            )
            result.update(
                gui_pid=bootstrap.pid, registered_command_executed=True,
                executed_command_sha256=hashlib.sha256(spec["registered_command"].encode()).hexdigest(),
                gui_returncode=bootstrap.wait(timeout=105),
            )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    try:
        result = execute(spec)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
    path = Path(spec["root"]) / "probe/job-launch.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result), encoding="utf-8")
    os.replace(temporary, path)
    return 1 if "error" in result or result.get("gui_returncode") != 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
