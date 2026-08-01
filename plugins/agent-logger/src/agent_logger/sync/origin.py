"""Session origin derivation + sidecar marking.

Each session records its **origin** -- the source *harness* repo it was worked
in (matched against the machine's configured harness repos) plus the machine, or
a **machine-only** marker when no harness resolves. The mark is a per-session-dir
sidecar (``origin.json``) that syncs with the session, so any downstream logger
daemon can route by origin without re-parsing ``workspace.yaml``.

This is the foundation of origin-routed filing (aperture-labs effort
``origin-routed-logging``; visions: agent-logger ``origin-routed-filing`` /
``derive-the-origin-never-guess`` and permanent-record ``origin-faithful
routing``). Deriving-not-guessing: the origin comes from the session's own
recorded ``workspace.yaml`` paths, or falls back to the machine default
explicitly.
"""

from __future__ import annotations

import json
from pathlib import Path

ORIGIN_SIDECAR = "origin.json"
SCHEMA_VERSION = 1

# workspace.yaml keys carrying a filesystem origin, in match precedence.
_ORIGIN_KEYS = ("git_root", "repository", "cwd")


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


def derive_origin(session_dir: Path, machine: str,
                  harness_repos: list[str]) -> dict:
    """Derive a session's origin as ``{machine, source_repo|None, basis}``.

    ``source_repo`` is the first configured harness repo whose name appears
    (case-insensitive substring) in the session's ``git_root`` / ``repository``
    / ``cwd`` (worktree-safe: a path like ``.../aperture-labs.worktrees/...``
    still resolves to ``aperture-labs``). When none matches -- no
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
                      denylist: list[str] | None = None) -> tuple[bool, dict]:
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
    """
    origin = derive_origin(session_dir, machine, effective)
    allow = {a.lower() for a in allowlist if a}
    deny = {d.lower() for d in (denylist or []) if d}
    src = origin["source_repo"]
    if src is not None:
        low = src.lower()
        if low in deny:
            return False, origin
        if allow:
            return (low in allow), origin
        return True, origin
    # No derived source_repo (machine-only).
    if _read_workspace_paths(session_dir):
        # A path that resolved to no recognized repo. With an allowlist this is a
        # strict exclude; in catch-all (no allowlist) mode it is not denied, so
        # it is kept unless fail_closed.
        if allow:
            return False, origin
        return (not fail_closed), origin
    return (not fail_closed), origin


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
