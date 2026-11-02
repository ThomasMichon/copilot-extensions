"""Synthetic installed prefixes for process-ownership tests, not install proofs."""

import os
import importlib.metadata
import shutil
import subprocess
import sys
import tomllib
import venv
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from worktree_manager import self_install as si
from agent_procutil import no_window_kwargs


def stub_runtime(slot: Path) -> Path:
    slot.mkdir(parents=True, exist_ok=True)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(exist_ok=True)
    python.touch()
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(exist_ok=True)
    (bootstrap / "runtime_launcher.py").touch()
    si._runtime_installer().runtime.mark_complete(slot.parent.parent, slot.name, payload_hash="a" * 64)
    return python


def boundary_runtime(root: Path, version: str, *, diagnostics: bool = False, nested=False) -> Path:
    """Real bootstrap plus the stdlib-only pane boundary, not full assembly."""
    slot = root / "versions" / version
    prefix = slot / ".venv" if nested else slot
    venv.EnvBuilder(with_pip=False).create(prefix)
    if nested:
        (slot / "application.json").write_text('{"name":"worktree-manager"}\n', encoding="utf-8")
    purelib = prefix / ("Lib/site-packages" if os.name == "nt" else
                      f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    package = purelib / "worktree_manager"
    package.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1]
    for name in ("__init__.py", "pane_lifecycle.py", "mux_mapping_registry.py",
                 "mux_file_lock.py", "source_config.py", "runtime_identity.py"):
        shutil.copyfile(source / "src" / "worktree_manager" / name, package / name)
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_source.py", "versioned_runtime_construction.py",
        "versioned_runtime_publication.py",
    ):
        shutil.copyfile(source / "scripts" / name, bootstrap / name)
    if diagnostics:
        launcher = bootstrap / "runtime_launcher.py"
        launcher.write_text(launcher.read_text(encoding="utf-8").replace(
            "from __future__ import annotations\n",
            "from __future__ import annotations\n"
            "import faulthandler\nfaulthandler.dump_traceback_later(2, repeat=True)\n",
        ), encoding="utf-8")
    si._runtime_installer().runtime.mark_complete(root, version, payload_hash="a" * 64)
    python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    environment = dict(os.environ, WORKTREE_MANAGER_ROOT=str(root))
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__",
                 "GH_TOKEN", "GITHUB_TOKEN", "AGENT_WORKTREES_AHP_AUTH_TOKEN"):
        environment.pop(name, None)
    health = subprocess.run(
        [str(python), "-I", "-S", "-B", "-X", "utf8",
         str(bootstrap / "runtime_launcher.py"), "-m", "worktree_manager.pane_lifecycle", "--help"],
        env=environment, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert health.returncode == 0, health.stdout + health.stderr
    return slot


def synthetic_runtime(root: Path, version: str, *, program: str | None = None, nested=False) -> Path:
    slot = root / "versions" / version
    prefix = slot / ".venv" if nested else slot
    venv.EnvBuilder(with_pip=False).create(prefix)
    source = Path(__file__).resolve().parents[1]
    package_source = source
    if program is None and not nested:
        si._copy_payload(source, slot / "payload")
        package_source = slot / "payload"
    elif program is not None and not nested:
        # Program controls need the real shell boundary, not standalone assembly.
        shutil.copytree(source / "bin", slot / "payload" / "bin")
    purelib = prefix / ("Lib/site-packages" if os.name == "nt" else
                      f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    purelib.mkdir(parents=True, exist_ok=True)
    adapter = si._runtime_installer()
    packages = tuple(adapter.engine._package_roots(package_source))
    projects = [
        tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        for package in packages
    ]
    for package in packages:
        shutil.copytree(
            package / "src", purelib, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
        )
    # Freeze the installed third-party runtime closure, never the caller's site path.
    internal = {canonicalize_name(project["name"]) for project in projects}
    pending = [(requirement, ("",)) for project in projects for requirement in project.get("dependencies", ())]
    seen = set()
    while pending:
        raw, extras = pending.pop()
        requirement = Requirement(raw)
        if requirement.marker and not any(
            requirement.marker.evaluate({"extra": extra}) for extra in extras
        ):
            continue
        name = canonicalize_name(requirement.name)
        if name in internal or name in seen:
            continue
        seen.add(name)
        distribution = importlib.metadata.distribution(requirement.name)
        installed = Path(distribution.locate_file("")).resolve()
        for member in distribution.files or ():
            path = Path(distribution.locate_file(member)).resolve()
            if not path.is_relative_to(installed) or not path.is_file() or path.suffix == ".pyc":
                continue
            target = purelib / path.relative_to(installed)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        pending.extend((child, ("", *requirement.extras)) for child in distribution.requires or ())
    if program is not None:
        (purelib / "worktree_manager" / "__main__.py").write_text(program, encoding="utf-8")
        if nested:
            shutil.copytree(source / "bin", purelib / "worktree_manager" / "_runtime_assets" / "bin")
    if nested:
        (slot / "application.json").write_text('{"name":"worktree-manager"}\n', encoding="utf-8")
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_source.py", "versioned_runtime_construction.py",
        "versioned_runtime_publication.py",
    ):
        shutil.copyfile(source / "scripts" / name, bootstrap / name)
    adapter.runtime.mark_complete(root, version, payload_hash="a" * 64)
    return slot
