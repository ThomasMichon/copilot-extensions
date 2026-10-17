#!/usr/bin/env python3
"""Tiny Copilot hook client for the resident agent-worktrees monitor.

The hot path is one bounded loopback request. If the monitor is unavailable,
pre-tool safety guards run in-process from their deployed standalone modules;
post-tool advisory work falls back to nudge_status and bind_nudge.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import signal
import shutil
import socket
import stat
import subprocess
import sys
import time
from functools import cache
from pathlib import Path

_CONNECT_TIMEOUT_S = 0.5
_SESSION_START_TIMEOUT_S = 12.0
_SESSION_START_DECISION_S = 10.0
_SESSION_END_TIMEOUT_S = 8.0
_FALLBACK_PRE_BUDGET_S = 25.0
_MAX_RESPONSE = 64 * 1024
_SESSION_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})$")
_SESSION_GUIDANCE_MAX_BYTES = 4 * 1024
_COMMAND_CATALOG_HEADING = "## agent-worktrees session command catalog"
_GUIDANCE_HEADER = "# Agent Worktrees session guidance\n\n"


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _selected_registry_context(home: Path) -> dict | None:
    if not os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip():
        return None
    helper = _load_sibling("registry_root.py")
    if helper is None:
        return None
    try:
        value = helper.resolve_registry_context(environment=os.environ)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _selected_runtime_root(home: Path) -> Path | None:
    legacy = home / ".agent-worktrees"
    if not os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip():
        return legacy
    helper = _load_sibling("registry_root.py")
    if helper is None:
        return None
    try:
        return Path(
            helper.resolve_registry_root(
                legacy_root=legacy,
                environment=os.environ,
            )
        )
    except Exception:
        return None


def _lock_matches_context(lock: dict, home: Path) -> bool:
    context = _selected_registry_context(home)
    if context is None:
        return not (
            str(lock.get("installReceipt") or "").strip()
            or str(lock.get("marketplaceId") or "").strip()
        )
    return (
        str(lock.get("installReceipt") or "").strip()
        == str(context.get("installReceipt") or "").strip()
        and str(lock.get("marketplaceId") or "").strip()
        == str(context.get("marketplaceId") or "").strip()
        and str(lock.get("pluginRoot") or "").strip()
        == str(context.get("pluginRoot") or "").strip()
    )


def _request(kind: str, payload: dict, home: Path) -> dict | None:
    runtime = _selected_runtime_root(home)
    if runtime is None:
        return None
    endpoint = _read_json(runtime / "status-monitor.lock")
    if not endpoint:
        return None
    if not _lock_matches_context(endpoint, home):
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
        runtime = _selected_runtime_root(Path.home())
        if runtime is None:
            return ""
        try:
            return (runtime / "current-version").read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return ""
    version = value.get("version") if isinstance(value, dict) else None
    return version if isinstance(version, str) else ""


def _enrich_session_payload(payload: dict) -> dict:
    enriched = dict(payload)
    enriched["_agentWorktrees"] = {
        "pluginVersion": _plugin_version(),
        "commandCatalog": _command_catalog_context(),
        "environment": {
            key: value
            for key, value in os.environ.items()
            if key != "WORKTREE_ID"
        },
        "terminal": _probe_terminal(),
    }
    return enriched


def _probe_terminal() -> dict:
    """Best-effort terminal facts from the deployed probe; never raises."""
    try:
        probe = _load_sibling("terminal_probe.py")
        return probe.probe() if probe is not None else {}
    except Exception:
        return {}


def _session_launch_key(payload: dict) -> str:
    if _RUNTIME is None:
        raise RuntimeError("Runtime hook helper is unavailable.")
    return _RUNTIME.session_launch_key(payload)


def _read_bounded_text(path: Path, limit: int = _MAX_RESPONSE) -> str | None:
    return _RUNTIME.read_bounded_text(path, limit=limit)


def _snapshot_context(
    payload: dict, home: Path, *, snapshot_name: str,
) -> str:
    launch_key = _session_launch_key(payload)
    if not launch_key:
        return ""
    runtime = _selected_runtime_root(home)
    if runtime is None:
        return ""
    root = runtime / ".session-context"
    json_path = root / f"{snapshot_name}-{launch_key}.json"
    try:
        raw = _read_bounded_text(json_path)
        state = json.loads(raw) if raw is not None else None
        if (
            isinstance(state, dict)
            and state.get("launchKey") == launch_key
            and isinstance(state.get("output"), str)
        ):
            return _RUNTIME.additional_context(state["output"])
    except (OSError, UnicodeError, ValueError, TypeError):
        pass

    legacy_path = root / f"{snapshot_name}-{launch_key}"
    raw = _read_bounded_text(legacy_path)
    if raw is None:
        return ""
    stored_key, separator, output = raw.partition("\n")
    return (
        _RUNTIME.additional_context(output)
        if separator and stored_key == launch_key
        else ""
    )


def _registration_context(payload: dict, home: Path) -> str:
    return _snapshot_context(payload, home, snapshot_name="register-session")


def _machine_context(payload: dict, home: Path) -> str:
    context = _snapshot_context(payload, home, snapshot_name="machine-context")
    return _RUNTIME.machine_context(context, hostname=socket.gethostname())


def _command_catalog_context() -> str:
    scripts = Path(__file__).resolve().parent
    windows = os.name == "nt"
    script = scripts / ("emit-command-catalog.ps1" if windows else "emit-command-catalog.sh")
    previous_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        # The generated declaration is shared with the native catalog emitter.
        prefix = "# payload-command-catalog-contract: "
        declaration = next(
            line[len(prefix):]
            for line in script.read_text(encoding="utf-8").splitlines()
            if line.startswith(prefix)
        )
        contract = json.loads(declaration)
        if contract["plugin"] != "agent-worktrees":
            raise ValueError("Catalog declaration belongs to another plugin")
        suffix = ""
        shell = "direct"
        if windows:
            shim = contract["windowsCatalogShim"]
            if shim not in {"powershell", "cmd"}:
                raise ValueError("Unsupported catalog command shim")
            suffix = ".ps1" if shim == "powershell" else ".cmd"
            shell = "direct" if shim == "powershell" else "cmd"
        root = scripts.parent
        installer = scripts / ("install.ps1" if windows else "install.sh")
        commands = []
        for command in contract["commands"]:
            path = root / (command["relativePath"] + suffix)
            ready = path.is_file() and installer.is_file()
            if not windows:
                ready = ready and os.access(path, os.X_OK)
            commands.append({
                "id": command["id"],
                "argv": [str(path)],
                "shell": shell,
                "purpose": command["purpose"],
                "availability": "ready" if ready else "unavailable",
            })
        helper = _load_sibling("command_binding.py")
        if helper is None:
            raise ValueError("Payload command binding helper is unavailable")
        catalog = {
            "schema": "copilot-extensions.session-command-catalog",
            "version": 1,
            "plugin": contract["plugin"],
            "payload": {"provenance": "payload-local"},
            "commands": commands,
            "binding": helper.binding_for_payload(
                root, contract["plugin"], Path.home() / ".agent-worktrees"
            ),
        }
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, StopIteration) as error:
        sys.stderr.write(f"[agent-worktrees] Command catalog unavailable: {error}\n")
        return ""
    finally:
        sys.dont_write_bytecode = previous_bytecode
    return (
        _COMMAND_CATALOG_HEADING + "\n\n"
        "Invoke the exact `argv` below. Do not search `PATH` or substitute a "
        "same-named command from another payload.\n\n"
        "```json\n" + json.dumps(catalog, sort_keys=True) + "\n```"
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


def _guidance_content(enriched: dict, home: Path, decision_context: str) -> str:
    registration = _registration_context(enriched, home)
    contexts = []
    if registration.startswith(_COMMAND_CATALOG_HEADING):
        contexts.append(registration)
    else:
        metadata = enriched.get("_agentWorktrees")
        catalog = metadata.get("commandCatalog") if isinstance(metadata, dict) else None
        if not isinstance(catalog, str) or not catalog.startswith(_COMMAND_CATALOG_HEADING):
            catalog = _command_catalog_context()
        for context in (catalog, registration):
            if context and context not in contexts:
                contexts.append(context)
    if decision_context and decision_context not in contexts:
        contexts.append(decision_context)
    machine = _machine_context(enriched, home)
    marker = _RUNTIME.guidance_marker(enriched)
    content = _GUIDANCE_HEADER + "\n\n".join([*contexts, machine]) + "\n" + marker
    if len(content.encode("utf-8")) > _SESSION_GUIDANCE_MAX_BYTES:
        content = (
            _GUIDANCE_HEADER
            + "Current agent-worktrees session guidance was omitted because "
            "it exceeded the bounded file budget. Treat guidance as "
            "unavailable for this session-start invocation.\n\n"
            + machine + "\n" + marker
        )
    elif not contexts:
        content = (
            _GUIDANCE_HEADER
            + "No current agent-worktrees session guidance was available. "
            "Treat guidance as unavailable for this session-start invocation.\n\n"
            + machine + "\n" + marker
        )
    return content


def _write_session_guidance(
    payload: dict,
    *,
    home: Path | None = None,
    decision_context: str = "",
    claim_launch: bool = True,
) -> bool:
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
    try:
        copilot_root = home / ".copilot"
        copilot_root.mkdir(exist_ok=True)
        if _is_link_or_reparse(copilot_root):
            return False
        unresolved_state_root = copilot_root / "session-state"
        unresolved_state_root.mkdir(exist_ok=True)
        if _is_link_or_reparse(unresolved_state_root):
            return False
        state_root = unresolved_state_root.resolve()
        unresolved_session_root = state_root / session_id
        unresolved_session_root.mkdir(exist_ok=True)
        if _is_link_or_reparse(unresolved_session_root):
            return False
        session_root = unresolved_session_root.resolve()
        session_root.relative_to(state_root)
        instructions_dir = session_root / "instructions"
        instructions_dir.mkdir(exist_ok=True)
        if _is_link_or_reparse(instructions_dir):
            return False
        target_dir = instructions_dir / "agent-worktrees"
        target_dir.mkdir(exist_ok=True)
        if _is_link_or_reparse(target_dir):
            return False
        target = target_dir / "session-guidance.instructions.md"
        return _RUNTIME.write_guidance(
            target, lambda: _guidance_content(enriched, home, decision_context),
            enriched, claim_launch=claim_launch,
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _resident_started(payload: dict, home: Path) -> bool:
    launch_key = _session_launch_key(payload)
    if not launch_key:
        return False
    runtime = _selected_runtime_root(home)
    if runtime is None:
        return False
    if _RUNTIME is None:
        raise RuntimeError("Runtime hook helper is unavailable.")
    return _RUNTIME.resident_started(runtime, launch_key)


def _runtime_python(home: Path) -> Path | None:
    runtime = _selected_runtime_root(home)
    if runtime is None:
        return None
    if _RUNTIME is None:
        raise RuntimeError("Runtime hook helper is unavailable.")
    return _RUNTIME.runtime_python(runtime)


def _version_key(version: str) -> tuple:
    if _RUNTIME is None:
        raise RuntimeError("Runtime hook helper is unavailable.")
    return _RUNTIME.version_key(version)


def _bootstrap_first_install(payload: dict) -> dict:
    if os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip():
        return {}
    scripts = Path(__file__).resolve().parent
    direct_stamp = False
    if os.name == "nt":
        script = scripts / "bootstrap-check.ps1"
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        argv = [
            shell, "-NoLogo", "-NoProfile", "-File", str(script),
            "-BootstrapPython", sys.executable,
        ] if shell else []
        installer = scripts / "install.ps1"
        binding = scripts / "command_binding.py"
        if shell and installer.is_file() and binding.is_file():
            state_path = Path(
                os.environ.get("BOOTSTRAP_KILLSWITCH_STATE_FILE")
                or Path.home() / ".copilot-extensions" / "bootstrap-killswitch.json"
            )
            try:
                state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, ValueError):
                state = None
            if isinstance(state, dict) and state.get("active") is True:
                reason = state.get("reason")
                message = f"ACTIVE -- {reason}" if reason else "ACTIVE"
                return {"_stderr": (
                    f"[bootstrap-killswitch] {message}; skipping automatic reconcile.\n"
                )}
            argv = [
                sys.executable, "-I", "-B", "-X", "utf8", str(binding),
                "--payload-root", str(scripts.parent), "--plugin-id", "agent-worktrees",
                "--legacy-root", str(Path.home() / ".agent-worktrees"),
                "--source-payload-root", str(scripts.parent),
                "--deferred-runtime", "--run-lifecycle",
                "--lifecycle-timeout", str(_SESSION_START_TIMEOUT_S - 2), "--",
                shell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(installer), "stamp", "-BootstrapPython", sys.executable,
            ]
            direct_stamp = True
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
            encoding="utf-8",
            timeout=None if direct_stamp else _SESSION_START_TIMEOUT_S,
            check=False,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as error:
        if direct_stamp:
            return {"_stderr": f"[agent-worktrees] Bootstrap stamp unavailable: {error}\n"}
        return {}
    if direct_stamp:
        diagnostic = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode:
            diagnostic += (
                f"[agent-worktrees] Bootstrap stamp failed (exit {completed.returncode}).\n"
            )
        return {"_stderr": diagnostic} if diagnostic else {}
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


def _fallback_legacy_session_start(payload: dict, *, skip_reconcile: bool = False) -> dict:
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
        if skip_reconcile and name in {"bootstrap-check", "provision-check"}:
            continue
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
                    env={
                        **os.environ,
                        "PYTHONPATH": "",
                        **(
                            {"WORKTREE_NO_RECONCILE": "1", "WORKTREE_NO_PROVISION": "1"}
                            if skip_reconcile else {}
                        ),
                    },
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


def _update_driver_image(helper, pid: int) -> str | None:
    image = helper._pid_image_path(pid)
    if image or sys.platform != "darwin":
        return image
    import ctypes

    libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    probe = libproc.proc_pidpath
    probe.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    probe.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(4096)
    if probe(pid, buffer, len(buffer)) <= 0:
        return None
    return os.fsdecode(buffer.value) or None


def _external_update_driver_active(home: Path, python: Path) -> bool:
    """Defer only to a live update stage, not an installed Manager.

    Manager's launch wrapper drives ``stage-update`` through the engine. Its
    ``update_stage.acquire_lock`` records pid/started in ``updater.lock`` for
    at most 120 seconds and releases it in ``finally``. Read that ownership
    signal directly, without invoking the stale runtime or a provider command.
    A completed stage or a cached ``skipped: locked`` status owns no work.
    """
    runtime = _selected_runtime_root(home)
    if runtime is None:
        return False
    lock = _read_json(runtime / "updater.lock")
    if not lock:
        return False
    pid, started = lock.get("pid"), lock.get("started")
    now = time.time()
    if (
        type(pid) is not int
        or pid <= 0
        or type(started) not in (int, float)
        or (type(started) is float and not math.isfinite(started))
        or not now - 120 < started <= now
    ):
        return False
    helper = _load_sibling("versioned_runtime.py")
    if helper is None:
        return False
    try:
        image = _update_driver_image(helper, pid)
        return bool(
            image
            and Path(image).resolve() == python.resolve()
            and helper._pid_alive(pid)
        )
    except Exception:
        return False


def _fallback_session_start(payload: dict, home: Path) -> dict:
    contextual = bool(os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip())
    python = _runtime_python(home)
    if python is None:
        if contextual:
            return {}
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
        if _external_update_driver_active(home, python):
            return {} if contextual else _fallback_legacy_session_start(
                payload, skip_reconcile=True
            )
        return {} if contextual else _fallback_legacy_session_start(payload)
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
        return {} if contextual else _fallback_legacy_session_start(payload)
    if result.stderr:
        sys.stderr.write(result.stderr)
    try:
        value = json.loads(result.stdout or "{}")
    except (TypeError, ValueError):
        return {} if contextual else _fallback_legacy_session_start(payload)
    return value if isinstance(value, dict) else {}


def _fallback_session_end(payload: dict, home: Path) -> dict:
    python = _runtime_python(home)
    if python is None:
        return {}
    try:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = ""
        subprocess.run(
            [
                str(python),
                "-m",
                "agent_worktrees",
                "deregister-session",
                "--stdin",
            ],
            input=json.dumps(payload, separators=(",", ":")),
            capture_output=True,
            text=True,
            timeout=_SESSION_END_TIMEOUT_S,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return {}


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


_RUNTIME = _load_sibling("hook_runtime.py")


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
    contextual = bool(os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip())
    if contextual:
        helper = _load_sibling("registry_root.py")
        if helper is None:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "agent-worktrees installation context cannot be validated"
                ),
            }
        try:
            helper.resolve_registry_root(
                legacy_root=home / ".agent-worktrees",
                environment=os.environ,
            )
        except Exception as error:
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "agent-worktrees installation context is invalid: "
                    f"{error}"
                ),
            }
    deadline = time.monotonic() + _FALLBACK_PRE_BUDGET_S
    combined: dict = {}
    for name in (
        "statelessness_guard.py",
        "cross_repo_guard.py",
        "anchor_write_guard.py",
        "pr_supersede_guard.py",
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
            if contextual:
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "agent-worktrees installation context changed during "
                        "policy evaluation"
                    ),
                }
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
    contextual = bool(os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip())
    if (
        kind == "sessionStart"
        and not isinstance(payload.get("_agentWorktrees"), dict)
    ):
        payload = _enrich_session_payload(payload)
    remote = _request(kind, payload, home)
    if remote is not None:
        return remote
    if kind == "sessionStart" and not contextual and _resident_started(payload, home):
        return {}
    if kind == "preToolUse":
        return _fallback_pre(payload, home)
    if kind == "postToolUse":
        return _fallback_post(payload, home)
    if kind == "sessionStart":
        return _fallback_session_start(payload, home)
    if kind == "sessionEnd":
        return _fallback_session_end(payload, home)
    return {}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    kind = argv[0] if argv else ""
    if kind not in {"preToolUse", "postToolUse", "sessionStart", "sessionEnd"}:
        return 0
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        if kind == "sessionStart":
            payload = _enrich_session_payload(payload)
            # Catalog evidence must survive cancellation of slow lifecycle work.
            _write_session_guidance(payload)
        if kind == "postToolUse" and "--local-fallback" in argv[1:]:
            result = _fallback_post(payload, Path.home())
        else:
            result = decide(kind, payload)
        diagnostic = result.pop("_stderr", None)
        if diagnostic:
            sys.stderr.write(str(diagnostic))
        if kind == "sessionStart":
            decision_context = result.get("additionalContext")
            _write_session_guidance(
                payload,
                claim_launch=False,
                decision_context=(
                    decision_context
                    if isinstance(decision_context, str)
                    else ""
                ),
            )
        elif result:
            sys.stdout.write(json.dumps(result, separators=(",", ":")))
    except Exception:
        pass
    # sessionStart always emits exactly one JSON object -- write it here,
    # outside the try block, so an exception anywhere above still leaves
    # valid, parseable stdout for the host.
    if kind == "sessionStart":
        sys.stdout.write("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
