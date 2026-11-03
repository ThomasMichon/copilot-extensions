"""Inert Komodo projection and explicitly code-selected workload composition."""

from __future__ import annotations

from .role_bindings import ProviderInstance, RoleBinding, RoleResource
from .role_deployment import ProviderContract


class KomodoRoleProvider:
    contract = ProviderContract(
        implementation="komodo",
        role_implementations=("container.service", "fleet-manager.gateway"),
        capabilities=("bootstrap-reference", "container-role", "named-resource"),
    )

    def project(self, instance: ProviderInstance, binding: RoleBinding) -> RoleResource:
        return RoleResource(
            role=binding.role, resource_type="Stack", instance_ref=instance.instance_ref,
            target=binding.target, resource_ref=binding.resource,
            artifact_ref=binding.artifact_ref, configuration_ref=binding.configuration_ref,
            key_refs=binding.key_refs,
        )

    def execution_binding(self, owner, slot, *, role: str, installation: str):
        """Only explicit SDK composition opts in; project() never reaches this path."""
        from .komodo_control import workload_binding
        from .komodo_workload import KomodoWorkloadOwner

        if type(owner) is not KomodoWorkloadOwner:
            from .planning import PlanError

            raise PlanError("Komodo role execution requires the selected workload owner")
        return workload_binding(owner, slot, role=role, installation=installation)
