"""Tuple-first metadata is explicit; historical execution labels remain opaque."""

from __future__ import annotations

from copy import deepcopy

import pytest

from agent_worktrees.worktree_machine_identity import (
    WorktreeMachineIdentity, WorktreeMachineIdentityError, parse_machine_identity,
)
from agent_worktrees.worktree_naming import prospective_session_worktree_id


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_tuple_roundtrip_keeps_physical_and_execution_roles(environment: str) -> None:
    identity = WorktreeMachineIdentity("physical-host", environment, "custom-execution")
    fields = identity.to_record_fields()
    assert fields == {
        "machine_identity_version": 1, "machine": "physical-host",
        "environment": environment, "execution_machine": "custom-execution",
        "platform": environment,
    }
    assert parse_machine_identity(fields) == identity


@pytest.mark.parametrize("machine", ["host-wsl", "host-wsl-wsl"])
def test_historical_metadata_is_not_inferred_or_upgraded(machine: str) -> None:
    fields = {"machine": machine, "platform": "wsl", "environment": "untrusted-extra"}
    before = deepcopy(fields)
    assert parse_machine_identity(fields) is None
    assert fields == before


@pytest.mark.parametrize("version", [None, True, "1", 0, 2])
def test_explicit_unknown_or_invalid_version_never_falls_back(version: object) -> None:
    fields = WorktreeMachineIdentity("host", "wsl", "guest-space").to_record_fields()
    fields["machine_identity_version"] = version
    with pytest.raises(WorktreeMachineIdentityError, match="version"):
        parse_machine_identity(fields)


@pytest.mark.parametrize(("field", "value"), [
    ("machine", ""), ("execution_machine", " guest"), ("execution_machine", "guest\n"),
    ("environment", "win"), ("environment", "future"), ("platform", "linux"),
])
def test_incomplete_or_conflicting_explicit_tuple_refuses(field: str, value: str) -> None:
    fields = WorktreeMachineIdentity("host", "wsl", "guest-space").to_record_fields()
    fields[field] = value
    with pytest.raises(WorktreeMachineIdentityError):
        parse_machine_identity(fields)


@pytest.mark.parametrize("field", ["machine", "environment", "execution_machine", "platform"])
def test_partial_explicit_tuple_refuses(field: str) -> None:
    fields = WorktreeMachineIdentity("host", "wsl", "guest-space").to_record_fields()
    del fields[field]
    with pytest.raises(WorktreeMachineIdentityError):
        parse_machine_identity(fields)


@pytest.mark.parametrize(("machine", "environment", "prefix"), [
    ("host", "wsl", "host-wsl"),
    ("host-wsl", "wsl", "host-wsl-wsl"),
    ("host-win", "windows", "host-win-win"),
    ("host-linux", "linux", "host-linux-linux"),
])
def test_prospective_name_preserves_the_entire_physical_name(
    machine: str, environment: str, prefix: str,
) -> None:
    identity = WorktreeMachineIdentity(machine, environment, "independent-owner")
    assert prospective_session_worktree_id(identity, "20261020-100000", "abcd") == (
        f"{prefix}-20261020-100000-abcd"
    )
