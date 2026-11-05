"""Explicit source admission for a registered native Git marketplace installation.

The native cache locates consistent bytes. The existing independent acquisition
owner authenticates their exact commit before canonical preparation. This
repository-only provider does not enable bootstrap or change runtime preferences.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import runtime_source_authentication as authentication
from runtime_profile import ProfileError, ProfileUnavailable
from runtime_profile_policy import InstallationProfilePlan

for _library in ("plugin-resolve", "update-telemetry"):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / _library / "src"))
import installation_context
from plugin_resolve.inventory import inventory_paths, native_git_inventory_item
from versioned_runtime_source import assembly_hash, payload_package_version, read_source_identity
from worktree_manager import engine_origin, runtime_source, runtime_source_config

_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def _registered(installation: dict, policy: dict):
    provenance = installation_context.resolve_payload_source(**{
        key: installation[key] for key in (
            "payload_root", "plugin_id", "copilot_home", "project_root",
            "source_descriptor", "marketplace_key", "environment",
        ) if key in installation
    })
    locator = provenance.get("locator")
    if not isinstance(locator, dict) or locator.get("kind") != "installed":
        raise ProfileUnavailable("marketplace source requires an installed native origin")
    if (
        provenance["sourceFingerprint"] != policy["owner"]["sourceFingerprint"]
        or provenance["pluginId"] != policy["owner"]["pluginId"]
    ):
        raise ProfileError("marketplace source differs from its installation owner")
    origin = engine_origin.select_registered_origin(
        installation_context, copilot_home=Path(locator["copilotHome"]),
        plugin_id=provenance["pluginId"], source_fingerprint=provenance["sourceFingerprint"],
        marketplace_id=provenance["marketplaceId"], payload_hint=Path(provenance["payloadRoot"]),
    )
    if any(origin.provenance[key] != provenance[key] for key in engine_origin._IDENTITY_FIELDS):
        raise ProfileError("registered marketplace differs from the installed source")
    return origin


def _repository(origin) -> str:
    descriptor = origin.definition["source"]
    kind = descriptor.get("source")
    field = "repo" if kind == "github" else "url"
    ref = descriptor.get("ref", "")
    if (
        kind not in ("github", "git")
        or set(descriptor) - {"source", field, "ref", "sha", "path"}
        or descriptor.get("path", ".") != "."
        or ref not in ("", "main", "refs/heads/main") and not _COMMIT.fullmatch(ref)
    ):
        raise ProfileUnavailable("registered marketplace ref or layout is unsupported")
    return f"https://github.com/{descriptor[field]}.git" if kind == "github" else descriptor[field]


@contextmanager
def retain_authenticated_marketplace_source(
    plan: InstallationProfilePlan, *, installation: dict, deadline: float,
):
    """Hold real acquisition/configuration custody through source consumption."""
    authentication._check_deadline(deadline)
    policy = authentication._policy(plan, installation)
    root = authentication.default_root().absolute()
    configured = runtime_source_config.configured_source(root)
    if configured.channel != "marketplace":
        raise ProfileUnavailable("independent configured runtime source is selected")
    origin = _registered(installation, policy)
    # Reject unsupported definitions before probing or acquiring another source.
    repo = _repository(origin)
    declared_environment = installation.get("environment")
    environment = {
        **(os.environ if declared_environment is None else declared_environment),
        "COPILOT_HOME": str(origin.copilot_home),
    }
    home, cache = inventory_paths(
        Path(installation.get("project_root") or origin.copilot_home), environment,
    )
    if home != origin.copilot_home:
        raise ProfileError("native cache and installation have different Copilot homes")
    ledger = engine_origin._ledger(home, origin.source, origin.payload)
    if ledger is None:
        raise ProfileUnavailable("native installed source ledger is unavailable")

    def inventory():
        authentication._check_deadline(deadline)
        return native_git_inventory_item(
            origin.source, home, cache, definition=origin.definition, environment=environment,
            execution_cwd=home, deadline=deadline, strict=True,
        )

    proof = inventory()
    if proof is None:
        raise ProfileUnavailable("native Git source evidence is unavailable")
    evidence = proof["inventory"]
    plugin = policy["owner"]["pluginId"]
    if (
        Path(evidence["catalog_root"]) != Path(evidence["git_root"])
        or Path(evidence["payload"]).relative_to(evidence["git_root"]) != Path("plugins") / plugin
    ):
        raise ProfileUnavailable("registered marketplace payload layout is unsupported")
    selection = runtime_source_config.RuntimeSource("exact", repo, evidence["git_commit"])
    with runtime_source.acquire(
        selection, root=root, deadline=deadline, environment=environment,
    ) as acquired:
        if acquired.commit != evidence["git_commit"] or acquired.tree != evidence["git_tree"]:
            raise ProfileError("acquired source differs from native marketplace commit and tree")
        with authentication.dependencies._temporary_inputs(None) as temporary:
            output = authentication.dependencies._private_retained_root(Path(temporary))
            prepared = authentication.prepare_runtime_source.prepare(
                acquired.checkout, acquired.commit, plugin, output,
                branch=selection.branch, deadline=deadline,
            )
            if (
                prepared.identity.source_commit != acquired.commit
                or prepared.identity.source_tree != acquired.tree
                or ledger.get("version") != prepared.identity.package_version
                or payload_package_version(origin.payload, plugin) != prepared.identity.package_version
                or assembly_hash(origin.payload) != prepared.identity.assembly_hash
            ):
                raise ProfileError("installed marketplace payload differs from authenticated preparation")
            canonical = json.dumps({
                "schema": "copilot-extensions.authenticated-runtime-source", "version": 1,
                "provider": "registered-marketplace-source", "owner_policy": policy["policyId"],
                "selection_sha256": "sha256:" + hashlib.sha256(json.dumps({
                    "source": origin.source, "definition": origin.definition,
                    "provenance": origin.provenance, "ledger": ledger, "inventory": proof,
                }, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "mode": "source", "trigger": plan.source_trigger,
                "source": prepared.identity.document(),
            }, sort_keys=True, separators=(",", ":")).encode()

            def verify():
                authentication._check_deadline(deadline)
                authentication._policy(plan, installation)
                if (
                    authentication.default_root().absolute() != root
                    or runtime_source_config.configured_source(root) != configured
                    or _registered(installation, policy) != origin
                    or engine_origin._ledger(home, origin.source, origin.payload) != ledger
                    or inventory() != proof
                    or payload_package_version(origin.payload, plugin) != prepared.identity.package_version
                    or assembly_hash(origin.payload) != prepared.identity.assembly_hash
                    or read_source_identity(
                        prepared.payload, prepared.identity.package_version, plugin=plugin,
                    ) != prepared.identity
                ):
                    raise ProfileError("authenticated marketplace source changed during consumption")
                authentication._check_deadline(deadline)

            verify()
            yield authentication.AuthenticatedSource(prepared.payload, prepared.identity, canonical, verify)
            verify()
