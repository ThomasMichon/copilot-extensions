"""Front-door invocation routing extracted from ``__main__``."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as cfg, git_ops
from . import installer as inst
from . import sessions


def _core():
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = vars(_core()).get(name)
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

# Slugs the router pins to the resolved project's checkout by chdir'ing the
# child process, rather than forwarding ``--project`` for the sibling to
# resolve itself (#2426 candidate 2). ``ssh`` is below ``agent-worktrees`` in
# the plugin-stack tier (docs/patterns/a-la-carte-independence.md), so it must
# never reach upward to resolve a project name itself; agent-worktrees already
# owns that registry, so the router resolves it and hands the child an
# already-correct cwd. Scoped per-verb, not per-slug: only ``mesh-status``/
# ``refresh-mesh`` resolve ``machines.yaml`` by ascending from cwd -- every
# other ``ssh`` verb (``emit-profile``, ``doctor``, ``verify``, ``explore``,
# ``restore-host``, ``copilot-config``) either takes its own explicit paths
# (relative to the caller's actual cwd, which chdir'ing would silently break)
# or operates on machine-global state with no project concept at all, so it
# must stay a plain cwd-preserving alias -- including never failing closed on
# an unresolvable project, since those verbs don't need one. An unresolvable
# project fails *only* a matched verb's routed call closed, rather than
# silently running it against the caller's original cwd.
_PROJECT_CHDIR_VERBS: dict[str, frozenset[str]] = {
    "ssh": frozenset({"mesh-status", "refresh-mesh"}),
}


def _chdir_verb_for(slug: str, rest: list[str]) -> str | None:
    """Return the first non-flag token in ``rest`` iff it is a verb that
    ``slug`` chdir-pins (see ``_PROJECT_CHDIR_VERBS``), else ``None``."""
    verbs = _PROJECT_CHDIR_VERBS.get(slug)
    if not verbs:
        return None
    for tok in rest:
        if tok.startswith("-"):
            continue
        return tok if tok in verbs else None
    return None


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
        try:
            _WORKTREES_VERBS = set(_core()._ALL_KNOWN_VERBS)
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


def _resolve_project_checkout(project: str) -> Path | None:
    """Resolve ``project``'s checkout path from agent-worktrees' own repo
    registry (in-process -- agent-worktrees owns this registry, so it never
    needs to shell out to itself). Returns ``None`` if unresolved."""
    from . import repos

    path = repos.resolve_path(project)
    if not path:
        return None
    candidate = Path(path)
    return candidate if candidate.is_dir() else None


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
    cwd: str | None = None
    chdir_verb = _chdir_verb_for(slug, rest) if project else None
    if chdir_verb is not None:
        # Resolve and chdir the child process ourselves (agent-worktrees owns
        # the repo registry) rather than forwarding --project for a lower-tier
        # sibling to resolve itself -- see _PROJECT_CHDIR_VERBS. Fail the
        # routed call closed on an unresolvable project instead of silently
        # running it against the caller's original cwd (the wrong-project
        # behavior this routing exists to prevent). Every other ``slug`` verb
        # falls through below as a plain cwd-preserving alias.
        resolved = _core_helper(
            "_resolve_project_checkout", _resolve_project_checkout
        )(project)
        if resolved is None:
            print(
                f"  \u2717 --project {project!r}: no checkout found for "
                f"'{slug} {chdir_verb}' to run against.",
                file=sys.stderr,
            )
            return 1
        cwd = str(resolved)
        child_env.pop("AGENT_WORKTREES_PROJECT_ROUTED", None)
    elif project and slug in _PROJECT_ARG_SLUGS:
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
    return subprocess.run(cmd, env=child_env, cwd=cwd).returncode


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
    def _rev_parse(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", *args],
            capture_output=True, text=True, timeout=5,
            env=git_ops.repository_identity_env(), stdin=subprocess.DEVNULL,
        )

    try:
        r = _rev_parse("--show-toplevel")
        if r.returncode == 0 and r.stdout.strip():
            return git_ops.resolve_to_anchor(Path(r.stdout.strip()).resolve())
    except Exception:
        pass
    # ``--show-toplevel`` always fails for a *bare* anchor (agent-worktrees'
    # own pattern once worktrees are attached) -- mirror git_ops.py's
    # credential-helper pin and use ``--git-dir`` so it still resolves as its
    # own project rather than "not inside an adopted repo".
    try:
        r = _rev_parse("--is-bare-repository", "--git-dir")
        lines = r.stdout.strip().splitlines()
        if r.returncode == 0 and len(lines) == 2 and lines[0].strip() == "true":
            git_dir = Path(lines[1].strip())
            if not git_dir.is_absolute():
                git_dir = (path / git_dir).resolve()
            root = git_dir.parent if git_dir.name == ".git" else git_dir
            return git_ops.resolve_to_anchor(root)
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
    "forks",
    "copilot-identity",
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
    "cancel-handoff",
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
    "activity-prune-worker",
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
    if command == "claims" and args_list[1:3] == ["find", "pr"]:
        # Scans every adopted project's tracking store, never the CWD's own:
        # it must give the same answer from any directory.
        return True
    if command == "get" and any(
        arg == "--session-id" or arg.startswith("--session-id=") for arg in args_list[1:]
    ):
        # `get <key> --session-id <sid>` resolves its own project via the
        # session binding (cmd_get's own _activate_session_binding /
        # find_worktree_id_by_session fallback) -- a caller sitting in a
        # neutral/HOME cwd (the bare-resume case a session id exists
        # precisely to recover from) must reach that resolution instead of
        # being rejected here before cmd_get() ever runs.
        return True
    if command == "identifiers" and "--repo" in args_list[1:]:
        # `identifiers sweep --repo NAME` names its own sweep target
        # explicitly and never consults CWD -- it must work from any
        # directory (the documented live-guard invocation from an
        # unrelated repo's own pre-push hook). The bare form (no --repo)
        # still auto-resolves its target from CWD via `cfg.active_project()`,
        # so it keeps needing ordinary project resolution here.
        return True
    return command == "config-root" and any(
        arg == "--destination" or arg.startswith("--destination=") for arg in args_list[1:]
    )


_PROJECT_IRRELEVANT_COMMANDS = frozenset(
    {
        "repos",
        "accounts",
        "identifiers",
        "forks",
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
        cwd_norm = sessions._normalize_path(str(cwd_anchor))
        for name, entry in projects.items():
            anchor = entry.get("anchor") if isinstance(entry, dict) else None
            if not anchor:
                continue
            if sessions._normalize_path(str(Path(anchor).resolve())) == cwd_norm:
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


# See manager_launch_cli.py (extracted from this module to stay under its
# own zero-headroom 1000-line cap, same move already made once before for
# control_plane_providers.py): Worktree Manager/control-plane-provider
# resolution, direct-launch fallback, and bare-invocation dispatch. Every
# name below is re-exported so every existing ``front_door_cli.<name>``
# caller (``__main__``'s flat re-export block, ``managed_mux_registry.py``,
# tests) keeps working unchanged -- a pure file-boundary move.
from . import manager_launch_cli as _mlc  # noqa: E402

_WORKTREE_MANAGER_BIN = _mlc._WORKTREE_MANAGER_BIN
_WORKTREE_MANAGER_MIN_PICKER_VERSION = _mlc._WORKTREE_MANAGER_MIN_PICKER_VERSION
_WORKTREE_MANAGER_ENGINE_ARGV_ENV = _mlc._WORKTREE_MANAGER_ENGINE_ARGV_ENV
_WORKTREE_MANAGER_ROOT_ENV = _mlc._WORKTREE_MANAGER_ROOT_ENV
_CONTROL_PLANE_PROVIDER_ENV = _mlc._CONTROL_PLANE_PROVIDER_ENV
_WORKTREE_MANAGER_REPO_URL = _mlc._WORKTREE_MANAGER_REPO_URL
_WORKTREE_MANAGER_INSTALL_SH = _mlc._WORKTREE_MANAGER_INSTALL_SH
_WORKTREE_MANAGER_INSTALL_PS1 = _mlc._WORKTREE_MANAGER_INSTALL_PS1
_CONTROL_PLANE_PROVIDERS_DIR_ENV = _mlc._CONTROL_PLANE_PROVIDERS_DIR_ENV
_ControlPlaneProviderManifest = _mlc._ControlPlaneProviderManifest
_parse_comparable_version = _mlc._parse_comparable_version
_control_plane_providers_dir = _mlc._control_plane_providers_dir
_LeakedTestTmpPathManifestError = _mlc._LeakedTestTmpPathManifestError
_parse_control_plane_provider_manifest = _mlc._parse_control_plane_provider_manifest
_discover_control_plane_provider_manifests = _mlc._discover_control_plane_provider_manifests
_select_control_plane_provider_manifest = _mlc._select_control_plane_provider_manifest
_provider_command_display_name = _mlc._provider_command_display_name
_worktree_manager_path = _mlc._worktree_manager_path
_launch_probe_env = _mlc._launch_probe_env
_probe_worktree_manager_version = _mlc._probe_worktree_manager_version
_usable_worktree_manager = _mlc._usable_worktree_manager
_worktree_manager_root = _mlc._worktree_manager_root
_current_version_slot = _mlc._current_version_slot
_usable_worktree_manager_launcher_dir = _mlc._usable_worktree_manager_launcher_dir
_agent_worktrees_launch_command = _mlc._agent_worktrees_launch_command
_resolve_direct_launch_plan = _mlc._resolve_direct_launch_plan
_wait_for_launch_child = _mlc._wait_for_launch_child
_run_post_exit_for_direct_launch = _mlc._run_post_exit_for_direct_launch
_run_direct_launch_fallback = _mlc._run_direct_launch_fallback
_exec_worktree_manager = _mlc._exec_worktree_manager
_bundled_picker_available = _mlc._bundled_picker_available
cmd_manager_install_trigger = _mlc.cmd_manager_install_trigger
_is_headless_project = _mlc._is_headless_project
_is_noninteractive_invocation = _mlc._is_noninteractive_invocation
cmd_noninteractive_bare = _mlc.cmd_noninteractive_bare
cmd_headless_bare = _mlc.cmd_headless_bare
dispatch_bare_invocation = _mlc.dispatch_bare_invocation


