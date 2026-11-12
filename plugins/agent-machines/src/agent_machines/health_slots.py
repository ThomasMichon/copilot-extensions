"""Explicit local observations and hourly storage, never probes or cloud I/O."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path

from .killswitch_mutation import mutation_lock

SCHEMA = "agent-machines.health.observation"
POLICY_SCHEMA = "agent-machines.health.store"
HOUR_MS = 3_600_000
SLOTS = 168
MAX_BYTES = 64 * 1024
MAX_INTEGER = 2**53 - 1
EVIDENCE = {
    "local_listener": "listener-check",
    "ssh_transport": "ssh-banner",
    "ssh_authenticated": "authenticated-session",
    "gateway_visibility": "gateway-owner",
    "authorization": "authorization-owner",
    "credential_readiness": "credential-owner",
    "logical_agents": "session-owner",
}
REASONS = {"none", "timeout", "unavailable", "refused", "invalid", "clock-skew", "not-observed"}


class HealthError(ValueError):
    """A bounded reason code, without input or filesystem content."""


def _integer(value, *, minimum=0) -> bool:
    return type(value) is int and minimum <= value <= MAX_INTEGER


def _identity(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is not None


def _fields(value, required: set[str]) -> None:
    if type(value) is not dict or value.keys() != required:
        raise HealthError("invalid-fields")


def validate_policy(value: dict) -> dict:
    version = value.get("version") if type(value) is dict else None
    _fields(value, {"schema", "version", "publisher", "continuity", *({"collection"} if version == 2 else set())})
    if (
        value["schema"] != POLICY_SCHEMA or type(version) is not int or version not in {1, 2}
        or not _identity(value["publisher"]) or not _identity(value["continuity"])
    ):
        raise HealthError("invalid-store-policy")
    if version == 2:
        from .health_hourly import validate_collection

        validate_collection(value["collection"])
    return value


def _codec(policy: dict):
    if policy["version"] == 2:
        from .health_hourly import MAX_BYTES as maximum, validate_record as validator

        return validator, maximum
    return validate_record, MAX_BYTES


def validate_record(value: dict) -> dict:
    _fields(value, {"schema", "version", "publisher", "continuity", "hour", "revision", "parent",
                    "observed_at_ms", "domains"})
    if (
        value["schema"] != SCHEMA or type(value["version"]) is not int or value["version"] != 1
        or not _identity(value["publisher"]) or not _identity(value["continuity"])
        or not _integer(value["observed_at_ms"]) or not _integer(value["hour"])
        or value["hour"] != value["observed_at_ms"] // HOUR_MS
        or not _integer(value["revision"], minimum=1)
        or (value["parent"] is not None and (
            not _integer(value["parent"], minimum=1) or value["parent"] >= value["revision"]
        ))
        or type(value["domains"]) is not dict or not value["domains"].keys() <= EVIDENCE.keys()
    ):
        raise HealthError("invalid-observation")
    for domain, observation in value["domains"].items():
        required = {"state", "evidence", "reason", "observed_at_ms"}
        if domain == "logical_agents":
            required.add("count")
        if domain == "ssh_authenticated":
            required.add("direction")
        _fields(observation, required)
        state = observation["state"]
        if (
            not isinstance(state, str) or state not in {"ok", "failed", "unknown", "unsupported"}
            or not isinstance(observation["reason"], str) or observation["reason"] not in REASONS
        ):
            raise HealthError("invalid-domain-observation")
        stamp = observation["observed_at_ms"]
        known = state in {"ok", "failed"}
        if known:
            if (
                not _integer(stamp) or stamp > value["observed_at_ms"]
                or observation["evidence"] != EVIDENCE[domain]
                or (state == "ok" and observation["reason"] != "none")
            ):
                raise HealthError("invalid-domain-evidence")
        elif stamp is not None or observation["evidence"] != "not-observed":
            raise HealthError("unknown-domain-evidence")
        if domain == "logical_agents":
            count = observation["count"]
            if (state == "ok" and (not _integer(count) or count > 65_535)) or (state != "ok" and count is not None):
                raise HealthError("invalid-logical-agent-count")
        if domain == "ssh_authenticated" and (
            observation["direction"] not in ("inbound", "outbound") if known
            else observation["direction"] is not None
        ):
            raise HealthError("invalid-ssh-direction")
    return value


def _plain(path: Path, *, directory=False, optional=False) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise HealthError("absolute-plain-path-required")
    for parent in (*reversed(path.parents), path):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            if optional and parent == path:
                return path
            raise HealthError("path-unavailable") from None
        is_directory = parent != path or directory
        if (
            parent.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(info.st_mode) if is_directory else stat.S_ISREG(info.st_mode))
            or (not is_directory and info.st_nlink != 1)
        ):
            raise HealthError("exclusive-plain-path-required")
    if (path if directory else path.parent).resolve(strict=True) != (path if directory else path.parent):
        raise HealthError("canonical-directory-required")
    return path


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise HealthError("duplicate-key")
        value[key] = item
    return value


def _nonfinite(_value):
    raise HealthError("nonfinite-value")


def _number(value):
    if len(value.lstrip("-")) > 16:
        raise HealthError("integer-out-of-range")
    return int(value)


def _float(_value):
    raise HealthError("floating-point-value")


def _hourly_number(value):
    if len(value.lstrip("-")) > 19:
        raise HealthError("integer-out-of-range")
    return int(value)


def _read(path: Path, validator, *, optional=False, maximum=MAX_BYTES):
    path = _plain(path, optional=optional)
    try:
        before = path.lstat()
    except FileNotFoundError:
        if optional:
            return None, None
        raise HealthError("path-unavailable") from None
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise HealthError("input-changed")
            raw = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
        _plain(path)
        final = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or (opened.st_dev, opened.st_ino) != (final.st_dev, final.st_ino)
            or (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
            or (after.st_size, after.st_mtime_ns) != (final.st_size, final.st_mtime_ns)
            or len(raw) > maximum
        ):
            raise HealthError("input-changed-or-oversize")
        try:
            value = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite,
                parse_int=_number if maximum == MAX_BYTES else _hourly_number, parse_float=_float,
            )
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            raise HealthError("invalid-json") from None
        return validator(value), hashlib.sha256(raw).hexdigest()
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _owner(record: dict, policy: dict) -> bool:
    return all(record[key] == policy[key] for key in ("publisher", "continuity"))


def _plan(store: Path, source: Path | dict, now_ms: int):
    if not _integer(now_ms):
        raise HealthError("invalid-observation-time")
    store = _plain(store, directory=True)
    policy, policy_hash = _read(store / "policy.json", validate_policy)
    validator, maximum = _codec(policy)
    if isinstance(source, Path):
        candidate, source_hash = _read(source, validator, maximum=maximum)
    else:
        raw = json.dumps(source, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > maximum:
            raise HealthError("input-oversize")
        candidate = validator(json.loads(raw))
        source_hash = hashlib.sha256(raw).hexdigest()
    target = store / f"{candidate['hour'] % SLOTS:03d}.json"
    if isinstance(source, Path):
        for protected in (store / "policy.json", store / "health.lock", store / "collection.lock", target):
            if source == protected or protected.exists() and source.samefile(protected):
                raise HealthError("input-overlaps-output")
    _plain(store / "health.lock", optional=True)
    previous, previous_hash = _read(target, validator, optional=True, maximum=maximum)
    if policy["version"] == 2:
        from .health_hourly import validate_replacement

        validate_replacement(candidate, previous, policy)
    status = "pending"
    if not _owner(candidate, policy) or previous is not None and not _owner(previous, policy):
        status = "owner-mismatch"
    elif candidate["observed_at_ms"] > now_ms:
        status = "future"
    elif candidate["hour"] < now_ms // HOUR_MS - (SLOTS - 1):
        status = "expired"
    elif previous is not None and previous["hour"] > candidate["hour"]:
        status = "newer-slot"
    elif previous is not None and previous["hour"] == candidate["hour"]:
        if previous == candidate:
            status = "unchanged"
        elif candidate["revision"] <= previous["revision"]:
            status = "revision-conflict"
        elif candidate["parent"] != previous["revision"]:
            status = "parent-gap"
        elif candidate["observed_at_ms"] < previous["observed_at_ms"]:
            status = "time-regression"
    elif candidate["revision"] != 1 or candidate["parent"] is not None:
        status = "untracked-parent"
    result = {
        "schema": "agent-machines.health.transaction", "version": 1,
        "status": status, "eligible": status in {"pending", "unchanged"},
        "hour": candidate["hour"], "slot": candidate["hour"] % SLOTS,
        "applied": False, "changed": False, "probe_execution": "not-performed",
        "carrier_publication": "not-requested", "observation_qualification": "input-supplied-not-verified",
    }
    info = store.stat()
    result["preview_token"] = _digest({
        "store": str(store), "identity": [info.st_dev, info.st_ino],
        "source": str(source) if isinstance(source, Path) else "collected-in-memory",
        "policy": policy_hash, "input": source_hash, "previous": previous_hash,
        "now_hour": now_ms // HOUR_MS, "result": result,
    })
    return result, candidate, target


def _write(path: Path, value: dict) -> None:
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    maximum = MAX_BYTES
    if value.get("schema") == "agent-machines.health.hourly":
        from .health_hourly import MAX_BYTES as maximum
    if len(raw) > maximum:
        raise HealthError("output-oversize")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".tmp.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _plain(path, optional=True)
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def record(store: Path, source: Path, *, now_ms: int, apply=False, expected="") -> dict:
    if apply and re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise HealthError("preview-token-required")
    if expected and not apply:
        raise HealthError("confirmation-requires-apply")
    result, _candidate, _target = _plan(store, source, now_ms)
    if not apply:
        return result
    return _commit(store, source, now_ms=now_ms, expected=expected, result=result)


def record_collected(store: Path, candidate: dict, *, now_ms: int, guard, deadline: float) -> dict:
    """Use the same revision preview and writer under the caller's policy/budget."""
    result, frozen, _target = _plan(store, candidate, now_ms)
    return _commit(
        store, frozen, now_ms=now_ms, expected=result["preview_token"], result=result,
        guard=guard, timeout=lambda: min(5.0, max(0.0, deadline - time.monotonic())),
    )


def _commit(store, source, *, now_ms, expected, result, guard=None, timeout=None):
    if not result["eligible"]:
        raise HealthError(result["status"])
    write_started = False
    try:
        if guard is not None:
            guard()
        lock_fields = {} if timeout is None else {"timeout": timeout()}
        with mutation_lock(store / "health", **lock_fields):
            result, candidate, target = _plan(store, source, now_ms)
            if not hmac.compare_digest(expected, result["preview_token"]):
                raise HealthError("preview-changed")
            if not result["eligible"]:
                raise HealthError(result["status"])
            if guard is not None:
                guard()
            if result["status"] != "unchanged":
                write_started = True
                _write(target, candidate)
                result["changed"] = True
            else:
                _sync_directory(store)
        return {**result, "status": "recorded", "applied": True}
    except OSError:
        if write_started:
            return {**result, "status": "unknown", "eligible": False, "applied": None, "changed": None}
        raise


def _sample(record: dict, now_ms: int) -> dict:
    domains = {}
    for domain in EVIDENCE:
        observed = record["domains"].get(domain)
        if observed is None:
            observed = {
                "state": "unknown", "reason": "not-observed",
                "observed_at_ms": None, "evidence": "not-observed",
            }
            if domain == "logical_agents":
                observed["count"] = None
            if domain == "ssh_authenticated":
                observed["direction"] = None
        stamp = observed["observed_at_ms"]
        age = None if stamp is None else now_ms - stamp
        domains[domain] = {
            **observed, "observed_state": observed["state"],
            "state": "stale" if age is not None and age >= HOUR_MS else observed["state"], "age_ms": age,
        }
    return {
        "observed_at_ms": record["observed_at_ms"], "revision": record["revision"],
        "sample_age_ms": now_ms - record["observed_at_ms"], "domains": domains,
    }


def inspect(store: Path, *, now_ms: int) -> dict:
    if not _integer(now_ms) or now_ms // HOUR_MS < SLOTS - 1:
        raise HealthError("invalid-observation-time")
    store = _plain(store, directory=True)
    policy, policy_hash = _read(store / "policy.json", validate_policy)
    validator, maximum = _codec(policy)
    identity = store.stat()
    hours = []
    for hour in range(now_ms // HOUR_MS - SLOTS + 1, now_ms // HOUR_MS + 1):
        item = {"hour": hour, "state": "missing"}
        try:
            observed, _hash = _read(
                store / f"{hour % SLOTS:03d}.json", validator, optional=True, maximum=maximum,
            )
            if observed is not None:
                if not _owner(observed, policy):
                    item["state"] = "owner-mismatch"
                elif observed["hour"] < hour:
                    item["state"] = "expired"
                elif observed["hour"] > hour or observed["observed_at_ms"] > now_ms:
                    item["state"] = "future"
                else:
                    if policy["version"] == 2:
                        from .health_hourly import sample

                        projection = sample(observed, now_ms)
                    else:
                        projection = _sample(observed, now_ms)
                    item.update(state="observed", sample=projection)
        except (HealthError, OSError):
            item["state"] = "invalid"
        hours.append(item)
    _policy, current_hash = _read(store / "policy.json", validate_policy)
    current = store.stat()
    if current_hash != policy_hash or (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
        raise HealthError("store-policy-changed")
    return {
        "schema": "agent-machines.health.view", "version": 1, "generated_at_ms": now_ms,
        "publisher": policy["publisher"], "continuity": policy["continuity"], "hours": hours,
        "observed_hours": sum(item["state"] == "observed" for item in hours),
        "view": "last-known", "probe_execution": "not-performed",
        "carrier_receipt": "not-observed", "observation_qualification": "input-supplied-not-verified",
    }
