"""Explicit, inert local-feed preparation; no subscription, delivery or startup hook."""

from __future__ import annotations

from collections.abc import Callable
import copy
from pathlib import Path
import time
from typing import Any, Literal, Protocol, TYPE_CHECKING
from urllib.parse import urlsplit

from .queue_feed_preparation import PREPARATION_PROTOCOL, FeedWaiterAdmission
from .queue_records import TaskError
from .recipes.registry import dedup_key_for, render_recipe
from .repository_feed_waiter_contract import (
    CACHE_PROTOCOL, validate_feed_binding, validate_feed_delegation,
)

if TYPE_CHECKING:
    from .queue import TaskQueue


class FeedClient(Protocol):
    deadline: float | None

    async def negotiate(self) -> dict[str, Any]: ...

    async def request(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]: ...


def create_repository_feed_preparer(
    queue: TaskQueue, *, binding: dict[str, Any], lane: str, repository_target: str,
    land: Literal["self", "author"], checkpoint: Callable[[], None],
) -> RepositoryFeedPreparer:
    """Select the optional SDK explicitly; ordinary Dispatch imports do not need it."""
    from agent_pull_requests.repository_contract import RepositoryIdentity
    from agent_pull_requests_service.async_local_client import AsyncBoundRepositoryClient

    selected = validate_feed_binding(binding, repo=binding["identity"]["repo"])
    client = AsyncBoundRepositoryClient(
        Path(selected["socket"]), selected["target"],
        identity=RepositoryIdentity(**selected["identity"]), consumer=selected["consumer"],
    )
    return RepositoryFeedPreparer(
        queue, binding=selected, lane=lane, repository_target=repository_target,
        land=land, client=client, checkpoint=checkpoint,
    )


class RepositoryFeedPreparer:
    """Negotiate outside SQLite, then recheck the same finite task atomically.

    The embedding owns trusted scope/target mapping and active-route admission.
    This object has no cycle, scheduler, registration or fallback transport.
    """

    def __init__(
        self, queue: TaskQueue, *, binding: dict[str, Any], lane: str,
        repository_target: str, land: Literal["self", "author"],
        client: FeedClient, checkpoint: Callable[[], None],
    ) -> None:
        selected = validate_feed_binding(binding, repo=binding["identity"]["repo"])
        address = urlsplit(repository_target)
        parts = address.path.lstrip("/").split("/")
        if (
            address.scheme != "https" or not address.hostname
            or ":" in address.hostname or address.port not in (None, 443)
            or address.username is not None or address.password is not None
            or address.query or address.fragment or repository_target.endswith("/")
            or any(char.isspace() for char in repository_target)
            or len(parts) < 2 or any(part in {"", ".", ".."} for part in parts)
            or not isinstance(lane, str) or not lane.strip()
            or land not in ("self", "author") or not callable(checkpoint)
        ):
            raise ValueError("trusted harness lane and forge-qualified reviewer target required")
        self.queue, self.client = queue, client
        self._binding, self._lane, self._target = selected, lane, repository_target
        self._land, self._checkpoint, self._running = land, checkpoint, False

    @property
    def binding(self) -> dict[str, Any]:
        return copy.deepcopy(self._binding)

    async def _negotiate(self) -> dict[str, Any]:
        capabilities = await self.client.negotiate()
        feature = capabilities.get("consumer_binding")
        if (
            capabilities.get("protocol") != CACHE_PROTOCOL
            or capabilities.get("identity") != self._binding["identity"]
            or capabilities.get("namespace") != self._binding["identity"]["namespace"]
            or capabilities.get("target") != self._binding["target"]
            or not isinstance(feature, dict)
            or feature.get("protocol") != "agent-pull-requests.repository-consumer/v1"
            or feature.get("consumer") != self._binding["consumer"]
        ):
            raise ValueError("executing repository/consumer selection differs")
        return capabilities

    async def prepare(
        self, task_id: str, *, delegation: dict[str, Any], worker_id: str,
        resume_worktree: str, reason: str, host: str | None = None,
    ) -> dict[str, Any]:
        """Retain original intent after running support, without registering it."""
        selected = validate_feed_delegation(delegation)
        if selected["feed"]["binding"] != self._binding:
            raise ValueError("repository-feed binding changed")
        if self._running:
            raise TaskError("repository-feed preparation already in flight")
        if getattr(self.queue, "repository_feed_preparation_protocol", None) != PREPARATION_PROTOCOL:
            raise TaskError("native repository-feed preparation unsupported")
        self._checkpoint()
        task = self.queue.get(task_id)
        if task is None:
            raise TaskError("repository-feed task is unavailable")
        recipe = render_recipe("reviewer", {
            "repo": self._target, "pr": str(selected["number"]), "land": self._land,
        })
        admission = FeedWaiterAdmission.observe(
            task, lane=self._lane, dedup_key=dedup_key_for(recipe),
        )
        existing = self.queue.find_delegated_preparation(task_id, selected["preparation_id"])
        if existing is not None:
            retained = FeedWaiterAdmission.from_record(existing.get("feed_admission"))
            if (
                retained.lane != admission.lane or retained.dedup_key != admission.dedup_key
                or retained.task_id != task_id
            ):
                raise TaskError("repository-feed preparation association changed")
            admission = retained
        remaining = selected["feed"]["deadline_at"] - time.time()
        if remaining <= 0:
            raise TaskError("original repository-feed deadline elapsed")
        prior_deadline = self.client.deadline
        deadline = time.monotonic() + remaining
        self.client.deadline = min(prior_deadline, deadline) if prior_deadline is not None else deadline
        self._running = True
        try:
            await self._negotiate()
            self._checkpoint()
            page = await self.client.request("next-actions", {
                "reviewer": self._binding["reviewer"], "limit": 1,
            })
            if (
                page.get("protocol") != "agent-pull-requests.reviewer-actions/v1"
                or page.get("identity") != self._binding["identity"]
                or page.get("reviewer") != self._binding["reviewer"]
            ):
                raise ValueError("executing reviewer-feed selection differs")
            self._checkpoint()
            return self.queue.prepare_repository_feed_waiter(
                task_id, admission=admission, delegation=selected, worker_id=worker_id,
                resume_worktree=resume_worktree, reason=reason, host=host,
            )
        finally:
            self.client.deadline = prior_deadline
            self._running = False
