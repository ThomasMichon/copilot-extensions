"""Remote git-ref mirror for :mod:`claim_history`'s local ownership ledger
(worktree-claims-transitive-finalization Phase 3b, 2026-10-03: "Remote-mirror
this history for converged repos ... the store already exists and is
trusted, no reason for PR ownership history to be local-only and evaporate
on cleanup").

:mod:`claim_history` is a single machine-local JSONL file
(``logs/claim-history.jsonl``) -- durable across a worktree's own cleanup,
but gone the moment the MACHINE itself is reimaged/retired, and invisible to
any other machine wanting to audit a PR's full ownership trail. This module
mirrors each ``pr``-kind resource's event history to its own append-only Git
ref on the same shared store repo :mod:`lease_store`/:mod:`lease_config`
already use for cross-machine lease coordination -- same store, same
account-scoped auth, same "no branches/tags, hidden ref namespace" posture,
new namespace (``refs/agent-worktrees/claim-history/v1``, see
:data:`DEFAULT_REF_PREFIX`) and a different (simpler) protocol: a plain
linear chain of empty-tree commits, one per recorded event, oldest at the
root -- no compare-and-swap *exclusivity* semantics are needed here (unlike
a lease, nothing is ever "currently held" by one ref state), only a durable,
append-only, race-safe way to grow the chain from multiple independent
writers.

**Deliberately a separate, explicit, opt-in sweep -- never synchronous with
a live claim mutation.** Every :mod:`claim_history` write happens inline
during an ordinary claim operation (``pr_ops.py``'s claim/release,
``finalize.py``'s bulk release, ...); adding a live network fetch+push to
each one would add real latency and a new failure surface to every such
operation for a durability concern that tolerates eventual (not immediate)
consistency. Instead, :func:`sync_pending` is wired into
``agent-worktrees gc --mirror-claim-history`` (opt-in, network +
force-push, exactly the posture Phase 5's ``--lease-gc`` already
established for this same store) -- call it whenever convenient; it is
idempotent and resumable (see below), never loses an event, and degrades to
a reported no-op (not an error) when no store is configured for this
project.

**Sync cursor.** :func:`sync_pending` tracks, per ``(kind, ref)`` resource,
how many of that resource's locally-recorded events have already been
pushed (``logs/claim-history-mirror-state.json``, by local event count --
monotonic since the local ledger is itself append-only). Each push is
confirmed (a successful ``git push``, or an observed-identical remote OID
after a benign concurrent-identical-push race) before the cursor advances
past it, and the cursor is persisted after each resource's own pushes
complete -- a later run always resumes exactly where a prior run (or a
mid-run crash) left off, never re-pushing an already-mirrored event and
never silently skipping one still pending.

**Reading.** :func:`fetch_remote_history` is a plain, read-only,
best-effort pull of one resource's mirrored chain -- independent of the
sync cursor above, safe to call any time (e.g. ``claims history <ref>
--remote``) to see what other machines have mirrored, whether or not THIS
machine has ever pushed to that same ref itself.
"""

from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from . import claim_history, handoff_trace
from . import config as cfg
from .lease_config import ConfigError, LeaseSettings, load_lease_settings
from .lease_protocol import ProtocolError, canonical_json, ref_for, resource

log = logging.getLogger("agent-worktrees")

#: Hidden ref namespace for the claim-history mirror -- a sibling of
#: ``lease_config.DEFAULT_REF_PREFIX`` on the same store repo, never a
#: branch/tag.
DEFAULT_REF_PREFIX = "refs/agent-worktrees/claim-history/v1"

_SENTINEL = "agent-worktrees-claim-history-envelope-v1"
_REQUIRED_KEYS = frozenset({"ts", "kind", "ref", "worktree_id", "machine", "event"})

_write_failures = 0
_read_failures = 0


#: Fields a mirrored event may legitimately omit -- dropped from the
#: serialized payload only when absent/empty, unlike the required fields
#: (which are kept verbatim even when empty: a legacy ``machine=""`` record
#: is a real, if degraded, value, not an absent one -- dropping it would
#: make the payload fail its own required-field check on the other end).
_OPTIONAL_KEYS = frozenset({"session_id", "note"})


def write_failure_count() -> int:
    """Count of :func:`sync_pending` push attempts that gave up after
    exhausting retries, for observability without breaking that function's
    "report, never raise" contract."""
    return _write_failures


def read_failure_count() -> int:
    """Count of :func:`fetch_remote_history` calls that failed outright
    (as opposed to merely finding nothing mirrored yet)."""
    return _read_failures


class ClaimHistoryMirrorError(RuntimeError):
    """A mirror push could not be completed (exhausted retries, or a
    non-CAS Git transport failure)."""


def _state_path() -> Path:
    return cfg.install_dir() / "logs" / "claim-history-mirror-state.json"


def _state_lock_path() -> Path:
    return _state_path().with_suffix(".lock")


def _load_state() -> dict[str, int]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, int) and value >= 0
    }


def _save_state(state: dict[str, int]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def mirror_settings(origin: str | None = None) -> LeaseSettings | None:
    """Resolve the shared store's settings for the claim-history namespace,
    or ``None`` when no store is configured for this project -- a no-op,
    never a raised error, since mirroring is opt-in."""
    try:
        return load_lease_settings(origin=origin, ref_prefix=DEFAULT_REF_PREFIX)
    except ConfigError:
        return None


def _serialize_entry(entry: dict) -> str:
    payload = {key: entry.get(key, "") for key in _REQUIRED_KEYS}
    for key in _OPTIONAL_KEYS:
        value = entry.get(key)
        if value not in (None, ""):
            payload[key] = value
    return f"{_SENTINEL}\n{canonical_json(payload)}"


def _parse_entry(message: str) -> dict:
    prefix = _SENTINEL + "\n"
    if not message.startswith(prefix) or message.count("\n") != 1:
        raise ProtocolError("claim-history commit message has an invalid envelope")
    body = message[len(prefix):]
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProtocolError("claim-history commit payload is not valid JSON") from exc
    if not isinstance(data, dict) or not _REQUIRED_KEYS.issubset(data):
        raise ProtocolError("claim-history commit payload is missing required fields")
    for key in _REQUIRED_KEYS:
        if not isinstance(data[key], str):
            raise ProtocolError(f"claim-history field {key!r} must be a string")
    for key in _OPTIONAL_KEYS:
        if key in data and not isinstance(data[key], str):
            raise ProtocolError(f"claim-history field {key!r} must be a string")
    if not _REQUIRED_KEYS.union(_OPTIONAL_KEYS).issuperset(data):
        raise ProtocolError("claim-history commit payload has unknown fields")
    return data


class ClaimHistoryMirror:
    """Git-ref append-only mirror operations for one configured store."""

    def __init__(
        self,
        settings: LeaseSettings,
        *,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.settings = settings
        self.retries = retries
        self._sleep = sleep
        self._jitter = jitter
        # Same cross-account auth-header injection lease_store.GitLeaseStore
        # uses for this same store repo -- see its own __init__ for why.
        self._auth_args: list[str] = []
        if settings.auth_remote and settings.auth_cwd:
            try:
                from . import git_ops
                self._auth_args = git_ops._auth_config_args(
                    settings.auth_remote, cwd=settings.auth_cwd
                )
            except Exception:
                self._auth_args = []

    def push(self, entry: dict) -> None:
        """Append one history ``entry`` to its resource's remote mirror
        ref. Idempotent under a benign race (a concurrent identical push
        from another machine/process observed on the remote is treated as
        success, never re-applied); a genuine conflict (the ref moved for
        a different reason) is retried, bounded, by re-parenting onto the
        new remote head. Raises :class:`ClaimHistoryMirrorError` only after
        exhausting retries -- callers (:func:`sync_pending`) treat that as
        "this one entry stays pending, try again next sweep," never fatal
        to the sweep as a whole.
        """
        global _write_failures
        item = resource(str(entry["kind"]), str(entry["ref"]))
        ref = ref_for(self.settings.ref_prefix, item)
        message = _serialize_entry(entry)
        attempt = 0
        while attempt <= self.retries:
            parent = self._remote_oid(ref)
            # Commit-tree and push must share the SAME ephemeral bare repo
            # (mirrors GitLeaseStore._transition's own single-repo-per-attempt
            # shape) -- the new commit is only reachable from that repo's own
            # object store, so pushing it from a freshly-deleted one would
            # always fail with "not a git repository" / an unknown object.
            with tempfile.TemporaryDirectory(prefix="agent-claim-history-write-") as temp:
                repo = Path(temp) / "repo.git"
                self._git(["init", "--bare", str(repo)])
                if parent:
                    # The parent commit object only exists in whichever
                    # ephemeral repo created it (long since deleted) -- fetch
                    # it into THIS repo first so commit-tree -p can resolve
                    # it (mirrors GitLeaseStore._transition's own parent-fetch
                    # step).
                    fetched = self._git(
                        [
                            f"--git-dir={repo}", "fetch", "--quiet", "--no-tags",
                            self.settings.origin, f"+{ref}:refs/agent-claim-history/parent",
                        ],
                        check=False,
                    )
                    if fetched.returncode != 0:
                        # Transient failure, or the ref moved since we read
                        # it above -- either way, retry with a freshly-read
                        # parent rather than committing on top of one we
                        # couldn't actually fetch.
                        attempt += 1
                        self._sleep(self._jitter(0.025, min(0.5, 0.05 * (2**attempt))))
                        continue
                    # Idempotency against a crash between a prior push and
                    # its own checkpoint save: if the tip we're about to
                    # parent onto already carries THIS exact event, a prior
                    # run already mirrored it -- treat as already-applied
                    # rather than appending a duplicate commit for the same
                    # event.
                    if self._commit_message(repo, parent) == message:
                        return
                tree = self._git(
                    [f"--git-dir={repo}", "mktree"], input_text="",
                ).stdout.strip()
                args = [f"--git-dir={repo}", "commit-tree", tree]
                if parent:
                    args += ["-p", parent]
                env = {
                    "GIT_AUTHOR_NAME": "agent-worktrees-claim-history",
                    "GIT_AUTHOR_EMAIL": "agent-worktrees-claim-history@localhost",
                    "GIT_COMMITTER_NAME": "agent-worktrees-claim-history",
                    "GIT_COMMITTER_EMAIL": "agent-worktrees-claim-history@localhost",
                }
                oid = self._git(
                    args, input_text=message + "\n", extra_env=env,
                ).stdout.strip()
                pushed = self._git(
                    [
                        f"--git-dir={repo}", "push", "--porcelain",
                        f"--force-with-lease={ref}:{parent or ''}",
                        self.settings.origin, f"{oid}:{ref}",
                    ],
                    check=False,
                )
            if pushed.returncode == 0:
                return
            remote_now = self._remote_oid(ref)
            if remote_now == oid:
                return
            if attempt >= self.retries:
                _write_failures += 1
                detail = (pushed.stderr or pushed.stdout).strip().splitlines()
                suffix = detail[-1] if detail else f"exit {pushed.returncode}"
                raise ClaimHistoryMirrorError(
                    f"giving up mirroring {entry.get('ref')!r} after "
                    f"{attempt + 1} attempts: {suffix}"
                )
            attempt += 1
            self._sleep(self._jitter(0.025, min(0.5, 0.05 * (2**attempt))))
        _write_failures += 1
        raise ClaimHistoryMirrorError(
            f"giving up mirroring {entry.get('ref')!r} after exhausting retries"
        )

    def fetch(self, kind: str, ref_value: str) -> list[dict]:
        """Return every mirrored event for ``(kind, ref_value)``, oldest
        first. Never raises -- an absent ref, an unreachable store, or a
        malformed commit along the way degrades to an empty or partial
        result (the entries that *did* parse cleanly), logged at debug.
        """
        item = resource(kind, ref_value)
        ref = ref_for(self.settings.ref_prefix, item)
        try:
            oid = self._remote_oid(ref)
            if oid is None:
                return []
            with tempfile.TemporaryDirectory(prefix="agent-claim-history-read-") as temp:
                repo = Path(temp) / "repo.git"
                self._git(["init", "--bare", str(repo)])
                self._git(
                    [
                        f"--git-dir={repo}", "fetch", "--quiet", "--no-tags",
                        self.settings.origin, f"+{ref}:refs/agent-claim-history/read",
                    ]
                )
                empty_tree = self._git(
                    [f"--git-dir={repo}", "hash-object", "-t", "tree", "--stdin"],
                    input_text="",
                ).stdout.strip()
                entries: list[dict] = []
                current = oid
                while current:
                    raw = self._git(
                        [f"--git-dir={repo}", "cat-file", "commit", current]
                    ).stdout
                    marker = "\n\n"
                    if marker not in raw or not raw.endswith("\n"):
                        log.debug("claim-history mirror: malformed commit %s on %s", current, ref)
                        break
                    headers, encoded_message = raw.split(marker, 1)
                    message = encoded_message[:-1]
                    tree_lines = [
                        line.removeprefix("tree ")
                        for line in headers.splitlines() if line.startswith("tree ")
                    ]
                    parents = [
                        line.removeprefix("parent ")
                        for line in headers.splitlines() if line.startswith("parent ")
                    ]
                    if tree_lines != [empty_tree] or len(parents) > 1:
                        log.debug(
                            "claim-history mirror: unexpected shape at %s on %s",
                            current, ref,
                        )
                        break
                    try:
                        entries.append(_parse_entry(message))
                    except ProtocolError as exc:
                        log.debug(
                            "claim-history mirror: unparsable entry at %s on %s: %s",
                            current, ref, exc,
                        )
                    current = parents[0] if parents else None
                entries.reverse()
                return entries
        except Exception as exc:
            global _read_failures
            _read_failures += 1
            log.debug("claim_history_mirror.fetch(%r, %r) failed: %s", kind, ref_value, exc)
            return []

    def _remote_oid(self, ref: str) -> str | None:
        result = self._git(
            ["ls-remote", "--refs", self.settings.origin, ref], check=True,
        )
        rows = [line for line in result.stdout.splitlines() if line.strip()]
        if not rows:
            return None
        parts = rows[0].split("\t", 1)
        if len(parts) != 2 or len(rows) != 1:
            raise ClaimHistoryMirrorError(f"remote returned an ambiguous result for {ref}")
        return parts[0]

    def _commit_message(self, repo: Path, oid: str) -> str | None:
        """Return ``oid``'s raw commit message (already-fetched into
        ``repo``), or ``None`` if it can't be read -- never raises, since
        callers use this only as a best-effort idempotency probe."""
        try:
            raw = self._git([f"--git-dir={repo}", "cat-file", "commit", oid]).stdout
        except ClaimHistoryMirrorError:
            return None
        marker = "\n\n"
        if marker not in raw or not raw.endswith("\n"):
            return None
        return raw.split(marker, 1)[1][:-1]

    # Only these subcommands reach the shared origin over the network and
    # need the account-scoped auth header; every other call runs against a
    # local ephemeral bare repo (mirrors GitLeaseStore._git's own split).
    _NETWORK_SUBCOMMANDS = frozenset({"ls-remote", "fetch", "push"})

    def _git(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        from . import git_ops

        env = git_ops.repository_identity_env()
        env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
        if extra_env:
            env.update(extra_env)
        call_args = args
        if self._auth_args and any(a in self._NETWORK_SUBCOMMANDS for a in args):
            call_args = [*self._auth_args, *args]
        try:
            with tempfile.TemporaryDirectory(prefix="agent-claim-history-git-") as cwd:
                env["GIT_CEILING_DIRECTORIES"] = str(Path(cwd).parent)
                result = subprocess.run(
                    ["git", *call_args],
                    cwd=cwd, input=input_text, capture_output=True, text=True,
                    env=env, timeout=45, check=False,
                )
        except FileNotFoundError as exc:
            raise ClaimHistoryMirrorError("git executable was not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise ClaimHistoryMirrorError("git command timed out") from exc
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            suffix = detail[-1] if detail else f"exit {result.returncode}"
            raise ClaimHistoryMirrorError(f"git command failed: {suffix}")
        return result


def _store_identity(settings: LeaseSettings) -> str:
    """A stable identity for the destination store (origin + namespace),
    so a persisted sync cursor is scoped to a specific store -- switching
    stores, or running the sweep against a different one, must never reuse
    a count that only ever reflected pushes to a DIFFERENT remote."""
    return f"{settings.origin}#{settings.ref_prefix}"


def _current_project_worktree_ids() -> set[str]:
    """Worktree ids tracked under the CURRENT project's own tracking
    directory -- a sync sweep only ever runs against ONE project's local
    claim_history ledger, but that ledger itself is machine-global (shared
    across every project's worktrees on this machine), so this is the
    restriction that keeps project A's sweep from also uploading project
    B's PR references/session IDs/notes to A's configured store. Any
    failure to read tracking records degrades to an EMPTY set (nothing
    eligible) rather than "assume everything belongs to this project" --
    fail closed, never leak."""
    try:
        from . import tracking
        records = tracking.list_records(cfg.tracking_dir())
    except Exception:
        return set()
    return {r.worktree_id for r in records if getattr(r, "worktree_id", None)}


def _distinct_refs(kind: str | None = None) -> list[tuple[str, str]]:
    """Every distinct ``(kind, ref)`` pair ever recorded locally, in
    first-seen order -- read directly off the ledger file rather than
    :func:`claim_history.history_for_ref` (which needs the ref value
    up front)."""
    path = claim_history.history_path()
    seen: dict[tuple[str, str], None] = {}
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(entry, dict):
                    continue
                k, ref_value = entry.get("kind"), entry.get("ref")
                if not isinstance(k, str) or not isinstance(ref_value, str):
                    continue
                if kind is not None and k != kind:
                    continue
                seen.setdefault((k, ref_value), None)
    except OSError:
        return []
    return list(seen.keys())


def sync_pending(
    *, origin: str | None = None, kind: str | None = None, dry_run: bool = False,
) -> dict[str, object]:
    """Push every locally-recorded claim-history event not yet mirrored to
    the shared store, resuming from each resource's own persisted cursor.
    Degrades to ``{"available": False, ...}`` (never an error) when no
    store is configured for this project -- this sweep is opt-in
    (``agent-worktrees gc --mirror-claim-history``) and must never fail an
    otherwise-successful ``gc``.

    Restricted to events belonging to a worktree THIS project's own
    tracking records currently know about (see
    :func:`_current_project_worktree_ids`) -- the local ledger is
    machine-global, but the configured store is this project's own, so an
    unrelated project's events must never ride along.

    The whole load-push-checkpoint cycle is serialized by a cross-process
    lock (:func:`handoff_trace._append_lock`) -- two overlapping sweeps
    (e.g. two sessions each running ``gc --mirror-claim-history`` at once)
    must never read the same starting cursor and push the same pending
    events twice. Each successful push's cursor is checkpointed
    immediately (not once at the end of the whole sweep), and ``push()``
    itself recognizes an already-applied event at its target position as a
    no-op -- together, a crash or interruption at any point leaves a
    resumable, never-duplicated state.

    ``dry_run=True`` reports how many events are pending per resource
    without pushing or advancing any cursor.
    """
    settings = mirror_settings(origin)
    if settings is None:
        return {"available": False, "pushed": 0, "refs": [], "failed": []}
    mirror = ClaimHistoryMirror(settings)
    store_id = _store_identity(settings)
    owned_ids = _current_project_worktree_ids()
    details: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    pushed_total = 0

    with handoff_trace._append_lock(_state_lock_path()):
        state = _load_state()
        for k, ref_value in _distinct_refs(kind=kind):
            events = [
                e for e in claim_history.history_for_ref(ref_value)
                if e.get("kind") == k and e.get("worktree_id") in owned_ids
            ]
            if not events:
                continue
            resource_key = resource(k, ref_value).identity
            cursor_key = f"{store_id}::{resource_key}"
            already = state.get(cursor_key)
            if already is None:
                # Best-effort migration from a pre-store-scoping cursor --
                # never trusted across a genuinely different store, but
                # avoids wholesale re-pushing this store's own
                # already-mirrored history on upgrade.
                already = state.get(resource_key, 0)
            pending = events[already:]
            if not pending:
                continue
            if dry_run:
                details.append({"ref": ref_value, "kind": k, "pending": len(pending)})
                continue
            pushed_here = 0
            for entry in pending:
                try:
                    mirror.push(entry)
                except ClaimHistoryMirrorError as exc:
                    log.debug("claim_history_mirror.sync_pending: %s", exc)
                    failed.append({"ref": ref_value, "kind": k, "error": str(exc)})
                    break
                pushed_here += 1
                state[cursor_key] = already + pushed_here
                try:
                    _save_state(state)
                except OSError as exc:
                    failed.append(
                        {"ref": ref_value, "kind": k, "error": f"checkpoint write failed: {exc}"}
                    )
                    break
            if pushed_here:
                pushed_total += pushed_here
                details.append({"ref": ref_value, "kind": k, "pushed": pushed_here})
    return {"available": True, "pushed": pushed_total, "refs": details, "failed": failed}


def fetch_remote_history(
    ref_value: str, *, kind: str = "pr", origin: str | None = None,
) -> list[dict]:
    """Read-only pull of one resource's mirrored chain, independent of the
    local sync cursor -- an empty list when no store is configured, the
    ref was never mirrored, or the store is unreachable (never raises)."""
    settings = mirror_settings(origin)
    if settings is None:
        return []
    return ClaimHistoryMirror(settings).fetch(kind, ref_value)
