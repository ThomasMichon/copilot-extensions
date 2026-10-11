"""SSH invokes the target's own structured launch admission and normal launcher."""

from __future__ import annotations

import base64
import json
import shlex
import subprocess
import uuid

from agent_procutil import no_window_kwargs

from . import config as cfg
from .launch_request import MAX_REQUEST_BYTES


def shell_command(shell: str, argv: list[str]) -> str:
    if shell.lower() in ("pwsh", "powershell", "powershell.exe", "pwsh.exe"):
        script = "& " + " ".join("'" + part.replace("'", "''") + "'" for part in argv)
        script += "; exit $LASTEXITCODE"
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        return f"{shell} -NoProfile -EncodedCommand {encoded}"
    from machine_transport import wrap_remote_command

    if shell not in ("bash", "sh", "zsh"):
        raise ValueError("Remote prompt launch requires a supported explicit shell")
    return wrap_remote_command(shell, " ".join(shlex.quote(part) for part in argv))


def prepare(config, alias: str, shell: str, args: list[str], text: str) -> dict:
    request_id = uuid.uuid4().hex
    kind = "new" if "--new" in args else "resume"
    wid = None if kind == "new" else args[args.index("--worktree-id") + 1]
    request = {
        "version": 1, "request_id": request_id, "kind": kind,
        "worktree_id": wid, "text": text, "no_mux": "--no-mux" in args,
    }
    raw = json.dumps(request, ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("Remote prompt exceeds the launch-request transport limit")
    encoded = base64.b64encode(raw).decode("ascii")
    project = cfg.project_name()
    command = shell_command(shell, [
        project, "resolve", "--json", "--launch-request-b64", encoded,
    ])
    if len(command) > 28000:
        raise ValueError("Remote launch request exceeds the remote shell command limit")
    try:
        result = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", alias, command],
            capture_output=True, text=True, encoding="utf-8", timeout=180,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        reason = "SSH admission timed out" if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
        raise RuntimeError(
            f"Remote launch admission {request_id} is unconfirmed on {alias}: {reason}. "
            f"Do not repeat New; run {project} resolve --json "
            f"--launch-request-status {request_id} on the target."
        ) from exc
    if result.returncode:
        detail = result.stdout.strip()
        try:
            error = json.loads(detail)
        except ValueError:
            error = None
        if isinstance(error, dict) and isinstance(error.get("error"), str):
            detail = error["error"]
        detail = detail or result.stderr.strip() or str(result.returncode)
        raise RuntimeError(
            f"Remote launch admission {request_id} failed on {alias}: "
            f"{detail}. "
            f"Inspect with {project} resolve --json --launch-request-status {request_id} "
            "on the target before another New attempt."
        )
    try:
        receipt = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError(f"Remote admission {request_id} returned an invalid receipt") from exc
    if (
        not isinstance(receipt, dict) or receipt.get("version") != 1
        or receipt.get("request_id") != request_id or receipt.get("seed_id") != request_id
        or receipt.get("seed_kind") != kind
        or not isinstance(receipt.get("worktree_id"), str) or not receipt["worktree_id"]
        or (wid is not None and receipt["worktree_id"] != wid)
    ):
        raise RuntimeError(f"Remote admission {request_id} returned mismatched launch identity")
    launch_args = [project, "--worktree-id", receipt["worktree_id"],
                   "--stage-launch-seed", "--seed-id", receipt["seed_id"]]
    if request["no_mux"]:
        launch_args.append("--no-mux")
    return {
        "action": "remote", "ssh_alias": alias,
        "remote_command": shell_command(shell, launch_args),
        "worktree_id": receipt["worktree_id"], "seed_id": receipt["seed_id"],
        "seed_kind": kind, "seed_pending": True, "launch_request_id": request_id,
    }
