"""Native command construction and optional locked-worker read dispatch."""

from __future__ import annotations

from collections.abc import Callable
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Any, NoReturn, cast


def command(agent_worktrees_path: str, args: list[str]) -> list[str]:
    if os.name == "nt" and Path(agent_worktrees_path).suffix.casefold() == ".ps1":
        host = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not host:
            return []
        return [
            host, "-NoProfile", "-NoLogo", "-ExecutionPolicy", "Bypass",
            "-File", agent_worktrees_path, *args,
        ]
    return [agent_worktrees_path, *args]


def run(
    agent_worktrees_path: str, args: list[str], *, cwd: str = "", noninteractive: bool = False,
    reader: object = None,
    command_builder: Callable[[str, list[str]], list[str]] = command,
) -> subprocess.CompletedProcess[str] | None:
    if not agent_worktrees_path:
        return None
    if callable(reader):
        native_read = cast(Callable[..., subprocess.CompletedProcess[str] | None], reader)
        result = native_read(agent_worktrees_path, args, cwd=cwd, noninteractive=noninteractive)
        if result is not None:
            return result
    argv = command_builder(agent_worktrees_path, args)
    if not argv:
        return None
    try:
        return subprocess.run(
            argv, cwd=cwd or None, capture_output=True, text=True,
            stdin=subprocess.DEVNULL if noninteractive else None, timeout=120,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def registration_argv(
    knowledge: str, knowledge_path: str, *, remote: str, default_branch: str,
    account: str, repo_class: str = "knowledge",
) -> list[str]:
    argv = ["repos", "add", knowledge, knowledge_path, "--class", repo_class]
    for flag, value in (("--remote", remote), ("--default-branch", default_branch), ("--account", account)):
        if value:
            argv.extend([flag, value])
    return argv


def render_command(argv: list[str]) -> str:
    if os.name == "nt":
        return "& " + " ".join("'" + value.replace("'", "''") + "'" for value in argv)
    return shlex.join(argv)


def preflight(
    agent_worktrees_path: str, knowledge: str, *,
    runner: Callable[[str, list[str]], subprocess.CompletedProcess[str] | None],
) -> tuple[int, dict[str, Any]]:
    contract = "agent-worktrees.repository-registration-preflight"
    unsupported = {
        "contract": contract, "version": 1, "status": "unsupported",
        "landing": {"status": "not-observed", "mode": "unknown"},
        "next_step": "The selected native owner has no qualified policy read; independently valid local binding is unchanged.",
    }
    inventory = runner(agent_worktrees_path, ["repos", "list", "--json"])
    if inventory is None or inventory.returncode:
        return 2, {**unsupported, "status": "failed", "code": "inventory-unavailable"}
    try:
        payload = json.loads(inventory.stdout)
        if not isinstance(payload, dict):
            raise ValueError
        capabilities = payload.get("capabilities")
        supported = capabilities.get("repository_registration_preflight") if isinstance(capabilities, dict) else None
    except (ValueError, TypeError):
        return 2, {**unsupported, "status": "failed", "code": "invalid-inventory"}
    if type(supported) is not int or supported != 1:
        return 0, unsupported
    observed = runner(agent_worktrees_path, [
        "repos", "registration", "preflight", "--repository", knowledge,
    ])
    if observed is None or observed.returncode:
        return 2, {**unsupported, "status": "failed", "code": "native-observation-unavailable"}
    try:
        value = json.loads(observed.stdout)
        registration = value.get("registration") if isinstance(value, dict) else None
        landing = value.get("landing") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict) or value.get("contract") != contract
            or type(value.get("version")) is not int or value["version"] != 1
            or value.get("status") not in {"observed", "partial"}
            or not isinstance(registration, dict) or registration.get("name") != knowledge
            or not isinstance(landing, dict) or landing.get("mode") not in {"direct", "pull-request", "unknown"}
        ):
            raise ValueError
    except (ValueError, TypeError):
        return 2, {**unsupported, "status": "failed", "code": "invalid-native-observation"}
    return 0, value


def preflight_command(
    args: argparse.Namespace, *, error: Callable[[str], NoReturn],
    runner: Callable[[str, list[str]], subprocess.CompletedProcess[str] | None],
) -> int:
    if (
        not args.knowledge or not args.agent_worktrees_path
        or any((args.capabilities, args.harness, args.register, args.configuration,
                args.control_plane, args.preview_token, args.account,
                args.harness_path, args.knowledge_path, args.home, args.product))
    ):
        error("--preflight takes only --knowledge, --agent-worktrees-path and optional --json")
    code, result = preflight(args.agent_worktrees_path, args.knowledge, runner=runner)
    print(json.dumps(result, indent=2))
    return code
