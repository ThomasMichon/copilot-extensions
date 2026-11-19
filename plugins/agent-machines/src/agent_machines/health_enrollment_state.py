"""Platform-isolated local consent and native-pair recovery records."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Literal, TypedDict, cast

from .health_jobs import LinuxDefinition
from .health_slots import HealthError

SCHEMA = "agent-machines.health.enrollment"


class LinuxDesired(TypedDict):
    store: str
    policy_token: str
    account_uid: int
    enabled: bool


class LinuxRecord(TypedDict):
    desired: LinuxDesired
    version: str
    identity: str
    definition: LinuxDefinition


class LinuxPending(TypedDict):
    before: LinuxRecord | None
    candidate: LinuxRecord | None
    token: str
    references: list[str]
    intent: Literal["replace", "disable", "remove"]
    native_unsettled: bool


class LinuxState(TypedDict):
    schema: str
    version: Literal[2]
    platform: Literal["linux"]
    desired: LinuxDesired | None
    confirmed: LinuxRecord | None
    pending: LinuxPending | None


def _mapping(value: object, fields: set[str], reason: str) -> dict[str, object]:
    if type(value) is not dict or value.keys() != fields:
        raise HealthError(reason)
    return cast(dict[str, object], value)


def _match(value: object, pattern: str) -> bool:
    return type(value) is str and re.fullmatch(pattern, value) is not None


def _version(value: object) -> bool:
    return (
        type(value) is str and bool(value) and value not in {".", "..", "dev"}
        and len(value) <= 128 and all(ord(char) >= 32 for char in value)
        and PurePosixPath(value).name == value and PureWindowsPath(value).name == value
        and ":" not in value
    )


def linux_desired(value: object, *, account_uid: int) -> LinuxDesired:
    wanted = _mapping(
        value, {"store", "policy_token", "account_uid", "enabled"},
        "invalid-local-job-enrollment",
    )
    uid = wanted["account_uid"]
    if (
        type(uid) is not int or not 0 <= uid < 2**32 - 1
        or type(account_uid) is not int or uid != account_uid
    ):
        raise HealthError("local-job-account-mismatch")
    store = wanted["store"]
    if (
        type(store) is not str or not PurePosixPath(store).is_absolute()
        or ".." in PurePosixPath(store).parts
        or any(ord(char) < 32 for char in store)
        or not _match(wanted["policy_token"], r"[a-f0-9]{64}")
        or type(wanted["enabled"]) is not bool
    ):
        raise HealthError("invalid-local-job-enrollment")
    return cast(LinuxDesired, wanted)


def linux_definition(value: object, desired: LinuxDesired) -> LinuxDefinition:
    definition = _mapping(value, {
        "schema", "version", "platform", "owner", "account_uid", "enabled", "artifacts",
    }, "invalid-native-job-definition")
    if (
        definition["schema"] != "agent-machines.health.linux-definition"
        or type(definition["version"]) is not int or definition["version"] != 1
        or definition["platform"] != "linux"
        or not _match(definition["owner"], r"urn:copilot-extensions:health-job:[a-f0-9]{64}")
        or type(definition["account_uid"]) is not int
        or definition["account_uid"] != desired["account_uid"]
        or type(definition["enabled"]) is not bool or definition["enabled"] != desired["enabled"]
    ):
        raise HealthError("invalid-native-job-definition")
    owner = cast(str, definition["owner"])
    name = "ce-health-" + owner.rsplit(":", 1)[1][:32]
    artifacts = _mapping(
        definition["artifacts"], {name + ".service", name + ".timer"},
        "invalid-native-job-definition",
    )
    for text in artifacts.values():
        if (
            type(text) is not str or not text or len(text.encode("utf-8")) > 16000
            or not text.startswith("[Unit]\nDescription=" + owner + "\n")
            or "\0" in text or "\r" in text
        ):
            raise HealthError("invalid-native-job-definition")
    return cast(LinuxDefinition, definition)


def linux_record(value: object, *, account_uid: int) -> LinuxRecord:
    record = _mapping(value, {
        "desired", "version", "identity", "definition",
    }, "invalid-local-job-state")
    if not _version(record["version"]) or not _match(record["identity"], r"[a-f0-9]{32}:[a-f0-9]{64}"):
        raise HealthError("invalid-local-job-state")
    wanted = linux_desired(record["desired"], account_uid=account_uid)
    linux_definition(record["definition"], wanted)
    return cast(LinuxRecord, record)


def validate_linux(value: object, *, account_uid: int) -> LinuxState:
    if type(value) is not dict:
        raise HealthError("invalid-local-job-state")
    if value.get("platform") != "linux":
        raise HealthError("local-job-platform-mismatch")
    state = _mapping(value, {
        "schema", "version", "platform", "desired", "confirmed", "pending",
    }, "invalid-local-job-state")
    if state["schema"] != SCHEMA or type(state["version"]) is not int or state["version"] != 2:
        raise HealthError("invalid-local-job-state")
    wanted = state["desired"]
    if wanted is not None:
        linux_desired(wanted, account_uid=account_uid)
    if state["confirmed"] is not None:
        confirmed = linux_record(state["confirmed"], account_uid=account_uid)
        if confirmed["desired"] != wanted:
            raise HealthError("invalid-local-job-state")
    pending = state["pending"]
    if pending is not None:
        recovery = _mapping(pending, {
            "before", "candidate", "token", "references", "intent", "native_unsettled",
        }, "invalid-local-job-recovery")
        if (
            recovery["intent"] not in ("replace", "disable", "remove")
            or type(recovery["native_unsettled"]) is not bool
            or not _match(recovery["token"], r"[a-f0-9]{64}")
        ):
            raise HealthError("invalid-local-job-recovery")
        references = recovery["references"]
        if (
            type(references) is not list or len(references) > 8
            or not all(_version(version) for version in references)
            or len(set(references)) != len(references)
        ):
            raise HealthError("invalid-local-job-recovery")
        for field in ("before", "candidate"):
            if recovery[field] is not None:
                linux_record(recovery[field], account_uid=account_uid)
    return cast(LinuxState, state)


def empty_linux() -> LinuxState:
    return {
        "schema": SCHEMA, "version": 2, "platform": "linux",
        "desired": None, "confirmed": None, "pending": None,
    }
