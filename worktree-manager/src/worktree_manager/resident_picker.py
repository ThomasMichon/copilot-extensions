"""Consume the engine's optional resident bootstrap without importing its code."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

BOOTSTRAP_ENV = "WORKTREE_MANAGER_RESIDENT_BOOTSTRAP"
_binding: tuple[str, Path, list[str]] | None = None
_local_settled: threading.Event | None = None


def settle_local(project: str | None = None) -> None:
    selected = project or (_binding[0] if _binding is not None else "")
    if root_for(selected) is not None and _local_settled is not None:
        _local_settled.set()


def wait_for_local(project: str | None, cancelled) -> bool:
    selected = project or (_binding[0] if _binding is not None else "")
    event = _local_settled if selected and root_for(selected) is not None else None
    if event is None:
        return not cancelled()
    deadline = time.monotonic() + 2.0
    while not cancelled():
        remaining = deadline - time.monotonic()
        if remaining <= 0 or event.wait(min(remaining, 0.05)):
            return not cancelled()
    return False


def root_for(project: str) -> Path | None:
    from . import engine_client

    if (
        _binding is None or _binding[0] != project
        or engine_client.engine_base_command() != _binding[2]
    ):
        return None
    return _binding[1]


def consume(args: list[str]) -> tuple[str, dict]:
    global _binding, _local_settled
    _binding = None
    _local_settled = None
    raw = os.environ.pop(BOOTSTRAP_ENV, "")
    if len(args) != 2 or args[0] != "--project":
        raise ValueError("resident Picker requires --project NAME")
    project = args[1]
    from . import engine_client
    from .production_picker import context

    warning = engine_client.accept_inherited_engine_command()
    try:
        result = json.loads(raw) if len(raw) <= 64 * 1024 else None
        if (
            warning or not isinstance(result, dict) or result.get("ok") is not True
            or type(result.get("contract_version")) is not int or result["contract_version"] != 1
        ):
            raise ValueError("missing or invalid resident bootstrap handoff")
        payload = result.get("bootstrap")
        if (
            not isinstance(payload, dict) or type(payload.get("version")) is not int
            or payload["version"] != 1
            or type(payload.get("should_switch_cwd")) is not bool
            or type(payload.get("default_live")) is not bool
        ):
            raise ValueError("invalid resident project bootstrap")
        bootstrap = context.ProjectBootstrap.from_payload(payload)
        root = result.get("runtime_root")
        command = engine_client.inherited_engine_command()
        if (
            bootstrap.project != project or payload.get("headless") is not False
            or not isinstance(root, str) or not Path(root).is_absolute() or not command
            or engine_client.engine_base_command() != command
        ):
            raise ValueError("resident bootstrap does not match the selected engine/project")
        payload = {
            **payload,
            "project_config": {**(payload.get("project_config") or {}), "install_dir": root},
        }
        _binding = project, Path(root), command
        _local_settled = threading.Event()
    except (TypeError, ValueError, engine_client.EngineError):
        raise
    return project, payload


def _run_decision(project: str, payload: dict, decision: dict) -> int:
    from .__main__ import _run_production_picker
    from .production_picker import context, project_config

    try:
        bootstrap = context.bind_project_bootstrap(payload)
        project_config.bind_bootstrap(project, payload.get("project_config"))
        if bootstrap.should_switch_cwd and bootstrap.cwd is not None:
            os.chdir(bootstrap.cwd)
    except (OSError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return _run_production_picker(project, initial_decision=decision)


def main(args: list[str]) -> int:
    raw = os.environ.get(BOOTSTRAP_ENV, "")
    try:
        project, payload = consume(args)
    except (TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    from .process_priority import raise_current_process_priority, restore_normal_process_priority
    raise_current_process_priority()
    try:
        from .warm_picker_client import run as run_warm

        warm, decision = run_warm(json.loads(raw))
        if not warm:
            from .production_picker import runner

            decision = runner.run(project, bootstrap_payload=payload)
    except Exception as error:
        print(f"error: production Picker failed: {error}", file=sys.stderr)
        return 1
    finally:
        restore_normal_process_priority()
    if not decision:
        return 0
    return _run_decision(project, payload, decision)


def finish(args: list[str]) -> int:
    """Execute one completed session decision using the retained Manager runtime."""
    try:
        if len(args) != 8 or args[::2] != [
            "--project", "--directory", "--manager-root", "--attach-exit",
        ]:
            raise ValueError("invalid resident result arguments")
        project, raw_directory, raw_root, code = args[1::2]
        original = json.loads(os.environ.get(BOOTSTRAP_ENV, ""))
        project, payload = consume(["--project", project])
        directory = Path(raw_directory).resolve(strict=True)
        root = Path(raw_root).resolve(strict=True)
        descriptor = original.get("manager_session") or {}
        if (
            not directory.is_relative_to(root / "picker-sessions")
            or descriptor.get("version") != 1
            or not isinstance(descriptor.get("root"), str)
            or Path(descriptor["root"]).resolve() != root
        ):
            raise ValueError("resident result is outside the selected Manager")
        request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
        from . import engine_client

        if request.get("handoff") != original or request.get("engine") != engine_client.inherited_engine_command():
            raise ValueError("resident result does not match this project/engine handoff")
        exit_code = int(code)
        if exit_code:
            with (directory / "consumed").open("x", encoding="utf-8"):
                pass
            return exit_code if 0 < exit_code < 256 else 1
        result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        if result.get("ok") is not True:
            raise ValueError("resident UI did not produce a successful decision")
        decision = result.get("decision")
        if decision is not None and not isinstance(decision, dict):
            raise ValueError("resident UI returned an invalid decision")
        with (directory / "consumed").open("x", encoding="utf-8"):
            pass
    except (OSError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if decision is None:
        return 0
    return _run_decision(project, payload, decision)
