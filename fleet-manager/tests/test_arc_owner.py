"""Pure protocol matrix and selected native boundaries, not live deployment proof."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import inspect
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from fleet_manager.arc_owner import ArcDeployment, ArcHelmOwner, ArcTooling, MAX_HELM_DEPTH
from fleet_manager.loadout_preparation import PreparationStore
from fleet_manager.loadouts import design_loadout
from fleet_manager.node_journal import NodeJournal
from fleet_manager.operations import OperationIntent, TargetReceipt
from fleet_manager.planning import PlanError, canonical, digest
from fleet_manager.runtime_admission import AdmissionAuthority, AdmissionError, OperationGrant
from test_loadouts import inputs
from test_runtime_admission import Harness, actor, policy
from test_runtime_identity import NOW
from test_runtime_identity import keys as keys


def _helm_protocol(args, root, input_text):
    import json
    from pathlib import Path

    with (root / "commands.jsonl").open("a") as out:
        out.write(json.dumps(args) + "\n")
    state, live = root / "release.json", root / "live-resource.json"

    def option(name):
        return args[args.index(name) + 1]

    if args[0] == "list":
        return 0, json.dumps([] if not state.exists() else [{
            "name": option("--filter").strip("^$"), "namespace": option("--namespace"),
        }])
    if args[0] == "status":
        status = json.loads(state.read_text())
        if "--show-resources" in args:
            objects = []
            if live.exists():
                values = json.loads(live.read_text())
                objects.append({
                    "apiVersion": "actions.github.com/v1alpha1", "kind": "AutoscalingRunnerSet",
                    "metadata": {"name": status["name"], "namespace": status["namespace"],
                                 "uid": "actual-resource", "resourceVersion": "1"},
                    "spec": {key: values[key] for key in (
                        "githubConfigUrl", "githubConfigSecret", "runnerGroup", "runnerScaleSetName",
                        "minRunners", "maxRunners", "template",
                    )},
                })
            status["info"]["resources"] = {"v1alpha1/AutoscalingRunnerSet": objects}
        return 0, json.dumps(status)
    if args[:2] == ["get", "values"]:
        return 0, json.dumps(json.loads(state.read_text())["config"])
    if args[0] == "upgrade":
        assert Path(args[2]).read_bytes() == b"selected-fixture-chart"
        values = json.loads(input_text)
        assert isinstance(values["githubConfigSecret"], str)
        previous = json.loads(state.read_text()) if state.exists() else {}
        value = {
            "name": args[1], "namespace": option("--namespace"),
            "version": previous.get("version", 0) + 1,
            "info": {"status": "deployed", "description": option("--description")},
            "config": values,
        }
        state.write_text(json.dumps(value))
        if not live.exists() or previous.get("config") != values:
            live.write_text(json.dumps(values))
        return (7, "") if (root / "lost-reply").exists() else (0, "{}")
    return 9, ""


@pytest.fixture
def rig(tmp_path, monkeypatch, keys, request):
    native_protocol = bool(getattr(request, "param", False))
    now = int(time.time()) if getattr(request, "param", False) == "wall-clock-native" else NOW
    root = Path(__file__).absolute().parents[2]
    monkeypatch.syspath_prepend(str(root / "libs" / "agent-procutil" / "src"))
    from agent_procutil.owned_process import run_caller_owned

    harness = Harness(keys)
    harness.clock.value = now
    config, inventory, catalog = inputs(now)
    plan = design_loadout(config, inventory, catalog, now=now)
    store = PreparationStore(tmp_path / "preparation.db", fleet="example", installation="install-a")
    candidate = {
        "schema": "fleet-manager.loadout-candidate", "version": 1,
        "fleet": "example", "installation": "install-a", "status": "prepared", "reason": None,
        "sources": {}, "plan_digest": hashlib.sha256(canonical(plan, output=True)).hexdigest(),
        "plan_bytes": len(canonical(plan, output=True)),
        "inventory_expires_at": inventory["expires_at"], "execution_admission": "not-implemented",
    }
    proposal = store.publish(candidate, plan, expected=0, now=now)
    store.confirm(generation=1, selected_digest=proposal["digest"], now=now, reviewer={
        "principal": "actor-a", "account": "account-a", "policy_revision": "policy-1",
    })
    allocation = plan["composition"]["allocations"]["runners"]
    chart = b"selected-fixture-chart"
    value = {
        "schema": "fleet-manager.arc-deployment", "version": 1,
        "fleet": "example", "installation": "install-a", "target": "worker-a",
        "allocation": "runners", "generation": 1, "preparation": proposal["digest"],
        "declaration": digest(allocation), "release_digest": "a" * 64,
        "policy_revision": "selection-1", "valid_until": now + 100,
        "outfitting_seconds": 20, "chart_sha256": hashlib.sha256(chart).hexdigest(),
        "references": {name: allocation["slots"][name] for name in (
            "artifact_ref", "configuration_ref", "credential_ref", "forge_scope_ref",
            "runner_group_ref", "trust_policy_ref", "toolchain_ref", "cluster",
        )},
        "support": {"support": {"target": "control-a", "receipt": "b" * 64}},
        "settings": {
            "namespace": "arc-runners", "release": "example-runners",
            "controller_namespace": "arc-system", "controller_service_account": "arc-controller",
            "runner_service_account": "existing-restricted-runner",
            "node": "node-a", "github_url": "https://github.com/example/repository",
            "runner_group": "restricted-group", "credential_secret": "existing-app",
            "runner_image": "ghcr.io/example/runner@sha256:" + "c" * 64,
        },
    }
    selected = [ArcDeployment.from_dict(value, chart=chart)]
    executable = Path(sys.executable).absolute()
    script = tmp_path / "helm-fixture.py"
    script.write_text(
        "import sys\nfrom pathlib import Path\n" + inspect.getsource(_helm_protocol)
        + "\ncode, output = _helm_protocol(sys.argv[1:], Path.cwd(), sys.stdin.read())\n"
        "print(output)\nraise SystemExit(code)\n"
    )
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("fixture-only")

    def native(argv, **kwargs):
        if native_protocol:
            return run_caller_owned([str(executable), str(script), *argv[1:]], **kwargs)
        code, output = _helm_protocol(argv[1:], Path(kwargs["cwd"]), kwargs["input_text"])
        kwargs["on_settled"]()
        return subprocess.CompletedProcess(argv, code, output, "")

    journal = NodeJournal(
        tmp_path / "node.db", authority="gateway-a", installation="install-a", target="worker-a",
    )
    tooling = ArcTooling(executable, kubeconfig, "cluster-a", tmp_path, native)
    owner = ArcHelmOwner(
        tooling=tooling, preparations=store, journal=journal,
        admission=harness.owner, current=lambda: selected[0], clock=harness.clock,
    )

    def request(operation="runner-1", **changes):
        return OperationIntent.from_dict({
            "schema": "fleet-manager.operation-intent", "version": 1,
            "authority": "gateway-a", "principal": "actor-a", "account": "account-a",
            "operation": operation, "target": "worker-a", "generation": 1,
            "command": "runner.converge", "parameters": {
                "preparation": selected[0].to_dict()["preparation"], "allocation": "runners",
                "deployment": selected[0].deployment_digest,
            }, **changes,
        })

    def permit(intent):
        return harness.admit(intent)

    harness.current = policy(fleet="example", valid_from=now - 1,
                            valid_until=now + 86400 * 31,
                            identity_trust_until=now + 86400 * 31,
                            enrollments=(actor(operations=(
        OperationGrant("runner.converge", "worker-a", 1, request().request_digest),
    )),))
    return {
        "owner": owner, "harness": harness, "selected": selected, "value": value,
        "chart": chart, "request": request, "permit": permit, "store": store,
        "journal": journal, "root": tmp_path, "allocation": allocation,
        "native_protocol": native_protocol,
    }


def commands(rig):
    path = rig["root"] / "commands.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_live_status_envelope_has_explicit_byte_depth_and_duplicate_key_bounds():
    value = "leaf"
    for _ in range(MAX_HELM_DEPTH):
        value = {"nested": value}
    assert ArcHelmOwner._json(json.dumps(value)) == value
    for raw in (
        json.dumps({"extra": {"extra": value}}),
        '{"repeated":1,"repeated":2}',
        '{"nonfinite":NaN}',
        json.dumps([0] * 4097),
        " " * (1024 * 1024 + 1),
    ):
        with pytest.raises(PlanError):
            ArcHelmOwner._json(raw)


@pytest.mark.parametrize("rig", [True], indirect=True)
def test_real_native_boundary_converges_and_repeat_does_not_replay(rig):
    assert rig["native_protocol"]
    request = rig["request"]()
    result = rig["owner"].converge(request, rig["permit"](request))
    result.check_intent(request)
    assert result.to_dict()["status"] == "succeeded"
    assert rig["owner"].converge(request, rig["permit"](request)) == result
    emitted = commands(rig)
    upgrades = [entry for entry in emitted if entry[0] == "upgrade"]
    assert len(upgrades) == 1
    assert all(flag in upgrades[0] for flag in (
        "--atomic", "--wait", "--reset-values", "--no-hooks", "--skip-crds",
    ))
    assert all(flag not in upgrades[0] for flag in (
        "--take-ownership", "--force", "--create-namespace", "--dependency-update",
    ))
    observed = json.loads((rig["root"] / "release.json").read_text())
    values = observed["config"]
    assert (values["minRunners"], values["maxRunners"]) == (0, 2)
    pod = values["template"]["spec"]
    assert pod["nodeSelector"] == {
        "kubernetes.io/hostname": "node-a", "kubernetes.io/arch": "amd64",
    }
    assert pod["automountServiceAccountToken"] is False
    assert pod["containers"][0]["resources"]["requests"] == {
        "cpu": "1000m", "memory": "2048", "ephemeral-storage": "4096",
    }
    assert not list(rig["root"].glob("fleet-arc-*"))


@pytest.mark.parametrize("rig", [True], indirect=True)
def test_lost_native_reply_survives_restart_and_new_operation_cannot_take_custody(rig):
    assert rig["native_protocol"]
    request = rig["request"]()
    (rig["root"] / "lost-reply").touch()
    with pytest.raises(PlanError, match="uncertain"):
        rig["owner"].converge(request, rig["permit"](request))
    restarted = NodeJournal(
        rig["journal"].path, authority="gateway-a", installation="install-a", target="worker-a",
    )
    assert restarted.inspect(request).to_dict()["status"] == "unknown"
    rig["owner"].journal = restarted
    count = len(commands(rig))
    assert rig["owner"].converge(request, rig["permit"](request)).to_dict()["status"] == "unknown"
    assert len(commands(rig)) == count
    other = rig["request"]("new-operation")
    rig["harness"].current = policy(fleet="example", revision=2, enrollments=(actor(operations=(
        OperationGrant("runner.converge", "worker-a", 1, other.request_digest),
    )),))
    with pytest.raises(PlanError, match="unsettled"):
        rig["owner"].converge(other, rig["permit"](other))
    assert len([entry for entry in commands(rig) if entry[0] == "upgrade"]) == 1


@pytest.mark.parametrize("change", [
    {"target": "other"}, {"preparation": "d" * 64}, {"declaration": "e" * 64},
    {"support": {}}, {"support": {"support": {"target": "wrong", "receipt": "b" * 64}}},
    {"valid_until": NOW}, {"references": {}},
])
def test_mismatched_or_expired_current_owner_inputs_refuse_before_native_work(rig, change):
    value = {**rig["value"], **change}
    with pytest.raises(PlanError):
        rig["selected"][0] = ArcDeployment.from_dict(value, chart=rig["chart"])
        request = rig["request"]()
        rig["harness"].current = policy(fleet="example", enrollments=(actor(operations=(
            OperationGrant("runner.converge", "worker-a", 1, request.request_digest),
        )),))
        rig["owner"].converge(request, rig["permit"](request))
    assert commands(rig) == []


def test_exact_preparation_review_is_not_a_deployment_permit(rig):
    request = rig["request"]()
    rig["harness"].current = policy(fleet="example", revision=2, enrollments=(actor(operations=()),))
    with pytest.raises(AdmissionError, match="forbidden"):
        rig["permit"](request)
    assert commands(rig) == []
    with pytest.raises(AdmissionError, match="unknown-permit"):
        rig["owner"].converge(request, object())
    assert commands(rig) == []


def test_runner_grant_requires_exact_content_not_just_action_target_and_generation(rig):
    with pytest.raises(AdmissionError, match="invalid-record"):
        OperationGrant("runner.converge", "worker-a", 1)
    alias = rig["request"]("another-identity")
    with pytest.raises(AdmissionError, match="forbidden"):
        rig["permit"](alias)
    changed = copy.deepcopy(rig["value"])
    changed["settings"]["runner_image"] = "ghcr.io/example/runner@sha256:" + "d" * 64
    rig["selected"][0] = ArcDeployment.from_dict(changed, chart=rig["chart"])
    changed_request = rig["request"]()
    with pytest.raises(AdmissionError, match="forbidden"):
        rig["permit"](changed_request)
    assert commands(rig) == []


def test_policy_revocation_at_effect_boundary_prevents_mutation(rig):
    request = rig["request"]()
    permit = rig["permit"](request)
    original = rig["owner"].current
    calls = 0

    def current():
        nonlocal calls
        calls += 1
        if calls == 2:
            rig["harness"].current = policy(fleet="example", revision=2, enrollments=(actor(operations=()),))
        return original()

    rig["owner"].current = current
    with pytest.raises(AdmissionError, match="forbidden"):
        rig["owner"].converge(request, permit)
    assert all(entry[0] != "upgrade" for entry in commands(rig))
    assert rig["journal"].inspect(request).to_dict()["status"] == "accepted"


def test_foreign_helm_release_is_never_taken_over(rig):
    (rig["root"] / "release.json").write_text(json.dumps({
        "name": "example-runners", "namespace": "arc-runners", "version": 1,
        "info": {"status": "deployed", "description": "foreign-owner"}, "config": {},
    }))
    request = rig["request"]()
    with pytest.raises(PlanError, match="not owned"):
        rig["owner"].converge(request, rig["permit"](request))
    assert all(entry[0] != "upgrade" for entry in commands(rig))


@pytest.mark.parametrize("failure", ["selection", "preflight", "busy"])
def test_failed_request_does_not_leave_the_only_authorization_slot_occupied(rig, failure):
    harness = rig["harness"]
    harness.owner = AdmissionAuthority(snapshot=harness.snapshot, clock=harness.clock, capacity=1)
    rig["owner"].admission = harness.owner
    request = rig["request"]()
    permit = rig["permit"](request)
    if failure == "selection":
        def unavailable():
            raise PlanError("selected source unavailable")
        rig["owner"].current = unavailable
    elif failure == "busy":
        assert rig["owner"]._lock.acquire(blocking=False)
    else:
        (rig["root"] / "release.json").write_text(json.dumps({
            "name": "example-runners", "namespace": "arc-runners", "version": 1,
            "info": {"status": "deployed", "description": "foreign-owner"}, "config": {},
        }))
    try:
        with pytest.raises(PlanError):
            rig["owner"].converge(request, permit)
    finally:
        if failure == "busy":
            rig["owner"]._lock.release()
    fresh = rig["permit"](request)
    assert harness.owner.discard(fresh)


@pytest.mark.parametrize("drift", ["none", "stored-values", "live-spec", "missing-live-resource"])
def test_new_admitted_generation_reapplies_without_trusting_stored_helm_values(rig, drift):
    request = rig["request"]()
    rig["owner"].converge(request, rig["permit"](request))
    live = rig["root"] / "live-resource.json"
    original = live.read_bytes()
    if drift in ("stored-values", "live-spec"):
        path = rig["root"] / ("release.json" if drift == "stored-values" else "live-resource.json")
        observed = json.loads(path.read_text())
        if drift == "stored-values":
            observed["config"]["maxRunners"] = 99
        else:
            observed["maxRunners"] = 99
        path.write_text(json.dumps(observed))
    elif drift == "missing-live-resource":
        live.unlink()
    store = rig["store"]
    old = store.read()
    candidate = {**old["candidate"], "sources": {"config": {"revision": "new-published-source"}}}
    proposal = store.publish(candidate, old["plan"], expected=1, now=NOW)
    store.confirm(generation=2, selected_digest=proposal["digest"], now=NOW, reviewer={
        "principal": "actor-a", "account": "account-a", "policy_revision": "policy-1",
    })
    selected = {**rig["value"], "generation": 2, "preparation": proposal["digest"]}
    rig["selected"][0] = ArcDeployment.from_dict(selected, chart=rig["chart"])
    next_request = rig["request"]("next", generation=2)
    rig["harness"].current = policy(fleet="example", revision=2, enrollments=(actor(operations=(
        OperationGrant("runner.converge", "worker-a", 2, next_request.request_digest),
    )),))
    if drift == "live-spec":
        with pytest.raises(PlanError, match="live resource differs"):
            rig["owner"].converge(next_request, rig["permit"](next_request))
        assert rig["journal"].inspect(next_request).to_dict()["status"] == "unknown"
        assert json.loads(live.read_text())["maxRunners"] == 99
    else:
        result = rig["owner"].converge(next_request, rig["permit"](next_request))
        assert result.to_dict()["status"] == "succeeded"
        assert live.read_bytes() == original
    assert len([entry for entry in commands(rig) if entry[0] == "upgrade"]) == 2


def test_foreign_fleet_permit_refuses_before_journal_custody_or_native_work(rig):
    request = rig["request"]()
    rig["harness"].current = policy(fleet="another-fleet", enrollments=(actor(operations=(
        OperationGrant("runner.converge", "worker-a", 1, request.request_digest),
    )),))
    permit = rig["permit"](request)
    with pytest.raises(PlanError, match="permit fleet"):
        rig["owner"].converge(request, permit)
    assert rig["journal"].inspect(request) is None
    assert commands(rig) == []
    with sqlite3.connect(rig["journal"].path) as connection:
        assert connection.execute("SELECT count(*) FROM external_owners").fetchone()[0] == 0


def test_live_evidence_checks_all_owned_scope_capacity_and_workload_fields(rig):
    request = rig["request"]()
    rig["owner"].converge(request, rig["permit"](request))
    selected = rig["selected"][0]
    status = rig["owner"]._inspect(selected, deadline=time.monotonic() + 10)[0]
    values = rig["owner"]._values(selected, rig["allocation"])
    metadata = status["info"]["resources"]["v1alpha1/AutoscalingRunnerSet"][0]["metadata"]
    metadata["managedFields"] = [{
        "manager": "helm", "operation": "Update", "apiVersion": "actions.github.com/v1alpha1",
        "fieldsType": "FieldsV1", "fieldsV1": {
            "f:spec": {"f:template": {"f:spec": {"f:containers": {
                'k:{"name":"runner"}': {"f:resources": {"f:limits": {
                    "f:ephemeral-storage": {}, "f:memory": {},
                }}},
            }}}},
        },
    }]
    decoded = ArcHelmOwner._json(json.dumps(status))
    assert decoded == status
    assert rig["owner"]._live_resource(decoded, selected, values)["uid"] == "actual-resource"
    changes = [
        (("metadata", "namespace"), "wrong"),
        (("metadata", "uid"), None),
        (("metadata", "deletionTimestamp"), "2026-10-24T12:00:00Z"),
        (("spec", "minRunners"), True),
        (("spec", "maxRunners"), 99),
        (("spec", "githubConfigSecret"), "unrelated-secret"),
        (("spec", "template", "spec", "automountServiceAccountToken"), True),
        (("spec", "template", "spec", "serviceAccountName"), "privileged-account"),
        (("spec", "template", "spec", "hostNetwork"), True),
        (("spec", "template", "spec", "nodeSelector"), {}),
        (("spec", "template", "spec", "containers", 0, "image"), "unselected:latest"),
        (("spec", "template", "spec", "containers", 0, "resources"), {}),
        (("spec", "template", "spec", "containers", 0, "securityContext"), {}),
    ]
    for path, replacement in changes:
        changed = copy.deepcopy(status)
        node = changed["info"]["resources"]["v1alpha1/AutoscalingRunnerSet"][0]
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = replacement
        with pytest.raises(PlanError):
            rig["owner"]._live_resource(changed, selected, values)
    for resources in ({}, {"v1alpha1/AutoscalingRunnerSet": []}):
        changed = copy.deepcopy(status)
        changed["info"]["resources"] = resources
        with pytest.raises(PlanError, match="live"):
            rig["owner"]._live_resource(changed, selected, values)


def test_generation_migration_and_unsettled_resource_ownership(rig):
    store, request = rig["journal"], rig["request"]()
    store.accept_named_external(request, resource="physical-release")
    assert store.begin_external(request)
    next_request = rig["request"]("next", generation=2)
    with pytest.raises(PlanError, match="unsettled"):
        store.accept_named_external(next_request, resource="physical-release")
    value = request.to_dict()
    del value["parameters"]
    value.update(schema="fleet-manager.target-receipt", request_digest=request.request_digest,
                 status="succeeded", sequence=3, result_ref="reconciled-by-owner")
    store.reconcile(request, TargetReceipt.from_dict(value))
    with pytest.raises(PlanError, match="newer generation"):
        store.accept_named_external(rig["request"]("alias"), resource="physical-release")
    assert store.accept_named_external(next_request, resource="physical-release").to_dict()["status"] == "accepted"
    with pytest.raises(PlanError, match="no longer owns"):
        store.accept_named_external(request, resource="physical-release")
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE external_owners")
        connection.execute("PRAGMA user_version=3")
    reopened = NodeJournal(store.path, authority="gateway-a", installation="install-a", target="worker-a")
    assert reopened.inspect(request).to_dict()["status"] == "succeeded"


def test_two_installed_processes_cannot_claim_one_external_release(rig):
    from agent_procutil.owned_process import run_caller_owned

    first, second = rig["request"](), rig["request"]("contender", generation=2)
    worker = """
import json,sys,time
from pathlib import Path
from fleet_manager.node_journal import NodeJournal
from fleet_manager.operations import OperationIntent
from fleet_manager.planning import PlanError
path,encoded,ready,go=sys.argv[1:]
node=NodeJournal(path,authority='gateway-a',installation='install-a',target='worker-a')
Path(ready).touch()
deadline=time.monotonic()+10
while not Path(go).exists():
    if time.monotonic()>=deadline: raise TimeoutError()
    time.sleep(.01)
intent=OperationIntent.from_dict(json.loads(encoded))
try:
    node.accept_named_external(intent,resource='one-physical-release')
    assert node.begin_external(intent)
except PlanError:
    print('blocked')
else:
    print('unknown-owner')
"""
    # Both native processes open the same canonical database before admission.
    processes = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for index, request in enumerate((first, second)):
            processes.append(pool.submit(run_caller_owned, [
                sys.executable, "-I", "-c", worker, str(rig["journal"].path),
                request.encoded.decode(), str(rig["root"] / f"ready-{index}"),
                str(rig["root"] / "go"),
            ], cwd=rig["root"], env=dict(os.environ), timeout=15,
                max_stdout_bytes=1024, max_stderr_bytes=4096))
        deadline = time.monotonic() + 10
        while not all((rig["root"] / f"ready-{i}").exists() for i in range(2)):
            assert time.monotonic() < deadline
            time.sleep(.01)
        (rig["root"] / "go").touch()
        results = [future.result() for future in processes]
    assert all(result.returncode == 0 for result in results)
    assert sorted(result.stdout.strip() for result in results) == ["blocked", "unknown-owner"]


@pytest.mark.parametrize("setting,value", [
    ("credential_secret", {"github_token": "not-allowed"}), ("runner_image", "runner:latest"),
    ("github_url", "https://user:secret@example.invalid/repository"),
    ("github_url", "https://example.invalid/repository?token=secret"),
    ("namespace", "arc-system"), ("release", "--take-ownership"),
    ("runner_group", "true"), ("runner_group", "0123"), ("release", "yes"),
    ("namespace", "123"), ("credential_secret", "false"),
    ("controller_service_account", "null"), ("runner_service_account", "no"),
])
def test_unsafe_selected_settings_are_rejected_without_native_work(rig, setting, value):
    selected = copy.deepcopy(rig["value"])
    selected["settings"][setting] = value
    with pytest.raises(PlanError):
        ArcDeployment.from_dict(selected, chart=rig["chart"])
    assert commands(rig) == []
