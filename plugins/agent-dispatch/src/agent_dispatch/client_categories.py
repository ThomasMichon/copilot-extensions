"""Capability-honest category requests across coordinator version skew."""

from __future__ import annotations

from typing import Any

from .task_categories import category_value
from .client_errors import DispatchUpgradeRequired


def require_endpoint_categories(endpoint: str) -> None:
    import os
    from .client import DispatchClient

    with DispatchClient(
        endpoint, token=os.environ.get("AGENT_DISPATCH_TOKEN"), timeout=3
    ) as client:
        client.require_task_categories()


class CategoryClientMixin:
    def require_task_categories(self) -> None:
        if self.health().get("task_categories") != 1:
            raise DispatchUpgradeRequired(
                "coordinator lacks task category filtering/classification"
            )

    def list(self, **params: Any) -> list[dict]:
        if params.get("category") is not None:
            category_value(params["category"], selection=True)
            self.require_task_categories()
        clean = {k: v for k, v in params.items() if v is not None}
        return self._unwrap(self._http.get("/tasks", params=clean))

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
