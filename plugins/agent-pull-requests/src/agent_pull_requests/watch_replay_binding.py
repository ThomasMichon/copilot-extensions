"""Expected scoped replay selection, never authentication or an authority grant."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .watch_replay import ConsumerScope
from .watch_replay_store import ReplayServiceIdentity

SCOPED_PROTOCOL = "agent-pull-requests.watch/v2"


class ReplayBindingMismatch(ValueError):
    """The selected operation cannot use the expected replay binding."""


@dataclass(frozen=True)
class ReplayBinding:
    fleet: str
    gateway: str
    slot: str
    namespace: str
    principal: str
    target: str
    consumer: str
    account: str
    repo: str

    def __post_init__(self) -> None:
        ReplayServiceIdentity(self.fleet, self.gateway, self.slot)
        ConsumerScope(self.consumer, self.account, self.repo)
        for value in (self.namespace, self.principal):
            if (not isinstance(value, str) or not value or len(value) > 256
                    or any(ord(char) < 32 for char in value)):
                raise ValueError("invalid replay binding")
        if not isinstance(self.target, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9-]{0,63}", self.target,
        ):
            raise ValueError("invalid replay binding")

    def document(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_document(cls, value: object) -> ReplayBinding:
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("invalid replay binding")
        return cls(**value)
