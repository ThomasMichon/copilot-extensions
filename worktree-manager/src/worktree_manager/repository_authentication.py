"""Compatibility adapter for the native owner's optional Configuration read."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned

from . import agent_plugin_runtime, engine_client
from .engine_environment import _engine_environment

CONTRACT = "agent-worktrees.repository-authentication"
PIVOT_NAME = "native-repository-authentication"


class InspectionError(ValueError):
    """A selected provider did not return its declared read contract."""


def _runtime_command(command: list[str] | None):
    if command and command[1:] == ["-B", "-m", "agent_worktrees"]:
        return command[0], command[2], command[3]
    if (
        command and len(command) == 5 and command[1] == "-X"
        and command[2].startswith("pycache_prefix=") and command[3:] == ["-m", "agent_worktrees"]
    ):
        return command[0], command[3], command[4]
    return None


def _unsupported(reason: str) -> dict:
    return {
        "entries": [{
            "id": "unsupported", "title": "Repository authentication inspection",
            "status": "unsupported", "subtitle": reason,
            "card": {
                "title": "Optional inspection unavailable",
                "body": reason + "\nUse Installation & Updates to prepare a supported selected runtime. "
                "Existing repository preferences and other credential owners are unchanged. "
                "No authentication command was run.",
            },
        }],
        "summary": {"status": "unsupported", "observed_at": "not observed"},
    }


def _supports_configuration(command: list[str]) -> bool:
    slot = Path(command[0]).parent.parent
    pattern = (
        "Lib/site-packages/agent_worktrees/repository-authentication-v1.json"
        if os.name == "nt" else
        "lib/python*/site-packages/agent_worktrees/repository-authentication-v1.json"
    )
    try:
        paths = list(slot.glob(pattern))
        if len(paths) != 1:
            return False
        descriptor = paths[0]
        if not descriptor.resolve().is_relative_to(slot.resolve()) or descriptor.stat().st_size > 65536:
            return False
        data = json.loads(descriptor.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(data, dict) and data.get("contract") == CONTRACT
        and type(data.get("version")) is int and data["version"] == 1
        and isinstance(data.get("operations"), list) and "inspect" in data["operations"]
        and data.get("read_only") is True
        and type(data.get("configuration")) is int and data["configuration"] == 1
    )


def _projection(result, project: str) -> dict:
    try:
        data = json.loads(result.stdout)
        if (
            not isinstance(data, dict) or data.get("contract") != CONTRACT
            or type(data.get("version")) is not int or data["version"] != 1
            or data.get("status") not in {"observed", "unavailable", "selection-required", "unsupported"}
            or result.returncode != (0 if data["status"] == "observed" else 1)
        ):
            raise ValueError
        target = data["target"]
        projection = data["configuration"]
        if (
            not isinstance(target, dict) or target.get("resource") != f"{project}/{project}"
            or not re.fullmatch(r"agent-worktrees:[a-f0-9]{64}", target.get("installation", ""))
            or not isinstance(target.get("machine"), str) or not target["machine"]
            or not re.fullmatch(r"[a-f0-9]{64}", data.get("registry_revision", ""))
            or not isinstance(data.get("observed_at"), str) or not data["observed_at"]
            or not isinstance(projection, dict)
            or not isinstance(projection.get("entries"), list) or not projection["entries"]
            or not isinstance(projection.get("summary"), dict)
            or projection["summary"].get("status") != data["status"]
            or projection["summary"].get("observed_at") != data["observed_at"]
        ):
            raise ValueError
        for row in projection["entries"]:
            if (
                not isinstance(row, dict)
                or any(not isinstance(row.get(key), str) or not row[key] for key in ("id", "title", "status"))
                or not isinstance(row.get("card"), dict)
                or any(not isinstance(row["card"].get(key), str) for key in ("title", "body"))
            ):
                raise ValueError
    except (ValueError, KeyError, TypeError):
        raise InspectionError(
            "Native authentication observation is invalid or failed; refresh this section or inspect the native owner."
        ) from None
    return projection


def inventory(project: str, *, engine_argv: list[str] | None = None) -> dict:
    selected = agent_plugin_runtime.resolve_installed_plugin_command("agent-worktrees")
    requested = engine_argv if engine_argv is not None else engine_client.engine_base_command()
    # Unsealed legacy slots get a fresh cache namespace per resolution.
    # Only that owner-generated cache argument is irrelevant to runtime identity.
    if (
        not selected or _runtime_command(selected) is None
        or _runtime_command(requested) != _runtime_command(selected)
        or not _supports_configuration(selected)
    ):
        return _unsupported("The selected runtime has no qualified version-1 Authentication Configuration read.")
    try:
        result = run_caller_owned(
            [*selected, "repos", "authentication", "inspect",
             "--context-project", project, "--repository", project, "--json"],
            timeout=19, env=_engine_environment(),
            max_stdout_bytes=256 * 1024, max_stderr_bytes=64 * 1024,
        )
    except OwnedCleanupError:
        raise
    except subprocess.TimeoutExpired:
        raise InspectionError("Authentication read timed out after 19s; refresh this section to retry.") from None
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise InspectionError("Authentication read failed; inspect the selected native owner and refresh.") from None
    return _projection(result, project)


def configuration_pivot():
    from .production_picker.picker_tui.pivots import parse_manifest

    command = [sys.executable, "-B", "-m", "worktree_manager.repository_authentication"]
    inherited = engine_client._ENGINE_CMD_OVERRIDE
    if inherited is None and not os.environ.get(engine_client.ENGINE_CMD_ENV, "").strip():
        inherited = engine_client.inherited_engine_command()
    if inherited:
        command += ["--engine-argv", json.dumps(inherited)]
    pointer = os.environ.get("COPILOT_EXTENSIONS_CONTEXT")
    if pointer:
        command += ["--engine-context", pointer]
    return parse_manifest({
        "schema_version": 1, "label": "Authentication", "placement": "config", "scope": "global",
        "list": [*command, "{repo}"], "summary": "{status} | Observed at: {observed_at}",
        "entry": {"id": "id", "title": "title", "subtitle": "subtitle", "badges": ["status"]},
        "actions": [{"key": "details", "label": "Observation details / limitations", "kind": "card"}],
    }, name=PIVOT_NAME, source_path=__file__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--engine-argv")
    parser.add_argument("--engine-context")
    parser.add_argument("project")
    args = parser.parse_args(argv)
    try:
        try:
            engine = json.loads(args.engine_argv) if args.engine_argv is not None else None
        except ValueError:
            raise InspectionError("The selected engine command is invalid.") from None
        if args.engine_argv is not None and (
            not isinstance(engine, list) or not engine
            or any(not isinstance(item, str) or not item for item in engine)
        ):
            raise InspectionError("The selected engine command is invalid.")
        if args.engine_context is not None:
            from .installation_updates import _restore_engine_context

            try:
                _restore_engine_context(args.engine_context)
            except ValueError as error:
                raise InspectionError(str(error)) from None
        result = inventory(args.project, engine_argv=engine)
    except (InspectionError, engine_client.EngineError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
