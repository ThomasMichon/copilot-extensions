"""Selected-cell, non-provisioning transport for a one-shot PR observation."""

from __future__ import annotations

import base64
import json
import math
import re
from pathlib import PurePosixPath, PureWindowsPath
import shlex
import subprocess
from datetime import datetime

from agent_procutil.owned_process import run_owned
from machine_transport.registry import find_machine_entry, machine_identity_names

from . import config as cfg, machine_identity
from .pr_sweep import SCHEMA, VERSION, utc_now


def targets(config: cfg.Config, fleet: bool) -> list[dict]:
    local = f"{config.machine}/{config.platform}"
    selected = {
        local: {"cell": local, "local": True, "available": True, "shell": "", "alias": ""},
    }
    if fleet:
        entries = cfg.load_machines_yaml(config.default_repo.anchor, require_alias=False)
        for name, machine in entries.items():
            environments = machine.ssh_environments
            if not environments:
                platform = machine.execution_platform or ""
                environments = [cfg.SSHEnvironment(
                    name=platform, alias=machine.alias or "",
                    shell="pwsh" if platform == "windows" else "bash",
                )]
            for environment in environments:
                label = environment.name or ""
                if machine_identity.is_local_machine(name, config) and label == config.platform:
                    continue
                cell = f"{name}/{label}"
                foreign_profiles = {
                    profile.alias.casefold() for profile in machine.ssh_environments
                    if profile.name and profile.name != label
                }
                machine_names = set()
                for identity in machine_identity_names(machine, entries=entries):
                    if identity.casefold() in foreign_profiles:
                        continue
                    try:
                        resolved = find_machine_entry(
                            entries, identity, execution_platform=label or None, reject_ambiguous=True,
                        )
                    except ValueError:
                        continue
                    if resolved == machine:
                        machine_names.add(identity)
                selected[cell] = {
                    "cell": cell, "local": False,
                    "available": bool(label and machine.copilot and machine.ssh_ready and environment.alias),
                    "shell": environment.shell or ("pwsh" if label == "windows" else "bash"),
                    "alias": environment.alias or "",
                    "machine_names": sorted(machine_names),
                }
    return list(selected.values())


def select_targets(cells: list[dict], include: list[str], exclude: list[str]) -> list[dict]:
    names = {cell["cell"] for cell in cells}
    unknown = (set(include) | set(exclude)) - names
    if unknown:
        raise ValueError("Unconfigured cell selector(s): " + ", ".join(sorted(unknown)))
    return [
        {
            **cell,
            "selected": cell["cell"] not in exclude and (not include or cell["cell"] in include),
        }
        for cell in cells
    ]


def validate_command_map(commands: object, cells: list[dict]) -> dict[str, dict[str, str]]:
    if not isinstance(commands, dict):
        raise ValueError("Cell commands must be an object keyed by configured cell")
    names = {cell["cell"] for cell in cells}
    for cell, binding in commands.items():
        if cell not in names:
            raise ValueError(f"Command names an unconfigured cell: {cell}")
        environment = cell.rsplit("/", 1)[-1].lower()
        windows = environment in ("windows", "win", "win32")
        if (
            not isinstance(binding, dict) or set(binding) != {"command", "project"}
            or not isinstance(binding["project"], str)
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", binding["project"])
        ):
            raise ValueError(f"Cell {cell} needs an absolute catalog command and its own registered project")
        command = binding["command"]
        if (
            not isinstance(command, str) or not command
            or any(character in command for character in "\0\r\n")
            or not (PureWindowsPath(command).is_absolute() if windows else PurePosixPath(command).is_absolute())
        ):
            raise ValueError(f"Cell {cell} needs its attributable absolute catalog command")
    return commands


def remote_command(shell: str, command: str, project: str, repo: str) -> str:
    arguments = [
        "-p", project, "claims", "find", "pr", "--repo", repo,
        "--state", "all", "--sweep-observation", "--json",
    ]
    if shell.lower() in ("pwsh", "powershell", "pwsh.exe", "powershell.exe"):
        def quoted(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        script = (
            "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
            "$env:AGENT_WORKTREES_NO_SELFPROVISION='1';"
            f"& {quoted(command)} " + " ".join(map(quoted, arguments))
            + ";exit $LASTEXITCODE"
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return f"{shell} -NoProfile -NonInteractive -EncodedCommand {encoded}"
    if shell.lower() not in ("bash", "sh", "zsh"):
        raise ValueError("The selected cell has an unsupported shell")
    prefix = "AGENT_WORKTREES_NO_SELFPROVISION=1 "
    script = prefix + shlex.join([command, *arguments])
    return f"{shell} -lc {shlex.quote(script)}"


def validate_observation(payload: object, *, repo: str) -> dict:
    if (
        not isinstance(payload, dict) or payload.get("schema") != SCHEMA
        or type(payload.get("version")) is not int or payload["version"] != VERSION
        or not isinstance(payload.get("repo"), str) or payload["repo"].lower() != repo.lower()
        or not isinstance(payload.get("checked_utc"), str)
        or not isinstance(payload.get("cell"), str)
        or any(not isinstance(payload.get(key), list) for key in ("roots", "claims", "errors"))
    ):
        raise ValueError("The selected cell returned an invalid PR-sweep observation")
    observed = datetime.fromisoformat(payload["checked_utc"].replace("Z", "+00:00"))
    if observed.tzinfo is None:
        raise ValueError("The selected cell returned an unqualified observation timestamp")
    for claim in payload["claims"]:
        if (
            not isinstance(claim, dict)
            or any(not isinstance(claim.get(key), str) for key in (
                "project", "worktree_id", "repo", "evidence", "checked_utc", "url",
            ))
            or claim["repo"].lower() != repo.lower()
            or not isinstance(claim.get("path"), list)
            or not claim["path"] or claim["path"][0] != claim["worktree_id"]
            or any(not isinstance(part, str) for part in claim["path"])
            or claim.get("evidence") not in ("tracked-candidate", "unsettled-transitive-claim")
            or (claim.get("authority") is not None and not isinstance(claim["authority"], str))
            or (claim.get("number") is not None and type(claim["number"]) is not int)
        ):
            raise ValueError("The selected cell returned invalid claim data")
    return payload


def observe_remote(
    cell: dict, command: str, *, project: str, repo: str, timeout: float,
) -> dict:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("The cell observation timeout must be finite and positive")
    try:
        remote = remote_command(cell["shell"], command, project, repo)
    except ValueError as error:
        return {
            "cell": cell["cell"], "status": "unsupported",
            "checked_utc": utc_now(), "error": str(error),
        }
    argv = [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", f"ConnectTimeout={max(1, math.ceil(min(timeout, 10)))}",
        cell["alias"], remote,
    ]
    try:
        result = run_owned(argv, timeout=timeout)
    except (OSError, subprocess.SubprocessError, RuntimeError, UnicodeError) as error:
        return {"cell": cell["cell"], "status": "failed", "checked_utc": utc_now(), "error": str(error)}
    if result.returncode:
        return {
            "cell": cell["cell"],
            "status": "unsupported" if result.returncode == 2 and "--sweep-observation" in result.stderr else "failed",
            "checked_utc": utc_now(),
            "error": result.stderr.strip() or f"Cell read exited {result.returncode}",
        }
    try:
        payload = validate_observation(json.loads(result.stdout.lstrip("\ufeff")), repo=repo)
        reported_machine, reported_environment = payload["cell"].rsplit("/", 1)
        selected_machine, selected_environment = cell["cell"].rsplit("/", 1)
        names = cell.get("machine_names", [selected_machine])
        if reported_environment != selected_environment or reported_machine.casefold() not in {
            name.casefold() for name in names
        }:
            raise ValueError(
                f"The selected cell reported another cell identity: {payload['cell']}"
            )
    except (ValueError, TypeError) as error:
        return {"cell": cell["cell"], "status": "failed", "checked_utc": utc_now(), "error": str(error)}
    return {
        "cell": cell["cell"], "status": "ok", "checked_utc": utc_now(),
        "reported_cell": payload["cell"], "observation": payload,
    }
