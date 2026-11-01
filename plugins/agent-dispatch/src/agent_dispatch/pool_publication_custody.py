"""Immutable metadata request custody, not enrollment or placement execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .pool_source_snapshot import PoolSourceSnapshot
from .queue_pool_catalog import PoolPublicationScope
from .queue_pool_cohort import PoolConfiguredCohort

if TYPE_CHECKING:
    from .pool_instances import PoolInstanceDefinition


class PoolPublicationError(RuntimeError):
    """Publication prerequisites or the original request fence did not match."""


@dataclass(frozen=True)
class PoolPublisherPosition:
    machine: str
    env: str
    publisher_id: str
    generation: int
    revision: int


@dataclass(frozen=True)
class PoolCatalogRead:
    registration_revision: int
    positions: tuple[PoolPublisherPosition, ...]
    scopes: tuple[PoolPublicationScope, ...] = ()
    configured_cohort: PoolConfiguredCohort | None = None

    def permits_scope(self, machine: str, env: str) -> bool:
        cohort = self.configured_cohort
        if cohort is None or cohort.revision == 0:
            return True
        observation = cohort.observation
        return observation is not None and observation.complete and any(
            (member.execution_machine, member.env) == (machine, env)
            for member in observation.members
        )

    @classmethod
    def from_dict(cls, value: object) -> PoolCatalogRead:
        from .pool_publication_response import read_pool_configured_cohort, read_pool_scope

        if not isinstance(value, dict) or (
            value.get("protocol") != "pool_catalog_transport/v1"
            or type(value.get("version")) is not int or value["version"] != 1
            or type(value.get("registration_revision")) is not int
            or value["registration_revision"] < 0
            or not isinstance(value.get("scopes"), list)
        ):
            raise PoolPublicationError("pool catalog input observation is malformed or unsupported")
        positions = []
        scopes = []
        seen = set()
        for scope in value["scopes"]:
            if not isinstance(scope, dict) or any(
                not isinstance(scope.get(key), str) or not scope[key].strip()
                or scope[key] != scope[key].strip()
                for key in ("machine", "env", "publisher_id")
            ) or any(
                type(scope.get(key)) is not int or scope[key] < minimum
                for key, minimum in (("generation", 1), ("revision", 0))
            ):
                raise PoolPublicationError("pool catalog publisher position is malformed")
            identity = (scope["machine"], scope["env"])
            if identity in seen:
                raise PoolPublicationError("pool catalog contains duplicate publication scopes")
            seen.add(identity)
            positions.append(PoolPublisherPosition(
                *identity, scope["publisher_id"], scope["generation"], scope["revision"],
            ))
            scopes.append(read_pool_scope(scope))
        cohort = (
            read_pool_configured_cohort(value["configured_cohort"])
            if "configured_cohort" in value else None
        )
        return cls(value["registration_revision"], tuple(positions), tuple(scopes), cohort)


@dataclass(frozen=True)
class PoolPublicationInputs:
    machine: str
    env: str
    publisher_id: str
    snapshot: PoolSourceSnapshot
    catalog: PoolCatalogRead | None
    reasons: tuple[str, ...]
    local_recheck_revision: str | None = None

    @property
    def coherent(self) -> bool:
        return (
            not self.reasons and self.snapshot.complete
            and self.snapshot.input_revision is not None and self.catalog is not None
            and self.catalog.permits_scope(self.machine, self.env)
            and self.local_recheck_revision == self.snapshot.input_revision
        )


@dataclass(frozen=True)
class PoolMetadataRequest:
    """The complete original wire body; replay never substitutes newer inputs."""

    operation: Literal["begin", "publish"]
    inputs: PoolPublicationInputs
    generation: int
    expected_revision: int = 0
    reason: str | None = None

    @property
    def expected_cohort_revision(self) -> int | None:
        catalog = self.inputs.catalog
        cohort = None if catalog is None else catalog.configured_cohort
        return cohort.revision if cohort is not None and cohort.revision > 0 else None

    @property
    def input_revision(self) -> str:
        if self.reason is not None:
            return f"unavailable:{self.inputs.snapshot.attempt}"
        revision = self.inputs.snapshot.input_revision
        if revision is None:
            raise PoolPublicationError("pool publication request has no retained local input revision")
        return revision

    def to_dict(self) -> dict[str, object]:
        body: dict[str, object] = {
            "version": 1, "machine": self.inputs.machine, "env": self.inputs.env,
            "publisher_id": self.inputs.publisher_id,
        }
        if self.expected_cohort_revision is not None:
            body["expected_cohort_revision"] = self.expected_cohort_revision
        if self.operation == "begin":
            body["expected_generation"] = self.generation
        else:
            catalog = self.inputs.catalog
            if catalog is None:
                raise PoolPublicationError("pool publication request has no retained input fence")
            body.update(
                generation=self.generation, expected_revision=self.expected_revision,
                registration_revision=catalog.registration_revision,
                input_revision=self.input_revision,
                definitions=[] if self.reason is not None
                else [item.to_dict() for item in self.inputs.snapshot.definitions],
            )
            if self.reason is not None:
                body["reason"] = self.reason
        return body


class PoolPublisherCustody:
    """Bound unresolved/last-settled custody; durable history stays provider-owned."""

    def __init__(self, publisher_id: str) -> None:
        self.publisher_id = publisher_id
        self.pending: PoolMetadataRequest | None = None
        self.position: PoolPublisherPosition | None = None
        self.settled: tuple[PoolMetadataRequest, PoolPublicationScope] | None = None

    def prepare(self, inputs: PoolPublicationInputs) -> PoolMetadataRequest:
        if self.pending is not None:
            raise PoolPublicationError("original pool metadata request remains unresolved")
        if "local-inputs-changed" in inputs.reasons:
            raise PoolPublicationError("pool metadata requires unchanged final local inputs")
        if inputs.publisher_id != self.publisher_id or inputs.catalog is None or any(
            reason.startswith("coordinator-") for reason in inputs.reasons
        ):
            raise PoolPublicationError("pool metadata requires current coordinator inputs from the original acquisition")
        catalog = inputs.catalog
        assert catalog is not None
        if not catalog.permits_scope(inputs.machine, inputs.env):
            raise PoolPublicationError("configured pool cohort is uncertain or excludes the execution scope")
        if self.settled is not None:
            original = self.settled[0].inputs.catalog
            prior_cohort = None if original is None else original.configured_cohort
            cohort = catalog.configured_cohort
            if prior_cohort is not None and prior_cohort.revision > 0 and (
                cohort is None or cohort.revision < prior_cohort.revision
                or cohort.revision == prior_cohort.revision and cohort != prior_cohort
            ):
                raise PoolPublicationError("pool configured cohort changed without a current revision")
        current = next(
            (position for position in catalog.positions
             if (position.machine, position.env) == (inputs.machine, inputs.env)), None,
        )
        if self.position is None:
            if not inputs.coherent:
                raise PoolPublicationError("pool publisher enrollment requires coherent complete inputs")
            if current is not None and current.publisher_id == self.publisher_id:
                raise PoolPublicationError("pool publisher acquisition has an unaccounted enrollment")
            request = PoolMetadataRequest("begin", inputs, current.generation if current else 0)
        else:
            if current != self.position:
                raise PoolPublicationError("pool publisher position changed; refusing revision rebase")
            assert current is not None
            retained = next(
                (scope for scope in catalog.scopes
                 if (scope.machine, scope.env) == (inputs.machine, inputs.env)), None,
            )
            if self.settled is None or retained != self.settled[1]:
                raise PoolPublicationError("pool publisher retained scope changed; refusing attribution rebase")
            reason = None if inputs.coherent else ";".join(inputs.reasons or inputs.snapshot.reasons)
            if reason == "":
                raise PoolPublicationError("unavailable pool input observation has no reason")
            request = PoolMetadataRequest("publish", inputs, current.generation, current.revision, reason)
        self.pending = request
        return request

    def acknowledge(self, request: PoolMetadataRequest, result: PoolPublicationScope) -> None:
        """Account for a late result too; recording it does not restore admission."""
        if self.pending is not request:
            raise PoolPublicationError("pool metadata result does not match the original request")
        catalog = request.inputs.catalog
        if catalog is None:
            raise PoolPublicationError("pool metadata request has no original catalog observation")
        expected_generation = request.generation + (request.operation == "begin")
        previous = next(
            (scope for scope in catalog.scopes
             if (scope.machine, scope.env) == (request.inputs.machine, request.inputs.env)), None,
        )
        if request.operation == "publish" and previous is None:
            raise PoolPublicationError("pool metadata request has no original enrollment attribution")
        expected_enrollment = (
            request.expected_cohort_revision if request.operation == "begin"
            else previous.enrollment_cohort_revision if previous is not None else None
        )
        if (
            (result.machine, result.env, result.publisher_id)
            != (request.inputs.machine, request.inputs.env, self.publisher_id)
            or result.generation != expected_generation
            or result.enrollment_cohort_revision != expected_enrollment
            or (request.operation == "begin" and (
                result.revision != 0 or result.state != "pending" or result.reason is not None
            ))
            or (request.operation == "publish" and (
                result.revision != request.expected_revision + 1
                or result.registration_revision != catalog.registration_revision
                or result.input_revision != request.input_revision
                or result.state != ("complete" if request.reason is None else "unavailable")
                or result.reason != request.reason
            ))
        ):
            raise PoolPublicationError("pool metadata result failed its original request fences")
        if request.operation == "begin":
            baseline = (
                ((), None, None, None, None, None, None) if previous is None else (
                    previous.definitions, previous.definitions_generation,
                    previous.definitions_revision, previous.input_revision,
                    previous.registration_revision, previous.valid_until,
                    previous.definitions_cohort_revision,
                )
            )
            if (
                result.definitions, result.definitions_generation,
                result.definitions_revision, result.input_revision,
                result.registration_revision, result.valid_until,
                result.definitions_cohort_revision,
            ) != baseline:
                raise PoolPublicationError("pool enrollment result changed original retained baseline")
        else:
            expected: tuple[
                tuple[PoolInstanceDefinition, ...], int | None, int | None, int | None,
            ]
            if request.reason is None:
                expected = (
                    request.inputs.snapshot.definitions, result.generation, result.revision,
                    request.expected_cohort_revision,
                )
            else:
                if self.settled is None:
                    raise PoolPublicationError("unavailable result has no prior definition custody")
                previous = self.settled[1]
                expected = (
                    previous.definitions, previous.definitions_generation,
                    previous.definitions_revision, previous.definitions_cohort_revision,
                )
            if (
                result.definitions, result.definitions_generation,
                result.definitions_revision, result.definitions_cohort_revision,
            ) != expected:
                raise PoolPublicationError("pool metadata result changed original definition custody")
        self.position = PoolPublisherPosition(
            result.machine, result.env, result.publisher_id, result.generation, result.revision,
        )
        self.settled = (request, result)
        self.pending = None
