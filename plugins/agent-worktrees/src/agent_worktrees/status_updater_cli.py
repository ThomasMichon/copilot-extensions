"""Per-session status updater surface extracted from ``__main__``."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import config as cfg
from . import sessions
from . import installer as inst


def _core():
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = vars(_core()).get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "status-updater",
        help="Background loop: keep a session's @aw_ctx/@aw_seg status vars "
        "fresh (no per-render binstub spawns)",
    )
    p.add_argument("--session", required=True, help="Mux session name to update (e.g. wt-<id>)")
    p.add_argument(
        "--mux",
        default=None,
        choices=["psmux", "tmux"],
        help="Multiplexer binary (default: auto-detect)",
    )
    p.add_argument(
        "--path", default=None, help="Worktree path to classify (default: current directory)"
    )
    p.add_argument(
        "--interval", type=int, default=15, help="Disposition refresh cadence in seconds (min 2)"
    )


def _git_toplevel(path: Path | None):
    return _core()._git_toplevel(path)


def _reverse_lookup_project(anchor: Path):
    return _core()._reverse_lookup_project(anchor)


def _status_monitor_enabled() -> bool:
    from . import status_monitor_runtime

    return _core_helper("_status_monitor_enabled", status_monitor_runtime._status_monitor_enabled)()


def _register_session_for_monitor(sess: str, path: str | None) -> bool:
    from . import status_monitor_runtime

    return _core_helper("_register_session_for_monitor", status_monitor_runtime._register_session_for_monitor)(
        sess, path
    )


def _ensure_status_monitor() -> bool:
    from . import status_monitor_runtime

    return _core_helper("_ensure_status_monitor", status_monitor_runtime._ensure_status_monitor)()


def _render_status_context(path: str | None = None, plain: bool = False) -> str:
    from . import status_bar_cli

    return _core_helper("_render_status_context", status_bar_cli._render_status_context)(path, plain=plain)


def _render_status_segment(
    path: str | None = None,
    fetch: bool = False,
    plain: bool = False,
    no_title: bool = False,
    persist_title: bool = False,
) -> str:
    from . import status_bar_cli

    return _core_helper("_render_status_segment", status_bar_cli._render_status_segment)(
        path,
        fetch=fetch,
        plain=plain,
        no_title=no_title,
        persist_title=persist_title,
    )


def _activate_project_for_path(path: str | None, *, force: bool = False) -> None:
    """Resolve + thread the active project in-process from a worktree path.

    ``status-updater`` is a ``_NO_PROJECT_COMMANDS`` entry, so ``main()``
    deliberately skips CWD-based project resolution for it -- but the updater
    *does* know its target worktree via ``--path``.  Without an active project,
    ``cfg.tracking_dir()`` raises inside ``_find_record_for_path`` (which
    swallows it and returns ``None``), so every status-bar field that comes
    only from the tracking record -- the ``repo:id4`` identity locus and the
    session title -- silently disappears from the bar.

    Resolve the project git-like from the path's anchor (the same reverse
    lookup ``main()`` uses for CWD) and set it in process, so the status
    renderers can find the worktree's record.  A no-op when a project is
    already active or the path is not inside an adopted repo -- unless ``force``
    is set, which is what the resident ``status-monitor`` uses to *switch* the
    active project per session as it sweeps across worktrees in different repos.
    """
    if not force and cfg.active_project():
        return
    name = None
    try:
        anchor = _git_toplevel(Path(path) if path else Path.cwd())
        if anchor is not None:
            name = _reverse_lookup_project(anchor)
    except Exception:
        name = None
    if name:
        cfg.set_active_project(name)
    elif force:
        # Under ``force`` (the resident monitor sweeping across repos), never
        # leave a PRIOR session's project active: clear it so an unresolved path
        # can't render with the previous session's identity/title.
        cfg.set_active_project(None)


def _resolve_mux_worktree_id(candidate: str | None) -> str | None:
    """Map a worktree id recovered from a mux session back to the tracked id.

    ``mux_session_name`` maps ``.`` -> ``_``, so an id derived from a live
    session name can be the sanitized form -- which matches no ``<id>.yaml`` and
    makes :func:`_activate_project_for_worktree_id` fail closed, silently
    dropping the sessionStart binding for a dotted worktree id.

    Compare *session names* across the bounded adopted-project registry, which
    matches the exact and sanitized spellings alike (``mux_session_name`` is
    idempotent). Ambiguity fails closed, as in the sibling resolver.
    """
    if not candidate:
        return None
    target = sessions.mux_session_name(candidate)
    try:
        projects = (inst.read_projects_registry().get("projects") or {}).keys()
    except Exception:
        return None
    matches: set[str] = set()
    for project in projects:
        try:
            entries = (cfg.project_dir(str(project)) / "worktrees").glob("*.yaml")
            for entry in entries:
                if sessions.mux_session_name(entry.stem) == target:
                    matches.add(entry.stem)
        except Exception:
            continue
    return next(iter(matches)) if len(matches) == 1 else None


def _activate_project_for_worktree_id(worktree_id: str | None) -> str | None:
    """Activate the unique adopted project that owns ``worktree_id``.

    A sessionStart hook can recover a ``wt-<id>`` mux session from process
    ancestry even when its payload cwd is HOME. Project state is partitioned, so
    resolve that globally unique worktree id across the bounded adopted-project
    registry before writing the session binding. Ambiguity fails closed.
    """
    override = _core_helper("_activate_project_for_worktree_id", _activate_project_for_worktree_id)
    if override is not _activate_project_for_worktree_id:
        return override(worktree_id)
    if not worktree_id:
        return None
    try:
        projects = (inst.read_projects_registry().get("projects") or {}).keys()
    except Exception:
        return None
    matches = [
        str(project)
        for project in projects
        if (cfg.project_dir(str(project)) / "worktrees" / f"{worktree_id}.yaml").is_file()
    ]
    if len(matches) != 1:
        return None
    cfg.set_active_project(matches[0])
    return matches[0]


def _slot_superseded(active: str, mine: str, versions_root: str) -> bool:
    """Pure comparison: is ``mine`` a *different* runtime slot than the active
    one, with both living under the ``versions/`` slot root?

    Conservative by design -- returns False unless BOTH paths resolve *under*
    ``versions_root`` (so a dev/source or system-python interpreter, which has no
    version slot to compare, is never judged superseded). Separator- and
    case-normalized (``normpath``/``normcase``) so it is correct on Windows
    (``\\``, case-insensitive) and POSIX alike. Factored out (plain strings, no
    filesystem) so the decision is unit-testable without symlinked version trees.
    """

    def _norm(p: str) -> str:
        return os.path.normcase(os.path.normpath(p))

    active, mine, versions_root = _norm(active), _norm(mine), _norm(versions_root)

    def _under(p: str) -> bool:
        return p == versions_root or p.startswith(versions_root + os.sep)

    if not (_under(active) and _under(mine)):
        return False
    return mine != active


def _runtime_superseded(*, prefix: str | None = None, install_root: Path | None = None) -> bool:
    """True when a newer agent-worktrees runtime has superseded the one running
    this ``status-updater`` -- i.e. the active ``versions/<current-version>``
    slot (published by the marker) no longer resolves to this process's
    ``sys.prefix`` (both under ``versions/``).

    A version update publishes a fresh ``versions/<v>`` slot via the
    ``current-version`` marker (junction-free; #1106) but cannot reap an
    already-running updater: its mux session is still live (so ``_has_session``
    keeps it serving) and a new-version updater only spawns on the next
    attach/join. Left alone, one updater per (session x version) piles up across
    every deploy (dotfiles #911 -- observed dev315..dev392 all still running for
    days). This self-check lets a superseded updater retire on its next tick; the
    next attach spawns a current-version one. Degrade-safe: any resolution error,
    a missing marker, or a non-slot interpreter keeps it serving.
    """
    override = _core_helper("_runtime_superseded", _runtime_superseded)
    if override is not _runtime_superseded:
        return override(prefix=prefix, install_root=install_root)
    try:
        root = install_root if install_root is not None else cfg.install_dir()
        versions_root = os.path.realpath(os.path.join(str(root), "versions"))
        try:
            ver = (Path(str(root)) / "current-version").read_text("utf-8").strip()
        except OSError:
            ver = ""
        if not ver:
            return False  # no marker -> cannot determine; keep serving
        active = os.path.realpath(os.path.join(str(root), "versions", ver))
        mine = os.path.realpath(prefix if prefix is not None else sys.prefix)
    except Exception:
        return False
    return _slot_superseded(active, mine, versions_root)


_BACKGROUND_AUTH_ENV_KEYS = {
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "AGENT_WORKTREES_AHP_AUTH_TOKEN",
}


def _background_environment() -> dict[str, str]:
    """Environment for resident helpers, excluding session credentials."""
    return {
        key: value for key, value in os.environ.items()
        if key.upper() not in _BACKGROUND_AUTH_ENV_KEYS
    }


def _spawn_status_updater(worktree_id: str, path: str | None) -> bool:
    """Detached-spawn the status-bar updater for ``wt-<worktree_id>``.

    The updater is the background loop that keeps a muxed session's status bar
    fresh (identity in ``@aw_ctx`` once, git disposition in ``@aw_seg`` each
    tick).  Historically it was spawned *only* by the launcher at psmux
    create/join (``Start-StatusUpdater`` in launch-session.ps1).  That single
    seam left long-lived attached sessions with no way to re-seed their updater
    after it retired -- e.g. a version deploy makes every running updater
    ``_runtime_superseded``-retire (dotfiles #911), and an attached session is
    never re-run through the launcher, so its bar goes dark until the next
    manual attach.  Reseeding from the ``sessionStart`` hook (``cmd_register_
    session``) closes that gap: every new Copilot session re-asserts its own
    updater, cross-platform (one Python seam behind both the ps1 and bash
    hooks) and independent of psmux attach/join (dotfiles #915).

    Idempotent by construction: the updater's ``@aw_updater`` token guard elects
    a single live instance, so a duplicate spawned here retires on its next
    tick.  Best-effort and cheap: it no-ops (returns ``False``) unless a mux is
    present *and* a live ``wt-<id>`` session exists (so a bare / non-mux session
    never spawns a pointless loop), and never raises into the hook path.
    """
    import shutil
    import subprocess

    if not worktree_id:
        return False
    sess = sessions.mux_session_name(worktree_id)
    try:
        mux = "psmux" if shutil.which("psmux") else ("tmux" if shutil.which("tmux") else None)
        if not mux:
            return False
        mux_bin = shutil.which(mux) or mux
        # Only seed when this session is actually under mux -- a bare/non-mux
        # Copilot has no status bar to feed, and spawning a loop that would
        # immediately see "gone" is wasteful.
        r = subprocess.run(
            [mux_bin, "has-session", "-t", sess],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            return False

        argv = [
            sys.executable,
            "-m",
            "agent_worktrees",
            "status-updater",
            "--session",
            sess,
            "--mux",
            mux,
        ]
        if path:
            argv += ["--path", path]

        kwargs: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            # Never inherit the spawner's cwd.  Both spawn seams -- the launcher
            # and the sessionStart reseed hook -- run FROM the plugin payload
            # dir, and a detached child that keeps that dir as its cwd holds an
            # open directory handle that blocks ``copilot plugin update`` from
            # replacing the payload on Windows (``os error 32``: the file/dir is
            # in use).  The updater locates its worktree via ``--path``, so its
            # cwd is irrelevant to its work -- root it at HOME.
            "cwd": os.path.expanduser("~"),
            "env": _background_environment(),
        }
        kwargs.update(_core().windowless_daemon_kwargs(breakaway=True))

        subprocess.Popen(argv, **kwargs)  # detached: fixed, trusted argv
        return True
    except Exception:
        return False


def cmd_status_updater(args: argparse.Namespace) -> int:
    """Keep a session's status-bar vars fresh without per-render spawns.

    The status bar references precomputed user options -- ``#{@aw_ctx}``
    (identity, static) and ``#{@aw_seg}`` (git disposition, dynamic) --
    instead of polling ``#(agent-worktrees ...)``.  psmux runs ``#()`` jobs
    synchronously in the paint path (no tmux-style caching), so a
    600 ms-class binstub spawn per repaint under Copilot's high-framerate
    TUI made muxed sessions unusable.  This long-lived loop moves that cost
    off the paint path: it renders **in-process** (paying Python import once,
    never re-spawning the binstub) and only ever shells out to the cheap,
    native ``set-option`` / ``has-session`` mux verbs.

    Identity is rendered once into ``@aw_ctx``; disposition is refreshed into
    ``@aw_seg`` every ``--interval`` seconds until the session ends.  Launched
    detached by the session launcher; safe to (re)spawn on every attach/join --
    an ``@aw_updater`` token elects a single live updater per session and older
    ones retire on their next tick.
    """
    import shutil
    import subprocess
    import time

    sess = args.session
    if not sess:
        return 2

    mux = args.mux or ("psmux" if shutil.which("psmux") else "tmux")
    mux_bin = shutil.which(mux) or mux
    path = args.path or os.getcwd()
    interval = args.interval if args.interval and args.interval >= 2 else 15

    # Coalescing tier (work-coalescing-singleton): default-on (opt-out via
    # AGENT_WORKTREES_STATUS_MONITOR=0). One resident status-monitor serves EVERY
    # session instead of a loop per session. Register this session's path (the
    # monitor's refcount), ensure the monitor is up, and hand off. If registration
    # or the spawn fails, fall through to this per-session loop so the session is
    # never left without a status bar.
    if _status_monitor_enabled() and _register_session_for_monitor(sess, path):
        if _ensure_status_monitor():
            return 0

    # status-updater is a no-project command, so main() never resolved a
    # project for us -- but the status renderers need one to find the
    # worktree's tracking record (repo:id locus + session title).  Resolve it
    # git-like from --path before rendering anything.
    _core_helper("_activate_project_for_path", _activate_project_for_path)(path)

    def _mux(*a: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                [mux_bin, *a],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except Exception:
            return None

    def _session_state() -> str:
        """Tri-state liveness: ``alive`` | ``gone`` | ``unknown``.

        ``unknown`` distinguishes a *transient* mux failure (a timed-out or
        errored ``has-session`` -> ``_mux`` returned ``None``) from a definitive
        "session is gone" (the mux ran and reported non-zero).  The loop must
        NOT retire on a transient hiccup -- under a busy high-framerate TUI the
        mux can momentarily fail to answer within the timeout, and treating that
        as "gone" silently kills the bar for the rest of the session (dotfiles
        #915).  Only a definitive ``gone`` (or a long run of ``unknown``s) ends
        the loop.
        """
        r = _mux("has-session", "-t", sess)
        if r is None:
            return "unknown"
        return "alive" if r.returncode == 0 else "gone"

    # A newer runtime already active? Then this updater is a leftover from a
    # pre-update version whose session is still live -- retire immediately rather
    # than serve stale (dotfiles #911). The next attach spawns a current one.
    if _runtime_superseded():
        return 0

    def _set(opt: str, val: str) -> None:
        # Session-scoped (no -g): empirically isolated per session on psmux
        # 3.3.6 and tmux 3.4, so concurrent worktree sessions don't clobber
        # each other's bar.
        _mux("set-option", "-t", sess, opt, val)

    # Bail only on a *definitive* absence -- a transient mux hiccup at startup
    # must not abort the updater before it ever paints (dotfiles #915).
    if _session_state() == "gone":
        return 0

    # Debounce redundant spawns (dotfiles #911).  Both the launcher
    # (Start-StatusUpdater) and the sessionStart reseed hook (cmd_register_
    # session) (re)spawn an updater at session start, so without a guard every
    # session leaks a *pair* of updaters -- and a duplicate that pins the plugin
    # payload dir as its cwd blocks ``copilot plugin update`` on Windows.  If a
    # live updater already owns this session on the *current* runtime, this
    # spawn is redundant: retire now rather than run a second loop.  A superseded
    # owner (older version, mid-deploy) is deliberately NOT deferred to -- the
    # reseed must replace it, so the bar never goes dark after a deploy
    # (dotfiles #915); likewise an owner with no published prefix (a pre-upgrade
    # updater) is replaced rather than deferred to, so the transition can't wedge
    # a dark bar.
    from . import update_stage as _upd

    _owner = _mux("display-message", "-t", sess, "-p", "#{@aw_updater}")
    if _owner is not None and _owner.returncode == 0:
        _tok = (_owner.stdout or "").strip()
        if _tok.isdigit() and int(_tok) != os.getpid() and _upd._pid_alive(int(_tok)):
            _op = _mux("display-message", "-t", sess, "-p", "#{@aw_updater_prefix}")
            _owner_prefix = (
                (_op.stdout or "").strip() if (_op is not None and _op.returncode == 0) else ""
            )
            if _owner_prefix and not _runtime_superseded(prefix=_owner_prefix):
                return 0

    # Single-instance guard.  The launcher may (re)spawn an updater on every
    # attach/join, so each updater claims @aw_updater with its own token; a
    # newer updater overwrites it and the older one retires on its next tick.
    # Cheaper and more portable than pid-liveness checks, and it doubles as the
    # tmux/psmux equivalent of the old flock guard.
    token = str(os.getpid())

    def _owns() -> bool:
        r = _mux("display-message", "-t", sess, "-p", "#{@aw_updater}")
        if r is None or r.returncode != 0:
            return True  # can't read the token -> assume ownership, keep serving
        return r.stdout.strip() == token

    _set("@aw_updater", token)
    # Publish this updater's runtime slot (``sys.prefix``) alongside the pid
    # token so a later spawn can distinguish a live *current* owner (defer to it)
    # from a live but superseded one (replace it) -- see the debounce above.
    _set("@aw_updater_prefix", os.path.realpath(sys.prefix))

    # Identity (machine | env | repo:id4) is static for the session's life:
    # render once, push to @aw_ctx, never poll it again.
    try:
        _set("@aw_ctx", _render_status_context(path, plain=False))
    except Exception:
        pass

    # Disposition (DIRTY/FINAL/WIP/CONVO/...) changes as work happens: refresh
    # @aw_seg on the interval until the session ends, a newer updater takes
    # over, or a newer runtime supersedes this one (dotfiles #911).  The bar
    # itself does zero process work between updates -- the mux only re-runs the
    # strftime %H:%M clock.
    #
    # Transient mux failures (``_session_state() == "unknown"``) are tolerated:
    # they do NOT retire the updater, they only skip that tick's liveness proof.
    # The loop exits only on a definitive ``gone``, a lost ownership token, a
    # superseding runtime, or ``_MAX_TRANSIENT_STRIKES`` consecutive unknowns
    # (a genuinely wedged mux -- ~strikes*interval seconds of silence), so a
    # busy-TUI timeout can no longer silently kill the bar (dotfiles #915).
    _MAX_TRANSIENT_STRIKES = 20
    strikes = 0
    while True:
        state = _session_state()
        if state == "gone":
            break
        if state == "unknown":
            strikes += 1
            if strikes >= _MAX_TRANSIENT_STRIKES:
                break
            time.sleep(interval)
            continue
        strikes = 0  # a live answer resets the transient run
        if not _owns() or _runtime_superseded():
            break
        try:
            seg = _render_status_segment(
                path,
                fetch=False,
                plain=False,
                no_title=False,
                persist_title=True,
            )
        except Exception:
            seg = ""
        _set("@aw_seg", seg)
        time.sleep(interval)
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# status-monitor -- one resident, coalescing tracker for ALL sessions
# ═══════════════════════════════════════════════════════════════════════════
#
# The per-session ``status-updater`` pays one resident Python process *per live
# session*.  With many concurrent worktree sessions that is N interpreters, each
# polling its own bar.  ``status-monitor`` consolidates them into the
# work-coalescing-singleton service tier: a single resident process refreshes
# every ``wt-*`` session's bar in one coalesced sweep, lives while at least one
# managed session, Picker, or active list consumer needs it, and idle-exits when
# every root goes away.  It is
# **default-on (opt-out** via ``AGENT_WORKTREES_STATUS_MONITOR=0``) -- when
# disabled, each session runs its own per-session updater (a-la-carte: the inline
# path stays correct with no daemon).  Single-active on the host via a liveness
# lock; a superseded runtime self-retires (mirrors the updater's #911 behaviour).

_STATUS_MONITOR_ENV = "AGENT_WORKTREES_STATUS_MONITOR"
_STATUS_MONITOR_HANDOFF_CLAIM_STALE_SECONDS_ENV = (
    "AGENT_WORKTREES_STATUS_MONITOR_HANDOFF_CLAIM_STALE_SECONDS"
)
_STATUS_MONITOR_HANDOFF_CLAIM_STALE_SECONDS_DEFAULT = 180.0


def _status_monitor_enabled() -> bool:
    """Whether the resident coalescing monitor is active (default-ON / opt-out).

    Enabled unless ``AGENT_WORKTREES_STATUS_MONITOR`` is explicitly falsy
    (``0``/``false``/``no``/``off``), in which case each session runs its own
    per-session ``status-updater`` (the a-la-carte inline fallback). Mirrors the
    self-retire opt-out convention.
    """
    return os.environ.get(_STATUS_MONITOR_ENV, "").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
