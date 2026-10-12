# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

def attribute_legacy_state(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    legacy_root: str | os.PathLike[str],
    legacy_items: Sequence[Mapping[str, Any]],
    legacy_lock: AbstractContextManager[Any],
    durable_home: str | os.PathLike[str] | None = None,
    maintenance_token: str | None = None,
    environment: Mapping[str, str] | None = None,
    os_profile: str | os.PathLike[str] | None = None,
    platform: str | None = None,
    wsl_distro: str | None = None,
) -> dict[str, Any]:
    """Explicitly attribute clear legacy state to one destination cell."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
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
        _fail("Legacy attribution context must be absolute.")
    legacy_root_path = Path(legacy_root)
    if not _path_is_fully_qualified(legacy_root_path):
        _fail("Legacy attribution legacy root must be absolute.")
    legacy_root_path = Path(os.path.abspath(os.fspath(legacy_root_path)))
    items = _validate_legacy_attribution_items(legacy_items)
    validated_context = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    marketplace_id = _string_property(validated_context, "marketplaceId")
    plugin_id = _string_property(validated_context, "pluginId")
    cell_root = canonical_path(_string_property(validated_context, "cellRoot"))
    plugin_root = canonical_path(_string_property(validated_context, "pluginRoot"))
    install_path = canonical_path(_string_property(validated_context, "installReceipt"))
    activation_path = plugin_root / "installation-activation.json"
    present_items = [
        item for item in items if os.path.lexists(Path(item["path"]))
    ]
    absent_items = [
        {
            "kind": "path",
            "identity": item["identity"],
            "path": str(item["path"]),
            "classification": "absent",
            "reason": "legacy-absent",
        }
        for item in items
        if not os.path.lexists(Path(item["path"]))
    ]
    if os.path.lexists(legacy_root_path) and _is_link_or_junction(legacy_root_path):
        return {
            "action": "attribute-legacy",
            "status": "preserved",
            "reason": "linked-root",
            "legacyRoot": str(legacy_root_path),
            "context": str(install_path),
            "tombstone": None,
            "tombstoneChanged": False,
            "activation": None,
            "activationChanged": False,
            "activationGeneration": 0,
            "namespaceGeneration": None,
            "installGeneration": None,
            "attributedItems": [],
            "preservedItems": [
                {
                    "kind": "path",
                    "identity": item["identity"],
                    "path": str(item["path"]),
                    "classification": "ambiguous",
                    "reason": "linked-root",
                }
                for item in present_items
            ],
            "absentItems": absent_items,
            "operative": False,
        }
    resolved_legacy_root = canonical_path(legacy_root_path)
    tombstone_path = legacy_root_path / ".installation-ownership.json"

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
    with legacy_lock, genesis_lock, install_lock:
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

        namespace_receipt = read_json(validated["namespaceReceipt"])
        if not isinstance(namespace_receipt, Mapping):
            _fail("namespace.json must be a JSON object.")
        if (
            _string_property(namespace_receipt, "state") != "active"
            or _string_property(validated, "state") != "active"
        ):
            _fail("Legacy attribution requires active namespace and install receipts.")

        legacy = _tombstone_result(
            legacy_root=resolved_legacy_root,
            durable_home=durable,
            plugin_id=plugin_id,
            current_marketplace_id=marketplace_id,
            current_environment=current_environment,
        )
        if legacy["disposition"] == "owned-by-current-cell":
            present_paths = {
                str(item["path"])
                for item in present_items
                if not _is_link_or_junction(Path(item["path"]))
            }
            tombstone_value = read_json(tombstone_path)
            recorded_items: set[str] | None = None
            if isinstance(tombstone_value, Mapping):
                attribution = _property(tombstone_value, "attribution")
                recorded = _property(attribution, "items") if isinstance(attribution, Mapping) else None
                if isinstance(recorded, Sequence) and not isinstance(recorded, (str, bytes, bytearray)):
                    parsed: set[str] = set()
                    for entry in recorded:
                        if not isinstance(entry, Mapping):
                            recorded_items = None
                            break
                        if _required_string(entry, "kind", "legacy attribution") != "path":
                            recorded_items = None
                            break
                        parsed.add(_required_string(entry, "path", "legacy attribution"))
                    else:
                        recorded_items = parsed
            if recorded_items is not None and recorded_items != present_paths:
                return {
                    "action": "attribute-legacy",
                    "status": "preserved",
                    "reason": "attribution-mismatch",
                    "legacyRoot": str(legacy_root_path),
                    "context": str(install_path),
                    "tombstone": legacy["tombstone"],
                    "tombstoneChanged": False,
                    "activation": activation["path"] if activation["state"] == "valid" else None,
                    "activationChanged": False,
                    "activationGeneration": actual_activation_generation,
                    "namespaceGeneration": actual_namespace_generation,
                    "installGeneration": actual_install_generation,
                    "attributedItems": [],
                    "preservedItems": [
                        {
                            "kind": "path",
                            "identity": item["identity"],
                            "path": str(item["path"]),
                            "classification": "ambiguous",
                            "reason": "attribution-mismatch",
                        }
                        for item in present_items
                    ],
                    "absentItems": absent_items,
                    "operative": False,
                }
            already_attributed = [
                {
                    "kind": "path",
                    "identity": item["identity"],
                    "path": str(item["path"]),
                    "classification": "already-attributed",
                    "reason": "owned-by-current-cell",
                }
                for item in present_items
            ]
            return {
                "action": "attribute-legacy",
                "status": "ready",
                "reason": "already-attributed",
                "legacyRoot": str(legacy_root_path),
                "context": str(install_path),
                "tombstone": legacy["tombstone"],
                "tombstoneChanged": False,
                "activation": activation["path"] if activation["state"] == "valid" else None,
                "activationChanged": False,
                "activationGeneration": actual_activation_generation,
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "attributedItems": already_attributed,
                "preservedItems": [],
                "absentItems": absent_items,
                "operative": False,
            }

        attributed_items: list[dict[str, Any]] = []
        preserved_items: list[dict[str, Any]] = []
        preserved_reason = None
        for item in items:
            path = Path(item["path"])
            if not os.path.lexists(path):
                continue
            entry = {
                "kind": "path",
                "identity": item["identity"],
                "path": str(path),
            }
            if _is_link_or_junction(path):
                preserved_reason = preserved_reason or "linked-path"
                preserved_items.append(
                    {
                        **entry,
                        "classification": "ambiguous",
                        "reason": "linked-path",
                    }
                )
                continue
            if legacy["disposition"] == "owned-by-other-cell":
                preserved_reason = preserved_reason or "owned-by-other-cell"
                preserved_items.append(
                    {
                        **entry,
                        "classification": "ambiguous",
                        "reason": "owned-by-other-cell",
                    }
                )
                continue
            if legacy["disposition"] == "orphaned-transfer":
                preserved_reason = preserved_reason or (
                    legacy["reason"] or "orphaned-transfer"
                )
                preserved_items.append(
                    {
                        **entry,
                        "classification": "orphaned",
                        "reason": legacy["reason"] or "orphaned-transfer",
                    }
                )
                continue
            attributed_items.append(
                {
                    **entry,
                    "classification": "attributable",
                    "reason": "explicit-destination",
                }
            )

        if preserved_items:
            return {
                "action": "attribute-legacy",
                "status": "preserved",
                "reason": preserved_reason or "preserved-for-diagnosis",
                "legacyRoot": str(legacy_root_path),
                "context": str(install_path),
                "tombstone": legacy["tombstone"],
                "tombstoneChanged": False,
                "activation": activation["path"] if activation["state"] == "valid" else None,
                "activationChanged": False,
                "activationGeneration": actual_activation_generation,
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "attributedItems": [],
                "preservedItems": preserved_items,
                "absentItems": absent_items,
                "operative": False,
            }

        if not attributed_items:
            return {
                "action": "attribute-legacy",
                "status": "ready",
                "reason": "no-legacy-state",
                "legacyRoot": str(legacy_root_path),
                "context": str(install_path),
                "tombstone": None,
                "tombstoneChanged": False,
                "activation": activation["path"] if activation["state"] == "valid" else None,
                "activationChanged": False,
                "activationGeneration": actual_activation_generation,
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "attributedItems": [],
                "preservedItems": [],
                "absentItems": absent_items,
                "operative": False,
            }

        if actual_activation_generation >= MAX_RECEIPT_GENERATION:
            _fail(
                "installation-activation.json generation cannot be incremented; "
                "explicit repair is required."
            )
        now = _utc_now()
        tombstone = {
            "schema": TOMBSTONE_SCHEMA,
            "version": 1,
            "marketplaceId": marketplace_id,
            "pluginId": plugin_id,
            "activation": {
                "path": str(canonical_path(activation_path)),
                "generation": actual_activation_generation + 1,
            },
            "environment": current_environment,
            "transferredAt": now,
            "attribution": {
                "kind": "explicit-legacy-attribution",
                "legacyRoot": str(legacy_root_path),
                "context": str(install_path),
                "items": [
                    {
                        "kind": item["kind"],
                        "identity": item["identity"],
                        "path": item["path"],
                    }
                    for item in attributed_items
                ],
            },
        }
        _atomic_write_json(
            tombstone_path,
            tombstone,
            lock=(genesis_lock, install_lock),
        )
        activation_result = _publish_activation_receipt_locked(
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
            activation_mode="namespaced",
            activation_state="active",
            legacy_disposition="retained-inert",
            recorded_probe={
                "declared": True,
                "result": "present",
                "checkedAt": now,
            },
            existing=existing,
            locks=(genesis_lock, install_lock),
        )
        published_legacy = _tombstone_result(
            legacy_root=resolved_legacy_root,
            durable_home=durable,
            plugin_id=plugin_id,
            current_marketplace_id=marketplace_id,
            current_environment=current_environment,
        )
        if published_legacy["disposition"] != "owned-by-current-cell":
            _fail("Published legacy ownership tombstone did not validate.")
        return {
            "action": "attribute-legacy",
            "status": "ready",
            "reason": "legacy-attributed",
            "legacyRoot": str(legacy_root_path),
            "context": str(install_path),
            "tombstone": str(legacy_root_path / ".installation-ownership.json"),
            "tombstoneChanged": True,
            "activation": activation_result["activation"],
            "activationChanged": True,
            "activationGeneration": activation_result["activationGeneration"],
            "namespaceGeneration": actual_namespace_generation,
            "installGeneration": actual_install_generation,
            "attributedItems": attributed_items,
            "preservedItems": [],
            "absentItems": absent_items,
            "operative": False,
        }


def _tombstone_result(
    *,
    legacy_root: Path,
    durable_home: Path,
    plugin_id: str | None,
    current_marketplace_id: str | None,
    current_environment: Mapping[str, Any],
) -> dict[str, Any]:
    tombstone_entry = legacy_root / ".installation-ownership.json"
    tombstone_present = os.path.lexists(tombstone_entry)
    tombstone_is_file = tombstone_entry.is_file() and not tombstone_entry.is_symlink()
    tombstone_path = canonical_path(tombstone_entry)
    result: dict[str, Any] = {
        "root": str(legacy_root),
        "tombstone": None,
        "disposition": "active",
        "ownerMarketplaceId": None,
        "status": None,
        "reason": None,
    }
    if not tombstone_present:
        return result
    result["tombstone"] = str(tombstone_path)
    if not tombstone_is_file:
        result.update(
            disposition="orphaned-transfer",
            status="orphaned-transfer",
            reason="orphaned-transfer",
        )
        return result
    try:
        tombstone = read_json(tombstone_path)
        if not isinstance(tombstone, Mapping):
            _fail("Legacy ownership tombstone must be a JSON object.")
        if _exact_property(tombstone, "schema") != TOMBSTONE_SCHEMA:
            _fail(f"Legacy ownership schema must be '{TOMBSTONE_SCHEMA}'.")
        version = _exact_property(tombstone, "version")
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            _fail("Legacy ownership version must be 1.")
        owner_marketplace_id = _required_string(
            tombstone, "marketplaceId", "legacy ownership"
        )
        _validate_marketplace_id(owner_marketplace_id)
        owner_plugin_id = _required_string(
            tombstone, "pluginId", "legacy ownership"
        )
        _assert_plugin_id(owner_plugin_id)
        if plugin_id is None or owner_plugin_id != plugin_id:
            _fail("Legacy ownership pluginId does not match the requested plugin.")
        _, foreign = _validate_environment_record(
            _exact_property(tombstone, "environment"),
            current_environment,
            "legacy ownership.environment",
        )
        if foreign:
            result.update(
                disposition="orphaned-transfer",
                ownerMarketplaceId=owner_marketplace_id,
                status="foreign-environment",
                reason="foreign-environment",
            )
            return result
        activation_reference = _exact_property(tombstone, "activation")
        if not isinstance(activation_reference, Mapping):
            _fail("Legacy ownership activation must be a JSON object.")
        activation_text = _required_string(
            activation_reference, "path", "legacy ownership.activation"
        )
        activation_pointer = Path(activation_text)
        if not _path_is_fully_qualified(activation_pointer):
            _fail("Legacy ownership activation.path must be absolute.")
        pinned_generation = _required_integer(
            activation_reference, "generation", "legacy ownership.activation"
        )
        _parse_rfc3339_utc(
            _exact_property(tombstone, "transferredAt"),
            "legacy ownership.transferredAt",
        )
        destination_plugin_root = canonical_path(
            durable_home
            / "marketplaces"
            / owner_marketplace_id
            / "plugins"
            / owner_plugin_id
        )
        canonical_activation = canonical_path(
            destination_plugin_root / "installation-activation.json"
        )
        if not paths_equal(activation_pointer, canonical_activation):
            _fail("Legacy ownership activation.path is not canonical.")
        destination = _activation_result(
            plugin_root=destination_plugin_root,
            durable_home=durable_home,
            marketplace_id=owner_marketplace_id,
            plugin_id=owner_plugin_id,
            current_environment=current_environment,
            legacy_root=legacy_root,
        )
        if (
            destination["state"] != "valid"
            or destination["actualMode"] != "namespaced"
            or destination["activationGeneration"] != pinned_generation
        ):
            _fail("Legacy ownership destination activation is not current and active.")
        disposition = (
            "owned-by-current-cell"
            if owner_marketplace_id == current_marketplace_id
            else "owned-by-other-cell"
        )
        result.update(
            disposition=disposition,
            ownerMarketplaceId=owner_marketplace_id,
        )
        return result
    except InstallationContextError:
        result.update(
            disposition="orphaned-transfer",
            status="orphaned-transfer",
            reason="orphaned-transfer",
        )
        return result


def _deactivation_record_path(
    plugin_root: Path,
    target_activation_generation: int,
) -> Path:
    return (
    plugin_root
    / DEACTIVATION_RECORDS_DIR
    / f"activation-{target_activation_generation}.json"
    )


def _load_owned_tombstone(
    *,
    tombstone_path: Path,
    install_path: Path,
    plugin_root: Path,
    marketplace_id: str,
    plugin_id: str,
    current_environment: Mapping[str, Any],
    expected_activation_generation: int,
) -> dict[str, Any] | None:
    if not os.path.lexists(tombstone_path):
        return None
    if tombstone_path.is_symlink() or not tombstone_path.is_file():
        _fail("Legacy ownership tombstone is invalid.")
    tombstone = read_json(tombstone_path)
    if not isinstance(tombstone, Mapping):
        _fail("Legacy ownership tombstone must be a JSON object.")
    if _exact_property(tombstone, "schema") != TOMBSTONE_SCHEMA:
        _fail(f"Legacy ownership schema must be '{TOMBSTONE_SCHEMA}'.")
    version = _exact_property(tombstone, "version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        _fail("Legacy ownership version must be 1.")
    if _required_string(tombstone, "marketplaceId", "legacy ownership") != marketplace_id:
        _fail("Legacy ownership tombstone belongs to another marketplace cell.")
    if _required_string(tombstone, "pluginId", "legacy ownership") != plugin_id:
        _fail("Legacy ownership tombstone belongs to another plugin.")
    _, foreign = _validate_environment_record(
        _exact_property(tombstone, "environment"),
        current_environment,
        "legacy ownership.environment",
    )
    if foreign:
        _fail("Legacy ownership tombstone belongs to a foreign environment.")
    activation_reference = _exact_property(tombstone, "activation")
    if not isinstance(activation_reference, Mapping):
        _fail("Legacy ownership activation must be a JSON object.")
    activation_text = _required_string(
        activation_reference,
        "path",
        "legacy ownership.activation",
    )
    activation_pointer = Path(activation_text)
    if not _path_is_fully_qualified(activation_pointer):
        _fail("Legacy ownership activation.path must be absolute.")
    canonical_activation = canonical_path(plugin_root / "installation-activation.json")
    if not paths_equal(activation_pointer, canonical_activation):
        _fail("Legacy ownership activation.path is not canonical.")
    pinned_generation = _required_integer(
        activation_reference,
        "generation",
        "legacy ownership.activation",
    )
    if pinned_generation != expected_activation_generation:
        _fail(
            "Legacy ownership tombstone does not match the requested "
            "activation generation."
        )
    transferred_at = _parse_rfc3339_utc(
        _exact_property(tombstone, "transferredAt"),
        "legacy ownership.transferredAt",
    ).isoformat().replace("+00:00", "Z")
    attribution = _property(tombstone, "attribution")
    if attribution is not None:
        if not isinstance(attribution, Mapping):
            _fail("Legacy ownership attribution must be a JSON object.")
        context_value = _property(attribution, "context")
        if context_value is not None:
            if not isinstance(context_value, str):
                _fail("Legacy ownership attribution context must be a string.")
            if not _path_is_fully_qualified(context_value):
                _fail("Legacy ownership attribution context must be absolute.")
            if not paths_equal(context_value, install_path):
                _fail("Legacy ownership attribution context is not canonical.")
    return {
        "path": str(canonical_path(tombstone_path)),
        "activationGeneration": pinned_generation,
        "transferredAt": transferred_at,
        "attribution": attribution,
        "document": tombstone,
    }


def _load_deactivation_record(
    *,
    record_path: Path,
    install_path: Path,
    plugin_root: Path,
    marketplace_id: str,
    plugin_id: str,
    current_environment: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not os.path.lexists(record_path):
        return None
    if record_path.is_symlink() or not record_path.is_file():
        _fail("Installation deactivation record is invalid.")
    record = read_json(record_path)
    if not isinstance(record, Mapping):
        _fail("Installation deactivation record must be a JSON object.")
    if _exact_property(record, "schema") != DEACTIVATION_SCHEMA:
        _fail(f"Installation deactivation schema must be '{DEACTIVATION_SCHEMA}'.")
    version = _exact_property(record, "version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        _fail("Installation deactivation version must be 1.")
    if _required_string(record, "marketplaceId", "installation deactivation") != marketplace_id:
        _fail("Installation deactivation marketplaceId does not match its cell.")
    if _required_string(record, "pluginId", "installation deactivation") != plugin_id:
        _fail("Installation deactivation pluginId does not match its plugin root.")
    context_value = _required_string(record, "context", "installation deactivation")
    if not _path_is_fully_qualified(context_value):
        _fail("Installation deactivation context must be absolute.")
    if not paths_equal(context_value, install_path):
        _fail("Installation deactivation context is not canonical.")
    _, foreign = _validate_environment_record(
        _exact_property(record, "environment"),
        current_environment,
        "installation deactivation.environment",
    )
    if foreign:
        _fail("Installation deactivation belongs to a foreign environment.")
    target = _exact_property(record, "target")
    if not isinstance(target, Mapping):
        _fail("Installation deactivation target must be a JSON object.")
    target_kind = _required_string(
        target,
        "kind",
        "installation deactivation target",
    )
    if target_kind not in {"cell-deactivation", "legacy-attribution-rollback"}:
        _fail("Installation deactivation target kind is invalid.")
    target_activation = _exact_property(target, "activation")
    if not isinstance(target_activation, Mapping):
        _fail("Installation deactivation target activation must be a JSON object.")
    activation_path = _required_string(
        target_activation,
        "path",
        "installation deactivation target activation",
    )
    if not _path_is_fully_qualified(activation_path):
        _fail("Installation deactivation target activation path must be absolute.")
    if not paths_equal(activation_path, plugin_root / "installation-activation.json"):
        _fail("Installation deactivation target activation path is not canonical.")
    target_mode = _required_string(
        target_activation,
        "mode",
        "installation deactivation target activation",
    )
    target_state = _required_string(
        target_activation,
        "state",
        "installation deactivation target activation",
    )
    if (target_mode, target_state) != ("namespaced", "active"):
        _fail("Installation deactivation target activation state is invalid.")
    target_generation = _required_integer(
        target_activation,
        "generation",
        "installation deactivation target activation",
    )
    target_namespace_generation = _required_integer(
        target_activation,
        "namespaceGeneration",
        "installation deactivation target activation",
    )
    target_install_generation = _required_integer(
        target_activation,
        "installGeneration",
        "installation deactivation target activation",
    )
    target_legacy_disposition = _required_string(
        target_activation,
        "legacyDisposition",
        "installation deactivation target activation",
    )
    if target_legacy_disposition not in {
        "absent",
        "quiesced",
        "retained-inert",
        "restored",
    }:
        _fail("Installation deactivation target legacy disposition is invalid.")
    target_tombstone = _property(target, "tombstone")
    if target_tombstone is None:
        tombstone = None
    else:
        if not isinstance(target_tombstone, Mapping):
            _fail("Installation deactivation target tombstone must be a JSON object.")
        tombstone_path = _required_string(
            target_tombstone,
            "path",
            "installation deactivation target tombstone",
        )
        if not _path_is_fully_qualified(tombstone_path):
            _fail("Installation deactivation target tombstone path must be absolute.")
        tombstone = {
            "path": tombstone_path,
            "activationGeneration": _required_integer(
                target_tombstone,
                "activationGeneration",
                "installation deactivation target tombstone",
            ),
            "transferredAt": _parse_rfc3339_utc(
                _exact_property(target_tombstone, "transferredAt"),
                "installation deactivation target tombstone.transferredAt",
            ).isoformat().replace("+00:00", "Z"),
            "attribution": _property(target_tombstone, "attribution"),
        }
    result = _exact_property(record, "result")
    if not isinstance(result, Mapping):
        _fail("Installation deactivation result must be a JSON object.")
    result_activation = _exact_property(result, "activation")
    if not isinstance(result_activation, Mapping):
        _fail("Installation deactivation result activation must be a JSON object.")
    result_path = _required_string(
        result_activation,
        "path",
        "installation deactivation result activation",
    )
    if not _path_is_fully_qualified(result_path):
        _fail("Installation deactivation result activation path must be absolute.")
    if not paths_equal(result_path, plugin_root / "installation-activation.json"):
        _fail("Installation deactivation result activation path is not canonical.")
    result_mode = _required_string(
        result_activation,
        "mode",
        "installation deactivation result activation",
    )
    result_state = _required_string(
        result_activation,
        "state",
        "installation deactivation result activation",
    )
    if (result_mode, result_state) != ("legacy", "deactivated"):
        _fail("Installation deactivation result activation state is invalid.")
    result_generation = _required_integer(
        result_activation,
        "generation",
        "installation deactivation result activation",
    )
    result_legacy_disposition = _required_string(
        result_activation,
        "legacyDisposition",
        "installation deactivation result activation",
    )
    if result_legacy_disposition not in {
        "absent",
        "quiesced",
        "retained-inert",
        "restored",
    }:
        _fail("Installation deactivation result legacy disposition is invalid.")
    result_tombstone = _property(result, "tombstone")
    cleared_tombstone = False
    if result_tombstone is not None:
        if not isinstance(result_tombstone, Mapping):
            _fail("Installation deactivation result tombstone must be a JSON object.")
        cleared_value = _property(result_tombstone, "cleared", False)
        if not isinstance(cleared_value, bool):
            _fail("Installation deactivation result tombstone.cleared must be a boolean.")
        cleared_tombstone = cleared_value
    _parse_rfc3339_utc(
        _exact_property(record, "createdAt"),
        "installation deactivation.createdAt",
    )
    return {
        "path": str(canonical_path(record_path)),
        "targetKind": target_kind,
        "targetActivationGeneration": target_generation,
        "targetNamespaceGeneration": target_namespace_generation,
        "targetInstallGeneration": target_install_generation,
        "targetLegacyDisposition": target_legacy_disposition,
        "targetTombstone": tombstone,
        "resultActivationGeneration": result_generation,
        "resultLegacyDisposition": result_legacy_disposition,
        "clearedTombstone": cleared_tombstone,
        "document": record,
    }
