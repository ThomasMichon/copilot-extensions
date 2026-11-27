"""The dispatch attention source's stalled item for a starved pool."""

from __future__ import annotations

import builtins
from typing import Any

import pytest

from agent_dispatch import attention_contract as ac
from agent_dispatch import attention_sources as srcs
from agent_dispatch.client import DispatchError

T1 = "2026-10-07T11:00:00+00:00"
TASKS = [{"id": "q1", "title": "x", "status": "queued", "repo": "o/r"}]


class _Client:
    def __init__(self, pools: builtins.list[dict[str, Any]], advertised: bool = True) -> None:
        self.pools, self.advertised = pools, advertised

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def list(self, **_kw: object) -> builtins.list[dict[str, Any]]:
        return TASKS

    def health(self, repo: str | None = None) -> dict[str, Any]:
        return {"backlog": {}, **({"pool_starvation": 1} if self.advertised else {})}

    def pool_starvation(self) -> builtins.list[dict[str, Any]]:
        return self.pools


def _starved(age: float) -> dict[str, Any]:
    return {"registration_id": "worker", "machine": "host-a", "env": "linux", "max_concurrent": 1,
            "load": 1, "queued": 2, "oldest_queued_age": age,
            "holders": [{"task_id": "s1", "task_status": "suspended", "reservation": "r", "state": "spawned"}]}


@pytest.mark.parametrize("age,advertised,stalled", [
    (600, True, False),   # exactly the threshold is not stalled
    (601, True, True),
    (99999, False, False),  # a coordinator that doesn't advertise the read isn't asked
])
def test_a_pool_held_only_by_stopped_tasks_is_one_stalled_pool_item(
    monkeypatch: pytest.MonkeyPatch, age: float, advertised: bool, stalled: bool,
) -> None:
    monkeypatch.delenv(srcs.POOL_STARVED_AFTER_ENV, raising=False)
    result = srcs.read_dispatch(lambda: _Client([_starved(age)], advertised), T1)
    pools = [i for i in result["items"] if i["entity"] == srcs.POOL_ENTITY]
    assert bool(pools) is stalled and result["status"] == "ok"
    if stalled:
        (item,) = pools
        assert (item["entity_ref"], item["display_state"]) == ("worker@host-a/linux", "stalled")
        assert item["actions"] == [{"verb": "show", "argv": ["agent-dispatch", "show", "s1"]}]
        ac.validate_item({**item, "created_at": T1})


def test_a_zero_threshold_turns_pool_starvation_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(srcs.POOL_STARVED_AFTER_ENV, "0")
    result = srcs.read_dispatch(lambda: _Client([_starved(99999)]), T1)
    assert not [i for i in result["items"] if i["entity"] == srcs.POOL_ENTITY]


def test_a_failed_pool_starvation_read_is_uncertain_not_failed() -> None:
    class Broken(_Client):
        def pool_starvation(self) -> builtins.list[dict[str, Any]]:
            raise DispatchError(500, "boom")

    result = srcs.read_dispatch(lambda: Broken([]), T1)
    assert (result["status"], result["uncertain"]) == ("uncertain", 1)
