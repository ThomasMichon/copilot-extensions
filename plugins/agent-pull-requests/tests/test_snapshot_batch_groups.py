"""Selected-member packing preserves logical work and reconciliation barriers."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path
import sys

import pytest

from agent_pull_requests.repository_contract import RepositoryIdentity
from agent_pull_requests.snapshot_batch_groups import Group, pack_members
from agent_pull_requests.snapshot_batch_state import PROTOCOL
from agent_pull_requests.watch_observer import NativeObservationBinding, TargetKey
from agent_pull_requests.watch_replay import ObservationTarget


def binding(root: Path) -> NativeObservationBinding:
    identity = RepositoryIdentity("test", "github", "https://api.github.com", "reader", "owner/repository")
    return NativeObservationBinding(
        identity.key, identity.repo, sys.executable, (sys.executable,),
        (sys.executable,), identity, str(root / "native.json"), PROTOCOL,
    )


def target(binding: NativeObservationBinding, number: int) -> ObservationTarget:
    return ObservationTarget(binding.account, binding.repo, number, frozenset())


def key(target: ObservationTarget) -> TargetKey:
    return target.account, target.repo, target.number


def test_interleaving_and_partial_tails_preserve_each_selected_object_and_both_orders(tmp_path: Path) -> None:
    selected = binding(tmp_path)
    scalar = replace(selected, snapshot_batch=None, inventory_config=str(tmp_path / "scalar.json"))
    members = [target(selected, number) for number in range(1, 26)]
    departures = [target(scalar, number) for number in range(101, 105)]
    groups: list[Group] = []
    for offset in range(0, 25, 3):
        groups.append((selected, members[offset:offset + 3]))
        if offset // 3 < 4:
            groups.append((scalar, [departures[offset // 3]]))
    before = [(b, list(ts)) for b, ts in groups]
    packed = pack_members(groups, member_keys={key(t) for t in members},
                          cache_keys={key(t) for t in members + departures})
    assert [len(ts) for b, ts in packed if b == selected] == [10, 10, 5]
    assert [t for b, ts in packed if b == selected for t in ts] == members
    assert [t for b, ts in packed if b == scalar for t in ts] == departures
    assert Counter(id(t) for _, ts in packed for t in ts) == Counter(
        id(t) for _, ts in groups for t in ts
    )
    assert groups == before


@pytest.mark.parametrize("barrier", [
    "foreign-active", "scalar-active", "watch", "retained", "noncache",
    "batch-reconcile", "mixed-role", "credential", "prefix", "config", "duplicate",
])
def test_packing_cannot_cross_work_or_full_binding_barriers(tmp_path: Path, barrier: str) -> None:
    selected = binding(tmp_path)
    scalar = replace(selected, snapshot_batch=None)
    first, last = target(selected, 1), target(selected, 3)
    middle_binding = selected
    middle = [target(selected, 2)]
    member_keys = {key(first), key(last)}
    cache_keys = member_keys | {key(middle[0])}
    if barrier == "foreign-active":
        identity = RepositoryIdentity("other", "github", "https://api.github.com", "other", selected.repo)
        middle_binding = replace(selected, account=identity.key, repository_identity=identity)
        middle = [target(middle_binding, 2)]
        member_keys.add(key(middle[0]))
        cache_keys.add(key(middle[0]))
    elif barrier == "scalar-active":
        middle_binding = scalar
        member_keys.add(key(middle[0]))
    elif barrier == "watch":
        middle_binding = scalar
        middle = [replace(middle[0], registration_ids=frozenset({"watch"}))]
    elif barrier == "retained":
        middle_binding = scalar
        middle = [replace(middle[0], retention_epoch=("owner", 1))]
    elif barrier == "noncache":
        middle_binding = scalar
        cache_keys.remove(key(middle[0]))
    elif barrier == "mixed-role":
        middle.append(target(selected, 4))
        member_keys.add(key(middle[1]))
        cache_keys.add(key(middle[1]))
    elif barrier in ("credential", "prefix", "config"):
        member_keys.add(key(middle[0]))
        if barrier == "credential":
            middle_binding = replace(selected, credential_command=(sys.executable, "other"))
        elif barrier == "prefix":
            middle_binding = replace(selected, prefix=(sys.executable, "other"))
        else:
            middle_binding = replace(selected, inventory_config=str(tmp_path / "other.json"))
    elif barrier == "duplicate":
        middle = [first]
    groups = [(selected, [first]), (middle_binding, middle), (selected, [last])]
    packed = pack_members(groups, member_keys=member_keys, cache_keys=cache_keys)
    assert packed[0] == groups[0]
    assert Counter(id(t) for _, ts in packed for t in ts) == Counter(
        id(t) for _, ts in groups for t in ts
    )
    if barrier != "duplicate":
        assert packed == groups
    assert all(len({t.number for t in ts}) == len(ts) for b, ts in packed if b.snapshot_batch)


def test_omitted_membership_keeps_adjacent_groups_unchanged(tmp_path: Path) -> None:
    selected = binding(tmp_path)
    scalar = replace(selected, snapshot_batch=None)
    groups = [(selected, [target(selected, 1)]), (scalar, [target(scalar, 2)]),
              (selected, [target(selected, 3)])]
    assert pack_members(groups, member_keys=frozenset(), cache_keys={key(t) for _, ts in groups for t in ts}) is groups
