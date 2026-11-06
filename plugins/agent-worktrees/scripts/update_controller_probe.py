"""Run the optional project-controller probe under shell-owned custody."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main(request):
    archive = Path(__file__).parent / "installation-context" / "daemon_instance_runtime.pyz"
    sys.path.insert(0, str(archive))
    from agent_procutil import no_window_kwargs
    from agent_procutil._shell_caller_owned import serve

    def invoke():
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
        arguments = [request["host"], "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", request["caller"]]
        if request["has_project"]:
            arguments.extend(["--project", request["project"]])
        arguments.extend(["get", "update-controller"])
        return subprocess.run(
            arguments, cwd=request["cwd"], env=environment,
            stdin=subprocess.DEVNULL, check=False, **no_window_kwargs(),
        ).returncode

    return serve(invoke, **request["lifetime"])
