"""Installer logic -- deploy Python package, venv, and wrappers.

This module handles the Python-side of installation. The native
install.ps1/install.sh scripts call into this for package deployment
after handling prereq checks and native-specific setup.

Can also be invoked directly for install-status checks.

Shared runtime goes to ~/.agent-worktrees/.  Per-project config and
state live at ~/.{project}/.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from contextlib import contextmanager
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
    """Install the agent_worktrees package into the managed venv via uv
    (non-editable), then stamp build info into the installed site-packages copy.

    Replaces the old file-copy-to-lib + PYTHONPATH model. The venv must already
    exist (see ``create_venv``).  Returns True on success.
    """
    src = find_package_source(repo_dir)
    if not src.exists():
        output.err(f"Package source not found at {src}")
        return False
    plugin_dir = src.parent.parent  # .../plugins/agent-worktrees

    python = _venv_python(venv_dir())
    if not python.exists():
        output.err("Venv Python missing -- create the venv first")
        return False

    try:
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python),
             "--reinstall-package", "agent-worktrees", str(plugin_dir), "--quiet"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        output.err(f"Package install failed: {e.stderr}")
        return False

    # Retire the legacy file-copy package dir FIRST, so a stale ambient
    # PYTHONPATH=.../lib cannot make the resolution below pick the old copy.
    legacy = lib_dir()
    if legacy.exists():
        shutil.rmtree(legacy, ignore_errors=True)

    pkg_dir = installed_package_dir(python)
    if pkg_dir:
        stamp_build_info(pkg_dir, repo_dir)
    else:
        output.warn("Could not locate installed agent_worktrees -- build info not stamped")

    output.ok("Package installed into venv")
    return True


def installed_package_dir(python: Path) -> Path | None:
    """Return the site-packages dir of the installed agent_worktrees, or None.

    Clears PYTHONPATH for the probe so a stale ``PYTHONPATH=.../lib`` cannot
    make the import resolve to a retired file-copy package.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    try:
        r = subprocess.run(
            [str(python), "-c",
             "import agent_worktrees, os; print(os.path.dirname(agent_worktrees.__file__))"],
            capture_output=True, text=True, check=True, env=env,
        )
        d = r.stdout.strip()
        return Path(d) if d else None
    except Exception:
        return None


def _source_kind(plugin_path: str) -> str:
    """Infer the runtime footprint source from the installer's location.

    Vendored under the Copilot CLI installed-plugins dir => marketplace;
    anything else (a git checkout) => local.
    """
    if "/.copilot/installed-plugins/" in plugin_path.replace("\\", "/"):
        return "marketplace"
    return "local"


def create_venv() -> bool:
    """Create venv at install_dir/.venv and install pyyaml.

    Returns True on success.
    """
    venv = venv_dir()

    # Idempotency / lock-safety: if the venv already exists and is healthy,
    # do NOT re-run `uv venv`. On Windows `uv venv` re-links Scripts\python.exe,
    # which fails with "Access is denied (os error 5)" whenever the interpreter
    # is held by a running process (the agent-bridge daemon or an active
    # worktree session). That early failure aborted the whole install before
    # the static-asset deploy (deploy_wrappers -> default-setup.ps1) could run.
    # Skipping recreation when healthy lets the install proceed to those steps.
    if check_venv_health():
        output.skipped(f"Venv already healthy at {venv}")
        return True

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

    # Dependencies (pyyaml, ...) are installed with the package from pyproject.
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
        for name in (
            "launch-session.cmd", "launch-session.ps1", "pane-wrapper.ps1",
        ):
            src = assets / name
            if not src.exists():
                output.err(f"{name} not found in {assets}")
                return False
            shutil.copy2(src, bd / name)
            output.ok(f"Wrapper: {bd / name}")
    else:
        for name in ("launch-session.sh", "pane-wrapper.sh"):
            src = assets / name
            if not src.exists():
                output.err(f"{name} not found in {assets}")
                return False
            shutil.copy2(src, bd / name)
            (bd / name).chmod(0o755)
            output.ok(f"Wrapper: {bd / name}")

    # Deploy bootstrap-check scripts (called by sessionStart hook) + the
    # session-conduct injector (sessionStart additionalContext) + the preToolUse
    # guards: statelessness_guard + cross_repo_guard + anchor_write_guard + the
    # postToolUse disposition nudge: nudge_status.
    for name in ("resolve-runtime.ps1", "resolve-runtime.sh",
                 "session-conduct.ps1", "session-conduct.sh",
                 "session-machine.ps1", "session-machine.sh",
                 "bootstrap-check.ps1", "bootstrap-check.sh",
                 "statelessness_guard.py", "cross_repo_guard.py",
                 "anchor_write_guard.py", "nudge_status.py", "bind_nudge.py",
                 "hook_client.py"):
        src = scripts / name
        if src.exists():
            shutil.copy2(src, bd / name)
            if platform.system() != "Windows" and name.endswith(".sh"):
                (bd / name).chmod(0o755)
            output.ok(f"Bootstrap: {bd / name}")

    # Copilot CLI 1.0.81-10 includes the extension-reload startup fix. Retire
    # the temporary warning hook assets from existing installations.
    for name in (
        "session-ext-reload.ps1",
        "session-ext-reload.sh",
        "ext-reload-hang.md",
    ):
        stale = bd / name
        if stale.exists():
            try:
                stale.unlink()
            except OSError as exc:
                output.warn(f"Could not retire obsolete {stale}: {exc}")
                continue
            output.changed(f"retired {stale}")

    # Deploy the session-conduct data fragments (scripts/conduct/*.md) that the
    # session-conduct sessionStart hook emits as additionalContext (cwd-gated).
    conduct_src = scripts / "conduct"
    if conduct_src.is_dir():
        conduct_dst = bd / "conduct"
        conduct_dst.mkdir(parents=True, exist_ok=True)
        for frag in sorted(conduct_src.glob("*.md")):
            shutil.copy2(frag, conduct_dst / frag.name)
            output.ok(f"Conduct: {conduct_dst / frag.name}")

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


def _write_binstub_if_changed(dst: Path, content: str) -> bool:
    """Write *content* to *dst* only if the on-disk content differs.

    Comparison is newline-normalized so that a stub written by a sibling
    generator (init.ps1 / install.ps1 with CRLF, init.sh with LF) is treated
    as unchanged when its logical content matches.

    This is critical on Windows: ``register``/``adopt``/``update`` run *through*
    the global ``agent-worktrees.cmd`` binstub, then call this function, which
    would otherwise rewrite that very file mid-execution. cmd.exe resumes
    reading a batch file by byte offset after a child process returns, so
    rewriting the running stub to a different length corrupts the read (the
    classic stray ``'b' is not recognized`` from ``exit /b`` plus a spurious
    re-run). Skipping the write when nothing changed avoids the corruption.

    Returns True if a write occurred, False if skipped.
    """
    def _norm(s: str) -> str:
        return s.replace("\r\n", "\n").replace("\r", "\n")

    if dst.exists():
        try:
            existing = dst.read_text(encoding="utf-8", errors="replace")
            if _norm(existing) == _norm(content):
                return False
        except OSError:
            pass
    # newline="" preserves the literal \r\n / \n already embedded in content
    dst.write_text(content, encoding="utf-8", newline="")
    return True


# The global ``agent-worktrees`` command shares ~/.local/bin with the project
# launchers, so its name is reserved: reconciliation never deploys it as a
# project stub (that would clobber the global launcher) and never removes it
# (it is owned by Deploy-GlobalBinstub / the global section of deploy_binstubs).
# A project accidentally registered under a reserved name (e.g. install.ps1 run
# from the plugin checkout) is therefore inert to binstub reconciliation.
_RESERVED_BINSTUB_NAMES = frozenset({"agent-worktrees"})
_BINSTUB_RECEIPT_SCHEMA = "agent-worktrees.project-binstub-ownership"
_BINSTUB_RECEIPT_VERSION = 1


class BinstubOwnershipError(RuntimeError):
    """A global project command is owned by another attributable payload."""


class BinstubContentError(BinstubOwnershipError):
    """A project command cannot be refreshed without overwriting unknown bytes."""


class ProjectBinstubRegistration:
    """Commit token for a registry mutation guarded by launcher ownership."""

    def __init__(self) -> None:
        self.committed = False

    def commit(self) -> None:
        self.committed = True


def is_reserved_project_command(project: str) -> bool:
    key = project.casefold() if platform.system() == "Windows" else project
    return key in _RESERVED_BINSTUB_NAMES


def _payload_root(repo_dir: str | Path | None = None) -> Path:
    explicit = os.environ.get("AGENT_WORKTREES_PAYLOAD_ROOT")
    if explicit:
        candidate = Path(explicit).expanduser()
        if (candidate / "plugin.json").is_file():
            return candidate.resolve()
    if repo_dir is not None:
        candidate = find_package_source(repo_dir).parent.parent
        if (candidate / "plugin.json").is_file():
            return candidate.resolve()
    manifest = install_dir() / "deploy-manifest.json"
    try:
        source = json.loads(manifest.read_text(encoding="utf-8"))["source"]["path"]
        candidate = Path(source).expanduser()
        if (candidate / "plugin.json").is_file():
            return candidate.resolve()
    except (OSError, KeyError, TypeError, ValueError):
        pass
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "plugin.json").is_file():
        return candidate
    raise BinstubOwnershipError(
        "cannot resolve the owning agent-worktrees payload root"
    )


def _binstub_owner(repo_dir: str | Path | None = None) -> dict[str, str]:
    root = _payload_root(repo_dir)
    try:
        manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    normalized = root.as_posix()
    match = re.search(
        r"/\.copilot/installed-plugins/([^/]+)/agent-worktrees(?:/|$)",
        normalized,
    )
    return {
        "marketplace": match.group(1) if match else "local",
        "plugin": str(manifest.get("name") or "agent-worktrees"),
        "payload_root": normalized,
        "repository": str(manifest.get("repository") or ""),
        "plugin_version": str(manifest.get("version") or ""),
    }


def _project_identity(project: str, repo_dir: str | Path | None = None) -> dict[str, str]:
    remote = ""
    path = ""
    try:
        from . import repos as repos_mod

        entry = repos_mod.find_repo(project)
        if entry is not None:
            remote = entry.remote or ""
            path = entry.local_path() or ""
    except Exception:
        pass
    if repo_dir is not None:
        path = str(Path(repo_dir).expanduser().resolve())
        try:
            result = subprocess.run(
                ["git", "-C", path, "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                remote = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    elif path:
        path = str(Path(path).expanduser().resolve())
    name = project.casefold() if platform.system() == "Windows" else project
    return {"name": name, "remote": remote, "path": path}


def _receipt_path(project: str) -> Path:
    key = project.casefold() if platform.system() == "Windows" else project
    return install_dir() / "binstub-receipts" / f"{key}.json"


def _read_receipt(project: str) -> dict | None:
    path = _receipt_path(project)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise BinstubOwnershipError(
            f"project command receipt is unreadable for {project}: {path}: {exc}"
        ) from exc
    if (
        not isinstance(data, dict)
        or data.get("schema") != _BINSTUB_RECEIPT_SCHEMA
        or data.get("version") != _BINSTUB_RECEIPT_VERSION
        or not isinstance(data.get("owner"), dict)
        or not isinstance(data.get("project"), dict)
        or not isinstance(data.get("stubs"), dict)
        or not all(
            isinstance(key, str) and isinstance(value, str)
            for section in ("owner", "project", "stubs")
            for key, value in data[section].items()
        )
    ):
        raise BinstubOwnershipError(
            f"project command receipt is invalid for {project}: {path}"
        )
    return data


def _same_identity(left: dict, right: dict, fields: tuple[str, ...]) -> bool:
    return all(left.get(field, "") == right.get(field, "") for field in fields)


def _write_receipt(project: str, data: dict) -> None:
    path = _receipt_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _stub_key(name: str) -> str:
    return name.casefold() if platform.system() == "Windows" else name


def _stub_hash(receipt: dict, path: Path) -> str | None:
    hashes = receipt.get("stubs", {})
    key = _stub_key(path.name)
    if key in hashes:
        return hashes[key]
    if platform.system() == "Windows":
        return next(
            (
                value
                for name, value in hashes.items()
                if name.casefold() == key
            ),
            None,
        )
    return None


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def _binstub_lock(project: str):
    key = project.casefold() if platform.system() == "Windows" else project
    path = install_dir() / "binstub-receipts" / f".{key}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


# A file in ~/.local/bin is one of *our* project binstubs when it carries the
# project-addressed payload routing signature. The legacy environment signature
# remains detection-only so reconciliation can migrate old stubs; generated
# stubs never emit it. Foreign stubs from other tools lack both signatures.
def _is_project_binstub(text: str) -> bool:
    if "agent-worktrees project binstub" in text:
        return True
    if "bin/payload/agent-worktrees" in text.replace("\\", "/"):
        return True
    if "agent_worktrees --project" in text:
        return True
    return "WORKTREE_PROJECT" in text and ".agent-worktrees" in text


def _is_legacy_project_binstub_for(text: str, project: str) -> bool:
    """Recognize a pre-receipt generated stub for exactly one project."""
    if "agent-worktrees project binstub" in text:
        return False
    if not _is_project_binstub(text):
        return False
    escaped = re.escape(project)
    return any(
        re.search(pattern, text)
        for pattern in (
            rf"--project(?:\s+|=)[\"']?{escaped}(?=[\"'\s]|$)",
            rf"WORKTREE_PROJECT\s*=\s*[\"']?{escaped}(?=[\"'\s]|$)",
        )
    )


def _can_migrate_legacy_project_binstub(project: str) -> bool:
    """Allow automatic transfer only for attributable pre-receipt bytes."""
    existing = [
        path
        for path, _content in _project_binstub_specs(project)
        if path.exists()
    ]
    if not existing:
        return False
    for path in existing:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            return False
        if not _is_legacy_project_binstub_for(text, project):
            return False
    return True


def _project_binstub_specs(
    project: str,
    *,
    repo_dir: str | Path | None = None,
) -> list[tuple[Path, str]]:
    """Return ``(dst_path, content)`` for each project binstub on this platform.

    Single source of truth for project-launcher content -- kept byte-compatible
    (newline-normalized) with ``install.ps1``'s ``Deploy-Binstub`` so the two
    generators never fight over the same files. Windows gets a ``.ps1`` (the
    primary pwsh resolution) plus a ``.cmd`` fallback; posix gets one bare stub.
    """
    lb = local_bin()
    payload = _payload_root(repo_dir)
    if platform.system() == "Windows":
        payload_cmd = payload / "bin" / "payload" / "agent-worktrees.cmd"
        payload_ps1 = payload / "bin" / "payload" / "agent-worktrees.ps1"
        cmd_path = str(payload_cmd).replace("%", "%%")
        ps1_path = str(payload_ps1).replace("'", "''")
        ps1_project = project.replace("'", "''")
        cmd_content = "\r\n".join([
            "@echo off",
            "rem agent-worktrees project binstub",
            'set "PYTHONUTF8=1"',
            f'set "AGENT_WORKTREES_LAUNCH_ID={project}-%RANDOM%-%RANDOM%"',
            'set "AGENT_WORKTREES_BINSTUB_STARTED=%DATE% %TIME%"',
            'set "AGENT_WORKTREES_LAUNCH_TRACE=%USERPROFILE%\\.agent-worktrees\\logs\\picker-launches.jsonl"',
            'if not exist "%USERPROFILE%\\.agent-worktrees\\logs" mkdir "%USERPROFILE%\\.agent-worktrees\\logs" >nul 2>&1',
            f'(>>"%AGENT_WORKTREES_LAUNCH_TRACE%" echo {{"event":"binstub_start","timestamp":"%AGENT_WORKTREES_BINSTUB_STARTED%","launch_id":"%AGENT_WORKTREES_LAUNCH_ID%","project":"{project}"}}) 2>nul',
            'if "%~1"=="" (',
            f'  call "%USERPROFILE%\\.agent-worktrees\\bin\\launch-session.cmd" --project {project}',
            "  exit /b %ERRORLEVEL%",
            ")",
            "rem This attributable project entry point is pinned to its owning payload.",
            f'"{cmd_path}" --project {project} %*',
            "exit /b %ERRORLEVEL%",
        ])
        ps1_content = "\r\n".join([
            "# agent-worktrees project binstub",
            "$env:PYTHONUTF8 = '1'",
            f"$env:AGENT_WORKTREES_LAUNCH_ID = '{ps1_project}-' + [guid]::NewGuid().ToString('N')",
            "$env:AGENT_WORKTREES_BINSTUB_STARTED = [DateTime]::UtcNow.ToString('o')",
            "$_awTraceDir = Join-Path $env:USERPROFILE '.agent-worktrees\\logs'",
            "$env:AGENT_WORKTREES_LAUNCH_TRACE = Join-Path $_awTraceDir 'picker-launches.jsonl'",
            "try {",
            "    [IO.Directory]::CreateDirectory($_awTraceDir) | Out-Null",
            f"    $_awEvent = [ordered]@{{ event = 'binstub_start'; timestamp = $env:AGENT_WORKTREES_BINSTUB_STARTED; launch_id = $env:AGENT_WORKTREES_LAUNCH_ID; project = '{ps1_project}' }}",
            "    [IO.File]::AppendAllText($env:AGENT_WORKTREES_LAUNCH_TRACE, ($_awEvent | ConvertTo-Json -Compress) + [Environment]::NewLine)",
            "} catch {}",
            "if ($args.Count -eq 0) {",
            f"    & \"$env:USERPROFILE\\.agent-worktrees\\bin\\launch-session.ps1\" --project '{ps1_project}'",
            "    exit $LASTEXITCODE",
            "}",
            "# This attributable project entry point is pinned to its owning payload.",
            f"& '{ps1_path}' --project '{ps1_project}' @args",
            "exit $LASTEXITCODE",
        ])
        return [
            (lb / f"{project}.ps1", ps1_content),
            (lb / f"{project}.cmd", cmd_content),
        ]
    payload_cmd = payload / "bin" / "payload" / "agent-worktrees"
    sh_content = (
        "#!/usr/bin/env bash\n"
        "# agent-worktrees project binstub\n"
        "export PYTHONUTF8=1\n"
        f"export AGENT_WORKTREES_LAUNCH_ID={shlex.quote(project)}-$$-$RANDOM-$(date +%s)\n"
        "export AGENT_WORKTREES_BINSTUB_STARTED=\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"\n"
        "export AGENT_WORKTREES_LAUNCH_TRACE=\"$HOME/.agent-worktrees/logs/picker-launches.jsonl\"\n"
        "mkdir -p \"$(dirname \"$AGENT_WORKTREES_LAUNCH_TRACE\")\" 2>/dev/null || true\n"
        f"printf '%s\\n' '{{\"event\":\"binstub_start\",\"timestamp\":\"'\"$AGENT_WORKTREES_BINSTUB_STARTED\"'\",\"launch_id\":\"'\"$AGENT_WORKTREES_LAUNCH_ID\"'\",\"project\":\"{project}\"}}' >>\"$AGENT_WORKTREES_LAUNCH_TRACE\" 2>/dev/null || true\n"
        "if [[ $# -eq 0 ]]; then\n"
        "  exec \"$HOME/.agent-worktrees/bin/launch-session.sh\" --project "
        f"{shlex.quote(project)}\n"
        "fi\n"
        "# This attributable project entry point is pinned to its owning payload.\n"
        f"exec {shlex.quote(str(payload_cmd))} --project "
        f"{shlex.quote(project)} \"$@\"\n"
    )
    return [(lb / project, sh_content)]


def _project_binstub_context(
    project: str,
    *,
    repo_dir: str | Path | None = None,
    transfer: bool = False,
) -> tuple[dict[str, str], dict[str, str], list[tuple[Path, str]]]:
    if not project or not cfg._PROJECT_NAME_RE.fullmatch(project):
        raise ValueError(
            "project command name must be 1-64 alphanumeric/dash/dot/underscore "
            f"characters: {project!r}"
        )
    command_key = project.casefold() if platform.system() == "Windows" else project
    if is_reserved_project_command(project):
        raise BinstubOwnershipError(
            f"project command name is reserved: {project}"
        )
    if platform.system() == "Windows":
        collisions = [
            name
            for name in read_projects_registry().get("projects", {})
            if name != project and name.casefold() == command_key
        ]
        if collisions:
            raise BinstubOwnershipError(
                f"project command name collides with {collisions[0]!r}: {project!r}"
            )
    local_bin().mkdir(parents=True, exist_ok=True)
    owner = _binstub_owner(repo_dir)
    project_identity = _project_identity(project, repo_dir)
    specs = _project_binstub_specs(project, repo_dir=repo_dir)
    try:
        receipt = _read_receipt(project)
    except BinstubOwnershipError:
        if not transfer:
            raise
        receipt = None
    existing = [(path, content) for path, content in specs if path.exists()]
    if receipt is not None and not transfer:
        if not _same_identity(
            receipt.get("owner", {}),
            owner,
            ("marketplace", "plugin", "payload_root", "repository"),
        ) or not _same_identity(
            receipt.get("project", {}),
            project_identity,
            ("name",),
        ):
            raise BinstubOwnershipError(
                f"refusing project command ownership transfer for {project}; "
                f"existing receipt: {_receipt_path(project)}"
            )
        modified = [
            path
            for path, _content in existing
            if _stub_hash(receipt, path) != _file_sha256(path)
        ]
        if modified:
            raise BinstubContentError(
                f"refusing to replace modified project command for {project}; "
                f"use reconcile-binstubs --transfer {project} to replace it"
            )
    elif existing and not transfer:
        raise BinstubContentError(
            f"refusing to replace unreceipted project command for {project}; "
            f"use reconcile-binstubs --transfer {project} to claim it"
        )
    return owner, project_identity, specs


def _deploy_project_binstub_unlocked(
    project: str,
    *,
    repo_dir: str | Path | None = None,
    transfer: bool = False,
) -> int:
    """Write a single project's receipt-gated binstub file(s)."""
    owner, project_identity, specs = _project_binstub_context(
        project,
        repo_dir=repo_dir,
        transfer=transfer,
    )

    is_windows = platform.system() == "Windows"
    written = 0
    hashes: dict[str, str] = {}
    for dst, content in specs:
        if _write_binstub_if_changed(dst, content):
            written += 1
        if not is_windows:
            try:
                dst.chmod(0o755)
            except OSError:
                pass
        hashes[_stub_key(dst.name)] = _file_sha256(dst)
    _write_receipt(
        project,
        {
            "schema": _BINSTUB_RECEIPT_SCHEMA,
            "version": _BINSTUB_RECEIPT_VERSION,
            "owner": owner,
            "project": project_identity,
            "runtime": {"root": str(install_dir()), "resolver": "payload-local"},
            "stubs": hashes,
        },
    )
    return written


@contextmanager
def project_binstub_registration(
    project: str,
    *,
    repo_dir: str | Path | None,
):
    """Hold ownership from registry preflight through launcher publication."""
    with _binstub_lock("__registries__"), _binstub_lock(project):
        registration = ProjectBinstubRegistration()
        content_error: BinstubContentError | None = None
        try:
            _project_binstub_context(project, repo_dir=repo_dir)
        except BinstubContentError as exc:
            content_error = exc
        yield registration
        if not registration.committed:
            return
        if content_error is not None:
            output.warn(str(content_error))
            return
        _deploy_project_binstub_unlocked(project, repo_dir=repo_dir)


def _deploy_project_binstub(
    project: str,
    *,
    repo_dir: str | Path | None = None,
    transfer: bool = False,
) -> int:
    if is_reserved_project_command(project):
        return 0
    with _binstub_lock(project):
        return _deploy_project_binstub_unlocked(
            project,
            repo_dir=repo_dir,
            transfer=transfer,
        )


def _discover_project_binstubs() -> dict[str, list[Path]]:
    """Map ``project -> [binstub paths]`` for *our* project stubs in ~/.local/bin.

    Signature-scoped (see :func:`_is_project_binstub`) so foreign binaries are
    never returned. The global ``agent-worktrees`` stub is excluded by name.
    """
    lb = local_bin()
    if not lb.is_dir():
        return {}
    is_windows = platform.system() == "Windows"
    found: dict[str, list[Path]] = {}
    for f in lb.iterdir():
        if not f.is_file():
            continue
        if is_windows:
            if f.suffix.lower() not in (".cmd", ".ps1"):
                continue
        name = f.stem if is_windows else f.name
        if is_reserved_project_command(name):
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        if _is_project_binstub(text):
            found.setdefault(name, []).append(f)
    return found


def _prune_reserved_projects_unlocked() -> list[str]:
    """Remove any reserved-name entries from projects.yaml (self-heal).

    The runtime's own name (``agent-worktrees``) is not a project. A prior buggy
    install could have self-registered it (see :func:`register_project`), leaving
    a projects.yaml entry that grows a bogus "Agent Worktrees" Terminal profile
    and a launcher backed by no repo. ``register_project`` now refuses such a
    write, but existing machines still carry the stale entry -- so drop it here,
    on every install/update, to heal them fleet-wide. Returns the names removed.
    """
    registry = read_projects_registry()
    projects = registry.get("projects", {})
    if not isinstance(projects, dict):
        return []
    is_windows = platform.system() == "Windows"
    removed = [
        name
        for name in list(projects)
        if (
            name.casefold() if is_windows else name
        ) in _RESERVED_BINSTUB_NAMES
    ]
    if removed:
        for n in removed:
            projects.pop(n, None)
        write_projects_registry(registry)
        output.changed(
            "Projects: removed non-project runtime entry "
            f"({', '.join(removed)}) from projects.yaml")
    return removed


def prune_reserved_projects() -> list[str]:
    """Remove reserved registry entries under the shared registry lock."""
    with _binstub_lock("__registries__"):
        return _prune_reserved_projects_unlocked()


def _reconcile_binstubs_unlocked() -> dict:
    """Reconcile project binstubs against the projects registry.

    Deploys receipt-owned binstubs for registered projects and removes stale
    files only when their receipt and hashes still prove ownership.
    """
    # Self-heal: a reserved runtime name must never be a registered project.
    _prune_reserved_projects_unlocked()

    registered = set(read_projects_registry().get("projects", {}).keys())
    registered_keys = {_stub_key(project) for project in registered}
    if platform.system() == "Windows":
        seen: dict[str, str] = {}
        for project in sorted(registered):
            key = project.casefold()
            if key in seen and seen[key] != project:
                raise BinstubOwnershipError(
                    "case-insensitive project command collision: "
                    f"{seen[key]!r} and {project!r}"
                )
            seen[key] = project

    added = 0
    migrated: list[str] = []
    preserved: list[str] = []
    for project in sorted(registered):
        command_key = (
            project.casefold() if platform.system() == "Windows" else project
        )
        if command_key in _RESERVED_BINSTUB_NAMES:
            continue
        try:
            added += _deploy_project_binstub(project)
        except BinstubContentError as exc:
            if _can_migrate_legacy_project_binstub(project):
                added += _deploy_project_binstub(project, transfer=True)
                migrated.append(project)
                output.changed(
                    f"Binstubs: migrated legacy project command for {project}"
                )
            else:
                preserved.append(project)
                output.warn(str(exc))
        except BinstubOwnershipError as exc:
            preserved.append(project)
            output.warn(str(exc))

    removed: list[Path] = []
    for name, paths in _discover_project_binstubs().items():
        if _stub_key(name) in registered_keys:
            continue
        with _binstub_lock(name):
            try:
                receipt = _read_receipt(name)
            except BinstubOwnershipError as exc:
                preserved.append(name)
                output.warn(str(exc))
                continue
            if receipt is None or not _same_identity(
                receipt.get("owner", {}),
                _binstub_owner(),
                ("marketplace", "plugin", "payload_root", "repository"),
            ):
                preserved.append(name)
                output.warn(
                    f"Binstubs: preserved unowned project command(s) for {name}"
                )
                continue
            if any(
                not path.exists()
                or _stub_hash(receipt, path) != _file_sha256(path)
                for path in paths
            ):
                preserved.append(name)
                output.warn(
                    f"Binstubs: preserved modified project command(s) for {name}"
                )
                continue
            for p in paths:
                try:
                    p.unlink()
                    removed.append(p)
                except OSError:
                    pass
            try:
                _receipt_path(name).unlink()
            except OSError:
                pass

    if added:
        output.ok(f"Binstubs: deployed/refreshed {added} file(s) for "
                  f"{len(registered)} registered project(s)")
    if removed:
        output.changed(
            "Binstubs: removed "
            f"{len(removed)} stale file(s): {', '.join(p.name for p in removed)}"
        )
    if not added and not removed:
        output.skipped(f"Binstubs: in sync ({len(registered)} project(s))")

    return {
        "registered": sorted(registered),
        "added": added,
        "migrated": migrated,
        "removed": [str(p) for p in removed],
        "preserved": sorted(set(preserved)),
    }


def reconcile_binstubs() -> dict:
    """Reconcile project commands while excluding concurrent registrations."""
    with _binstub_lock("__registries__"):
        return _reconcile_binstubs_unlocked()


def transfer_project_binstub(project: str) -> int:
    """Explicitly transfer a registered project command to this payload."""
    with _binstub_lock("__registries__"):
        registered = read_projects_registry().get("projects", {})
        if platform.system() == "Windows":
            project = next(
                (
                    name
                    for name in registered
                    if name.casefold() == project.casefold()
                ),
                project,
            )
        if project not in registered:
            raise BinstubOwnershipError(
                f"cannot transfer unregistered project command: {project}"
            )
        return _deploy_project_binstub(project, transfer=True)


def remove_project_binstub(project: str) -> list[Path]:
    """Remove only a project command owned byte-for-byte by this payload."""
    with _binstub_lock(project):
        receipt = _read_receipt(project)
        if receipt is None or not _same_identity(
            receipt.get("owner", {}),
            _binstub_owner(),
            ("marketplace", "plugin", "payload_root", "repository"),
        ):
            raise BinstubOwnershipError(
                f"refusing to remove unowned project command for {project}"
            )
        allowed = {
            _stub_key(path.name): path
            for path, _ in _project_binstub_specs(project)
        }
        hashes = {
            _stub_key(name): value
            for name, value in receipt.get("stubs", {}).items()
        }
        paths = [allowed[name] for name in hashes if name in allowed]
        if set(hashes) != set(allowed) or any(
            not path.exists()
            or hashes.get(_stub_key(path.name)) != _file_sha256(path)
            for path in paths
        ):
            raise BinstubOwnershipError(
                f"refusing to remove modified project command for {project}"
            )
        removed: list[Path] = []
        for path in paths:
            path.unlink()
            removed.append(path)
        _receipt_path(project).unlink()
        return removed


def deploy_binstubs(repo_dir: str | Path, project: str) -> bool:
    """Generate project-specific binstubs in ~/.local/bin/.

    Creates a thin binstub that names its project via ``--project`` (context
    otherwise resolves from CWD, git-like) and routes through the Python CLI for
    subcommand dispatch. Falls back to the shell launcher if the venv is missing,
    still carrying the project through ``--project``.

    On Windows both a ``.ps1`` (primary pwsh resolution) and a ``.cmd`` fallback
    are written; posix gets one bare stub.

    Returns True on success.
    """
    lb = local_bin()
    lb.mkdir(parents=True, exist_ok=True)

    is_windows = platform.system() == "Windows"

    # Project-specific launcher(s). Generated for every supported platform --
    # previously this only had a Windows code path, so on macOS/Linux `register`
    # silently created no launcher at all; and on Windows it emitted only the
    # `.cmd`, leaving pwsh to fall through to the child-cmd path.
    if project:
        _deploy_project_binstub(project, repo_dir=repo_dir)
        for dst, _ in _project_binstub_specs(project, repo_dir=repo_dir):
            output.ok(f"Binstub: {dst}")

    # Unified agent-worktrees command (project-agnostic; routes straight to the
    # venv console script). Global subcommands like `register <project>`,
    # `update`, and `--version` run without project context. Project-specific
    # launchers carry explicit `--project`; this stub stays unconditional.
    #
    # IMPORTANT: this content must stay byte-for-byte (newline-normalized)
    # identical to the global stub written by the native installers
    # (scripts/init.ps1, scripts/init.sh, and the static bin/agent-worktrees.cmd
    # copied by install.ps1). register/adopt/update run *through* this stub and
    # then call deploy_binstubs; if the content differs, _write_binstub_if_changed
    # rewrites the executing file mid-run and corrupts cmd.exe's byte-offset read.
    # IMPORTANT: this must stay byte-identical (newline-normalized) to the global
    # stub deployed by install.ps1/install.sh -- register/adopt/update run
    # *through* this stub, and if the content differs, _write_binstub_if_changed
    # rewrites the executing file mid-run and corrupts cmd.exe's byte-offset read.
    # To GUARANTEE identity we deploy the SAME static `bin/` file the native
    # installers copy (single source of truth) rather than a hand-duplicated
    # string -- both are the junction-free, marker-only resolver (#1106).
    _bin_assets = Path(repo_dir) / "plugins" / "agent-worktrees" / "bin"
    if not (_bin_assets / "agent-worktrees.cmd").exists():
        # Fallback: the bin/ dir ships alongside this package's source
        # (installer.py -> agent_worktrees -> src -> plugins/agent-worktrees/bin).
        _alt = Path(__file__).resolve().parent.parent.parent / "bin"
        if (_alt / "agent-worktrees.cmd").exists():
            _bin_assets = _alt
    if is_windows:
        src = _bin_assets / "agent-worktrees.cmd"
        if src.exists():
            dst = lb / "agent-worktrees.cmd"
            _write_binstub_if_changed(dst, src.read_text(encoding="utf-8"))
            output.ok(f"Binstub: {dst}")
    else:
        src = _bin_assets / "agent-worktrees"
        if src.exists():
            dst = lb / "agent-worktrees"
            _write_binstub_if_changed(dst, src.read_text(encoding="utf-8"))
            dst.chmod(0o755)
            output.ok(f"Binstub: {dst}")

    return True


def write_deploy_manifest(repo_dir: str | Path, machine: str) -> None:
    """Write the unified schema_version 3 deploy-manifest.json (atomic).

    Records the runtime source footprint (local checkout vs marketplace),
    inferred from where this installer source lives.
    """
    manifest_path = install_dir() / "deploy-manifest.json"
    plugin_dir = find_package_source(repo_dir).parent.parent
    plugin_path = str(plugin_dir)
    kind = _source_kind(plugin_path)

    version = "0.0.0"
    pyproject = plugin_dir / "pyproject.toml"
    if pyproject.exists():
        try:
            for line in pyproject.read_text().splitlines():
                if line.strip().startswith("version"):
                    version = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except Exception:
            pass

    # Git provenance only applies to a local checkout.
    commit = None
    branch = None
    dirty = False
    if kind == "local":
        try:
            r = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
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
        except Exception:
            pass

    plat = cfg.detect_platform()
    manifest = {
        "schema_version": 3,
        "service": "agent-worktrees",
        "deployed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deployed_by": f"{machine}-{plat}",
        "source": {
            "kind": kind,
            "path": plugin_path.replace("\\", "/"),
            "repo": "copilot-extensions",
            "plugin": "agent-worktrees",
            "version": version,
            "commit": commit,
            "branch": branch,
            "dirty": dirty,
        },
        "venv": str(venv_dir()).replace("\\", "/"),
        "runtime": "python",
    }

    tmp = manifest_path.with_name(manifest_path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2))
    tmp.replace(manifest_path)
    output.ok(f"Deploy manifest: {manifest_path} (source: {kind})")


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
    python = cfg.venv_python()
    lib = lib_dir() / "agent_worktrees"

    print(f"  Runtime:  {base}")
    print(f"  Project:  {project} ({proj_dir})")
    print()

    # Venv
    if python.exists():
        output.ok(f"Venv Python: {python}")
    else:
        output.err(f"Venv Python missing: {python}")

    # Package: verify the marker-selected runtime can import agent_worktrees.
    # cfg.venv_python() uses the same current-version -> completed-slot fallback
    # as the binstubs, rather than the retired stable .venv path.
    pkg_loc = ""
    if python.exists():
        try:
            probe_env = os.environ.copy()
            probe_env.pop("PYTHONPATH", None)
            r = subprocess.run(
                [str(python), "-c",
                 "import agent_worktrees, os; "
                 "print(os.path.dirname(agent_worktrees.__file__))"],
                capture_output=True, text=True, timeout=15,
                env=probe_env,
            )
            if r.returncode == 0:
                pkg_loc = r.stdout.strip()
        except Exception:
            pkg_loc = ""
    if pkg_loc:
        output.ok(f"Package importable: {pkg_loc}")
    else:
        if lib.exists():
            output.warn(f"Stale legacy package present at {lib}; active runtime import failed")
        output.err(
            "Package missing: active runtime cannot import agent_worktrees "
            f"(checked {python} and {lib})"
        )

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

    # Machine identity is delivered live via the session-machine sessionStart
    # hook (dotfiles#1056), not a deployed machine.instructions.md / AGENTS.md.
    # Report the hook injector's presence instead of the retired files.
    machine_hook = bin_dir() / (
        "session-machine.ps1" if platform.system() == "Windows"
        else "session-machine.sh"
    )
    _has_machines_yaml = False
    try:
        _reg = read_projects_registry()
        _proj_entry = _reg.get("projects", {}).get(project, {})
        _my = _proj_entry.get("machines_yaml")
        if _my and Path(_my).exists():
            _has_machines_yaml = True
    except Exception:
        pass

    if machine_hook.exists():
        if _has_machines_yaml:
            output.ok("machine identity via session-machine hook (machines.yaml configured)")
        else:
            output.skipped("session-machine hook present (no machines.yaml -- emits nothing)")
    else:
        output.warn("session-machine hook missing (run install or update)")


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

        from . import config_migrations

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"projects": {}}
        # Lazy schema migration (in memory, never persists / never raises).
        data = config_migrations.migrate_loaded(data, config_migrations.SCHEMA_PROJECTS)
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


def write_projects_registry(registry: dict, path: Path | None = None) -> None:
    """Write the projects registry back to projects.yaml.

    ``path`` defaults to :func:`projects_yaml_path`; callers that resolve the
    registry location independently (e.g. the reconciliation doctor) may pass
    an explicit path so read and write stay symmetric.
    """
    if path is None:
        path = projects_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# ~/.agent-worktrees/projects.yaml",
        "# Adoption/launch registry: lean, name-keyed to repos.yaml (the single",
        "# owning store of anchor/path/branch). Carries only harness/adoption-",
        "# runtime facts (config_dir, wsl, base_repo, elevated, expose_agent,",
        "# display_name).",
        "",
        "schema_version: 2",
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
    expose_agent: bool | None = None,
    base_repo: bool | None = None,
    elevated: bool | None = None,
    display_name: str | None = None,
    wsl_state: str | None = None,
    wsl_distro: str | None = None,
    wsl_path: str | None = None,
) -> None:
    """Add or update a lean project entry in projects.yaml.

    projects.yaml is the **adoption/launch** registry; it defers to
    ``repos.yaml`` (the single owning store) for identity/location facts --
    ``anchor``, ``machines_yaml``, ``default_branch`` -- which every consumer
    resolves from the repo registry by the project *name*. This function
    therefore records only the harness/adoption-runtime facts repos.yaml can't
    model. ``repo_dir``/``default_branch`` are accepted for call-site
    compatibility but are **not** persisted (they live in repos.yaml).

    Fields default to **preserve-existing** (``None``) so a context-less
    re-register from the marketplace payload cannot drop a prior adoption fact.

    Parameters
    ----------
    expose_agent
        Whether agent-bridge should expose a same-machine agent for this
        project. ``None`` preserves the existing value (or ``True`` when new);
        the authoritative source is ``repos.yaml`` ``agent`` (the caller
        resolves it and passes it in).
    base_repo
        When true, the project is adopted in **base-repo (no-worktree)** mode.
        ``None`` preserves the existing value.
    elevated
        When true, agent-bridge runs this project's agent elevated. ``None``
        preserves the existing value.
    display_name
        Optional harness-level display casing for terminal profiles / shortcuts
        (e.g. ``"SPO.Core"`` for a ``spo-core`` slug). When omitted the
        generator title-cases the project name. Preserved across re-registration
        when not explicitly provided.
    wsl_state
        WSL adoption state: ``"adopted"`` (full install exists in WSL),
        ``"bootstrap"`` (bootstrap stub deployed), or *None* (no WSL).
    wsl_distro
        WSL distribution name (e.g. ``"Ubuntu"``).  Stored so terminal
        profiles can target a specific distro with ``wsl.exe -d``.
    wsl_path
        Path to the repo anchor inside WSL (e.g. ``~/src/my-project``).
    """
    # The runtime's own name is NOT a project. ``~/.agent-worktrees`` is the
    # shared *install* dir (it carries a global ``config.yaml``), so the
    # installer's "does ``~/.<cwd>/config.yaml`` exist?" project inference gives
    # a false positive when ``install.ps1`` runs from a dir named
    # ``agent-worktrees`` (the plugin checkout or the venv), self-registering the
    # manager as a launchable project -- which then grows a bogus "Agent
    # Worktrees" Terminal profile and a projects.yaml entry backed by no repo.
    # ``_RESERVED_BINSTUB_NAMES`` already made such an entry inert to binstub
    # reconciliation; refuse the registry *write* here too so it never appears in
    # the first place (the single owning writer is the right place to enforce it).
    is_windows = platform.system() == "Windows"
    command_key = project.casefold() if is_windows else project
    if command_key in _RESERVED_BINSTUB_NAMES:
        output.skipped(
            f"'{project}' is the runtime itself, not a project -- "
            "skipping projects.yaml registration")
        return

    registry = read_projects_registry()
    if is_windows:
        collision = next(
            (
                name
                for name in registry["projects"]
                if name != project and name.casefold() == command_key
            ),
            None,
        )
        if collision is not None:
            raise BinstubOwnershipError(
                f"project command name collides with {collision!r}: {project!r}"
            )
    existing = registry["projects"].get(project, {})
    if not isinstance(existing, dict):
        existing = {}

    eff_expose = expose_agent if expose_agent is not None else bool(
        existing.get("expose_agent", True)
    )
    eff_base = base_repo if base_repo is not None else bool(
        existing.get("base_repo", False)
    )
    eff_elevated = elevated if elevated is not None else bool(
        existing.get("elevated", False)
    )

    entry: dict = {
        "config_dir": f"~/.{project}",
        "expose_agent": eff_expose,
        "registered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if eff_base:
        entry["base_repo"] = True
    if eff_elevated:
        entry["elevated"] = True

    # Display casing: explicit value wins, else preserve any prior override.
    resolved_display = display_name or existing.get("display_name")
    if resolved_display:
        entry["display_name"] = resolved_display

    # Preserve existing WSL state when re-registering from Windows
    existing_wsl = existing.get("wsl")

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
