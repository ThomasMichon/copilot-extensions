"""Reconcile one original settled UNKNOWN through a separately admitted executor."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Iterator

from .komodo_declaration import SERVICES, fingerprint
from .komodo_evidence import native_fingerprint, native_reference, native_set_matches, same_native_observation
from .komodo_health import current_health, public_key_fingerprints, public_keys
from .komodo_native import KomodoNative
from .komodo_network import same_network_observation, verify_network
from .komodo_owner import KomodoOwner, native_running, verify_attempt_keys, verify_candidate, verify_failed_candidate
from .komodo_repair_selection import KomodoRepairSelection
from .komodo_repair_evidence import (
    original_file, original_history, read_outcome, retained_shape, validate_history,
    validate_retained_outcomes,
)
from .komodo_state import KomodoState
from .node_journal import NodeJournal
from .operations import OperationIntent, TargetReceipt
from .planning import PlanError, canonical, digest, integer
from .private_state import validate_private_file
from .runtime_admission import AdmissionAuthority, CallerIdentity, Permit

RepairIdentity = CallerIdentity
RepairResolver = Callable[[RepairIdentity, OperationIntent, str, str], KomodoRepairSelection]


@dataclass(frozen=True)
class _Observation:
    containers: dict[str, dict[str, Any]]
    network: str | None
    endpoints: dict[str, Any] | None
    failed: bool


def _file_identity(path: Path) -> tuple[int, int]:
    try:
        validate_private_file(path)
        info = path.stat()
    except OSError:
        raise PlanError("Komodo repair original journal is unavailable") from None
    return info.st_dev, info.st_ino


class _Resolution:
    def __init__(self, owner: KomodoRepairOwner, identity: RepairIdentity,
                 request: OperationIntent, role: str, installation: str) -> None:
        self.owner, self.identity, self.request = owner, identity, request
        self.role, self.installation = role, installation
        self.selected: KomodoRepairSelection | None = None
        self.binding: tuple[str, ...] | None = None
        self.revision = 0

    def refresh(self) -> None:
        selected = self.owner.resolve(self.identity, self.request, self.role, self.installation)
        if type(selected) is not KomodoRepairSelection:
            raise PlanError("Komodo repair transition authority is unavailable")
        now = self.owner.clock()
        integer(now, "Komodo repair current time")
        value, candidate = self.request.to_dict(), selected.candidate
        if (now >= min(selected.valid_until, candidate.valid_until, selected.executor.valid_until)
                or selected.request_digest != self.request.request_digest
                or value["authority"] != self.owner.authority
                or value["command"] != "manager.converge"
                or value["generation"] != candidate.generation
                or value["target"] != candidate.declaration.to_dict()["target"]
                or value["parameters"] != {"deployment": candidate.deployment_digest}
                or selected.transition_revision < self.revision
                or (self.binding is not None and selected.binding != self.binding)):
            raise PlanError("Komodo repair differs from current exact-original transition")
        self.selected, self.binding = selected, selected.binding
        self.revision = selected.transition_revision

    def current(self) -> KomodoRepairSelection:
        if self.selected is None:
            raise PlanError("Komodo repair transition authority is unavailable")
        return self.selected


class _Attempt:
    def __init__(self, owner: KomodoRepairOwner, resolution: _Resolution,
                 state: KomodoState, permit: Permit) -> None:
        self.owner, self.resolution, self.state, self.permit = owner, resolution, state, permit
        selected = resolution.current().candidate
        self.request = resolution.request
        self.record = KomodoOwner._baseline(state, selected, {})
        retained_shape(self.record)
        self.history = original_history(state.root, self.record, self.request.request_digest)
        prior = self.history.get("failed", self.history.get("good"))
        if prior is not None:
            deployment = read_outcome(state.root, prior)["deployment"]
            if deployment != selected.deployment_digest and deployment not in selected.compatible_with:
                raise PlanError("Komodo repair lacks the original retained-data compatibility")
        self.saved = canonical(self.record)
        self.path = state.root / "node.sqlite"
        self.journal_identity = _file_identity(self.path)
        self.journal = NodeJournal(
            self.path, authority=owner.authority, installation=selected.installation,
            target=selected.declaration.to_dict()["target"], existing_only=True)
        if self.journal.path != self.path:
            raise PlanError("Komodo repair journal differs from the original physical namespace")
        self.receipt = self.journal.inspect(self.request)
        if (self.receipt is None or self.journal.inspect_named_external(
                resource=selected.resource) != self.receipt):
            raise PlanError("Komodo repair requires the original journal and physical resource owner")
        before = self.receipt.to_dict()
        if ((before["status"], before["sequence"]) not in (
                ("unknown", 2), ("succeeded", 3), ("failed", 3))
                or (before["status"] == "unknown" and before["result_ref"] is not None)):
            raise PlanError("Komodo repair requires the original uncertain receipt")
        self.original = self.record["attempt"]
        if before["status"] != "unknown":
            outcome = self.outcome(before["status"])
            self.original = outcome["original_attempt"]
            if self.record["attempt"] is not None and self.record["attempt"] != self.original:
                raise PlanError("Komodo repair has a conflicting pending attempt")
        attempt = self.original
        if (type(attempt) is not dict or attempt.get("request") != self.request.to_dict()
                or attempt.get("deployment") != selected.deployment_digest
                or attempt.get("native_settled") is not True
                or attempt.get("fixed_profile") is not True
                or type(attempt.get("before")) is not dict
                or not set(attempt["before"]) <= set(SERVICES)
                or "network_before" not in attempt):
            raise PlanError("Komodo repair lacks the original settled fixed-profile attempt")
        for reference in attempt["before"].values():
            native_reference(reference)
        if attempt["network_before"] is not None:
            fingerprint(attempt["network_before"])
        profile = selected.profile()
        self.profile = state.root / "deployments" / digest(
            {"profile": profile.decode("utf-8")}) / "compose.json"
        if (self.original.get("profile") != str(self.profile)
                or original_file(self.profile) != profile):
            raise PlanError("Komodo repair original profile is missing or changed")
        verify_attempt_keys(self.original, selected)
        self.keys = public_key_fingerprints(selected.material)
        self.material = selected.material_evidence()
        KomodoOwner._preserved_material(self.record, selected, allow_new_identity=True)
        self.native = KomodoNative(selected.tooling)
        self.deadline = time.monotonic() + selected.declaration.to_dict()["outfitting_seconds"]
        self.observation: _Observation | None = None
        self.retained: tuple[str, dict[str, Any]] | None = None
        self.checkpoint()

    def _custody(self) -> None:
        selected = self.resolution.current().candidate
        self.state.check()
        if (self.permit.fleet != selected.fleet
                or canonical(self.state.read()) != self.saved
                or _file_identity(self.path) != self.journal_identity
                or self.journal.inspect(self.request) != self.receipt
                or self.journal.inspect_named_external(resource=selected.resource) != self.receipt
                or original_file(self.profile) != selected.profile()
                or public_key_fingerprints(selected.material) != self.keys
                or selected.material_evidence() != self.material):
            raise PlanError("Komodo repair original evidence changed during reconciliation")
        verify_attempt_keys(self.original, selected, actual=self.keys)

    def checkpoint(self) -> None:
        self.permit = self.owner.admission.revalidate(self.permit, self.request)
        try:
            selected = self.resolution.current().candidate
            self._custody()
            validate_retained_outcomes(self.state.root, self.record)
            validate_history(self.state.root, self.history)
            if self.retained is not None and read_outcome(self.state.root, self.retained[0]) != self.retained[1]:
                raise PlanError("Komodo repair retained outcome changed during publication")
            if self.observation is not None:
                containers = self.native.inspect(
                    selected.declaration, selected.material, deadline=self.deadline)
                if not same_native_observation(self.observation.containers, containers):
                    raise PlanError("Komodo repair native process changed during publication")
                network = self.native.network(
                    selected.declaration, selected.material, deadline=self.deadline)
                verify_network(network,
                    selected, containers, self.observation.network, pending=self.observation.failed)
                if (network["Containers"] if network is not None else None) != self.observation.endpoints:
                    raise PlanError("Komodo repair native network endpoints changed during publication")
            self._custody()
            self.permit = self.owner.admission.revalidate(self.permit, self.request)
        except BaseException:
            self.owner.admission.discard(self.permit)
            raise

    def evidence(self, *, failed: bool, retained: dict[str, Any] | None = None) -> dict[str, Any]:
        self.checkpoint()
        if retained is None:
            retained = self.pending_outcome(failed=failed)
        selected = self.resolution.current().candidate
        images = self.native.prerequisites(selected.declaration, selected.material, deadline=self.deadline)
        containers = self.native.inspect(selected.declaration, selected.material, deadline=self.deadline)
        if retained is not None and not native_set_matches(retained["native"], containers):
            phase = "pending" if self.receipt.to_dict()["status"] == "unknown" else "terminal"
            raise PlanError(f"Komodo repair {phase} retained native evidence is unproved")
        if failed:
            selected_native = verify_failed_candidate(
                containers, selected, images, selected.profile(), self.original)
        else:
            verify_candidate(containers, selected, images, selected.profile(), self.original)
            selected_native = True
        before_network = deepcopy(self.native.network(
            selected.declaration, selected.material, deadline=self.deadline))
        expected_network = self.original["network_before"] if retained is None else retained["network"]
        network = verify_network(before_network,
            selected, containers, expected_network, created=retained is None, pending=failed)
        self.checkpoint()
        health = None
        if failed:
            if selected_native and set(containers) == set(SERVICES) and all(
                    map(native_running, containers.values())):
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise PlanError("Komodo repair requires remaining native inspection time")
                try:
                    asyncio.run(current_health(
                        selected.declaration, selected.material,
                        deadline=self.deadline - min(2, remaining / 3), core=containers["core"]))
                except PlanError:
                    self.checkpoint()
                else:
                    raise PlanError("Komodo candidate is currently healthy; repair success instead")
        else:
            observer = KomodoOwner(
                current=lambda: self.resolution.current().candidate, admission=self.owner.admission,
                authority=self.owner.authority, clock=self.owner.clock)
            observer.native = self.native
            health = observer._ready(selected, containers, network, deadline=self.deadline)
        after = self.native.inspect(selected.declaration, selected.material, deadline=self.deadline)
        if not same_native_observation(containers, after):
            raise PlanError("Komodo repair native process changed during readiness")
        after_network = self.native.network(
            selected.declaration, selected.material, deadline=self.deadline)
        verify_network(after_network, selected, after, network, pending=failed)
        if not same_network_observation(before_network, after_network):
            raise PlanError("Komodo repair native network endpoints changed during readiness")
        self.observation = _Observation(
            deepcopy(after), network,
            deepcopy(after_network["Containers"]) if after_network is not None else None, failed)
        self.checkpoint()
        keys = public_keys(selected.material, missing_ok=failed)
        outcome = {
            "deployment": selected.deployment_digest, "profile": str(self.profile),
            "material": selected.material_evidence(),
            "public_identity": digest(keys) if len(keys) == 2 else None,
            "native": {name: native_fingerprint(item) for name, item in after.items()},
            "selection": selected.receipt_context(), "network": network, "fixed_profile": True,
            "original_attempt": self.original,
            "prior_outcomes": self.history,
            "repair": self.resolution.current().provenance(
                admission_revision=self.permit.policy_revision),
        }
        if failed:
            outcome.update(reason="authenticated-readiness-not-established", selected_native=selected_native)
            if set(containers) != set(SERVICES) or len(keys) != 2:
                outcome["partial_public_keys"] = {name: digest(value) for name, value in keys.items()}
        else:
            outcome["health"] = health
        return outcome

    def outcome(self, status: str) -> dict[str, Any]:
        value = self.record["good" if status == "succeeded" else "failed"]
        selected = self.resolution.current()
        if (type(value) is not dict or type(value.get("repair")) is not dict
                or type(value.get("original_attempt")) is not dict):
            raise PlanError("Komodo repair terminal provenance is unavailable")
        proof = value["repair"]
        integer(proof.get("transition_revision"), "Komodo retained repair transition revision", minimum=1)
        integer(proof.get("admission_revision"), "Komodo retained repair admission revision", minimum=1)
        expected = selected.provenance(admission_revision=self.permit.policy_revision)
        if (any(proof.get(key) != expected[key] for key in (
                "schema", "version", "request_digest", "candidate", "candidate_release", "executor",
                "executor_release", "executor_runtime", "resource", "transition_reference",
                "native_mutations_performed"))
                or proof["transition_revision"] > selected.transition_revision
                or proof["admission_revision"] > self.permit.policy_revision
                or value["deployment"] != selected.candidate.deployment_digest
                or value["original_attempt"].get("request") != self.request.to_dict()):
            raise PlanError("Komodo repair terminal provenance differs from the original transition")
        reference = self.receipt.to_dict()["result_ref"] if (
            self.receipt.to_dict()["status"] != "unknown") else digest(value)
        fingerprint(reference)
        if read_outcome(self.state.root, reference) != value:
            raise PlanError("Komodo repair immutable outcome is missing or changed")
        validate_history(self.state.root, value.get("prior_outcomes"))
        return value

    def pending_outcome(self, *, failed: bool | None = None) -> dict[str, Any] | None:
        pending = [name for name in ("good", "failed")
                   if type(self.record[name]) is dict and self.record[name].get(
                       "repair", {}).get("request_digest") == self.request.request_digest]
        if not pending:
            return None
        if (len(pending) != 1
                or failed is not None and pending[0] != ("failed" if failed else "good")):
            raise PlanError("Komodo repair cannot change an interrupted terminal decision")
        return self.outcome("failed" if pending[0] == "failed" else "succeeded")

    def publish(self, value: dict[str, Any], *, failed: bool) -> TargetReceipt:
        status, key = ("failed", "failed") if failed else ("succeeded", "good")
        pending = self.pending_outcome(failed=failed)
        if pending is not None:
            if (pending["native"] != value["native"] or pending["material"] != value["material"]
                    or pending["public_identity"] != value["public_identity"]
                    or pending["network"] != value["network"]
                    or pending["original_attempt"] != value["original_attempt"]):
                raise PlanError("Komodo repair pending outcome no longer proves the original candidate")
            value = pending
        self.checkpoint()
        reference = self.state.retain(value)
        self.retained = reference, deepcopy(value)
        self.checkpoint()
        if not failed:
            if (self.record["good"] is not None
                    and self.record["good"]["deployment"] != value["deployment"]):
                self.record["previous_good"] = self.record["good"]
            self.record["failed"], self.record["retirement"] = None, None
        self.record[key] = value
        self.state.save(self.record)
        self.saved = canonical(self.record)
        self.checkpoint()
        result = self.request.to_dict()
        del result["parameters"]
        result.update(schema="fleet-manager.target-receipt", status=status, sequence=3,
                      request_digest=self.request.request_digest, result_ref=reference)
        self.receipt = self.journal.reconcile(
            self.request, TargetReceipt.from_dict(result), checkpoint=self.checkpoint)
        self.checkpoint()
        self.finish()
        return self.receipt

    def finish(self) -> None:
        self.checkpoint()
        if self.record["attempt"] is not None:
            self.record["attempt"] = None
            self.state.save(self.record)
            self.saved = canonical(self.record)
            self.checkpoint()
        self.owner.admission.complete(self.permit, self.request)


class KomodoRepairOwner:
    """Only a code-registered existing authority can appoint this repair resolver."""

    def __init__(self, *, resolve: RepairResolver, admission: AdmissionAuthority,
                 authority: str, clock: Callable[[], int]) -> None:
        if not callable(resolve) or not callable(clock) or type(admission) is not AdmissionAuthority:
            raise PlanError("Komodo repair requires code-registered current authority resolution")
        self.resolve, self.admission, self.authority, self.clock = resolve, admission, authority, clock

    @contextmanager
    def _operation(self, identity: RepairIdentity, request: OperationIntent, *,
                   role: str, installation: str) -> Iterator[_Attempt]:
        KomodoOwner._worker()
        resolution = _Resolution(self, identity, request, role, installation)
        permit = self.admission.authorize(
            identity, request, role=role, installation=installation, checkpoint=resolution.refresh)
        operation = None
        try:
            selected = resolution.current().candidate
            if permit.fleet != selected.fleet:
                raise PlanError("Komodo repair permit fleet differs from the original owner")
            with KomodoState.open(
                selected.host_runtime, daemon=selected.tooling.daemon_id,
                project=selected.declaration.to_dict()["project"], existing_only=True,
            ) as state:
                operation = _Attempt(self, resolution, state, permit)
                yield operation
        finally:
            self.admission.discard(operation.permit if operation is not None else permit)

    def reconcile(self, identity: RepairIdentity, request: OperationIntent, *,
                  role: str, installation: str, failed: bool = False) -> TargetReceipt:
        if type(failed) is not bool:
            raise PlanError("Komodo repair requires an explicit terminal decision")
        with self._operation(identity, request, role=role, installation=installation) as operation:
            selected = operation.resolution.current().candidate
            prior = operation.receipt.to_dict()["status"]
            if prior != "unknown":
                if prior != ("failed" if failed else "succeeded"):
                    raise PlanError("Komodo repair cannot change the original terminal decision")
                outcome = operation.outcome(prior)
                current = operation.evidence(failed=False, retained=outcome) if not failed else None
                if failed:
                    operation.native.prerequisites(
                        selected.declaration, selected.material, deadline=operation.deadline)
                containers = operation.native.inspect(
                    selected.declaration, selected.material, deadline=operation.deadline)
                if (not native_set_matches(outcome["native"], containers)
                        or (current is not None and current["public_identity"] != outcome["public_identity"])):
                    raise PlanError("Komodo repair terminal native evidence has drifted")
                if failed:
                    network = deepcopy(operation.native.network(
                        selected.declaration, selected.material, deadline=operation.deadline))
                    network_reference = verify_network(
                        network, selected, containers, outcome["network"], pending=True)
                    operation.observation = _Observation(
                        deepcopy(containers), network_reference,
                        deepcopy(network["Containers"]) if network is not None else None, True)
                operation.finish()
                return operation.receipt
            return operation.publish(operation.evidence(failed=failed), failed=failed)

    def inspect(self, identity: RepairIdentity, request: OperationIntent, *,
                role: str, installation: str) -> dict[str, Any]:
        with self._operation(identity, request, role=role, installation=installation) as operation:
            selected = operation.resolution.current().candidate
            images = operation.native.prerequisites(
                selected.declaration, selected.material, deadline=operation.deadline)
            containers = operation.native.inspect(
                selected.declaration, selected.material, deadline=operation.deadline)
            status = operation.receipt.to_dict()["status"]
            blockers: list[str] = []
            installed, healthy, observed, error = None, None, None, None
            try:
                if status == "unknown":
                    verify_candidate(containers, selected, images, selected.profile(),
                                     operation.original, ready=False)
                    selected_native = True
                    retained = operation.pending_outcome()
                    if retained is not None and not native_set_matches(retained["native"], containers):
                        raise PlanError("Komodo repair pending retained native evidence is unproved")
                    expected_network = (operation.original["network_before"]
                                        if retained is None else retained["network"])
                else:
                    retained = operation.outcome(status)
                    if not native_set_matches(retained["native"], containers):
                        raise PlanError("Komodo repair retained native evidence is unproved")
                    selected_native = retained.get("selected_native", True)
                    expected_network = retained["network"]
                installed, observed = set(containers) == set(SERVICES), selected.deployment_digest
                before_network = deepcopy(operation.native.network(
                    selected.declaration, selected.material, deadline=operation.deadline))
                network = verify_network(before_network,
                    selected, containers, expected_network,
                    created=status == "unknown" and retained is None, pending=True)
                if installed and selected_native and all(map(native_running, containers.values())):
                    observer = KomodoOwner(
                        current=lambda: operation.resolution.current().candidate,
                        admission=self.admission, authority=self.authority, clock=self.clock)
                    observer.native = operation.native
                    try:
                        observer._ready(selected, containers, network, deadline=operation.deadline)
                    except PlanError as detail:
                        healthy, error = False, str(detail)
                        blockers.append("authenticated-current-health-unavailable")
                    else:
                        healthy = True
                else:
                    blockers.append("original-candidate-incomplete-mixed-or-not-running")
                after = operation.native.inspect(
                    selected.declaration, selected.material, deadline=operation.deadline)
                if not same_native_observation(containers, after):
                    raise PlanError("Komodo repair native process changed during status inspection")
                after_network = operation.native.network(
                    selected.declaration, selected.material, deadline=operation.deadline)
                verify_network(after_network,
                    selected, after, network, pending=True)
                if not same_network_observation(before_network, after_network):
                    raise PlanError("Komodo repair native network endpoints changed during status inspection")
                operation.observation = _Observation(
                    deepcopy(after), network,
                    deepcopy(after_network["Containers"]) if after_network is not None else None, True)
            except PlanError as detail:
                if operation.native.native_failure is not None:
                    raise
                installed, healthy, observed, error = None, None, None, str(detail)
                blockers.append("original-candidate-custody-unproved")
            operation.checkpoint()
            current = operation.resolution.current()
            unresolved = status == "unknown"
            result = {
                "schema": "fleet-manager.komodo-repair-status", "version": 1,
                "candidate": selected.deployment_digest,
                "candidate_release": selected.release_digest,
                "desired": current.executor.deployment_digest,
                "executor_release": current.executor.release_digest,
                "executor_runtime": current.executor_runtime,
                "accepted": status, "observed": observed, "installed": installed,
                "currently_healthy": healthy, "unresolved_effects": unresolved,
                "converged": False, "state": "unresolved" if unresolved else "drift" if blockers else "not-converged",
                "blockers": blockers, "health_error": error,
                "native_mutations_performed": False,
            }
            self.admission.complete(operation.permit, request)
            return result
