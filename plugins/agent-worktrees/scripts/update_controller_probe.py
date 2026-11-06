"""Run the optional project-controller probe under shell-owned custody."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _activate_helpers():
    archive = Path(__file__).parent / "installation-context" / "daemon_instance_runtime.pyz"
    sys.path.insert(0, str(archive))


def _environment():
    environment = dict(os.environ)
    for name in (
        "PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "VIRTUAL_ENV",
        "UV_INTERNAL__PYTHONHOME", "__PYVENV_LAUNCHER__", "AGENT_RT_ROOT",
        "AGENT_WORKTREES_INTERNAL_SKIP_UPDATE_BOOTSTRAP",
        "AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER",
    ):
        environment.pop(name, None)
    environment["AGENT_WORKTREES_NO_SELFPROVISION"] = "1"
    environment["AGENT_WORKTREES_INTERNAL_REQUIRE_RUNTIME"] = "1"
    return environment


def main(request):
    _activate_helpers()
    from agent_procutil import no_window_kwargs
    from agent_procutil._shell_caller_owned import serve

    def invoke():
        arguments = [request["host"], "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", request["caller"]]
        if request["has_project"]:
            arguments.extend(["--project", request["project"]])
        arguments.extend(["get", "update-controller"])
        return subprocess.run(
            arguments, cwd=request["cwd"], env=_environment(),
            stdin=subprocess.DEVNULL, check=False, **no_window_kwargs(),
        ).returncode

    return serve(invoke, **request["lifetime"])


def posix_main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--caller", required=True)
    parser.add_argument("--project")
    parser.add_argument("--timeout", type=int, required=True)
    args = parser.parse_args()
    _activate_helpers()
    from agent_procutil.owned_process import OutputLimitExceeded, run_caller_owned

    command = ["bash", args.caller]
    if args.project is not None:
        command.extend(["--project", args.project])
    command.extend(["get", "update-controller"])
    try:
        result = run_caller_owned(
            command, timeout=args.timeout, env=_environment(),
            max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
    except subprocess.TimeoutExpired:
        print("[agent-worktrees] controller probe timed out; no update ran.", file=sys.stderr)
        return 124
    except (OSError, OutputLimitExceeded) as error:
        print(f"[agent-worktrees] controller probe failed: {error}", file=sys.stderr)
        return 125
    if result.returncode in (124, 125):
        return result.returncode
    if result.returncode:
        return 10
    sys.stdout.write(result.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(posix_main())
