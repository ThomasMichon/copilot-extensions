"""Capability-selected standing-charter requests across coordinator version skew."""

from __future__ import annotations

import time
from typing import Any

from .client_errors import DispatchError, DispatchUpgradeRequired


def _deadline_options(deadline: float | None) -> dict[str, float]:
    if deadline is None:
        return {}
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("standing source request deadline exhausted")
    return {"timeout": remaining}


def standing_support(health: object) -> dict | None:
    support = health.get("standing_charters") if isinstance(health, dict) else None
    if (
        isinstance(support, dict) and type(support.get("version")) is int
        and support["version"] == 1 and support.get("finite_child_link") == 1
        and type(support.get("attachment")) is bool
    ):
        return support
    return None


class StandingClientMixin:
    def require_standing_charters(
        self, *, attachment: bool = False, deadline: float | None = None,
    ) -> dict:
        """Refuse before effects unless the running coordinator declares support."""
        support = standing_support(self.health(**_deadline_options(deadline)))
        if support is None:
            raise DispatchUpgradeRequired("coordinator lacks standing-charter support")
        if attachment and support["attachment"] is not True:
            raise DispatchError(409, "coordinator cannot protect standing-charter attachments")
        return support

    def _standing_post(self, path: str, body: dict[str, Any], *, attachment: bool = False) -> dict:
        self.require_standing_charters(attachment=attachment)
        return self._unwrap(self._http.post(path, json=body, headers=self._control_headers()))

    def _require_standing_source_sets(self, *, deadline: float | None = None) -> None:
        support = self.require_standing_charters(deadline=deadline)
        if type(support.get("source_set")) is not int or support["source_set"] != 1:
            raise DispatchUpgradeRequired("coordinator lacks standing-charter source-set support")

    def standing_source_repositories(self, namespace: str, *, deadline: float | None = None) -> list[str]:
        """Read all retained repository lanes for one declaring source."""
        self._require_standing_source_sets(deadline=deadline)
        result = self._unwrap(self._http.get(
            "/standing-charters/source-repositories", params={"namespace": namespace},
            headers=self._control_headers(), **_deadline_options(deadline),
        ))
        repositories = result.get("repositories") if isinstance(result, dict) else None
        if (
            not isinstance(repositories, list)
            or any(not isinstance(repo, str) or not repo or repo != repo.strip()
                   for repo in repositories)
            or len(set(repositories)) != len(repositories)
        ):
            raise DispatchError(502, "coordinator returned an invalid standing source inventory")
        return repositories

    def standing_source_observation(
        self, repo: str, namespace: str, *, deadline: float | None = None,
    ) -> str | None:
        """Read the running coordinator's retained same-source observation."""
        self._require_standing_source_sets(deadline=deadline)
        result = self._unwrap(self._http.get(
            "/standing-charters/source-sets", params={"repo": repo, "namespace": namespace},
            headers=self._control_headers(), **_deadline_options(deadline),
        ))
        if (
            not isinstance(result, dict) or "observation" not in result
            or (result["observation"] is not None and (
                not isinstance(result["observation"], str) or not result["observation"]
            ))
        ):
            raise DispatchError(502, "coordinator returned an invalid standing source observation")
        return result["observation"]

    def reconcile_standing_source_set(
        self, repo: str, namespace: str, entries: list[dict], *,
        revision: str | None, expected_observation: str | None, deadline: float | None = None,
    ) -> dict:
        """Send a complete source set only to a coordinator declaring that contract."""
        self._require_standing_source_sets(deadline=deadline)
        return self._unwrap(self._http.post(
            "/standing-charters/source-sets", json={
                "repo": repo, "namespace": namespace, "entries": entries,
                "revision": revision, "expected_observation": expected_observation,
            }, headers=self._control_headers(), **_deadline_options(deadline),
        ))

    def reconcile_standing_charter(
        self, title: str, declaration: dict[str, str], *, repo: str,
        expected_revision: int | None = None,
    ) -> dict:
        body: dict[str, Any] = {"title": title, "repo": repo, "declaration": declaration}
        if expected_revision is not None:
            body["expected_revision"] = expected_revision
        return self._standing_post("/standing-charters", body)

    def reserve_standing_charter(
        self, task_id: str, workspace: dict[str, str], *, expected_revision: int,
    ) -> dict:
        return self._standing_post(
            f"/tasks/{task_id}/standing/reserve",
            {"workspace": workspace, "expected_revision": expected_revision}, attachment=True,
        )

    def accept_standing_charter(
        self, task_id: str, workspace: dict[str, str], *, expected_revision: int,
        expected_generation: int, owner_session_id: str, expected_head_revision: int,
        expected_owner_session_id: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "workspace": workspace, "expected_revision": expected_revision,
            "expected_generation": expected_generation, "owner_session_id": owner_session_id,
            "expected_head_revision": expected_head_revision,
        }
        if expected_owner_session_id is not None:
            body["expected_owner_session_id"] = expected_owner_session_id
        return self._standing_post(f"/tasks/{task_id}/standing/accept", body, attachment=True)

    def release_standing_attachment(
        self, task_id: str, workspace: dict[str, str], *, expected_revision: int,
        expected_generation: int, owner_session_id: str, expected_conclusion_revision: int,
    ) -> dict:
        return self._standing_post(f"/tasks/{task_id}/standing/release", {
            "workspace": workspace, "expected_revision": expected_revision,
            "expected_generation": expected_generation, "owner_session_id": owner_session_id,
            "expected_conclusion_revision": expected_conclusion_revision,
        }, attachment=True)

    def change_standing_lifecycle(self, task_id: str, action: str, *, expected_revision: int) -> dict:
        if action not in {"retire", "settle-retirement", "reenroll"}:
            raise ValueError(f"unsupported standing lifecycle action: {action}")
        return self._standing_post(
            f"/tasks/{task_id}/standing/{action}", {"expected_revision": expected_revision},
        )
