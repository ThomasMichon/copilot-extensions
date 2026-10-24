"""SSH invokes the target's own structured launch admission and normal launcher."""

from __future__ import annotations

import base64
import json
import re
import shlex
import subprocess
import uuid

from agent_procutil import no_window_kwargs

from . import config as cfg
from .launch_attempt import identity
from .launch_request import MAX_REQUEST_BYTES

MAX_REMOTE_COMMAND_CHARS = 28000


def _unconfirmed_admission(project: str, alias: str, request_id: str, kind: str,
                           reason: str) -> RuntimeError:
    return RuntimeError(
        f"Remote launch admission {request_id} is unconfirmed on {alias}: {reason}. "
        f"Do not resubmit this {kind} prompt; run {project} resolve --json "
        f"--launch-request-status {request_id} on the target."
    )


def shell_command(shell: str, argv: list[str]) -> str:
    shell = shell.lower()
    if shell in ("pwsh", "powershell", "powershell.exe", "pwsh.exe"):
        script = "& " + " ".join("'" + part.replace("'", "''") + "'" for part in argv)
        script += "; exit $LASTEXITCODE"
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        return f"{shell} -NoProfile -EncodedCommand {encoded}"
    from machine_transport import wrap_remote_command

    if shell not in ("bash", "sh", "zsh"):
        raise ValueError("Remote prompt launch requires a supported explicit shell")
    return wrap_remote_command(shell, " ".join(shlex.quote(part) for part in argv))


def prepare(config, alias: str, shell: str, args: list[str], text: str) -> dict:
    new = "--new" in args
    existing = "--worktree-id" in args
    if new == existing or (existing and args.index("--worktree-id") + 1 >= len(args)):
        raise ValueError("Remote prompt requires exactly one New or existing worktree selector")
    selector_end = args.index("--worktree-id") + 1 if existing else -1
    native_required = "--require-native-seed" in args
    attempt_options = [arg for index, arg in enumerate(args)
                       if index != selector_end and arg.startswith("--launch-attempt-id=")]
    if len(attempt_options) > 1:
        raise ValueError("Duplicate launch attempt identity")
    attempt_id = attempt_options[0].split("=", 1)[1] if attempt_options else None
    if attempt_id is not None and (
        not native_required or re.fullmatch(r"[0-9a-f]{32}", attempt_id) is None
    ):
        raise ValueError("Launch attempt identity requires negotiated native seed admission")
    if "--require-launch-attempt" in args:
        identity(attempt_id)
    for index, arg in enumerate(args):
        if index == selector_end or arg in (
            "--new", "--worktree-id", "--no-mux", "--defer-new-seed", "--stage-launch-seed",
            "--require-agent-hosting", "--require-native-seed",
            "--require-launch-attempt",
        ) or arg in attempt_options:
            continue
        raise ValueError(
            f"Remote prompt admission does not support {arg!r}; use this option on the target."
        )
    request_id = uuid.uuid4().hex
    kind = "new" if new else "resume"
    wid = None if kind == "new" else args[args.index("--worktree-id") + 1]
    request = {
        "version": 2 if native_required else 1, "request_id": request_id, "kind": kind,
        "worktree_id": wid, "text": text, "no_mux": "--no-mux" in args,
    }
    if native_required:
        request.update(attempt_id=attempt_id, native_seed_required=True)
    raw = json.dumps(request, ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("Remote prompt exceeds the launch-request transport limit")
    encoded = base64.b64encode(raw).decode("ascii")
    project = cfg.project_name()
    command = shell_command(shell, [
        project, "resolve", "--json", "--require-agent-hosting", "--launch-request-b64", encoded,
        *(["--require-native-seed"] if native_required else []),
        *(["--require-launch-attempt"] if "--require-launch-attempt" in args else []),
    ])
    if len(command) > MAX_REMOTE_COMMAND_CHARS:
        raise ValueError("Remote launch request exceeds the remote shell command limit")
    try:
        result = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", alias, command],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        reason = "SSH admission timed out" if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
        raise _unconfirmed_admission(project, alias, request_id, kind, reason) from exc
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
            f"on the target before resubmitting this {kind} prompt."
        )
    try:
        receipt = json.loads(result.stdout)
    except ValueError as exc:
        raise _unconfirmed_admission(
            project, alias, request_id, kind, "invalid receipt",
        ) from exc
    if (
        not isinstance(receipt, dict) or type(receipt.get("version")) is not int
        or receipt["version"] != request["version"]
        or receipt.get("request_id") != request_id or receipt.get("seed_id") != request_id
        or receipt.get("seed_kind") != kind
        or not isinstance(receipt.get("worktree_id"), str) or not receipt["worktree_id"]
        or (wid is not None and receipt["worktree_id"] != wid)
        or (native_required and (
            receipt.get("native_seed_required") is not True
            or "attempt_id" not in receipt or receipt["attempt_id"] != attempt_id
        ))
    ):
        raise _unconfirmed_admission(
            project, alias, request_id, kind, "mismatched launch identity",
        )
    launch_args = [project, "--worktree-id", receipt["worktree_id"],
                   "--require-agent-hosting", "--stage-launch-seed", "--seed-id", receipt["seed_id"]]
    if native_required:
        launch_args.append("--require-native-seed")
    if "--require-launch-attempt" in args:
        launch_args += ["--require-launch-attempt", "--launch-attempt-id=" + identity(attempt_id)]
    if request["no_mux"]:
        launch_args.append("--no-mux")
    return {
        "action": "remote", "ssh_alias": alias,
        "remote_command": shell_command(shell, launch_args),
        "worktree_id": receipt["worktree_id"], "seed_id": receipt["seed_id"],
        "seed_kind": kind, "seed_pending": True, "launch_request_id": request_id,
        **({
            "launch_request_version": 2, "launch_attempt_id": attempt_id,
            "native_seed_required": True,
        } if native_required else {}),
    }
