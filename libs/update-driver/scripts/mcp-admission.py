"""Batch MCP's read-only publication/source inspection under one caller owner."""

from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys
from typing import Any


def main(request: dict[str, Any]) -> int:
    directory = Path(request["payload"]) / "scripts" / "installation-context"
    archive = directory / "daemon_instance_runtime.pyz"
    if not archive.is_file():
        raise RuntimeError("The owning payload caller-lifetime source archive is unavailable.")
    sys.path.insert(0, str(archive))
    from agent_procutil._shell_caller_owned import serve

    def inspect() -> int:
        sdk = runpy.run_path(str(directory / "installation_context.py"))
        arguments = {
            "payload_root": request["payload"], "plugin_id": "agent-mcp",
            "project_root": request["project"],
        }
        admission = sdk["resolve_runtime_deployment"](
            **arguments, legacy_root=request["runtime"],
        )
        if (
            admission["status"] != "ready" or admission["actualMode"] != "legacy"
            or admission["deployment"]["allowPluginPublication"] is not True
        ):
            raise RuntimeError(
                "Owning installation refused first-use publication: "
                + admission["deployment"]["reason"],
            )
        provenance = sdk["resolve_payload_source"](**arguments)
        print(json.dumps({"admission": admission, "provenance": provenance}))
        return 0

    return serve(inspect, **request["lifetime"])
