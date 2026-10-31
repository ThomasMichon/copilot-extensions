"""Exact original grants, distinct current repair authority and interrupted publication."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from fleet_manager.komodo_control import CONTRACT, REPAIR_CONTRACT, repair_bootstrap, repair_status
from fleet_manager.komodo_declaration import KomodoDeclaration
from fleet_manager.komodo_native import KomodoNative
from fleet_manager.komodo_owner import KomodoOwner
from fleet_manager.komodo_repair import KomodoRepairOwner
from fleet_manager.komodo_repair_selection import KomodoRepairSelection
from fleet_manager.komodo_selection import KomodoSelection
from fleet_manager.komodo_state import KomodoState
from fleet_manager.node_journal import NodeJournal
from fleet_manager.planning import PlanError, canonical, decode, digest
from fleet_manager.runtime_admission import AdmissionError, AdmissionAuthority, OperationGrant
from komodo_public_fixtures import PUBLIC_DER, PUBLIC_PEM
from test_komodo_declaration import intent
from test_komodo_native import native_rig as native_rig
from test_runtime_admission import Harness, actor, policy
from test_runtime_identity import keys as keys

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux original-owner repair")

CRASHES = [
    "native-before", "health-after", "retain-before", "retain-after", "save-before", "save-after",
    "journal-before", "journal-after", "clear-before", "clear-after",
    "complete-before", "complete-after", "journal-revoke", "append-revoke", "save-revoke",
]
DRIFTS = [
    f"publication-drift-{stage}-{change}"
    for stage in ("retain", "save", "append")
    for change in ("stop", "restart", "resource", "network", "endpoint", "attachment", "disconnect",
                   "immutable-outcome-missing", "immutable-outcome-corrupt", "immutable-history-missing",
                   "immutable-history-corrupt", "host-runtime")
]
REFUSALS = [
    "different-request", "different-generation", "changed-candidate", "changed-executor", "same-executor",
    "changed-material", "inode-material", "wrong-fleet", "expired-transition", "expired-candidate", "revoked-transition",
    "revoked-grant", "missing-owner", "missing-lock", "missing-journal", "missing-profile",
    "missing-selection", "busy-owner",
    "unsettled", "not-fixed", "changed-profile", "changed-native", "wrong-journal",
    "revoke-after-health", "grant-after-health", "expiry-after-health", "key-after-health",
    "process-after-health", "state-after-health", "executor-after-health", "runtime-after-health",
    "health-endpoint-drift", "host-root-after-health",
] + DRIFTS


@pytest.mark.parametrize("mode", [
    pytest.param(mode, marks=[] if mode in (
        "success", "revoked-transition", "revoke-after-health", "journal-revoke",
    ) else [pytest.mark.client_distribution_full])
    for mode in ("success", "failed", "mount-order", "renewed", "prior-failed",
                 "status-endpoint-drift", *REFUSALS, *CRASHES)
])
def test_original_unknown_uses_current_repair_without_native_replay(
    native_rig: Any, keys: Any, monkeypatch: Any, mode: str,
) -> None:
    rig = native_rig
    (rig["directory"] / "stateful").touch()
    runtime = rig["root"] / "repair-runtime"
    runtime.mkdir(mode=0o700)
    for name in ("core", "periphery"):
        (rig["material"].keys / (name + ".pub")).write_text(PUBLIC_PEM[name])
    sentinel = rig["material"].mongo_data / "retained-fixture"
    sentinel.write_text("original-data")
    harness = Harness(keys)
    original_health_failure = [True]
    health_changes: list[bool] = []
    candidate: list[KomodoSelection] = []
    executor: list[KomodoSelection] = []
    transition = {"active": True, "revision": 1, "expiry": harness.clock() + 1000}
    runtime_identity = ["9" * 64]
    resolver_calls: list[tuple[str, str, str]] = []

    def live() -> dict[str, Any]:
        return json.loads((rig["directory"] / "live.json").read_text())

    async def exercise() -> None:
        async def handler(request: web.Request) -> web.Response:
            if request.path == "/auth/login/LoginLocalUser":
                assert await request.json() == {
                    "username": "admin_username-fixture-material",
                    "password": "admin_password-fixture-material",
                }
                return web.json_response({"data": {"jwt": "repair-fixture-token"}})
            assert request.headers.get("authorization") == "repair-fixture-token"
            result = {
                "/read/GetVersion": {"version": "9.0.0" if original_health_failure[0] else "2.0.0"},
                "/read/GetServer": {
                    "id": "server-a", "name": "example-host", "config": {"enabled": True, "address": ""},
                    "info": {"public_key": ""},
                },
                "/read/GetServerState": {"status": "Ok"},
                "/read/GetPeripheryInformation": {
                    "version": "2.0.0", "public_key": PUBLIC_DER["periphery"],
                    "docker_connected": True,
                },
            }[request.path]
            if request.path == "/read/GetPeripheryInformation" and not health_changes:
                health_changes.append(True)
                if mode in ("revoke-after-health", "executor-after-health"):
                    if mode.startswith("revoke"):
                        transition["active"] = False
                    else:
                        executor[0] = replace(executor[0], release_digest="8" * 64)
                elif mode in ("grant-after-health", "renewed"):
                    harness.current = policy(revision=2, enrollments=() if mode.startswith("grant") else (
                        actor(operations=(OperationGrant(
                            "manager.converge", "host-a", 1, original.request_digest),)),))
                    transition["revision"] = 2
                elif mode == "expiry-after-health":
                    harness.clock.value += 31
                elif mode == "runtime-after-health":
                    runtime_identity[0] = "8" * 64
                elif mode == "key-after-health":
                    (rig["material"].keys / "core.pub").write_text(PUBLIC_PEM["periphery"])
                elif mode in ("health-endpoint-drift", "status-endpoint-drift"):
                    (rig["directory"] / "endpoint-overrides.json").write_text(json.dumps({
                        live()["core"]["Id"]: {"EndpointID": "changed-during-health"},
                    }))
                elif mode == "host-root-after-health":
                    shifted = rig["root"] / "shifted-runtime"
                    shifted.mkdir(mode=0o700)
                    candidate[0], executor[0] = (
                        replace(item, host_runtime=shifted) for item in (candidate[0], executor[0]))
                elif mode in ("process-after-health", "mount-order"):
                    changed = live()
                    if mode == "mount-order":
                        for item in changed.values():
                            item["Mounts"].reverse()
                    else:
                        changed["core"]["State"]["StartedAt"] = "different-native-process"
                    (rig["directory"] / "live.json").write_text(json.dumps(changed))
            return web.json_response(result)

        app = web.Application()
        app.router.add_post("/{tail:.*}", handler)
        async with TestServer(app, host="127.0.0.1") as server:
            desired = rig["selected"].to_dict()
            desired.update(core_port=server.port, outfitting_seconds=10)
            candidate.append(KomodoSelection(
                KomodoDeclaration.from_dict(desired), "fleet-a", "install-a", 1, "policy-1",
                harness.clock() + 1000, "e" * 64, runtime, rig["tooling"], rig["material"]))
            executor.append(replace(candidate[0], release_digest="f" * 64))
            prior_ref = None
            baseline = KomodoOwner(
                current=lambda: candidate[0], admission=harness.owner,
                authority="gateway-a", clock=harness.clock)
            if mode == "prior-failed" or "immutable-history-" in mode:
                candidate[0] = replace(candidate[0], release_digest="d" * 64)
                before = intent("prior-failed", generation=1, deployment=candidate[0].deployment_digest)
                harness.current = policy(enrollments=(actor(operations=(
                    OperationGrant("manager.converge", "host-a", 1, before.request_digest),
                )),))
                with pytest.raises(PlanError, match="current Core version"):
                    await asyncio.to_thread(baseline.converge, before, harness.admit(before))
                failure = await asyncio.to_thread(baseline.settle_failed, before, harness.admit(before))
                prior_ref = failure.to_dict()["result_ref"]
                prior_deployment = candidate[0].deployment_digest
                candidate[0] = replace(candidate[0], release_digest="e" * 64,
                                       generation=2, compatible_with=(prior_deployment,))
                executor[0] = replace(candidate[0], release_digest="f" * 64)
            generation = candidate[0].generation
            original = intent("retained-original", generation=1, deployment=candidate[0].deployment_digest)
            if generation != 1:
                original = intent("retained-original", generation=generation,
                                  deployment=candidate[0].deployment_digest)
            harness.current = policy(revision=2 if prior_ref is not None else 1, enrollments=(actor(operations=(
                OperationGrant("manager.converge", "host-a", generation, original.request_digest),
            )),))
            with pytest.raises(PlanError, match="current Core version"):
                await asyncio.to_thread(baseline.converge, original, harness.admit(original))
            expected_effects = "one\n" * (2 if prior_ref is not None else 1)
            assert (rig["directory"] / "effects").read_text() == expected_effects
            native_before = live()
            owner_root = runtime / candidate[0].resource
            retained = decode((owner_root / "selection.json").read_bytes())["attempt"]
            journal = NodeJournal(
                owner_root / "node.sqlite", authority="gateway-a", installation="install-a",
                target="host-a", existing_only=True)
            uncertain = journal.inspect(original)
            assert uncertain.to_dict()["status"] == "unknown"
            assert uncertain.to_dict()["sequence"] == 2 and uncertain.to_dict()["result_ref"] is None
            original_health_failure[0] = mode == "failed"

            def resolve(identity: Any, request: Any, role: str, installation: str) -> KomodoRepairSelection:
                resolver_calls.append((identity.subject, role, installation))
                if not transition["active"]:
                    raise PlanError("fixture repair transition withdrawn")
                if (identity.subject, role, installation, request.request_digest) != (
                        "principal-1", "client", "client-a", original.request_digest):
                    raise PlanError("fixture repair transition scope differs")
                return KomodoRepairSelection(
                    candidate[0], executor[0], original.request_digest, runtime_identity[0],
                    "repair-transition-a", transition["revision"], transition["expiry"])

            def repair_owner() -> KomodoRepairOwner:
                return KomodoRepairOwner(
                    resolve=resolve, admission=harness.owner, authority="gateway-a", clock=harness.clock)

            def call(*, status: bool = False) -> Any:
                return (repair_status if status else repair_bootstrap)(
                    repair_owner(), harness.identity(), selected_request,
                    role="client", installation="client-a", **({} if status else {"failed": mode == "failed"}))

            selected_request = original
            if mode == "success":
                baseline.current = lambda: executor[0]
                calls = (rig["directory"] / "calls.jsonl").read_bytes()
                with pytest.raises(PlanError, match="current selected owner content"):
                    await asyncio.to_thread(baseline.converge, original, harness.admit(original))
                with pytest.raises(PlanError, match="unsupported"):
                    repair_bootstrap(baseline, harness.identity(), original,
                                     role="client", installation="client-a")
                assert (rig["directory"] / "calls.jsonl").read_bytes() == calls
                status = await asyncio.to_thread(call, status=True)
                assert status["accepted"] == "unknown" and status["unresolved_effects"] is True
                assert status["candidate"] == status["observed"] == candidate[0].deployment_digest
                assert status["candidate_release"] == "e" * 64 and status["executor_release"] == "f" * 64
                assert status["desired"] == executor[0].deployment_digest != status["candidate"]
                assert status["installed"] is status["currently_healthy"] is True
                assert not status["converged"] and not status["native_mutations_performed"]

            if mode == "different-request":
                selected_request = intent("replacement-operation", generation=1,
                                          deployment=candidate[0].deployment_digest)
            elif mode == "different-generation":
                candidate[0] = replace(candidate[0], generation=2)
                executor[0] = replace(executor[0], generation=2)
            elif mode == "changed-candidate":
                value = candidate[0].declaration.to_dict()
                value["revision"] = "unselected-revision"
                candidate[0] = replace(candidate[0], declaration=KomodoDeclaration.from_dict(value))
                executor[0] = replace(executor[0], declaration=candidate[0].declaration)
            elif mode == "changed-executor":
                executor[0] = replace(executor[0], generation=2)
            elif mode == "same-executor":
                executor[0] = replace(executor[0], release_digest=candidate[0].release_digest)
            elif mode == "changed-material":
                rig["material"].admin_password.write_text("unselected-credential")
            elif mode == "inode-material":
                path = rig["material"].admin_password
                retained_path = path.with_name(path.name + ".retained")
                path.rename(retained_path)
                path.touch(mode=0o600)
                path.write_bytes(retained_path.read_bytes())
            elif mode == "wrong-fleet":
                harness.current = policy(fleet="other-fleet", enrollments=(actor(operations=(
                    OperationGrant("manager.converge", "host-a", 1, original.request_digest),
                )),))
                harness.owner = AdmissionAuthority(snapshot=lambda: harness.current, clock=harness.clock)
            elif mode == "expired-transition":
                transition["expiry"] = harness.clock()
            elif mode == "expired-candidate":
                candidate[0] = replace(candidate[0], valid_until=harness.clock())
            elif mode == "revoked-transition":
                transition["active"] = False
            elif mode == "revoked-grant":
                harness.current = policy(revision=2, enrollments=())
            elif mode == "missing-owner":
                owner_root.rename(owner_root.with_name("retained-owner"))
            elif mode.startswith("missing-"):
                path = (owner_root / {"missing-lock": "owner.lock", "missing-journal": "node.sqlite",
                                     "missing-selection": "selection.json"}[mode]
                        if mode != "missing-profile" else Path(retained["profile"]))
                path.rename(path.with_name(path.name + ".retained"))
            elif mode in ("unsettled", "not-fixed"):
                with KomodoState.open(runtime, daemon="daemon-a", project=desired["project"]) as state:
                    record = state.read()
                    record["attempt"]["native_settled" if mode == "unsettled" else "fixed_profile"] = False
                    state.save(record)
            elif mode == "changed-profile":
                Path(retained["profile"]).write_bytes(Path(retained["profile"]).read_bytes() + b"\n")
            elif mode == "changed-native":
                changed = live()
                changed["core"]["Mounts"][0]["Source"] = "/unselected"
                (rig["directory"] / "live.json").write_text(json.dumps(changed))
            elif mode == "wrong-journal":
                foreign = owner_root / "foreign.sqlite"
                foreign.touch(mode=0o600)
                NodeJournal(foreign, authority="other-authority", installation="install-a", target="host-a")
                (owner_root / "node.sqlite").rename(owner_root / "original.sqlite")
                foreign.rename(owner_root / "node.sqlite")
            elif mode == "state-after-health":
                def corrupt(record: Any) -> None:
                    record["attempt"]["native_settled"] = False
                def change_after_health(*args: Any, **kwargs: Any) -> Any:
                    health = ready(*args, **kwargs)
                    record = decode((owner_root / "selection.json").read_bytes())
                    corrupt(record)
                    (owner_root / "selection.json").write_text(json.dumps(record))
                    return health
                ready = KomodoOwner._ready
                monkeypatch.setattr(KomodoOwner, "_ready", change_after_health)

            fired: list[bool] = []
            if mode in CRASHES:
                if mode.startswith("native"):
                    target, name = KomodoNative, "prerequisites"
                elif mode.startswith("health"):
                    target, name = KomodoOwner, "_ready"
                elif mode.startswith("retain"):
                    target, name = KomodoState, "retain"
                elif mode.startswith(("save", "clear")):
                    target, name = KomodoState, "save"
                elif mode.startswith("complete"):
                    target, name = AdmissionAuthority, "complete"
                else:
                    target, name = NodeJournal, "_append" if mode == "append-revoke" else "reconcile"
                original_method = getattr(target, name)

                def interrupted(instance: Any, *args: Any, **kwargs: Any) -> Any:
                    selected = not fired
                    if name == "save":
                        value = args[0]
                        selected = selected and type(value["good"]) is dict and "repair" in value["good"]
                        selected = selected and ((value["attempt"] is None) == mode.startswith("clear"))
                    if name == "_append":
                        selected = selected and args[2].to_dict()["status"] == "succeeded"
                    if selected and mode.endswith("-before"):
                        fired.append(True)
                        raise PlanError("fixture publication interrupted")
                    if selected and mode == "journal-revoke":
                        fired.append(True)
                        transition["active"] = False
                    result = (original_method(*args, **kwargs) if name == "_append"
                              else original_method(instance, *args, **kwargs))
                    if selected and mode in ("append-revoke", "save-revoke"):
                        fired.append(True)
                        transition["active"] = False
                    elif selected and mode.endswith(("-after", "-after-health")):
                        fired.append(True)
                        raise PlanError("fixture publication interrupted")
                    return result

                monkeypatch.setattr(target, name, interrupted)

            if mode in DRIFTS:
                _, _, stage, change = mode.split("-", 3)
                target, name = ((NodeJournal, "_append") if stage == "append"
                                else (KomodoState, stage))
                original_method = getattr(target, name)

                def drifted(instance: Any, *args: Any, **kwargs: Any) -> Any:
                    selected = not fired
                    if name == "save":
                        selected = selected and type(args[0]["good"]) is dict and "repair" in args[0]["good"]
                    elif name == "_append":
                        selected = selected and args[2].to_dict()["status"] == "succeeded"
                    result = (original_method(*args, **kwargs) if name == "_append"
                              else original_method(instance, *args, **kwargs))
                    if selected:
                        fired.append(True)
                        if change == "host-runtime":
                            shifted = rig["root"] / "shifted-runtime"
                            shifted.mkdir(mode=0o700)
                            candidate[0], executor[0] = (
                                replace(item, host_runtime=shifted) for item in (candidate[0], executor[0]))
                            return result
                        if change.startswith(("immutable-outcome-", "immutable-history-")):
                            _, scope, corruption = change.split("-")
                            reference = prior_ref if scope == "history" else (
                                result if name == "retain" else digest(args[0]["good"])
                                if name == "save" else args[2].to_dict()["result_ref"])
                            path = owner_root / "receipts" / (reference + ".json")
                            path.rename(path.with_name(path.name + ".fixture-retained"))
                            if corruption == "corrupt":
                                path.touch(mode=0o600)
                                path.write_bytes(canonical({"changed": True}))
                            return result
                        if change in ("attachment", "disconnect"):
                            identity = live()["core"]["Id"]
                            path = rig["directory"] / (
                                "endpoint-overrides.json" if change == "attachment" else "excluded-endpoints.json")
                            changed = ({identity: {"EndpointID": "changed-endpoint"}}
                                       if change == "attachment" else [identity])
                        elif change in ("network", "endpoint"):
                            path = rig["directory"] / "network.json"
                            changed = json.loads(path.read_text())
                            if change == "network":
                                changed["IPAM"]["Driver"] = "changed-network"
                            else:
                                changed["Containers"]["f" * 64] = {"Name": "foreign-endpoint"}
                        else:
                            path = rig["directory"] / "live.json"
                            changed = live()
                            if change == "stop":
                                changed["core"]["State"].update(Status="exited", Running=False)
                            elif change == "restart":
                                changed["core"]["State"].update(Pid=9999, StartedAt="changed-process")
                            else:
                                changed["core"]["Mounts"][0]["Source"] = "/unselected"
                        path.write_text(json.dumps(changed))
                    return result

                monkeypatch.setattr(target, name, drifted)

            if mode == "status-endpoint-drift":
                status = await asyncio.to_thread(call, status=True)
                assert status["accepted"] == "unknown" and status["unresolved_effects"]
                assert status["installed"] is status["currently_healthy"] is None
                assert "original-candidate-custody-unproved" in status["blockers"]
                assert journal.inspect(original) == uncertain and not harness.owner._issued
            elif mode in REFUSALS:
                if mode == "busy-owner":
                    with KomodoState.open(runtime, daemon="daemon-a", project=desired["project"]):
                        with pytest.raises(PlanError, match="owner is busy"):
                            await asyncio.to_thread(call)
                else:
                    with pytest.raises((PlanError, AdmissionError)):
                        await asyncio.to_thread(call)
                assert not harness.owner._issued
                if mode not in ("missing-owner", "missing-journal", "wrong-journal"):
                    assert journal.inspect(original) == uncertain
                if mode in DRIFTS:
                    assert fired and len(journal.results()) == (5 if prior_ref is not None else 2)
                    assert decode((owner_root / "selection.json").read_bytes())["attempt"] == retained
                if mode == "host-root-after-health" or mode.endswith("host-runtime"):
                    assert not (rig["root"] / "shifted-runtime" / candidate[0].resource).exists()
                if mode == "missing-owner":
                    assert not owner_root.exists()
                elif mode == "missing-journal":
                    assert not (owner_root / "node.sqlite").exists()
                elif mode == "missing-lock":
                    assert not (owner_root / "owner.lock").exists()
                elif mode == "missing-selection":
                    assert not (owner_root / "selection.json").exists()
            else:
                if mode in CRASHES:
                    with pytest.raises((PlanError, AdmissionError)):
                        await asyncio.to_thread(call)
                    assert fired and not harness.owner._issued
                    assert journal.inspect(original).to_dict()["sequence"] in (2, 3)
                    transition["active"] = True
                receipt = await asyncio.to_thread(call)
                assert receipt.to_dict()["status"] == ("failed" if mode == "failed" else "succeeded")
                assert receipt.to_dict()["sequence"] == 3
                assert receipt.to_dict()["request_digest"] == original.request_digest
                assert await asyncio.to_thread(call) == receipt
                assert len(journal.results()) == (6 if prior_ref is not None else 3)
                value = decode((owner_root / "receipts" / (
                    receipt.to_dict()["result_ref"] + ".json")).read_bytes())
                assert value["deployment"] == original.to_dict()["parameters"]["deployment"]
                assert value["original_attempt"] == retained
                assert value["selection"]["release"] == value["repair"]["candidate_release"] == "e" * 64
                assert value["repair"]["executor_release"] == "f" * 64
                assert value["repair"]["executor_runtime"] == "9" * 64
                assert value["repair"]["native_mutations_performed"] is False
                if prior_ref is not None:
                    assert value["prior_outcomes"]["failed"] == prior_ref
                    prior_value = decode((owner_root / "receipts" / (prior_ref + ".json")).read_bytes())
                    assert prior_value["deployment"] in value["selection"]["compatible_with"]
                else:
                    assert value["prior_outcomes"] == {}
                assert decode((owner_root / "selection.json").read_bytes())["attempt"] is None
                if mode == "renewed":
                    assert value["repair"]["transition_revision"] == value["repair"]["admission_revision"] == 2
            assert CONTRACT == "fleet-manager.komodo-substrate-owner.v1"
            assert REPAIR_CONTRACT == "fleet-manager.komodo-original-repair.v1"
            assert sentinel.read_text() == "original-data"
            assert (rig["directory"] / "effects").read_text() == expected_effects
            assert not (rig["directory"] / "retire-effects").exists()
            if mode not in ("mount-order", "changed-native", "process-after-health", *DRIFTS):
                assert live() == native_before
            if mode not in ("revoked-transition",):
                assert resolver_calls

    asyncio.run(exercise())
