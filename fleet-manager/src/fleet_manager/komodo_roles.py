"""Komodo named-resource projection; no Core API, credentials or deployment."""

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
