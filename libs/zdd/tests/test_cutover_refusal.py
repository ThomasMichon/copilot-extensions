from __future__ import annotations

from pathlib import Path

from zdd import routing
from zdd.cutover import CutoverOrchestrator


def test_cutover_refuses_active_route_under_cutover_lock(tmp_path: Path) -> None:
    routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=61001,
        pid=101,
        version="old",
    )
    spawned: list[int] = []

    class Routing:
        def reap_stale_active(self, *_a, **_k):
            return None

        def __getattr__(self, name: str):
            return getattr(routing, name)

    orch = CutoverOrchestrator(
        tmp_path,
        bind="127.0.0.1",
        version="new",
        spawn_passive=lambda port: spawned.append(port),
        health_check=lambda _host, _port: True,
        make_client=lambda _base: None,
        pick_free_port=lambda: 61002,
        refuse_old=lambda active: "active route refused" if active else None,
        routing_mod=Routing(),
    )

    res = orch.run(health_timeout=1, drain_timeout=1)

    assert not res.ok
    assert res.error == "active route refused"
    assert res.steps == ["refused: active route refused"]
    assert spawned == []
