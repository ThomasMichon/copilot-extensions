"""Discover native setup support on the selected runtime, without admission."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

from agent_procutil.owned_process import run_caller_owned


def supports_posix_setup(root: Path) -> bool:
    try:
        version = (root / "current-version").read_text(encoding="utf-8").strip()
        if not root.is_absolute() or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,255}", version) is None:
            return False
        slot = root / "versions" / version
        python = slot / "bin" / "python"
        bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
        if not python.is_file() or not bootstrap.is_file() or not (slot / ".install-complete.json").is_file():
            return False
        environment = {
            key: value for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"}
        }
        result = run_caller_owned(
            [str(python), "-I", "-S", "-B", "-X", "utf8", str(bootstrap),
             "-m", "agent_worktrees.launch_seed_native", "--describe-contract"],
            env=environment, timeout=10, max_stdout_bytes=65536, max_stderr_bytes=4096,
        )
        descriptor = json.loads(result.stdout)
        return (
            result.returncode == 0
            and isinstance(descriptor, dict)
            and descriptor.get("contract") == "agent-worktrees.native-seed"
            and type(descriptor.get("version")) is int and descriptor["version"] == 1
            and descriptor.get("nul_reply_schema") == "agent-worktrees.native-seed-reply.v1"
            and all(
                isinstance(descriptor.get(key), list) and value in descriptor[key]
                for key, value in (
                    ("preparation_output_formats", "nul"),
                    ("setup_process_start_time_sources", "provider"),
                    ("backend_execution_modes", "posix-exec-env-v1"),
                    ("preparation_environment_modes", "execution-hint"),
                )
            )
            and (root / "current-version").read_text(encoding="utf-8").strip() == version
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return False
