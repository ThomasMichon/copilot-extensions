"""Explicit-path inventory prototype; status data never grants control authority."""
from __future__ import annotations

import json
import math
import stat
from dataclasses import dataclass, fields, replace
from pathlib import Path

from dropin_registry import atomic_write_text
from single_instance_lease import OperationLock, OperationLockedError

_MAX_INTEGER = (1 << 63) - 1
_ERROR_CODES = frozenset({
    "unavailable", "partial", "limit", "invalid", "disconnected", "action_pending",
})


class InventoryError(ValueError):
    """A bounded schema/model refusal, without source payloads in diagnostics."""


class InventoryLimitError(InventoryError):
    """The explicit caller-supplied budget was exceeded."""


def _text(value, *, optional=False):
    if optional and value is None:
        return
    if type(value) is not str or not value or value.isspace():
        raise InventoryError("invalid_string")


def _integer(value, *, minimum=0):
    if type(value) is not int or not minimum <= value <= _MAX_INTEGER:
        raise InventoryError("invalid_integer")


def _time(value):
    if type(value) not in (int, float):
        raise InventoryError("invalid_time")
    try:
        result = float(value)
    except OverflowError:
        raise InventoryError("invalid_time") from None
    if not math.isfinite(result) or result < 0:
        raise InventoryError("invalid_time")
    return result


def _error(value):
    if value is not None and (type(value) is not str or value not in _ERROR_CODES):
        raise InventoryError("invalid_error_code")


@dataclass(frozen=True)
class Limits:
    max_bytes: int
    max_rows: int
    max_field_bytes: int

    def __post_init__(self):
        for value in (self.max_bytes, self.max_rows, self.max_field_bytes):
            _integer(value, minimum=1)


@dataclass(frozen=True)
class Policy:
    status_age_s: float
    inactive_age_s: float
    disconnected_grace_s: float

    def __post_init__(self):
        for field in fields(self):
            object.__setattr__(self, field.name, _time(getattr(self, field.name)))


@dataclass(frozen=True)
class SourceKey:
    installation: str
    namespace: str

    def __post_init__(self):
        _text(self.installation)
        _text(self.namespace)


@dataclass(frozen=True)
class VersionToken:
    source: SourceKey
    epoch: str
    revision: int

    def __post_init__(self):
        if type(self.source) is not SourceKey:
            raise InventoryError("invalid_source")
        _text(self.epoch)
        _integer(self.revision, minimum=1)


@dataclass(frozen=True)
class InventoryRow:
    name: str
    state: str
    observed_at: float
    last_seen: float | None
    producer_epoch: str | None
    sequence: int | None
    disconnected_at: float | None
    error_code: str | None

    def __post_init__(self):
        _text(self.name)
        _text(self.state)
        _text(self.producer_epoch, optional=True)
        _error(self.error_code)
        if self.sequence is not None:
            _integer(self.sequence)
        for name in ("observed_at", "last_seen", "disconnected_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _time(value))
            elif name == "observed_at":
                raise InventoryError("invalid_time")


@dataclass(frozen=True)
class AccountObservation:
    account: str | None
    source_generation: str | None
    observed_at: float
    complete: bool
    error_code: str | None
    rows: tuple[InventoryRow, ...]

    def __post_init__(self):
        _text(self.account, optional=True)
        _text(self.source_generation, optional=True)
        _error(self.error_code)
        if type(self.complete) is not bool or (self.complete and self.error_code is not None):
            raise InventoryError("invalid_completeness")
        if type(self.rows) is not tuple or any(type(row) is not InventoryRow for row in self.rows):
            raise InventoryError("mutable_or_invalid_rows")
        object.__setattr__(self, "observed_at", _time(self.observed_at))


@dataclass(frozen=True)
class Snapshot:
    schema_version: int
    source: SourceKey
    epoch: str
    revision: int
    accounts: tuple[AccountObservation, ...]

    def __post_init__(self):
        if type(self.schema_version) is not int:
            raise InventoryError("invalid_schema_type")
        if self.schema_version != 1:
            raise InventoryError("unsupported_schema")
        VersionToken(self.source, self.epoch, self.revision)
        if type(self.accounts) is not tuple or any(type(item) is not AccountObservation for item in self.accounts):
            raise InventoryError("mutable_or_invalid_accounts")

    @property
    def version(self):
        return VersionToken(self.source, self.epoch, self.revision)


@dataclass(frozen=True)
class ReadResult:
    status: str
    snapshot: Snapshot | None = None


@dataclass(frozen=True)
class CommitResult:
    status: str
    reason: str
    version: VersionToken | None = None


@dataclass(frozen=True)
class StatusFreshness:
    label: str
    age: float | None
    reasons: tuple[str, ...]
    reconcile: bool


_MODELS = (SourceKey, InventoryRow, AccountObservation, Snapshot)


def _account_key(account):
    return account.casefold() if account is not None else None


def _scalars(value):
    if type(value) in _MODELS:
        for field in fields(value):
            yield from _scalars(getattr(value, field.name))
    elif type(value) is tuple:
        for item in value:
            yield from _scalars(item)
    elif type(value) in (str, int, float, bool) or value is None:
        yield value
    else:
        raise InventoryError("invalid_model")


def _validate(snapshot, limits):
    if type(snapshot) is not Snapshot or type(limits) is not Limits:
        raise InventoryError("invalid_model")
    # Every encoded observation requires at least one byte, even if a caller
    # supplies an enormous row limit with a tiny byte limit.
    bound = min(limits.max_rows, limits.max_bytes)
    if len(snapshot.accounts) > bound or sum(len(item.rows) for item in snapshot.accounts) > bound:
        raise InventoryLimitError("row_limit")
    remaining = limits.max_bytes
    for value in _scalars(snapshot):
        if type(value) is str:
            if len(value) > min(limits.max_field_bytes, remaining):
                raise InventoryLimitError("string_limit")
            try:
                size = len(value.encode("utf-8"))
            except UnicodeError:
                raise InventoryError("invalid_utf8") from None
            if size > min(limits.max_field_bytes, remaining):
                raise InventoryLimitError("string_limit")
            remaining -= size
    accounts = set()
    for observation in snapshot.accounts:
        key = _account_key(observation.account)
        if key in accounts:
            raise InventoryError("duplicate_account")
        accounts.add(key)
        names = set()
        for row in observation.rows:
            if row.name in names:
                raise InventoryError("duplicate_venue")
            names.add(row.name)


def _tokens(value):
    if type(value) in _MODELS:
        yield "{"
        for index, field in enumerate(fields(value)):
            if index:
                yield ","
            yield json.dumps(field.name) + ":"
            yield from _tokens(getattr(value, field.name))
        yield "}"
    elif type(value) is tuple:
        yield "["
        for index, item in enumerate(value):
            if index:
                yield ","
            yield from _tokens(item)
        yield "]"
    else:
        yield json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _encode_text(snapshot, limits):
    _validate(snapshot, limits)
    chunks = []
    size = 0
    for chunk in _tokens(snapshot):
        size += len(chunk.encode("utf-8"))
        if size > limits.max_bytes:
            raise InventoryLimitError("byte_limit")
        chunks.append(chunk)
    return "".join(chunks)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InventoryError("duplicate_key")
        result[key] = value
    return result


def _json_integer(value):
    if len(value) > 20:
        raise InventoryError("invalid_integer")
    result = int(value)
    if abs(result) > _MAX_INTEGER:
        raise InventoryError("invalid_integer")
    return result


def _constant(_):
    raise InventoryError("nonfinite_number")


def _shape(value, model):
    if type(value) is not dict or set(value) != {field.name for field in fields(model)}:
        raise InventoryError("unknown_or_missing_field")
    return value


def _from_document(document, limits):
    root = dict(_shape(document, Snapshot))
    root["source"] = SourceKey(**_shape(root["source"], SourceKey))
    accounts = root["accounts"]
    if type(accounts) is not list:
        raise InventoryError("invalid_accounts")
    if len(accounts) > min(limits.max_rows, limits.max_bytes):
        raise InventoryLimitError("row_limit")
    converted = []
    total = 0
    for value in accounts:
        observation = dict(_shape(value, AccountObservation))
        rows = observation["rows"]
        if type(rows) is not list:
            raise InventoryError("invalid_rows")
        total += len(rows)
        if total > min(limits.max_rows, limits.max_bytes):
            raise InventoryLimitError("row_limit")
        observation["rows"] = tuple(InventoryRow(**_shape(row, InventoryRow)) for row in rows)
        converted.append(AccountObservation(**observation))
    root["accounts"] = tuple(converted)
    result = Snapshot(**root)
    _validate(result, limits)
    return result


def _decode(raw, limits):
    if len(raw) > limits.max_bytes:
        raise InventoryLimitError("byte_limit")
    document = json.loads(
        raw.decode("utf-8"), object_pairs_hook=_object,
        parse_int=_json_integer, parse_constant=_constant,
    )
    return _from_document(document, limits)


def _plain(info, kind):
    return kind(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _inspect_path(path):
    if not path.is_absolute() or not path.name:
        raise InventoryError("unsafe_path")
    if path.parent.resolve(strict=True) != path.parent:
        raise InventoryError("unsafe_parent")
    parents = []
    for parent in path.parents:
        info = parent.lstat()
        if not _plain(info, stat.S_ISDIR):
            raise InventoryError("unsafe_parent")
        parents.append((info.st_dev, info.st_ino))
    try:
        info = path.lstat()
    except FileNotFoundError:
        return tuple(parents), None
    if not _plain(info, stat.S_ISREG):
        raise InventoryError("unsafe_file")
    return tuple(parents), (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def read_inventory(path: Path, *, expected_source: SourceKey, limits: Limits) -> ReadResult:
    path = Path(path)
    if type(expected_source) is not SourceKey or type(limits) is not Limits:
        raise InventoryError("invalid_argument")
    try:
        before = _inspect_path(path)
        if before[1] is None:
            return ReadResult("absent" if _inspect_path(path) == before else "unknown")
        with path.open("rb") as stream:
            raw = stream.read(limits.max_bytes + 1)
        if _inspect_path(path) != before:
            return ReadResult("unknown")
        snapshot = _decode(raw, limits)
        if snapshot.source != expected_source:
            return ReadResult("wrong_source")
        return ReadResult("valid", snapshot)
    except InventoryLimitError:
        return ReadResult("too_large")
    except InventoryError as error:
        code = str(error)
        return ReadResult(
            "unsupported" if code == "unsupported_schema"
            else "unsafe_path" if code.startswith("unsafe_") else "corrupt"
        )
    except OSError:
        return ReadResult("unknown")
    except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
        return ReadResult("corrupt")


def commit_inventory(
    path: Path, candidate: Snapshot, *, expected_version: VersionToken | None, limits: Limits,
) -> CommitResult:
    """CAS the complete source/creation-epoch/revision token; never repair state."""
    path = Path(path)
    attempted = False
    try:
        before = _inspect_path(path)
        if expected_version is not None and type(expected_version) is not VersionToken:
            return CommitResult("refused", "invalid_version")
        _validate(candidate, limits)
        lock_path = path.with_name(path.name + ".lock")
        with OperationLock(lock_path) as custody:
            if _inspect_path(path) != before:
                return CommitResult("refused", "path_changed")
            current = read_inventory(path, expected_source=candidate.source, limits=limits)
            if current.status == "absent":
                if expected_version is not None or candidate.revision != 1:
                    return CommitResult("conflict", "initial_version")
            elif current.status == "valid":
                if current.snapshot.version != expected_version:
                    return CommitResult("conflict", "version_changed")
                if (
                    candidate.source != expected_version.source
                    or candidate.epoch != expected_version.epoch
                    or candidate.revision != expected_version.revision + 1
                ):
                    return CommitResult("refused", "candidate_version")
            else:
                return CommitResult("refused", current.status)
            content = _encode_text(candidate, limits)
            custody.assert_owned()
            if _inspect_path(path) != before:
                return CommitResult("refused", "path_changed")
            attempted = True
            atomic_write_text(path, content)
            custody.assert_owned()
            postcommit = _inspect_path(path)
            after = read_inventory(path, expected_source=candidate.source, limits=limits)
            if (
                postcommit[0] != before[0] or _inspect_path(path) != postcommit
                or after.status != "valid" or after.snapshot != candidate
            ):
                return CommitResult("unknown", "postcommit_unverified")
            custody.assert_owned()
            result = CommitResult("committed", "verified", candidate.version)
        return result
    except Exception as error:
        if attempted:
            return CommitResult("unknown", "commit_uncertain")
        if isinstance(error, OperationLockedError):
            return CommitResult("busy", "locked")
        return CommitResult("refused", "invalid_or_unavailable")


def apply_account_observation(
    snapshot: Snapshot, observation: AccountObservation, *, limits: Limits,
) -> Snapshot:
    """Merge positive evidence without deleting unseen or inactive records."""
    _validate(snapshot, limits)
    incoming = replace(snapshot, accounts=(observation,))
    _validate(incoming, limits)
    key = _account_key(observation.account)
    old = next((item for item in snapshot.accounts if _account_key(item.account) == key), None)
    if old is not None and (
        old.source_generation != observation.source_generation
        or observation.observed_at < old.observed_at
    ):
        raise InventoryError("observation_generation_or_order")
    rows = {row.name: row for row in old.rows} if old is not None else {}
    positive = set()
    unusable = False
    for row in observation.rows:
        if row.error_code is not None or row.state.casefold() == "unknown":
            unusable = True
            continue
        positive.add(row.name)
        previous = rows.get(row.name)
        if previous is None or row.observed_at >= previous.observed_at:
            rows[row.name] = row
    unseen = set(rows) - positive
    complete = observation.complete and not unseen and not unusable
    merged = replace(
        observation, rows=tuple(rows.values()), complete=complete,
        error_code=observation.error_code or ("partial" if unseen or unusable else None),
    )
    accounts = tuple(
        merged if _account_key(item.account) == key else item for item in snapshot.accounts
    )
    if old is None:
        accounts += (merged,)
    result = replace(snapshot, accounts=accounts)
    _validate(result, limits)
    return result


def classify_status(
    observation: AccountObservation, name: str, *, now_epoch: float,
    live_transport_epoch: str | None, policy: Policy,
) -> StatusFreshness:
    """Classify one account-qualified venue for display, never for an effect."""
    if type(observation) is not AccountObservation or type(policy) is not Policy:
        raise InventoryError("invalid_argument")
    _text(name)
    _text(live_transport_epoch, optional=True)
    now = _time(now_epoch)
    row = next((item for item in observation.rows if item.name == name), None)
    if row is None:
        return StatusFreshness("unknown", None, ("not_observed",), True)
    times = [observation.observed_at, row.observed_at]
    times += [value for value in (row.last_seen, row.disconnected_at) if value is not None]
    if any(value > now for value in times):
        return StatusFreshness("unknown", None, ("clock_uncertain",), True)
    age = max(now - observation.observed_at, now - row.observed_at)
    if not observation.complete or observation.error_code or row.error_code or row.state.casefold() == "unknown":
        return StatusFreshness("unknown", age, ("source_unknown",), True)
    maximum = policy.status_age_s if row.state.casefold() == "available" else policy.inactive_age_s
    if age > maximum:
        return StatusFreshness("stale", age, ("age_expired",), True)
    if row.producer_epoch is not None and live_transport_epoch != row.producer_epoch:
        if row.disconnected_at is not None and now - row.disconnected_at <= policy.disconnected_grace_s:
            return StatusFreshness("last_known", age, ("disconnected",), True)
        return StatusFreshness("unknown", age, ("transport_unverified",), True)
    return StatusFreshness("fresh", age, ("status_age_only",), False)
