"""Lossless optional owner provenance without inventing canonical session references."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_logger.sync.worktree_state import WorktreeStateError, canonical_bytes
from agent_logger.sync.worktree_state_archive import (
    collect_worktree_state,
    readback_worktree_state,
)
from agent_logger.sync.worktree_state_contributors import ContributorReader, contributor_bytes

from .test_worktree_state import IDENTITY, Packet, collect, fake_owner
from .test_worktree_state import packet as packet

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX collection adapter")


def projection(source: Path) -> dict[str, Any]:
    return {
        "schema": "efforts.agenda-export-contributors",
        "version": 1,
        "completeness": "complete",
        "manifest_sha256": hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest(),
        "source": {key: IDENTITY.as_dict()[key] for key in ("worktree", "store_id", "revision")},
        "contributors": [
            {
                "actor": None,
                "session": None,
                "qualification": "unknown",
                "first_revision": 1,
                "last_revision": 7,
                "operations": 2,
            },
            {
                "actor": "writer/\u975eASCII",
                "session": "../arbitrary session",
                "qualification": "caller-asserted",
                "first_revision": 2,
                "last_revision": 6,
                "operations": 4,
            },
            {
                "actor": None,
                "session": "same-session",
                "qualification": "caller-asserted",
                "first_revision": 3,
                "last_revision": 3,
                "operations": 1,
            },
        ],
    }


def reader(value: dict[str, Any]) -> ContributorReader:
    def read(directory: Path, **selection: Any) -> dict[str, Any]:
        fake_owner(directory, **selection)
        return deepcopy(value)

    return read


def test_complete_nullable_provenance_round_trips_without_canonical_session_coercion(
    packet: Packet,
) -> None:
    cfg, target, plan, source = packet
    expected = projection(source)
    owner = reader(expected)
    receipt = collect_worktree_state(
        cfg,
        target,
        plan,
        source,
        IDENTITY,
        (),
        fake_owner,
        contributors=owner,
    )
    revision = target._root() / IDENTITY.relative_directory
    retained = (revision / "contributors.json").read_bytes()
    assert json.loads(retained) == expected
    assert not list((revision / "references").glob("*.json"))
    assert receipt.as_dict()["version"] == 2
    assert receipt.as_dict()["contributors_sha256"] == hashlib.sha256(retained).hexdigest()
    assert receipt.as_dict()["remote_archive_acknowledged"] is False
    assert receipt.as_dict()["reclamation_authorized"] is False
    assert (
        collect_worktree_state(
            cfg,
            target,
            plan,
            source,
            IDENTITY,
            ("session-1",),
            fake_owner,
            contributors=owner,
        )
        == receipt
    )
    assert len(list((revision / "references").glob("*.json"))) == 1
    assert (
        readback_worktree_state(
            cfg,
            target,
            plan,
            IDENTITY,
            fake_owner,
            contributors=owner,
        )
        == receipt
    )
    with pytest.raises(WorktreeStateError):
        readback_worktree_state(cfg, target, plan, IDENTITY, fake_owner)


@pytest.mark.parametrize(
    "kind",
    [
        "manifest",
        "source",
        "version",
        "qualification",
        "duplicate",
        "count",
        "order",
        "boolean",
        "extra",
        "too-long",
        "surrogate",
        "empty",
        "over-count",
    ],
)
def test_incomplete_or_misbound_provenance_refuses_before_destination_creation(
    packet: Packet,
    kind: str,
) -> None:
    cfg, target, plan, source = packet
    value = projection(source)
    entry = value["contributors"][0]
    if kind == "manifest":
        value["manifest_sha256"] = "0" * 64
    elif kind == "source":
        value["source"]["revision"] += 1
    elif kind == "version":
        value["version"] = True
    elif kind == "qualification":
        entry["qualification"] = "authenticated"
    elif kind == "duplicate":
        value["contributors"].append(deepcopy(entry))
    elif kind == "count":
        entry["operations"] -= 1
    elif kind == "order":
        value["contributors"].reverse()
    elif kind == "boolean":
        entry["first_revision"] = True
    elif kind == "extra":
        entry["unsupported_evidence"] = "must not silently disappear"
    elif kind == "too-long":
        entry["actor"] = "x" * 1025
    elif kind == "empty":
        entry["actor"] = ""
    elif kind == "over-count":
        value["contributors"] = [deepcopy(entry)] * 4097
    else:
        entry["actor"] = "\ud800"
    with pytest.raises(ValueError):
        collect_worktree_state(
            cfg,
            target,
            plan,
            source,
            IDENTITY,
            (),
            fake_owner,
            contributors=reader(value),
        )
    assert not target._root().exists()


def test_complete_projection_byte_budget_refuses_instead_of_truncating(packet: Packet) -> None:
    _, target, _, source = packet
    value = projection(source)
    identity = replace(IDENTITY, revision=256)
    value["source"]["revision"] = identity.revision
    value["contributors"] = [
        {
            "actor": "\U0001f600" * 255 + str(index),
            "session": "\U0001f600" * 256,
            "qualification": "caller-asserted",
            "first_revision": index + 1,
            "last_revision": index + 1,
            "operations": 1,
        }
        for index in range(256)
    ]

    def owner(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return value

    with pytest.raises(WorktreeStateError, match="byte budget"):
        contributor_bytes(source, identity, (source / "manifest.json").read_bytes(), owner)
    assert not target._root().exists()


def test_changed_destination_projection_never_issues_receipt(packet: Packet) -> None:
    cfg, target, plan, source = packet
    value = projection(source)

    def changed(directory: Path, **selection: Any) -> dict[str, Any]:
        actual = reader(value)(directory, **selection)
        if directory != source:
            actual["contributors"][1]["actor"] = "different owner metadata"
        return actual

    with pytest.raises(WorktreeStateError, match="differs from source"):
        collect_worktree_state(
            cfg,
            target,
            plan,
            source,
            IDENTITY,
            (),
            fake_owner,
            contributors=changed,
        )
    assert not list(target._root().rglob("receipt.json"))


def test_projection_above_old_metadata_limit_is_lossless(packet: Packet) -> None:
    cfg, target, plan, source = packet
    value = projection(source)
    value["contributors"] = [
        {
            "actor": "\U0001f600" * 255 + str(index),
            "session": "\U0001f600" * 256,
            "qualification": "caller-asserted",
            "first_revision": index + 1,
            "last_revision": index + 1,
            "operations": 1,
        }
        for index in range(7)
    ]
    assert len(canonical_bytes(value)) > 32768
    receipt = collect_worktree_state(
        cfg,
        target,
        plan,
        source,
        IDENTITY,
        (),
        fake_owner,
        contributors=reader(value),
    )
    retained = target._root() / IDENTITY.relative_directory / "contributors.json"
    assert json.loads(retained.read_bytes()) == value
    assert (
        readback_worktree_state(
            cfg,
            target,
            plan,
            IDENTITY,
            fake_owner,
            contributors=reader(value),
        )
        == receipt
    )


@pytest.mark.parametrize("kind", ["changed", "missing", "oversized"])
def test_fresh_readback_checks_actual_retained_provenance(packet: Packet, kind: str) -> None:
    cfg, target, plan, source = packet
    value = projection(source)
    owner = reader(value)
    collect_worktree_state(
        cfg,
        target,
        plan,
        source,
        IDENTITY,
        (),
        fake_owner,
        contributors=owner,
    )
    path = target._root() / IDENTITY.relative_directory / "contributors.json"
    if kind == "missing":
        path.rename(path.with_suffix(".retained"))
    elif kind == "oversized":
        path.write_bytes(b" " * ((1 << 20) + 1))
    else:
        value["contributors"][1]["actor"] = "changed"
        path.write_bytes(canonical_bytes(value))
    with pytest.raises((WorktreeStateError, OSError)):
        readback_worktree_state(cfg, target, plan, IDENTITY, fake_owner, contributors=owner)
    with pytest.raises(WorktreeStateError):
        collect_worktree_state(
            cfg,
            target,
            plan,
            source,
            IDENTITY,
            (),
            fake_owner,
            contributors=owner,
        )


@pytest.mark.parametrize("old_first", [False, True])
def test_v1_receipt_never_counts_as_rich_provenance_ack(packet: Packet, old_first: bool) -> None:
    cfg, target, plan, source = packet
    owner = reader(projection(source))
    if old_first:
        receipt = collect(packet)
        assert receipt.as_dict()["version"] == 1
    else:
        collect_worktree_state(
            cfg,
            target,
            plan,
            source,
            IDENTITY,
            (),
            fake_owner,
            contributors=owner,
        )
    with pytest.raises(WorktreeStateError, match="conflicts"):
        collect_worktree_state(
            cfg,
            target,
            plan,
            source,
            IDENTITY,
            ("session-1",),
            fake_owner,
            contributors=owner if old_first else None,
        )


def test_privacy_refusal_precedes_provenance_read(packet: Packet) -> None:
    cfg, target, plan, source = packet
    cfg._data["sync"]["redact_secrets"] = True

    def forbidden(*args: Any, **kwargs: Any) -> dict[str, Any]:
        pytest.fail("unadmitted collection reached contributor reader")

    with pytest.raises(WorktreeStateError, match="privacy"):
        collect_worktree_state(
            cfg,
            target,
            plan,
            source,
            IDENTITY,
            (),
            fake_owner,
            contributors=forbidden,
        )
    assert not target._root().exists()


@pytest.mark.parametrize("sessions", [(), ("../not-canonical",)])
def test_old_reference_contract_is_not_weakened(packet: Packet, sessions: tuple[str, ...]) -> None:
    cfg, target, plan, source = packet
    with pytest.raises(WorktreeStateError):
        collect_worktree_state(cfg, target, plan, source, IDENTITY, sessions, fake_owner)
    assert not target._root().exists()
