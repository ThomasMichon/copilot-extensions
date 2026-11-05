"""Use the selected Bridge process, without importing its runtime into this venv."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

from agent_procutil import no_window_kwargs

from . import worktrees
from ._peer_launch import ContextRefused, launch_prefix


class BridgeReceiving:
    def __init__(self) -> None:
        own = worktrees.validate_context()
        self.environment = dict(os.environ)
        if own is not None:
            self.prefix = launch_prefix(
                "agent-codespaces", Path(own["pluginRoot"]),
                os.environ["COPILOT_EXTENSIONS_CONTEXT"], "agent-bridge",
            )
        else:
            binary = shutil.which("agent-bridge.ps1") if os.name == "nt" else None
            binary = binary or shutil.which("agent-bridge")
            if not binary:
                raise RuntimeError("The selected Bridge command is unavailable")
            if os.name == "nt" and Path(binary).suffix.lower() in {".cmd", ".bat"}:
                raise RuntimeError("Portable receiving requires Bridge's native PowerShell shim on Windows")
            self.prefix = [binary]
            self.environment.pop("COPILOT_PLUGIN_ROOT", None)
        self.capabilities = self.request(["receiving-support", "capabilities", "--owner"])
        mux = self.capabilities.get("mux")
        if (
            self.capabilities.get("schema") != "agent-bridge.receiving-support"
            or type(self.capabilities.get("version")) is not int or self.capabilities["version"] != 1
            or type(self.capabilities.get("owner_mux")) is not int or self.capabilities["owner_mux"] != 1
            or not isinstance(mux, dict) or type(mux.get("version")) is not int or mux["version"] != 1
            or mux.get("platform") != "posix" or mux.get("transport") != "command-stdin"
            or type(mux.get("registration")) is not int or mux["registration"] != 1
        ):
            raise RuntimeError("The selected Bridge does not advertise portable mux receiving")
        from repository_policy.receiving import receiving_marketplace

        context = self.capabilities.get("policy_context")
        if not isinstance(context, str):
            raise RuntimeError("Selected Bridge has no receiving policy context")
        self.policy_context = context
        receiving_marketplace(self.policy_context)

    def run(self, argv, **kwargs):
        """Shared venue helpers keep their semantics, but not a second peer choice."""
        arguments = [*self.prefix, *argv[1:]]
        environment = {**(kwargs.pop("env", None) or self.environment)}
        environment.pop("COPILOT_PLUGIN_ROOT", None)
        if "COPILOT_EXTENSIONS_CONTEXT" in self.environment:
            environment["COPILOT_EXTENSIONS_CONTEXT"] = self.environment["COPILOT_EXTENSIONS_CONTEXT"]
        if Path(arguments[0]).suffix.lower() == ".ps1":
            # PS5 native argv serialization loses quoted JSON. Keep literal
            # arguments in a child-only environment; stdin remains the payload.
            environment["AGENT_CODESPACES_BRIDGE_ARGV"] = json.dumps(arguments)
            host = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            arguments = [
                str(host), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command",
                "$a=ConvertFrom-Json $env:AGENT_CODESPACES_BRIDGE_ARGV; "
                "$p=$a[0]; $rest=@($a | Select-Object -Skip 1); "
                "& $p @rest; exit $LASTEXITCODE",
            ]
        for key, value in no_window_kwargs().items():
            kwargs[key] = kwargs.get(key, 0) | value
        result = subprocess.run(arguments, env=environment, **kwargs)
        if result.returncode == 126:
            raise ContextRefused("Selected Bridge installation context refused")
        return result

    def request(self, arguments: list[str], payload: dict | None = None) -> dict:
        result = self.run(
            ["agent-bridge", *arguments],
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True, text=True, timeout=90,
        )
        if result.returncode:
            # A receiving payload may contain credentials. Never reflect its
            # command output or a subprocess exception's input in diagnostics.
            raise RuntimeError("Selected Bridge receiving request failed")
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("Selected Bridge returned invalid receiving metadata") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Selected Bridge returned no receiving object")
        return value

    def launch(self, request: dict) -> tuple[str, bytes]:
        if request.get("ensure_mux") and self.capabilities["mux"].get("ensure_mux") != 1:
            raise RuntimeError("Selected Bridge cannot prepare the native mux prerequisite")
        if request.get("driven_by") and self.capabilities["mux"].get("driven_by") != 1:
            raise RuntimeError("Selected Bridge cannot retain the session driver label")
        value = self.request(["receiving-support", "mux-command"], request)
        if (
            value.get("schema") != "agent-bridge.receiving-command"
            or type(value.get("version")) is not int or value["version"] != 1
            or not isinstance(value.get("command"), str) or not value["command"]
            or not isinstance(value.get("stdin_base64"), str)
        ):
            raise RuntimeError("Selected Bridge returned an unsupported receiving command")
        return value["command"], base64.b64decode(value["stdin_base64"], validate=True)
