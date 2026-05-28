"""Installer logic — deploy Python package, venv, and wrappers.

This module handles the Python-side of installation. The native
install.ps1/install.sh scripts call into this for package deployment
after handling prereq checks and native-specific setup.

Can also be invoked directly for install-status checks.

Shared runtime goes to ~/.worktree-manager/.  Per-project config and
state live at ~/.{project}/.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg
from . import output


def install_dir() -> Path:
    """~/.worktree-manager (shared runtime)"""
    return cfg.install_dir()


def lib_dir() -> Path:
    """~/.worktree-manager/lib — deployed Python package source."""
    return install_dir() / "lib"


def venv_dir() -> Path:
    """~/.worktree-manager/.venv"""
    return install_dir() / ".venv"


def bin_dir() -> Path:
    """~/.worktree-manager/bin"""
    return install_dir() / "bin"


def local_bin() -> Path:
    """~/.local/bin"""
    if platform.system() == "Windows":
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".local" / "bin"
    return Path.home() / ".local" / "bin"


def find_package_source(repo_dir: str | Path) -> Path:
    """Locate the worktree_manager package source in the repo.

    Checks the current layout (services/worktree-manager/) first,
    then falls back to the legacy path (tools/worktree/).
    """
    rd = Path(repo_dir)
    current = rd / "services" / "worktree-manager" / "src" / "worktree_manager"
    if current.exists():
        return current
    return rd / "tools" / "worktree" / "src" / "worktree_manager"


def check_prereqs() -> list[str]:
    """Check for required tools. Returns list of missing prereqs."""
    missing: list[str] = []

    # git
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        missing.append("git")

    # uv
    try:
        subprocess.run(["uv", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        missing.append("uv")

    # python
    try:
        subprocess.run(
            [sys.executable, "--version"], capture_output=True, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        missing.append("python")

    return missing


def deploy_package(repo_dir: str | Path) -> bool:
    """Copy the worktree_manager package from repo to install_dir/lib/.

    Returns True on success.
    """
    src = find_package_source(repo_dir)
    if not src.exists():
        output.err(f"Package source not found at {src}")
        return False

    dst = lib_dir() / "worktree_manager"

    # Clean previous deployment
    if dst.exists():
        shutil.rmtree(dst)

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    output.ok(f"Package deployed to {dst}")
    return True


def create_venv() -> bool:
    """Create venv at install_dir/.venv and install pyyaml.

    Returns True on success.
    """
    venv = venv_dir()

    # Create venv via uv (fast, reliable)
    try:
        subprocess.run(
            ["uv", "venv", str(venv), "--python", "3.11", "--allow-existing"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        # Fallback: try without specifying python version
        try:
            subprocess.run(
                ["uv", "venv", str(venv), "--allow-existing"],
                capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError as e:
            output.err(f"Failed to create venv: {e.stderr}")
            return False

    # Install pyyaml into the venv
    try:
        subprocess.run(
            ["uv", "pip", "install", "--python", str(_venv_python(venv)), "pyyaml"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        output.err(f"Failed to install pyyaml: {e.stderr}")
        return False

    output.ok(f"Venv created at {venv}")
    return True


def _venv_python(venv: Path) -> Path:
    if platform.system() == "Windows":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def is_running_from_managed_venv() -> bool:
    """Check if the current Python process is running from the managed venv."""
    current_exe = Path(sys.executable).resolve()
    managed_venv = venv_dir().resolve()
    try:
        current_exe.relative_to(managed_venv)
        return True
    except ValueError:
        return False


def check_venv_health() -> bool:
    """Check if the managed venv exists and can import pyyaml.

    Returns True if healthy.
    """
    python = _venv_python(venv_dir())
    if not python.exists():
        return False
    try:
        r = subprocess.run(
            [str(python), "-c", "import yaml; print('ok')"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0 and "ok" in r.stdout
    except Exception:
        return False


def upgrade_venv_deps() -> bool:
    """Upgrade pyyaml in the managed venv without recreating it.

    Safe to run even when the venv's Python is locked (Windows).
    Returns True on success.
    """
    python = _venv_python(venv_dir())
    if not python.exists():
        output.err("Venv Python missing — use --recreate-venv")
        return False
    try:
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python),
             "--upgrade", "pyyaml"],
            capture_output=True, text=True, check=True,
        )
        output.ok("Venv dependencies up to date")
        return True
    except subprocess.CalledProcessError as e:
        output.err(f"Failed to upgrade venv deps: {e.stderr}")
        return False


def deploy_wrappers(repo_dir: str | Path) -> bool:
    """Copy the platform-appropriate launch wrapper to install_dir/bin/.

    Returns True on success.
    """
    bd = bin_dir()
    bd.mkdir(parents=True, exist_ok=True)

    assets = Path(repo_dir) / "tools" / "worktree" / "bin"
    if not assets.exists():
        output.err(f"Wrapper assets not found at {assets}")
        return False

    if platform.system() == "Windows":
        for name in ("launch-session.cmd", "launch-session.ps1"):
            src = assets / name
            if not src.exists():
                output.err(f"{name} not found in {assets}")
                return False
            shutil.copy2(src, bd / name)
            output.ok(f"Wrapper: {bd / name}")
    else:
        src = assets / "launch-session.sh"
        if not src.exists():
            output.err(f"launch-session.sh not found in {assets}")
            return False
        shutil.copy2(src, bd / "launch-session.sh")
        (bd / "launch-session.sh").chmod(0o755)
        output.ok(f"Wrapper: {bd / 'launch-session.sh'}")

    return True


def deploy_binstubs(repo_dir: str | Path, project: str) -> bool:
    """Generate project-specific binstubs in ~/.local/bin/.

    Creates a thin binstub that sets ``WORKTREE_PROJECT`` and
    delegates to the shared launcher.

    Returns True on success.
    """
    lb = local_bin()
    lb.mkdir(parents=True, exist_ok=True)

    if project:
        # Generate project-specific binstub
        if platform.system() == "Windows":
            binstub_content = (
                "@echo off\r\n"
                'set "PYTHONUTF8=1"\r\n'
                f'set "WORKTREE_PROJECT={project}"\r\n'
                f'"%USERPROFILE%\\.worktree-manager\\bin\\launch-session.cmd" %*\r\n'
            )
            dst = lb / f"{project}.cmd"
            dst.write_text(binstub_content)
            output.ok(f"Binstub: {dst}")

            # Unified worktree-manager command (project-agnostic)
            wm_content = (
                "@echo off\r\n"
                "setlocal\r\n"
                'if not defined WORKTREE_PROJECT (\r\n'
                '    echo ERROR: WORKTREE_PROJECT is not set. '
                'Use the project launcher binstub instead. >&2\r\n'
                '    exit /b 1\r\n'
                ')\r\n'
                'set "PYTHONUTF8=1"\r\n'
                'set "PYTHON=%USERPROFILE%\\.worktree-manager\\.venv\\Scripts\\python.exe"\r\n'
                'set "PYTHONPATH=%USERPROFILE%\\.worktree-manager\\lib"\r\n'
                '"%PYTHON%" -m worktree_manager %*\r\n'
                "exit /b %ERRORLEVEL%\r\n"
            )
            dst = lb / "worktree-manager.cmd"
            dst.write_text(wm_content)
            output.ok(f"Binstub: {dst}")
    else:
        binstub_content = (
            "#!/usr/bin/env bash\n"
            f'export WORKTREE_PROJECT="{project}"\n'
            f'exec "$HOME/.worktree-manager/bin/launch-session.sh" "$@"\n'
        )
        dst = lb / project
        dst.write_text(binstub_content)
        dst.chmod(0o755)
        output.ok(f"Binstub: {dst}")

        # Unified worktree-manager command (project-agnostic)
        wm_content = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ -z "${WORKTREE_PROJECT:-}" ]]; then\n'
            '    echo "ERROR: WORKTREE_PROJECT is not set. '
            'Use the project launcher binstub instead." >&2\n'
            '    exit 1\n'
            'fi\n'
            f'PYTHON="$HOME/.worktree-manager/.venv/bin/python"\n'
            f'export PYTHONPATH="$HOME/.worktree-manager/lib"\n'
            'unset PYTHONHOME\n'
            'export PYTHONUTF8=1\n'
            'exec "$PYTHON" -m worktree_manager "$@"\n'
        )
        dst = lb / "worktree-manager"
        dst.write_text(wm_content)
        dst.chmod(0o755)
        output.ok(f"Binstub: {dst}")

    return True


def write_deploy_manifest(repo_dir: str | Path, machine: str) -> None:
    """Write deploy-manifest.json for provenance tracking."""
    manifest_path = install_dir() / "deploy-manifest.json"

    commit = None
    branch = None
    dirty = False
    dirty_files: list[str] = []

    try:
        r = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            commit = r.stdout.strip()

        r = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            branch = r.stdout.strip()

        r = subprocess.run(
            ["git", "-C", str(repo_dir), "status", "--porcelain", "--",
             "services/worktree-manager/"],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            dirty = True
            dirty_files = [l[3:].strip() for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        pass

    plat = cfg.detect_platform()
    env_id = f"{machine}-{plat}"

    manifest = {
        "schema_version": 1,
        "service": "worktree-manager",
        "environment": env_id,
        "commit": commit,
        "branch": branch,
        "dirty": dirty,
        "dirty_files": dirty_files,
        "git_available": commit is not None,
        "deployed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deployed_by": env_id,
        "source_paths": ["services/worktree-manager/"],
        "installer_path": "services/worktree-manager/install.ps1",
        "runtime": "python",
    }

    manifest_path.write_text(json.dumps(manifest, indent=2))
    output.ok(f"Deploy manifest: {manifest_path}")


def show_install_status() -> None:
    """Show the current installation status."""
    output.header("Worktree Manager Status")

    base = install_dir()
    project = cfg.project_name()
    proj_dir = cfg.project_dir()
    venv = venv_dir()
    python = _venv_python(venv)
    lib = lib_dir() / "worktree_manager"

    print(f"  Runtime:  {base}")
    print(f"  Project:  {project} ({proj_dir})")
    print()

    # Venv
    if python.exists():
        output.ok(f"Venv Python: {python}")
    else:
        output.err(f"Venv Python missing: {python}")

    # Package
    if lib.exists():
        output.ok(f"Package deployed: {lib}")
    else:
        output.err(f"Package missing: {lib}")

    # Wrappers
    bd = bin_dir()
    if platform.system() == "Windows":
        wrapper_name = "launch-session.cmd"
    else:
        wrapper_name = "launch-session.sh"
    p = bd / wrapper_name
    if p.exists():
        output.ok(f"{wrapper_name} deployed")
    else:
        output.err(f"{wrapper_name} missing")

    # Binstub
    lb = local_bin()
    if platform.system() == "Windows":
        bs = lb / f"{project}.cmd"
    else:
        bs = lb / project
    if bs.exists():
        output.ok(f"Binstub: {bs}")
    else:
        output.err(f"Binstub missing: {bs}")

    # Config (per-project)
    config_path = cfg.default_config_path()
    if config_path.exists():
        output.ok(f"Config: {config_path}")
    else:
        output.err(f"Config missing: {config_path}")

    # PATH check
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    lb_str = str(lb)
    if any(Path(d) == lb or d == lb_str for d in path_dirs):
        output.ok(f"{lb} is on PATH")
    else:
        output.err(f"{lb} is not on PATH")

    # Deploy manifest
    manifest_path = base / "deploy-manifest.json"
    if manifest_path.exists():
        try:
            m = json.loads(manifest_path.read_text())
            commit = (m.get("commit") or "unknown")[:10]
            branch = m.get("branch", "unknown")
            deployed_at = m.get("deployed_at", "unknown")
            is_dirty = m.get("dirty", False)
            suffix = " (DIRTY)" if is_dirty else ""
            output.ok(f"Deployed from {branch} @ {commit}{suffix}")
            output.ok(f"Deployed at {deployed_at}")
            output.ok(f"Runtime: {m.get('runtime', 'unknown')}")
        except Exception:
            output.warn("Deploy manifest unreadable")
    else:
        output.skipped("No deploy manifest")

    # Active worktrees (per-project)
    tracking_path = cfg.tracking_dir()
    if tracking_path.exists():
        yamls = list(tracking_path.glob("*.yaml"))
        active = sum(1 for y in yamls if "status: active" in y.read_text())
        output.ok(f"{active} active worktree(s), {len(yamls)} total")

    # Copilot instructions — check deployed artifacts exist
    instr_path = proj_dir / ".github" / "instructions" / "machine.instructions.md"
    agents_path = proj_dir / "AGENTS.md"
    if instr_path.exists() and agents_path.exists():
        output.ok("machine.instructions.md + AGENTS.md deployed")
    elif instr_path.exists():
        output.ok("machine.instructions.md deployed (AGENTS.md missing)")
    elif agents_path.exists():
        output.warn("AGENTS.md deployed but machine.instructions.md missing (run update)")
    else:
        output.err("instruction files missing (run install or update)")
