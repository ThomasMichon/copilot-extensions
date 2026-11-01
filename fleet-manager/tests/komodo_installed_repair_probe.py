"""Installed-only repair bodies; the parent prepends its noneditable SDK guard."""

from __future__ import annotations

UNSUPPORTED_PROBE = r"""
import fleet_manager.komodo_control as control
assert control.CONTRACT == "fleet-manager.komodo-substrate-owner.v1"
assert not hasattr(control, "REPAIR_CONTRACT")
assert not hasattr(control, "repair_bootstrap") and not hasattr(control, "repair_status")
print(json.dumps({"result": "unsupported-installed-repair", "source_denied": True}))
"""

CHECKPOINT_UNSUPPORTED_PROBE = r"""
import fleet_manager.komodo_control as control
from fleet_manager.planning import PlanError
assert control.REPAIR_CONTRACT == "fleet-manager.komodo-original-repair.v1"
assert callable(control.repair_bootstrap) and callable(control.repair_status)
def required_checkpoint_owner():
    if getattr(control, "CHECKPOINT_REPAIR_CONTRACT", None) != (
            "fleet-manager.komodo-original-repair-checkpoint-auth.v1"):
        raise PlanError("required checkpoint support unavailable")
    raise AssertionError("unsupported SDK reached new owner construction")
try:
    required_checkpoint_owner()
except PlanError as error:
    assert str(error) == "required checkpoint support unavailable"
else:
    raise AssertionError("required checkpoint support silently downgraded")
print(json.dumps({"result": "unsupported-installed-checkpoint", "source_denied": True}))
"""

REPAIR_PROBE = r"""
from dataclasses import replace
from types import SimpleNamespace
from agent_procutil.owned_process import run_caller_owned
import fleet_manager.komodo_control as control
import fleet_manager.komodo_state as state_module
from fleet_manager.komodo_declaration import KomodoDeclaration
from fleet_manager.komodo_native import KomodoTooling
from fleet_manager.komodo_owner import KomodoOwner
from fleet_manager.komodo_profile import KomodoMaterialization
from fleet_manager.komodo_repair import KomodoRepairOwner
from fleet_manager.komodo_repair_selection import KomodoRepairSelection
from fleet_manager.komodo_selection import KomodoSelection
from fleet_manager.komodo_state import KomodoState
from fleet_manager.machine_identity import (
    MachineBinding, MachineIdentityVerifier, PROFILE, make_machine_proof,
)
from fleet_manager.node_journal import NodeJournal
from fleet_manager.operations import OperationIntent
from fleet_manager.planning import PlanError, canonical, decode, digest
from fleet_manager.runtime_admission import (
    AdmissionAuthority, AdmissionError, AuthoritySnapshot, Enrollment, OperationGrant,
)
from joserfc.jwk import ECKey

assert control.REPAIR_CONTRACT == "fleet-manager.komodo-original-repair.v1"
assert callable(control.repair_bootstrap) and callable(control.repair_status)
now = int(time.time())
clock = lambda: int(time.time())
checkpoint_auth = context.get("checkpoint_auth", False)
if checkpoint_auth:
    assert control.CHECKPOINT_REPAIR_CONTRACT == (
        "fleet-manager.komodo-original-repair-checkpoint-auth.v1")
    class AdvancingClock:
        def __init__(self):
            self.started, self.offset = time.monotonic(), 0
        def monotonic(self):
            return time.monotonic() + self.offset
        def __call__(self):
            return now + int(self.monotonic() - self.started)
    clock = AdvancingClock()
    import fleet_manager.komodo_repair as repair_module
    import fleet_manager.komodo_native as native_module
    import fleet_manager.komodo_health as health_module
    import fleet_manager.komodo_owner as owner_module
    for module in (repair_module, native_module, health_module, owner_module):
        module.time = SimpleNamespace(monotonic=clock.monotonic)
material = KomodoMaterialization(**{key: Path(value) for key, value in context["material"].items()})
def native(argv, **kwargs):
    return run_caller_owned([
        context["native_python"], context["native_script"], *argv[1:],
    ], **kwargs)
tooling = KomodoTooling(
    Path(context["native_python"]), Path(context["client_config"]), "daemon-a", native)
candidate = KomodoSelection(
    KomodoDeclaration.from_dict(context["declaration"]), "fleet-a", "install-a", 1,
    "policy-1", context["valid_until"], context["candidate_release"],
    Path(context["host_runtime"]), tooling, material)
assert digest(context["expected"]) == context["executor_release"]
executor = replace(candidate, release_digest=context["executor_release"])
executor_runtime = digest({"runtime": str(runtime), "modules": context["expected"]})
request = OperationIntent.from_dict(context["original"])
assert request.to_dict()["parameters"] == {"deployment": candidate.deployment_digest}
key = ECKey.generate_key("P-256")
binding = MachineBinding(
    "operator-a", "caller", "bootstrap", canonical(key.as_dict(private=False)), now + 600)
verifier = MachineIdentityVerifier(
    audience="manager-owner", bindings=(binding,), trust_valid_until=now + 600, clock=clock)
proofs = []
original_identity = None
def signed_identity(action):
    uri = "https://manager.example/komodo/repair/" + action
    proof = make_machine_proof(
        key, principal="operator-a", audience="manager-owner", kind="caller", resource="bootstrap",
        method="POST", target_uri=uri,
        nonce=verifier.nonce, body=request.encoded, now=clock())
    actual = verifier.verify(
        "operator-a", proof, method="POST", target_uri=uri,
        kind="caller", resource="bootstrap", content_digest=request.request_digest)
    assert actual.proof_key != context["original_proof_key"]
    proofs.append((action, actual.evidence))
    return actual
def identity(action="status"):
    global original_identity
    original_identity = signed_identity(action)
    return original_identity
revision = 2 if context.get("resume") and context["mode"] == "save-partial-renewed" else 1
snapshot = AuthoritySnapshot(
    "gateway-a", "fleet-a", PROFILE, "manager-owner", revision, now - 1, now + 600, now + 600,
    (Enrollment(
        "operator-a", "actor-a", "account-a", "operator", "client", "client-a", 1, "active",
        operations=(OperationGrant(
            "manager.converge", "host-a", 1,
            "0" * 64 if context["mode"] == "changed-grant" else request.request_digest),),
    ),), identity_profile=PROFILE)
admission = AdmissionAuthority(snapshot=lambda: snapshot, clock=clock)
resolutions = []
def resolve(actual, original, role, installation):
    if checkpoint_auth:
        clock.offset += 1
    assert verifier.current(actual)
    assert (actual.subject, original.encoded, role, installation) == (
        "operator-a", request.encoded, "client", "client-a")
    if context["mode"] == "withdrawn-transition":
        raise PlanError("installed repair transition withdrawn")
    resolutions.append(actual.evidence)
    return KomodoRepairSelection(
        candidate, executor, request.request_digest, executor_runtime,
        "current-installed-repair", revision, context["valid_until"])
fresh_proofs = []
def fresh_identity(initial, original, role, installation, action):
    assert initial is original_identity
    assert verifier.current(initial)
    assert (original.encoded, role, installation) == (request.encoded, "client", "client-a")
    assert action in ("status", "succeeded", "failed")
    assert not admission._issued
    actual = signed_identity(action)
    fresh_proofs.append((action, actual.evidence))
    return actual
options = {"fresh_identity": fresh_identity} if checkpoint_auth else {}
owner = KomodoRepairOwner(
    resolve=resolve, admission=admission, authority="gateway-a", clock=clock, **options)
root = Path(context["host_runtime"]) / candidate.resource
journal = NodeJournal(
    root / "node.sqlite", authority="gateway-a", installation="install-a", target="host-a",
    existing_only=True)
before = journal.inspect(request)
mode = context["mode"]
resuming = context.get("resume", False)
assert (before.to_dict()["status"], before.to_dict()["sequence"]) == (
    ("succeeded", 3) if resuming and mode == "journal-after" else ("unknown", 2))
original_rows = tuple(journal.results())[:2]
original_attempt = decode((root / "selection.json").read_bytes())["attempt"]
if mode == "executor-skew":
    executor = replace(executor, generation=2)
try:
    started, before_fresh = clock(), len(fresh_proofs)
    status = control.repair_status(owner, identity(), request, role="client", installation="client-a")
except (PlanError, AdmissionError) as error:
    assert mode in ("changed-grant", "withdrawn-transition", "executor-skew"), str(error)
    assert journal.inspect(request) == before and tuple(journal.results()) == original_rows
    result = "repair-refused"
else:
    assert mode in ("success", "save-after", "journal-after", "save-partial-renewed")
    assert status["candidate"] == status["observed"] == candidate.deployment_digest
    assert status["desired"] == executor.deployment_digest != status["candidate"]
    assert status["candidate_release"] == context["candidate_release"]
    assert status["executor_release"] == context["executor_release"]
    assert status["executor_runtime"] == executor_runtime
    assert status["accepted"] == ("succeeded" if resuming and mode == "journal-after" else "unknown")
    assert status["unresolved_effects"] is (status["accepted"] == "unknown")
    assert status["installed"] and status["currently_healthy"], status
    assert not status["converged"] and not status["native_mutations_performed"]
    if checkpoint_auth:
        assert 30 < clock() - started < context["declaration"]["outfitting_seconds"]
        assert len(fresh_proofs) - before_fresh == 5
    baseline = KomodoOwner(current=lambda: executor, admission=admission, authority="gateway-a", clock=clock)
    try:
        control.repair_bootstrap(baseline, identity("succeeded"), request, role="client", installation="client-a")
    except PlanError as error:
        assert "unsupported" in str(error)
    else:
        raise AssertionError("baseline owner acquired repair authority")
    fired = []
    target = KomodoState if mode == "save-after" else NodeJournal
    name = "save" if mode == "save-after" else "reconcile"
    method = getattr(target, name)
    def interrupt(instance, *args, **kwargs):
        selected = not fired
        if name == "save":
            selected = selected and isinstance(args[0]["good"], dict) and "repair" in args[0]["good"]
        value = method(instance, *args, **kwargs)
        if selected:
            fired.append(True)
            raise PlanError("installed publication interrupted")
        return value
    partial = []
    if mode == "save-partial-renewed":
        target, name = state_module, "_complete_pending"
        method = getattr(target, name)
        def interrupt(path, content):
            value = decode(content)
            if not fired and path.parent == root and isinstance(value.get("good"), dict):
                fired.append(True)
                method(path, content[:-1])
                partial.append((str(path), digest(value["good"])))
                raise PlanError("installed publication interrupted")
            return method(path, content)
    if mode != "success" and not resuming:
        setattr(target, name, interrupt)
        try:
            control.repair_bootstrap(owner, identity("succeeded"), request, role="client", installation="client-a")
        except PlanError as error:
            assert "installed publication interrupted" in str(error) and fired
        else:
            raise AssertionError("publication control did not interrupt")
        finally:
            setattr(target, name, method)
        assert journal.inspect(request).to_dict()["sequence"] == (3 if mode == "journal-after" else 2)
        assert not admission._issued
        print(json.dumps({
            "result": "publication-interrupted", "source_denied": True,
            **({"pending_path": partial[0][0], "pending_ref": partial[0][1]} if partial else {}),
        }))
        raise SystemExit(0)
    started, before_fresh = clock(), len(fresh_proofs)
    receipt = control.repair_bootstrap(owner, identity("succeeded"), request, role="client", installation="client-a")
    assert receipt.to_dict()["status"] == "succeeded" and receipt.to_dict()["sequence"] == 3
    assert receipt.to_dict()["request_digest"] == request.request_digest
    if checkpoint_auth:
        assert 30 < clock() - started < context["declaration"]["outfitting_seconds"]
        assert len(fresh_proofs) - before_fresh >= 5
    assert control.repair_bootstrap(
        owner, identity("succeeded"), request, role="client", installation="client-a") == receipt
    assert tuple(journal.results())[:2] == original_rows and len(journal.results()) == 3
    retained = decode((root / "receipts" / (receipt.to_dict()["result_ref"] + ".json")).read_bytes())
    assert retained["deployment"] == candidate.deployment_digest
    assert retained["selection"]["release"] == context["candidate_release"]
    assert retained["original_attempt"] == original_attempt and retained["prior_outcomes"] == {}
    assert retained["repair"]["executor_release"] == context["executor_release"]
    assert retained["repair"]["executor_runtime"] == executor_runtime
    assert retained["repair"]["transition_reference"] == "current-installed-repair"
    assert retained["repair"]["native_mutations_performed"] is False
    if mode == "save-partial-renewed":
        assert retained["repair"]["transition_revision"] == retained["repair"]["admission_revision"] == 2
    assert decode((root / "selection.json").read_bytes())["attempt"] is None
    final = control.repair_status(owner, identity(), request, role="client", installation="client-a")
    assert final["accepted"] == "succeeded" and not final["unresolved_effects"]
    assert final["observed"] == candidate.deployment_digest and final["desired"] == executor.deployment_digest
    assert not final["converged"]
    assert resolutions
    assert len({evidence for _, evidence in proofs}) == len(proofs)
    result = "original-repaired"
assert not admission._issued
print(json.dumps({
    "result": result, "source_denied": True, "distinct_identity": True,
    "baseline_contract": control.CONTRACT, "repair_contract": control.REPAIR_CONTRACT,
    "checkpoint_auth": checkpoint_auth, "fresh_proofs": len(fresh_proofs),
}))
"""
