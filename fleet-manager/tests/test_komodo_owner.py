"""Composed current grants, real owned child, HTTP auth and restart custody."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import sys
import time
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from fleet_manager.komodo_declaration import KomodoDeclaration
from fleet_manager.komodo_health import identity_digest
from fleet_manager.komodo_network import network_fingerprint
from fleet_manager.komodo_owner import KomodoOwner, native_fingerprint
from fleet_manager.komodo_profile import render_profile
from fleet_manager.komodo_selection import KomodoSelection
from fleet_manager.komodo_state import KomodoState
from fleet_manager.planning import PlanError
from fleet_manager.runtime_admission import OperationGrant
from test_komodo_declaration import intent
from test_komodo_native import native_rig as native_rig
from test_runtime_admission import Harness, actor, policy
from test_runtime_identity import keys as keys

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux host substrate contract")


@pytest.mark.parametrize("scenario", [
    "fresh-repeat", "lost-reply", "recovery", "update", "adoption", "foreign",
    "failed-recovery", "unsettled-restart", "lost-terminal-receipt",
    "credential-drift", "native-drift", "network-drift", "foreign-endpoint",
    "foreign-network", "adoption-drift", "adoption-update", "adoption-update-corrupt", "adoption-failed-drift",
    "retire-reinstall", "retire-lost-reply", "retire-partial",
])
def test_composed_owner_preserves_original_operation_and_real_native_custody(
    native_rig: Any, keys: Any, scenario: str, monkeypatch: Any,
) -> None:
    rig = native_rig
    (rig["directory"] / "stateful").touch()
    runtime = rig["root"] / "host-runtime"
    runtime.mkdir(mode=0o700)
    for name in ("core", "periphery"):
        (rig["material"].keys / (name + ".pub")).write_text("fixture-public-key-" + name)

    def live() -> dict:
        return json.loads((rig["directory"] / "live.json").read_text())

    def effects() -> int:
        path = rig["directory"] / "effects"
        return len(path.read_text().splitlines()) if path.exists() else 0

    async def exercise() -> None:
        current = []
        health_failure = [False]
        token = "local-owner-fixture-token"

        async def handler(request: web.Request) -> web.Response:
            if not (rig["directory"] / "live.json").exists() or set(live()) != {"core", "mongo", "periphery"}:
                return web.json_response({"error": "starting"}, status=503)
            body = await request.json()
            if request.path == "/auth/login/LoginLocalUser":
                assert body == {
                    "username": "admin_username-fixture-material",
                    "password": "admin_password-fixture-material",
                }
                return web.json_response({"type": "Jwt", "data": {"jwt": token}})
            assert request.headers.get("authorization") == token
            actual = live()
            core_version = "2.0.1" if actual["core"]["Image"] == "sha256:" + "f" * 64 else "2.0.0"
            periphery_version = ("2.0.1" if actual["periphery"]["Image"] == "sha256:" + "1" * 64
                                 else "2.0.0")
            return web.json_response({
                "/read/GetVersion": {"version": "9.0.0" if health_failure[0]
                                    else core_version},
                "/read/GetServer": {
                    "id": "server-a", "name": "example-host", "config": {"enabled": True},
                    "info": {"public_key": "fixture-public-key-periphery"},
                },
                "/read/GetServerState": {"status": "Ok"},
                "/read/GetPeripheryInformation": {
                    "version": periphery_version,
                    "public_key": "fixture-public-key-periphery", "docker_connected": True,
                },
            }[request.path])

        app = web.Application()
        app.router.add_post("/{tail:.*}", handler)
        async with TestServer(app, host="127.0.0.1") as server:
            desired = rig["selected"].to_dict()
            desired.update(core_port=server.port, outfitting_seconds=5)
            selected = KomodoSelection(
                KomodoDeclaration.from_dict(desired), "fleet-a", "install-a", 1, "policy-1",
                1900001000, "e" * 64, runtime, rig["tooling"], rig["material"],
            )
            current.append(selected)
            harness = Harness(keys)
            revision = 0

            def owner() -> KomodoOwner:
                return KomodoOwner(current=lambda: current[0], admission=harness.owner,
                                   authority="gateway-a", clock=harness.clock)

            def request(name: str = "install-1") -> Any:
                return intent(name, generation=current[0].generation,
                              deployment=current[0].deployment_digest)

            async def invoke(instance: KomodoOwner, original: Any, *,
                             failed: bool = False, status: bool = False) -> Any:
                nonlocal revision
                revision += 1
                harness.current = policy(revision=revision, enrollments=(actor(operations=(
                    OperationGrant("manager.converge", "host-a", original.to_dict()["generation"],
                                   original.request_digest),
                )),))
                permit = harness.admit(original)
                return await asyncio.to_thread(
                    instance.inspect if status else instance.settle_failed if failed else instance.converge,
                    original, permit)

            if scenario.startswith("adoption") or scenario in ("foreign", "foreign-network"):
                profile = rig["root"] / "incumbent.json"
                raw = render_profile(selected.declaration, selected.material, owner="f" * 64)
                profile.write_bytes(raw)
                profile.chmod(0o600)
                await asyncio.to_thread(
                    rig["native"]._apply, selected.declaration, selected.material,
                    profile=profile, expected=raw, deadline=time.monotonic() + 5)
                original_native = live()
                if scenario == "foreign-network":
                    (rig["directory"] / "live.json").write_text("{}")
                    with pytest.raises(PlanError, match="network requires exact existing-owner adoption"):
                        await invoke(owner(), request())
                    assert effects() == 1
                    return
                if scenario == "foreign":
                    with pytest.raises(PlanError, match="existing-owner adoption"):
                        await invoke(owner(), request())
                    assert effects() == 1 and live() == original_native
                    return
                current[0] = replace(
                    selected, adoption=tuple((name, native_fingerprint(item))
                                             for name, item in original_native.items()),
                    adopted_public_identity=identity_digest(selected.material),
                    adopted_network=network_fingerprint(json.loads(
                        (rig["directory"] / "network.json").read_text())))
                if scenario == "adoption-failed-drift":
                    health_failure[0] = True
                    original = request()
                    with pytest.raises(PlanError, match="current Core version"):
                        await invoke(owner(), original)
                    changed = live()
                    changed["core"]["Config"]["Env"].append("UNSELECTED_SETTING=unexpected")
                    (rig["directory"] / "live.json").write_text(json.dumps(changed))
                    status = await invoke(owner(), original, status=True)
                    assert status["currently_healthy"] is None and not status["converged"]
                    for failed in (False, True):
                        with pytest.raises(PlanError, match="adopted native candidate"):
                            await invoke(owner(), original, failed=failed)
                    assert effects() == 1
                    return
            if scenario in ("lost-reply", "unsettled-restart"):
                (rig["directory"] / "lost-reply").touch()
                original = request()
                with pytest.raises(PlanError, match="uncertain"):
                    await invoke(owner(), original)
                assert effects() == 1
                status = await invoke(owner(), request("inspect-with-new-id"), status=True)
                assert status["accepted"] is None and status["unresolved_effects"] is True
                assert status["state"] == "unresolved" and status["converged"] is False
                assert status["installed"] is status["currently_healthy"] is None
                assert status["blockers"] == [
                    "native-resources-differ-from-current-or-retained-owner",
                    "native-network-custody-changed-or-unproved",
                ]
                assert effects() == 1 and status["native_mutations_performed"] is False
                if scenario == "unsettled-restart":
                    with KomodoState.open(runtime, daemon="daemon-a", project=desired["project"]) as state:
                        record = state.read()
                        record["attempt"]["native_settled"] = False
                        state.save(record)
                    status = await invoke(owner(), original, status=True)
                    assert status["installed"] is status["currently_healthy"] is True
                    assert status["unresolved_effects"] is True and status["converged"] is False
                    assert status["state"] == "unresolved" and status["native_mutations_performed"] is False
                    for failed in (False, True):
                        with pytest.raises(PlanError, match="settled|unsettled"):
                            await invoke(owner(), original, failed=failed)
                    assert effects() == 1
                    return
                current[0] = replace(current[0], generation=2)
                with pytest.raises(PlanError, match="unsettled|original-operation"):
                    await invoke(owner(), request("cannot-take-over"))
                assert effects() == 1
                current[0] = replace(current[0], generation=1, valid_until=1900002000,
                                     policy_revision="renewed-policy")
                receipt = await invoke(owner(), original)
                assert receipt.to_dict()["status"] == "succeeded" and effects() == 1
                return

            first = owner()
            original = request()
            if scenario == "fresh-repeat":
                status = await invoke(first, original, status=True)
                assert status["installed"] is False and status["accepted"] is None
                assert status["currently_healthy"] is None and status["converged"] is False
                assert effects() == 0
            if scenario == "lost-terminal-receipt":
                from fleet_manager.node_journal import NodeJournal

                reconcile = NodeJournal.reconcile
                failures = []

                def lost(journal: Any, request: Any, receipt: Any) -> Any:
                    if not failures:
                        failures.append(True)
                        raise PlanError("fixture-lost-terminal-receipt")
                    return reconcile(journal, request, receipt)

                monkeypatch.setattr(NodeJournal, "reconcile", lost)
                with pytest.raises(PlanError, match="fixture-lost-terminal"):
                    await invoke(first, original)
                assert effects() == 1
                receipt = await invoke(owner(), original)
                assert receipt.to_dict()["status"] == "succeeded" and effects() == 1
                return
            receipt = await invoke(first, original)
            assert receipt.to_dict()["status"] == "succeeded"
            original_native = live()
            assert effects() == 1
            repeated = await invoke(owner(), original)
            assert repeated == receipt and effects() == 1 and live() == original_native
            status = await invoke(owner(), original, status=True)
            assert status["installed"] is status["currently_healthy"] is status["converged"] is True
            assert status["state"] == "converged" and effects() == 1
            for value in rig["material"].credential_bytes().values():
                with KomodoState.open(runtime, daemon="daemon-a", project=desired["project"]) as state:
                    assert value not in (state.root / "selection.json").read_bytes()
            if scenario == "credential-drift":
                rig["material"].admin_password.write_text("changed-material-without-admission")
                changed_request = request("inspect-changed-material")
                status = await invoke(owner(), changed_request, status=True)
                assert status["installed"] is True and status["currently_healthy"] is None
                assert status["blockers"] == ["retained-storage-credentials-or-identity-changed"]
                with pytest.raises(PlanError, match="storage, credentials or identity"):
                    await invoke(owner(), changed_request)
                assert effects() == 1
                return
            if scenario in ("native-drift", "adoption-drift"):
                changed_native = live()
                changed_native["core"]["Config"]["Env"].append("UNSELECTED_SETTING=unexpected")
                (rig["directory"] / "live.json").write_text(json.dumps(changed_native))
                status = await invoke(owner(), request("inspect-drift"), status=True)
                assert status["installed"] is status["currently_healthy"] is None
                assert status["state"] == "drift" and status["converged"] is False
                assert "UNSELECTED_SETTING" not in json.dumps(status)
                with pytest.raises(PlanError, match="replaced or changed"):
                    await invoke(owner(), original)
                assert effects() == 1
                return
            if scenario in ("network-drift", "foreign-endpoint"):
                path = rig["directory"] / "network.json"
                changed = json.loads(path.read_text())
                if scenario == "network-drift":
                    changed["Id"] = "9" * 64
                else:
                    changed["Containers"]["9" * 64] = {"Name": "unrelated"}
                path.write_text(json.dumps(changed))
                status = await invoke(owner(), original, status=True)
                assert status["currently_healthy"] is None and status["state"] == "drift"
                with pytest.raises(PlanError, match="network"):
                    await invoke(owner(), original)
                assert effects() == 1
                return
            if scenario.startswith("retire-"):
                path = rig["directory"] / "network.json"
                network = path.read_bytes()
                sentinel = rig["material"].mongo_data / "retained-data"
                sentinel.write_text("must-survive")
                material = current[0].material_evidence()
                changed = current[0].declaration.to_dict()
                changed["desired_state"] = "absent"
                current[0] = replace(current[0], declaration=KomodoDeclaration.from_dict(changed),
                                     generation=2)
                absent = request("retire-owned")
                if scenario != "retire-reinstall":
                    (rig["directory"] / ("lost-rm" if scenario == "retire-lost-reply" else "lost-stop")).touch()
                    with pytest.raises(PlanError, match="uncertain"):
                        await invoke(owner(), absent)
                    assert len((rig["directory"] / "retire-effects").read_text().splitlines()) == (
                        2 if scenario == "retire-lost-reply" else 1)
                    if scenario == "retire-partial":
                        status = await invoke(owner(), request("inspect-partial-retirement"), status=True)
                        assert status["accepted"] is None and status["unresolved_effects"] is True
                        assert status["state"] == "unresolved" and status["converged"] is False
                        assert status["native_mutations_performed"] is False
                        with pytest.raises(PlanError, match="unresolved"):
                            await invoke(owner(), absent)
                        assert len((rig["directory"] / "retire-effects").read_text().splitlines()) == 1
                        assert path.read_bytes() == network and sentinel.read_text() == "must-survive"
                        return
                retired = await invoke(owner(), absent)
                assert retired.to_dict()["status"] == "succeeded" and not live()
                assert await invoke(owner(), absent) == retired
                status = await invoke(owner(), absent, status=True)
                assert status["state"] == "retired" and status["converged"] is True
                assert status["installed"] is False and status["currently_healthy"] is None
                assert path.read_bytes() == network and current[0].material_evidence() == material
                assert sentinel.read_text() == "must-survive"
                assert len((rig["directory"] / "retire-effects").read_text().splitlines()) == 2
                if scenario == "retire-reinstall":
                    changed.pop("desired_state")
                    current[0] = replace(current[0], declaration=KomodoDeclaration.from_dict(changed),
                                         generation=3)
                    restored = await invoke(owner(), request("reinstall-owned"))
                    assert restored.to_dict()["status"] == "succeeded" and effects() == 2
                    assert path.read_bytes() == network and sentinel.read_text() == "must-survive"
                return
            if scenario == "recovery":
                missing = live()
                del missing["core"]
                (rig["directory"] / "live.json").write_text(json.dumps(missing))
                current[0] = replace(current[0], generation=2)
                status = await invoke(owner(), request("recover-core"), status=True)
                assert status["installed"] is False and status["currently_healthy"] is None
                assert effects() == 1
                recovered = await invoke(owner(), request("recover-core"))
                assert recovered.to_dict()["status"] == "succeeded" and effects() == 2
                assert live()["core"]["Id"] != original_native["core"]["Id"]
                assert live()["mongo"] == original_native["mongo"]
                assert live()["periphery"] == original_native["periphery"]
            if scenario in ("update", "failed-recovery", "adoption-update", "adoption-update-corrupt"):
                retained_declaration = current[0].declaration
                changed = current[0].declaration.to_dict()
                changed["revision"] = "config-2"
                changed["images"]["core"] = {
                    "image": "ghcr.io/moghtech/komodo-core@sha256:" + "f" * 64, "version": "2.0.1",
                }
                changed["images"]["periphery"] = {
                    "image": "ghcr.io/moghtech/komodo-periphery@sha256:" + "1" * 64, "version": "2.0.1",
                }
                previous = current[0].deployment_digest
                current[0] = replace(current[0], declaration=KomodoDeclaration.from_dict(changed),
                                     generation=2)
                status = await invoke(owner(), request("update-core"), status=True)
                assert status["installed"] is status["currently_healthy"] is True
                assert status["converged"] is False and status["accepted"] is None
                assert status["observed"] == previous and status["desired"] != previous
                with pytest.raises(PlanError, match="database compatibility"):
                    await invoke(owner(), request("update-core"))
                assert effects() == 1
                current[0] = replace(current[0], compatible_with=(previous,))
                if scenario == "failed-recovery":
                    health_failure[0] = True
                    original_failure = request("compatible-update-core")
                    with pytest.raises(PlanError, match="current Core version"):
                        await invoke(owner(), original_failure)
                    assert effects() == 2
                    failed_deployment = current[0].deployment_digest
                    failed = await invoke(owner(), original_failure, failed=True)
                    assert failed.to_dict()["status"] == "failed" and effects() == 2
                    assert await invoke(owner(), original_failure) == failed
                    current[0] = replace(
                        current[0], declaration=retained_declaration, generation=3,
                        compatible_with=(failed_deployment,))
                    health_failure[0] = False
                    recovered = await invoke(owner(), request("explicit-compatible-recovery"))
                    assert recovered.to_dict()["status"] == "succeeded" and effects() == 3
                    assert live()["mongo"] == original_native["mongo"]
                    with KomodoState.open(runtime, daemon="daemon-a", project=desired["project"]) as state:
                        retained = state.root / "receipts" / (failed.to_dict()["result_ref"] + ".json")
                        evidence = json.loads(retained.read_text())
                        assert evidence["selection"]["compatible_with"] == [previous]
                        assert evidence["selection"]["declaration"]["images"]["core"]["version"] == "2.0.1"
                        assert state.read()["failed"] is None
                    return
                if scenario == "adoption-update-corrupt":
                    (rig["directory"] / "corrupt-fixed").touch()
                    original = request("compatible-update-core")
                    with pytest.raises(PlanError, match="fixed configuration"):
                        await invoke(owner(), original)
                    for failed in (False, True):
                        with pytest.raises(PlanError, match="fixed configuration"):
                            await invoke(owner(), original, failed=failed)
                    status = await invoke(owner(), original, status=True)
                    assert status["currently_healthy"] is None and not status["converged"]
                    assert effects() == 2
                    return
                updated = await invoke(owner(), request("compatible-update-core"))
                assert updated.to_dict()["status"] == "succeeded" and effects() == 2
                if scenario == "adoption-update":
                    assert live()["mongo"]["Mounts"] == original_native["mongo"]["Mounts"]
                else:
                    assert live()["mongo"] == original_native["mongo"]
                with KomodoState.open(runtime, daemon="daemon-a", project=desired["project"]) as state:
                    assert state.read()["previous_good"]["deployment"] == previous
                    assert state.read()["good"]["fixed_profile"] is True
                if scenario == "adoption-update":
                    changed_native = live()
                    changed_native["core"]["Config"]["Env"].append("UNSELECTED_SETTING=unexpected")
                    (rig["directory"] / "live.json").write_text(json.dumps(changed_native))
                    status = await invoke(owner(), request("compatible-update-core"), status=True)
                    assert status["currently_healthy"] is None and not status["converged"]
                    with pytest.raises(PlanError, match="replaced or changed"):
                        await invoke(owner(), request("compatible-update-core"))

    asyncio.run(exercise())
