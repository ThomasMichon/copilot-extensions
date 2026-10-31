"""Current authority-resolved repair subjects, never wire grants or old code."""

from __future__ import annotations

from dataclasses import dataclass

from .komodo_declaration import fingerprint
from .komodo_selection import KomodoSelection
from .planning import PlanError, identifier, integer


@dataclass(frozen=True)
class KomodoRepairSelection:
    """The code-registered resolver authenticates both subjects and the transition.

    Construction only checks shape and exact scope. The existing authority
    must issue this result anew for the verified caller at each checkpoint.
    """

    candidate: KomodoSelection
    executor: KomodoSelection
    request_digest: str
    executor_runtime: str
    transition_reference: str
    transition_revision: int
    valid_until: int

    def __post_init__(self) -> None:
        if type(self.candidate) is not KomodoSelection or type(self.executor) is not KomodoSelection:
            raise PlanError("Komodo repair requires independently resolved candidate and executor")
        fingerprint(self.request_digest)
        fingerprint(self.executor_runtime)
        identifier(self.transition_reference, "Komodo repair transition")
        integer(self.transition_revision, "Komodo repair transition revision", minimum=1)
        integer(self.valid_until, "Komodo repair transition expiry", minimum=1)
        candidate, executor = self.candidate, self.executor
        if candidate.release_digest == executor.release_digest:
            raise PlanError("Komodo repair requires a distinct released executor")
        if ((candidate.fleet, candidate.installation, candidate.generation, candidate.resource,
             candidate.host_runtime, candidate.tooling, candidate.material)
                != (executor.fleet, executor.installation, executor.generation, executor.resource,
                    executor.host_runtime, executor.tooling, executor.material)
                or candidate.profile() != executor.profile()
                or candidate.material_evidence() != executor.material_evidence()
                or any(candidate.receipt_context()[key] != executor.receipt_context()[key]
                       for key in ("declaration", "generation", "compatible_with", "adoption",
                                   "adopted_public_identity", "adopted_network"))):
            raise PlanError("Komodo repair may change executor code only, not candidate custody")
        if candidate.declaration.to_dict().get("desired_state", "present") != "present":
            raise PlanError("Komodo repair cannot authorize native withdrawal")

    @property
    def binding(self) -> tuple[str, ...]:
        return (self.request_digest, self.candidate.deployment_digest,
                self.executor.deployment_digest, self.executor_runtime,
                self.candidate.resource, self.transition_reference)

    def provenance(self, *, admission_revision: int) -> dict:
        return {
            "schema": "fleet-manager.komodo-original-repair", "version": 1,
            "request_digest": self.request_digest,
            "candidate": self.candidate.deployment_digest,
            "candidate_release": self.candidate.release_digest,
            "executor": self.executor.deployment_digest,
            "executor_release": self.executor.release_digest,
            "executor_runtime": self.executor_runtime,
            "resource": self.candidate.resource,
            "transition_reference": self.transition_reference,
            "transition_revision": self.transition_revision,
            "admission_revision": admission_revision,
            "native_mutations_performed": False,
        }
