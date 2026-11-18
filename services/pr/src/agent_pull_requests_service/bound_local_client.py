"""Explicit local scope selection; no task, credential or service activation."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any

from agent_pull_requests.repository_contract import RepositoryIdentity

from .local_client import PROTOCOL, LocalClient, LocalClientError

# The client must import with prior library versions, then negotiate live support.
CONSUMER_BINDING_PROTOCOL = "agent-pull-requests.repository-consumer/v1"


class BoundRepositoryClient(LocalClient):
    """Pin configured repository and consumer expectations through existing Unix IO."""

    _expected_transport: tuple[Path, str, str]
    _identity: RepositoryIdentity
    _consumer: str

    def __init__(
        self, path: Path, target: str, *, identity: RepositoryIdentity, consumer: str,
    ) -> None:
        if sys.platform != "linux" or not path.is_absolute():
            raise LocalClientError("Linux absolute Unix socket required")
        if not isinstance(consumer, str) or not 1 <= len(consumer) <= 128 or any(
            ord(char) < 32 for char in consumer
        ):
            raise LocalClientError("invalid expected repository consumer")
        super().__init__(path, identity.namespace, target)
        self._expected_transport = path, identity.namespace, target
        self._identity, self._consumer = identity, consumer

    def _check_transport(self) -> None:
        if (self.path, self.namespace, self.target) != self._expected_transport:
            raise LocalClientError("configured local transport changed; original binding retained")
        if self.binding is not None and self.binding != {
            "protocol": PROTOCOL, "namespace": self._identity.namespace,
            "repository_key": self._identity.key,
        }:
            raise LocalClientError("configured repository binding changed; original identity retained")

    def negotiate(self) -> dict[str, Any]:
        """Read executing support without registering, adopting or preparing a task."""
        self._check_transport()
        selected = self._http("GET", "capabilities")
        feature = selected.get("consumer_binding")
        if (
            selected.get("identity") != self._identity.document()
            or selected.get("repository_key") != self._identity.key
            or selected.get("target") != self.target
            or not isinstance(feature, dict)
            or feature.get("protocol") != CONSUMER_BINDING_PROTOCOL
            or feature.get("consumer") != self._consumer
        ):
            raise LocalClientError("configured repository/consumer binding unsupported or different")
        self._accept_selection(selected)
        return copy.deepcopy(selected)

    def _select(self) -> None:
        self._check_transport()
        if self.binding is None:
            self.negotiate()

    def _envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._check_transport()
        return {**super()._envelope(payload), "expected_consumer": self._consumer}
