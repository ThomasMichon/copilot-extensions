#!/usr/bin/env python3
"""Tiny Copilot hook client for the resident agent-worktrees monitor.

The hot path is one bounded loopback request. If the monitor is unavailable,
pre-tool safety guards run in-process from their deployed standalone modules;
post-tool advisory work falls back to nudge_status and bind_nudge.
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import os
import re
import signal
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from functools import cache
from pathlib import Path

_CONNECT_TIMEOUT_S = 0.5
_SESSION_START_TIMEOUT_S = 12.0
_SESSION_START_DECISION_S = 10.0
_FALLBACK_PRE_BUDGET_S = 25.0
_MAX_RESPONSE = 64 * 1024
_SESSION_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})$")
_SESSION_GUIDANCE_MAX_BYTES = 8 * 1024


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _request(kind: str, payload: dict, home: Path) -> dict | None:
    endpoint = _read_json(home / ".agent-worktrees" / "status-monitor.lock")
    if not endpoint:
        return None
    if endpoint.get("hook_transport") != "tcp":
        return None
    address = str(endpoint.get("hook_endpoint") or "")
    host, sep, port_text = address.rpartition(":")
    token = endpoint.get("hook_token")
    if not sep or host != "127.0.0.1" or not port_text.isdigit():
        return None
    if not isinstance(token, str) or not token:
        return None
    timeout = (
        _SESSION_START_TIMEOUT_S
        if kind == "sessionStart"
        else _CONNECT_TIMEOUT_S
    )
    decision_timeout = (
        _SESSION_START_DECISION_S
        if kind == "sessionStart"
        else timeout
    )
    request = json.dumps(
        {
            "version": 1,
            "token": token,
            "kind": kind,
            "payload": payload,
            "deadline": time.time() + decision_timeout,
        },
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    try:
        with socket.create_connection(
            (host, int(port_text)), timeout=min(_CONNECT_TIMEOUT_S, timeout)
        ) as conn:
            conn.settimeout(timeout)
            conn.sendall(request)
            chunks: list[bytes] = []
            size = 0
            while size < _MAX_RESPONSE:
                chunk = conn.recv(min(4096, _MAX_RESPONSE - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if b"\n" in chunk:
                    break
    except OSError:
        return None
    raw = b"".join(chunks).split(b"\n", 1)[0]
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or value.get("fallback") is True
    ):
        return None
    capabilities = value.get("capabilities")
    if kind == "sessionStart" and (
        not isinstance(capabilities, list)
        or "session-lifecycle-v1" not in capabilities
    ):
        return None
    result = value.get("result")
    return result if isinstance(result, dict) else {}


def _plugin_version() -> str:
    try:
        value = json.loads(
            (Path(__file__).resolve().parents[1] / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, UnicodeError, ValueError, TypeError):
        try:
            return (
                Path.home()
                .joinpath(".agent-worktrees", "current-version")
                .read_text(encoding="utf-8")
                .strip()
            )
        except (OSError, UnicodeError):
            return ""
    version = value.get("version") if isinstance(value, dict) else None
    return version if isinstance(version, str) else ""


def _enrich_session_payload(payload: dict) -> dict:
    enriched = dict(payload)
    enriched["_agentWorktrees"] = {
        "pluginVersion": _plugin_version(),
        "environment": {
            key: value
            for key, value in os.environ.items()
            if key != "WORKTREE_ID"
        },
    }
    return enriched


def _session_launch_key(payload: dict) -> str:
    metadata = payload.get("_agentWorktrees")
    if not isinstance(metadata, dict):
        return ""
    version = metadata.get("pluginVersion")
    session_id = payload.get("sessionId")
    cwd = payload.get("cwd")
    source = payload.get("source", "")
    timestamp = payload.get("timestamp")
    if (
        not isinstance(version, str)
        or not version
        or not isinstance(session_id, str)
        or not session_id
        or not isinstance(cwd, str)
        or not os.path.isabs(cwd)
        or not isinstance(source, str)
        or isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(timestamp)
    ):
        return ""
    timestamp_text = (
        str(timestamp)
        if isinstance(timestamp, int)
        else f"f64:{struct.pack('>d', timestamp).hex()}"
    )
    identity = json.dumps(
        [session_id, os.path.realpath(cwd), source, version, timestamp_text],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _additional_context(raw: str) -> str:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    context = value.get("additionalContext") if isinstance(value, dict) else None
    return context.strip() if isinstance(context, str) else ""


def _registration_context(payload: dict, home: Path) -> str:
    launch_key = _session_launch_key(payload)
    if not launch_key:
        return ""
    root = home / ".agent-worktrees" / ".session-context"
    json_path = root / f"register-session-{launch_key}.json"
    try:
        state = json.loads(json_path.read_text(encoding="utf-8-sig"))
        if (
            isinstance(state, dict)
            and state.get("launchKey") == launch_key
            and isinstance(state.get("output"), str)
        ):
            return _additional_context(state["output"])
    except (OSError, UnicodeError, ValueError, TypeError):
        pass

    legacy_path = root / f"register-session-{launch_key}"
    try:
        stored_key, separator, output = legacy_path.read_text(
            encoding="utf-8"
        ).partition("\n")
    except (OSError, UnicodeError):
        return ""
    return (
        _additional_context(output)
        if separator and stored_key == launch_key
        else ""
    )


def _command_catalog_context() -> str:
    scripts = Path(__file__).resolve().parent
    if os.name == "nt":
        script = scripts / "emit-command-catalog.ps1"
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        argv = (
            [shell, "-NoLogo", "-NoProfile", "-File", str(script)]
            if shell else []
        )
    else:
        script = scripts / "emit-command-catalog.sh"
        shell = shutil.which("bash")
        argv = [shell, str(script)] if shell else []
    if not argv or not script.is_file():
        return ""
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
            env={**os.environ, "PYTHONPATH": ""},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (
        _additional_context(completed.stdout)
        if completed.returncode == 0 else ""
    )


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _write_session_guidance(payload: dict, *, home: Path | None = None) -> bool:
    home = home or Path.home()
    session_id = payload.get("sessionId")
    if (
        not isinstance(session_id, str)
        or not _SESSION_IDENTIFIER.fullmatch(session_id)
    ):
        return False

    enriched = (
        payload
        if isinstance(payload.get("_agentWorktrees"), dict)
        else _enrich_session_payload(payload)
    )
    catalog = _command_catalog_context()
    registration = _registration_context(enriched, home)
    contexts = []
    if registration and catalog and registration.startswith(catalog):
        contexts.append(registration)
    else:
        for context in (catalog, registration):
            if context and context not in contexts:
                contexts.append(context)
    if not contexts:
        return False

    content = (
        "# Agent Worktrees session guidance\n\n"
        + "\n\n".join(contexts)
        + "\n"
    )
    if len(content.encode("utf-8")) > _SESSION_GUIDANCE_MAX_BYTES:
        return False

    try:
        state_root = (home / ".copilot" / "session-state").resolve()
        session_root = (state_root / session_id).resolve()
        session_root.relative_to(state_root)
        session_root.mkdir(parents=True, exist_ok=True)
        if _is_link_or_reparse(session_root):
            return False
        instructions_dir = session_root / "instructions"
        instructions_dir.mkdir(exist_ok=True)
        if _is_link_or_reparse(instructions_dir):
            return False
        target_dir = instructions_dir / "agent-worktrees"
        target_dir.mkdir(exist_ok=True)
        if _is_link_or_reparse(target_dir):
            return False
        target = target_dir / "session-guidance.instructions.md"
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target_dir,
            prefix=".session-guidance.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, target)
        return True
    except (OSError, ValueError):
        try:
            temporary.unlink()
        except (OSError, UnboundLocalError):
            pass
        return False


def _resident_started(payload: dict, home: Path) -> bool:
    launch_key = _session_launch_key(payload)
    if not launch_key:
        return False
    receipt = (
        home
        / ".agent-worktrees"
        / ".session-context"
        / f"lifecycle-{launch_key}.json"
    )
    try:
        value = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return False
    return (
        isinstance(value, dict)
        and value.get("launchKey") == launch_key
        and value.get("state") in {"started", "completed"}
    )


def _complete_runtime_python(runtime: Path, version: str) -> Path | None:
    if not version or "\\" in version or "/" in version:
        return None
    slot = runtime / "versions" / version
    try:
        marker = json.loads(
            (slot / ".install-complete.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    if not isinstance(marker, dict) or marker.get("version") != version:
        return None
    for relative in (
        Path("Scripts") / "python.exe",
        Path("bin") / "python",
    ):
        executable = slot / relative
        if executable.is_file():
            return executable
    return None


def _version_key(version: str) -> tuple:
    import re

    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-dev(\d+))?", version)
    if match:
        major, minor, patch, dev = match.groups()
        return (0, int(major), int(minor), int(patch), dev is None, int(dev or 0))
    return (1, version.lower())


def _runtime_python(home: Path) -> Path | None:
    runtime = home / ".agent-worktrees"
    for marker_name in ("current-version", "last-known-good"):
        try:
            version = (runtime / marker_name).read_text("utf-8").strip()
        except (OSError, UnicodeError):
            continue
        executable = _complete_runtime_python(runtime, version)
        if executable is not None:
            return executable
    try:
        versions = sorted(
            (
                path.name
                for path in (runtime / "versions").iterdir()
                if path.is_dir()
            ),
            key=_version_key,
            reverse=True,
        )
    except OSError:
        return None
    for version in versions:
        executable = _complete_runtime_python(runtime, version)
        if executable is not None:
            return executable
    return None


def _bootstrap_first_install(payload: dict) -> dict:
    scripts = Path(__file__).resolve().parent
    if os.name == "nt":
        script = scripts / "bootstrap-check.ps1"
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        argv = [shell, "-NoLogo", "-NoProfile", "-File", str(script)] if shell else []
    else:
        script = scripts / "bootstrap-check.sh"
        shell = shutil.which("bash")
        argv = [shell, str(script)] if shell else []
    if not argv or not script.is_file():
        return {}
    try:
        completed = subprocess.run(
            argv,
            input=json.dumps(payload, separators=(",", ":")),
            text=True,
            timeout=_SESSION_START_TIMEOUT_S,
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    result: dict = {}
    stdout = (completed.stdout or "").strip()
    if stdout:
        try:
            value = json.loads(stdout)
        except (TypeError, ValueError):
            result["additionalContext"] = stdout
        else:
            if isinstance(value, dict):
                result.update(value)
            else:
                result["additionalContext"] = stdout
    if completed.stderr:
        result["_stderr"] = completed.stderr
    return result


def _fallback_legacy_session_start(payload: dict) -> dict:
    scripts = Path(__file__).resolve().parent
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        suffix = ".ps1"
        prefix = [shell, "-NoLogo", "-NoProfile", "-File"] if shell else []
    else:
        shell = shutil.which("bash")
        suffix = ".sh"
        prefix = [shell] if shell else []
    if not prefix:
        return {}
    specs = (
        ("bootstrap-check", ()),
        ("project-hooks", ()),
        ("register-nudge", ("--side-effect-only",)),
        ("register-session", ("--side-effect-only",)),
        ("anchor-hygiene-check", ()),
        ("marketplace-overrides", ("--side-effect-only",)),
        ("provision-check", ()),
    )
    encoded = json.dumps(payload, separators=(",", ":"))
    processes = []
    group_kwargs = (
        {"start_new_session": True}
        if os.name == "posix"
        else {
            "creationflags": getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        }
    )
    for name, extra in specs:
        script = scripts / f"{name}{suffix}"
        if not script.is_file():
            continue
        try:
            processes.append(
                subprocess.Popen(
                    [*prefix, str(script), *extra],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env={**os.environ, "PYTHONPATH": ""},
                    **group_kwargs,
                )
            )
        except OSError:
            continue
    deadline = time.monotonic() + _SESSION_START_TIMEOUT_S
    result: dict = {}
    diagnostics = []
    for process in processes:
        try:
            stdout, stderr = process.communicate(
                encoded,
                timeout=max(0.1, deadline - time.monotonic()),
            )
        except subprocess.TimeoutExpired:
            try:
                if os.name == "posix":
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
            except (OSError, ProcessLookupError):
                process.kill()
            try:
                stdout, stderr = process.communicate(timeout=0.25)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = "", ""
                if process.stdout:
                    process.stdout.close()
                if process.stderr:
                    process.stderr.close()
        if stderr:
            diagnostics.append(stderr)
        try:
            value = json.loads(stdout.strip() or "{}")
        except ValueError:
            if stdout:
                diagnostics.append(stdout)
            continue
        if isinstance(value, dict) and value.get("additionalContext"):
            result = value
    if diagnostics:
        result["_stderr"] = "".join(diagnostics)
    return result


def _fallback_session_start(payload: dict, home: Path) -> dict:
    python = _runtime_python(home)
    if python is None:
        return _bootstrap_first_install(payload)
    metadata = payload.get("_agentWorktrees")
    payload_version = (
        metadata.get("pluginVersion")
        if isinstance(metadata, dict)
        else ""
    )
    runtime_version = python.parents[1].name
    if (
        isinstance(payload_version, str)
        and payload_version
        and _version_key(runtime_version) < _version_key(payload_version)
    ):
        return _fallback_legacy_session_start(payload)
    try:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = ""
        result = subprocess.run(
            [
                str(python),
                "-m",
                "agent_worktrees",
                "session-lifecycle",
                "--stdin",
                "--timeout-seconds",
                str(_SESSION_START_DECISION_S),
            ],
            input=json.dumps(payload, separators=(",", ":")),
            capture_output=True,
            text=True,
            timeout=_SESSION_START_TIMEOUT_S,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return _fallback_legacy_session_start(payload)
    if result.stderr:
        sys.stderr.write(result.stderr)
    try:
        value = json.loads(result.stdout or "{}")
    except (TypeError, ValueError):
        return _fallback_legacy_session_start(payload)
    return value if isinstance(value, dict) else {}


@cache
def _load_sibling(name: str):
    path = Path(__file__).resolve().with_name(name)
    try:
        spec = importlib.util.spec_from_file_location(
            f"_agent_worktrees_hook_{path.stem}", path
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _merge_pre_decisions(current: dict, new: dict) -> dict:
    result = dict(current)
    contexts = [
        str(value)
        for value in (
            current.get("additionalContext"),
            new.get("additionalContext"),
        )
        if value
    ]
    if contexts:
        result["additionalContext"] = "\n\n".join(contexts)
    rank = {"": 0, "allow": 0, "ask": 1, "deny": 2}
    current_decision = str(current.get("permissionDecision") or "").lower()
    new_decision = str(new.get("permissionDecision") or "").lower()
    if rank.get(new_decision, 0) > rank.get(current_decision, 0):
        result["permissionDecision"] = new.get("permissionDecision")
        reason = new.get("permissionDecisionReason")
        if reason:
            result["permissionDecisionReason"] = reason
    return result


def _fallback_pre(payload: dict, home: Path) -> dict:
    deadline = time.monotonic() + _FALLBACK_PRE_BUDGET_S
    combined: dict = {}
    for name in (
        "statelessness_guard.py",
        "cross_repo_guard.py",
        "anchor_write_guard.py",
    ):
        module = _load_sibling(name)
        if module is None:
            continue
        try:
            kwargs = (
                {"deadline": deadline - 2.0}
                if name == "cross_repo_guard.py" else {}
            )
            decision = module.decide(payload, home=home, **kwargs)
        except Exception:
            continue
        if isinstance(decision, dict) and decision:
            combined = _merge_pre_decisions(combined, decision)
            if combined.get("permissionDecision") == "deny":
                break
    return combined


def _fallback_post(payload: dict, home: Path) -> dict:
    contexts = []
    nudge = _load_sibling("nudge_status.py")
    if nudge is not None:
        try:
            text = nudge.decide(payload, home=home)
            if text:
                contexts.append(text)
        except Exception:
            pass
    binding = _load_sibling("bind_nudge.py")
    if binding is not None:
        try:
            text = binding.decide(payload, home=home)
            if text:
                contexts.append(text)
        except Exception:
            pass
    return (
        {"additionalContext": "\n\n".join(contexts)}
        if contexts else {}
    )


def decide(kind: str, payload: dict, *, home: Path | None = None) -> dict:
    home = home or Path.home()
    if kind == "sessionStart":
        payload = _enrich_session_payload(payload)
    remote = _request(kind, payload, home)
    if remote is not None:
        return remote
    if kind == "sessionStart" and _resident_started(payload, home):
        return {}
    if kind == "preToolUse":
        return _fallback_pre(payload, home)
    if kind == "postToolUse":
        return _fallback_post(payload, home)
    if kind == "sessionStart":
        return _fallback_session_start(payload, home)
    return {}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    kind = argv[0] if argv else ""
    if kind not in {"preToolUse", "postToolUse", "sessionStart"}:
        return 0
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        result = decide(kind, payload)
        if kind == "sessionStart":
            _write_session_guidance(payload)
        diagnostic = result.pop("_stderr", None)
        if diagnostic:
            sys.stderr.write(str(diagnostic))
        if result:
            sys.stdout.write(json.dumps(result, separators=(",", ":")))
        elif kind == "sessionStart":
            sys.stdout.write("{}")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
