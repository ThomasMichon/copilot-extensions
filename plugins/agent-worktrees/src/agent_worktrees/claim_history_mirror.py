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

**Globally unique event identity, not local position alone.** Every event
pushed here carries a ``(ledger_id, seq)`` pair as its durable identity:
``seq`` is the event's own 0-based position within its resource's full
LOCAL event sequence (stable forever -- the ledger is append-only); ``
ledger_id`` is a random id minted once for this machine's own ledger
incarnation (:func:`_ledger_id`, persisted alongside the ledger file
itself). ``seq`` alone is only unique WITHIN one ledger's lifetime -- two
independent machines' first events for the same PR both get ``seq=0``, and
a reimaged machine starting a fresh ledger would collide with its own
earlier incarnation's ``seq=0`` too. ``ledger_id`` discriminates both
cases; a lost/absent ledger file (no prior incarnation at all, or a
genuine reimage) mints a fresh one.

**One verified remote snapshot per operation.** Reading "what does the
remote already have" and "what tip do I compare-and-swap against" from two
SEPARATE network round trips (an ``ls-remote`` then a later ``fetch``, or
vice versa) lets a concurrent writer move the ref in between, producing a
torn read: a push could adopt a parent that doesn't actually match the
content it just inspected. :meth:`ClaimHistoryMirror._fetch_chain` is the
ONE place that reads the remote; it returns a tip oid and the fully parsed
chain from that EXACT oid together, and re-validates after its own fetch
that the ref didn't move underneath it. Every other method (``fetch``,
``push_batch``) is built on top of that single verified read -- never a
second independent query for "the same" state.

**Batched, not per-event.** :meth:`ClaimHistoryMirror.push_batch` pushes
every still-missing event for one resource as ONE linear chain of commits
in a SINGLE network push (one verified snapshot read, then one
compare-and-swap write) -- not one fetch+push round trip per event, which
would turn an N-event backfill into roughly N times the resource's own
growing chain length worth of commit reads. :meth:`~ClaimHistoryMirror.push`
(a single event) is a thin wrapper over a one-entry batch, kept for
callers that only ever have one event in hand.

**Stateless by design -- no local sync cursor, no local lock.** An earlier
revision of this module persisted a local JSON "how many events have I
already pushed" cursor, serialized by a local advisory lock -- a design
with real gaps (the lock's own setup could raise and escape the sweep; a
crash between a landed push and its own checkpoint write could re-push or
skip events; a cursor derived from a PROJECT-FILTERED list silently
drifted when that filter's output changed shape). :func:`sync_pending`
instead always asks the REMOTE directly which identities a resource's
chain already has, and only pushes what's missing -- two overlapping
sweeps need no local coordination at all; one wins the compare-and-swap,
the other's retry re-reads and finds its own events already there.

**Durable project attribution.** A sweep must never upload one project's
PR references/session IDs/notes to a DIFFERENT project's configured store.
Every event :mod:`claim_history` records now carries an explicit
``project`` whenever its caller has one to pass (the OWNING worktree's own
``WorktreeRecord.repo`` -- never ambient config, which can genuinely
differ from the record actually being mutated, e.g. an ``--owner-ref``
cross-project write or a daemon dispatch with no project context of its
own). A legacy event recorded before this field existed, or written by a
caller with no record-derived project in hand, falls back to
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
import uuid
from collections.abc import Callable
from pathlib import Path

from . import claim_history, handoff_trace
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
_REQUIRED_STR_KEYS = frozenset(
    {"ts", "kind", "ref", "worktree_id", "machine", "event", "ledger_id"}
)

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


class _SnapshotUnavailable(Exception):
    """Internal: the remote couldn't be read as one consistent, verified
    snapshot (a transient failure, or the ref moved between the initial
    ``ls-remote`` and the follow-up ``fetch`` of that exact oid) -- always
    caught and translated into a bounded retry by the caller, never
    surfaced directly."""


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


def _event_identity(entry: dict) -> tuple[object, object]:
    return (entry.get("ledger_id"), entry.get("seq"))


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
        """Append one history ``entry`` -- a thin single-event wrapper over
        :meth:`push_batch`. Returns ``True`` if a new commit was created,
        ``False`` if the event's identity was already present remotely (a
        genuine no-op, not an error)."""
        return self.push_batch([entry]) > 0

    def push_batch(self, entries: list[dict]) -> int:
        """Push every entry in ``entries`` (all for the SAME ``(kind,
        ref)`` resource, in the order given) that the remote doesn't
        already have, as ONE linear chain of commits in a single network
        push -- one verified snapshot read, then one compare-and-swap
        write, rather than one fetch+push round trip per event (which
        would make an N-event backfill read roughly N times the
        resource's own growing chain length worth of commits). Returns
        how many were actually newly committed (``0`` if every entry was
        already present -- a legitimate no-op). Raises
        :class:`ClaimHistoryMirrorError` only after exhausting retries on
        a non-idempotent failure -- callers (:func:`sync_pending`) treat
        that as "this batch stays pending, try again next sweep," never
        fatal to the sweep as a whole.
        """
        global _write_failures
        if not entries:
            return 0
        kind, ref_value = entries[0]["kind"], entries[0]["ref"]
        if any(e["kind"] != kind or e["ref"] != ref_value for e in entries):
            raise ValueError("push_batch requires every entry to share one (kind, ref)")
        item = resource(str(kind), str(ref_value))
        ref = ref_for(self.settings.ref_prefix, item)
        wanted = [(_event_identity(e), e) for e in entries]

        attempt = 0
        while attempt <= self.retries:
            try:
                tip_oid, existing = self._fetch_chain(item.kind, item.key)
            except _SnapshotUnavailable:
                attempt += 1
                self._sleep(self._jitter(0.025, min(0.5, 0.05 * (2**attempt))))
                continue
            existing_ids = {_event_identity(e) for e in existing}
            pending = [e for ident, e in wanted if ident not in existing_ids]
            if not pending:
                return 0

            with tempfile.TemporaryDirectory(prefix="agent-claim-history-write-") as temp:
                repo = Path(temp) / "repo.git"
                self._git(["init", "--bare", str(repo)])
                if tip_oid:
                    # The parent commit object only exists in whichever
                    # ephemeral repo created it (long since deleted) --
                    # fetch it into THIS repo first so commit-tree -p can
                    # resolve it.
                    fetched = self._git(
                        [
                            f"--git-dir={repo}", "fetch", "--quiet", "--no-tags",
                            self.settings.origin,
                            f"+{ref}:refs/agent-claim-history/parent",
                        ],
                        check=False,
                    )
                    if fetched.returncode != 0:
                        attempt += 1
                        self._sleep(self._jitter(0.025, min(0.5, 0.05 * (2**attempt))))
                        continue
                tree = self._git(
                    [f"--git-dir={repo}", "mktree"], input_text="",
                ).stdout.strip()
                env = {
                    "GIT_AUTHOR_NAME": "agent-worktrees-claim-history",
                    "GIT_AUTHOR_EMAIL": "agent-worktrees-claim-history@localhost",
                    "GIT_COMMITTER_NAME": "agent-worktrees-claim-history",
                    "GIT_COMMITTER_EMAIL": "agent-worktrees-claim-history@localhost",
                }
                parent = tip_oid
                for e in pending:
                    args = [f"--git-dir={repo}", "commit-tree", tree]
                    if parent:
                        args += ["-p", parent]
                    parent = self._git(
                        args, input_text=_serialize_entry(e) + "\n", extra_env=env,
                    ).stdout.strip()
                final_oid = parent
                pushed = self._git(
                    [
                        f"--git-dir={repo}", "push", "--porcelain",
                        f"--force-with-lease={ref}:{tip_oid or ''}",
                        self.settings.origin, f"{final_oid}:{ref}",
                    ],
                    check=False,
                )
            if pushed.returncode == 0:
                return len(pending)
            remote_now = self._remote_oid(ref)
            if remote_now == final_oid:
                return len(pending)
            if attempt >= self.retries:
                _write_failures += 1
                detail = (pushed.stderr or pushed.stdout).strip().splitlines()
                suffix = detail[-1] if detail else f"exit {pushed.returncode}"
                raise ClaimHistoryMirrorError(
                    f"giving up mirroring {ref_value!r} after "
                    f"{attempt + 1} attempts: {suffix}"
                )
            attempt += 1
            self._sleep(self._jitter(0.025, min(0.5, 0.05 * (2**attempt))))
        _write_failures += 1
        raise ClaimHistoryMirrorError(
            f"giving up mirroring {ref_value!r} after exhausting retries"
        )

    def fetch(self, kind: str, ref_value: str) -> list[dict]:
        """Return every mirrored event for ``(kind, ref_value)``, oldest
        first. Never raises -- an absent ref, an unreachable store, a
        race with a concurrent writer, or a malformed commit along the
        way degrades to an empty or partial result (the entries that DID
        parse cleanly), logged at debug.
        """
        global _read_failures
        try:
            _tip, entries = self._fetch_chain(kind, ref_value)
            return entries
        except _SnapshotUnavailable:
            _read_failures += 1
            log.debug(
                "claim_history_mirror.fetch(%r, %r): remote snapshot unavailable",
                kind, ref_value,
            )
            return []
        except Exception as exc:
            _read_failures += 1
            log.debug("claim_history_mirror.fetch(%r, %r) failed: %s", kind, ref_value, exc)
            return []

    def _fetch_chain(self, kind: str, ref_value: str) -> tuple[str | None, list[dict]]:
        """Read ``(tip_oid, entries)`` as ONE verified remote snapshot --
        the single place this class ever reads remote state, so a caller
        needing both "what's already there" and "what oid to
        compare-and-swap against" never risks a torn read across two
        separate network round trips. ``tip_oid`` is ``None`` only for a
        genuinely absent ref (a valid, verified empty snapshot); any
        failure to confirm the read -- a transient Git error, or the ref
        having moved between the initial ``ls-remote`` and the follow-up
        fetch of that exact oid -- raises :class:`_SnapshotUnavailable`
        rather than returning a value that might not actually be current.
        """
        item = resource(kind, ref_value)
        ref = ref_for(self.settings.ref_prefix, item)
        oid = self._remote_oid(ref)
        if oid is None:
            return None, []
        with tempfile.TemporaryDirectory(prefix="agent-claim-history-read-") as temp:
            repo = Path(temp) / "repo.git"
            self._git(["init", "--bare", str(repo)])
            fetched = self._git(
                [
                    f"--git-dir={repo}", "fetch", "--quiet", "--no-tags",
                    self.settings.origin, f"+{ref}:refs/agent-claim-history/read",
                ],
                check=False,
            )
            if fetched.returncode != 0:
                raise _SnapshotUnavailable(f"fetch of {ref} failed")
            actual = self._git(
                [f"--git-dir={repo}", "rev-parse", "refs/agent-claim-history/read"]
            ).stdout.strip()
            if actual != oid:
                # The ref moved between our ls-remote and this fetch -- the
                # content we're about to read would not correspond to
                # `oid`. Never silently substitute the moved tip; the
                # caller retries with a fresh read instead.
                raise _SnapshotUnavailable(f"{ref} moved during read")
            empty_tree = self._git(
                [f"--git-dir={repo}", "hash-object", "-t", "tree", "--stdin"],
                input_text="",
            ).stdout.strip()
            entries: list[dict] = []
            current: str | None = oid
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
                        "claim-history mirror: unexpected shape at %s on %s", current, ref,
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
            return oid, entries

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


def _ledger_id_path() -> Path:
    return claim_history.history_path().with_name("claim-history.ledger-id")


def _ledger_id() -> str:
    """A persistent, random identifier for the LOCAL ledger's current
    incarnation -- the discriminator half of a mirrored event's
    ``(ledger_id, seq)`` identity, since ``seq`` alone is only unique
    WITHIN one ledger's own lifetime: two independent machines' first
    events for the same PR both get ``seq=0``, and a reimaged machine
    starting a fresh, empty ledger would otherwise collide with its own
    earlier incarnation's ``seq=0`` too. Minted fresh (and persisted)
    whenever the ledger file itself doesn't exist yet alongside a prior id
    -- the strongest available local signal of "this is a new
    incarnation," pragmatic rather than airtight against every possible
    on-disk corruption, but correct for the two cases that matter here:
    two genuinely independent machines, and a genuine reimage.

    The whole check-then-mint-then-persist sequence is serialized by the
    same cross-process advisory lock :mod:`claim_history` uses for its own
    ledger appends -- two overlapping first sweeps racing to initialize
    this sidecar must never each mint and persist a DIFFERENT id (the
    second write winning silently would orphan whichever events the first
    sweep already stamped and pushed under its own, now-overwritten id,
    since no future read would ever match them again).
    """
    ledger_path = claim_history.history_path()
    id_path = _ledger_id_path()
    lock_path = id_path.with_suffix(id_path.suffix + ".lock")
    with handoff_trace._append_lock(lock_path):
        if ledger_path.exists() and id_path.exists():
            try:
                existing = id_path.read_text(encoding="utf-8").strip()
            except OSError:
                existing = ""
            if existing:
                return existing
        new_id = uuid.uuid4().hex
        id_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = id_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(new_id, encoding="utf-8")
            tmp.replace(id_path)
        finally:
            tmp.unlink(missing_ok=True)
        return new_id


def _ledger_id_readonly() -> str | None:
    """Read-only sibling of :func:`_ledger_id` -- the persisted ledger
    incarnation id if the sidecar already exists, else ``None``. NEVER
    mints or writes one: a pure display/read path (e.g. ``claims history
    --remote``'s own local/remote merge) must never have the side effect
    of creating a fresh ledger identity merely by being invoked."""
    id_path = _ledger_id_path()
    if not id_path.exists():
        return None
    try:
        existing = id_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return existing or None


def local_identities_for_ref(kind: str, ref_value: str) -> list[dict]:
    """Local events for ``(kind, ref_value)``, each stamped with the same
    ``(seq, ledger_id)`` durable identity a sync sweep would use -- for
    comparing against a resource's MIRRORED chain (e.g. ``claims history
    --remote``'s own merge), never for pushing. Read-only: uses
    :func:`_ledger_id_readonly`, so merely displaying history never mints
    a ledger id as a side effect. A ``ledger_id`` of ``None`` here (no
    sidecar yet -- this machine has never mirrored anything) means no
    local event can be durably matched against a remote one; callers
    should treat that as "no confirmed match," never a false one.
    """
    events = [
        e for e in claim_history.history_for_ref(ref_value) if e.get("kind") == kind
    ]
    ledger_id = _ledger_id_readonly()
    stamped = []
    for i, e in enumerate(events):
        entry = dict(e)
        entry["seq"] = i
        entry["ledger_id"] = ledger_id
        stamped.append(entry)
    return stamped


def _current_project_worktree_ids() -> set[str]:
    """Worktree ids tracked under the CURRENT project's own tracking
    directory -- the fallback eligibility signal for a LEGACY event
    recorded before :mod:`claim_history` started stamping an explicit
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
    configured store. A durable ``project`` stamp (present whenever its
    original caller had a record-derived project to pass) is authoritative
    and never falls back to the tracking-record heuristic, even if it
    disagrees -- it survives exactly the tracking-record retirement the
    heuristic cannot. A legacy/unstamped event falls back to
    :func:`_current_project_worktree_ids`."""
    project = entry.get("project")
    if isinstance(project, str) and project:
        return project_name is not None and project == project_name
    return entry.get("worktree_id") in owned_ids


def _grouped_events(kind: str | None = None) -> dict[tuple[str, str], list[dict]]:
    """Parse the append-only ledger exactly once, grouping every event by
    its ``(kind, ref)`` resource in ledger order and stamping each with its
    own durable ``(ledger_id, seq)`` identity (``seq`` -- its 0-based index
    within that resource's own full event sequence) -- the identity
    :meth:`ClaimHistoryMirror.push_batch`/:meth:`~ClaimHistoryMirror.fetch`
    use. Re-reading the whole ledger once per sweep (rather than once per
    resource, as an earlier revision did via repeated
    :func:`claim_history.history_for_ref` calls) avoids reparsing roughly
    resources-times-ledger-length worth of lines on every run.
    """
    path = claim_history.history_path()
    grouped: dict[tuple[str, str], list[dict]] = {}
    if not path.exists():
        return grouped
    ledger_id = _ledger_id()
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
                stamped["ledger_id"] = ledger_id
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

    Stateless and batched (see the module docstring): one verified remote
    snapshot per resource per attempt, one push for however many events
    are genuinely missing.

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

    try:
        grouped = _grouped_events(kind=kind)
    except OSError as exc:
        # The ledger-id sidecar's own init failed (disk full, permissions,
        # ...) -- every event in this sweep is unstampable without it, so
        # report that plainly rather than silently sweeping nothing.
        return {
            "available": True, "pushed": 0, "refs": [],
            "failed": [{"ref": None, "kind": kind, "error": f"ledger id init failed: {exc}"}],
        }

    for (k, ref_value), events in grouped.items():
        eligible = [
            e for e in events
            if _event_eligible(e, project_name=project_name, owned_ids=owned_ids)
        ]
        if not eligible:
            continue
        if dry_run:
            try:
                _tip, existing = mirror._fetch_chain(k, ref_value)
            except (_SnapshotUnavailable, ClaimHistoryMirrorError) as exc:
                failed.append({"ref": ref_value, "kind": k, "error": str(exc)})
                continue
            existing_ids = {_event_identity(e) for e in existing}
            pending = [e for e in eligible if _event_identity(e) not in existing_ids]
            if pending:
                details.append({"ref": ref_value, "kind": k, "pending": len(pending)})
            continue
        try:
            pushed_here = mirror.push_batch(eligible)
        except ClaimHistoryMirrorError as exc:
            log.debug("claim_history_mirror.sync_pending: %s", exc)
            failed.append({"ref": ref_value, "kind": k, "error": str(exc)})
            continue
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
