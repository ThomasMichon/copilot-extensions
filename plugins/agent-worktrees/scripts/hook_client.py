#!/usr/bin/env python3
"""Bounded resident hook transport with canonical standalone policy fallback."""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import signal
import shutil
import socket
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
    lifecycle = _load_sibling("hook_lifecycle_runtime.py") if kind in {"sessionStart", "sessionEnd"} else None
    if lifecycle is None and kind in {"sessionStart", "sessionEnd"}:
        return None
    source_aware = _source_aware_lifecycle(payload)
    if lifecycle and not lifecycle.admitted(
        kind, source_aware, endpoint.get("hook_capabilities"), preflight=True,
    ):
        return lifecycle.unavailable(kind)
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
        else _SESSION_END_TIMEOUT_S
        if kind == "sessionEnd"
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
    if lifecycle and not lifecycle.admitted(
        kind, source_aware, value.get("capabilities"), preflight=False,
    ):
        return lifecycle.unavailable(kind) if source_aware or kind == "sessionEnd" else None
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
        "nativeSource": _capture_native_source(),
    }
    return enriched


def _capture_native_source() -> dict:
    path = Path(__file__).resolve().with_name("native_source_context.py")
    if not path.is_file():
        path = Path(__file__).resolve().parents[1] / "src" / "agent_worktrees" / path.name
    try:
        spec = importlib.util.spec_from_file_location("_agent_worktrees_native_source", path)
        if spec is None or spec.loader is None:
            raise ImportError("Native source metadata helper is unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.capture()
    except (OSError, ImportError, SyntaxError, AttributeError, ValueError) as exc:
        sys.stderr.write(f"[agent-worktrees] Native lifecycle source unavailable: {exc}\n")
        return {"version": 1, "qualified": False, "reason": "native-source-unavailable"}


# Mirrors nudge_status.NUDGE_ENV_KEYS (asserted equal in tests).
_POST_TOOL_ENV_KEYS = ("AGENT_WORKTREES_NUDGE", "AGENT_WORKTREES_AGENDA_NUDGE",
                       "AGENT_WORKTREES_NUDGE_CALLS", "AGENT_WORKTREES_NUDGE_MINUTES")


def _enrich_post_tool_payload(payload: dict) -> dict:
    """Forward this session's nudge switches; the resident's env is not ours."""
    env = {key: os.environ[key] for key in _POST_TOOL_ENV_KEYS if key in os.environ}
    return {**payload, "_agentWorktrees": {"environment": env}}


def _enrich_session_end_payload(payload: dict) -> dict:
    return {
        **payload,
        "_agentWorktrees": {
            "nativeSource": _capture_native_source(),
            "environment": {
                key: os.environ[key] for key in (
                    "AGENT_WORKTREES_BIND_PROJECT", "AGENT_WORKTREES_BIND_WORKTREE_ID",
                    "AGENT_WORKTREES_BIND_SESSION_ID",
                ) if key in os.environ
            },
        },
    }


def _source_aware_lifecycle(payload: dict) -> bool:
    metadata = payload.get("_agentWorktrees")
    return isinstance(metadata, dict) and "nativeSource" in metadata


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
    previous_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        plugin, commands = _RUNTIME.payload_commands(scripts)
        helper = _load_sibling("command_binding.py")
        if helper is None:
            raise ValueError("Payload command binding helper is unavailable")
        catalog = {
            "schema": "copilot-extensions.session-command-catalog",
            "version": 1,
            "plugin": plugin,
            "payload": {"provenance": "payload-local"},
            "commands": commands,
            "binding": helper.binding_for_payload(
                scripts.parent, plugin, Path.home() / ".agent-worktrees"
            ),
        }
        if callable(add_projection := getattr(_load_sibling("resident_control_projection.py"), "add_to_catalog", None)):
            add_projection(catalog)
    except (
        OSError, UnicodeError, ValueError, KeyError, TypeError, StopIteration,
        ImportError, SyntaxError, AttributeError,
    ) as error:
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


def _guidance_degraded_reason(error: BaseException | None = None) -> str:
    if sys.version_info < (3, 10):
        return "unsupported-interpreter"
    if _RUNTIME is None:
        return "runtime-helper-unavailable"
    if error is None:
        return "command-catalog-unavailable"
    if isinstance(error, ImportError):
        return "import-error"
    return "session-start-error"


def _guidance_content(enriched: dict, home: Path, decision_context: str) -> str:
    registration = _registration_context(enriched, home)
    metadata = enriched.get("_agentWorktrees")
    catalog = metadata.get("commandCatalog") if isinstance(metadata, dict) else None
    contexts = []
    floor = ""
    if registration.startswith(_COMMAND_CATALOG_HEADING):
        if isinstance(catalog, str) and catalog.startswith(_COMMAND_CATALOG_HEADING):
            block = re.compile(r"(?ms)^```json\r?\n.*?\r?\n```(?=\r?\n|$)")
            current = block.search(catalog)
            if current is not None:
                registration = block.sub(lambda _: current.group(0), registration, count=1)
        contexts.append(registration)
    else:
        if not isinstance(catalog, str) or not catalog.startswith(_COMMAND_CATALOG_HEADING):
            catalog = _command_catalog_context()
        if not catalog:
            catalog = floor = _RUNTIME.guidance_floor(
                _guidance_degraded_reason(), Path(__file__).resolve().parent
            )
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
            + (floor + "\n\n" if floor else "")
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
        target = _RUNTIME.guidance_target(home, session_id)
        if target is None:
            return False
        return _RUNTIME.write_guidance(
            target, lambda: _render_guidance(enriched, home, decision_context),
            enriched, claim_launch=claim_launch,
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _floor_document(reason: str, payload: dict) -> str:
    floor = _RUNTIME.guidance_floor(reason, Path(__file__).resolve().parent)
    return _GUIDANCE_HEADER + floor + "\n" + _RUNTIME.guidance_marker(payload)


def _render_guidance(enriched: dict, home: Path, decision_context: str) -> str:
    try:
        return _guidance_content(enriched, home, decision_context)
    except Exception as error:
        # Degrade inside the locked, launch-ordered writer instead of dropping guidance.
        reason = _guidance_degraded_reason(error)
        sys.stderr.write(f"[agent-worktrees] Session guidance degraded (reason: {reason}).\n")
        return _floor_document(reason, enriched)


def _write_guidance_floor(payload: dict, reason: str, *, home: Path | None = None) -> bool:
    """Publish the minimal guidance floor after session-start enrichment failed."""
    sys.stderr.write(f"[agent-worktrees] Session guidance degraded (reason: {reason}).\n")
    session_id = payload.get("sessionId")
    if not isinstance(session_id, str) or not _SESSION_IDENTIFIER.fullmatch(session_id):
        return False
    metadata = payload.get("_agentWorktrees")
    owned = {
        **payload,
        "_agentWorktrees": {
            "pluginVersion": _plugin_version(),
            **(metadata if isinstance(metadata, dict) else {}),
        },
    }
    try:
        target = _RUNTIME.guidance_target(home or Path.home(), session_id)
        return target is not None and _RUNTIME.write_guidance(
            target, lambda: _floor_document(reason, owned), owned, claim_launch=True,
        )
    except Exception:
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
    """Compatibility entry: missing runtime installation is never hook work."""
    return {}


def _fallback_legacy_session_start(
    payload: dict, *, skip_reconcile: bool = False, report_only: bool = False,
) -> dict:
    """Run non-install hooks; the legacy skip argument has no remaining install work."""
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
        ("project-hooks", ()),
        ("register-nudge", ("--side-effect-only",)),
        ("register-session", ("--side-effect-only",)),
        ("anchor-hygiene-check", ()),
        ("provision-check", ()),
    )
    encoded = json.dumps(payload, separators=(",", ":"))
    processes = []
    diagnostics = []
    environment = {**os.environ, "PYTHONPATH": ""}
    python = _runtime_python(Path.home())
    if python is not None:
        environment["PYTHONPYCACHEPREFIX"] = _RUNTIME.python_cache_arguments(python)[1].split("=", 1)[1]
    group_kwargs = (
        {"start_new_session": True}
        if os.name == "posix"
        else {
            "creationflags": getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            ) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        }
    )
    for name, extra in specs:
        if report_only and name != "provision-check":
            continue
        script = scripts / f"{name}{suffix}"
        if not script.is_file():
            continue
        if name == "provision-check":
            try:
                with script.open("rb") as stream:
                    header = stream.read(512)
            except OSError:
                header = b""
            if b"# Report-only compatibility contract: provision-status-v1" not in header:
                diagnostics.append("[agent-worktrees] Report-only provisioning status helper unavailable; no legacy provisioning attempted.\n")
                continue
        try:
            processes.append(
                subprocess.Popen(
                    [*prefix, str(script), *extra],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=environment,
                    **group_kwargs,
                )
            )
        except OSError:
            continue
    deadline = time.monotonic() + _SESSION_START_TIMEOUT_S
    result: dict = {}
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


def _external_update_driver_active(home: Path, python: Path) -> bool:
    """Defer only to a live update stage, not an installed Manager.

    Manager's launch wrapper drives ``stage-update`` through the engine. Its
    ``update_stage.acquire_lock`` records PID/birth identity in ``updater.lock``
    while it holds the native lease and removes the record before release.
    Read that ownership
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
        or not 0 <= started <= now
    ):
        return False
    helper = _load_sibling("versioned_runtime.py")
    if helper is None:
        return False
    try:
        recorded = lock.get("start_time")
        if recorded:
            identity = _load_sibling("hook_process_identity.py")
            if identity is None:
                return False
            current = identity.process_start_time(pid)
            if current is None or str(recorded) != current:
                return False
        image = _RUNTIME.update_driver_image(helper, pid)
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
        return _fallback_legacy_session_start(payload, report_only=True)
    metadata = payload.get("_agentWorktrees")
    payload_version = (
        metadata.get("pluginVersion")
        if isinstance(metadata, dict)
        else ""
    )
    environment = python.parent.parent
    runtime_version = (environment.parent if environment.name == ".venv" else environment).name
    source_aware = _source_aware_lifecycle(payload)
    capabilities = _RUNTIME.lifecycle_capabilities(python)
    required = {"session-lifecycle-v1"}
    if source_aware:
        required.add("session-start-source-v1")
    if (capabilities is not None and not required <= set(capabilities)) or (
        capabilities is None and environment.name == ".venv"
    ):
        return {"_stderr": (
            "[agent-worktrees] Selected runtime does not declare the required sessionStart "
            "contract; use explicit installation management to upgrade it. No lifecycle work was sent.\n"
        )}
    if (
        capabilities is None
        and isinstance(payload_version, str)
        and payload_version
        and _version_key(runtime_version) < _version_key(payload_version)
    ):
        if source_aware:
            return {"_stderr": (
                "[agent-worktrees] Native source registration requires the current runtime; "
                "older lifecycle fallback refused.\n"
            )}
        return {} if contextual else _fallback_legacy_session_start(payload)
    helper = _load_sibling("hook_lifecycle_runtime.py")
    if helper is None:
        return {"_stderr": "[agent-worktrees] Lifecycle transport is unavailable.\n"}
    result = helper.run_start(
        python, payload, source_aware=source_aware,
        timeout=_SESSION_START_TIMEOUT_S, decision_timeout=_SESSION_START_DECISION_S,
        python_arguments=["-I", *_RUNTIME.python_cache_arguments(python)],
    )
    return result if result is not None else (
        {} if contextual else _fallback_legacy_session_start(payload)
    )


def _fallback_session_end(payload: dict, home: Path) -> dict:
    python = _runtime_python(home)
    if python is None:
        return {"_stderr": "[agent-worktrees] SessionEnd runtime is unavailable; closure is unconfirmed.\n"}
    capabilities = _RUNTIME.lifecycle_capabilities(python)
    if (capabilities is not None and "session-end-source-v1" not in capabilities) or (
        capabilities is None and python.parent.parent.name == ".venv"
    ):
        return {"_stderr": (
            "[agent-worktrees] Selected runtime does not declare the required sessionEnd "
            "contract; use explicit installation management to upgrade it. Closure is unconfirmed.\n"
        )}
    helper = _load_sibling("hook_lifecycle_runtime.py")
    if helper is None:
        return {"_stderr": "[agent-worktrees] Lifecycle transport is unavailable.\n"}
    return helper.run_end(
        python, payload, timeout=_SESSION_END_TIMEOUT_S,
        python_arguments=["-I", *_RUNTIME.python_cache_arguments(python)],
    )


def _load_sibling(name: str):
    return _load_file(Path(__file__).resolve().with_name(name))


@cache
def _load_file(path: Path):
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


def _pre_unavailable(boundary: str) -> dict:
    return {"additionalContext": (
        f"[agent-worktrees pre-tool] Boundary: {boundary}. "
        "Effect: this component could not evaluate policy; missing evidence is not authorization. This hook did not execute the tool; independent policy decisions remain in force. Next: inspect the selected installation pre-tool files and existing repairing-worktrees guidance; use its catalog command read-only doctor only if available. Retry: do not repeat the tool solely for this warning; inspect its result first, and retry evaluation only after this boundary changes."
    )}


def _fallback_pre(
    payload: dict, home: Path, *, require_policy: bool = False,
    initial_decision=None, only_observations=False,
) -> dict:
    policy = _load_file(Path(__file__).resolve().with_name("hook_pre_policy.py"))
    decide_policy = getattr(policy, "decide", None)
    if not callable(decide_policy):
        return _pre_unavailable("hook_pre_policy.py unavailable")
    return decide_policy(
        payload, home=home, load_sibling=_load_sibling, merge_decisions=_merge_pre_decisions,
        budget_seconds=_FALLBACK_PRE_BUDGET_S, require_policy=require_policy,
        initial_decision=initial_decision, only_observations=only_observations,
    )


def _fallback_post(payload: dict, home: Path) -> dict:
    contexts = []
    nudge = _load_sibling("nudge_status.py")
    tracking_lookup = None
    find_tracking = getattr(nudge, "_find_tracking_yaml", None)
    if callable(find_tracking):
        records = {}

        def tracking_lookup(cwd, home):
            key = cwd or os.getcwd()
            if key not in records:
                found = find_tracking(cwd, home)
                # The legacy resolver also returns None for resolution failures.
                if found is None:
                    return None
                records[key] = found
            return records[key]

    if nudge is not None:
        try:
            text = nudge.decide(payload, home=home, tracking_lookup=tracking_lookup)
            if text:
                contexts.append(text)
        except Exception:
            pass
    binding = _load_sibling("bind_nudge.py")
    if binding is not None:
        try:
            text = binding.decide(payload, home=home, tracking_lookup=tracking_lookup)
            if text:
                contexts.append(text)
        except Exception:
            pass
    return (
        {"additionalContext": "\n\n".join(contexts)}
        if contexts else {}
    )


def _managed_session_admitted(payload: dict, home: Path | None = None) -> bool:
    helper = _load_sibling("managed_session.py")
    try:
        return helper is not None and helper.admitted(payload, home=home)
    except Exception:
        return False


def decide(
    kind: str, payload: dict, *, home: Path | None = None, require_policy: bool = False,
) -> dict:
    home = home or Path.home()
    contextual = bool(os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip())
    if kind == "preToolUse" and not isinstance(payload, dict):
        return _pre_unavailable("event payload is not an object")
    if kind == "preToolUse" and require_policy:
        return _fallback_pre(payload, home, require_policy=True)
    if kind == "preToolUse" and (
        "toolCalls" in payload
        or (payload.get("toolName") or payload.get("tool_name")) in {"apply_patch", "multi_edit"}
    ):
        # Older healthy monitors cannot classify these native write forms.
        return _fallback_pre(payload, home)
    if (
        kind == "sessionStart"
        and not isinstance(payload.get("_agentWorktrees"), dict)
    ):
        payload = _enrich_session_payload(payload)
    if kind == "sessionEnd" and not isinstance(payload.get("_agentWorktrees"), dict):
        payload = _enrich_session_end_payload(payload)
    if kind == "postToolUse" and not isinstance(payload.get("_agentWorktrees"), dict):
        payload = _enrich_post_tool_payload(payload)
    if kind == "preToolUse" and (payload.get("toolName") or payload.get("tool_name")) in ("view", "rg", "glob", "bash", "sh", "shell", "powershell", "pwsh", "cmd", "run", "run_command", "execute", "exec", "terminal"):
        helper = _load_sibling("hook_pre_relevance.py")
        irrelevant = getattr(helper, "is_irrelevant", None)
        if callable(irrelevant) and irrelevant(payload, contextual, _load_sibling):
            return {}
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
    if kind == "preToolUseStrict":
        kind = "preToolUse"
        argv.append("--require-policy")
    if kind not in {"preToolUse", "postToolUse", "sessionStart", "sessionEnd"}:
        return 0
    strict_policy = kind == "preToolUse" and "--require-policy" in argv[1:]
    if (kind == "preToolUse" and not strict_policy
            and os.environ.get("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION") == "resident"):
        return 0
    try:
        raw = sys.stdin.read()
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except ValueError:
            if kind != "preToolUse":
                raise
            if strict_policy:
                sys.stdout.write("{}")
            return 0
        if not isinstance(payload, dict):
            if kind == "preToolUse":
                if strict_policy:
                    sys.stdout.write("{}")
                return 0
            payload = {}
        admission_payload = (
            payload.get("event") if strict_policy and "--only-pr-observations" in argv else payload
        )
        if not _managed_session_admitted(admission_payload):
            if kind == "sessionStart" or strict_policy:
                sys.stdout.write("{}")
            return 0
        if kind == "preToolUse":
            switch = _load_sibling("pretool_killswitch.py")
            if switch is None:
                print("[pretool-killswitch] helper unavailable; guards remain enabled", file=sys.stderr)
            elif switch.is_active():
                if strict_policy:
                    sys.stdout.write("{}")
                return 0
        initial = None
        if strict_policy and "--only-pr-observations" in argv:
            payload, initial = _load_sibling("hook_pre_policy.py").deferred_frame(payload)
        if kind == "sessionStart":
            try:
                payload = _enrich_session_payload(payload)
                # Catalog evidence must survive cancellation of slow lifecycle work.
                _write_session_guidance(payload)
            except Exception as error:
                # Never let enrichment failures silently drop session guidance.
                _write_guidance_floor(payload, _guidance_degraded_reason(error))
                raise
        elif kind == "sessionEnd":
            payload = _enrich_session_end_payload(payload)
        if kind == "postToolUse" and "--local-fallback" in argv[1:]:
            result = _fallback_post(payload, Path.home())
        elif strict_policy:
            result = (
                _fallback_pre(payload, Path.home(), require_policy=True, initial_decision=initial, only_observations=True)
                if initial is not None else decide(kind, payload, require_policy=True)
            )
        else:
            if kind == "preToolUse":
                helper = _load_sibling("hook_pre_policy.py")
                execute = getattr(helper, "execute_owned_observations", None)
                handled = execute(payload, raw, _load_sibling, _merge_pre_decisions) if callable(execute) else None
                if handled is not None:
                    return handled
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
        elif kind != "sessionEnd" and (result or strict_policy):
            sys.stdout.write(json.dumps(result, separators=(",", ":")))
    except Exception as error:
        if kind == "preToolUse":
            hold = getattr(_load_sibling("hook_pre_policy.py"), "hold_pending", None)
            if callable(hold):
                hold(error)
            sys.stdout.write(json.dumps(_pre_unavailable("policy client evaluation failed")))
    # sessionStart always emits exactly one JSON object -- write it here,
    # outside the try block, so an exception anywhere above still leaves
    # valid, parseable stdout for the host.
    if kind == "sessionStart":
        sys.stdout.write("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
