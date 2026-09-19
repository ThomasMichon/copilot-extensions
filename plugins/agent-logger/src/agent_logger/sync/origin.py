"""Session origin derivation + sidecar marking.

Each session records its **origin** -- the source *harness* repo it was worked
in (matched against the machine's configured harness repos) plus the machine, or
a **machine-only** marker when no harness resolves. The mark is a per-session-dir
sidecar (``origin.json``) that syncs with the session, so any downstream logger
daemon can route by origin without re-parsing ``workspace.yaml``.

This is the foundation of origin-routed filing (test-chamber effort
``origin-routed-logging``; visions: agent-logger ``origin-routed-filing`` /
``derive-the-origin-never-guess`` and permanent-record ``origin-faithful
routing``). Deriving-not-guessing: the origin comes from the session's own
recorded ``workspace.yaml`` paths, or falls back to the machine default
explicitly.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a hard dependency
    yaml = None  # type: ignore[assignment]

ORIGIN_SIDECAR = "origin.json"
SCHEMA_VERSION = 1

# workspace.yaml keys carrying a filesystem origin, in match precedence.
_ORIGIN_KEYS = ("git_root", "repository", "cwd")

# Repo-owned sync opt-in config: same relative shape as agent-index's
# `.copilot-extensions/<plugin>/config.yaml` activation convention.
_OPT_IN_CONFIG_RELATIVE = (".copilot-extensions", "agent-logger", "config.yaml")
_LEGACY_OPT_IN_CONFIG_RELATIVE = (".agent-logger", "config.yaml")
_OPT_IN_SUBPROCESS_TIMEOUT = 10
_MAX_OPT_IN_CONFIG_BYTES = 256 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _read_workspace_paths(session_dir: Path) -> list[tuple[str, str]]:
    """Return ``(basis, value)`` pairs from a session's ``workspace.yaml``.

    Only the origin-bearing keys (``git_root`` / ``repository`` / ``cwd``) are
    returned, in that precedence order. Missing file or a read error yields an
    empty list (the caller then falls back to the machine default).
    """
    ws = session_dir / "workspace.yaml"
    if not ws.is_file():
        return []
    found: dict[str, str] = {}
    try:
        with open(ws, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                for key in _ORIGIN_KEYS:
                    prefix = f"{key}:"
                    if line.startswith(prefix):
                        val = line[len(prefix):].strip()
                        if val and key not in found:
                            found[key] = val
    except OSError:
        return []
    return [(k, found[k]) for k in _ORIGIN_KEYS if k in found]


def _origin_path_for(session_dir: Path, effective: list[str]) -> Path | None:
    """Return the on-disk repo path a session's origin matched, if any.

    Mirrors :func:`derive_origin`'s matching but returns the actual recorded
    path instead of a repo name, for filesystem-backed follow-up checks (the
    repo-owned sync opt-in gate below). Only ``git_root``/``cwd`` carry a
    real filesystem path here; ``repository`` (a URL/slug in some workspaces)
    never resolves to a path. The path is never persisted in the origin
    sidecar -- it stays local to this process.
    """
    for basis, value in _read_workspace_paths(session_dir):
        low = value.lower()
        for repo in effective:
            if repo and repo.lower() in low:
                if basis not in ("git_root", "cwd"):
                    return None
                candidate = Path(value)
                return candidate if candidate.is_dir() else None
    return None


def _is_link_or_reparse(info: os.stat_result) -> bool:
    return bool(
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _safe_repo_file(repo_path: Path, relative: tuple[str, ...]) -> Path | None:
    """Resolve ``repo_path/relative`` only if every path component -- each
    intermediate directory and the final file -- is an ordinary entry (no
    symlink/reparse point), so a committed opt-in config can never point
    outside the checkout it's declared in. Mirrors agent-index's
    ``_safe_file`` containment check.
    """
    try:
        root = repo_path.resolve(strict=True)
    except OSError:
        return None
    current = root
    for part in relative:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            return None
        if _is_link_or_reparse(info):
            return None
        is_last = part == relative[-1]
        if not is_last and not stat.S_ISDIR(info.st_mode):
            return None
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    if not resolved.is_file() or resolved.stat().st_size > _MAX_OPT_IN_CONFIG_BYTES:
        return None
    return resolved


def _load_opt_in_config(repo_path: Path, relative: tuple[str, ...]) -> dict[str, Any] | None:
    if yaml is None:  # pragma: no cover - hard dependency
        return None
    path = _safe_repo_file(repo_path, relative)
    if path is None:
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _declared_opt_in(repo_path: Path) -> bool | None:
    """Read this repo's own opt-in declaration, if it carries one.

    Returns ``True``/``False`` for an explicit ``sync: {opt_in: <bool>}`` in
    either the canonical or legacy config location (canonical wins when both
    are present), each loaded only when it is an ordinary, repo-contained
    file (see :func:`_safe_repo_file` -- a symlink/reparse point pointing
    outside the repo is never followed). Only an actual boolean activates or
    deactivates; any other scalar (a string like ``"false"``, a number,
    null, ...) is treated the same as the key being absent -- "no opinion",
    letting the caller fall through (e.g. to a bound knowledge repo) rather
    than silently truthy-cast an invalid declaration into an accidental
    opt-in.
    """
    for relative in (_OPT_IN_CONFIG_RELATIVE, _LEGACY_OPT_IN_CONFIG_RELATIVE):
        data = _load_opt_in_config(repo_path, relative)
        if data is None:
            continue
        sync_block = data.get("sync")
        if not isinstance(sync_block, dict) or "opt_in" not in sync_block:
            continue
        value = sync_block["opt_in"]
        if isinstance(value, bool):
            return value
    return None


@functools.lru_cache(maxsize=256)
def _bound_knowledge_repo_cached(repo_path_str: str) -> str | None:
    """Cached body of :func:`_bound_knowledge_repo`, keyed by the resolved
    repo path string. One ``agent-worktrees`` process launch per distinct
    repo per interpreter lifetime, instead of one per classified session --
    a sync/compaction pass over a large session store no longer pays a
    process-startup cost per session for the same handful of repos."""
    repo_path = Path(repo_path_str)
    command = shutil.which("agent-worktrees")
    if not command:
        return None
    try:
        result = subprocess.run(
            [command, "state-root", "--json"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=_OPT_IN_SUBPROCESS_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("source") != "knowledge_repo"
        or not payload.get("bound")
        or not isinstance(payload.get("state_root"), str)
        or not payload["state_root"].strip()
    ):
        return None
    try:
        state_root = Path(payload["state_root"]).expanduser().resolve(strict=True)
    except OSError:
        return None
    if not state_root.is_dir() or state_root == repo_path:
        return None
    return str(state_root)


def _bound_knowledge_repo(repo_path: Path) -> Path | None:
    """Best-effort resolve ``repo_path``'s bound knowledge repo via
    ``agent-worktrees state-root``. Returns ``None`` on any ambiguity or
    failure -- this is a fail-closed forwarding hop, never a hard dependency.
    Cached per resolved repo path (see :func:`_bound_knowledge_repo_cached`).
    """
    try:
        resolved = repo_path.resolve(strict=True)
    except OSError:
        return None
    cached = _bound_knowledge_repo_cached(str(resolved))
    return Path(cached) if cached is not None else None


def resolve_repo_opt_in(repo_path: Path) -> bool:
    """Return whether ``repo_path`` durably opts itself into session-sync.

    Mirrors ``agent-index``'s repo-owned activation-gate convention: a repo
    (or, when it declares none and requires external state, its bound
    knowledge repo) commits ``.copilot-extensions/agent-logger/config.yaml``
    (legacy: ``.agent-logger/config.yaml``) with a top-level ``sync: {opt_in:
    true}``. Neither an *unopinionated* config (present but silent on
    ``opt_in``) nor an unresolvable/absent one activates sync -- this gate
    fails closed, so being ``enabledPlugins``-enabled never implies syncing.    """
    declared = _declared_opt_in(repo_path)
    if declared is not None:
        return declared
    knowledge_repo = _bound_knowledge_repo(repo_path)
    if knowledge_repo is None:
        return False
    return bool(_declared_opt_in(knowledge_repo))


def derive_origin(session_dir: Path, machine: str,
                  harness_repos: list[str]) -> dict:
    """Derive a session's origin as ``{machine, source_repo|None, basis}``.

    ``source_repo`` is the first configured harness repo whose name appears
    (case-insensitive substring) in the session's ``git_root`` / ``repository``
    / ``cwd`` (worktree-safe: a path like ``.../test-chamber.worktrees/...``
    still resolves to ``test-chamber``). When none matches -- no
    ``workspace.yaml``, no path, or an unrecognized (non-harness) repo -- the
    origin falls back to machine-only (``source_repo=None``,
    ``basis='machine-default'``). It never guesses which machine ran the sync.
    """
    for basis, value in _read_workspace_paths(session_dir):
        low = value.lower()
        for repo in harness_repos:
            if repo and repo.lower() in low:
                return {
                    "schema_version": SCHEMA_VERSION,
                    "machine": machine,
                    "source_repo": repo,
                    "basis": basis,
                }
    return {
        "schema_version": SCHEMA_VERSION,
        "machine": machine,
        "source_repo": None,
        "basis": "machine-default",
    }


def _payload(origin: dict) -> str:
    return json.dumps(origin, indent=2, sort_keys=True) + "\n"


def effective_harness(allowlist: list[str], harness_repos: list[str],
                      denylist: list[str] | None = None) -> list[str]:
    """Union of the sync allowlist, denylist, and the machine's harness repos --
    allowlist first (so an allowlisted repo wins naming precedence). This is the
    set an origin is derived against for both marking and the sync decision, so
    a session that syncs (or is explicitly denied) is always derivable to a
    recognized repo. A denied repo MUST be in this set for the denylist to
    match, so the denylist is folded in here."""
    out: dict[str, str] = {}
    for repo in [*allowlist, *(denylist or []), *harness_repos]:
        if repo and repo.lower() not in out:
            out[repo.lower()] = repo
    return list(out.values())


def classify_for_sync(session_dir: Path, machine: str, allowlist: list[str],
                      effective: list[str], *,
                      fail_closed: bool = False,
                      denylist: list[str] | None = None,
                      require_repo_opt_in: bool = False,
                      opt_in_resolver=resolve_repo_opt_in) -> tuple[bool, dict]:
    """Origin-based per-repo sync decision. Returns ``(include, origin)``.

    Precedence:

    1. **Denylist wins.** A session whose derived ``source_repo`` is in
       ``denylist`` is always **excluded** -- the complement primitive that lets
       a target be "everything *except* these repos".
    2. **Allowlist gates when present.** With a non-empty ``allowlist``, a
       classified session **syncs iff** its ``source_repo`` is in it (exactly the
       prior behavior). A path resolving to a non-allowlisted repo is excluded; a
       session with no resolvable path follows ``fail_closed``.
    3. **Catch-all when no allowlist.** With an **empty** ``allowlist`` (denylist
       mode), every session that is not denied is **included** -- a classified
       non-denied repo, and (fail-open) an unrecognized/metadata-less session --
       unless ``fail_closed`` drops the truly unclassifiable ones. This is the
       "everything else" sink.
    4. **Repo opt-in gate (opt-in feature, off by default).** When
       ``require_repo_opt_in`` is set, a session that would otherwise sync per
       1-3 additionally requires the matched repo (or its bound knowledge
       repo) to durably declare ``sync: {opt_in: true}`` -- see
       :func:`resolve_repo_opt_in`. A session with no on-disk repo path to
       check (deleted checkout, machine-only, or a ``repository``-only
       basis) is excluded once this gate is enabled, since there is nothing
       to consult.
    """
    origin = derive_origin(session_dir, machine, effective)
    src = origin["source_repo"]
    include = classify_source_repo(
        src,
        has_recorded_paths=bool(_read_workspace_paths(session_dir)),
        allowlist=allowlist,
        denylist=denylist,
        fail_closed=fail_closed,
    )
    if include and require_repo_opt_in:
        repo_path = _origin_path_for(session_dir, effective)
        include = repo_path is not None and opt_in_resolver(repo_path)
    return include, origin


def classify_source_repo(
    source_repo: str | None,
    *,
    has_recorded_paths: bool,
    allowlist: list[str],
    denylist: list[str] | None = None,
    fail_closed: bool = False,
) -> bool:
    """Apply the canonical exact repo policy to a pre-classified source."""
    allow = {a.lower() for a in allowlist if a}
    deny = {d.lower() for d in (denylist or []) if d}
    src = source_repo
    if src is not None:
        low = src.lower()
        if low in deny:
            return False
        if allow:
            return low in allow
        return True
    # No derived source_repo (machine-only).
    if has_recorded_paths:
        # A path that resolved to no recognized repo. With an allowlist this is a
        # strict exclude; in catch-all (no allowlist) mode it is not denied, so
        # it is kept unless fail_closed.
        if allow:
            return False
        return not fail_closed
    return not fail_closed


def read_origin_sidecar(session_dir: Path) -> dict | None:
    """Read a session's ``origin.json`` sidecar; return its dict or ``None``.

    ``None`` means no resolvable *recorded* origin -- either the sidecar is
    absent, unreadable, or malformed. A present sidecar whose ``source_repo`` is
    ``null`` is a valid *machine-only* origin (derived, no harness matched) and
    is returned as-is; the caller applies the machine-default fallback for it
    (``derive-the-origin-never-guess``). Downstream logger daemons read this to
    route a session without re-parsing ``workspace.yaml``.
    """
    path = session_dir / ORIGIN_SIDECAR
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_origin_sidecar(session_dir: Path, origin: dict) -> bool:
    """Write ``origin.json`` into ``session_dir``. Idempotent: returns ``False``
    when the file already holds the identical payload, ``True`` when written."""
    path = session_dir / ORIGIN_SIDECAR
    payload = _payload(origin)
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == payload:
                return False
        except OSError:
            pass
    try:
        path.write_text(payload, encoding="utf-8")
    except OSError:
        return False
    return True


def mark_all(source: Path, machine: str, harness_repos: list[str],
             *, dry_run: bool = False) -> dict:
    """Ensure every local session-state dir carries an origin sidecar.

    Returns ``{total, marked, by_repo}``. Marks **all** local sessions (not just
    the synced/allowlisted ones), so the origin is available locally to
    distinguish personal vs work regardless of what syncs. ``dry_run`` derives
    and counts without writing.
    """
    ss = source / "session-state"
    summary: dict = {"total": 0, "marked": 0, "by_repo": {}}
    if not ss.is_dir():
        return summary
    for entry in sorted(ss.iterdir()):
        if not entry.is_dir():
            continue
        summary["total"] += 1
        origin = derive_origin(entry, machine, harness_repos)
        key = origin["source_repo"] or "(machine-only)"
        summary["by_repo"][key] = summary["by_repo"].get(key, 0) + 1
        if not dry_run and write_origin_sidecar(entry, origin):
            summary["marked"] += 1
    return summary


def backfill_corpus(corpus_root: Path, harness_repos: list[str],
                    *, dry_run: bool = False) -> dict:
    """Backfill origin sidecars across a **multi-machine** synced corpus.

    The corpus layout is ``<corpus_root>/<machine>/session-state/<sid>/`` (the
    NAS/fleet-hub shape, distinct from :func:`mark_all`'s single-machine
    ``<source>/session-state/`` local shape). Each session's ``machine`` is its
    machine-directory name; every session is derived against the same
    *harness_repos* (a union of the fleet's harness repos works because
    :func:`derive_origin` matches by the session's own recorded path). Existing
    correct sidecars are left untouched (idempotent), so this is safe to re-run.

    This is the Phase-4 backfill of sessions that predate origin marking (e.g. a
    machine still on the legacy syncer, whose sessions reached the corpus with
    no ``origin.json``), so historical sessions become routable/filterable too.

    Returns ``{total, marked, by_machine: {machine: mark_all-summary}}``.
    """
    summary: dict = {"total": 0, "marked": 0, "by_machine": {}}
    if not corpus_root.is_dir():
        return summary
    for machine_dir in sorted(corpus_root.iterdir()):
        if not machine_dir.is_dir() or not (machine_dir / "session-state").is_dir():
            continue
        machine = machine_dir.name
        per = mark_all(machine_dir, machine, harness_repos, dry_run=dry_run)
        summary["by_machine"][machine] = per
        summary["total"] += per["total"]
        summary["marked"] += per["marked"]
    return summary
