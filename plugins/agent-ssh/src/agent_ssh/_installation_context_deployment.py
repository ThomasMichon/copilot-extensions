# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

RUNTIME_DEPLOYMENT_SCHEMA = "copilot-extensions.runtime-deployment"
RUNTIME_DEPLOYMENT_FILE = "runtime-deployment.json"
RUNTIME_PROFILE_RECOVERY = (
    "Runtime profile intent requires a profile-aware construction lifecycle; "
    "ordinary plugin publication cannot ignore it."
)


def _runtime_profile_settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not set(value) <= {
        "allowSourceFallback", "backend",
    }:
        _fail("runtimeProfiles supports only allowSourceFallback and backend.")
    result = dict(value)
    if "allowSourceFallback" in result and not isinstance(result["allowSourceFallback"], bool):
        _fail("runtimeProfiles.allowSourceFallback must be a JSON boolean.")
    if "backend" in result and (
        not isinstance(result["backend"], str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", result["backend"]) is None
    ):
        _fail("runtimeProfiles.backend must be an explicit opaque capability reference.")
    return result


def _runtime_profile_selection(
    settings: Mapping[str, Any], *, backend: str | None,
    retained_backend: str | None, existing_installation: bool,
) -> dict[str, Any]:
    settings = _runtime_profile_settings(settings)
    for selection in (backend, retained_backend):
        if selection is not None:
            _runtime_profile_settings({"backend": selection})
    if not isinstance(existing_installation, bool):
        _fail("existing_installation must be a boolean.")
    configured = settings.get("backend")
    if backend is not None and configured is not None and backend != configured:
        _fail("Explicit runtime backend conflicts with the installation-owned policy.")
    selected = backend or configured or retained_backend
    if selected is None:
        if existing_installation:
            _fail("An unprofiled runtime requires an explicit migration capability.")
        selected = "cpu"
    return {
        "preferArtifact": True,
        "allowSourceFallback": settings.get("allowSourceFallback", True),
        "backend": selected,
    }


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
        required = {"schema", "version", "owner", "managed"}
        if not required <= set(policy) or not set(policy) <= required | {"runtimeProfiles"}:
            _fail("Runtime deployment policy has missing or unsupported fields.")
        profiles = (
            _runtime_profile_settings(_exact_property(policy, "runtimeProfiles"))
            if "runtimeProfiles" in policy else None
        )
        managed = _exact_property(policy, "managed")
        if not isinstance(managed, bool):
            _fail("Runtime deployment policy managed must be a JSON boolean.")
        owner = _exact_property(policy, "owner")
        expected = _deployment_owner(resolution, arguments)
        if not isinstance(owner, Mapping) or dict(owner) != expected:
            _fail("Runtime deployment policy belongs to another installation or environment.")
        result.update(
            state="valid", managed=managed,
            allowPluginPublication=not managed and profiles is None,
            reason=(
                "deployment-managed" if managed else
                "runtime-profile-consumer-required" if profiles is not None else
                "deployment-explicit-standalone"
            ),
            owner=expected,
            recovery=(
                "Use explicit installation management to install or update this runtime; "
                "plugin bootstrap and first use may not publish it."
                if managed else RUNTIME_PROFILE_RECOVERY if profiles is not None else None
            ),
        )
        if profiles is not None:
            result["runtimeProfiles"] = profiles
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


@_validation_scope
def resolve_runtime_profile_policy(
    *, backend: str | None = None, retained_backend: str | None = None,
    existing_installation: bool = False, **arguments: Any,
) -> dict[str, Any]:
    """Read owner-bound selection intent, not record approval or permission to install."""
    resolution = resolve_runtime_deployment(**arguments)
    deployment = resolution["deployment"]
    result: dict[str, Any] = {
        "status": "blocked", "reason": "runtime-profile-owner-unresolved",
        "owner": None, "selection": None, "policyId": None,
    }
    if resolution["status"] != "ready" or resolution.get("reason") == "activation-required":
        return {**resolution, "runtimeProfiles": result}
    if deployment["state"] not in {"missing", "valid"}:
        result["reason"] = deployment["reason"]
        return {**resolution, "runtimeProfiles": result}
    owner = _deployment_owner(resolution, arguments)
    selection = _runtime_profile_selection(
        deployment.get("runtimeProfiles", {}), backend=backend,
        retained_backend=retained_backend, existing_installation=existing_installation,
    )
    identity = {
        "schema": "copilot-extensions.runtime-profile-policy", "version": 1,
        "owner": owner, "selection": selection,
        "managed": deployment["managed"],
        "allowPluginPublication": deployment["allowPluginPublication"],
        "profileAwarePublicationRequired": "runtimeProfiles" in deployment,
    }
    digest = hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")).hexdigest()
    result.update(
        status="ready", reason="runtime-profile-policy-resolved",
        owner=owner, selection=selection, policyId=f"sha256:{digest}",
    )
    return {**resolution, "runtimeProfiles": result}
