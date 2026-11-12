"""Current reporter naming remains fresh and independent of saved worktrees."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_bridge.app_routes import configure_serving_app
from agent_bridge.models import ServiceConfig, TopologyProfile
from agent_bridge.routes import execution_context


def client_for(cfg: ServiceConfig) -> TestClient:
    app = configure_serving_app(cfg, "fixture", lifespan=None)
    scheme = "Be" + "arer "
    return TestClient(app, headers={"Authorization": scheme + "fixture"})


def topology(path: Path, guest: str = "guest") -> None:
    path.write_text(
        "machines:\n"
        "  native:\n"
        "    hostname: observed-host\n"
        "    physical_host: host\n"
        "    execution_platform: windows\n"
        f"  {guest}:\n"
        "    hostname: observed-host\n"
        "    physical_host: host\n"
        "    execution_platform: wsl\n",
        encoding="utf-8",
    )


def test_rename_is_visible_on_next_request_without_restarting_or_rewriting_records(tmp_path: Path) -> None:
    path = tmp_path / "machines.yaml"
    topology(path)
    cfg = ServiceConfig(topologies={"example": TopologyProfile(machines_yaml=str(path))})
    client = client_for(cfg)
    params = {"hostname": "observed-host", "environment": "wsl"}
    first = client.get("/api/v1/execution-context", params=params)
    assert first.status_code == 200
    assert first.json()["context"]["execution_machine"] == "guest"
    topology(path, "renamed-guest")
    second = client.get("/api/v1/execution-context", params=params)
    assert second.status_code == 200
    assert second.json()["context"]["execution_machine"] == "renamed-guest"
    native = client.get("/api/v1/execution-context", params={**params, "environment": "windows"})
    assert native.json()["context"]["execution_machine"] == "native"
    path.write_text("machines: [broken]", encoding="utf-8")
    assert client.get("/api/v1/execution-context", params=params).status_code == 503


@pytest.mark.parametrize("environment, expected", [
    ("windows", "new-host"), ("linux", "new-host"), ("wsl", "new-host-wsl"),
])
def test_standalone_uses_shared_unmapped_host_semantics(environment: str, expected: str) -> None:
    response = client_for(ServiceConfig()).get(
        "/api/v1/execution-context", params={"hostname": "new-host", "environment": environment},
    )
    assert response.status_code == 200
    assert response.json() == {
        "schema": execution_context.SCHEMA,
        "context": {
            "hostname": "new-host", "machine": "new-host", "environment": environment,
            "execution_machine": expected, "customization_key": None,
        },
    }


def test_unavailable_or_ambiguous_config_never_uses_the_daemons_cached_roster(tmp_path: Path) -> None:
    path = tmp_path / "machines.yaml"
    cfg = ServiceConfig(topologies={"example": TopologyProfile(machines_yaml=str(path))})
    client = client_for(cfg)
    params = {"hostname": "observed-host", "environment": "wsl"}
    assert client.get("/api/v1/execution-context", params=params).status_code == 503
    topology(path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            "  another-guest:\n    hostname: observed-host\n"
            "    physical_host: host\n    execution_platform: wsl\n",
        )
    assert client.get("/api/v1/execution-context", params=params).status_code == 503
    assert client.get("/api/v1/execution-context", params={**params, "environment": "invalid"}).status_code == 422
    for malformed in (
        "machines: {guest: null}\n",
        "machines: {guest: {hostname: observed-host, execution_platform: wsl, "
        "ssh: {environments: [{alias: guest}]}}}\n",
        "machines: {guest: {ssh: {environments: [null]}}}\n",
        "machines: {guest: {ssh: {environments: broken}}}\n",
    ):
        path.write_text(malformed, encoding="utf-8")
        assert client.get("/api/v1/execution-context", params=params).status_code == 503


def test_current_context_route_retains_bridge_authentication() -> None:
    client = client_for(ServiceConfig())
    client.headers.clear()
    assert client.get(
        "/api/v1/execution-context", params={"hostname": "host", "environment": "linux"},
    ).status_code == 401
