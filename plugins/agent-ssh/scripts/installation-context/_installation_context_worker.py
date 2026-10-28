"""Private PowerShell adapter worker; lifetime belongs to agent-procutil."""

from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys


def main(request: dict) -> int:
    archive = Path(__file__).with_name("daemon_instance_runtime.pyz")
    if not archive.is_file():
        raise RuntimeError("the owning payload caller-lifetime source archive is unavailable")
    sys.path.insert(0, str(archive))
    from agent_procutil._shell_caller_owned import serve

    def invoke() -> int:
        os.chdir(request["processDirectory"])
        os.environ.update(request["environment"])
        sdk = runpy.run_path(request["sdk"], run_name="_installation_context_sdk")
        return sdk["_powershell_main"](
            request["action"], request["parameters"], request["supplied"],
        )

    return serve(invoke, **request["lifetime"])
