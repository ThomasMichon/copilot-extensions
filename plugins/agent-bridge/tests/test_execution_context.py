"""Current reporter naming remains fresh and independent of saved worktrees."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from agent_bridge.app_routes import configure_serving_app
from agent_bridge.db import Database
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


@pytest.mark.parametrize("family_sibling", [False, True])
def test_explicit_hostname_conflict_is_not_resolved_by_the_machine_key(
    tmp_path: Path, family_sibling: bool,
) -> None:
    path = tmp_path / "machines.yaml"
    path.write_text(
        "machines:\n  victim:\n    hostname: real-host\n"
        "    physical_host: shared-root\n    execution_platform: linux\n"
        + ("  shared-root:\n    hostname: observed-host\n    physical_host: shared-root\n"
           "    execution_platform: windows\n" if family_sibling else ""),
        encoding="utf-8",
    )
    client = client_for(ServiceConfig(topologies={"example": TopologyProfile(machines_yaml=str(path))}))
    response = client.get(
        "/api/v1/execution-context",
        params={"hostname": "observed-host" if family_sibling else "victim", "environment": "linux"},
    )
    assert response.status_code == 503


@pytest.mark.parametrize("environment, expected", [("windows", "legacy"), ("wsl", "legacy-wsl")])
def test_legacy_profiles_without_ssh_aliases_keep_native_guest_naming(
    tmp_path: Path, environment: str, expected: str,
) -> None:
    path = tmp_path / "machines.yaml"
    path.write_text(
        "machines:\n  legacy:\n    hostname: observed-host\n"
        "    ssh:\n      environments:\n        - {name: windows}\n        - {name: wsl}\n",
        encoding="utf-8",
    )
    client = client_for(ServiceConfig(topologies={"example": TopologyProfile(machines_yaml=str(path))}))
    response = client.get(
        "/api/v1/execution-context", params={"hostname": "observed-host", "environment": environment},
    )
    assert response.status_code == 200
    assert response.json()["context"]["execution_machine"] == expected


def test_machine_rename_rollover_retains_alias_and_cli_claim(tmp_db: Database) -> None:
    from agent_procutil.owned_process import run_caller_owned

    node = shutil.which("node")
    assert node is not None, "execution-context rollover control requires Node"
    modules = (
        Path(__file__).resolve().parents[3]
        / "libs/copilot-driver-core/src/agent_remote_driver/bridge_extension"
    )
    script = """
import {serializedRegister, adoptSessionId} from __DELIVERY__;
import {refreshRegistrationMetadata, processIdentity} from __METADATA__;
let machine = "old-machine";
const state = {sessionId: "predecessor", registered: false, meta: {
  machine, cwd: "/fixture", worktree_id: "rollover-worktree", repo: "example",
  branch: null, role: null,
}};
const packets = [];
const register = serializedRegister(state, async (id, metadata) => {
  packets.push({session_id: id, ...processIdentity(), ...metadata});
  if (id === "predecessor" && machine === "renamed-machine") machine = "changed-again";
  return true;
}, () => {}, {prepare: () => refreshRegistrationMetadata(state, async () => machine)});
await register();
machine = "renamed-machine";
adoptSessionId(state, "successor");
await register();
await register.close();
console.log(JSON.stringify(packets));
""".replace("__DELIVERY__", json.dumps((modules / "delivery.mjs").as_uri())).replace(
        "__METADATA__", json.dumps((modules / "metadata.mjs").as_uri()),
    )
    result = run_caller_owned(
        [node, "--input-type=module", "--eval", script],
        timeout=60, max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert result.returncode == 0, result.stderr
    packets = json.loads(result.stdout)
    assert [packet["session_id"] for packet in packets] == ["predecessor", "predecessor", "successor"]
    assert [packet["machine"] for packet in packets] == ["old-machine", "renamed-machine", "renamed-machine"]
    now = time.time()
    tmp_db.create_cli_mode_reservation("rollover-worktree", now=now)
    for offset, packet in enumerate(packets, 1):
        assert tmp_db.register_live_session(**packet, now=now + offset) == "live"
    assert tmp_db.get_live_session("predecessor")["session_id"] == "successor"
    assert tmp_db.get_cli_mode_reservation("rollover-worktree")["claimed_by_session_id"] == "successor"
