"""Front-door invocation routing extracted from ``__main__``."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as cfg, git_ops, output
from . import installer as inst


def _core():
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = getattr(_core(), name, None)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def _extract_project_flag(args_list: list[str]) -> tuple[list[str], str | None]:
    """Pop a global --project/-p flag from args, returning (remaining, value).

    Supports ``--project NAME``, ``--project=NAME``, ``-p NAME``. Only the
    first occurrence is consumed; the rest pass through to the subcommand.
    """
    out: list[str] = []
    project: str | None = None
    i = 0
    while i < len(args_list):
        arg = args_list[i]
        if project is None and arg in ("--project", "-p"):
            if i + 1 < len(args_list):
                project = args_list[i + 1]
                i += 2
                continue
            i += 1
            continue
        if project is None and arg.startswith("--project="):
            project = arg.split("=", 1)[1]
            i += 1
            continue
        out.append(arg)
        i += 1
    return out, (project.strip() if project else None)


# ── `<repo> <slug>` command-surface router ───────────────────────────────────
# The router DERIVES its routable set from the installed ``agent-<slug>``
# binstubs (so a newly-installed agent-* plugin auto-gets a `<repo> <slug>`
# namespace), unioned with a curated core set as a floor. A leading token that
# names a routable slug -- and is NOT a real worktrees verb (the collision guard)
# -- is dispatched to that sibling plugin. `worktrees` folds back into this
# binstub so `<repo> worktrees <verb>` == the bare `<repo> <verb>` alias.
_CORE_SLUGS = frozenset(
    {
        "worktrees",
        "bridge",
        "ssh",
        "dispatch",
        "codespaces",
        "containers",
        "logger",
        "vault",
        "mcp",
    }
)

# Slugs whose sibling plugin consumes a top-level ``--project`` (bridge overrides
# its remote-resolve target project; codespaces chdir's to the project checkout).
# The router injects ``--project <repo>`` only for these; every other slug routes
# as a cwd-preserving alias (so plugins that don't declare --project never see it
# and can't argparse-error on it).
_PROJECT_ARG_SLUGS = frozenset({"bridge", "codespaces"})


def _installed_sibling_slugs() -> set[str]:
    """Discover ``<slug>`` for every installed ``agent-<slug>`` binstub in
    ~/.local/bin, so the routable set is derived from what's installed rather than
    hardcoded. Excludes ``agent-worktrees`` itself (which folds back)."""
    import re as _re

    slugs: set[str] = set()
    try:
        entries = list(inst.local_bin().iterdir())
    except OSError:
        return slugs
    for p in entries:
        m = _re.match(
            r"^agent-([a-z0-9][a-z0-9-]*?)(?:\.(?:ps1|cmd|exe|sh))?$",
            p.name.lower(),
        )
        if m and m.group(1) != "worktrees":
            slugs.add(m.group(1))
    return slugs


_WORKTREES_VERBS: set[str] | None = None


def _worktrees_verbs() -> set[str]:
    """The agent-worktrees subcommand names (cached). The router excludes these
    from routing so a plugin slug can never shadow a real worktrees verb."""
    override = _core_helper("_worktrees_verbs", _worktrees_verbs)
    if override is not _worktrees_verbs:
        return override()
    global _WORKTREES_VERBS
    if _WORKTREES_VERBS is None:
        import argparse

        try:
            parser = _core().build_parser()
            subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
            _WORKTREES_VERBS = set(subs[0].choices) if subs else set()
        except Exception:
            _WORKTREES_VERBS = set()
    return _WORKTREES_VERBS


def _canonical_slug(tok: str) -> str | None:
    """Map a leading token to a routable slug, tolerating singular/plural
    variance so the surface is forgiving of the plugins' inconsistent
    pluralization (``bridge``/``ssh`` singular; ``codespaces``/``containers``/
    ``worktrees`` plural). Returns the canonical slug (matching the actual
    ``agent-<slug>`` binstub) or None. The caller still gates on the collision
    guard (a real worktrees verb is never passed here)."""
    if tok in _CORE_SLUGS:
        return tok
    siblings = _core_helper("_installed_sibling_slugs", _installed_sibling_slugs)()
    if tok in siblings:
        return tok
    alt = tok[:-1] if (tok.endswith("s") and len(tok) > 1) else tok + "s"
    if alt in _CORE_SLUGS or alt in siblings:
        return alt
    return None


def _sibling_binstub(slug: str) -> Path | None:
    """Locate the ``agent-<slug>`` binstub in ~/.local/bin (it runs in its own
    venv, so the router shells out to it rather than importing it)."""
    lb = inst.local_bin()
    cand = lb / (f"agent-{slug}.ps1" if platform.system() == "Windows" else f"agent-{slug}")
    return cand if cand.exists() else None


def _route_to_sibling_plugin(slug: str, project: str | None, rest: list[str]) -> int:
    """Re-dispatch ``<repo> <slug> …`` to the ``agent-<slug>`` binstub,
    project-pinned when a project is known. Returns the child's exit code."""
    stub = _core_helper("_sibling_binstub", _sibling_binstub)(slug)
    if stub is None:
        print(
            f"  \u2717 '{slug}' needs the agent-{slug} command, which is not "
            f"installed here.\n"
            f"  \u2717 Install its plugin, or run 'agent-worktrees --help' for "
            f"local commands.",
            file=sys.stderr,
        )
        return 1
    forwarded: list[str] = []
    child_env = os.environ.copy()
    if project:
        forwarded += ["--project", project]
        child_env["AGENT_WORKTREES_PROJECT_ROUTED"] = "1"
    else:
        child_env.pop("AGENT_WORKTREES_PROJECT_ROUTED", None)
    forwarded += list(rest)
    if platform.system() == "Windows":
        pwsh = shutil.which("pwsh") or shutil.which("powershell") or "pwsh"
        cmd = [pwsh, "-NoProfile", "-NoLogo", "-File", str(stub), *forwarded]
    else:
        cmd = [str(stub), *forwarded]
    return subprocess.run(cmd, env=child_env).returncode


def _safe_cwd() -> Path | None:
    """Return ``Path.cwd()``, or ``None`` if the current directory is gone.

    ``os.getcwd()`` raises ``FileNotFoundError`` when the process's working
    directory has been removed out from under it. This happens when a plugin
    hook re-invokes this CLI during ``copilot plugin update``: Copilot deletes
    and re-vendors the payload directory the hook inherited as its cwd, so the
    hook subprocess ends up with a vanished cwd. Treat that as "no project
    context" rather than crashing startup (dotfiles#989).
    """
    override = _core_helper("_safe_cwd", _safe_cwd)
    if override is not _safe_cwd:
        return override()
    try:
        return Path.cwd()
    except OSError:
        return None


def _git_toplevel(path: Path | None) -> Path | None:
    """Return the git toplevel of ``path`` resolved to its anchor, or None.

    ``path`` may be ``None`` (e.g. ``_safe_cwd()`` returned ``None`` because the
    caller's cwd was deleted); in that case there is nothing to resolve.
    """
    override = _core_helper("_git_toplevel", _git_toplevel)
    if override is not _git_toplevel:
        return override(path)
    if path is None:
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            env=git_ops.repository_identity_env(),
            stdin=subprocess.DEVNULL,
        )
        if r.returncode == 0 and r.stdout.strip():
            return git_ops.resolve_to_anchor(Path(r.stdout.strip()).resolve())
    except Exception:
        pass
    return None


_NO_PROJECT_COMMANDS = {
    "--version",
    "-V",
    "--help",
    "-h",
    "repos",
    "accounts",
    "related",
    "install",
    "register",
    "hook",
    "knowledge",
    "reconcile-marketplaces",
    "picker",
    "doctor",
    "hygiene",
    "reap-shells",
    "status-updater",
    "status-monitor",
    "reconcile-sessions",
    "status-monitor-restart",
    "restart",
    "register-session",
    "handoff-trace",
    "session-lifecycle",
    "deregister-session",
    "session-binding",
    "session-recovery",
    "session-lineage",
    "installer-readiness",
    "bind-session",
    "bind-nudge",
    "history-digest",
    "note-handoff",
    "session-role",
    "head-session",
    "worktree-lineage",
    "worktree-status-bundle",
    "worktree-status-audit",
    "conclude-session",
    "conclude-disposable",
    "link-succession",
    "config-migrate",
    "session-tail",
    "session-lock",
    "machine-context",
    "reconcile-binstubs",
    "register-project-entry",
    "terminal-fragment",
}


def _is_no_project_invocation(args_list: list[str]) -> bool:
    """Whether this exact argv can run without resolving one project."""
    if not args_list:
        return False
    command = args_list[0]
    if command in _NO_PROJECT_COMMANDS or command.startswith("-"):
        return True
    if command == "list-sessions" and "--all-projects" in args_list[1:]:
        return True
    return command == "config-root" and any(
        arg == "--destination" or arg.startswith("--destination=") for arg in args_list[1:]
    )


_PROJECT_IRRELEVANT_COMMANDS = frozenset(
    {
        "repos",
        "accounts",
        "picker",
        "--version",
        "-V",
        "--help",
        "-h",
    }
)


def _is_registered_project(name: str) -> bool:
    """True if *name* is a known adopted project or a registered repo.

    A real project binstub only ever injects a REGISTERED project as
    ``--project``, so this is how the guard tells a legitimate (binstub-injected
    or real) project name from a likely hand-typed mistake -- without requiring
    any binstub or environment cooperation.
    """
    try:
        if name in inst.read_projects_registry().get("projects", {}):
            return True
    except Exception:
        pass
    try:
        from . import repos as _repos

        if name in _repos.read_registry().repos:
            return True
    except Exception:
        pass
    return False


def _guard_project_scope(project_override: str | None, command: str | None) -> None:
    """Softly note a likely-mistaken ``--project`` on a machine-global verb."""
    os.environ.pop("AGENT_WORKTREES_PROJECT_ROUTED", None)
    if not project_override:
        return
    if command not in _PROJECT_IRRELEVANT_COMMANDS:
        return
    if _is_registered_project(project_override):
        return
    print(
        f"note: --project {project_override!r} has no effect on the "
        f"machine-global command '{command}' and is not a known project; "
        f"ignoring it.",
        file=sys.stderr,
    )


def _anchor_for_project(name: str) -> Path | None:
    """Return the anchor checkout path for project *name*, or ``None``."""
    try:
        projects = inst.read_projects_registry().get("projects", {})
        entry = projects.get(name)
        if isinstance(entry, dict) and entry.get("anchor"):
            p = Path(entry["anchor"])
            if p.is_dir():
                return p.resolve()
    except Exception:
        pass
    try:
        anchor = cfg._resolve_anchor_from_registry(name, cfg.detect_platform())
        if anchor and Path(anchor).is_dir():
            return Path(anchor).resolve()
    except Exception:
        pass
    return None


def _reverse_lookup_project(anchor: Path) -> str | None:
    """Map an anchor checkout path back to its adopted project name, or ``None``."""
    target = git_ops._normalize_wt_path(str(anchor))
    try:
        projects = inst.read_projects_registry().get("projects", {})
    except Exception:
        projects = {}
    for name, entry in projects.items():
        a = entry.get("anchor") if isinstance(entry, dict) else None
        if a and git_ops._normalize_wt_path(str(Path(a))) == target:
            return name
    try:
        from . import repos as _repos

        registry = _repos.read_registry()
        plat = cfg.detect_platform()
        for name in registry.repos:
            a = registry.repos[name].local_path(plat)
            if a and git_ops._normalize_wt_path(str(Path(a))) == target:
                return name
    except Exception:
        pass
    return None


def _cwd_is_inside_project(anchor: Path) -> bool:
    """Return True if the current directory belongs to the repo at *anchor*."""
    top = _git_toplevel(_safe_cwd())
    if top is None:
        return False
    return git_ops._normalize_wt_path(str(top)) == git_ops._normalize_wt_path(str(anchor))


def _resolve_active_project(
    project_override: str | None,
) -> tuple[str | None, Path | None]:
    """Resolve ``(project, anchor)`` the way git resolves its repo."""
    if project_override:
        return project_override, _anchor_for_project(project_override)
    cwd_anchor = _git_toplevel(_safe_cwd())
    if cwd_anchor is not None:
        name = _reverse_lookup_project(cwd_anchor)
        if name:
            return name, None
    return None, None


def cmd_help_unrouted(requested: str | None = None) -> int:
    """Help shown when ``agent-worktrees`` runs without project context."""
    out = sys.stderr
    print("agent-worktrees -- worktree session lifecycle manager", file=out)
    print(file=out)
    if requested:
        print(
            f"Could not resolve a project for '{requested}'. Context is "
            f"discovered from the current directory (like git), but this "
            f"directory is not inside an adopted repo or worktree, and no "
            f"--project was given.",
            file=out,
        )
    else:
        print(
            "Could not resolve a project. Context is discovered from the "
            "current directory (like git), but this directory is not inside "
            "an adopted repo or worktree. Run from inside one, use a project "
            "binstub, or pass --project <name>.",
            file=out,
        )
    print(file=out)

    print("Commands:", file=out)
    groups = [
        ("Worktree lifecycle", "worktree, create, list, status, push-changes, finalize, cleanup"),
        (
            "Project / install",
            "register, install, uninstall, update, install-status, get, validate",
        ),
        ("Namespaces", "services ..., repos ..."),
        ("Diagnostics", "activity"),
        ("Info", "--version, --help"),
    ]
    for title, cmds in groups:
        print(f"  {title + ':':<22}{cmds}", file=out)
    print(file=out)

    try:
        projects = inst.read_projects_registry().get("projects", {})
    except Exception:
        projects = {}
    cwd_anchor = _git_toplevel(_safe_cwd())

    matched: str | None = None
    if cwd_anchor is not None:
        cwd_norm = _core()._normalize_path(str(cwd_anchor))
        for name, entry in projects.items():
            anchor = entry.get("anchor") if isinstance(entry, dict) else None
            if not anchor:
                continue
            if _core()._normalize_path(str(Path(anchor).resolve())) == cwd_norm:
                matched = name
                break

    print("Recommended next step:", file=out)
    if matched:
        print(
            f"  You are inside the '{matched}' project. Run:\n"
            f"    {matched}                         # interactive picker\n"
            f"    agent-worktrees --project {matched} worktree list",
            file=out,
        )
    elif cwd_anchor is not None:
        print(
            f"  This git repo ({cwd_anchor.name}) is not adopted yet. Adopt it:\n"
            f"    agent-worktrees register {cwd_anchor.name}",
            file=out,
        )
    elif projects:
        names = ", ".join(sorted(projects))
        print(
            f"  Pick an adopted project (run its binstub or use --project):\n"
            f"    Adopted: {names}\n"
            f"    e.g. agent-worktrees --project {sorted(projects)[0]} worktree list",
            file=out,
        )
    else:
        print(
            "  No projects adopted yet. From inside a git repo, run:\n"
            "    agent-worktrees register <name>",
            file=out,
        )
    return 1


_WORKTREE_MANAGER_BIN = "worktree-manager"
_WORKTREE_MANAGER_MIN_PICKER_VERSION = (0, 1, 0, 21)
_WORKTREE_MANAGER_ENGINE_ARGV_ENV = "WORKTREE_MANAGER_ENGINE_ARGV"
_WORKTREE_MANAGER_ROOT_ENV = "WORKTREE_MANAGER_ROOT"
_WORKTREE_MANAGER_REPO_URL = "https://github.com/ThomasMichon/copilot-extensions"
_WORKTREE_MANAGER_INSTALL_SH = (
    "curl -fsSL https://raw.githubusercontent.com/ThomasMichon/"
    "copilot-extensions/main/worktree-manager/bootstrap.sh | bash"
)
_WORKTREE_MANAGER_INSTALL_PS1 = (
    "iex (irm https://raw.githubusercontent.com/ThomasMichon/"
    "copilot-extensions/main/worktree-manager/bootstrap.ps1)"
)


def _worktree_manager_path() -> str | None:
    """Locate the out-of-plugin Worktree Manager binstub on PATH, if installed."""
    return shutil.which(_WORKTREE_MANAGER_BIN)


def _launch_probe_env() -> dict[str, str]:
    """Subprocess env for Manager/launch probes."""
    env = {**os.environ, "PYTHONUTF8": "1"}
    env.pop("PYTHONHOME", None)
    env.pop("UV_INTERNAL__PYTHONHOME", None)
    return env


def _probe_worktree_manager_version(
    command: list[str],
) -> tuple[tuple[int, int, int, int] | None, subprocess.CompletedProcess[str] | None]:
    """Run a fast ``--version`` probe and parse a comparable version tuple."""
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            env=_launch_probe_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    if proc.returncode != 0:
        return None, proc
    match = re.search(
        r"\b(\d+)\.(\d+)\.(\d+)(?:-dev(\d+))?\b",
        proc.stdout or "",
    )
    if match is None:
        return None, proc
    major, minor, patch = (int(match.group(i)) for i in range(1, 4))
    dev = int(match.group(4)) if match.group(4) is not None else 1_000_000
    return (major, minor, patch, dev), proc


def _usable_worktree_manager() -> str | None:
    """The Manager binstub, but only if it is actually invocable (DQ8 guard)."""
    override = _core_helper("_usable_worktree_manager", _usable_worktree_manager)
    if override is not _usable_worktree_manager:
        return override()
    mgr = _core_helper("_worktree_manager_path", _worktree_manager_path)()
    if not mgr:
        return None
    version, proc = _core_helper(
        "_probe_worktree_manager_version", _probe_worktree_manager_version
    )([mgr, "--version"])
    if proc is None:
        output.err(
            f"Ignoring an unusable '{_WORKTREE_MANAGER_BIN}' on PATH ({mgr}): "
            "it could not be run. Falling back to the bundled picker."
        )
        return None
    if version is None and proc.returncode != 0:
        output.err(
            f"Ignoring a broken '{_WORKTREE_MANAGER_BIN}' on PATH ({mgr}): it "
            f"failed a --version health check (exit {proc.returncode}). This is "
            "usually a stale binstub from an old install; reinstall or remove "
            "it. Falling back to the bundled picker."
        )
        return None
    if version is None:
        output.err(
            f"Ignoring an incompatible '{_WORKTREE_MANAGER_BIN}' on PATH ({mgr}): "
            "its --version output did not include a supported version. Falling "
            "back to the bundled picker."
        )
        return None
    if version < _WORKTREE_MANAGER_MIN_PICKER_VERSION:
        output.err(
            f"Ignoring an older '{_WORKTREE_MANAGER_BIN}' on PATH ({mgr}): "
            f"production Picker handoff requires 0.1.0-dev21 or newer. "
            "Falling back to the bundled picker."
        )
        return None
    return mgr


def _worktree_manager_root() -> Path:
    configured = os.environ.get(_WORKTREE_MANAGER_ROOT_ENV, "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".worktree-manager"


def _current_version_slot(root: Path) -> Path | None:
    try:
        version = (root / "current-version").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not version:
        return None
    versions_dir = (root / "versions").resolve()
    slot = (versions_dir / version).resolve()
    try:
        slot.relative_to(versions_dir)
    except ValueError:
        return None
    return slot


def _usable_worktree_manager_launcher_dir() -> Path | None:
    """Return the relocated Worktree Manager launcher directory when usable."""
    override = _core_helper(
        "_usable_worktree_manager_launcher_dir", _usable_worktree_manager_launcher_dir
    )
    if override is not _usable_worktree_manager_launcher_dir:
        return override()
    slot = _core_helper("_current_version_slot", _current_version_slot)(
        _core_helper("_worktree_manager_root", _worktree_manager_root)()
    )
    if slot is None:
        return None
    version, proc = _core_helper(
        "_probe_worktree_manager_version", _probe_worktree_manager_version
    )(
        ["uv", "run", "--quiet", "--project", str(slot), "python", "-m", "worktree_manager", "--version"]
    )
    if proc is None:
        output.warn(
            "Ignoring an unusable Worktree Manager install at "
            f"{slot}: its versioned runtime could not be started."
        )
        return None
    if version is None and proc.returncode != 0:
        output.warn(
            "Ignoring a broken Worktree Manager install at "
            f"{slot}: its versioned runtime failed a --version health check "
            f"(exit {proc.returncode})."
        )
        return None
    if version is None:
        output.warn(
            "Ignoring an incompatible Worktree Manager install at "
            f"{slot}: its --version output did not include a supported version."
        )
        return None
    return slot / "bin"


def _agent_worktrees_launch_command(project: str | None) -> list[str]:
    argv = [sys.executable, "-m", "agent_worktrees"]
    if project:
        argv += ["--project", project]
    return argv


def _resolve_direct_launch_plan(
    project: str | None, passthrough: list[str]
) -> tuple[dict[str, object], str | None]:
    """Resolve the same launch plan the mux launchers consume, but in Python."""
    resolve_args = _agent_worktrees_launch_command(project)
    resolve_args += ["resolve", "--no-mux", *passthrough]
    proc = subprocess.run(
        resolve_args,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        env=_launch_probe_env(),
    )
    if proc.returncode != 0:
        return {"action": "none", "exit_code": proc.returncode}, project
    if not proc.stdout.strip():
        output.err("resolve produced no output on stdout")
        return {"action": "none", "exit_code": 1}, project
    plan = json.loads(proc.stdout)
    if isinstance(plan, dict) and isinstance(plan.get("launch"), dict):
        plan = plan["launch"]
    if not isinstance(plan, dict):
        raise RuntimeError("resolve did not return a launch plan object")
    plan_project = plan.get("project")
    if isinstance(plan_project, str) and plan_project:
        project = plan_project
    return plan, project


def _wait_for_launch_child(proc: subprocess.Popen) -> int:
    try:
        return proc.wait()
    except KeyboardInterrupt:
        try:
            return proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            return 130


def _run_post_exit_for_direct_launch(project: str | None, worktree_id: str) -> None:
    post_args = _agent_worktrees_launch_command(project)
    post_args += ["post-exit", worktree_id]
    result = subprocess.run(post_args, env=_launch_probe_env())
    if result.returncode != 0:
        output.warn(
            f"Post-exit finalization failed (exit code {result.returncode}). "
            "Run 'agent-worktrees finalize' to retry."
        )


def _run_direct_launch_fallback(project: str | None, passthrough: list[str]) -> int:
    """Launch Copilot directly from the resolved plan when mux support is absent."""
    while True:
        plan, project = _resolve_direct_launch_plan(project, passthrough)
        action = str(plan.get("action", "none"))
        if action == "none":
            return int(plan.get("exit_code", 0) or 0)
        if action == "refresh":
            if _core()._env_get("WORKTREE_NO_UPDATE") != "1":
                update_args = _agent_worktrees_launch_command(project)
                update_args.append("update")
                result = subprocess.run(update_args, env=_launch_probe_env())
                if result.returncode != 0:
                    output.warn("Full update returned non-zero -- continuing to relaunch")
            continue
        if action == "remote":
            ssh_alias = plan.get("ssh_alias")
            remote_cmd = plan.get("remote_command")
            if not isinstance(ssh_alias, str) or not isinstance(remote_cmd, str):
                output.err("Remote launch plan is missing ssh handoff details.")
                return 1
            proc = subprocess.Popen(["ssh", "-t", ssh_alias, remote_cmd])
            return _wait_for_launch_child(proc)
        if action != "exec":
            output.err(f"Unknown launch action: {action}")
            return 1
        work_dir = plan.get("work_dir")
        cmd = plan.get("cmd")
        if not isinstance(work_dir, str) or not work_dir:
            output.err("Resolved launch plan is missing work_dir.")
            return 1
        if not isinstance(cmd, list) or not all(isinstance(item, str) for item in cmd):
            output.err("Resolved launch plan is missing an executable command.")
            return 1
        child_env = _launch_probe_env()
        plan_env = plan.get("env")
        if isinstance(plan_env, dict):
            for key, value in plan_env.items():
                if isinstance(key, str):
                    child_env[key] = str(value)
        child_env.pop("WORKTREE_ID", None)
        child_env.pop("WORKTREE_PROJECT", None)
        proc = subprocess.Popen(cmd, cwd=work_dir, env=child_env)
        rc = _wait_for_launch_child(proc)
        worktree_id = plan.get("worktree_id")
        if isinstance(worktree_id, str) and worktree_id and plan.get("post_exit"):
            _run_post_exit_for_direct_launch(project, worktree_id)
        return rc


def _exec_worktree_manager(
    mgr: str, project: str | None, *, subcommand: list[str] | None = None
) -> int:
    """Hand an invocation off to the Worktree Manager (the seam)."""
    argv = [mgr]
    if subcommand:
        argv += list(subcommand)
    if project:
        argv += ["--project", project]
    env = {
        **os.environ,
        _WORKTREE_MANAGER_ENGINE_ARGV_ENV: json.dumps([sys.executable, "-m", "agent_worktrees"]),
    }
    if platform.system() == "Windows":
        proc = subprocess.Popen(argv, env=env)
        try:
            rc = proc.wait()
        except KeyboardInterrupt:
            try:
                rc = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = 130
        sys.exit(rc)
    os.execvpe(mgr, argv, env)
    return 1


def _bundled_picker_available() -> bool:
    """Return True while the interactive Picker still ships inside the plugin."""
    try:
        return (Path(__file__).resolve().parent / "picker_tui" / "__init__.py").exists()
    except Exception:
        return False


def cmd_manager_install_trigger(project: str | None) -> int:
    """The install trigger: guide the user to install the Worktree Manager."""
    out = sys.stderr
    name = project or "agent-worktrees"
    is_windows = platform.system() == "Windows"
    install_cmd = _WORKTREE_MANAGER_INSTALL_PS1 if is_windows else _WORKTREE_MANAGER_INSTALL_SH

    output.header(f"{name} -- the interactive front-end has moved")
    print(
        "The Picker / session launcher now ships as the standalone Worktree "
        "Manager, installed and updated out-of-band from the plugin. Install "
        "it to launch and manage worktrees interactively again.",
        file=out,
    )
    print(file=out)
    print(f"  Source (verify this is ours): {_WORKTREE_MANAGER_REPO_URL}", file=out)
    print(file=out)
    print("  Install / update:", file=out)
    print(f"    {install_cmd}", file=out)
    print(file=out)
    print(
        f"Once installed, run this binstub again -- bare `{name}` will open the "
        f"Manager. Agents are unaffected: `{name} <verb>` (e.g. list, create, "
        "finalize) works headless without the Manager.",
        file=out,
    )
    return 0


def _is_headless_project() -> bool:
    """Return True if the active project is configured headless (CLI-only)."""
    try:
        return cfg.load_config().headless
    except Exception:
        return False


def _is_noninteractive_invocation() -> bool:
    """True when stdin is not a real, attached terminal."""
    try:
        return not sys.stdin.isatty()
    except Exception:
        return False


def cmd_noninteractive_bare() -> int:
    """Bare invocation of a binstub with no attached interactive terminal."""
    try:
        project = cfg.project_name()
    except Exception:
        project = "<project>"
    print(
        f"'{project}' was invoked without an attached interactive terminal -- "
        f"refusing to open the Manager/Picker (nothing would be able to drive "
        f"or close it).",
        file=sys.stderr,
    )
    print(file=sys.stderr)
    rc = _core().cmd_worktree_dispatch(["list"])
    print(file=sys.stderr)
    print(
        f"Manage it with: {project} worktree <create|status|push|finalize|cleanup>",
        file=sys.stderr,
    )
    return rc


def cmd_headless_bare() -> int:
    """Bare invocation of a headless project's binstub."""
    try:
        project = cfg.project_name()
    except Exception:
        project = "<project>"
    print(
        f"'{project}' is a headless (CLI-only) project -- it is driven via "
        f"worktree commands, not an interactive session.",
        file=sys.stderr,
    )
    print(file=sys.stderr)
    rc = _core().cmd_worktree_dispatch(["list"])
    print(file=sys.stderr)
    print(
        f"Manage it with: {project} worktree <create|status|push|finalize|cleanup>",
        file=sys.stderr,
    )
    return rc


def dispatch_bare_invocation(has_project: bool) -> int:
    """Handle the bare no-args front door."""
    is_noninteractive = _core_helper("_is_noninteractive_invocation", _is_noninteractive_invocation)
    noninteractive_bare = _core_helper("cmd_noninteractive_bare", cmd_noninteractive_bare)
    help_unrouted = _core_helper("cmd_help_unrouted", cmd_help_unrouted)
    is_headless = _core_helper("_is_headless_project", _is_headless_project)
    headless_bare = _core_helper("cmd_headless_bare", cmd_headless_bare)
    usable_manager = _core_helper("_usable_worktree_manager", _usable_worktree_manager)
    exec_worktree_manager = _core_helper("_exec_worktree_manager", _exec_worktree_manager)
    bundled_picker_available = _core_helper("_bundled_picker_available", _bundled_picker_available)
    manager_install_trigger = _core_helper(
        "cmd_manager_install_trigger", cmd_manager_install_trigger
    )
    if is_noninteractive():
        if has_project:
            return noninteractive_bare()
        return help_unrouted()
    if has_project:
        if is_headless():
            return headless_bare()
        mgr = usable_manager()
        if mgr:
            return exec_worktree_manager(mgr, cfg.active_project())
        if bundled_picker_available():
            return _core().cmd_launch([])
        return manager_install_trigger(cfg.active_project())
    mgr = usable_manager()
    if mgr:
        return exec_worktree_manager(mgr, None)
    if bundled_picker_available():
        return help_unrouted()
    return manager_install_trigger(None)
