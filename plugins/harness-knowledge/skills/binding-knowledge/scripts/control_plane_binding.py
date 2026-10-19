"""Resolve declared roles through native readers before the existing bind writer."""

from __future__ import annotations

import json
import importlib.util
import os
import re
from pathlib import Path
from types import ModuleType

_git_spec = importlib.util.spec_from_file_location(
    "binding_git", Path(__file__).with_name("binding_git.py")
)
_binding_git = importlib.util.module_from_spec(_git_spec)
_git_spec.loader.exec_module(_binding_git)
validate_checkout = _binding_git.validate_checkout


def _json_result(
    owner: ModuleType, command: str, argv: list[str], *,
    cwd: str = "", accepted: tuple[int, ...] = (0,),
) -> dict:
    result = owner._run_agent_worktrees(command, argv, cwd=cwd, noninteractive=True)
    if result is None or result.returncode not in accepted:
        detail = (
            (result.stderr.strip() or result.stdout.strip())
            if result is not None
            else "native command unavailable"
        )
        raise RuntimeError(f"control-plane binding preflight failed: {detail}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("control-plane binding preflight returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("control-plane binding preflight must return a JSON object")
    return value


def resolve_binding(
    owner: ModuleType,
    topology: str,
    command: str,
    *,
    home: Path | None = None,
    harness: str = "",
    knowledge: str = "",
    harness_path: str = "",
    knowledge_path: str = "",
) -> dict:
    """Resolve an already-registered pair; never infer names, register or write."""
    if not command:
        raise RuntimeError("--control-plane requires the exact --agent-worktrees-path")
    topology = str(Path(topology).expanduser().resolve())
    declaration = _json_result(
        owner,
        command,
        ["knowledge", "control-plane", "--path", topology, "--json"],
    )
    roles = declaration.get("control_plane")
    if (
        type(declaration.get("version")) is not int
        or declaration["version"] != 1
        or not isinstance(roles, dict)
        or roles.get("paired") is not True
    ):
        raise RuntimeError("control-plane binding requires a supported explicit repository pair")
    if not owner._same_path(str(declaration.get("source", "")), topology):
        raise RuntimeError("control-plane reader returned a different topology source")
    for role in ("harness", "knowledge"):
        value = roles.get(role)
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
            raise RuntimeError(f"control-plane reader returned an invalid {role} repository")
    if roles["harness"].casefold() == roles["knowledge"].casefold():
        raise RuntimeError("control-plane harness and knowledge repositories must be distinct")
    if roles.get("legacy_project") not in (None, roles["harness"]):
        raise RuntimeError("control-plane reader returned a conflicting legacy launch target")
    if harness and harness != roles["harness"]:
        raise RuntimeError("--harness conflicts with the declared control-plane pair")
    if knowledge and knowledge != roles["knowledge"]:
        raise RuntimeError("--knowledge conflicts with the declared control-plane pair")

    harness, knowledge = roles["harness"], roles["knowledge"]
    platform = declaration.get("platform")
    if not isinstance(platform, str) or platform not in {"windows", "wsl", "linux"}:
        raise RuntimeError("control-plane reader did not identify its native platform")
    registry = _json_result(owner, command, ["repos", "list", "--json"])
    entries = registry.get("repos")
    if not isinstance(entries, list):
        raise RuntimeError("native repository registry did not return a repository list")
    resolved: dict[str, str] = {}
    for role, name, explicit in (
        ("harness", harness, harness_path),
        ("knowledge", knowledge, knowledge_path),
    ):
        matches = [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and entry["name"].casefold() == name.casefold()
        ]
        if len(matches) != 1 or matches[0]["name"] != name:
            raise RuntimeError(f"{role} requires one exact canonical registration for {name}")
        entry = matches[0]
        paths = entry.get("paths")
        path = paths.get(platform) if isinstance(paths, dict) else None
        if not isinstance(path, str) or not Path(path).expanduser().is_dir():
            raise RuntimeError(f"registered {role} checkout is unavailable")
        allowed = {"worktree"} if role == "harness" else {"knowledge", "worktree"}
        if entry.get("class") not in allowed:
            raise RuntimeError(f"registered {role} does not support its declared lifecycle")
        if explicit and not owner._same_path(explicit, path):
            raise RuntimeError(f"--{role}-path conflicts with the canonical registration")
        resolved[f"{role}_path"] = os.path.abspath(os.path.expanduser(path))
        validate_checkout(resolved[f"{role}_path"])

    if Path(resolved["harness_path"]).resolve() == Path(resolved["knowledge_path"]).resolve():
        raise RuntimeError("control-plane roles cannot share the same physical checkout")

    destination = _json_result(
        owner,
        command,
        ["-p", harness, "config-root", "--json"],
        cwd=resolved["harness_path"],
    )
    native_root = destination.get("config_root")
    if (
        destination.get("repo") != harness
        or destination.get("stateless") is not True
        or destination.get("bound") is not True
        or not isinstance(native_root, str)
        or not native_root
    ):
        detail = destination.get("error") or (
            f"repo={destination.get('repo')}, stateless={destination.get('stateless')}, "
            f"bound={destination.get('bound')}"
        )
        raise RuntimeError(
            f"native owner did not validate this stateless harness configuration root: {detail}"
        )
    root = Path(native_root).resolve()
    if home is not None and not root.is_relative_to(home.resolve()):
        raise RuntimeError("--home override does not contain the native configuration root")
    config = root / "config.yaml"
    if config.is_file():
        owner._check_existing_binding(config.read_text(encoding="utf-8"), knowledge)
    effective = _json_result(
        owner, command, ["-p", harness, "state-root", "--json"],
        cwd=resolved["harness_path"], accepted=(0, 3),
    )
    if (
        effective.get("source") != "knowledge_repo"
        or effective.get("requires_external") is not True
        or not isinstance(effective.get("repo"), str)
        or effective["repo"] not in {"", knowledge}
        or type(effective.get("bound")) is not bool
        or (not effective["repo"] and effective["bound"])
    ):
        raise RuntimeError("effective harness knowledge binding is invalid or conflicts with the pair")
    for role, name in (("harness", harness), ("knowledge", knowledge)):
        readiness = owner.inspect_registration(
            name, resolved[f"{role}_path"], command, platform_key=platform
        )
        if readiness["status"] != "ready":
            raise RuntimeError(f"{role} registration is not ready: {readiness['reason']}")
    return {
        "harness": harness,
        "knowledge": knowledge,
        **resolved,
        "config_root": str(root),
        "platform_key": platform,
        "control_plane": roles,
        "source": declaration.get("source"),
    }
