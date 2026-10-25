"""Optional read-only Worktrees lookup, retaining explicit installation fences."""

from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from ._peer_launch import ContextRefused, validate_owner


def run_caller_owned(*args, **kwargs):
    from agent_procutil.owned_process import run_caller_owned as run

    return run(*args, **kwargs)


def validate_context() -> dict | None:
    raw = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
    if not raw:
        return None
    try:
        pointer = json.loads(raw).get("installReceipt") if raw.lstrip().startswith("{") else raw
        if not isinstance(pointer, str) or not pointer:
            raise ValueError("Explicit context must name an installation receipt")
        root = Path(os.environ.get("AGENT_RT_ROOT") or Path(pointer).parent)
        return validate_owner("agent-ssh", root, raw)
    except (OSError, ValueError, ImportError) as exc:
        raise ContextRefused("agent-ssh installation context refused") from exc


def state_root(cwd: Path, own: dict | None) -> tuple[Path | None, str]:
    payload = (own.get("payloadRoot") if own else os.environ.get("AGENT_SSH_PAYLOAD_ROOT")) or None
    python = Path(sys.executable)
    if python.name.casefold() == "pythonw.exe":
        python = python.with_name("python.exe")
    argv = [str(python), "-I", "-X", "utf8", "-m", "agent_ssh._worktrees", "state-root", "--json"]
    environment = os.environ.copy()
    environment["AGENT_WORKTREES_NO_SELFPROVISION"] = "1"
    environment["AGENT_SSH_NO_SELFPROVISION"] = "1"
    request = json.dumps({"version": 1, "owner": own, "payload": payload}, allow_nan=False)
    return _read_state_root(
        argv,
        environment,
        cwd,
        own,
        append_args=False,
        input_text=request,
    )


def _read_state_root(argv, environment, cwd, own, *, timeout=5, append_args=True, input_text=None):
    try:
        result = run_caller_owned(
            [*argv, "state-root", "--json"] if append_args else argv,
            cwd=cwd,
            env=environment,
            timeout=timeout,
            **({"input_text": input_text} if input_text is not None else {}),
            max_stdout_bytes=65536,
            max_stderr_bytes=65536,
        )
    except (OSError, RuntimeError, UnicodeError, subprocess.SubprocessError) as exc:
        if own is not None:
            raise ContextRefused("same-cell Worktrees invocation failed") from exc
        return None, "unavailable"
    if result.returncode == 126:
        raise ContextRefused("optional Worktrees installation context refused")
    if result.returncode == 127:
        return None, "unavailable"
    if result.returncode not in (0, 3) or len(result.stdout.encode("utf-8")) > 65536:
        return None, "error"
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or type(value.get("requires_external")) is not bool:
            raise ValueError("invalid state-root response")
        if result.returncode == 3 and not (
            value["requires_external"] and value.get("bound") is False
        ):
            raise ValueError("unexpected state-root failure response")
        if not value["requires_external"]:
            return None, "not-required"
        if value.get("bound") is not True:
            return None, "unbound"
        path = Path(value["state_root"])
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("invalid state root")
        return path, "bound"
    except (KeyError, TypeError, ValueError):
        return None, "error"


def main() -> int:
    """Resolve and execute inside the caller-owned aggregate deadline."""
    if sys.argv[1:] != ["state-root", "--json"]:
        return 126
    try:
        raw = sys.stdin.buffer.read(65537)
        request = json.loads(raw) if len(raw) <= 65536 else None
        if (
            not isinstance(request, dict)
            or set(request) != {"version", "owner", "payload"}
            or type(request["version"]) is not int
            or request["version"] != 1
            or (request["owner"] is not None and not isinstance(request["owner"], dict))
            or (request["payload"] is not None and not isinstance(request["payload"], str))
        ):
            raise ValueError("Invalid optional state-root request")
        own = validate_context()
        if json.dumps(own, sort_keys=True, allow_nan=False) != json.dumps(
            request["owner"],
            sort_keys=True,
            allow_nan=False,
        ):
            raise ContextRefused("Captured caller installation changed")
        if own is not None:
            payload = Path(own["payloadRoot"])
        else:
            from .host_restore import payload_root

            try:
                payload = payload_root()
            except (OSError, RuntimeError):
                return 127
        expected = request["payload"]
        if expected is not None and (
            not Path(expected).is_absolute() or Path(expected).resolve() != payload.resolve()
        ):
            raise ContextRefused("Captured caller payload changed")
    except (OSError, ValueError, RuntimeError, ImportError, KeyError):
        return 126
    try:
        spec = importlib.util.spec_from_file_location(
            "_ssh_snapshot_dispatch",
            payload / "scripts" / "snapshot_runtime.py",
        )
        if spec is None or spec.loader is None:
            raise ValueError("Snapshot dispatch unavailable")
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        argv, environment = adapter.worktrees_command(payload, own)
        return subprocess.call(
            argv,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=sys.stdout,
            stderr=sys.stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError:
        if own is not None:
            peer = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
            if peer.exists() or peer.is_symlink():
                return 126
        return 127
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError):
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
