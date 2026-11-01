# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

def _activation_result(
    *,
    plugin_root: Path,
    durable_home: Path,
    marketplace_id: str,
    plugin_id: str,
    current_environment: Mapping[str, Any],
    legacy_root: Path,
) -> dict[str, Any]:
    activation_entry = plugin_root / "installation-activation.json"
    activation_present = os.path.lexists(activation_entry)
    activation_is_file = activation_entry.is_file() and not activation_entry.is_symlink()
    activation_path = canonical_path(activation_entry)
    missing = {
        "state": "missing",
        "path": None,
        "actualMode": "legacy",
        "runtimeRoot": str(legacy_root),
        "context": None,
        "activationGeneration": None,
        "installGeneration": None,
        "reason": None,
    }
    if not activation_present:
        return missing
    result = dict(missing)
    result["path"] = str(activation_path)
    if not activation_is_file:
        result.update(
            state="invalid",
            actualMode=None,
            runtimeRoot=None,
            reason="activation-invalid",
        )
        return result
    try:
        activation = read_json(activation_path)
        if not isinstance(activation, Mapping):
            _fail("Installation activation must be a JSON object.")
        if _exact_property(activation, "schema") != ACTIVATION_SCHEMA:
            _fail(f"Installation activation schema must be '{ACTIVATION_SCHEMA}'.")
        version = _exact_property(activation, "version")
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            _fail("Installation activation version must be 1.")
        if _required_string(
            activation, "marketplaceId", "installation activation"
        ) != marketplace_id:
            _fail("Installation activation marketplaceId does not match its cell.")
        if _required_string(
            activation, "pluginId", "installation activation"
        ) != plugin_id:
            _fail("Installation activation pluginId does not match its plugin root.")
        mode = _required_string(activation, "mode", "installation activation")
        state = _required_string(activation, "state", "installation activation")
        if (mode, state) not in {
            ("namespaced", "active"),
            ("legacy", "deactivated"),
        }:
            _fail("Installation activation mode/state pair is invalid.")
        _, foreign = _validate_environment_record(
            _exact_property(activation, "environment"),
            current_environment,
            "installation activation.environment",
        )
        if foreign:
            result.update(
                state="foreign",
                actualMode=None,
                runtimeRoot=None,
                reason="foreign-environment",
            )
            return result
        context_text = _required_string(
            activation, "context", "installation activation"
        )
        context_path = Path(context_text)
        if not _path_is_fully_qualified(context_path):
            _fail("Installation activation context must be absolute.")
        canonical_context = canonical_path(plugin_root / "install.json")
        if not paths_equal(context_path, canonical_context):
            _fail("Installation activation context is not the canonical install.json.")
        namespace_generation = _required_integer(
            activation, "namespaceGeneration", "installation activation"
        )
        pinned_install_generation = _required_integer(
            activation, "installGeneration", "installation activation"
        )
        activation_generation = _required_integer(
            activation, "generation", "installation activation"
        )
        legacy = _exact_property(activation, "legacy")
        if not isinstance(legacy, Mapping):
            _fail("Installation activation legacy evidence must be a JSON object.")
        disposition = _required_string(
            legacy, "disposition", "installation activation.legacy"
        )
        if disposition not in {"absent", "quiesced", "retained-inert", "restored"}:
            _fail("Installation activation legacy disposition is invalid.")
        _validate_legacy_probe(
            _exact_property(legacy, "probe"),
            "installation activation.legacy.probe",
        )
        created_at = _parse_rfc3339_utc(
            _exact_property(activation, "createdAt"),
            "installation activation.createdAt",
        )
        updated_at = _parse_rfc3339_utc(
            _exact_property(activation, "updatedAt"),
            "installation activation.updatedAt",
        )
        if updated_at < created_at:
            _fail("Installation activation updatedAt precedes createdAt.")
        validated = validate_context_receipt(
            canonical_context,
            durable_home,
            expected_marketplace_id=marketplace_id,
            expected_plugin_id=plugin_id,
            expected_cell_root=plugin_root.parent.parent,
            environment={},
        )
        current_namespace_generation = int(validated["namespaceGeneration"])
        current_install_generation = int(validated["generation"])
        actual_mode = "namespaced" if mode == "namespaced" else "legacy"
        runtime_root = plugin_root if actual_mode == "namespaced" else legacy_root
        result.update(
            actualMode=actual_mode,
            runtimeRoot=str(runtime_root),
            context=str(canonical_context),
            activationGeneration=activation_generation,
            installGeneration=current_install_generation,
        )
        if (
            namespace_generation != current_namespace_generation
            or pinned_install_generation != current_install_generation
        ):
            result.update(state="revalidation", reason="revalidation-required")
            return result
        result.update(state="valid", reason=None)
        return result
    except InstallationContextError:
        result.update(
            state="invalid",
            actualMode=None,
            runtimeRoot=None,
            context=None,
            activationGeneration=None,
            installGeneration=None,
            reason="activation-invalid",
        )
        return result


def _publish_activation_receipt_locked(
    *,
    activation_path: Path,
    plugin_root: Path,
    durable: Path,
    marketplace_id: str,
    plugin_id: str,
    current_environment: Mapping[str, Any],
    resolved_legacy_root: Path,
    actual_namespace_generation: int,
    actual_install_generation: int,
    actual_activation_generation: int,
    activation_mode: str,
    activation_state: str,
    legacy_disposition: str,
    recorded_probe: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
    locks: Sequence[_DirectoryLock],
) -> dict[str, Any]:
    if actual_activation_generation >= MAX_RECEIPT_GENERATION:
        _fail(
            "installation-activation.json generation cannot be incremented; "
            "explicit repair is required."
        )
    next_generation = actual_activation_generation + 1
    now = _utc_now()
    desired: dict[str, Any] = {
        "schema": ACTIVATION_SCHEMA,
        "version": 1,
        "marketplaceId": marketplace_id,
        "pluginId": plugin_id,
        "mode": activation_mode,
        "state": activation_state,
        "environment": current_environment,
        "context": str(plugin_root / "install.json"),
        "namespaceGeneration": actual_namespace_generation,
        "installGeneration": actual_install_generation,
        "generation": next_generation,
        "legacy": {
            "disposition": legacy_disposition,
            "probe": recorded_probe,
        },
        "createdAt": (
            _exact_property(existing, "createdAt")
            if existing is not None
            else now
        ),
        "updatedAt": now,
    }
    _atomic_write_json(
        activation_path,
        desired,
        lock=tuple(locks),
    )

    published = _activation_result(
        plugin_root=plugin_root,
        durable_home=durable,
        marketplace_id=marketplace_id,
        plugin_id=plugin_id,
        current_environment=current_environment,
        legacy_root=resolved_legacy_root,
    )
    if published["state"] != "valid":
        _fail("Published activation receipt did not validate as current.")
    return {
        "action": "activation-cas",
        "status": "ready",
        "reason": "activation-published",
        "activation": published["path"],
        "activationChanged": True,
        "activationGeneration": published["activationGeneration"],
        "namespaceGeneration": actual_namespace_generation,
        "installGeneration": actual_install_generation,
        "environment": current_environment,
        "mode": activation_mode,
        "state": activation_state,
        "context": str(plugin_root / "install.json"),
        "operative": False,
    }


@_validation_scope
def compare_and_swap_activation(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    expected_namespace_generation: int,
    expected_install_generation: int,
    expected_activation_generation: int,
    activation_mode: str,
    activation_state: str,
    legacy_disposition: str,
    legacy_probe: Mapping[str, Any],
    durable_home: str | os.PathLike[str] | None = None,
    legacy_root: str | os.PathLike[str] | None = None,
    maintenance_token: str | None = None,
    environment: Mapping[str, str] | None = None,
    os_profile: str | os.PathLike[str] | None = None,
    platform: str | None = None,
    wsl_distro: str | None = None,
) -> dict[str, Any]:
    """Atomically publish a generation-pinned activation receipt."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    if (activation_mode, activation_state) not in {
        ("namespaced", "active"),
        ("legacy", "deactivated"),
    }:
        _fail("Activation mode/state pair is invalid.")
    if legacy_disposition not in {
        "absent",
        "quiesced",
        "retained-inert",
        "restored",
    }:
        _fail("Activation legacy disposition is invalid.")
    recorded_probe = _validate_legacy_probe(
        legacy_probe,
        "activation legacy probe",
    )
    expected_generations = {
        "namespace": expected_namespace_generation,
        "install": expected_install_generation,
        "activation": expected_activation_generation,
    }
    for name, value in expected_generations.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail(f"Expected {name} generation must be a non-negative integer.")

    caller_environment = environment if environment is not None else os.environ
    current_environment, profile = _current_environment(
        environment=caller_environment,
        os_profile=os_profile,
        platform=platform,
        wsl_distro=wsl_distro,
    )
    durable = canonical_path(
        durable_home
        or Path(caller_environment.get("HOME") or Path.home())
        / ".copilot-extensions"
    )
    context_path = Path(context)
    if not _path_is_fully_qualified(context_path):
        _fail("Activation context must be absolute.")
    validated = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    marketplace_id = _string_property(validated, "marketplaceId")
    plugin_id = _string_property(validated, "pluginId")
    cell_root = canonical_path(_string_property(validated, "cellRoot"))
    plugin_root = canonical_path(_string_property(validated, "pluginRoot"))
    install_path = canonical_path(_string_property(validated, "installReceipt"))
    activation_path = plugin_root / "installation-activation.json"
    legacy_root_value = legacy_root or (
        Path(current_environment["homeRealPath"]) / f".{plugin_id}"
    )
    if not _path_is_fully_qualified(legacy_root_value):
        _fail("Activation legacy root must be absolute.")
    resolved_legacy_root = canonical_path(legacy_root_value)

    genesis_lock = _DirectoryLock(
        durable / "marketplaces" / ".locks" / f"{marketplace_id}.genesis",
        kind="genesis",
        marketplace_id=marketplace_id,
    )
    install_lock = _DirectoryLock(
        cell_root / ".locks" / f"{plugin_id}.install.lock",
        kind="install",
        marketplace_id=marketplace_id,
        plugin_id=plugin_id,
    )
    with genesis_lock, install_lock:
        _require_management_authorization(
            profile=profile,
            plugin_root=plugin_root,
            maintenance_token=maintenance_token,
            current_time=datetime.now(timezone.utc),
            host=socket.gethostname(),
            pid_is_live=_pid_is_live,
        )
        validated = validate_context_receipt(
            install_path,
            durable,
            expected_marketplace_id=marketplace_id,
            expected_plugin_id=plugin_id,
            expected_cell_root=cell_root,
            environment={},
        )
        actual_namespace_generation = int(validated["namespaceGeneration"])
        actual_install_generation = int(validated["generation"])
        activation = _activation_result(
            plugin_root=plugin_root,
            durable_home=durable,
            marketplace_id=marketplace_id,
            plugin_id=plugin_id,
            current_environment=current_environment,
            legacy_root=resolved_legacy_root,
        )
        existing: Mapping[str, Any] | None = None
        if activation["state"] == "missing":
            actual_activation_generation = 0
        elif activation["state"] == "foreign":
            _fail("Existing activation receipt belongs to a foreign environment.")
        elif activation["state"] == "invalid":
            _fail("Existing activation receipt is invalid.")
        else:
            actual_activation_generation = int(activation["activationGeneration"])
            loaded = read_json(activation_path)
            if not isinstance(loaded, Mapping):
                _fail("Existing activation receipt must be a JSON object.")
            existing = loaded

        actual_generations = {
            "namespace": actual_namespace_generation,
            "install": actual_install_generation,
            "activation": actual_activation_generation,
        }
        if actual_generations != expected_generations:
            return {
                "action": "activation-cas",
                "status": "revalidation-required",
                "reason": "generation-changed",
                "activation": (
                    str(canonical_path(activation_path))
                    if os.path.lexists(activation_path)
                    else None
                ),
                "activationChanged": False,
                "activationGeneration": actual_activation_generation,
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "expectedActivationGeneration": expected_activation_generation,
                "expectedNamespaceGeneration": expected_namespace_generation,
                "expectedInstallGeneration": expected_install_generation,
                "operative": False,
            }

        namespace_receipt = read_json(validated["namespaceReceipt"])
        if not isinstance(namespace_receipt, Mapping):
            _fail("namespace.json must be a JSON object.")
        if (
            _string_property(namespace_receipt, "state") != "active"
            or _string_property(validated, "state") != "active"
        ):
            _fail("Activation requires active namespace and install receipts.")
        return _publish_activation_receipt_locked(
            activation_path=activation_path,
            plugin_root=plugin_root,
            durable=durable,
            marketplace_id=marketplace_id,
            plugin_id=plugin_id,
            current_environment=current_environment,
            resolved_legacy_root=resolved_legacy_root,
            actual_namespace_generation=actual_namespace_generation,
            actual_install_generation=actual_install_generation,
            actual_activation_generation=actual_activation_generation,
            activation_mode=activation_mode,
            activation_state=activation_state,
            legacy_disposition=legacy_disposition,
            recorded_probe=recorded_probe,
            existing=existing,
            locks=(genesis_lock, install_lock),
        )
