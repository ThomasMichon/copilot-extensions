"""Installer logic -- deploy Python package, venv, and wrappers.

This module handles the Python-side of installation. The native
install.ps1/install.sh scripts call into this for package deployment
after handling prereq checks and native-specific setup.

Can also be invoked directly for install-status checks.

Shared runtime goes to ~/.agent-worktrees/.  Per-project config and
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
    """~/.agent-worktrees (shared runtime)"""
    return cfg.install_dir()


def lib_dir() -> Path:
    """~/.agent-worktrees/lib -- deployed Python package source."""
    return install_dir() / "lib"


def venv_dir() -> Path:
    """~/.agent-worktrees/.venv"""
    return install_dir() / ".venv"


def bin_dir() -> Path:
    """~/.agent-worktrees/bin"""
    return install_dir() / "bin"


def local_bin() -> Path:
    """~/.local/bin"""
    if platform.system() == "Windows":
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".local" / "bin"
    return Path.home() / ".local" / "bin"


def find_package_source(repo_dir: str | Path) -> Path:
    """Locate the agent_worktrees package source in the repo.

    Checks the current layout (plugins/agent-worktrees/) first,
    then falls back to the legacy path (tools/worktree/).
    """
    rd = Path(repo_dir)
    current = rd / "plugins" / "agent-worktrees" / "src" / "agent_worktrees"
    if current.exists():
        return current
    return rd / "tools" / "worktree" / "src" / "agent_worktrees"


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
    """Copy the agent_worktrees package from repo to install_dir/lib/.

    Returns True on success.
    """
    src = find_package_source(repo_dir)
    if not src.exists():
        output.err(f"Package source not found at {src}")
        return False

    dst = lib_dir() / "agent_worktrees"

    # Clean previous deployment
    if dst.exists():
        shutil.rmtree(dst)

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    stamp_build_info(dst, repo_dir)
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
        output.err("Venv Python missing -- use --recreate-venv")
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


def stamp_build_info(
    package_dir: Path,
    repo_dir: str | Path | None = None,
) -> None:
    """Overwrite _build_info.py in the deployed package with provenance.

    Called after every package copy -- from ``deploy_package()``, bootstrap
    auto-update, and the native install scripts.
    """
    version = "1.0.0"
    commit = "unknown"
    branch = "unknown"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    source = str(repo_dir) if repo_dir else "unknown"

    if repo_dir:
        try:
            r = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                commit = r.stdout.strip()
        except Exception:
            pass

        try:
            r = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                branch = r.stdout.strip()
        except Exception:
            pass

        # Try reading version from pyproject.toml
        pyproject = Path(repo_dir) / "plugins" / "agent-worktrees" / "pyproject.toml"
        if pyproject.exists():
            try:
                for line in pyproject.read_text().splitlines():
                    if line.strip().startswith("version"):
                        version = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
            except Exception:
                pass

    info_path = package_dir / "_build_info.py"
    content = (
        '"""Build provenance -- auto-generated at deploy time. Do not edit."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "BUILD_INFO: dict[str, str] = {\n"
        f'    "version": "{version}",\n'
        f'    "commit": "{commit}",\n'
        f'    "branch": "{branch}",\n'
        f'    "build_timestamp": "{ts}",\n'
        f'    "source": "{source.replace(chr(92), "/")}",\n'
        "}\n"
    )
    info_path.write_text(content, encoding="utf-8")


def deploy_wrappers(repo_dir: str | Path) -> bool:
    """Copy the platform-appropriate launch wrapper to install_dir/bin/.

    Also deploys the bootstrap-check scripts used by the sessionStart hook.

    Returns True on success.
    """
    bd = bin_dir()
    bd.mkdir(parents=True, exist_ok=True)

    assets = Path(repo_dir) / "plugins" / "agent-worktrees" / "bin"
    if not assets.exists():
        output.err(f"Wrapper assets not found at {assets}")
        return False

    scripts = Path(repo_dir) / "plugins" / "agent-worktrees" / "scripts"

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

    # Deploy bootstrap-check scripts (called by sessionStart hook)
    for name in ("bootstrap-check.ps1", "bootstrap-check.sh"):
        src = scripts / name
        if src.exists():
            shutil.copy2(src, bd / name)
            if platform.system() != "Windows" and name.endswith(".sh"):
                (bd / name).chmod(0o755)
            output.ok(f"Bootstrap: {bd / name}")

    # Deploy default setup scripts (used when repos lack their own)
    sd = install_dir() / "scripts"
    sd.mkdir(parents=True, exist_ok=True)
    for name in ("default-setup.ps1", "default-setup.sh"):
        src = scripts / name
        if src.exists():
            shutil.copy2(src, sd / name)
            if platform.system() != "Windows" and name.endswith(".sh"):
                (sd / name).chmod(0o755)
            output.ok(f"Default setup: {sd / name}")

    return True


def deploy_binstubs(repo_dir: str | Path, project: str) -> bool:
    """Generate project-specific binstubs in ~/.local/bin/.

    Creates a thin binstub that sets ``WORKTREE_PROJECT`` and routes
    through the Python CLI for subcommand dispatch. Falls back to the
    shell launcher if the venv is missing (recovery path).

    Returns True on success.
    """
    lb = local_bin()
    lb.mkdir(parents=True, exist_ok=True)

    is_windows = platform.system() == "Windows"

    # Project-specific launcher (sets WORKTREE_PROJECT, routes to the CLI).
    # Generated for every supported platform -- previously this only had a
    # Windows code path, so on macOS/Linux `register` silently created no
    # launcher at all.
    if project:
        if is_windows:
            binstub_content = (
                "@echo off\r\n"
                'set "PYTHONUTF8=1"\r\n'
                f'set "WORKTREE_PROJECT={project}"\r\n'
                'set "_PY=%USERPROFILE%\\.agent-worktrees\\.venv\\Scripts\\python.exe"\r\n'
                'if exist "%_PY%" (\r\n'
                '    set "PYTHONPATH=%USERPROFILE%\\.agent-worktrees\\lib"\r\n'
                '    "%_PY%" -m agent_worktrees %*\r\n'
                '    exit /b %ERRORLEVEL%\r\n'
                ')\r\n'
                'rem Fallback: launch session directly (venv missing / recovery)\r\n'
                '"%USERPROFILE%\\.agent-worktrees\\bin\\launch-session.cmd" %*\r\n'
                'exit /b %ERRORLEVEL%\r\n'
            )
            dst = lb / f"{project}.cmd"
        else:
            binstub_content = (
                "#!/usr/bin/env bash\n"
                "export PYTHONUTF8=1\n"
                f'export WORKTREE_PROJECT="{project}"\n'
                '_PY="$HOME/.agent-worktrees/.venv/bin/python"\n'
                'if [[ -x "$_PY" ]]; then\n'
                '    export PYTHONPATH="$HOME/.agent-worktrees/lib${PYTHONPATH:+:$PYTHONPATH}"\n'
                '    exec "$_PY" -m agent_worktrees "$@"\n'
                'fi\n'
                '# Fallback: launch session directly (venv missing / recovery)\n'
                'exec "$HOME/.agent-worktrees/bin/launch-session.sh" "$@"\n'
            )
            dst = lb / project
        dst.write_text(binstub_content)
        if not is_windows:
            dst.chmod(0o755)
        output.ok(f"Binstub: {dst}")

    # Unified agent-worktrees command (project-agnostic; routes straight to
    # the Python CLI). It must NOT require WORKTREE_PROJECT -- global
    # subcommands like `register <project>`, `update`, and `--version` run
    # without a project context. The project-specific launchers above are the
    # gating mechanism that sets WORKTREE_PROJECT; this stub stays unconditional
    # and matches the binstub written by init.sh.
    if is_windows:
        wm_content = (
            "@echo off\r\n"
            'set "PYTHONUTF8=1"\r\n'
            'set "PYTHON=%USERPROFILE%\\.agent-worktrees\\.venv\\Scripts\\python.exe"\r\n'
            'set "PYTHONPATH=%USERPROFILE%\\.agent-worktrees\\lib"\r\n'
            '"%PYTHON%" -m agent_worktrees %*\r\n'
            "exit /b %ERRORLEVEL%\r\n"
        )
        dst = lb / "agent-worktrees.cmd"
        dst.write_text(wm_content)
        output.ok(f"Binstub: {dst}")
    else:
        wm_content = (
            "#!/usr/bin/env bash\n"
            "export PYTHONUTF8=1\n"
            'export PYTHONPATH="$HOME/.agent-worktrees/lib${PYTHONPATH:+:$PYTHONPATH}"\n'
            'unset PYTHONHOME\n'
            'exec "$HOME/.agent-worktrees/.venv/bin/python" -m agent_worktrees "$@"\n'
        )
        dst = lb / "agent-worktrees"
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
             "plugins/agent-worktrees/"],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            dirty = True
            dirty_files = [l[3:].strip() for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        pass

    plat = cfg.detect_platform()
    env_id = f"{machine}-{plat}"

    # Resolve the plugin root directory
    plugin_source = str(find_package_source(repo_dir).parent.parent)

    manifest = {
        "schema_version": 1,
        "service": "agent-worktrees",
        "environment": env_id,
        "commit": commit,
        "branch": branch,
        "dirty": dirty,
        "dirty_files": dirty_files,
        "git_available": commit is not None,
        "deployed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deployed_by": env_id,
        "source_paths": ["plugins/agent-worktrees/"],
        "installer_path": "plugins/agent-worktrees/scripts/install.ps1",
        "runtime": "python",
        "plugin_source": plugin_source,
    }

    manifest_path.write_text(json.dumps(manifest, indent=2))
    output.ok(f"Deploy manifest: {manifest_path}")


def show_install_status() -> None:
    """Show the current installation status."""
    output.header("Agent Worktrees Status")

    # Version / build info
    try:
        from ._build_info import BUILD_INFO
        v = BUILD_INFO.get("version", "?.?.?")
        c = BUILD_INFO.get("commit", "unknown")[:10]
        ts = BUILD_INFO.get("build_timestamp", "unknown")
        br = BUILD_INFO.get("branch", "unknown")
        output.ok(f"Version {v}  commit {c}  branch {br}  built {ts}")
    except ImportError:
        output.warn("Build info not available (dev mode)")

    base = install_dir()
    project = cfg.project_name()
    proj_dir = cfg.project_dir()
    venv = venv_dir()
    python = _venv_python(venv)
    lib = lib_dir() / "agent_worktrees"

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

    # Copilot instructions -- context-aware check
    instr_path = proj_dir / ".github" / "instructions" / "machine.instructions.md"
    agents_path = proj_dir / "AGENTS.md"
    # Check if machines.yaml is configured for this project
    _has_machines_yaml = False
    try:
        _reg = read_projects_registry()
        _proj_entry = _reg.get("projects", {}).get(project, {})
        _my = _proj_entry.get("machines_yaml")
        if _my and Path(_my).exists():
            _has_machines_yaml = True
    except Exception:
        pass

    if _has_machines_yaml:
        # machines.yaml exists -- instruction files should be deployed
        if instr_path.exists() and agents_path.exists():
            output.ok("machine.instructions.md + AGENTS.md deployed")
        elif instr_path.exists():
            output.ok("machine.instructions.md deployed (AGENTS.md missing)")
        elif agents_path.exists():
            output.warn("AGENTS.md deployed but machine.instructions.md missing (run update)")
        else:
            output.err("instruction files missing (run install or update)")
    else:
        # No machines.yaml -- instruction files are optional
        if instr_path.exists() or agents_path.exists():
            output.ok("machine instruction files present")
        else:
            output.skipped("machine instructions not configured (no machines.yaml)")


# ── Projects registry ───────────────────────────────────────────────────


def projects_yaml_path() -> Path:
    """Path to the projects registry at ~/.agent-worktrees/projects.yaml."""
    return install_dir() / "projects.yaml"


def read_projects_registry() -> dict:
    """Read projects.yaml and return a dict with a 'projects' key.

    Returns ``{"projects": {}}`` if file is missing or unparseable.
    """
    path = projects_yaml_path()
    if not path.exists():
        return {"projects": {}}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"projects": {}}
        if "projects" not in data or not isinstance(data["projects"], dict):
            data["projects"] = {}
        return data
    except Exception:
        return {"projects": {}}


def _format_yaml_value(v: object) -> str:
    """Format a scalar value for hand-written YAML."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    escaped = str(v).replace("\\", "\\\\")
    return f'"{escaped}"'


def write_projects_registry(registry: dict) -> None:
    """Write the projects registry back to projects.yaml."""
    path = projects_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# ~/.agent-worktrees/projects.yaml",
        "# Registry of adopted repos for terminal profile generation.",
        "",
        "projects:",
    ]
    projects = registry.get("projects", {})
    for name in sorted(projects.keys()):
        entry = projects[name]
        lines.append(f"  {name}:")
        if isinstance(entry, dict):
            for k, v in sorted(entry.items()):
                if isinstance(v, dict):
                    # Nested dict (e.g. wsl: {state: ..., distro: ...})
                    lines.append(f"    {k}:")
                    for nk, nv in sorted(v.items()):
                        lines.append(f"      {nk}: {_format_yaml_value(nv)}")
                else:
                    lines.append(f"    {k}: {_format_yaml_value(v)}")
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def register_project(
    project: str,
    repo_dir: Path | str | None = None,
    default_branch: str = "master",
    *,
    wsl_state: str | None = None,
    wsl_distro: str | None = None,
    wsl_path: str | None = None,
) -> None:
    """Add or update a project entry in projects.yaml.

    Parameters
    ----------
    wsl_state
        WSL adoption state: ``"adopted"`` (full install exists in WSL),
        ``"bootstrap"`` (bootstrap stub deployed), or *None* (no WSL).
    wsl_distro
        WSL distribution name (e.g. ``"Ubuntu"``).  Stored so terminal
        profiles can target a specific distro with ``wsl.exe -d``.
    wsl_path
        Path to the repo anchor inside WSL (e.g. ``~/src/my-project``).
    """
    registry = read_projects_registry()

    repo_path = Path(repo_dir) if repo_dir else None
    machines_yaml: str | None = None
    if repo_path and (repo_path / "machines.yaml").exists():
        machines_yaml = str(repo_path / "machines.yaml")

    entry: dict = {
        "config_dir": f"~/.{project}",
        "anchor": str(repo_path) if repo_path else "",
        "machines_yaml": machines_yaml,
        "default_branch": default_branch,
        "registered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Preserve existing WSL state when re-registering from Windows
    existing = registry["projects"].get(project, {})
    existing_wsl = existing.get("wsl") if isinstance(existing, dict) else None

    # Build WSL metadata block
    if wsl_state:
        wsl_info: dict = {"state": wsl_state}
        if wsl_distro:
            wsl_info["distro"] = wsl_distro
        if wsl_path:
            wsl_info["path"] = wsl_path
        entry["wsl"] = wsl_info
    elif existing_wsl and isinstance(existing_wsl, dict):
        # Preserve previously recorded WSL state
        entry["wsl"] = existing_wsl

    registry["projects"][project] = entry

    write_projects_registry(registry)
    output.ok(f"Project '{project}' registered in projects.yaml")
