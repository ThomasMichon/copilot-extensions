"""Git-backed compare-and-swap lease store."""

from __future__ import annotations

import random
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_procutil import no_window_kwargs

from . import git_ops
from .lease_config import LeaseSettings
from .lease_history import ResourceHistory, validate_history
from .lease_protocol import (
    LeaseRecord,
    PROOF_SCHEMA_VERSION,
    ProtocolError,
    RETURN_PROOF_LIMIT,
    Resource,
    ReturnedGeneration,
    format_timestamp,
    parse_record,
    parse_timestamp,
    ref_for,
    record_payload,
    resource,
    resource_from_ref,
    serialize_record,
    validate_context,
    validate_holder,
    validate_oid,
)
from .service_custody_protocol import (
    CoordinationRecord,
    SENTINEL as CUSTODY_SENTINEL,
    ServiceCustodyRecord,
    parse_custody,
)
from .provider_effect_protocol import (
    SENTINEL as EFFECT_SENTINEL,
    ProviderEffectRecord,
    parse_effect,
    serialize_effect,
    validate_begin,
)


class GitError(RuntimeError):
    """A Git transport or plumbing operation failed."""


class LeaseConflict(RuntimeError):
    """A resource's existing lease does not permit this acquisition."""

    def __init__(self, snapshot: LeaseSnapshot, *, released_only: bool = False) -> None:
        self.snapshot = snapshot
        super().__init__(
            f"{snapshot.record.resource['identity']} is leased by "
            f"{snapshot.record.holder!r} until {snapshot.record.expires_at}"
            + ("; --released-only requires an explicit release, not expiry" if released_only else "")
        )


class LeaseLost(RuntimeError):
    """The supplied fencing token is no longer the resource's current OID."""


class ServiceCustodyConflict(LeaseLost):
    """Task operations cannot reinterpret or replace permanent service custody."""

    def __init__(self, snapshot: ServiceCustodySnapshot) -> None:
        self.snapshot = snapshot
        super().__init__(
            f"{snapshot.record.resource['identity']} has permanent service custody "
            f"{snapshot.record.custody_id}; task lease operations are forbidden"
        )


class ProviderEffectConflict(LeaseLost):
    """Unsettled native responsibility cannot expire into a task acquisition."""

    def __init__(self, snapshot: ProviderEffectSnapshot) -> None:
        self.snapshot = snapshot
        super().__init__(
            f"{snapshot.record.resource['identity']} has unsettled provider effect "
            f"{snapshot.record.effect_id}; owner settlement is required"
        )


class _CASConflict(RuntimeError):
    """Internal exact-ref compare-and-swap failure."""


@dataclass(frozen=True)
class LeaseSnapshot:
    """One validated lease ref state plus its fencing token."""

    ref: str
    oid: str
    record: LeaseRecord
    live: bool
    safe_deadline: str
    has_effect_history: bool = False

    def to_dict(self) -> dict[str, object]:
        result = record_payload(self.record)
        result.update(
            {
                "ref": self.ref,
                "token": self.oid,
                "live": self.live,
                "safe_deadline": self.safe_deadline,
            }
        )
        return result


@dataclass(frozen=True)
class ServiceCustodySnapshot:
    """Permanent protection has no task TTL, deadline or renewable holder."""

    ref: str
    oid: str
    record: ServiceCustodyRecord

    def to_dict(self) -> dict[str, object]:
        result = asdict(self.record)
        result.update({"ref": self.ref, "token": self.oid, "custody_type": "service"})
        return result


@dataclass(frozen=True)
class ProviderEffectSnapshot:
    ref: str
    oid: str
    record: ProviderEffectRecord

    def to_dict(self) -> dict[str, object]:
        result = asdict(self.record)
        result.update({"ref": self.ref, "token": self.oid, "custody_type": "provider-effect"})
        return result


ResourceSnapshot = LeaseSnapshot | ServiceCustodySnapshot | ProviderEffectSnapshot


def parse_coordination_record(message: str, expected: Resource) -> CoordinationRecord:
    """Keep task codec admission with its existing store owner."""
    if message.startswith(CUSTODY_SENTINEL + "\n"):
        return parse_custody(message, expected)
    if message.startswith(EFFECT_SENTINEL + "\n"):
        return parse_effect(message, expected)
    return parse_record(message, expected)


class GitLeaseStore:
    """Lease protocol operations against one configured Git origin."""

    def __init__(
        self,
        settings: LeaseSettings,
        *,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.settings = settings
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self._jitter = jitter
        # Account-scoped auth for the network git ops (ls-remote/fetch/push)
        # against the shared origin -- reuse agent-worktrees' cross-account
        # ``http.extraheader`` injection so the push authenticates as the store
        # repo's owner, not the ambient active gh account (the multi-account
        # rule). Empty when the owner *is* the active account (the credential
        # helper already authenticates) or auth context is unset (e.g. tests).
        self._auth_args: list[str] = []
        if settings.auth_remote and settings.auth_cwd:
            try:
                self._auth_args = git_ops._auth_config_args(
                    settings.auth_remote, cwd=settings.auth_cwd
                )
            except git_ops.GitError as exc:
                raise GitError(str(exc)) from exc

    def acquire(
        self,
        kind: str,
        key: str,
        holder: str,
        *,
        ttl_seconds: int | None = None,
        context: object = None,
        retries: int | None = None,
        released_only: bool = False,
    ) -> LeaseSnapshot:
        """Acquire an absent/released lease, or a stale one unless prohibited."""
        item = resource(kind, key)
        holder = validate_holder(holder)
        ttl = self.settings.ttl(ttl_seconds)
        context_value = validate_context(context)
        retry_count = self.settings.acquire_retries if retries is None else retries
        if not 0 <= retry_count <= 10:
            raise ProtocolError("acquisition retries must be between 0 and 10")

        for attempt in range(retry_count + 1):
            current = self.inspect(kind, key)
            now = self._utc_now()
            if current is not None and (
                current.live or (released_only and current.record.state != "released")
            ):
                raise LeaseConflict(current, released_only=released_only)
            event = (
                "takeover"
                if current is not None and current.record.state == "leased"
                else "acquire"
            )
            stamp = format_timestamp(now)
            record = LeaseRecord(
                schema_version=current.record.schema_version if current is not None else 1,
                resource={"identity": item.identity, "kind": item.kind, "key": item.key},
                state="leased",
                event=event,
                lease_id=uuid.uuid4().hex,
                holder=holder,
                issued_at=stamp,
                renewed_at=stamp,
                expires_at=format_timestamp(now + timedelta(seconds=ttl)),
                ttl_seconds=ttl,
                context=context_value,
                returned_generations=current.record.returned_generations if current is not None else (),
            )
            expected = current.oid if current is not None else ""
            try:
                oid = self._transition(item, expected, record)
                return self._snapshot(item, oid, record, now,
                                      has_effect_history=bool(current and current.has_effect_history))
            except _CASConflict:
                if attempt >= retry_count:
                    raise LeaseLost(
                        f"acquisition lost a compare-and-swap race after {attempt + 1} attempts"
                    ) from None
                self._sleep(self._jitter(0.025, min(0.5, 0.05 * (2**attempt))))
        raise AssertionError("unreachable")

    borrow = acquire

    def renew(
        self,
        kind: str,
        key: str,
        token: str,
        *,
        ttl_seconds: int | None = None,
        context: object = None,
    ) -> LeaseSnapshot:
        """Renew the live lease identified by the exact current fencing token."""
        item = resource(kind, key)
        token = validate_oid(token)
        current = self._require_token(item, token)
        now = self._utc_now()
        if not current.live:
            raise LeaseLost("cannot renew a released or expired lease")
        if now > current.record.expires() - timedelta(
            seconds=self.settings.clock_skew_seconds
        ):
            raise LeaseLost("cannot renew after the lease's safe local deadline")
        ttl = self.settings.ttl(ttl_seconds)
        context_value = (
            current.record.context if context is None else validate_context(context)
        )
        record = LeaseRecord(
            schema_version=current.record.schema_version,
            resource=current.record.resource,
            state="leased",
            event="renew",
            lease_id=current.record.lease_id,
            holder=current.record.holder,
            issued_at=current.record.issued_at,
            renewed_at=format_timestamp(now),
            expires_at=format_timestamp(now + timedelta(seconds=ttl)),
            ttl_seconds=ttl,
            context=context_value,
            returned_generations=current.record.returned_generations,
        )
        try:
            oid = self._transition(item, token, record)
        except _CASConflict:
            raise LeaseLost("renewal compare-and-swap failed; the lease is lost") from None
        return self._snapshot(item, oid, record, now, has_effect_history=current.has_effect_history)

    def transfer(
        self,
        kind: str,
        key: str,
        token: str,
        holder: str,
        *,
        ttl_seconds: int | None = None,
        context: object = None,
    ) -> LeaseSnapshot:
        """Atomically fence a live lease over to a new holder.

        The exact current ``token`` is required, so a transfer is a strict
        read-modify-write on the live remote ref rather than a best-effort
        release-and-reacquire dance.
        """
        item = resource(kind, key)
        token = validate_oid(token)
        holder = validate_holder(holder)
        current = self._require_token(item, token)
        now = self._utc_now()
        if not current.live:
            raise LeaseLost("cannot transfer a released or expired lease")
        if now > current.record.expires() - timedelta(
            seconds=self.settings.clock_skew_seconds
        ):
            raise LeaseLost("cannot transfer after the lease's safe local deadline")
        if current.record.holder == holder:
            return current
        ttl = self.settings.ttl(ttl_seconds)
        context_value = (
            current.record.context if context is None else validate_context(context)
        )
        stamp = format_timestamp(now)
        record = LeaseRecord(
            schema_version=current.record.schema_version,
            resource=current.record.resource,
            state="leased",
            event="transfer",
            lease_id=uuid.uuid4().hex,
            holder=holder,
            issued_at=stamp,
            renewed_at=stamp,
            expires_at=format_timestamp(now + timedelta(seconds=ttl)),
            ttl_seconds=ttl,
            context=context_value,
            returned_generations=current.record.returned_generations,
        )
        try:
            oid = self._transition(item, token, record)
        except _CASConflict:
            raise LeaseLost("transfer compare-and-swap failed; the lease is lost") from None
        return self._snapshot(item, oid, record, now, has_effect_history=current.has_effect_history)

    def release(
        self, kind: str, key: str, token: str, *, retain_return_proof: bool = False,
    ) -> LeaseSnapshot:
        """Append a release tombstone iff token is the exact current OID."""
        item = resource(kind, key)
        token = validate_oid(token)
        current = self._require_token(item, token)
        if current.record.state != "leased":
            raise LeaseLost("cannot release a lease that is already tombstoned")
        now = self._utc_now()
        stamp = format_timestamp(now)
        version = PROOF_SCHEMA_VERSION if retain_return_proof else current.record.schema_version
        returned = current.record.returned_generations
        if version == PROOF_SCHEMA_VERSION:
            returned = (*returned, ReturnedGeneration(
                current.record.lease_id, current.record.holder, stamp,
            ))[-RETURN_PROOF_LIMIT:]
        record = LeaseRecord(
            schema_version=version,
            resource=current.record.resource,
            state="released",
            event="release",
            lease_id=current.record.lease_id,
            holder=current.record.holder,
            issued_at=current.record.issued_at,
            renewed_at=stamp,
            expires_at=stamp,
            ttl_seconds=0,
            context=current.record.context,
            returned_generations=returned,
        )
        try:
            oid = self._transition(item, token, record)
        except _CASConflict:
            raise LeaseLost("release compare-and-swap failed; the lease is lost") from None
        return self._snapshot(item, oid, record, now, has_effect_history=current.has_effect_history)

    def squash_stale(
        self, *, retention_days: int, kind: str | None = None, dry_run: bool = False,
    ) -> list[dict[str, str]]:
        """Collapse each RELEASED lease ref past ``retention_days`` to a
        minimal **two-commit** history -- a synthetic ``acquire`` root
        (reusing the terminal record's own real ``lease_id``/``holder``/
        ``issued_at``/``context``, so the original acquisition time and
        who held it both survive) followed by the unmodified terminal
        ``release`` record -- never fewer: the protocol's own
        self-validation (:meth:`_validate_transition`) hard-requires the
        history's root commit to be an ``acquire`` event, so a single
        parentless ``release`` commit would fail every future read. Never
        deleted either, so ``acquire()``'s "absent, released, or stale"
        contract is untouched and the terminal disposition remains
        directly inspectable -- only the discarded intermediate
        renew/takeover/transfer events stop making the ref's history grow
        forever (worktree-claims-transitive-finalization Phase 5).

        Only a ``released`` ref without provider-effect history is ever touched;
        effect responsibilities and their terminal receipts remain recoverable.
        A live/stale ``leased``
        ref is never squashed, regardless of age (its own TTL expiry is
        the only thing that ever makes it re-acquirable). Best-effort per
        ref: a lost CAS race (a concurrent ``acquire()`` on the same
        now-released resource) or a transient Git failure skips that one
        ref rather than aborting the whole sweep. A ref already at or
        below the minimal two-commit shape is left untouched.

        ``dry_run=True`` reports every eligible ref (``old_oid`` only,
        ``new_oid`` is empty) without pushing anything -- the history-length
        probe is still a real (read-only) network fetch, but no force-push
        ever happens.

        Returns one ``{"ref", "old_oid", "new_oid"}`` entry per ref
        actually (or, under ``dry_run``, would-be) squashed, oldest-ref-first
        (by resource identity, via :meth:`list`'s own sort) for a stable,
        reviewable report.
        """
        cutoff = self._utc_now() - timedelta(days=retention_days)
        squashed: list[dict[str, str]] = []
        for snapshot in self.list_resources(kind=kind):
            record = snapshot.record
            if (not isinstance(snapshot, LeaseSnapshot) or snapshot.has_effect_history
                    or record.state != "released"):
                continue
            if parse_timestamp(record.renewed_at) > cutoff:
                continue
            if self._history_length(snapshot.ref) <= 2:
                continue
            if dry_run:
                squashed.append(
                    {"ref": snapshot.ref, "old_oid": snapshot.oid, "new_oid": ""}
                )
                continue
            try:
                new_oid = self._squash_ref(snapshot.ref, snapshot.oid, record)
            except (_CASConflict, GitError):
                continue
            if new_oid != snapshot.oid:
                squashed.append(
                    {"ref": snapshot.ref, "old_oid": snapshot.oid, "new_oid": new_oid}
                )
        return squashed

    def _history_length(self, ref: str) -> int:
        """Total commit count reachable from ``ref``, or a large sentinel
        when it can't be determined (never mistaken for "already minimal",
        which would wrongly skip a genuine squash candidate)."""
        with tempfile.TemporaryDirectory(prefix="agent-leases-gc-check-") as temp:
            repo = Path(temp) / "repo.git"
            self._git(["init", "--bare", str(repo)])
            fetched = self._git(
                [f"--git-dir={repo}", "fetch", "--quiet", "--no-tags",
                 self.settings.origin, ref],
                check=False,
            )
            if fetched.returncode != 0:
                return 1 << 30
            count = self._git(
                [f"--git-dir={repo}", "rev-list", "--count", "FETCH_HEAD"], check=False,
            )
            return int(count.stdout.strip()) if count.returncode == 0 else (1 << 30)

    def _squash_ref(self, ref: str, expected_oid: str, record: LeaseRecord) -> str:
        """Push a minimal synthetic-acquire-root + real-release-leaf pair
        carrying ``record`` over ``ref``, iff the remote still points at
        ``expected_oid`` -- the same compare-and-swap discipline
        :meth:`_transition` uses, minus the parent-fetch step (a squash
        never needs the old history, only the terminal record it's
        replacing with an equivalent minimal chain).
        """
        root_record = LeaseRecord(
            schema_version=record.schema_version,
            resource=record.resource,
            state="leased",
            event="acquire",
            lease_id=record.lease_id,
            holder=record.holder,
            issued_at=record.issued_at,
            renewed_at=record.issued_at,
            expires_at=record.issued_at,
            ttl_seconds=0,
            context=record.context,
            returned_generations=record.returned_generations[:-1],
        )
        with tempfile.TemporaryDirectory(prefix="agent-leases-gc-write-") as temp:
            repo = Path(temp) / "repo.git"
            self._git(["init", "--bare", str(repo)])
            tree = self._git([f"--git-dir={repo}", "mktree"], input_text="").stdout.strip()
            env = {
                "GIT_AUTHOR_NAME": "agent-leases",
                "GIT_AUTHOR_EMAIL": "agent-leases@localhost",
                "GIT_COMMITTER_NAME": "agent-leases",
                "GIT_COMMITTER_EMAIL": "agent-leases@localhost",
            }
            root_oid = self._git(
                [f"--git-dir={repo}", "commit-tree", tree],
                input_text=serialize_record(root_record) + "\n",
                extra_env=env,
            ).stdout.strip()
            validate_oid(root_oid)
            oid = self._git(
                [f"--git-dir={repo}", "commit-tree", tree, "-p", root_oid],
                input_text=serialize_record(record) + "\n",
                extra_env=env,
            ).stdout.strip()
            validate_oid(oid)
            pushed = self._git(
                [f"--git-dir={repo}", "push", "--porcelain",
                 f"--force-with-lease={ref}:{expected_oid}",
                 self.settings.origin, f"{oid}:{ref}"],
                check=False,
            )
            if pushed.returncode != 0:
                remote_oid_now = self._remote_oid(ref)
                if remote_oid_now == oid:
                    return oid
                if remote_oid_now != expected_oid:
                    raise _CASConflict()
                self._raise_git_error(pushed, "push lease squash")
            if self._remote_oid(ref) != oid:
                raise _CASConflict()
            return oid

    def inspect(self, kind: str, key: str) -> LeaseSnapshot | None:
        """Read a task lease; service custody is an explicit protected conflict."""
        snapshot = self.inspect_resource(kind, key)
        if isinstance(snapshot, ServiceCustodySnapshot):
            raise ServiceCustodyConflict(snapshot)
        if isinstance(snapshot, ProviderEffectSnapshot):
            raise ProviderEffectConflict(snapshot)
        return snapshot

    status = inspect

    def inspect_resource(self, kind: str, key: str) -> ResourceSnapshot | None:
        """Read and validate the resource's current ref directly from the remote."""
        selected = self._inspect_history(resource(kind, key))
        if selected is None:
            return None
        ref, oid, history = selected
        record = history.current
        if isinstance(record, ServiceCustodyRecord):
            return ServiceCustodySnapshot(ref=ref, oid=oid, record=record)
        if isinstance(record, ProviderEffectRecord):
            return ProviderEffectSnapshot(ref=ref, oid=oid, record=record)
        return self._snapshot(resource(kind, key), oid, record, self._utc_now(),
                              has_effect_history=bool(history.effects))

    def _inspect_history(self, item: Resource) -> tuple[str, str, ResourceHistory] | None:
        ref = ref_for(self.settings.ref_prefix, item)
        for attempt in range(3):
            oid = self._remote_oid(ref)
            if oid is None:
                return None
            try:
                history = self._read_record(ref, oid, item)
            except _CASConflict:
                if attempt == 2:
                    raise LeaseLost("lease ref changed repeatedly while reading") from None
                continue
            return ref, oid, history
        raise AssertionError("unreachable")

    def list(self, *, kind: str | None = None) -> list[LeaseSnapshot]:
        """Read task leases without silently omitting protected service records."""
        snapshots: list[LeaseSnapshot] = []
        for snapshot in self.list_resources(kind=kind):
            if isinstance(snapshot, ServiceCustodySnapshot):
                raise ServiceCustodyConflict(snapshot)
            if isinstance(snapshot, ProviderEffectSnapshot):
                raise ProviderEffectConflict(snapshot)
            snapshots.append(snapshot)
        return snapshots

    def list_resources(self, *, kind: str | None = None) -> list[ResourceSnapshot]:
        """List and strictly validate every resource ref in the namespace."""
        if kind is not None:
            resource(kind, "_validation_only")
        prefix = self.settings.ref_prefix
        rows = self._ls_remote(f"{prefix}/*")
        snapshots: list[ResourceSnapshot] = []
        for _oid, ref in rows:
            item = resource_from_ref(prefix, ref)
            if kind is not None and item.kind != kind:
                continue
            snapshot = self.inspect_resource(item.kind, item.key)
            if snapshot is None:
                raise LeaseLost(f"lease ref {ref} disappeared while listing")
            snapshots.append(snapshot)
        return sorted(snapshots, key=lambda value: value.record.resource["identity"])

    def begin_effect(self, kind: str, key: str, token: str, *,
                     effect_id: str, owner_id: str, provider_id: str,
                     provider_resource_id: str, action: str,
                     request_digest: str) -> ProviderEffectSnapshot:
        """Trusted provider only: persist responsibility before one native attempt.

        The embedding authenticates current authority, immutable native identity
        and exact request scope. Neither labels nor this record authenticate it.
        No retry, timer-based release or service-owner effect grant is provided.
        """
        item = resource(kind, key)
        token = validate_oid(token)
        selected = self._inspect_history(item)
        if selected is None:
            raise LeaseLost("provider effect requires current task custody")
        ref, oid, history = selected
        if isinstance(history.current, ServiceCustodyRecord):
            raise ServiceCustodyConflict(ServiceCustodySnapshot(ref, oid, history.current))
        if isinstance(history.current, ProviderEffectRecord):
            raise ProviderEffectConflict(ProviderEffectSnapshot(ref, oid, history.current))
        if oid != token:
            raise LeaseLost("provider effect task fence is stale")
        now = self._utc_now(precise=True)
        if now > history.current.expires() - timedelta(seconds=self.settings.clock_skew_seconds):
            raise LeaseLost("provider effect admission is after the task safe deadline")
        record = ProviderEffectRecord(
            schema_version=1, resource=history.current.resource,
            state="effect-unsettled", event="begin", effect_id=effect_id,
            owner_id=owner_id, provider_id=provider_id,
            provider_resource_id=provider_resource_id, action=action,
            request_digest=request_digest, task_token=token, predecessor=token,
            started_at=format_timestamp(now),
            admission_margin_seconds=self.settings.clock_skew_seconds,
        )
        parse_effect(serialize_effect(record), item)
        if any(prior.effect_id == effect_id for _, prior in history.effects):
            raise ProtocolError("provider effect_id cannot be reused")
        validate_begin(history.current, record, parent_oid=token)
        try:
            claimed = self._transition(item, token, record)
        except _CASConflict:
            raise LeaseLost("provider effect admission compare-and-swap failed") from None
        return ProviderEffectSnapshot(ref, claimed, record)

    def settle_effect(self, kind: str, key: str, token: str, *,
                      effect_id: str, owner_id: str, outcome: str,
                      settlement_digest: str) -> ProviderEffectSnapshot:
        """Exact original owner supplies verified native settlement, not a timeout.

        The embedding verifies termination/non-start of the original native
        operation, including any independently running backend action. An
        uncertain subprocess exit or a digest alone cannot establish settlement.
        Recover a lost reply with inspect_effect; never replay the native effect.
        """
        item = resource(kind, key)
        token = validate_oid(token)
        current = self.inspect_resource(kind, key)
        if (not isinstance(current, ProviderEffectSnapshot) or current.oid != token
                or current.record.effect_id != effect_id or current.record.owner_id != owner_id):
            raise LeaseLost("provider effect settlement does not own the current responsibility")
        record = replace(
            current.record, state="effect-settled", event="settle", predecessor=token,
            settled_at=format_timestamp(self._utc_now()), outcome=outcome,
            settlement_digest=settlement_digest,
        )
        parse_effect(serialize_effect(record), item)
        try:
            oid = self._transition(item, token, record)
        except _CASConflict:
            raise LeaseLost("provider effect settlement compare-and-swap failed") from None
        return ProviderEffectSnapshot(current.ref, oid, record)

    def inspect_effect(self, kind: str, key: str, effect_id: str) -> ProviderEffectSnapshot | None:
        """Inspect the original attempt after lost replies or later task transitions."""
        selected = self._inspect_history(resource(kind, key))
        if selected is None:
            return None
        ref, _, history = selected
        for oid, record in reversed(history.effects):
            if record.effect_id == effect_id:
                return ProviderEffectSnapshot(ref, oid, record)
        return None

    def _require_token(self, item: Resource, token: str) -> LeaseSnapshot:
        current = self.inspect(item.kind, item.key)
        if current is None:
            raise LeaseLost("lease ref is absent")
        if current.oid != token:
            raise LeaseLost(
                f"fencing token is stale; expected current OID {current.oid}, got {token}"
            )
        return current

    def _snapshot(
        self,
        item: Resource,
        oid: str,
        record: LeaseRecord,
        now: datetime,
        *,
        has_effect_history: bool = False,
    ) -> LeaseSnapshot:
        grace = timedelta(seconds=self.settings.clock_skew_seconds)
        live = record.state == "leased" and now <= record.expires() + grace
        safe = record.expires() - grace
        return LeaseSnapshot(
            ref=ref_for(self.settings.ref_prefix, item),
            oid=oid,
            record=record,
            live=live,
            safe_deadline=format_timestamp(safe),
            has_effect_history=has_effect_history,
        )

    def _utc_now(self, *, precise: bool = False) -> datetime:
        now = self._now()
        if now.tzinfo is None:
            raise ProtocolError("clock returned a naive datetime")
        utc = now.astimezone(timezone.utc)
        return utc if precise else utc.replace(microsecond=0)

    def _remote_oid(self, ref: str) -> str | None:
        rows = self._ls_remote(ref)
        if not rows:
            return None
        if len(rows) != 1 or rows[0][1] != ref:
            raise GitError(f"remote returned an ambiguous result for {ref}")
        return validate_oid(rows[0][0])

    def _ls_remote(self, pattern: str) -> list[tuple[str, str]]:
        result = self._git(
            ["ls-remote", "--refs", self.settings.origin, pattern],
            check=True,
        )
        rows: list[tuple[str, str]] = []
        for line in result.stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) != 2:
                raise GitError("git ls-remote returned malformed output")
            rows.append((validate_oid(parts[0]), parts[1]))
        return rows

    def _read_record(self, ref: str, expected_oid: str, item: Resource) -> ResourceHistory:
        with tempfile.TemporaryDirectory(prefix="agent-leases-read-") as temp:
            repo = Path(temp) / "repo.git"
            self._git(["init", "--bare", str(repo)])
            fetched = self._git(
                [
                    f"--git-dir={repo}",
                    "fetch",
                    "--quiet",
                    "--no-tags",
                    self.settings.origin,
                    f"+{ref}:refs/agent-leases/read",
                ],
                check=False,
            )
            if fetched.returncode != 0:
                if self._remote_oid(ref) != expected_oid:
                    raise _CASConflict()
                self._raise_git_error(fetched, "fetch lease ref")
            actual = self._git(
                [f"--git-dir={repo}", "rev-parse", "refs/agent-leases/read"]
            ).stdout.strip()
            if actual != expected_oid:
                raise _CASConflict()
            return self._validate_history(repo, expected_oid, item)

    def _validate_history(
        self,
        repo: Path,
        head_oid: str,
        item: Resource,
    ) -> ResourceHistory:
        """Validate the complete linear, empty-tree lease history."""
        empty_tree = self._git(
            [f"--git-dir={repo}", "hash-object", "-t", "tree", "--stdin"],
            input_text="",
        ).stdout.strip()
        chain: list[tuple[str, CoordinationRecord]] = []
        oid = head_oid
        while True:
            raw = self._git(
                [f"--git-dir={repo}", "cat-file", "commit", oid]
            ).stdout
            marker = "\n\n"
            if marker not in raw or not raw.endswith("\n"):
                raise ProtocolError("lease ref does not point to a valid commit")
            headers, encoded_message = raw.split(marker, 1)
            message = encoded_message[:-1]
            tree_lines = [
                line.removeprefix("tree ")
                for line in headers.splitlines()
                if line.startswith("tree ")
            ]
            parents = [
                line.removeprefix("parent ")
                for line in headers.splitlines()
                if line.startswith("parent ")
            ]
            if tree_lines != [empty_tree]:
                raise ProtocolError("lease commits must use the empty tree")
            if len(parents) > 1:
                raise ProtocolError("lease history must be linear")
            chain.append((oid, parse_coordination_record(message, item)))
            if not parents:
                break
            oid = validate_oid(parents[0])

        return validate_history(list(reversed(chain)),
                                clock_skew_seconds=self.settings.clock_skew_seconds)

    def _transition(self, item: Resource, expected_oid: str,
                    record: LeaseRecord | ProviderEffectRecord) -> str:
        ref = ref_for(self.settings.ref_prefix, item)
        with tempfile.TemporaryDirectory(prefix="agent-leases-write-") as temp:
            repo = Path(temp) / "repo.git"
            self._git(["init", "--bare", str(repo)])
            if expected_oid:
                fetched = self._git(
                    [
                        f"--git-dir={repo}",
                        "fetch",
                        "--quiet",
                        "--no-tags",
                        self.settings.origin,
                        f"+{ref}:refs/agent-leases/parent",
                    ],
                    check=False,
                )
                if fetched.returncode != 0:
                    if self._remote_oid(ref) != expected_oid:
                        raise _CASConflict()
                    self._raise_git_error(fetched, "fetch lease parent")
                actual = self._git(
                    [f"--git-dir={repo}", "rev-parse", "refs/agent-leases/parent"]
                ).stdout.strip()
                if actual != expected_oid:
                    raise _CASConflict()

            tree = self._git(
                [f"--git-dir={repo}", "mktree"],
                input_text="",
            ).stdout.strip()
            args = [f"--git-dir={repo}", "commit-tree", tree]
            if expected_oid:
                args += ["-p", expected_oid]
            env = {
                "GIT_AUTHOR_NAME": "agent-leases",
                "GIT_AUTHOR_EMAIL": "agent-leases@localhost",
                "GIT_COMMITTER_NAME": "agent-leases",
                "GIT_COMMITTER_EMAIL": "agent-leases@localhost",
            }
            oid = self._git(
                args,
                input_text=(serialize_effect(record) if isinstance(record, ProviderEffectRecord)
                            else serialize_record(record)) + "\n",
                extra_env=env,
            ).stdout.strip()
            validate_oid(oid)
            self._git(
                [f"--git-dir={repo}", "update-ref", "refs/agent-leases/write", oid]
            )
            pushed = self._git(
                [
                    f"--git-dir={repo}",
                    "push",
                    "--porcelain",
                    f"--force-with-lease={ref}:{expected_oid}",
                    self.settings.origin,
                    f"refs/agent-leases/write:{ref}",
                ],
                check=False,
            )
            if pushed.returncode != 0:
                remote_oid = self._remote_oid(ref)
                if remote_oid == oid:
                    return oid
                if remote_oid != (expected_oid or None):
                    raise _CASConflict()
                self._raise_git_error(pushed, "push lease transition")
            if self._remote_oid(ref) != oid:
                raise _CASConflict()
            return oid

    # Git subcommands that reach the shared origin over the network and must
    # therefore carry the account-scoped auth header; every other invocation
    # runs against a local ephemeral bare repo and needs no credential.
    _NETWORK_SUBCOMMANDS = frozenset({"ls-remote", "fetch", "push"})

    def _git(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = git_ops.repository_identity_env()
        env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
        if extra_env:
            env.update(extra_env)
        call_args = args
        if self._auth_args and any(a in self._NETWORK_SUBCOMMANDS for a in args):
            call_args = [*self._auth_args, *args]
        try:
            with tempfile.TemporaryDirectory(prefix="agent-lease-git-") as cwd:
                env["GIT_CEILING_DIRECTORIES"] = str(Path(cwd).parent)
                result = subprocess.run(
                    ["git", *call_args],
                    cwd=cwd,
                    input=input_text,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=45,
                    check=False,
                    **no_window_kwargs(),
                )
        except FileNotFoundError as exc:
            raise GitError("git executable was not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError("git command timed out") from exc
        if check and result.returncode != 0:
            GitLeaseStore._raise_git_error(result, "git command")
        return result

    @staticmethod
    def _raise_git_error(result: subprocess.CompletedProcess[str], action: str) -> None:
        detail = (result.stderr or result.stdout).strip().splitlines()
        suffix = detail[-1] if detail else f"exit {result.returncode}"
        raise GitError(f"{action} failed: {suffix}")
