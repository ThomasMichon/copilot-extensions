"""Stdlib-only MCP-start snapshot, embedded in the anonymous client overlay."""

import json
import os
from pathlib import Path
import subprocess
import sys

ENTRY_CAPABILITY = "optional-physical-owner-entry-v1"
ENVIRONMENT_KEYS = (
    "AGENT_MCP_HOME", "AGENT_MCP_MARKETPLACE_ROOT", "COPILOT_EXTENSIONS_CONTEXT",
    "AGENT_MCP_PAYLOAD_ROOT", "COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN",
    "COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS",
    "AGENT_RT_ROOT", "PYTHONUTF8", "PYTHONPATH", "PYTHONHOME",
)


def receiver(report):
    """Accept an optional entry declaration, never infer it from pair support."""
    contract = report.get("contract", {})
    entry = report.get("entry", {})
    if not (
        report.get("supported") is True and report.get("invocation_allowed") is True
        and contract.get("name") == "agent-mcp.session-owner-handoff"
        and type(contract.get("minimum")) is int and contract["minimum"] == 1
        and type(contract.get("maximum")) is int and contract["maximum"] >= 1
        and isinstance(contract.get("capabilities"), list)
        and ENTRY_CAPABILITY in contract["capabilities"]
        and isinstance(entry, dict)
        and isinstance(entry.get("environment"), dict)
        and set(entry.get("environment", {})) == set(ENVIRONMENT_KEYS)
    ):
        return None
    for key in ("python", "prefix", "module", "cwd"):
        value = entry.get(key)
        if not isinstance(value, str) or "\0" in value or not Path(value).is_absolute():
            return None
    if not Path(entry["module"]).is_relative_to(Path(entry["prefix"])):
        return None
    if any(
        value is not None and (not isinstance(value, str) or "\0" in value)
        for value in entry["environment"].values()
    ):
        return None
    return entry


def start(request):
    """Probe only at MCP start, then exec one physical receiver or the baseline."""
    if not sys.platform.startswith("linux"):
        raise ValueError("Anonymous owner entry bootstrap requires Linux")
    original = request["original"]
    environment = os.environ.copy()
    try:
        result = subprocess.run(
            request["query"], env={**environment, "AGENT_MCP_NO_SELFPROVISION": "1"},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
        )
        report = json.loads(result.stdout)
        entry = receiver(report) if result.returncode == 0 else None
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        entry = None
    if entry is None:
        os.execvpe(original[0], original, environment)
    else:
        request["receiver"] = entry
        request["selection"] = report.get("selection")
        command = [
            entry["python"], "-I", "-m", "agent_mcp", "session-owner-entry",
            json.dumps(request, ensure_ascii=True),
        ]
        try:
            os.execve(command[0], command, environment)
        except OSError:
            # A failed exec accepted no application bytes.
            os.execvpe(original[0], original, environment)
    raise RuntimeError("MCP entry exec unexpectedly returned")


if __name__ == "__main__":
    start(json.loads(sys.argv[1]))
