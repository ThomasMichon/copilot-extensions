"""Default-off observation of the ordinary Bridge subprocess boundary."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from agent_procutil.diagnostics import (
    normalized_diagnostic_text,
    safe_diagnostic_text,
    structured_diagnostic_fields,
)

from .install_paths import install_dir

log = logging.getLogger("agent-dispatch.launch_debug")
ENV = "AGENT_DISPATCH_BRIDGE_CLI_TRACE"
_SECRET_KEY = re.compile(r"token|secret|password|credential|authorization|cookie|api.?key", re.I)
_VALUES = {"idle", "stopped", "running", "awaiting_first_prompt", "delivered",
           "accepted", "refused_unavailable", "native_receipt_missing"}
_PHRASES = ("connection refused", "permission denied", "timed out", "unrecognized arguments",
            "no such file or directory", "not recognized as", "traceback (most recent call last)")
_CONTEXT_ENV = {
    "session_id": "COPILOT_AGENT_SESSION_ID", "owner_ref": "AGENT_WORKTREES_OWNER_REF",
    "pane": "TMUX_PANE", "terminal_session": "WT_SESSION",
}


def _enabled(root: Path) -> bool:
    override = os.environ.get(ENV)
    if override is not None:
        if override.strip().lower() in {"1", "true", "yes", "on"}:
            return True
        if override.strip().lower() in {"0", "false", "no", "off"}:
            return False
        log.warning("launch-debug override invalid; tracing disabled")
        return False
    try:
        with (root / "launch-debug.json").open(encoding="utf-8-sig") as source:
            raw = source.read(8193)
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError):
        log.warning("launch-debug config unreadable; tracing disabled")
        return False
    try:
        if len(raw) > 8192:
            raise ValueError("oversized config")
        config = json.loads(raw)
        if (not isinstance(config, dict) or set(config) != {"bridge_cli_trace"}
                or type(config["bridge_cli_trace"]) is not bool):
            raise ValueError("invalid schema")
    except (ValueError, RecursionError):
        log.warning("launch-debug config invalid; tracing disabled")
        return False
    return config["bridge_cli_trace"]


def _metadata(value: str | bytes | None) -> dict:
    raw = value.encode("utf-8", errors="surrogatepass") if isinstance(value, str) else value
    return {"bytes": len(raw or b""), "sha256": hashlib.sha256(raw or b"").hexdigest()}


def _streams(value: str | bytes | None, environment: dict[str, str], *,
             stderr: bool = False, complete: bool = True) -> dict:
    """Retain causal diagnostics without dumping stdout envelopes or inputs."""
    if value is None:
        return {"capture": "unavailable", "bytes": None, "sha256": None,
                "content": "unavailable", "diagnostics": []}
    result = {**_metadata(value), "capture": "complete" if complete else "partial",
              "content": "omitted", "diagnostics": []}
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    result["fields"] = structured_diagnostic_fields(text, environment=environment)
    if len(text) > 65536:
        result["diagnostics_omitted"] = "oversized"
    try:
        body = json.loads(text) if len(text) <= 65536 else None
    except (ValueError, RecursionError):
        body = None
    if isinstance(body, dict):
        for key in ("status", "reason", "outcome", "admission"):
            if isinstance(body.get(key), str) and body[key] in _VALUES:
                result["diagnostics"].append(f"{key}={body[key]}")
    # Recognized conditions only: even a stderr line can contain a prompt,
    # environment dump or an opaque credential that no regex can sanitize.
    lowered = text.lower()
    result["diagnostics"] += [phrase for phrase in _PHRASES if phrase in lowered]
    result["diagnostics"] = [
        item for item in result["diagnostics"]
        if safe_diagnostic_text(item, 800, environment) == item
    ]
    if stderr:
        opaque_context = re.search(
            r"""["'](?:env|environment|prompt|startup_invocation)["']\s*:""",
            normalized_diagnostic_text(text),
        )
        if opaque_context:
            result["text"] = json.dumps(result["fields"]) if result["fields"] else "<omitted:opaque-context>"
        elif isinstance(body, (dict, list)) or text.lstrip().startswith(("{", "[")):
            result["text"] = json.dumps(result["fields"]) if result["fields"] else "<omitted:opaque-envelope>"
        else:
            result["text"] = safe_diagnostic_text(text, 1600, environment)
        result["content"] = "redacted"
    return result


def _safe(value: str, environment: dict[str, str]) -> str:
    sanitized = safe_diagnostic_text(value, 4096, environment)
    # JSON argv metadata is not a shell string. Preserve benign punctuation,
    # quoting and repeated spaces when the redactor made no substantive change.
    if (sanitized == normalized_diagnostic_text(value)
            and len(value) <= 4096 and all(c.isprintable() for c in value)):
        return value
    return sanitized


def _argv(argv: list[str], environment: dict[str, str]) -> list[str]:
    result = []
    sensitive_value = False
    for arg in argv:
        result.append("[REDACTED]" if sensitive_value else _safe(arg, environment))
        sensitive_value = bool(arg.startswith("--") and "=" not in arg and _SECRET_KEY.search(arg))
    return result


@dataclass
class Trace:
    path: Path
    started: float
    environment: dict[str, str]

    def finish(self, *, result: subprocess.CompletedProcess | None = None,
               error: subprocess.TimeoutExpired | OSError | None = None) -> None:
        timeout = isinstance(error, subprocess.TimeoutExpired)
        record = {
            "event": "finished", "elapsed_seconds": time.monotonic() - self.started,
            "outcome": "unknown_timeout" if timeout else "os_error" if error else "exited",
            "returncode": result.returncode if result is not None else None,
            "stdout": _streams(result.stdout if result is not None else error.output if timeout else None, self.environment, complete=result is not None),
            "stderr": _streams(result.stderr if result is not None else error.stderr if timeout else None, self.environment, stderr=True, complete=result is not None),
        }
        if isinstance(error, OSError):
            record["errno"] = error.errno
        try:
            with os.fdopen(os.open(self.path, os.O_WRONLY | os.O_APPEND),
                           "a", encoding="utf-8") as sink:
                sink.write(json.dumps(record, ensure_ascii=True) + "\n")
        except OSError:
            log.warning("launch-debug sink unavailable; launch result unchanged")


def begin(prefix: list[str], argv: list[str], *, prompt_input: str | None = None,
          input_reference: str | None = None, timeout: float = 180) -> Trace | None:
    """Record the actual call, not a recipe or an alternate execution route."""
    root = install_dir()
    if not _enabled(root):
        return None
    environment = {key: value for key, value in os.environ.items() if value and _SECRET_KEY.search(key)}
    if prompt_input:
        environment["TRACE_STDIN_SECRET"] = prompt_input
        try:
            envelope = json.loads(prompt_input)
        except (ValueError, RecursionError):
            envelope = None
        if isinstance(envelope, dict) and isinstance(envelope.get("prompt"), str) and envelope["prompt"]:
            environment["TRACE_PROMPT_SECRET"] = envelope["prompt"]
    safe_prefix = [_safe(arg, environment) for arg in prefix]
    safe_argv = _argv(argv, environment)
    try:
        cwd = os.getcwd()
        directory = root / "logs" / "bridge-cli"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / f"{uuid.uuid4().hex}.jsonl"
        record = {
            "version": 1, "event": "started", "unix_time": time.time(),
            "prefix": safe_prefix, "argv": safe_argv,
            "argv_exact": safe_prefix == prefix and safe_argv == argv,
            "cwd": _safe(cwd, environment), "cwd_exact": _safe(cwd, environment) == cwd,
            "context": {
                "machine": _safe(socket.gethostname(), environment),
                "install_root": _safe(str(root), environment),
                **{key: _safe(os.environ[name], environment) for key, name in _CONTEXT_ENV.items()
                   if os.environ.get(name)},
            },
            "timeout_seconds": timeout,
            "stdin": {
                **_metadata(prompt_input), "source": "frozen_startup_invocation" if prompt_input is not None else "none",
                "reference": _safe(input_reference, environment) if input_reference else None,
                "content": "omitted",
            },
        }
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                       "w", encoding="utf-8") as sink:
            sink.write(json.dumps(record, ensure_ascii=True) + "\n")
        return Trace(path, time.monotonic(), environment)
    except OSError:
        log.warning("launch-debug sink unavailable; launch continues unchanged")
        return None
