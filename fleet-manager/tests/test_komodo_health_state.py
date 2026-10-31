"""Current authenticated loopback probes and canonical local custody controls."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from fleet_manager.komodo_declaration import KomodoDeclaration
from fleet_manager.komodo_health import current_health
from fleet_manager.komodo_state import KomodoState
from fleet_manager.planning import PlanError, canonical
from komodo_public_fixtures import PUBLIC_DER, PUBLIC_PEM
from test_komodo_native import native_rig as native_rig

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux host substrate contract")


@pytest.mark.parametrize("failure", [
    None, "credentials", "version", "key", "disconnected", "payload", "missing-core", "missing-periphery",
    "global", "global-inline", "global-missing", "global-wrong", "global-mount", "missing-override",
    "live-key", "retained-key",
    "global-address-nonempty", "global-address-missing", "global-address-null", "global-address-whitespace",
    "override-address-nonempty", "override-address-missing",
])
def test_real_authenticated_current_core_and_periphery_health(
    native_rig: Any, failure: str | None,
) -> None:
    rig = native_rig
    for name in ("core", "periphery"):
        if failure != "missing-" + name:
            (rig["material"].keys / (name + ".pub")).write_text(
                "invalid-key" if failure == "retained-key" else PUBLIC_PEM[name])
    core = {
        "Config": {"Env": ["KOMODO_PERIPHERY_PUBLIC_KEY=file:/config/keys/periphery.pub"]},
        "Mounts": [{"Type": "bind", "Source": str(rig["material"].keys), "Destination": "/config/keys"}],
    }
    if failure == "global-inline":
        core["Config"]["Env"] = ["KOMODO_PERIPHERY_PUBLIC_KEY=" + PUBLIC_DER["periphery"]]
    elif failure == "global-wrong":
        core["Config"]["Env"] = ["KOMODO_PERIPHERY_PUBLIC_KEY=" + PUBLIC_DER["core"]]
    elif failure == "global-missing":
        core["Config"]["Env"] = []
    elif failure == "global-mount":
        core["Mounts"][0]["Source"] = str(rig["root"] / "foreign-keys")
    server_config = {"enabled": True, "address": ""}
    if failure in ("global-address-nonempty", "override-address-nonempty"):
        server_config["address"] = "https://periphery.example.com"
    elif failure in ("global-address-missing", "override-address-missing"):
        del server_config["address"]
    elif failure == "global-address-null":
        server_config["address"] = None
    elif failure == "global-address-whitespace":
        server_config["address"] = " "

    async def exercise() -> None:
        token = "local-fixture-session-token"
        requests = []

        async def handler(request: web.Request) -> web.Response:
            requests.append(request.path)
            body = await request.json()
            if request.path == "/auth/login/LoginLocalUser":
                assert body == {
                    "username": "admin_username-fixture-material",
                    "password": "admin_password-fixture-material",
                }
                if failure == "credentials":
                    return web.json_response({"error": "refused"}, status=401)
                if failure == "payload":
                    response = web.StreamResponse(headers={"Content-Length": "1024"})
                    await response.prepare(request)
                    await response.write(b'{"incomplete":')
                    request.transport.close()
                    return response
                return web.json_response({"type": "Jwt", "data": {"jwt": token}})
            # This exercises the actual raw-token header construction, not a client mock.
            assert request.headers.get("authorization") == token
            responses = {
                "/read/GetVersion": {"version": "2.0.1" if failure == "version" else "2.0.0"},
                "/read/GetServer": {
                    "id": "server-a", "name": "example-host", "config": server_config,
                    "info": ({} if failure == "missing-override" else {
                        "public_key": PUBLIC_DER["core"] if failure == "key"
                        else "" if failure and failure.startswith("global")
                        else PUBLIC_DER["periphery"]}),
                },
                "/read/GetServerState": {"status": "NotOk" if failure == "disconnected" else "Ok"},
                "/read/GetPeripheryInformation": {
                    "version": "2.0.0", "public_key": PUBLIC_DER[
                        "core" if failure == "live-key" else "periphery"],
                    "docker_connected": True,
                },
            }
            if request.path != "/read/GetVersion":
                assert body == {"server": "example-host"}
            return web.json_response(responses[request.path])

        app = web.Application()
        app.router.add_post("/{tail:.*}", handler)
        async with TestServer(app, host="127.0.0.1") as server:
            declaration = rig["selected"].to_dict()
            declaration["core_port"] = server.port
            selected = KomodoDeclaration.from_dict(declaration)
            deadline = time.monotonic() + (0.15 if failure in ("disconnected", "payload") else 3)
            if failure in (None, "global", "global-inline", "override-address-nonempty", "override-address-missing"):
                result = await current_health(selected, rig["material"], deadline=deadline, core=core)
                assert result["core_version"] == result["periphery_version"] == "2.0.0"
                assert result["server"] == "server-a"
                assert token.encode() not in canonical(result)
                assert requests == [
                    "/auth/login/LoginLocalUser", "/read/GetVersion", "/read/GetServer",
                    "/read/GetServerState", "/read/GetPeripheryInformation",
                ]
            else:
                with pytest.raises(PlanError) as caught:
                    await current_health(selected, rig["material"], deadline=deadline, core=core)
                assert token not in str(caught.value) and "fixture-material" not in str(caught.value)
                if failure in ("missing-core", "missing-periphery"):
                    assert str(caught.value) == "Komodo retained public identity is unavailable"
                if failure and failure.startswith("global-address"):
                    assert requests == [
                        "/auth/login/LoginLocalUser", "/read/GetVersion", "/read/GetServer",
                    ]
                assert requests.count("/auth/login/LoginLocalUser") == 1

    asyncio.run(exercise())


def test_physical_state_uses_same_lock_across_installation_aliases_and_real_process(
    native_rig: Any,
) -> None:
    from agent_procutil.owned_process import run_caller_owned

    root = native_rig["root"] / "host-runtime"
    root.mkdir(mode=0o700)
    source = str(Path(__file__).absolute().parents[1] / "src")
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from fleet_manager.komodo_state import KomodoState\n"
        "from fleet_manager.planning import PlanError\n"
        "try:\n"
        " with KomodoState.open(Path(sys.argv[1]), daemon='daemon-a', project='project-a'):\n"
        "  raise RuntimeError('second owner entered')\n"
        "except PlanError as error:\n"
        " assert 'busy' in str(error)\n"
        " print('same-physical-owner-refused')\n"
    )
    with KomodoState.open(root, daemon="daemon-a", project="project-a") as first:
        selected = {"installation": "first-owner", "generation": 1}
        first.save(selected)
        first.profile(b'{"services":{}}')
        result = run_caller_owned(
            [str(Path(sys.executable).resolve()), "-c", script, str(root)],
            timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
            env={"PYTHONPATH": source},
        )
        assert result.returncode == 0 and result.stdout.strip() == "same-physical-owner-refused"
        assert first.read() == selected
        location = first.root
    with KomodoState.open(root, daemon="daemon-a", project="project-a") as second:
        assert second.root == location and second.read() == selected
    with KomodoState.open(root, daemon="daemon-a", project="project-b") as independent:
        assert independent.root != location and independent.read() is None


def test_state_retains_failed_slots_and_refuses_changed_or_conflicting_publication(native_rig: Any) -> None:
    root = native_rig["root"] / "host-runtime"
    root.mkdir(mode=0o700)
    with KomodoState.open(root, daemon="daemon-a", project="project-a") as owner:
        first = owner.profile(b'{"selected":"first"}')
        second = owner.profile(b'{"selected":"second"}')
        assert first != second and first.exists() and second.exists()
        owner.save({"generation": 1})
        pending = owner.root / "selection.pending"
        pending.write_bytes(canonical({"generation": 2}))
        pending.chmod(0o600)
        with pytest.raises(PlanError, match="conflicts|unresolved"):
            owner.save({"generation": 3})
        assert owner.read() == {"generation": 1}
        owner.save({"generation": 2})
        assert owner.read() == {"generation": 2}
        assert first.exists() and second.exists()
        assert json.loads((owner.root / "selection.json").read_text()) == {"generation": 2}


def test_original_pending_prefix_recovers_without_overwriting_existing_bytes(native_rig: Any) -> None:
    root = native_rig["root"] / "host-runtime"
    root.mkdir(mode=0o700)
    with KomodoState.open(root, daemon="daemon-a", project="project-a") as owner:
        expected = {"generation": 1, "selected": "exact-original-content"}
        raw = canonical(expected)
        pending = owner.root / "selection.pending"
        pending.write_bytes(raw[:9])
        pending.chmod(0o600)
        owner.save(expected)
        assert owner.read() == expected
        outcome = {"candidate": "retained-failure", "compatible_with": ["original-data"]}
        reference = owner.retain(outcome)
        retained = owner.root / "receipts" / (reference + ".json")
        before = retained.read_bytes()
        assert owner.retain(outcome) == reference and retained.read_bytes() == before
