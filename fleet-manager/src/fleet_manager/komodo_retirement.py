"""Explicit receipt-owned container withdrawal, retaining data, credentials and evidence."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .komodo_native import KomodoNative
from .komodo_network import retained_network, verify_network
from .komodo_owner import native_fingerprint
from .komodo_state import KomodoState
from .node_journal import NodeJournal
from .operations import OperationIntent, TargetReceipt
from .planning import PlanError
from .private_state import validate_private_file
from .runtime_admission import Permit

if TYPE_CHECKING:
    from .komodo_owner import KomodoOwner


def retire_owner(owner: KomodoOwner, request: OperationIntent, permit: Permit) -> TargetReceipt:
    try:
        permit = owner.admission.revalidate(permit, request)
        selected = owner._selected(request)
        desired = selected.declaration.to_dict()
        content = selected.deployment_digest
        if desired.get("desired_state", "present") != "absent" or permit.fleet != selected.fleet:
            raise PlanError("Komodo retirement requires exact current absence admission")
        deadline = time.monotonic() + desired["outfitting_seconds"]
        if owner.native is None:
            owner.native = KomodoNative(selected.tooling)
        if owner.native.tooling != selected.tooling:
            raise PlanError("Komodo native tooling changed within its owner")
        owner.native.verify_host(selected.declaration, selected.material, deadline=deadline)
        with KomodoState.open(
            selected.host_runtime, daemon=selected.tooling.daemon_id, project=desired["project"],
        ) as state:
            containers = owner.native.inspect(selected.declaration, selected.material, deadline=deadline)
            record = owner._baseline(state, selected, containers)
            retained = record["failed"] or record["good"]
            if retained is None:
                raise PlanError("Komodo retirement requires a previously recorded installation")
            before_declaration = dict(retained["selection"]["declaration"])
            before_declaration.pop("desired_state", None)
            current_declaration = dict(desired)
            current_declaration.pop("desired_state", None)
            if before_declaration != current_declaration:
                raise PlanError("Komodo retirement cannot also change the selected deployment")
            owner._preserved_material(record, selected)
            if any(retained["native"].get(name) != native_fingerprint(item)
                   for name, item in containers.items()):
                raise PlanError("Komodo retirement refuses replaced or foreign containers")
            network = verify_network(owner.native.network(
                selected.declaration, selected.material, deadline=deadline),
                selected, containers, retained_network(record, selected))
            path = state.root / "node.sqlite"
            validate_private_file(path)
            journal = NodeJournal(
                path, authority=owner.authority, installation=selected.installation,
                target=desired["target"])
            owner._clear_settled_attempt(state, record, journal, request)
            permit = owner.admission.revalidate(permit, request)
            receipt = journal.accept_named_external(request, resource=selected.resource)
            status = receipt.to_dict()["status"]
            if status == "succeeded":
                if (containers or record["retirement"] is None
                        or not owner.native.identities_absent(
                            selected.material, record["retirement"]["removed_containers"], deadline=deadline)):
                    raise PlanError("Komodo retired native state has drifted")
                owner.admission.complete(permit, request)
                return receipt
            if status == "accepted":
                if record["attempt"] is not None and (
                    record["attempt"]["request"] != request.to_dict()
                    or record["attempt"].get("deployment") != content
                    or "retiring" not in record["attempt"]
                ):
                    raise PlanError("Komodo retirement retains another unsettled publication")
                identities = sorted(item["Id"] for item in containers.values())
                record["attempt"] = {
                    "request": request.to_dict(), "deployment": content,
                    "retiring": identities, "native_settled": False,
                    "network_before": network,
                }
                state.save(record)
                owner._selected(request, content)
                permit = owner.admission.revalidate(permit, request)
                state.check()
                if not journal.begin_external(request):
                    raise PlanError("Komodo retirement was already accepted")

                def checkpoint() -> None:
                    nonlocal permit
                    owner._selected(request, content)
                    owner._preserved_material(record, selected)
                    permit = owner.admission.revalidate(permit, request)
                    state.check()

                try:
                    verify_network(owner.native.network(
                        selected.declaration, selected.material, deadline=deadline),
                        selected, containers, network)
                    if identities:
                        owner.native._retire(selected.material, identities, deadline=deadline,
                                             checkpoint=checkpoint)
                    else:
                        checkpoint()
                    owner.admission.complete(permit, request)
                finally:
                    if owner.native.native_failure is None:
                        record["attempt"]["native_settled"] = True
                        state.save(record)
            elif status == "unknown":
                owner.admission.complete(permit, request)
                attempt = record["attempt"]
                if (attempt is None or attempt["request"] != request.to_dict()
                        or attempt.get("deployment") != content or "retiring" not in attempt
                        or attempt["native_settled"] is not True):
                    raise PlanError("Komodo retirement requires original settled native custody")
                identities = attempt["retiring"]
            else:
                raise PlanError("Komodo retirement has an incompatible prior outcome")
            if (owner.native.inspect(selected.declaration, selected.material, deadline=deadline)
                    or not owner.native.identities_absent(selected.material, identities, deadline=deadline)):
                raise PlanError("Komodo retirement remains unresolved; native commands were not replayed")
            verify_network(owner.native.network(
                selected.declaration, selected.material, deadline=deadline), selected, {}, network)
            owner._selected(request, content)
            outcome = {
                "deployment": content, "removed_containers": identities,
                "material": selected.material_evidence(), "selection": selected.receipt_context(),
                "data_credentials_and_workloads_retained": True,
                "network": network,
            }
            reference = state.retain(outcome)
            record["retirement"] = outcome
            state.save(record)
            value = request.to_dict()
            del value["parameters"]
            value.update(schema="fleet-manager.target-receipt", sequence=3, status="succeeded",
                         request_digest=request.request_digest, result_ref=reference)
            settled = journal.reconcile(request, TargetReceipt.from_dict(value))
            record["attempt"] = None
            state.save(record)
            return settled
    finally:
        owner.admission.discard(permit)
