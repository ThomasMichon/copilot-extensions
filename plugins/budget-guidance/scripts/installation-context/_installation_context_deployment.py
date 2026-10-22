# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

RUNTIME_DEPLOYMENT_SCHEMA = "copilot-extensions.runtime-deployment"
RUNTIME_DEPLOYMENT_FILE = "runtime-deployment.json"


def _deployment_owner(
    resolution: Mapping[str, Any], arguments: Mapping[str, Any],
) -> dict[str, Any]:
    provenance = resolve_payload_source(
        **{
            key: arguments[key]
            for key in (
                "payload_root", "plugin_id", "copilot_home", "project_root",
                "source_descriptor", "marketplace_key", "environment",
            )
            if key in arguments
        },
    )
    if (
        not (
            resolution["status"] == "ready"
            or (resolution["status"] == "deactivation-required"
                and resolution["actualMode"] == "namespaced")
        )
        or resolution["actualMode"] not in {"legacy", "namespaced"}
        or provenance["marketplaceId"] != resolution["marketplaceId"]
        or provenance["pluginId"] != resolution["pluginId"]
    ):
        _fail("Runtime deployment requires a resolved installation owner.")
    return {
        "sourceFingerprint": provenance["sourceFingerprint"],
        "pluginId": resolution["pluginId"],
        "mode": resolution["actualMode"],
        "runtimeRoot": resolution["runtimeRoot"],
        "environment": resolution["environment"],
    }


def _deployment_policy(
    path: Path, resolution: Mapping[str, Any], arguments: Mapping[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "state": "missing",
        "managed": False,
        "allowPluginPublication": True,
        "reason": "deployment-default-standalone",
        "owner": None,
        "diagnostic": None,
        "recovery": None,
    }
    try:
        path.lstat()
    except FileNotFoundError:
        return result
    except OSError as error:
        result["diagnostic"] = str(error)
    result.update(
        state="invalid", managed=None, allowPluginPublication=False,
        reason="deployment-policy-invalid",
        recovery="Repair the installation-owned runtime deployment policy explicitly.",
    )
    if result["diagnostic"] is not None:
        return result
    try:
        # Do not canonicalize the leaf before inspecting it: that would turn
        # an injected policy symlink into an apparently ordinary JSON file.
        content = _read_regular_file_bytes(
            path, label="Runtime deployment policy", require_stable_identity=True,
        )
        policy = json.loads(
            content.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys,
        )
        if not isinstance(policy, Mapping):
            _fail("Runtime deployment policy must be a JSON object.")
        if _exact_property(policy, "schema") != RUNTIME_DEPLOYMENT_SCHEMA:
            _fail("Runtime deployment policy has an invalid schema.")
        version = _exact_property(policy, "version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            _fail("Runtime deployment policy version must be a positive integer.")
        if version > 1:
            result.update(
                state="unsupported", reason="deployment-policy-version-unsupported",
                diagnostic=f"Runtime deployment policy version {version} is unsupported.",
            )
            return result
        if set(policy) != {"schema", "version", "owner", "managed"}:
            _fail("Runtime deployment policy fields must be schema, version, owner and managed.")
        managed = _exact_property(policy, "managed")
        if not isinstance(managed, bool):
            _fail("Runtime deployment policy managed must be a JSON boolean.")
        owner = _exact_property(policy, "owner")
        expected = _deployment_owner(resolution, arguments)
        if not isinstance(owner, Mapping) or dict(owner) != expected:
            _fail("Runtime deployment policy belongs to another installation or environment.")
        result.update(
            state="valid", managed=managed, allowPluginPublication=not managed,
            reason="deployment-managed" if managed else "deployment-explicit-standalone",
            owner=expected,
            recovery=(
                "Use explicit installation management to install or update this runtime; "
                "plugin bootstrap and first use may not publish it."
                if managed else None
            ),
        )
    except (InstallationContextError, OSError, UnicodeError, json.JSONDecodeError) as error:
        result["diagnostic"] = str(error)
    return result


@_validation_scope
def resolve_runtime_deployment(**arguments: Any) -> dict[str, Any]:
    """Read plugin-publication policy without selecting, installing or authorizing a runtime."""
    try:
        resolution = resolve_installation_mode(**arguments)
    except OSError as error:
        _fail(f"Cannot resolve runtime deployment installation: {error}")
    runtime_root = resolution["runtimeRoot"]
    if runtime_root is None:
        # An unattributed standalone legacy bootstrap still has a governed
        # legacy root. Inspect its policy too; missing provenance must never
        # turn an existing managed/invalid policy into a standalone default.
        runtime_root = resolution["legacy"]["root"]
    policy = _deployment_policy(
        Path(runtime_root) / RUNTIME_DEPLOYMENT_FILE, resolution, arguments,
    )
    return {**resolution, "deployment": policy}
