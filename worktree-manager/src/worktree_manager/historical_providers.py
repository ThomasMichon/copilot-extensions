"""Optional cleanup adapters use selected native providers without provisioning."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


class WorktreesDaemons:
    def __init__(self):
        from . import engine_client

        self.engine = engine_client
        self.command = engine_client.installed_engine_command()
        if self.command is None:
            raise ValueError("selected Worktrees runtime is unavailable; cleanup never provisions it")
        self.support = self._request("capabilities")
        if (self.support.get("detector") != "worktrees-monitor-control-v1"
            or type(self.support.get("revision")) is not int or self.support["revision"] != 1
            or self.support.get("inspect") is not True):
            raise ValueError("selected Worktrees runtime does not support historical cleanup v1")

    def _request(self, operation, findings=None):
        arguments = ["status-monitor-cleanup", operation, "--json"]
        runner = None
        if findings is not None:
            raw = json.dumps(findings, separators=(",", ":"), allow_nan=False)
            if len(raw.encode("utf-8")) > 262144 or not 0 < len(findings) <= 16:
                raise ValueError("native historical monitor selection exceeds its bound")
            arguments.extend(["--request", "-"])
            def run(command, timeout):
                return subprocess.run(
                    command, input=raw, capture_output=True, text=True, timeout=timeout,
                    env=self.engine._engine_environment(), check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            runner = run
        result = self.engine.run_json(
            None, arguments, engine_command=self.command, runner=runner, allow_nonzero=True,
            timeout=30 if findings is None else 30 + 90 * len(findings),
        )
        if len(json.dumps(result).encode("utf-8")) > 262144:
            raise ValueError("native historical monitor response exceeds its bound")
        return result

    def inspect(self, root, bin_dir):
        result = self._request("inspect")
        if not isinstance(result.get("findings"), list):
            raise ValueError(result.get("reason", "native monitor inventory is unavailable"))
        return result["findings"]

    def apply_many(self, root, bin_dir, findings):
        if self.support.get("apply") is not True:
            return [{"resource": item["resource"], "status": "preserved",
                     "reason": "selected native provider cannot pin processes on this platform"}
                    for item in findings]
        result = self._request("apply", findings)
        if not isinstance(result.get("actions"), list) or len(result["actions"]) != len(findings):
            raise ValueError(result.get("reason", "native monitor retirement outcomes are unconfirmed"))
        return result["actions"]


class IndexRegistrations:
    def __init__(self, selection_path):
        from . import cleanup_cli

        self.selection = cleanup_cli._bounded_json(Path(selection_path))
        if not isinstance(self.selection, dict):
            raise ValueError("Index selection must be a current installation-context object")
        for field in ("payload_root", "context", "durable_home"):
            value = self.selection.get(field)
            if (not isinstance(value, str) or not value.strip() or "\0" in value
                    or not Path(value).is_absolute()):
                raise ValueError(f"Index selection {field} must be an absolute path string")
        identity = self.selection.get("marketplace_id")
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("Index selection marketplace_id must be a nonempty identity string")
        for field in ("namespace_generation", "install_generation", "activation_generation"):
            value = self.selection.get(field)
            if type(value) is not int or value < 1:
                raise ValueError(f"Index selection {field} must be a positive integer")
        token = self.selection.get("maintenance_token")
        if token is not None and (not isinstance(token, str) or not token.strip()):
            raise ValueError("Index selection maintenance_token must be a nonempty string or null")

    def inspect(self, root, bin_dir):
        from . import historical_registrations

        return historical_registrations.inspect(root, self.selection)

    def apply(self, root, bin_dir, finding):
        from . import historical_registrations

        return historical_registrations.apply(root, self.selection, finding)
