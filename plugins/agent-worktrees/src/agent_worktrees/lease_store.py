"""Git-backed compare-and-swap lease store."""

from __future__ import annotations

import os
import random
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .lease_config import LeaseSettings
from .lease_protocol import (
    LeaseRecord,
    ProtocolError,
    Resource,
    format_timestamp,
    parse_record,
    ref_for,
    resource,
    resource_from_ref,
    serialize_record,
    validate_context,
    validate_holder,
    validate_oid,
)


class GitError(RuntimeError):
    """A Git transport or plumbing operation failed."""


class LeaseConflict(RuntimeError):
    """A resource has a live lease held by another client."""

    def __init__(self, snapshot: LeaseSnapshot) -> None:
        self.snapshot = snapshot
        super().__init__(
            f"{snapshot.record.resource['identity']} is leased by "
            f"{snapshot.record.holder!r} until {snapshot.record.expires_at}"
        )


class LeaseLost(RuntimeError):
    """The supplied fencing token is no longer the resource's current OID."""


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

    def to_dict(self) -> dict[str, object]:
        result = asdict(self.record)
        result.update(
            {
                "ref": self.ref,
                "token": self.oid,
                "live": self.live,
                "safe_deadline": self.safe_deadline,
            }
        )
        return result


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
                from . import git_ops
                self._auth_args = git_ops._auth_config_args(
                    settings.auth_remote, cwd=settings.auth_cwd
                )
            except Exception:
                self._auth_args = []

    def acquire(
        self,
        kind: str,
        key: str,
        holder: str,
        *,
        ttl_seconds: int | None = None,
        context: object = None,
        retries: int | None = None,
    ) -> LeaseSnapshot:
        """Acquire an absent, released, or stale resource lease."""
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
            if current is not None and current.live:
                raise LeaseConflict(current)
            event = (
                "takeover"
                if current is not None and current.record.state == "leased"
                else "acquire"
            )
            stamp = format_timestamp(now)
            record = LeaseRecord(
                schema_version=1,
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
            )
            expected = current.oid if current is not None else ""
            try:
                oid = self._transition(item, expected, record)
                return self._snapshot(item, oid, record, now)
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
            schema_version=1,
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
        )
        try:
            oid = self._transition(item, token, record)
        except _CASConflict:
            raise LeaseLost("renewal compare-and-swap failed; the lease is lost") from None
        return self._snapshot(item, oid, record, now)

    def release(self, kind: str, key: str, token: str) -> LeaseSnapshot:
        """Append a release tombstone iff token is the exact current OID."""
        item = resource(kind, key)
        token = validate_oid(token)
        current = self._require_token(item, token)
        if current.record.state != "leased":
            raise LeaseLost("cannot release a lease that is already tombstoned")
        now = self._utc_now()
        stamp = format_timestamp(now)
        record = LeaseRecord(
            schema_version=1,
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
        )
        try:
            oid = self._transition(item, token, record)
        except _CASConflict:
            raise LeaseLost("release compare-and-swap failed; the lease is lost") from None
        return self._snapshot(item, oid, record, now)

    def inspect(self, kind: str, key: str) -> LeaseSnapshot | None:
        """Read and validate the resource's current ref directly from the remote."""
        item = resource(kind, key)
        ref = ref_for(self.settings.ref_prefix, item)
        for attempt in range(3):
            oid = self._remote_oid(ref)
            if oid is None:
                return None
            try:
                record = self._read_record(ref, oid, item)
            except _CASConflict:
                if attempt == 2:
                    raise LeaseLost("lease ref changed repeatedly while reading") from None
                continue
            return self._snapshot(item, oid, record, self._utc_now())
        raise AssertionError("unreachable")

    status = inspect

    def list(self, *, kind: str | None = None) -> list[LeaseSnapshot]:
        """List and strictly validate every resource ref in the namespace."""
        if kind is not None:
            resource(kind, "_validation_only")
        prefix = self.settings.ref_prefix
        rows = self._ls_remote(f"{prefix}/*")
        snapshots: list[LeaseSnapshot] = []
        for _oid, ref in rows:
            item = resource_from_ref(prefix, ref)
            if kind is not None and item.kind != kind:
                continue
            snapshot = self.inspect(item.kind, item.key)
            if snapshot is None:
                raise LeaseLost(f"lease ref {ref} disappeared while listing")
            snapshots.append(snapshot)
        return sorted(snapshots, key=lambda value: value.record.resource["identity"])

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
        )

    def _utc_now(self) -> datetime:
        now = self._now()
        if now.tzinfo is None:
            raise ProtocolError("clock returned a naive datetime")
        return now.astimezone(timezone.utc).replace(microsecond=0)

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

    def _read_record(self, ref: str, expected_oid: str, item: Resource) -> LeaseRecord:
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
    ) -> LeaseRecord:
        """Validate the complete linear, empty-tree lease history."""
        empty_tree = self._git(
            [f"--git-dir={repo}", "hash-object", "-t", "tree", "--stdin"],
            input_text="",
        ).stdout.strip()
        chain: list[tuple[str, LeaseRecord]] = []
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
            chain.append((oid, parse_record(message, item)))
            if not parents:
                break
            oid = validate_oid(parents[0])

        previous: LeaseRecord | None = None
        for _oid, current in reversed(chain):
            self._validate_transition(previous, current)
            previous = current
        return chain[0][1]

    def _validate_transition(
        self,
        previous: LeaseRecord | None,
        current: LeaseRecord,
    ) -> None:
        """Validate one event against its exact parent record."""
        if previous is None:
            if current.event != "acquire":
                raise ProtocolError("the root lease commit must be an acquire event")
            return

        issued = datetime.strptime(
            current.issued_at, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        renewed = datetime.strptime(
            current.renewed_at, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        prior_renewed = datetime.strptime(
            previous.renewed_at, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)

        if current.event == "acquire":
            if previous.state != "released":
                raise ProtocolError("a non-root acquire must follow a release tombstone")
            if current.lease_id == previous.lease_id:
                raise ProtocolError("a new acquisition must use a new lease_id")
            if issued < prior_renewed:
                raise ProtocolError("acquisition time precedes its release parent")
            return

        if previous.state != "leased":
            raise ProtocolError(f"{current.event} must follow leased state")
        if current.event == "takeover":
            deadline = previous.expires() + timedelta(
                seconds=self.settings.clock_skew_seconds
            )
            if current.lease_id == previous.lease_id:
                raise ProtocolError("a takeover must use a new lease_id")
            if issued <= deadline:
                raise ProtocolError("takeover occurred before expiry plus clock skew")
            return

        if current.lease_id != previous.lease_id:
            raise ProtocolError(f"{current.event} changed the lease_id")
        if current.holder != previous.holder:
            raise ProtocolError(f"{current.event} changed the holder")
        if current.issued_at != previous.issued_at:
            raise ProtocolError(f"{current.event} changed issued_at")
        if renewed < prior_renewed:
            raise ProtocolError(f"{current.event} moved renewed_at backward")
        if current.event == "release" and current.context != previous.context:
            raise ProtocolError("release changed diagnostic context")

    def _transition(self, item: Resource, expected_oid: str, record: LeaseRecord) -> str:
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
                input_text=serialize_record(record) + "\n",
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
        env = os.environ.copy()
        for name in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_COMMON_DIR",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        ):
            env.pop(name, None)
        env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
        if extra_env:
            env.update(extra_env)
        call_args = args
        if self._auth_args and any(a in self._NETWORK_SUBCOMMANDS for a in args):
            call_args = [*self._auth_args, *args]
        try:
            result = subprocess.run(
                ["git", *call_args],
                input=input_text,
                capture_output=True,
                text=True,
                env=env,
                timeout=45,
                check=False,
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
