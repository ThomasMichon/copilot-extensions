"""Install / register / uninstall CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from . import git_ops, installer as inst, output
from . import config as cfg


def _core():
    from . import __main__ as core
    return core


def _core_helper(name: str, local):
    candidate = vars(_core()).get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def _clarify_registration_account(*args, **kwargs): return _core()._clarify_registration_account(*args, **kwargs)
def _find_repo_dir(*args, **kwargs): return _core()._find_repo_dir(*args, **kwargs)
def _resolve_remote_default_branch(*args, **kwargs): return _core()._resolve_remote_default_branch(*args, **kwargs)
def _write_config(*args, **kwargs): return _core()._write_config(*args, **kwargs)
def _write_global_config(*args, **kwargs): return _core()._write_global_config(*args, **kwargs)
def _refresh_terminal_profiles(*args, **kwargs): return _core()._refresh_terminal_profiles(*args, **kwargs)


def add_parsers(sub) -> None:
    p = sub.add_parser("install", help="Deploy worktree manager (shared runtime + project)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--machine", default=None)
    p = sub.add_parser("register", help="Adopt a repo as a worktree project (repo taken from cwd by default)", description=("Adopt a repo as an agent-worktrees project. The repository is taken from the CURRENT WORKING DIRECTORY (the git root of cwd, resolved to the main checkout) -- so run this FROM INSIDE the target repo's checkout. project_name is only the LABEL; it does NOT locate the repo, so `register <name>` run from a different repo adopts THAT repo's path under <name>. To adopt a repo you are not standing in, pass --repo-dir <path> (or use `agent-worktrees repos add <name> <path> --class worktree`)."))
    p.add_argument("project_name", help="Project LABEL (e.g. 'my-project') -- NOT a repo locator; the path comes from cwd (or --repo-dir)")
    p.add_argument("--repo-dir", default=None, help="Explicit repo path to adopt (overrides cwd detection); use this to register a repo you are not standing in")
    p.add_argument("--default-branch", default=None, help="Default branch (auto-detected from origin/HEAD if omitted)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--machine", default=None)
    p.add_argument("--headless", action="store_true", help="Adopt as a CLI-only project: the bare binstub lists worktrees instead of launching an interactive session")
    p.add_argument("--no-agent", action="store_true", help="Adopt without an agent-bridge agent (reference-style: worktree-managed but no agent). Default is to expose one.")
    p.add_argument("--agent", action="store_true", help="Force exposing an agent-bridge agent (overrides a repos.yaml agent:false classification).")
    p.add_argument("--base-repo", action="store_true", help="Adopt in base-repo (no-worktree) mode: the anchor checkout is used directly and no worktree is created. For repos that can't support worktrees (e.g. an enlistment monorepo). Also set repos.<name>.base_repo in the user-local ~/.<project>/config.yaml.")
    p.add_argument("--elevated", action="store_true", help="Record that agent-bridge should run this project's agent in an elevated (admin) context.")
    p = sub.add_parser("uninstall", help="Remove worktree manager")
    p.add_argument("--remove-config", action="store_true")

def _validate_machine_registry(
    repo_dir: Path,
    machine: str,
) -> cfg.MachineEntry | None:
    """Look up *machine* in machines.yaml by key or alias.  Returns the
    entry or prints an error and returns None."""
    try:
        registry = cfg.load_machines_yaml(repo_dir)
    except FileNotFoundError:
        output.err(f"Machine registry not found at {cfg.machines_yaml_path(repo_dir)}")
        output.info("Create .agent-worktrees/machines.yaml with an entry for this machine.")
        return None
    except (RuntimeError, ValueError) as exc:
        output.err(str(exc))
        return None

    entry = cfg.find_machine_entry(registry, machine)
    if entry is None:
        output.err(f"Machine '{machine}' not found in machines.yaml")
        output.info("Add an entry for this machine and retry:")
        output.info("  machines:")
        output.info(f"    {machine}:")
        output.info(f"      display_name: {machine.title()}")
        output.info('      environment: "<OS and version>"')
        output.info(
            '      # alias: "<multi-machine system-name>"  '
            "# colloquial name if different from hostname"
        )
        return None

    return entry


# Ownership marker embedded in generated instruction files
_INSTRUCTION_MARKER = "<!-- managed by agent-worktrees -->"

# worktree-status-core: MIGRATED to the session-conduct sessionStart hook
# (dotfiles#1054 / effort instructions-to-hooks). The guidance text now lives in
# ``plugins/agent-worktrees/scripts/conduct/worktree-conduct.md`` and is emitted
# as ``additionalContext`` by the cwd-gated session-conduct hook, instead of
# being materialized into ``~/.{project}/.github/instructions/``. The deploy path
# now only retires any stale copy of the old file (see
# :func:`_remove_managed_instruction`).


# account-conduct: MIGRATED to the session-conduct sessionStart hook
# (dotfiles#1053 / effort instructions-to-hooks). The guidance text now lives in
# ``plugins/agent-worktrees/scripts/conduct/account-conduct.md`` and is emitted
# as ``additionalContext`` by the session-conduct hook -- cwd-gated to managed
# projects and launch-path-independent -- instead of being materialized into
# ``~/.{project}/.github/instructions/`` and loaded via
# COPILOT_CUSTOM_INSTRUCTIONS_DIRS. The deploy path now only retires any stale
# copy of the old file (see :func:`_remove_managed_instruction`).


def _remove_managed_file(path: Path, label: str) -> None:
    """Remove a previously-deployed managed file (idempotent, marker-guarded).

    Only removes a file carrying the agent-worktrees ownership marker, so an
    unmarked user file is never touched. Used to retire content that has migrated
    to a sessionStart hook.
    """
    if not path.exists():
        return
    try:
        if _INSTRUCTION_MARKER in path.read_text():
            path.unlink()
            output.changed(f"removed migrated {label} (now a sessionStart hook)")
    except OSError:
        pass


def _remove_managed_instruction(proj_dir: Path, name: str) -> None:
    """Remove a previously-deployed managed ``*.instructions.md`` (idempotent).

    Marker-guarded: only removes a file carrying the agent-worktrees ownership
    marker, so an unmarked user file is never touched. Used to retire fragments
    that have migrated to a sessionStart hook.
    """
    _remove_managed_file(proj_dir / ".github" / "instructions" / name, name)


def _gh_env_for_repo(target: str) -> tuple[dict[str, str], str | None, bool]:
    """Build the environment for running ``gh`` against *target* under the
    account that owns it, via **token injection** (never ``gh auth switch``).

    ``gh``'s active account is global per-machine, so switching it is racy on a
    shared box. Instead resolve ``account_for_github_slug(target)`` and mint its
    token (``gh auth token --user <login>``), injected as ``GH_TOKEN`` -- a
    side-effect-free, race-safe override that leaves the active account alone.

    Returns ``(env, login, injected)``: a copy of ``os.environ`` with
    ``GH_TOKEN`` set when an account resolved *and* a token minted; ``login`` is
    the resolved account (or None); ``injected`` reports whether ``GH_TOKEN``
    was set. When nothing resolves the ambient env is returned unchanged (caller
    falls back to the active gh account).
    """
    from . import repos

    env = dict(os.environ)
    login = repos.account_for_github_slug(target)
    injected = False
    if login:
        token = git_ops.gh_token_for_account(login)
        if token:
            env["GH_TOKEN"] = token
            injected = True
    return env, login, injected


def _deploy_copilot_instructions(
    proj_dir: Path,
    entry: cfg.MachineEntry,
    project: str = "",
) -> None:
    """Retire migrated managed instruction files + clean up legacy artifacts.

    All the guidance this used to materialize into the
    COPILOT_CUSTOM_INSTRUCTIONS_DIRS directory now arrives via sessionStart hooks
    that emit ``additionalContext`` (effort instructions-to-hooks):

    - machine identity (``machine.instructions.md`` + the nested-discovery
      ``AGENTS.md``) -> the ``session-machine`` hook / ``machine-context`` command
      (dotfiles#1056), computed live from ``machines.yaml``;
    - account- and worktree-conduct -> the ``session-conduct`` hook.

    So this function no longer *writes* those files; it retires any stale copy we
    previously deployed (marker-guarded, so unmarked user files are never
    touched). ``entry`` is retained for call-site compatibility but unused. The
    retired extension-reload warning's old per-project file is also removed here.
    """
    # Machine identity migrated to the session-machine sessionStart hook
    # (dotfiles#1056): retire the stale per-project file + nested AGENTS.md.
    instr_dir = proj_dir / ".github" / "instructions"
    _remove_managed_instruction(proj_dir, "machine.instructions.md")
    _remove_managed_file(proj_dir / "AGENTS.md", "AGENTS.md")

    # worktree-conduct migrated to the session-conduct sessionStart hook
    # (dotfiles#1054): retire any stale per-project file we used to deploy.
    _remove_managed_instruction(proj_dir, "worktree-conduct.instructions.md")

    # account-conduct migrated to the session-conduct sessionStart hook
    # (dotfiles#1053): retire any stale per-project file we used to deploy.
    _remove_managed_instruction(proj_dir, "account-conduct.instructions.md")

    # Retire any stale per-project copy of the removed extension-reload warning.
    _remove_managed_instruction(proj_dir, "ext-reload-hang.instructions.md")

    # Clean up stale ssh.instructions.md from previous versions
    ssh_instr_path = instr_dir / "ssh.instructions.md"
    if ssh_instr_path.exists():
        try:
            text = ssh_instr_path.read_text()
            if _INSTRUCTION_MARKER in text:
                ssh_instr_path.unlink()
                output.changed("removed stale ssh.instructions.md (now a skill)")
        except OSError:
            pass

    # Clean up legacy files from previous deploy strategies
    for legacy_name in ("copilot-instructions.md",):
        legacy = proj_dir / legacy_name
        if legacy.exists():
            legacy.unlink()
            output.changed(f"removed legacy {legacy_name}")


def _cleanup_stale_instructions(proj_dir: Path) -> None:
    """Remove generated instruction files when machines.yaml is absent.

    Only removes files that contain the agent-worktrees ownership marker,
    so user-created instruction files are preserved.
    """
    candidates = [
        proj_dir / ".github" / "instructions" / "machine.instructions.md",
        proj_dir / ".github" / "instructions" / "ssh.instructions.md",
        proj_dir / ".github" / "instructions" / "worktree-conduct.instructions.md",
        proj_dir / ".github" / "instructions" / "account-conduct.instructions.md",
        proj_dir / ".github" / "instructions" / "ext-reload-hang.instructions.md",
        proj_dir / "AGENTS.md",
    ]
    for path in candidates:
        if path.exists():
            try:
                content = path.read_text()
                if _INSTRUCTION_MARKER in content:
                    path.unlink()
                    output.changed(f"removed stale {path.name} (no machines.yaml)")
            except OSError:
                pass


def _prepare_namespaced_project_state(
    project: str,
    repo_dir: Path,
    platform_name: str,
) -> bool:
    """Publish repository identity before the first cell-local state lookup."""
    from . import project_state

    if not project_state.namespaced():
        return True
    from . import repos as repos_mod

    existing = repos_mod.find_repo(project)
    remote = ""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            remote = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    if not remote and existing is not None:
        remote = existing.remote
    try:
        repos_mod.add_repo(
            project,
            str(repo_dir),
            repo_class=(
                existing.repo_class
                if existing is not None and existing.repo_class != "reference"
                else "worktree"
            ),
            remote=remote,
            agent=existing.agent if existing is not None else True,
            plat=platform_name,
        )
        project_state.ensure_project_state(project)
    except ValueError as error:
        output.err(f"Could not establish repository identity for {project}: {error}")
        return False
    return True


def _ensure_ado_pr_cli(pr_cfg) -> None:
    """Provision the Azure DevOps CLI prereq for a repo that manages PRs via ADO.

    ``create-pr``/``pr-merge`` shell out to ``az repos pr ...`` (the
    ``azure-devops`` provider), which needs the ``azure-devops`` az extension.
    A machine missing it makes ``az`` prompt interactively and fail under
    automation, so provision it at install/adopt time. Best-effort: warn,
    never abort.
    """
    try:
        if not (pr_cfg.enabled and pr_cfg.provider == "azure-devops"):
            return
        from .providers.azure_devops import ensure_cli_ready

        ok, msg = ensure_cli_ready()
        (output.ok if ok else output.warn)(f"Azure DevOps CLI: {msg}")
    except Exception as e:  # best-effort preflight -- never block install/adopt
        output.warn(f"Could not verify Azure DevOps CLI setup: {e}")


def cmd_install(args: argparse.Namespace) -> int:
    """Deploy the worktree manager shared runtime + register current project."""
    project = cfg.project_name()
    output.header("Installing Agent Worktrees")

    # Prereqs
    missing = inst.check_prereqs()
    if missing:
        output.err(f"Missing prerequisites: {', '.join(missing)}")
        return 1

    # Determine repo dir (we must be running from the repo)
    repo_dir = _find_repo_dir()
    if not repo_dir:
        output.err("Cannot determine repo root. Run from within the source repo.")
        return 1

    machine = args.machine or cfg.detect_machine(repo_dir)
    plat = cfg.detect_platform()
    print(f"  Machine:  {machine}")
    print(f"  Platform: {plat}")
    print(f"  Project:  {project}")
    print(f"  Repo:     {repo_dir}")

    # Machine registry is optional -- repos without machines.yaml still work
    machine_entry: cfg.MachineEntry | None = None
    machines_yaml = repo_dir / "machines.yaml"
    if machines_yaml.exists():
        machine_entry = _core_helper("_validate_machine_registry", _validate_machine_registry)(repo_dir, machine)
        if machine_entry is None:
            return 1

    # Create shared runtime directories
    runtime_dir = cfg._home() / ".agent-worktrees"
    for d in [runtime_dir, runtime_dir / "bin", inst.local_bin()]:
        d.mkdir(parents=True, exist_ok=True)

    if not _core_helper("_prepare_namespaced_project_state", _prepare_namespaced_project_state)(project, repo_dir, plat):
        return 1

    # Create per-project directories
    proj_dir = cfg.project_dir(project)
    for d in [proj_dir, proj_dir / "worktrees"]:
        d.mkdir(parents=True, exist_ok=True)

    # Deploy global machine-wide config (lowest tier), then per-project config
    config_path = proj_dir / "config.yaml"
    _core_helper("_write_global_config", _write_global_config)(machine, plat, repo_dir.parent)
    if not config_path.exists() or args.force:
        _core_helper("_write_config", _write_config)(config_path, repo_dir, machine, plat, project)
    else:
        output.skipped(f"Config exists at {config_path} (use --force to overwrite)")

    # Deploy copilot-instructions.md from machine registry (if available)
    if machine_entry is not None:
        _core_helper("_deploy_copilot_instructions", _deploy_copilot_instructions)(proj_dir, machine_entry, project=project)
    else:
        _core_helper("_cleanup_stale_instructions", _cleanup_stale_instructions)(proj_dir)

    # Create venv first (shared runtime) -- package install targets the venv
    if not inst.create_venv():
        return 1

    # Deploy Python package into the venv (shared runtime)
    if not inst.deploy_package(repo_dir):
        return 1

    # Deploy wrappers (shared runtime)
    if not inst.deploy_wrappers(repo_dir):
        return 1

    # Register repository identity before project state or its attributable
    # launcher, so every first-install receipt is stable.
    from . import repos as _repos

    with inst.project_binstub_registration(project, repo_dir=repo_dir) as binstub_registration:
        _entry = _repos.find_repo(project)
        _reg_class = (
            _entry.repo_class
            if _entry is not None and _entry.repo_class != "reference"
            else "worktree"
        )
        _remote = ""
        try:
            _remote_result = subprocess.run(
                ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if _remote_result.returncode == 0:
                _remote = _remote_result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            _repos.add_repo(
                project,
                str(repo_dir),
                repo_class=_reg_class,
                remote=_remote,
                agent=_entry.agent if _entry else True,
                plat=plat,
            )
        except Exception as exc:
            output.err(f"Could not record repository identity for {project}: {exc}")
            return 1

        # Update projects registry. Honor the repos.yaml agent-exposure
        # classification (default ON) so reference-only repos stay hidden.
        _entry = _repos.find_repo(project)
        _expose_agent = _entry.agent if _entry else True
        inst.register_project(project, repo_dir=repo_dir, expose_agent=_expose_agent)
        binstub_registration.commit()

    # Project publication above owns its launcher; this call refreshes only the
    # project-agnostic global tool shim.
    if not inst.deploy_binstubs(repo_dir, project=""):
        return 1

    # Reconcile all project binstubs against the registry (add missing, incl.
    # the .ps1 primary on Windows; remove stubs for deregistered projects).
    inst.reconcile_binstubs()

    # Run post-install hook (project-specific, e.g. icon deployment)
    try:
        config = cfg.load_config(config_path)
        hook = config.default_repo.post_install_hook.get(plat)
        if hook:
            cmd = [
                s.replace("{repo_dir}", str(repo_dir)).replace("{runtime_dir}", str(runtime_dir))
                for s in hook
            ]
            result = subprocess.run(cmd, cwd=str(repo_dir))
            if result.returncode == 0:
                output.ok("Post-install hook completed")
            else:
                output.warn(f"Post-install hook exited with code {result.returncode}")
    except Exception:
        pass  # hook is optional

    # Deploy manifest (shared runtime)
    inst.write_deploy_manifest(repo_dir, machine)

    # PR-workflow git hooks are an ADOPT concern (register), NOT install.
    # install/update are machine-local and read-only w.r.t. the repo's git: if
    # PR mode is on but the managed shims are missing or a stale core.hooksPath
    # shadows them, WARN -- never inject or mutate repo git here. Arming (and
    # clearing a stale core.hooksPath) happens on adopt: run '<repo> register'
    # or 'agent-worktrees hook install'.
    try:
        cfg_for_hooks = cfg.load_config(config_path)
        if cfg_for_hooks.default_repo.pr.enabled:
            from . import hooks as _hooks

            present, stale = _hooks.hook_health(repo_dir)
            if not present:
                output.warn(
                    "PR mode is enabled but the PR-workflow git hooks are not "
                    "installed. Re-adopt the repo ('agent-worktrees register') "
                    "or run 'agent-worktrees hook install' to arm them."
                )
            if stale:
                output.warn(
                    f"core.hooksPath is set to '{stale}', which shadows the "
                    "managed .git/hooks shims -- the PR-workflow guard will not "
                    "run. Re-adopt ('agent-worktrees register') to clear it."
                )
    except Exception as e:
        output.warn(f"Could not check git-hook health: {e}")

    # Azure DevOps PR provider prereq (machine-local): ensure the az
    # 'azure-devops' extension so create-pr/pr-merge work on this machine.
    try:
        _core_helper("_ensure_ado_pr_cli", _ensure_ado_pr_cli)(cfg.load_config(config_path).default_repo.pr)
    except Exception:
        pass

    print()
    output.ok("Installation complete")
    print(f"  Runtime:   {runtime_dir}")
    print(f"  Project:   {proj_dir}")
    print(f"  Usage:     {project}")
    return 0



def cmd_register(args: argparse.Namespace) -> int:
    """Register a project with the worktree manager (create config + binstub)."""
    project = args.project_name
    output.header(f"Registering project: {project}")

    if not cfg._PROJECT_NAME_RE.match(project):
        output.err(f"Invalid project name: {project!r}")
        return 1

    # Determine repo dir
    if getattr(args, "repo_dir", None):
        repo_dir = Path(args.repo_dir).resolve()
        if not (repo_dir / ".git").exists() and not (repo_dir / ".git").is_file():
            output.err(f"Not a git repository: {repo_dir}")
            return 1
    else:
        # For `register`, the current directory is authoritative -- resolve the
        # git root of cwd first. _find_repo_dir() walks up from the installed
        # module location (~/.agent-worktrees/...) before checking cwd, which
        # can resolve to an unrelated repo (e.g. when $HOME itself is a git
        # repo, as with dotfiles-in-$HOME setups).
        repo_dir = None
        try:
            r = subprocess.run(
                ["git", "-C", str(Path.cwd()), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                # Normalize through resolve_to_anchor so that running from
                # inside a linked worktree resolves back to the main checkout,
                # matching _find_repo_dir()'s behavior. Without this, registering
                # from an active worktree would anchor to the ephemeral path.
                repo_dir = git_ops.resolve_to_anchor(Path(r.stdout.strip()).resolve())
        except Exception:
            pass
        if not repo_dir:
            repo_dir = _find_repo_dir()
        if not repo_dir:
            repo_dir = Path.cwd()
            output.warn(f"Using current directory as repo root: {repo_dir}")

    machine = args.machine or cfg.detect_machine(repo_dir)
    plat = cfg.detect_platform()

    # Auto-detect default branch if not specified. Respect the REMOTE's
    # configured default -- never a stale local `master` (dotfiles#1046):
    # origin/HEAD, else `ls-remote --symref` (authoritative even when the local
    # origin/HEAD was never set), else a main-first remote-ref probe.
    default_branch = getattr(args, "default_branch", None) or None
    if not default_branch:
        default_branch = _resolve_remote_default_branch(str(repo_dir), "origin", allow_remote=True)
    if not default_branch:
        # No remote signal (remote-less or offline repo) -- probe LOCAL heads,
        # main-first. Never fall back to the current branch, which is often a
        # feature branch in worktree workflows and would record the wrong default.
        for candidate in ("main", "master"):
            r = git_ops.git(
                "rev-parse",
                "--verify",
                "--quiet",
                f"refs/heads/{candidate}",
                cwd=str(repo_dir),
                check=False,
            )
            if r.returncode == 0:
                default_branch = candidate
                break
    if not default_branch:
        # Undeterminable -- ask explicitly rather than guessing.
        output.warn(
            "Could not detect default branch (no remote default, no local main or master branch)"
        )
        branch_input = input("  Default branch name: ").strip()
        if branch_input:
            default_branch = branch_input
        else:
            default_branch = "main"
            output.warn(f"Assuming default branch: {default_branch}")

    print(f"  Repo:     {repo_dir}")
    print(f"  Branch:   {default_branch}")
    print(f"  Machine:  {machine}")
    print(f"  Platform: {plat}")

    # Machine registry is optional -- external repos may not have machines.yaml
    machine_entry: cfg.MachineEntry | None = None
    machines_yaml = repo_dir / "machines.yaml"
    if machines_yaml.exists():
        machine_entry = _core_helper("_validate_machine_registry", _validate_machine_registry)(repo_dir, machine)
        if machine_entry is None:
            return 1

    if not _core_helper("_prepare_namespaced_project_state", _prepare_namespaced_project_state)(project, repo_dir, plat):
        return 1

    # Create project directory
    proj_dir = cfg.project_dir(project)
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "worktrees").mkdir(exist_ok=True)

    # Write global machine-wide config (lowest tier), then per-project config
    config_path = proj_dir / "config.yaml"
    _core_helper("_write_global_config", _write_global_config)(machine, plat, repo_dir.parent)
    # Resolve agent exposure up front: explicit flags win, else the repos.yaml
    # classification, else default ON (adopting a repo means working in it).
    # A no-agent adoption is worked programmatically (create --json) rather than
    # launched from the terminal dropdown, so it also gets NO terminal profile.
    if getattr(args, "no_agent", False):
        expose_agent = False
    elif getattr(args, "agent", False):
        expose_agent = True
    else:
        from . import repos as _repos

        _entry = _repos.find_repo(project)
        expose_agent = _entry.agent if _entry else True

    if not config_path.exists() or args.force:
        _core_helper("_write_config", _write_config)(
            config_path,
            repo_dir,
            machine,
            plat,
            project,
            default_branch,
            headless=getattr(args, "headless", False),
            no_terminal_profile=not expose_agent,
        )
    else:
        output.skipped(f"Config exists at {config_path} (use --force to overwrite)")

    # Deploy copilot-instructions.md from machine registry
    if machine_entry is not None:
        _core_helper("_deploy_copilot_instructions", _deploy_copilot_instructions)(proj_dir, machine_entry, project=project)
    else:
        _core_helper("_cleanup_stale_instructions", _cleanup_stale_instructions)(proj_dir)

    # Update projects registry -- include WSL state only when actually in WSL
    wsl_state: str | None = None
    wsl_distro: str | None = None
    wsl_path: str | None = None
    wsl_distro_name = os.environ.get("WSL_DISTRO_NAME")
    if wsl_distro_name:
        wsl_state = "adopted"
        wsl_distro = wsl_distro_name
        wsl_path = str(repo_dir)

    # #282: ensure the repo has a repos.yaml entry so CWD->project discovery
    # resolves it. projects.yaml is deliberately lean -- it defers identity and
    # location (anchor / default_branch) to repos.yaml, the single owning store
    # -- and the reverse-lookup that answers "which project am I in?" keys off
    # repos.yaml's per-platform anchor. Without an entry a freshly registered
    # repo is reachable only via its own binstub or --project, never a bare
    # `agent-worktrees <verb>` from its own directory (the confusing "not adopted
    # yet -- run register" after register already ran). Record the anchor under
    # the CURRENT platform (so a WSL adoption is filed under 'wsl', not 'linux'),
    # merging into any existing entry and preserving a deliberate non-worktree
    # class (add_repo only upgrades away from the 'reference' default).
    with inst.project_binstub_registration(project, repo_dir=repo_dir) as binstub_registration:
        try:
            from . import repos as _repos_reg

            _existing_repo = _repos_reg.find_repo(project)
            _reg_class = (
                _existing_repo.repo_class
                if _existing_repo is not None and _existing_repo.repo_class != "reference"
                else "worktree"
            )
            _reg_remote_url = ""
            _rru = subprocess.run(
                ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if _rru.returncode == 0:
                _reg_remote_url = _rru.stdout.strip()
            _repos_reg.add_repo(
                project,
                str(repo_dir),
                repo_class=_reg_class,
                remote=_reg_remote_url,
                default_branch=default_branch,
                agent=expose_agent,
                plat=plat,
            )
        except Exception as _e:
            output.err(f"Could not record repos.yaml entry for '{project}': {_e}")
            return 1

        inst.register_project(
            project,
            repo_dir=repo_dir,
            default_branch=default_branch,
            expose_agent=expose_agent,
            base_repo=getattr(args, "base_repo", False),
            elevated=getattr(args, "elevated", False),
            wsl_state=wsl_state,
            wsl_distro=wsl_distro,
            wsl_path=wsl_path,
        )
        binstub_registration.commit()

    if not inst.deploy_binstubs(repo_dir, project=""):
        return 1

    # #537: adopting a repo is the moment to pin its gh account. If the account
    # only resolves by falling back to an org owner that isn't an authenticated
    # gh account, clarify it (prompt / persist an account_map entry) now rather
    # than letting a later gh/CodeSpace op fail on an unusable derived login.
    try:
        _reg_remote = ""
        _rr = subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if _rr.returncode == 0:
            _reg_remote = _rr.stdout.strip()
        if _reg_remote:
            from . import repos as _repos_acct

            _explicit = ""
            _entry_acct = _repos_acct.find_repo(project)
            if _entry_acct is not None:
                _explicit = _entry_acct.account
            _core_helper("_clarify_registration_account", _clarify_registration_account)(_reg_remote, project, _explicit, str(repo_dir))
    except Exception:
        pass

    # PR-workflow git hooks -- an ADOPT concern. Adopting a repo (which you own)
    # is the one flow permitted to mutate its git: clear a stale core.hooksPath
    # that would shadow the managed shims, then inject/refresh them into the
    # shared .git/hooks. Gated on PR mode so adopting a direct-push repo never
    # touches its hooks; inert at runtime unless AGENT_WORKTREES_HOOKS=1.
    try:
        cfg_for_hooks = cfg.load_config(config_path)
        if cfg_for_hooks.default_repo.pr.enabled:
            from . import hooks as _hooks

            cleared = _hooks.clear_stale_hooks_path(repo_dir)
            if cleared:
                output.changed(
                    f"Cleared stale core.hooksPath ('{cleared}') that shadowed "
                    "the managed .git/hooks shims"
                )
            installed_hooks = _hooks.install_hooks(repo_dir)
            if installed_hooks:
                output.ok(f"PR-workflow git hooks installed ({', '.join(installed_hooks)})")
    except Exception as e:
        output.warn(f"Could not install git hooks: {e}")

    # Azure DevOps PR provider needs the az 'azure-devops' extension; provision
    # it at adopt time so the first create-pr doesn't fail on a fresh machine.
    try:
        _core_helper("_ensure_ado_pr_cli", _ensure_ado_pr_cli)(cfg.load_config(config_path).default_repo.pr)
    except Exception:
        pass

    # Adoption owns repository-local machine wiring. Committed relative-path
    # directory marketplace sources resolve live against whatever checkout is
    # active, so no local source-override seeding is needed here
    # (#marketplace-override-retirement).

    # Refresh Windows Terminal profiles if installed via install.ps1
    if plat == "windows":
        _core_helper("_refresh_terminal_profiles", _refresh_terminal_profiles)()

    output.ok(f"Project '{project}' registered")
    print(f"  Config:  {config_path}")
    print(f"  Usage:   {project}")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    output.header("Uninstalling Agent Worktrees")

    # Remove only this payload's receipt-owned project binstub.
    project = cfg.project_name()
    try:
        for path in inst.remove_project_binstub(project):
            output.changed(f"Removed binstub: {path}")
    except inst.BinstubOwnershipError as exc:
        output.warn(str(exc))

    # Remove wrappers
    bd = inst.bin_dir()
    for name in ("launch-session.cmd", "launch-session.ps1", "launch-session.sh"):
        p = bd / name
        if p.exists():
            p.unlink()
    output.changed(f"Removed wrappers from {bd}")

    # Remove venv
    venv = inst.venv_dir()
    if venv.exists():
        shutil.rmtree(venv, ignore_errors=True)
        output.changed(f"Removed venv: {venv}")

    # Remove lib
    lib = inst.lib_dir()
    if lib.exists():
        shutil.rmtree(lib, ignore_errors=True)
        output.changed(f"Removed package: {lib}")

    if args.remove_config:
        base = inst.install_dir()
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)
            output.changed(f"Removed {base} (config + session metadata)")
    else:
        manifest = inst.install_dir() / "deploy-manifest.json"
        if manifest.exists():
            manifest.unlink()
        output.skipped("Config and session metadata preserved")
        print("    Use --remove-config to delete everything")

    output.ok("Uninstall complete")
    return 0


