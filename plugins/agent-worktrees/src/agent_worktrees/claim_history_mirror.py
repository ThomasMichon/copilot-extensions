"""Remote git-ref mirror for :mod:`claim_history`'s local ownership ledger
(worktree-claims-transitive-finalization Phase 3b's remote-mirroring item).

:mod:`claim_history` is a single machine-local JSONL file
(``logs/claim-history.jsonl``) -- durable across a worktree's own cleanup,
but gone the moment the MACHINE itself is reimaged/retired, and invisible to
any other machine wanting to audit a PR's full ownership trail. This module
mirrors each ``pr``-kind resource's event history to its own append-only Git
ref on the same shared store repo :mod:`lease_store`/:mod:`lease_config`
already use for cross-machine lease coordination (new namespace
``refs/agent-worktrees/claim-history/v1``, same account-scoped auth): a
plain linear chain of empty-tree commits, one per recorded event, oldest at
the root.

**Deliberately a separate, explicit, opt-in sweep -- never synchronous with
a live claim mutation.** Every :mod:`claim_history` write happens inline
during an ordinary claim operation; adding a live network fetch+push to
each one would add real latency and a new failure surface for a durability
concern that tolerates eventual (not immediate) consistency. Instead,
:func:`sync_pending` is wired into ``agent-worktrees gc
--mirror-claim-history`` (opt-in, network + force-push, the same posture
Phase 5's ``--lease-gc`` established for this same store).

**Durable event identity, not payload equality.** Two genuinely distinct
local events can serialize identically (``record_event`` timestamps only to
the second, so a claim released and re-claimed within one second produces
two otherwise-indistinguishable "claimed" records); comparing raw payloads
would wrongly treat the second as "already mirrored" and silently drop it.
Every event pushed here instead carries a ``seq`` -- its own 0-based
position within its resource's FULL local event sequence, in ledger order
-- as its durable identity. ``seq`` is stable forever: it is derived purely
from the append-only ledger's own order, never from anything that can
change later (a worktree's tracking record, a project's live config, ...).

**Stateless by design -- no local sync cursor, no local lock.** An earlier
revision of this module persisted a local JSON "how many events have I
already pushed" cursor, serialized by a local advisory lock. That design
had real gaps a reviewer caught: the lock's own setup could raise and
escape the sweep; a crash between a landed push and its own checkpoint
write could re-push (or, worse, skip) events; and a cursor based on a
PROJECT-FILTERED event list silently drifted whenever that filter's output
changed shape (e.g. a worktree's tracking record got reaped between
sweeps). Removing the local cursor file removes that whole class of bugs
outright rather than patching each one: :func:`sync_pending` always asks
the REMOTE directly (:meth:`ClaimHistoryMirror.fetch`) which ``seq``
values a resource's chain already has, and only pushes the ones still
missing. :meth:`ClaimHistoryMirror.push` repeats that same check against
the remote's CURRENT state immediately before committing (not just once
up front), so two sweeps racing on the same ref are safe without any local
coordination at all -- one wins the compare-and-swap; the other's retry
re-fetches, sees its ``seq`` now present, and no-ops.

**Durable project attribution.** A sweep must never upload one project's
PR references/session IDs/notes to a DIFFERENT project's configured store
-- the local ledger is machine-global, but a sweep's store is one
project's own. Every event :mod:`claim_history` records now carries an
optional ``project`` stamp (``claim_history.current_project_name()``,
best-effort, at write time) -- durable, since it survives a worktree's own
tracking record being retired long after the event was recorded. A legacy
event recorded before this stamp existed falls back to
:func:`_current_project_worktree_ids`'s live-tracking-record heuristic
(lossy once a worktree is reaped, but fail-closed: never exported when
neither signal vouches for it).

**Reading.** :func:`fetch_remote_history` is a plain, read-only,
best-effort pull of one resource's mirrored chain -- safe to call any time
(e.g. ``claims history <ref> --remote``) to see what other machines have
mirrored, whether or not THIS machine has ever pushed to that same ref
itself.
"""

from __future__ import annotations

import json
import logging
import random
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from . import claim_history
from .lease_config import ConfigError, LeaseSettings, load_lease_settings
from .lease_protocol import ProtocolError, canonical_json, ref_for, resource

log = logging.getLogger("agent-worktrees")

#: Hidden ref namespace for the claim-history mirror -- a sibling of
#: ``lease_config.DEFAULT_REF_PREFIX`` on the same store repo, never a
#: branch/tag.
DEFAULT_REF_PREFIX = "refs/agent-worktrees/claim-history/v1"

_SENTINEL = "agent-worktrees-claim-history-envelope-v1"

#: Fields every mirrored event carries as a plain string, always -- even
#: when the value is empty (a legacy ``machine=""`` record is a real, if
#: degraded, value, never an absent one).
_REQUIRED_STR_KEYS = frozenset({"ts", "kind", "ref", "worktree_id", "machine", "event"})

#: Fields a mirrored event may legitimately omit -- dropped from the
#: serialized payload only when absent/empty.
_OPTIONAL_KEYS = frozenset({"session_id", "note"})

_write_failures = 0
_read_failures = 0


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


def mirror_settings(origin: str | None = None) -> LeaseSettings | None:
    """Resolve the shared store's settings for the claim-history namespace,
    or ``None`` when no store is configured for this project -- a no-op,
    never a raised error, since mirroring is opt-in."""
    try:
        return load_lease_settings(origin=origin, ref_prefix=DEFAULT_REF_PREFIX)
    except ConfigError:
        return None


def _serialize_entry(entry: dict) -> str:
    payload = {key: entry.get(key, "") for key in _REQUIRED_STR_KEYS}
    payload["seq"] = int(entry["seq"])
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
    if not isinstance(data, dict):
        raise ProtocolError("claim-history commit payload is not an object")
    if not _REQUIRED_STR_KEYS.issubset(data) or "seq" not in data:
        raise ProtocolError("claim-history commit payload is missing required fields")
    for key in _REQUIRED_STR_KEYS:
        if not isinstance(data[key], str):
            raise ProtocolError(f"claim-history field {key!r} must be a string")
    # bool is a subclass of int -- exclude it explicitly so a stray
    # True/False can never masquerade as a valid sequence number.
    if not isinstance(data["seq"], int) or isinstance(data["seq"], bool):
        raise ProtocolError("claim-history field 'seq' must be an integer")
    for key in _OPTIONAL_KEYS:
        if key in data and not isinstance(data[key], str):
            raise ProtocolError(f"claim-history field {key!r} must be a string")
    if not _REQUIRED_STR_KEYS.union(_OPTIONAL_KEYS, {"seq"}).issuperset(data):
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

    def push(self, entry: dict) -> bool:
        """Append one history ``entry`` (carrying its own durable ``seq``)
        to its resource's remote mirror ref, unless that ``seq`` is already
        present anywhere in the remote chain. Returns ``True`` if a new
        commit was created, ``False`` if the event was already there (a
        genuine no-op, not an error). The existence check is re-done
        against the CURRENT remote state on every attempt (not just once up
        front), so two sweeps racing on the same ref need no local
        coordination: one wins the compare-and-swap push; the other's
        retry re-fetches, finds its ``seq`` now present, and returns
        ``False``. Raises :class:`ClaimHistoryMirrorError` only after
        exhausting retries on a non-idempotent failure (a genuine,
        persistent Git transport problem) -- callers (:func:`sync_pending`)
        treat that as "this one entry stays pending, try again next
        sweep," never fatal to the sweep as a whole.
        """
        global _write_failures
        item = resource(str(entry["kind"]), str(entry["ref"]))
        ref = ref_for(self.settings.ref_prefix, item)
        seq = int(entry["seq"])
        message = _serialize_entry(entry)
        attempt = 0
        while attempt <= self.retries:
            if any(e.get("seq") == seq for e in self.fetch(item.kind, item.key)):
                return False
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
                        # parent/existing-seq set rather than committing on
                        # top of one we couldn't actually fetch.
                        attempt += 1
                        self._sleep(self._jitter(0.025, min(0.5, 0.05 * (2**attempt))))
                        continue
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
                return True
            remote_now = self._remote_oid(ref)
            if remote_now == oid:
                return True
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


def _current_project_worktree_ids() -> set[str]:
    """Worktree ids tracked under the CURRENT project's own tracking
    directory -- the fallback eligibility signal for a LEGACY event
    recorded before :mod:`claim_history` started stamping a durable
    ``project`` field (see :func:`_event_eligible`). Any failure to read
    tracking records degrades to an EMPTY set (nothing eligible) rather
    than "assume everything belongs to this project" -- fail closed,
    never leak."""
    try:
        from . import config as cfg
        from . import tracking
        records = tracking.list_records(cfg.tracking_dir())
    except Exception:
        return set()
    return {r.worktree_id for r in records if getattr(r, "worktree_id", None)}


def _event_eligible(entry: dict, *, project_name: str | None, owned_ids: set[str]) -> bool:
    """Whether ``entry`` may be mirrored to the CURRENT project's
    configured store. A durable ``project`` stamp (present on every event
    recorded since that field existed) is authoritative and never falls
    back to the tracking-record heuristic, even if it disagrees -- it
    survives exactly the tracking-record retirement the heuristic cannot.
    A legacy event with no stamp at all falls back to
    :func:`_current_project_worktree_ids`."""
    project = entry.get("project")
    if isinstance(project, str) and project:
        return project_name is not None and project == project_name
    return entry.get("worktree_id") in owned_ids


def _grouped_events(kind: str | None = None) -> dict[tuple[str, str], list[dict]]:
    """Parse the append-only ledger exactly once, grouping every event by
    its ``(kind, ref)`` resource in ledger order and stamping each with its
    own durable, position-based ``seq`` (its 0-based index within that
    resource's own full event sequence) -- the identity
    :meth:`ClaimHistoryMirror.push`/:meth:`~ClaimHistoryMirror.fetch` use
    for idempotency. Re-reading the whole ledger once per sweep (rather
    than once per resource, as an earlier revision did via repeated
    :func:`claim_history.history_for_ref` calls) avoids reparsing
    roughly resources-times-ledger-length worth of lines on every run.
    """
    path = claim_history.history_path()
    grouped: dict[tuple[str, str], list[dict]] = {}
    if not path.exists():
        return grouped
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
                bucket = grouped.setdefault((k, ref_value), [])
                stamped = dict(entry)
                stamped["seq"] = len(bucket)
                bucket.append(stamped)
    except OSError:
        return {}
    return grouped


def sync_pending(
    *, origin: str | None = None, kind: str | None = None, dry_run: bool = False,
) -> dict[str, object]:
    """Push every locally-recorded, eligible claim-history event not yet
    present on the shared store. Degrades to ``{"available": False, ...}``
    (never an error) when no store is configured for this project -- this
    sweep is opt-in (``agent-worktrees gc --mirror-claim-history``) and
    must never fail an otherwise-successful ``gc``.

    Restricted to events eligible for THIS project's configured store (see
    :func:`_event_eligible`) -- the local ledger is machine-global, but the
    configured store is this project's own, so an unrelated project's
    events must never ride along.

    Stateless: no local cursor, no local lock (see the module docstring).
    One preliminary :meth:`~ClaimHistoryMirror.fetch` per resource finds
    what the remote already has; :meth:`~ClaimHistoryMirror.push` itself
    re-confirms against the remote's CURRENT state before every commit, so
    the whole operation is safe under concurrent/overlapping sweeps without
    needing to coordinate with them at all.

    ``dry_run=True`` reports how many events are genuinely still missing
    per resource without pushing anything.
    """
    settings = mirror_settings(origin)
    if settings is None:
        return {"available": False, "pushed": 0, "refs": [], "failed": []}
    mirror = ClaimHistoryMirror(settings)
    project_name = claim_history.current_project_name()
    owned_ids = _current_project_worktree_ids()
    details: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    pushed_total = 0

    for (k, ref_value), events in _grouped_events(kind=kind).items():
        eligible = [
            e for e in events
            if _event_eligible(e, project_name=project_name, owned_ids=owned_ids)
        ]
        if not eligible:
            continue
        existing_seqs = {e.get("seq") for e in mirror.fetch(k, ref_value)}
        pending = [e for e in eligible if e["seq"] not in existing_seqs]
        if not pending:
            continue
        if dry_run:
            details.append({"ref": ref_value, "kind": k, "pending": len(pending)})
            continue
        pushed_here = 0
        for entry in pending:
            try:
                if mirror.push(entry):
                    pushed_here += 1
            except ClaimHistoryMirrorError as exc:
                log.debug("claim_history_mirror.sync_pending: %s", exc)
                failed.append({"ref": ref_value, "kind": k, "error": str(exc)})
                break
        if pushed_here:
            pushed_total += pushed_here
            details.append({"ref": ref_value, "kind": k, "pushed": pushed_here})
    return {"available": True, "pushed": pushed_total, "refs": details, "failed": failed}


def fetch_remote_history(
    ref_value: str, *, kind: str = "pr", origin: str | None = None,
) -> list[dict]:
    """Read-only pull of one resource's mirrored chain -- an empty list
    when no store is configured, the ref was never mirrored, or the store
    is unreachable (never raises)."""
    settings = mirror_settings(origin)
    if settings is None:
        return []
    return ClaimHistoryMirror(settings).fetch(kind, ref_value)
