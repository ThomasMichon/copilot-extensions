"""Installed host bootstrap and Gateway composition over the same fixed owner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .komodo_owner import KomodoOwner
from .operations import OperationIntent, TargetReceipt
from .planning import PlanError, canonical, identifier
from .runtime_admission import AdmissionError
from .runtime_identity import VerifiedIdentity

if TYPE_CHECKING:
    from .gateway_local_handler import LocalHandlerBinding, LocalServiceRequest
    from .gateway_manifest import ServiceSlot

CONTRACT = "fleet-manager.komodo-substrate-owner.v1"


def bootstrap(owner: KomodoOwner, identity: VerifiedIdentity, request: OperationIntent,
              *, role: str, installation: str) -> TargetReceipt:
    """Consume an existing verifier's identity before Core or Gateway exists."""
    permit = owner.admission.authorize(identity, request, role=role, installation=installation)
    return owner.converge(request, permit)


def gateway_binding(owner: KomodoOwner, slot: ServiceSlot, *,
                    role: str, installation: str) -> LocalHandlerBinding:
    """Code-register only fixed convergence/reconciliation in the existing lifetime."""
    from .gateway_local_handler import LocalHandlerBinding, settled
    from .gateway_service_handler import ServiceResponse

    identifier(role, "Komodo caller role")
    identifier(installation, "Komodo caller installation")

    async def handle(local: LocalServiceRequest) -> ServiceResponse:
        request = local.request
        if request.method != "POST" or request.target not in ("/converge", "/settle-failed", "/status"):
            return ServiceResponse(404)
        try:
            intent = OperationIntent(request.body)
            if intent.to_dict()["command"] != "manager.converge":
                raise PlanError("unsupported Komodo operation")
        except PlanError:
            return ServiceResponse(400, (("content-type", "application/json"),),
                                   canonical({"error": "invalid-komodo-operation"}))
        try:
            permit = local.authorize_operation(
                intent, owner.admission, authority=owner.authority, role=role, installation=installation)
        except AdmissionError as error:
            return ServiceResponse(403, (("content-type", "application/json"),),
                                   canonical({"error": error.code}))
        try:
            if request.target == "/status":
                status = await settled(lambda: owner.inspect(intent, permit))
                return ServiceResponse(200, (("content-type", "application/json"),), canonical(status))
            operation = owner.converge if request.target == "/converge" else owner.settle_failed
            receipt = await settled(lambda: operation(intent, permit))
            return ServiceResponse(200, (("content-type", "application/json"),), receipt.encoded)
        except PlanError as error:
            return ServiceResponse(409, (("content-type", "application/json"),),
                                   canonical({"error": "komodo-convergence-refused", "detail": str(error)}))
        finally:
            owner.admission.discard(permit)

    return LocalHandlerBinding(slot, handle)
