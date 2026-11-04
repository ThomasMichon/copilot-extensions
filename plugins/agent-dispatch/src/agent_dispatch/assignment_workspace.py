"""Original workspace identity for a retained task relationship."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from .queue_records import TaskError


def same_assignment_worker(left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    left_host, left_sep, left_tree = left.partition("/")
    right_host, right_sep, right_tree = right.partition("/")
    return (
        bool(left_sep and right_sep)
        and left_tree == right_tree
        and (left_host.strip().lower() == right_host.strip().lower())
    )


@dataclass(frozen=True)
class AssignmentWorkspace:
    machine: str
    project: str
    worktree: str
    path: str
    creation_nonce: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in asdict(self).values()):
            raise TaskError("task relationship requires the original complete workspace identity")
        if "/" in self.machine or "/" in self.project or "#" in self.owner_ref:
            raise TaskError("task relationship requires a qualified workspace owner")

    @classmethod
    def from_dict(cls, value: object) -> AssignmentWorkspace:
        try:
            if not isinstance(value, dict):
                raise TypeError("workspace must be an object")
            return cls(**value)
        except TypeError as exc:
            raise TaskError("task relationship workspace fields are invalid") from exc

    @property
    def owner_ref(self) -> str:
        return f"{self.machine}/{self.project}/{self.worktree}"

    @property
    def worker_id(self) -> str:
        return f"{self.machine}/{self.worktree}"


class RelationshipWriter(Protocol):
    """The selected ground owner, not a worker-supplied receipt."""

    def __call__(
        self,
        workspace: AssignmentWorkspace,
        operation: str,
        payload: dict,
    ) -> dict: ...
