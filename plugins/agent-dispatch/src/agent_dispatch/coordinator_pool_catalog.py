"""Control-authorized metadata transport, not supervisor or placement admission."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from .coordinator_auth import _make_control_auth
from .events import EventBus
from .pool_instances import POOL_DEFINITION_VERSION, PoolDefinitionError, PoolInstanceDefinition
from .queue import TaskQueue
from .queue_pool_catalog import POOL_CATALOG_VERSION, PoolCatalogSnapshot, PoolPublicationScope
from .queue_records import TaskError

POOL_CATALOG_PROTOCOL = "pool_catalog_transport/v1"


def pool_catalog_contract(control_token: str | None) -> dict[str, object]:
    """Describe these running routes separately from package or execution support."""
    return {
        "protocol": POOL_CATALOG_PROTOCOL,
        "supported_versions": [POOL_CATALOG_VERSION],
        "definition_versions": [POOL_DEFINITION_VERSION],
        "control_authority_configured": bool(control_token),
        "metadata_only": True,
        "lock_holder_verification": False,
        "configured_scope_membership": False,
        "configured_cohort_fencing_versions": [1],
        "atomic_placement": False,
    }


class PoolScopeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Annotated[StrictInt, Field(ge=1, le=1)]
    machine: StrictStr
    env: StrictStr
    publisher_id: StrictStr
    expected_cohort_revision: Annotated[StrictInt, Field(ge=0)] | None = None


class BeginPoolPublicationBody(PoolScopeBody):
    expected_generation: Annotated[StrictInt, Field(ge=0)]


class PublishPoolCatalogBody(PoolScopeBody):
    generation: Annotated[StrictInt, Field(ge=1)]
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    registration_revision: Annotated[StrictInt, Field(ge=0)]
    input_revision: StrictStr
    definitions: list[dict[str, object]]
    reason: StrictStr | None = None


def _scope_payload(scope: PoolPublicationScope) -> dict[str, object]:
    return {
        "machine": scope.machine,
        "env": scope.env,
        "publisher_id": scope.publisher_id,
        "generation": scope.generation,
        "revision": scope.revision,
        "state": scope.state,
        "input_revision": scope.input_revision,
        "registration_revision": scope.registration_revision,
        "valid_until": scope.valid_until,
        "definitions": [definition.to_dict() for definition in scope.definitions],
        "definitions_generation": scope.definitions_generation,
        "definitions_revision": scope.definitions_revision,
        "reason": scope.reason,
        "enrollment_cohort_revision": scope.enrollment_cohort_revision,
        "definitions_cohort_revision": scope.definitions_cohort_revision,
    }


def _catalog_payload(snapshot: PoolCatalogSnapshot) -> dict[str, object]:
    cohort = snapshot.configured_cohort
    observation = cohort.observation
    return {
        "protocol": POOL_CATALOG_PROTOCOL,
        "version": snapshot.version,
        "revision": snapshot.revision,
        "registration_revision": snapshot.registration_revision,
        "scopes": [_scope_payload(scope) for scope in snapshot.scopes],
        "definitions": [definition.to_dict() for definition in snapshot.definitions],
        "blockers": list(snapshot.blockers),
        "metadata_ready": snapshot.ready,
        "configured_metadata_ready": snapshot.configured_ready,
        "configured_cohort": {
            "revision": cohort.revision,
            "observation": None if observation is None else {
                "state": observation.state, "source_revision": observation.source_revision,
                "revision": observation.revision, "reason": observation.reason,
                "members": [
                    {"machine": member.machine, "execution_machine": member.execution_machine,
                     "env": member.env}
                    for member in observation.members
                ],
            },
        },
    }


def register_pool_catalog_routes(
    app: FastAPI, queue: TaskQueue, *, control_token: str | None
) -> None:
    """Expand provider routes without enrolling a publisher or migrating a caller."""
    bus: EventBus = app.state.bus

    def emit(event: str, detail: dict[str, object]) -> None:
        bus.publish({"type": event, **detail})

    def refused(operation: str, exc: Exception, *, status: int = 409) -> HTTPException:
        detail: dict[str, object] = {
            "code": "pool_catalog_rejected",
            "operation": operation,
            "reason": str(exc),
            "retryable": False,
        }
        emit("pool_catalog.rejected", detail)
        return HTTPException(status_code=status, detail=detail)

    control = Depends(
        _make_control_auth(
            control_token or None,
            emit,
            operation="pool-catalog",
            rejection_event="pool_catalog.rejected",
            code_prefix="pool_catalog_control",
            unavailable_message="pool catalog metadata requires configured control authority",
            forbidden_message="invalid or missing pool catalog control authority",
        )
    )

    @app.get("/pool-catalog", dependencies=[control])
    def catalog(request: Request) -> dict[str, object]:
        """Read every retained scope in one transaction without renewing freshness."""
        if request.query_params:
            raise refused(
                "snapshot", TaskError("pool catalog does not accept scoped or partial reads"),
                status=400,
            )
        return _catalog_payload(queue.pool_catalog_snapshot())

    @app.post("/pool-catalog/scopes/begin", dependencies=[control])
    def begin(body: BeginPoolPublicationBody) -> dict[str, object]:
        """Retain control-owner incarnation accounting with its original CAS."""
        try:
            scope = queue.begin_pool_publication(
                body.machine, body.env, body.publisher_id,
                expected_generation=body.expected_generation,
                expected_cohort_revision=body.expected_cohort_revision,
            )
        except TaskError as exc:
            raise refused("begin", exc) from exc
        return {
            "protocol": POOL_CATALOG_PROTOCOL,
            "version": POOL_CATALOG_VERSION,
            "scope": _scope_payload(scope),
        }

    @app.post("/pool-catalog/scopes/publish", dependencies=[control])
    def publish(body: PublishPoolCatalogBody) -> dict[str, object]:
        """Replace a whole scope or retain unavailable evidence, never place work."""
        try:
            definitions = tuple(
                PoolInstanceDefinition.from_dict(value) for value in body.definitions
            )
            scope = queue.publish_pool_catalog_scope(
                body.machine, body.env, body.publisher_id,
                generation=body.generation,
                expected_revision=body.expected_revision,
                registration_revision=body.registration_revision,
                input_revision=body.input_revision,
                definitions=definitions,
                reason=body.reason,
                expected_cohort_revision=body.expected_cohort_revision,
            )
        except PoolDefinitionError as exc:
            raise refused("publish", exc, status=400) from exc
        except TaskError as exc:
            raise refused("publish", exc) from exc
        return {
            "protocol": POOL_CATALOG_PROTOCOL,
            "version": POOL_CATALOG_VERSION,
            "scope": _scope_payload(scope),
        }
