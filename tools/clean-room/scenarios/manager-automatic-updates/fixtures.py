"""Prepare self-contained, immutable fixture slots through the real Manager copier."""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any

OLD = "0.0.0.dev1+nativeprobe.old"
NEW = "0.0.0.dev2+nativeprobe.new"
HERE = Path(__file__).resolve().parent


def private_directory(path: Path) -> Path:
    if not path.is_absolute() or "session-state" in path.parts:
        raise ValueError("provide an absolute private scratch directory outside session state")
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise ValueError("qualification output must not be inside a source checkout")
    for parent in path.parents:
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("qualification scratch has a linked or reparsed ancestor")
    if os.name == "nt":
        from windows_api import create_private_directory

        create_private_directory(path)
    else:
        path.mkdir(mode=0o700)
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise ValueError("qualification scratch must be an ordinary directory")
    if os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError("qualification scratch must be owned by the caller and private")
    return path.resolve(strict=True)


def payload_path(source: Path) -> Path:
    source = source.resolve(strict=True)
    payload = source / "worktree-manager" if (source / "worktree-manager").is_dir() else source
    if not (payload / "src/worktree_manager/self_install.py").is_file():
        raise ValueError("source must be the candidate repository or a prepared Manager payload")
    return payload


def load_manager(source: Path) -> dict[str, Any]:
    payload = payload_path(source)
    sys.dont_write_bytecode = True
    paths = [payload / "src", *sorted((payload.parent / "libs").glob("*/src")),
             *sorted((payload / "libs").glob("*/src"))]
    for path in reversed(paths):
        sys.path.insert(0, str(path))
    existing = sys.modules.get("worktree_manager")
    if existing is not None and Path(existing.__file__).resolve().parent != payload / "src/worktree_manager":
        raise RuntimeError("qualification needs a fresh interpreter for this candidate source")
    return {
        name: importlib.import_module(f"worktree_manager.{name}")
        for name in ("self_install", "automatic_updates_policy", "automatic_updates_process")
    }


def runtime_hashes(payload: Path) -> dict[str, str]:
    package = payload / "src/worktree_manager"
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(package.glob("automatic_updates_*.py"))
    }


def target_environment() -> str:
    return (
        f"sys_platform == '{sys.platform}' and "
        f"python_version == '{sys.version_info.major}.{sys.version_info.minor}'"
    )


def verify_prepared(payload: Path) -> None:
    record = payload.parent / "prepared.json"
    if not record.is_file():
        return
    data = json.loads(record.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or data.get("runtime_hashes") != runtime_hashes(payload):
        raise ValueError("prepared candidate does not match its recorded runtime source")


def prepare(source: Path, output: Path) -> Path:
    payload = payload_path(source)
    if output.resolve().is_relative_to(payload):
        raise ValueError("prepared scenario must be outside its source")
    output = private_directory(output)
    manager = load_manager(payload)
    before = runtime_hashes(payload)
    manager["self_install"]._copy_payload(payload, output / "payload")
    for path in HERE.iterdir():
        if path.suffix in {".py", ".json", ".sh", ".md"}:
            shutil.copy2(path, output / path.name)
    if runtime_hashes(payload) != before or runtime_hashes(output / "payload") != before:
        raise AssertionError("source or prepared runtime changed during materialization")
    (output / "prepared.json").write_text(
        json.dumps({"schema": 1, "runtime_hashes": before}, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return output


def environment(root: Path, *, native: bool = False) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "ASKPASS"))
        and not key.upper().startswith("GIT_")
    }
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT",
                "COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_CONTEXT"):
        env.pop(key, None)
    for key in tuple(env):
        if key.startswith("UPDATE_DRIVER_"):
            env.pop(key)
    if "UV_CACHE_DIR" not in env:
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("canonical uv is unavailable")
        cache = subprocess.run(
            [uv, "cache", "dir"], capture_output=True, text=True, timeout=15, check=True,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        ).stdout.strip()
        env["UV_CACHE_DIR"] = cache
    home = root / "home"
    home.mkdir(exist_ok=True)
    if not native:
        env.pop("WORKTREE_MANAGER_AUTOMATIC_UPDATES_UNIT", None)
        env.update({
            "HOME": str(home), "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={root / 'absent-session-bus'}",
        })
    env.update({
        "WORKTREE_MANAGER_ROOT": str(root), "PYTHONDONTWRITEBYTECODE": "1",
        "WORKTREE_MANAGER_AUTOMATIC_UPDATES_SELF_CYCLE": "1",
        "CE_UPDATE_UNATTENDED": "1", "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1",
    })
    return env


def fixture_main(path: Path, *, failing_standby: bool) -> None:
    text = path.read_text(encoding="utf-8")
    nodes = ast.parse(text)
    guards = {
        node.name: ast.dump(node, include_attributes=False)
        for node in nodes.body if isinstance(node, ast.FunctionDef)
        and node.name in {"_cmd_update", "_cmd_update_recorded"}
    }
    if len(guards) != 2:
        raise ValueError("candidate main lacks the public update guard/telemetry boundary")
    function = next(
        node for node in nodes.body
        if isinstance(node, ast.FunctionDef) and node.name == "_cmd_update_steps"
    )
    lines = text.splitlines(keepends=True)
    lines[function.lineno - 1:function.end_lineno] = [
        "from ._qualification_cycle import execute as _qualification_update\n\n",
        "def _cmd_update_steps(rest, run, **kwargs):\n",
        "    return _qualification_update()\n",
    ]
    text = "".join(lines)
    if failing_standby:
        nodes = ast.parse(text)
        main_guard = ast.dump(ast.parse('__name__ == "__main__"', mode="eval").body)
        entries = [
            node for node in nodes.body if isinstance(node, ast.If)
            and ast.dump(node.test) == main_guard
            and [ast.unparse(statement) for statement in node.body] in (
                ["console_entry()"], ["raise SystemExit(main())"],
            )
        ]
        if len(entries) != 1:
            raise ValueError("fixture needs one qualified late Manager console entry")
        entry = entries[0]
        lines = text.splitlines(keepends=True)
        lines[entry.lineno - 1:entry.lineno - 1] = [
            'if __name__ == "__main__" and sys.argv[1:3] == ["automatic-updates", "run"]:\n',
            "    raise SystemExit(23)\n\n",
        ]
        text = "".join(lines)
    changed = ast.parse(text)
    for node in changed.body:
        if isinstance(node, ast.FunctionDef) and node.name in guards:
            if ast.dump(node, include_attributes=False) != guards[node.name]:
                raise AssertionError("fixture changed the public update guard or telemetry")
    path.write_text(text, encoding="utf-8")


def build(
    source: Path, root: Path, manager: dict[str, Any], *, failing_standby: bool = False,
) -> dict[str, Any]:
    root.mkdir(mode=0o700)
    payload = payload_path(source)
    expected = runtime_hashes(payload)
    for version in (OLD, NEW):
        candidate = root / "fixture-inputs" / version
        manager["self_install"]._copy_payload(payload, candidate)
        init = candidate / "src/worktree_manager/__init__.py"
        body, count = re.subn(
            r'(?m)^__version__\s*=\s*["\'][^"\']+["\']',
            f'__version__ = "{version}"', init.read_text(encoding="utf-8"), count=1,
        )
        if count != 1:
            raise AssertionError("fixture package has no unique version field")
        init.write_text(body, encoding="utf-8")
        project = candidate / "pyproject.toml"
        body, count = re.subn(
            r'(?m)^version\s*=\s*"[^"]+"', f'version = "{version}"',
            project.read_text(encoding="utf-8"), count=1,
        )
        if count != 1:
            raise AssertionError("fixture project has no unique version field")
        project.write_text(body, encoding="utf-8")
        base_python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        (candidate / ".python-version").write_text(str(base_python), encoding="utf-8")
        shutil.copy2(
            HERE / "fixture_cycle.py", candidate / "src/worktree_manager/_qualification_cycle.py",
        )
        fixture_main(
            candidate / "src/worktree_manager/__main__.py",
            failing_standby=failing_standby and version == NEW,
        )
        installer = manager["self_install"]._runtime_installer()
        try:
            with installer.candidate(
                candidate, root, assemble=manager["self_install"]._copy_payload,
            ) as ready:
                if ready.slot.name != version:
                    raise AssertionError("fixture construction selected an unexpected generation")
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"canonical fixture preparation failed: {exc}") from exc
        if runtime_hashes(root / "versions" / version / "payload") != expected:
            raise AssertionError("qualification modified production automatic-update modules")
    (root / "probe").mkdir()
    (root / "current-version").write_text(OLD, encoding="utf-8")
    manager["automatic_updates_policy"].configure(
        root, owner="manager", provenance="native-qualification-fixture",
        interval_seconds=300, project="fixture",
    )
    return {
        "runtime_hashes": expected, "versions": [OLD, NEW],
        "resolution_environment": target_environment(),
    }


def command(root: Path, args: list[str], *, version: str | None = None) -> list[str]:
    version = version or (root / "current-version").read_text(encoding="utf-8").strip()
    if version not in (OLD, NEW):
        raise ValueError("fixture selector is not an owned immutable version")
    return [
        str(root / "versions" / version / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
        "-I", "-S", "-B", "-X", "utf8",
        str(root / "versions" / version / ".runtime-bootstrap/runtime_launcher.py"),
        "-m", "worktree_manager", *args,
    ]


def warm(root: Path, env: dict[str, str]) -> None:
    """Exercise the installed retained launcher; construction already resolved dependencies."""
    for version in (OLD, NEW):
        completed = subprocess.run(
            command(root, ["--version"], version=version),
            cwd=root, env=env, capture_output=True, text=True, timeout=150, check=False,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        if completed.returncode:
            raise RuntimeError(f"canonical fixture preparation failed: {completed.stderr[-4000:]}")
