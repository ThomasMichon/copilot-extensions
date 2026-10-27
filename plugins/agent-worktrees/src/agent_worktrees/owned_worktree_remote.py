"""Finite calls to the selected receiving Worktrees owner, never a remote shell API."""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from contextlib import nullcontext
from typing import ContextManager

from agent_procutil.owned_process import run_caller_owned
from machine_transport import resolve_machine_selection, resolve_ssh_target

from . import config as cfg, owned_worktree as allocation, resource_producer as producer
from .remote_hosting import shell_command
from ._lifecycle_human.redact import redact


def _failure_detail(stdout: str, stderr: str, token: str) -> str:
    try:
        response = json.loads(stdout, object_pairs_hook=_unique)
    except (ValueError, TypeError):
        response = None
    detail = response.get("message") if isinstance(response, dict) else None
    if not isinstance(detail, str) or not detail.strip():
        detail = stderr
    detail = redact(detail.replace(token, "[REDACTED]") if token else detail)
    detail = re.sub(
        r"""(?i)(\b(?:token|password|secret|api[_-]?key)\b["']?\s*[=:]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)""",
        r"\1[REDACTED]", detail,
    )
    detail = "".join(" " if unicodedata.category(char).startswith("C") else char for char in detail)
    return " ".join(detail.split())[:512]


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("receiving allocation response has duplicate JSON keys")
        result[key] = value
    return result


def _destination_for(creator: str, target: dict, operation: str) -> tuple[str, str]:
    _, project, _ = allocation.qualified_ref(creator)
    config = cfg.load_project_config(project)
    entries = cfg.load_machines_yaml(config.default_repo.anchor, config=config)
    selected = resolve_machine_selection(entries, target["machine"])
    entry = selected.entry
    if entry is None or entry.key != target["machine"]:
        raise ValueError("allocation target must be an exact registered execution space")
    if not entry.ssh_ready:
        raise ValueError("allocation target has no ready SSH transport")
    alias, shell = resolve_ssh_target(entry)
    if not alias or alias.startswith("-"):
        raise ValueError("allocation target has no safe SSH alias")
    return alias, shell_command(
        shell, target["project"],
        ["worktree-allocation-receive", operation, "--json"],
    )


def _destination(metadata: dict, operation: str) -> tuple[str, str]:
    return _destination_for(metadata["creator"], metadata["recovery"], operation)


def capabilities(creator: str, target: dict) -> dict:
    """Read the selected receiver's existing declaration before reserving effects."""
    if not allocation.valid_target(target):
        raise ValueError("invalid worktree allocation target")
    alias, command = _destination_for(creator, target, "capabilities")
    result = _invoke(alias, command, input_text=None, token="", maximum=producer.MAX_INPUT_BYTES)
    producer.canonical_json(result)
    return result


def _invoke(
    alias: str, command: str, *, input_text: str | None, token: str, maximum: int,
) -> dict:
    try:
        result = run_caller_owned(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", alias, command],
            timeout=60, max_stdout_bytes=maximum,
            max_stderr_bytes=4096, input_text=input_text,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "receiving allocation outcome is unknown; inspect the original request before retrying"
            if token else "receiving capability discovery unavailable; no allocation requested",
        ) from exc
    if result.returncode != 0:
        detail = _failure_detail(result.stdout, result.stderr, token)
        raise RuntimeError(
            f"receiving allocation operation failed (exit {result.returncode})"
            + ("; responsibility retained" if token else "; no allocation requested")
            + (f": {detail}" if detail else ""),
        )
    response = json.loads(result.stdout, object_pairs_hook=_unique)
    if not isinstance(response, dict):
        raise ValueError("receiving allocation response must be one JSON object")
    return response


def request(operation: str, metadata: dict, *, context_lock: ContextManager | None = None) -> dict:
    """Address only the original target operation with its protected request facts."""
    if operation not in {"inspect", "confirm", "cancel", "create", "result"}:
        raise ValueError("unsupported receiving allocation operation")
    if not allocation.valid_metadata(metadata) or metadata["phase"] == "closed":
        raise ValueError("receiving allocation requires valid unsettled responsibility")
    with context_lock or nullcontext():
        alias, command = _destination(metadata, operation)
    target = metadata["recovery"]
    payload = {
        "schema": allocation.SCHEMA, "version": allocation.VERSION,
        "origin": metadata["creator"], "request_id": metadata["request_id"],
        "token": metadata["token"], "target": target,
        "identity": metadata.get("identity"),
    }
    from . import allocation_creation_result

    response = _invoke(
        alias, command, input_text=producer.canonical_json(payload), token=metadata["token"],
        maximum=allocation_creation_result.MAX_RESPONSE_BYTES if operation == "result" else producer.MAX_INPUT_BYTES,
    )
    if operation == "result":
        allocation_creation_result.validate(response, metadata)
    else:
        producer.canonical_json(response)
    return response
