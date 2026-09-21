"""Profiles / picker / terminal-fragment CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
from pathlib import Path

from . import output, validate as val
from . import config as cfg
from . import installer as inst
from .update_stage import discover_plugin_dir


def _core():
    from . import __main__ as core
    return core


def _core_helper(name: str, local):
    candidate = getattr(_core(), name, None)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def _exec_worktree_manager(*args, **kwargs): return _core()._exec_worktree_manager(*args, **kwargs)
def _json_error(*args, **kwargs): return _core()._json_error(*args, **kwargs)
def _json_output(*args, **kwargs): return _core()._json_output(*args, **kwargs)
def _usable_worktree_manager(*args, **kwargs): return _core()._usable_worktree_manager(*args, **kwargs)
def cmd_manager_install_trigger(*args, **kwargs): return _core().cmd_manager_install_trigger(*args, **kwargs)


def add_parsers(sub) -> None:
    p = sub.add_parser("profiles", help="Read or write this machine's terminal-profile selection (the Picker's Profiles column)")
    p.add_argument("profiles_action", choices=["get", "apply"], help="get: emit this host's selected launch targets; apply: persist a new selection (--set) and mirror it")
    p.add_argument("--set", default=None, help="With apply: a JSON array of {machine, env, kind} objects -- the new column for this host (the locked self·agent target is always included)")
    p.add_argument("--no-mirror", action="store_true", help="With apply: persist the selection but skip regenerating the terminal profiles")
    p.add_argument("--json", action="store_true", help="Emit a JSON result object")
    p = sub.add_parser("terminal-fragment", help="Preview the Windows Terminal fragment this machine's config would emit (no deploy)")
    p.add_argument("--machine", default=None, help="Machine key to preview as (default: this machine from config)")
    p.add_argument("--explain", action="store_true", help="Per-project decision trace instead of the raw fragment JSON")
    p.add_argument("--doctor", action="store_true", help="Read-only report of live Windows Terminal state drift (hidden/orphaned/duplicate profiles); no mutation")
    p.add_argument("--migrate-selections", action="store_true", help="Rewrite every local project's terminal_profiles selection from the legacy display_name vocabulary to the canonical machine key (full name); prints a summary and does not emit the fragment JSON")
    p = sub.add_parser("repair", help="Repair local integration in place -- regenerate Windows Terminal profiles (heal hidden + reclaim orphans) and redeploy project binstubs. Version-independent (unlike 'update').")
    p.add_argument("--terminal", action="store_true", help="Repair only Windows Terminal profiles (default: both terminal and binstubs)")
    p.add_argument("--binstubs", action="store_true", help="Repair only project binstubs (default: both terminal and binstubs)")
    p = sub.add_parser("picker", help="Inspect or hand off to the standalone Worktree Manager picker")
    p.add_argument("picker_action", choices=["status", "mock", "screenshot"], nargs="?", default="status", help="status (default) reports whether the standalone Worktree Manager owns the picker seam here; mock and screenshot hand off to that manager when it is installed, otherwise the install trigger is shown")
    p.add_argument("--json", action="store_true", help="Emit a JSON result")
    p.add_argument("--out", default=None, help="screenshot: write the capture to this file (default: stdout)")
    p.add_argument("--format", dest="picker_format", choices=["svg", "text", "ansi"], default="svg", help="screenshot format: svg (audit screenshot), text (plain character grid), ansi (colour-aware grid)")
    p.add_argument("--live", action="store_true", help="screenshot: render the multi-machine SSH source instead of the local-only source")
    p.add_argument("--pivot", dest="picker_pivot", default=None, help="screenshot: switch to this pivot (top tab) before capturing, e.g. 'CodeSpaces' (case-insensitive; unknown labels capture the default Worktrees tab)")
    p.add_argument("--wait", dest="picker_wait", type=float, default=0.0, help="screenshot: with --pivot, seconds to wait for a registered pivot's background list to finish loading so the capture shows real rows (default: 0 = no wait)")
    p.add_argument("--local", dest="picker_local", action="store_true", help="mock: force the local-only source (data_local) instead of the multi-machine SSH source -- for an isolated sandbox preview with no resolvable mesh repo/roster")
    p = sub.add_parser("validate", help="Validate core infrastructure files")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--files", nargs="*", default=None)
    p.add_argument("--worktree-path", default=None)
    p.add_argument("--default-branch", default="origin/master")

def _profiles_host() -> tuple[str, str]:
    """This machine's (display_name, env_label) in roster vocabulary."""
    from . import roster

    return roster.local_host()


def cmd_profiles(args: argparse.Namespace) -> int:
    """Read or write this machine's terminal-profile column for the repo.

    ``get`` emits this host's selected launch targets (its column of the host x
    target matrix) as JSON. ``apply --set <json>`` persists a new column into
    ``~/.<project>/config.yaml`` and, unless ``--no-mirror``, regenerates the
    terminal profiles to match. Both are SSH-able so the Picker can read/write
    a remote host's column over its SSH alias.
    """
    from . import profiles as profiles_mod

    action = getattr(args, "profiles_action", "get")
    as_json = getattr(args, "json", False)
    cfg_path = cfg.default_config_path()
    machine, env = _core_helper("_profiles_host", _profiles_host)()

    if action == "get":
        managed = profiles_mod.has_selection(cfg_path)
        if managed:
            sels = profiles_mod.normalize_selection(
                profiles_mod.load_selection(cfg_path), machine, env
            )
        else:
            # Unmanaged -> report the DEFAULT column (minimal per-agent + bare
            # cross-machine), computed from the roster candidates. The Picker
            # keys off ``managed`` (False) and renders the default itself, so
            # these targets are for human/JSON legibility.
            from . import roster

            candidates = [
                profiles_mod.TargetSel(m, e, kind)
                for (m, e) in roster.target_envs()
                for kind in ("agent", "shell")
            ]
            sels = profiles_mod.default_selection(candidates, machine, env)
        payload = {
            "machine": machine,
            "env": env,
            "managed": managed,
            "targets": [s.as_dict() for s in sels],
        }
        if as_json:
            _json_output(payload)
        else:
            state = "managed" if managed else "default (minimal + bare cross-machine)"
            print(f"Terminal profiles for {machine} {env} [{state}]:")
            for s in sels:
                lock = (
                    " (self, locked)"
                    if (s.machine == machine and s.env == env and s.kind == "agent")
                    else ""
                )
                print(f"  - {s.machine} {s.env} · {s.kind}{lock}")
        return 0

    # action == "apply"
    raw = getattr(args, "set", None)
    if raw is None:
        msg = "profiles apply requires --set '<json-array>'"
        if as_json:
            _json_error(msg)
        else:
            output.err(msg)
        return 2
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        msg = f"invalid --set JSON: {e}"
        if as_json:
            _json_error(msg)
        else:
            output.err(msg)
        return 2
    if not isinstance(parsed, list):
        msg = "--set must be a JSON array of {machine, env, kind} objects"
        if as_json:
            _json_error(msg)
        else:
            output.err(msg)
        return 2
    sels = [
        profiles_mod.TargetSel(
            str(o.get("machine", "")).strip(),
            str(o.get("env", "")).strip(),
            str(o.get("kind", "agent")).strip().lower(),
        )
        for o in parsed
        if isinstance(o, dict)
    ]
    written = profiles_mod.save_selection(cfg_path, sels, self_machine=machine, self_env=env)

    mirrored = False
    if not getattr(args, "no_mirror", False):
        mirrored = _core_helper("_mirror_terminal_profiles", _mirror_terminal_profiles)()

    payload = {
        "machine": machine,
        "env": env,
        "targets": [s.as_dict() for s in written],
        "mirrored": mirrored,
    }
    if as_json:
        _json_output(payload)
    else:
        output.ok(
            f"Saved {len(written)} terminal profile(s) for {machine} {env}"
            + (" · mirrored" if mirrored else "")
        )
    return 0


def _mirror_terminal_profiles() -> bool:
    """Regenerate the local terminal profiles from the saved selection.

    Mirroring is a Windows-only concern today (Windows Terminal fragment via
    the installer); on WSL/Linux hosts it is a no-op (Tabby/Linux mirroring is
    future work). Returns True only when a mirror actually ran **and succeeded**
    (the fragment was regenerated) -- so ``profiles apply`` / the Picker report
    ``mirrored`` honestly rather than masking a failed refresh (dotfiles#563).
    """
    if platform.system() != "Windows":
        return False
    try:
        return _core_helper("_refresh_terminal_profiles", _refresh_terminal_profiles)()
    except Exception:
        return False


def cmd_terminal_fragment(args: argparse.Namespace) -> int:
    """Preview the Windows Terminal fragment this machine's config would emit.

    Reads the same local sources the installer's ``Build-TerminalFragment``
    consults (``repos.yaml`` / ``projects.yaml`` / per-project ``machines.yaml``
    + ``config.yaml``) and prints the fragment **without deploying it**. Use it
    to see why a project does or does not get a Terminal profile.

    Output modes:
      * default   -- the fragment JSON exactly as it would be written.
      * --explain -- a per-project decision trace (managed/unmanaged, agent
                     exposure, and each emitted profile name/target).
    """
    from . import terminal_fragment as tf

    machine = getattr(args, "machine", None)
    if not machine:
        try:
            machine = cfg.load_config().machine
        except Exception:
            machine = None
    if not machine:
        output.err(
            "Could not resolve this machine's key. Run from a managed repo or "
            "pass --machine <key>."
        )
        return 1

    try:
        current = cfg.project_name()
    except Exception:
        current = None

    if getattr(args, "migrate_selections", False):
        changed = tf.migrate_local_selections(current_project=current)
        if changed:
            output.header("Migrating terminal_profiles selections to machine keys")
            for name in changed:
                output.ok(f"{name}: selection rewritten to canonical keys")
        else:
            output.ok("terminal_profiles selections already use machine keys")
        return 0

    if getattr(args, "doctor", False):
        return _core_helper("_terminal_fragment_doctor", _terminal_fragment_doctor)(machine, current)

    result = tf.preview_local(machine, current_project=current)

    if getattr(args, "explain", False):
        print(
            f"Terminal fragment preview for '{machine}' "
            f"({len(result.profiles)} profile(s) across "
            f"{len(result.plans)} project(s)):\n"
        )
        for plan in result.plans:
            state = (
                "unmanaged -> default column" if plan.unmanaged_default else "managed selection"
            )
            agent = "agent-exposed" if plan.agent_exposed else "no-agent"
            print(f"- {plan.display} [{plan.name}]  ({state}; {agent})")
            if not plan.profiles:
                print("    (no profiles emitted)")
            for p in plan.profiles:
                print(f"    - {p.name!r}  <{p.kind}>  {p.commandline}")
            print()
        return 0

    print(json.dumps(result.fragment(), indent=2))
    return 0


def _terminal_fragment_doctor(machine: str, current: str | None) -> int:
    """Read-only report of Windows Terminal state drift vs. the fragment.

    Surfaces the two failure modes the delta-based ``Sync-TerminalState`` could
    leave behind and never self-heal: fragment profiles WT is *hiding* (in the
    fragment + ``generatedProfiles`` but missing from ``settings.json``), and
    orphaned ``generatedProfiles`` cruft. Never mutates WT state -- the fix is
    applied by the installer's convergent sync on the next ``update``.
    """
    from . import terminal_fragment as tf

    diag = tf.diagnose_wt_state()
    if diag is None:
        output.warn("Windows Terminal state unavailable (non-Windows, or WT not installed).")
        return 0

    result = tf.preview_local(machine, current_project=current)
    frag_names = {p.guid.lower(): p.name for p in result.profiles}

    print(f"Windows Terminal state doctor for '{machine}':")
    print(f"  fragment profiles : {diag.fragment_count}")
    print(f"  settings profiles : {diag.settings_count}")
    print(f"  generatedProfiles : {diag.generated_count}")

    if diag.hidden:
        print(
            f"\n  HIDDEN -- in fragment + generatedProfiles but not in "
            f"settings.json ({len(diag.hidden)}):"
        )
        for g in diag.hidden:
            print(f"    - {frag_names.get(g, g)}  {g}")
        print(
            "    -> the next 'update' will prune these from generatedProfiles "
            "so WT re-discovers them."
        )
    if diag.orphans:
        print(
            f"\n  ORPHANS -- generatedProfiles entries in no fragment and not "
            f"materialized ({len(diag.orphans)}): accumulated cruft."
        )
        if diag.reclaimable_orphans:
            print(
                f"    - {len(diag.reclaimable_orphans)} reclaimable (ours) -> "
                f"the next 'update' prunes these automatically."
            )
        if diag.foreign_orphans:
            print(
                f"    - {len(diag.foreign_orphans)} kept (v4/v5 GUIDs -- "
                f"WT built-in / random profiles; never auto-pruned)."
            )
    if diag.duplicate_names:
        print(
            "\n  DUPLICATE profile names in settings.json "
            "(often a legacy stand-alone fragment colliding with the "
            "generated one):"
        )
        for name, count in diag.duplicate_names:
            print(f"    - {name!r} x{count}")

    if diag.healthy:
        print("\n  OK -- no hidden or duplicate profiles detected.")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# picker -- Textual picker status (no opt-out; see picker_tui module docstring)
# ═══════════════════════════════════════════════════════════════════════════


def cmd_picker(args: argparse.Namespace) -> int:
    """Inspect or hand off to the standalone Worktree Manager picker."""
    action = getattr(args, "picker_action", "status")
    as_json = getattr(args, "json", False)
    mgr = _usable_worktree_manager()

    if action == "mock":
        if not mgr:
            return cmd_manager_install_trigger(cfg.active_project())
        subcommand = ["picker", "mock"]
        project = cfg.active_project()
        if project:
            subcommand.append(project)
        if getattr(args, "picker_local", False):
            subcommand.append("--local")
        return _exec_worktree_manager(mgr, None, subcommand=subcommand)

    if action == "screenshot":
        if not mgr:
            return cmd_manager_install_trigger(cfg.active_project())
        subcommand = [
            "picker",
            "screenshot",
        ]
        project = cfg.active_project()
        if project:
            subcommand.append(project)
        subcommand += ["--format", getattr(args, "picker_format", "svg")]
        out = getattr(args, "out", None)
        if out:
            subcommand += ["--out", out]
        if getattr(args, "live", False):
            subcommand.append("--live")
        pivot = getattr(args, "picker_pivot", None)
        if pivot:
            subcommand += ["--pivot", pivot]
        wait_pivot = float(getattr(args, "picker_wait", 0.0) or 0.0)
        if wait_pivot > 0:
            subcommand += ["--wait", str(wait_pivot)]
        return _exec_worktree_manager(mgr, None, subcommand=subcommand)

    # status
    effective = mgr is not None
    if as_json:
        _json_output(
            {
                "effective": effective,
                "manager_available": effective,
                "bundled": False,
                "install_trigger": not effective,
            }
        )
    else:
        print(f"effective:    {str(effective).lower()}")
        print("owner:        Worktree Manager" if effective else "owner:        install trigger")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# validate
# ═══════════════════════════════════════════════════════════════════════════


def cmd_validate(args: argparse.Namespace) -> int:
    worktree_path = args.worktree_path or str(Path.cwd())
    files = args.files if args.files else None

    # Load config to get validate_paths for the repo
    validate_paths: list[str] | None = None
    try:
        config = cfg.load_config()
        repo = config.default_repo
        if repo.validate_paths:
            validate_paths = repo.validate_paths
    except Exception:
        pass  # Fall back to legacy paths

    failures = val.validate_files(
        worktree_path,
        files,
        default_branch=args.default_branch,
        dry_run=args.dry_run,
        validate_paths=validate_paths,
    )
    return 1 if failures else 0


# ═══════════════════════════════════════════════════════════════════════════
# Argument parser
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# install / uninstall / update / install-status
# ═══════════════════════════════════════════════════════════════════════════



def _resolve_terminal_install_script() -> Path | None:
    """Locate ``install.ps1`` for the Windows Terminal profile refresh.

    Resolution order (first existing wins):
      1. The deploy-manifest's ``plugin_source`` -- authoritative when set, but
         the marketplace-install flow leaves it empty (dotfiles#211), so it is
         only a hint, not a hard gate.
      2. The installed plugin dir (``~/.copilot/installed-plugins/...``) as
         discovered by :func:`update_stage.discover_plugin_dir` -- the robust fallback
         that does not depend on manifest correctness.
      3. The running module's own plugin root (``<plugin>/scripts/install.ps1``)
         -- covers direct/dev runs from a checkout.

    Returns the first candidate whose ``scripts/install.ps1`` exists, else None.
    """
    candidates: list[Path] = []

    # 1. deploy-manifest plugin_source (may be empty after a marketplace install)
    manifest_path = cfg.install_dir() / "deploy-manifest.json"
    if manifest_path.exists():
        try:
            m = json.loads(manifest_path.read_text())
            plugin_source = m.get("plugin_source")
            if plugin_source:
                candidates.append(Path(plugin_source) / "scripts" / "install.ps1")
        except Exception:
            pass

    # 2. installed plugin dir (marketplace or _direct) -- robust fallback
    try:
        plugin_dir, _layout = _core_helper("discover_plugin_dir", discover_plugin_dir)()
        if plugin_dir:
            candidates.append(plugin_dir / "scripts" / "install.ps1")
    except Exception:
        pass

    # 3. the running module's own scripts dir (src/agent_worktrees -> plugin root)
    try:
        candidates.append(Path(__file__).resolve().parents[2] / "scripts" / "install.ps1")
    except Exception:
        pass

    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue
    return None


def _refresh_terminal_profiles() -> bool:
    """Regenerate the Windows Terminal fragment from the saved selection.

    Delegates to the PowerShell installer's narrow ``refresh-profiles`` action
    (dotfiles#563), which regenerates **only** the WT fragment (Deploy-Shortcuts)
    for the active project. The old path shelled the whole ``update`` action
    (venv redeploy, pip install, binstub reconcile, psmux, instruction deploy --
    ~60s+ in practice) under a 30s subprocess timeout, so it was killed long
    before the fragment was rebuilt and the ``TimeoutExpired`` was swallowed into
    a warning. Returns ``True`` only when the refresh actually succeeded (exit
    code 0) so callers (the Picker Apply, ``profiles apply``) report mirror
    status honestly instead of a blanket ``mirrored: true``.

    The installer script is resolved via :func:`_resolve_terminal_install_script`
    so an empty deploy-manifest ``plugin_source`` (marketplace install) no longer
    silently no-ops the refresh; when it genuinely cannot be found we emit a
    warning rather than returning silently (dotfiles#211).
    """
    install_script = _core_helper(
        "_resolve_terminal_install_script", _resolve_terminal_install_script
    )()
    if install_script is None:
        output.warn(
            "Could not refresh Windows Terminal profiles: install.ps1 not found "
            "(checked deploy-manifest plugin_source and installed-plugin dir)"
        )
        return False

    cmd = ["pwsh", "-NoProfile", "-File", str(install_script), "refresh-profiles"]
    # Pass the active project explicitly so the installer regenerates the
    # fragment for the right context instead of relying on CWD/env inference in
    # the subprocess: the mirror often runs from a worktree dir whose basename
    # does not map to a project config.
    try:
        cmd += ["-ProjectName", cfg.project_name()]
    except Exception:
        pass

    try:
        # A fragment-only regen is fast; the generous timeout only guards a cold
        # PowerShell start + YAML parse and (unlike the old 30s cap on the full
        # ``update``) comfortably outlasts the work, so we never kill it
        # mid-write.
        #
        # Decode defensively: the installer's captured stdout/stderr is not
        # guaranteed UTF-8 (a redirected PowerShell pipe honors
        # ``[Console]::OutputEncoding``, which can be an OEM/ANSI codepage under
        # which glyphs like the box-drawing headers or a project/path name emit
        # non-UTF-8 bytes). With the default strict ``text=True`` a stray byte
        # (e.g. 0xfb) raised ``UnicodeDecodeError`` inside subprocess's reader
        # thread -- a noisy traceback even though the refresh itself succeeded.
        # ``errors="replace"`` keeps the capture robust regardless of the child's
        # console codepage.
        result = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except Exception:
        output.warn("Could not refresh Windows Terminal profiles")
        return False

    if result.returncode != 0:
        output.warn(
            f"Could not refresh Windows Terminal profiles (installer exited {result.returncode})"
        )
        return False
    output.ok("Windows Terminal profiles refreshed")
    return True


def cmd_repair(args: argparse.Namespace) -> int:
    """Repair this machine's agent-worktrees integration in place.

    Two independently-selectable targets (default: **both**):

    * ``--terminal`` -- regenerate the Windows Terminal fragment and reconcile
      live WT state: heal fragment profiles WT is hiding (in the fragment +
      ``generatedProfiles`` but missing from ``settings.json``) and reclaim our
      accumulated ``generatedProfiles`` orphans. Windows-only (a no-op else).
    * ``--binstubs`` -- redeploy every registered project's ``~/.local/bin``
      launcher (add/refresh missing or stale) and remove stale ones.

    Unlike ``update``, ``repair`` never touches the plugin/runtime version -- it
    only reconciles local *deployed state*, so it is the right tool when the
    Terminal dropdown or a binstub is wrong but the runtime is already current
    (``update`` would otherwise version-skip the installer). It is idempotent
    and safe to re-run.
    """
    want_terminal = getattr(args, "terminal", False)
    want_binstubs = getattr(args, "binstubs", False)
    if not want_terminal and not want_binstubs:
        want_terminal = want_binstubs = True  # neither flag -> repair both

    rc = 0

    if want_binstubs:
        output.header("Repairing project binstubs")
        try:
            inst.reconcile_binstubs()
        except Exception as e:
            output.err(f"Binstub repair failed: {e}")
            rc = 1

    if want_terminal:
        output.header("Repairing Windows Terminal profiles")
        if platform.system() != "Windows":
            output.skipped("Terminal profile repair is Windows-only -- skipped")
        else:
            from . import terminal_fragment as tf

            diag = tf.diagnose_wt_state()
            if diag is not None:
                if diag.hidden:
                    output.info(f"Will heal {len(diag.hidden)} hidden fragment profile(s)")
                if diag.reclaimable_orphans:
                    output.info(
                        f"Will reclaim {len(diag.reclaimable_orphans)} "
                        "orphaned generatedProfiles GUID(s)"
                    )
                for name, count in diag.duplicate_names:
                    output.warn(
                        f"Duplicate profile {name!r} x{count} in "
                        "settings.json -- a separate stand-alone fragment "
                        "shares the name; not auto-resolved here"
                    )
                if diag.healthy and not diag.reclaimable_orphans:
                    output.ok("Windows Terminal state already clean")
            if not _core_helper("_refresh_terminal_profiles", _refresh_terminal_profiles)():
                rc = 1
            elif diag is not None and (diag.hidden or diag.reclaimable_orphans):
                # The installer's Sync-TerminalState logs the concrete counts and
                # the WT-running caveat; surface the follow-through explicitly.
                output.info(
                    "If Windows Terminal was open, close it fully and "
                    "reopen for the healed profiles to appear."
                )

    return rc
