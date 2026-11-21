"""Capability-honest category requests across coordinator version skew."""

from __future__ import annotations

import json
from typing import Any

from .task_categories import category_value
from .client_errors import DispatchError, DispatchUpgradeRequired
from .client_standing import standing_support
from .queue_standing_schema import LEGACY_CLAIM_FENCE, STANDING_KIND

_STANDING_FENCE = json.loads(LEGACY_CLAIM_FENCE)


def _legacy_standing(task: dict) -> bool:
    """Older coordinators omit ``kind`` but still serialize the claim fence."""
    return task.get("kind", "finite") == STANDING_KIND or task.get("requires") == _STANDING_FENCE


def require_endpoint_categories(endpoint: str, *, token: str | None = None) -> None:
    import os
    from .client import DispatchClient

    with DispatchClient(
        endpoint, token=token or os.environ.get("AGENT_DISPATCH_TOKEN"), timeout=3
    ) as client:
        client.require_task_categories()


class CategoryClientMixin:
    def require_task_categories(self) -> None:
        if self.health().get("task_categories") != 1:
            raise DispatchUpgradeRequired(
                "coordinator lacks task category filtering/classification"
            )

    def list(self, **params: Any) -> list[dict]:
        if params.get("dedup_key") is not None:
            conflicts = [
                key
                for key, value in params.items()
                if key not in {"repo", "dedup_key"}
                and value is not None
                and not (key == "sweep" and value is False)
            ]
            if conflicts:
                raise DispatchError(
                    400, f"dedup_key lookup cannot combine selectors: {conflicts}"
                )
            return self.lookup_dedup(params["dedup_key"], repo=params.get("repo"))
        if params.get("category") is not None:
            category_value(params["category"], selection=True)
            self.require_task_categories()
        legacy_finite = False
        if params.get("standing_parent_id") is not None or params.get("kind") == "standing-charter":
            # Older coordinators silently ignore unknown query parameters.
            self.require_standing_charters()
        elif params.get("kind") == "finite" and standing_support(self.health()) is None:
            # A rolled-back coordinator ignores the selector yet still reads
            # retained standing rows, so exclude them by their claim fence.
            legacy_finite = True
            params = {**params, "kind": None}
        clean = {k: v for k, v in params.items() if v is not None}
        tasks = self._unwrap(self._http.get("/tasks", params=clean))
        if not legacy_finite:
            return tasks
        finite = [task for task in tasks if not _legacy_standing(task)]
        if tasks and not finite and len(tasks) >= (200 if params.get("limit") is None else params["limit"]):
            # Draining visible finite rows reveals older ones; a full page of
            # standing rows never drains and would hide finite work forever.
            raise DispatchUpgradeRequired(
                "coordinator cannot exclude standing charters before its list limit"
            )
        return finite

    def lookup_dedup(self, dedup_key: str, *, repo: str | None) -> list[dict]:
        """Read [] or the exact key's current create-dedup occupant in one repo."""
        if not isinstance(repo, str) or not repo.strip():
            raise DispatchError(400, "dedup lookup requires a non-empty repo lane")
        if not isinstance(dedup_key, str) or not dedup_key.strip():
            raise DispatchError(400, "dedup lookup requires a non-empty dedup_key")
        if self.health().get("exact_dedup_lookup") != 1:
            raise DispatchUpgradeRequired("coordinator lacks exact repo-scoped dedup lookup")
        return self._unwrap(
            self._http.get("/tasks", params={"repo": repo, "dedup_key": dedup_key})
        )

    def find(
        self,
        query: str,
        *,
        repo: str | None = None,
        limit: int = 50,
        category: str | None = None,
    ) -> list[dict]:
        return self.list(q=query, repo=repo, limit=limit, category=category)

    def sweep(
        self,
        *,
        repo: str | None = None,
        limit: int = 500,
        category: str | None = None,
    ) -> list[dict]:
        return self.list(sweep=True, repo=repo, limit=limit, category=category)

    def create(self, title: str, **kwargs: Any) -> dict:
        category = kwargs.pop("category", "work")
        category_value(category)
        if category == "maintenance":
            self.require_task_categories()
            kwargs["category"] = category
        if kwargs.get("standing_parent_id") is None:
            kwargs.pop("standing_parent_id", None)
        else:
            self.require_standing_charters()
        # Omitted/work requests retain the old wire shape for older peers.
        return self._unwrap(self._http.post("/tasks", json={"title": title, **kwargs}))

    def classify_maintenance(self, **body: Any) -> dict:
        self.require_task_categories()
        return self._unwrap(
            self._http.post(
                "/task-categories/classify",
                json=body,
                headers=self._control_headers(),
            )
        )
