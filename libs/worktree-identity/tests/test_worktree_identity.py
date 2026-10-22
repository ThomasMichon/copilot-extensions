"""The wire codec has no transport/configuration dependency or inferred authority."""

from __future__ import annotations

from copy import deepcopy

import pytest

from worktree_identity import (
    WorktreeMachineIdentity,
    WorktreeMachineIdentityError,
    execution_machine_from_fields,
    parse_machine_identity,
    projected_machine_fields,
)


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_tuple_keeps_physical_and_execution_identity_separate(environment):
    identity = WorktreeMachineIdentity("host-wsl", environment, "custom-owner")
    fields = identity.to_record_fields()
    before = deepcopy(fields)
    assert fields == {
        "machine_identity_version": 1, "machine": "host-wsl",
        "environment": environment, "execution_machine": "custom-owner",
        "platform": environment,
    }
    assert parse_machine_identity(fields) == identity
    assert execution_machine_from_fields(fields) == "custom-owner"
    assert projected_machine_fields(fields) == fields
    assert fields == before


@pytest.mark.parametrize("machine", ["host-wsl-wsl", "", None, 23])
def test_legacy_absence_does_not_validate_infer_or_upgrade(machine):
    fields = {
        "machine": machine, "platform": "historical-platform",
        "environment": "untrusted", "execution_machine": "ignored",
    }
    before = deepcopy(fields)
    assert parse_machine_identity(fields) is None
    assert execution_machine_from_fields(fields) == (machine if isinstance(machine, str) else None)
    assert projected_machine_fields(fields) == {
        "machine": machine, "platform": "historical-platform",
    }
    assert fields == before


@pytest.mark.parametrize("reader", [
    parse_machine_identity, execution_machine_from_fields, projected_machine_fields,
])
@pytest.mark.parametrize("version", [None, False, True, 0, 2, 1.0, "1"])
def test_explicit_invalid_version_never_becomes_legacy(reader, version):
    fields = WorktreeMachineIdentity("host", "wsl", "guest").to_record_fields()
    fields["machine_identity_version"] = version
    with pytest.raises(WorktreeMachineIdentityError, match="unsupported machine identity version"):
        reader(fields)


@pytest.mark.parametrize("field", ["machine", "environment", "execution_machine", "platform"])
@pytest.mark.parametrize("value", [None, 7, "", " padded", "padded ", "x\r", "x\n", "x\0"])
def test_invalid_dimensions_refuse(field, value):
    fields = WorktreeMachineIdentity("host", "wsl", "guest").to_record_fields()
    fields[field] = value
    for reader in (
        parse_machine_identity, execution_machine_from_fields, projected_machine_fields,
    ):
        with pytest.raises(WorktreeMachineIdentityError, match="nonempty clean string"):
            reader(fields)


@pytest.mark.parametrize("field", ["machine", "environment", "execution_machine", "platform"])
def test_missing_dimensions_refuse(field):
    fields = WorktreeMachineIdentity("host", "wsl", "guest").to_record_fields()
    del fields[field]
    with pytest.raises(WorktreeMachineIdentityError, match="nonempty clean string"):
        parse_machine_identity(fields)


@pytest.mark.parametrize("environment", ["win", "Windows", "WSL", "LINUX"])
def test_environment_aliases_are_not_canonical_wire_values(environment):
    with pytest.raises(WorktreeMachineIdentityError, match="must use a canonical environment"):
        WorktreeMachineIdentity("host", environment, "guest")


@pytest.mark.parametrize("environment", ["macos", "darwin", "future"])
def test_unknown_environments_preserve_error_and_cause(environment):
    with pytest.raises(WorktreeMachineIdentityError, match="environment is unsupported") as caught:
        WorktreeMachineIdentity("host", environment, "guest")
    assert type(caught.value.__cause__) is ValueError
    assert str(caught.value.__cause__) == f"Unknown environment: {environment}"


def test_conflicting_platform_refuses():
    fields = WorktreeMachineIdentity("host", "wsl", "guest").to_record_fields()
    fields["platform"] = "windows"
    with pytest.raises(WorktreeMachineIdentityError, match="environment conflicts with platform"):
        parse_machine_identity(fields)
