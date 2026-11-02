"""Emit copyable wheel-first uv application projects; never install or publish."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs" / "installer-engine"))
from runtime_application_binstubs import slot_binstubs  # noqa: E402

_ROOTS = {
    "worktree-manager": ("WORKTREE_MANAGER_ROOT", ".worktree-manager"),
    "agent-worktrees": ("AGENT_WORKTREES_ROOT", ".agent-worktrees"),
    "agent-bridge": ("AGENT_BRIDGE_INSTALL_DIR", ".agent-bridge"),
    "agent-dispatch": ("AGENT_DISPATCH_INSTALL_DIR", ".agent-dispatch"),
    "agent-dispatch-service": ("AGENT_DISPATCH_INSTALL_DIR", ".agent-dispatch"),
    "agent-index-engine": ("AGENT_INDEX_ENGINE_HOME", ".agent-index/engine"),
}
_START_CALLBACKS = {
    "agent-bridge", "agent-codespaces", "agent-containers",
    "agent-dispatch", "agent-dispatch-service", "agent-index",
    "agent-pull-requests", "agent-vault", "agent-index-engine",
    "agent-index-service", "agent-ssh-service", "agent-chat-service", "fleet-manager",
}
_NO_CALLBACKS = {"agent-machines", "agent-ssh", "budget-guidance"}


def root_binding(name: str) -> tuple[str, str]:
    return _ROOTS.get(name, (name.upper().replace("-", "_") + "_HOME", "." + name))


def install_callbacks(name: str) -> tuple[list[str] | None, list[str] | None]:
    if name == "worktree-manager":
        return [], []
    if name in {"agent-mcp", "agent-worktrees", "agent-pull-requests-service"}:
        return ["__source-setup"], ["__source-start"]
    if name == "agent-logger":
        return ["__source-setup"], []
    if name in {"agent-codespaces", "agent-containers"}:
        return ["__source-setup"], ["__source-start"]
    if name in _START_CALLBACKS:
        return [], ["__source-start"]
    if name in _NO_CALLBACKS:
        return [], []
    return None, None


def emit(
    destination: Path, *, distribution: str, version: str, requires_python: str,
    commands: dict[str, str], wheels: dict[str, Path], primary: str | None = None,
    setup: list[str] | None = None, start: list[str] | None = None,
    runtime_source: dict | None = None, target_environment: dict | None = None,
    requirements: list[str] | None = None,
    source_fallbacks: dict[str, Path] | None = None,
    construction: dict | None = None,
) -> Path:
    """First-party wheel paths are relative; uv creates/checks the actual lock."""
    if not commands:
        raise ValueError(f"{distribution}: a declared application command is required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", distribution):
        raise ValueError("application distribution has no ordinary name")
    primary = primary or (distribution if distribution in commands else next(iter(commands)))
    if primary not in commands:
        raise ValueError("primary application command is not declared")
    launchers = {}
    for name, entry in commands.items():
        for filename, content in slot_binstubs(name, entry).items():
            if filename in {"install", "install.cmd", "install.ps1", "install.sh"} and name != primary:
                continue
            if filename in launchers:
                raise ValueError(f"application commands collide: {filename}")
            launchers[filename] = content
    sources = {}
    fallback_inputs = {}
    for name, path in wheels.items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise ValueError(f"invalid associated application name: {name}")
        try:
            path.lstat()
        except FileNotFoundError:
            fallback = (source_fallbacks or {}).get(name)
            if fallback is None:
                raise ValueError(f"missing associated application wheel: {name}") from None
            if not fallback.is_dir() or not (fallback / "pyproject.toml").is_file():
                raise ValueError(f"missing application source fallback: {name}")
            fallback_inputs[name] = fallback
            sources[name] = {"path": "source/" + name, "editable": False}
            continue
        if (
            not path.is_file() or path.is_symlink() or path.suffix != ".whl"
            or path.name in {source["path"].rsplit("/", 1)[-1] for source in sources.values()}
        ):
            raise ValueError(f"invalid or missing associated application wheel: {name}")
        sources[name] = {"path": "wheels/" + path.name}
    if distribution not in sources:
        raise ValueError("the application has no associated root wheel")
    tool = 'package = false\nexclude-newer = "7 days"\n'
    if target_environment is not None:
        platform = target_environment["sys_platform"]
        machine = target_environment["platform_machine"]
        tool += "environments = " + json.dumps([
            f"sys_platform == {platform!r} and platform_machine == {machine!r}",
        ]) + "\n"
    manifest = (
        "[project]\n"
        f"name = {json.dumps('uv-application-' + distribution.lower().replace('_', '-'))}\n"
        f"version = {json.dumps(version)}\n"
        f"requires-python = {json.dumps(requires_python)}\n"
        f"dependencies = {json.dumps(requirements or [distribution + '==' + version])}\n\n"
        "[tool.uv]\n" + tool + "\n[tool.uv.sources]\n"
        + "".join(f"{json.dumps(name)} = {{ path = {json.dumps(row['path'])}"
                  + (", editable = false" if "editable" in row else "") + " }\n"
                  for name, row in sorted(sources.items()))
    )
    root_environment, default_directory = root_binding(primary)
    declaration = {
        "name": primary, "module": commands[primary],
        "commands": list(commands),
        "root_environment": root_environment, "default_directory": default_directory,
        "setup": setup, "start": start,
        "construction": construction,
    }
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "wheels").mkdir()
    for name, path in wheels.items():
        if name not in fallback_inputs:
            shutil.copyfile(path, destination / sources[name]["path"])
    if fallback_inputs:
        sys.path.insert(0, str(ROOT / "libs" / "versioned-runtime"))
        from installer_engine import freeze_inputs

        for name, path in fallback_inputs.items():
            freeze_inputs(path, destination / sources[name]["path"])
    (destination / "pyproject.toml").write_text(manifest, encoding="utf-8", newline="\n")
    if target_environment is not None:
        (destination / ".python-version").write_text(
            target_environment["python_full_version"] + "\n", encoding="utf-8",
        )
    (destination / "application.json").write_text(
        json.dumps(declaration, indent=2) + "\n", encoding="utf-8", newline="\n",
    )
    for filename, text in launchers.items():
        path = destination / filename
        path.write_text(text, encoding="utf-8", newline="")
        if path.suffix == ".sh" or not path.suffix:
            path.chmod(0o700)
    canonical = ROOT / "libs" / "installer-engine"
    shutil.copyfile(canonical / "runtime_application_install.py", destination / "install.py")
    shutil.copyfile(canonical / "runtime_application_binstubs.py",
                    destination / "runtime_application_binstubs.py")
    runtime = destination / "runtime"
    runtime.mkdir()
    for directory in (canonical, ROOT / "libs" / "versioned-runtime"):
        for source in directory.glob("*.py"):
            shutil.copyfile(source, runtime / source.name)
    if runtime_source is not None:
        (destination / "runtime-source.json").write_text(
            json.dumps(runtime_source, sort_keys=True) + "\n", encoding="utf-8", newline="\n",
        )
    return destination
