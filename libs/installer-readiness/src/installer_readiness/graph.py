"""Cross-installation module discovery and dependency-graph validation.

Groups attributable plugin installations, parses each one's manifest via
:mod:`installer_readiness.manifest`, and validates the resulting module
dependency graph (duplicates, unknown dependencies, cycles).
"""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterable, Sequence

from installation_context import InstallationContextError, normalize_source, source_identity

from .manifest import (
    _FINGERPRINT,
    _ID,
    _MARKETPLACE_ID,
    _finding,
    discover_installation,
)
from .model import Decline, DiscoveryReport, Finding, Module, PluginInstallation


def _validate_graph(modules: Sequence[Module]) -> list[Finding]:
    findings: list[Finding] = []
    by_id: dict[str, Module] = {}
    duplicates: set[str] = set()
    for module in modules:
        if module.qualified_id in by_id:
            duplicates.add(module.qualified_id)
        else:
            by_id[module.qualified_id] = module
    for qualified_id in sorted(duplicates):
        module = by_id[qualified_id]
        findings.append(
            _finding(
                "duplicate-module-id",
                f"multiple modules claim '{qualified_id}'",
                module.source,
                owner=module.owner.owner_id,
                module_id=qualified_id,
                remedy="Give every module one unique id within its installation cell.",
            )
        )

    dependencies: dict[str, tuple[str, ...]] = {}
    for module in modules:
        qualified_dependencies = tuple(
            f"{module.owner.provenance.marketplace_id}::{dependency}"
            for dependency in module.dependencies
        )
        dependencies[module.qualified_id] = qualified_dependencies
        for dependency in qualified_dependencies:
            if dependency == module.qualified_id:
                findings.append(
                    _finding(
                        "self-dependency",
                        "module cannot depend on itself",
                        module.source,
                        owner=module.owner.owner_id,
                        module_id=module.qualified_id,
                        remedy="Remove the module's self-dependency.",
                    )
                )
                continue
            if dependency not in by_id:
                findings.append(
                    _finding(
                        "unknown-dependency",
                        f"module depends on unknown module '{dependency}'",
                        module.source,
                        owner=module.owner.owner_id,
                        module_id=module.qualified_id,
                        remedy=(
                            "Declare the prerequisite in the same installation "
                            "cell or remove the dependency."
                        ),
                    )
                )

    state: dict[str, int] = {}
    stack: list[str] = []
    reported: set[tuple[str, ...]] = set()

    def visit(module_id: str) -> None:
        state[module_id] = 1
        stack.append(module_id)
        for dependency in sorted(dependencies.get(module_id, ())):
            if dependency not in by_id:
                continue
            if state.get(dependency, 0) == 0:
                visit(dependency)
            elif state.get(dependency) == 1:
                start = stack.index(dependency)
                cycle = (*stack[start:], dependency)
                canonical = min(
                    tuple(cycle[index:-1] + cycle[:index] + (cycle[index],))
                    for index in range(len(cycle) - 1)
                )
                if canonical not in reported:
                    reported.add(canonical)
                    module = by_id[module_id]
                    findings.append(
                        _finding(
                            "dependency-cycle",
                            f"dependency cycle: {' -> '.join(cycle)}",
                            module.source,
                            owner=module.owner.owner_id,
                            module_id=module_id,
                            remedy="Remove at least one edge from the dependency cycle.",
                        )
                    )
        stack.pop()
        state[module_id] = 2

    for module_id in sorted(by_id):
        if state.get(module_id, 0) == 0:
            visit(module_id)
    return findings


def discover_modules(
    installations: Iterable[PluginInstallation],
) -> DiscoveryReport:
    """Read plugin-owned manifests from attributable enabled payloads.

    The function is read-only. Callers must supply roots obtained from a host
    manifest or a validated installation receipt; no cache path or ``PATH``
    lookup is performed.
    """
    findings: list[Finding] = []
    modules: list[Module] = []
    declines: list[Decline] = []
    machine_gated: set[str] = set()
    grouped: dict[str, list[PluginInstallation]] = defaultdict(list)
    for installation in installations:
        invalid: list[str] = []
        if not _ID.fullmatch(installation.plugin_id):
            invalid.append("plugin id")
        provenance = installation.provenance
        if not _MARKETPLACE_ID.fullmatch(provenance.marketplace_id):
            invalid.append("marketplace id")
        if not _FINGERPRINT.fullmatch(provenance.source_fingerprint):
            invalid.append("source fingerprint")
        if not provenance.source_kind or not provenance.source_canonical:
            invalid.append("source identity")
        else:
            readable_name = provenance.marketplace_id.rpartition("--")[0]
            try:
                normalized = normalize_source(
                    {
                        "kind": provenance.source_kind,
                        "canonical": provenance.source_canonical,
                        "ref": provenance.source_ref,
                    },
                    from_receipt=True,
                )
                identity = source_identity(
                    normalized,
                    readable_name,
                )
            except InstallationContextError:
                invalid.append("source identity")
            else:
                if (
                    identity["marketplaceId"] != provenance.marketplace_id
                    or identity["fingerprint"] != provenance.source_fingerprint
                ):
                    invalid.append("marketplace provenance")
        if not installation.payload_root.is_absolute():
            invalid.append("absolute payload root")
        if invalid:
            findings.append(
                _finding(
                    "invalid-installation-owner",
                    f"invalid {', '.join(invalid)}",
                    installation.payload_root,
                    owner=installation.owner_id,
                    remedy="Supply an identity-verified enabled plugin installation.",
                )
            )
            continue
        grouped[installation.owner_id].append(installation)
    for owner_id in sorted(grouped):
        candidates = grouped[owner_id]
        roots = {
            os.path.normcase(str(candidate.payload_root.resolve()))
            for candidate in candidates
        }
        fingerprints = {
            candidate.provenance.source_fingerprint for candidate in candidates
        }
        receipts = {
            os.path.normcase(str(candidate.install_receipt.resolve()))
            for candidate in candidates
            if candidate.install_receipt is not None
        }
        if len(roots) != 1 or len(fingerprints) != 1 or len(receipts) > 1:
            findings.append(
                _finding(
                    "ambiguous-installation-owner",
                    f"enabled installation resolves to multiple payload roots: {sorted(roots)}",
                    owner_id,
                    owner=owner_id,
                    remedy="Repair installation receipts or host plugin-root attribution.",
                )
            )
            continue
        first = candidates[0]
        installation = PluginInstallation(
            plugin_id=first.plugin_id,
            payload_root=first.payload_root,
            provenance=first.provenance,
            scopes=tuple(
                sorted({scope for candidate in candidates for scope in candidate.scopes})
            ),
            install_receipt=first.install_receipt,
        )
        discovered, decline, local_findings, is_machine_gated = discover_installation(
            installation
        )
        findings.extend(local_findings)
        if is_machine_gated:
            machine_gated.add(owner_id)
        modules.extend(discovered)
        if decline is not None:
            declines.append(decline)
    findings.extend(_validate_graph(modules))
    covered = {module.owner.owner_id for module in modules}
    covered.update(decline.owner.owner_id for decline in declines)
    for owner_id in sorted(machine_gated - covered):
        if not any(finding.owner == owner_id for finding in findings):
            findings.append(
                _finding(
                    "missing-module-metadata",
                    "enabled machine-gated plugin was silently omitted",
                    owner_id,
                    owner=owner_id,
                    remedy="Declare supported modules or an intentional decline.",
                )
            )
    return DiscoveryReport(
        modules=tuple(sorted(modules, key=lambda module: module.qualified_id)),
        declines=tuple(sorted(declines, key=lambda decline: decline.owner.owner_id)),
        findings=tuple(findings),
        machine_gated_owners=tuple(sorted(machine_gated)),
    )
