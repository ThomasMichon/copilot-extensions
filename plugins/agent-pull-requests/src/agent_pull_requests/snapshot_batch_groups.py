"""Pack selected cache members without crossing active-work or watch barriers."""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING

from .watch_replay import ObservationTarget

if TYPE_CHECKING:
    from .watch_observer import NativeObservationBinding, TargetKey

Group = tuple["NativeObservationBinding", list[ObservationTarget]]


def pack_members(
    groups: list[Group], *, member_keys: Collection[TargetKey],
    cache_keys: Collection[TargetKey],
) -> list[Group]:
    """Move member prefixes across scalar reconciliation, preserving both orders."""
    if not member_keys:
        return groups

    def role(target: ObservationTarget, *, member: bool) -> bool:
        key = (target.account, target.repo, target.number)
        return (
            not target.registration_ids and target.retention_epoch is None
            and key in cache_keys and (key in member_keys) == member
        )

    def active(group: Group) -> bool:
        binding, targets = group
        return (
            binding.snapshot_batch is not None
            and all(role(target, member=True) for target in targets)
            and len({target.number for target in targets}) == len(targets)
        )

    remaining = [(binding, list(targets)) for binding, targets in groups]
    packed: list[Group] = []
    while remaining:
        binding, targets = remaining.pop(0)
        if active((binding, targets)):
            index = 0
            numbers = {target.number for target in targets}
            while len(targets) < 10 and index < len(remaining):
                other, candidates = remaining[index]
                if other.snapshot_batch is None and all(role(t, member=False) for t in candidates):
                    index += 1
                    continue
                if other != binding or not active((other, candidates)):
                    break
                count = 0
                for target in candidates:
                    if len(targets) == 10 or target.number in numbers:
                        break
                    targets.append(target)
                    numbers.add(target.number)
                    count += 1
                if count < len(candidates):
                    remaining[index] = (other, candidates[count:])
                    break
                remaining.pop(index)
        packed.append((binding, targets))
    return packed
