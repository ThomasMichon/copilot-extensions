"""Confirmed existing-repository binding through the preserving owner."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import re
import socket
from typing import Any, cast

_spec = importlib.util.spec_from_file_location(
    "configuration_control_plane_binding", Path(__file__).with_name("control_plane_binding.py"),
)
if _spec is None or _spec.loader is None:
    raise ImportError("Cannot load the binding preflight owner")
control_plane_binding = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(control_plane_binding)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _selection(owner: Any, command: str, harness: str, knowledge: str) -> dict[str, Any]:
    selected = cast(dict[str, Any], control_plane_binding.resolve_registered_binding(
        owner, command, harness=harness, knowledge=knowledge,
    ))
    config = Path(selected["config_root"]) / "config.yaml"
    content = config.read_bytes() if config.exists() else None
    selected["configuration_revision"] = (
        hashlib.sha256(content).hexdigest() if content is not None else None
    )
    return selected


def preview(owner: Any, command: str, harness: str, knowledge: str) -> dict[str, Any]:
    selected = _selection(owner, command, harness, knowledge)
    return {
        "version": 1, "token": _digest(selected), "scope": "machine-local",
        "target": {
            "installation": "harness-knowledge:" + _digest(str(Path(command).resolve())),
            "machine": socket.gethostname().lower(), "resource": f"{harness}/{knowledge}",
        },
        "operation": "set",
        "summary": "Bind the selected existing knowledge repository",
        "effects": [
            f"Selected harness: {harness}; existing knowledge repository: {knowledge}.",
            f"Preserve or set only the knowledge pointer in {selected['config_root']}.",
            "Refresh the existing owner-managed local personal-plugin overlay.",
            "No registration, repository creation, source commit, authentication, account switch or session launch.",
            "Existing conflicting bindings refuse; refresh and preview again after any change.",
        ],
        "findings": [{
            "code": "existing-repository-only", "severity": "info",
            "message": "Binding is not provider landing-policy, writable pairing or installed-runtime proof.",
        }],
    }


def apply(owner: Any, command: str, harness: str, knowledge: str, token: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        raise RuntimeError("a fresh existing-binding preview is required")

    def revalidate() -> None:
        if not hmac.compare_digest(token, preview(owner, command, harness, knowledge)["token"]):
            raise RuntimeError("binding preview changed; refresh and preview again")
        precondition = getattr(owner, "_NATIVE_BINDING_PRECONDITION", None)
        if precondition is not None:
            precondition()

    selected = _selection(owner, command, harness, knowledge)
    if not hmac.compare_digest(token, _digest(selected)):
        raise RuntimeError("binding preview changed; refresh and preview again")
    pointer_committed = False

    def committed() -> None:
        nonlocal pointer_committed
        pointer_committed = True

    try:
        summary = owner.bind(
            harness, knowledge, selected["knowledge_path"], home=Path.home(),
            harness_path=selected["harness_path"], agent_worktrees_path=command,
            preserve_binding=True, config_root=Path(selected["config_root"]),
            platform_key=selected["platform_key"], precondition=revalidate,
            pointer_committed=committed,
        )
        complete = (
            summary["registration"]["status"] == "ready"
            and summary["state_root"]["status"] == "ready"
            and not summary.get("plugins_error")
        )
    except Exception:
        if not pointer_committed:
            raise
        return {
            "status": "incomplete", "pointer_committed": True,
            "operation": "set", "binding_operation": "bind-existing", "knowledge_repo": knowledge,
            "message": "The binding pointer committed but follow-up failed. Refresh readiness before retrying.",
        }
    return {
        "status": "persisted" if complete else "incomplete", "pointer_committed": True,
        "operation": "set", "binding_operation": "bind-existing", "knowledge_repo": knowledge,
        "registration_status": summary["registration"]["status"],
        "state_root_status": summary["state_root"]["status"],
        "plugins_status": "failed" if summary.get("plugins_error") else "ready",
        "message": "Refresh readiness before New; existing sessions and tracked pairs were not changed.",
    }


def command(owner: Any, args: argparse.Namespace) -> int:
    if any((
        args.control_plane, args.register, args.home, args.account,
        args.harness_path, args.knowledge_path, args.product,
    )):
        raise RuntimeError("Configuration only supports an existing registered pair")
    if args.configuration == "preview" and args.preview_token:
        raise RuntimeError("A preview token is supplied only with confirmed apply")
    if args.configuration == "apply":
        result = owner._run_agent_worktrees(
            args.agent_worktrees_path,
            ["repos", "configuration", "knowledge-apply", "--context-project", args.harness,
             "--repository", args.knowledge, "--preview-token", args.preview_token],
            noninteractive=True,
        )
        if result is None:
            raise RuntimeError("The native binding transaction is unavailable")
        print(result.stdout.strip())
        return int(result.returncode)
    result = (
        preview(owner, args.agent_worktrees_path, args.harness, args.knowledge)
        if args.configuration == "preview" else
        apply(owner, args.agent_worktrees_path, args.harness, args.knowledge, args.preview_token)
    )
    print(json.dumps(result))
    return 0 if result.get("status") != "incomplete" else 1
