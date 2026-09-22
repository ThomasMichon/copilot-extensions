# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
def _retirement_record_path(
    plugin_root: Path,
    target_activation_generation: int,
    retirement_id: str,
) -> Path:
    return (
        plugin_root
        / RETIREMENT_RECORDS_DIR
        / f"activation-{target_activation_generation}--{retirement_id}.json"
    )


def _validate_retirement_health_report(
    value: Any,
    *,
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be a JSON object.")
    kind = _required_string(value, "kind", label)
    status = _required_string(value, "status", label)
    reason = _required_string(value, "reason", label)
    checked_at = _parse_rfc3339_utc(
        _exact_property(value, "checkedAt"),
        f"{label}.checkedAt",
    ).isoformat().replace("+00:00", "Z")
    evidence = _property(value, "evidence")
    if evidence is not None and not isinstance(evidence, Mapping):
        _fail(f"{label}.evidence must be a JSON object or null.")
    return {
        "kind": kind,
        "status": status,
        "reason": reason,
        "checkedAt": checked_at,
        "evidence": evidence,
    }


def _load_retirement_record(
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
        _fail("Legacy retirement record is invalid.")
    record = read_json(record_path)
    if not isinstance(record, Mapping):
        _fail("Legacy retirement record must be a JSON object.")
    if _exact_property(record, "schema") != RETIREMENT_SCHEMA:
        _fail(f"Legacy retirement schema must be '{RETIREMENT_SCHEMA}'.")
    version = _exact_property(record, "version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        _fail("Legacy retirement version must be 1.")
    if _required_string(record, "marketplaceId", "legacy retirement") != marketplace_id:
        _fail("Legacy retirement marketplaceId does not match its cell.")
    if _required_string(record, "pluginId", "legacy retirement") != plugin_id:
        _fail("Legacy retirement pluginId does not match its plugin root.")
    context_value = _required_string(record, "context", "legacy retirement")
    if not _path_is_fully_qualified(context_value):
        _fail("Legacy retirement context must be absolute.")
    if not paths_equal(context_value, install_path):
        _fail("Legacy retirement context is not canonical.")
    _, foreign = _validate_environment_record(
        _exact_property(record, "environment"),
        current_environment,
        "legacy retirement.environment",
    )
    if foreign:
        _fail("Legacy retirement belongs to a foreign environment.")
    target = _exact_property(record, "target")
    if not isinstance(target, Mapping):
        _fail("Legacy retirement target must be a JSON object.")
    retirement_id = _validate_retirement_id(
        _exact_property(target, "id")
    )
    target_activation = _exact_property(target, "activation")
    if not isinstance(target_activation, Mapping):
        _fail("Legacy retirement target activation must be a JSON object.")
    activation_path = _required_string(
        target_activation,
        "path",
        "legacy retirement target activation",
    )
    if not _path_is_fully_qualified(activation_path):
        _fail("Legacy retirement target activation path must be absolute.")
    if not paths_equal(activation_path, plugin_root / "installation-activation.json"):
        _fail("Legacy retirement target activation path is not canonical.")
    target_mode = _required_string(
        target_activation,
        "mode",
        "legacy retirement target activation",
    )
    target_state = _required_string(
        target_activation,
        "state",
        "legacy retirement target activation",
    )
    if (target_mode, target_state) != ("namespaced", "active"):
        _fail("Legacy retirement target activation state is invalid.")
    target_generation = _required_integer(
        target_activation,
        "generation",
        "legacy retirement target activation",
    )
    target_namespace_generation = _required_integer(
        target_activation,
        "namespaceGeneration",
        "legacy retirement target activation",
    )
    target_install_generation = _required_integer(
        target_activation,
        "installGeneration",
        "legacy retirement target activation",
    )
    target_legacy_disposition = _required_string(
        target_activation,
        "legacyDisposition",
        "legacy retirement target activation",
    )
    if target_legacy_disposition not in {
        "absent",
        "quiesced",
        "retained-inert",
        "restored",
    }:
        _fail("Legacy retirement target legacy disposition is invalid.")
    target_tombstone = _exact_property(target, "tombstone")
    if not isinstance(target_tombstone, Mapping):
        _fail("Legacy retirement target tombstone must be a JSON object.")
    tombstone_path = _required_string(
        target_tombstone,
        "path",
        "legacy retirement target tombstone",
    )
    if not _path_is_fully_qualified(tombstone_path):
        _fail("Legacy retirement target tombstone path must be absolute.")
    tombstone = {
        "path": tombstone_path,
        "activationGeneration": _required_integer(
            target_tombstone,
            "activationGeneration",
            "legacy retirement target tombstone",
        ),
        "transferredAt": _parse_rfc3339_utc(
            _exact_property(target_tombstone, "transferredAt"),
            "legacy retirement target tombstone.transferredAt",
        ).isoformat().replace("+00:00", "Z"),
        "attribution": _property(target_tombstone, "attribution"),
    }
    health = _validate_retirement_health_report(
        _exact_property(target, "health"),
        label="legacy retirement target health",
    )
    items_value = _exact_property(target, "items")
    if not isinstance(items_value, Sequence) or isinstance(
        items_value, (str, bytes, bytearray)
    ):
        _fail("Legacy retirement target items must be an array.")
    target_items = _validate_legacy_attribution_items(items_value)
    result = _exact_property(record, "result")
    if not isinstance(result, Mapping):
        _fail("Legacy retirement result must be a JSON object.")
    retired_items_value = _exact_property(result, "items")
    if not isinstance(retired_items_value, Sequence) or isinstance(
        retired_items_value, (str, bytes, bytearray)
    ):
        _fail("Legacy retirement result items must be an array.")
    retired_items: list[dict[str, Any]] = []
    for index, item in enumerate(retired_items_value):
        if not isinstance(item, Mapping):
            _fail(
                "Legacy retirement result items must be JSON objects "
                f"(item {index + 1})."
            )
        validated = _validate_legacy_attribution_items([item])[0]
        disposition = _required_string(
            item,
            "disposition",
            "legacy retirement result item",
        )
        if disposition not in {"removed", "already-absent"}:
            _fail("Legacy retirement result item disposition is invalid.")
        retired_items.append(
            {
                **validated,
                "disposition": disposition,
            }
        )
    _parse_rfc3339_utc(
        _exact_property(record, "createdAt"),
        "legacy retirement.createdAt",
    )
    return {
        "path": str(canonical_path(record_path)),
        "retirementId": retirement_id,
        "targetActivationGeneration": target_generation,
        "targetNamespaceGeneration": target_namespace_generation,
        "targetInstallGeneration": target_install_generation,
        "targetTombstone": tombstone,
        "targetHealth": health,
        "targetItems": target_items,
        "retiredItems": retired_items,
        "document": record,
    }


def _write_deactivation_record_locked(
    *,
    record_path: Path,
    install_path: Path,
    plugin_root: Path,
    marketplace_id: str,
    plugin_id: str,
    current_environment: Mapping[str, Any],
    target_kind: str,
    target_activation: Mapping[str, Any],
    target_tombstone: Mapping[str, Any] | None,
    result_activation_generation: int,
    result_legacy_disposition: str,
    locks: Sequence[_DirectoryLock],
) -> dict[str, Any]:
    now = _utc_now()
    desired: dict[str, Any] = {
    "schema": DEACTIVATION_SCHEMA,
    "version": 1,
    "marketplaceId": marketplace_id,
    "pluginId": plugin_id,
    "context": str(install_path),
    "environment": current_environment,
    "target": {
        "kind": target_kind,
        "activation": target_activation,
        "tombstone": target_tombstone,
    },
    "result": {
        "activation": {
            "path": str(canonical_path(plugin_root / "installation-activation.json")),
            "generation": result_activation_generation,
            "mode": "legacy",
            "state": "deactivated",
            "legacyDisposition": result_legacy_disposition,
        },
        "tombstone": (
            None
            if target_tombstone is None
            else {
                "path": target_tombstone["path"],
                "cleared": True,
                "clearedAt": now,
            }
        ),
    },
    "createdAt": now,
    }
    existing = _load_deactivation_record(
    record_path=record_path,
    install_path=install_path,
    plugin_root=plugin_root,
    marketplace_id=marketplace_id,
    plugin_id=plugin_id,
    current_environment=current_environment,
    )
    if existing is not None:
        if existing["document"] != desired:
            _fail("Installation deactivation record already exists with different content.")
        return {"path": existing["path"], "changed": False}
    _atomic_write_json(record_path, desired)
    published = _load_deactivation_record(
        record_path=record_path,
        install_path=install_path,
        plugin_root=plugin_root,
        marketplace_id=marketplace_id,
        plugin_id=plugin_id,
        current_environment=current_environment,
    )
    if published is None:
        _fail("Published installation deactivation record did not validate.")
    return {"path": published["path"], "changed": True}


@_validation_scope
def deactivate_installation(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    expected_namespace_generation: int,
    expected_install_generation: int,
    expected_activation_generation: int,
    legacy_root: str | os.PathLike[str],
    legacy_probe: Mapping[str, Any],
    expected_tombstone_activation_generation: int | None = None,
    expect_tombstone_absent: bool = False,
    legacy_lock: AbstractContextManager[Any] | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    maintenance_token: str | None = None,
    environment: Mapping[str, str] | None = None,
    os_profile: str | os.PathLike[str] | None = None,
    platform: str | None = None,
    wsl_distro: str | None = None,
) -> dict[str, Any]:
    """Publish a legacy/deactivated activation and optional tombstone rollback."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    recorded_probe = _validate_legacy_probe(
    legacy_probe,
    "installation deactivation legacy probe",
    )
    if (expected_tombstone_activation_generation is None) == (not expect_tombstone_absent):
        _fail(
            "Installation deactivation requires exactly one explicit target: "
            "--expected-tombstone-activation-generation or --expect-tombstone-absent."
        )
    expected_generations = {
        "namespace": expected_namespace_generation,
        "install": expected_install_generation,
        "activation": expected_activation_generation,
    }
    for name, value in expected_generations.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail(f"Expected {name} generation must be a non-negative integer.")
    if expected_tombstone_activation_generation is not None:
        if (
            isinstance(expected_tombstone_activation_generation, bool)
            or not isinstance(expected_tombstone_activation_generation, int)
            or expected_tombstone_activation_generation < 0
        ):
            _fail("Expected tombstone activation generation must be a non-negative integer.")
        if expected_tombstone_activation_generation != expected_activation_generation:
            _fail(
                "Installation deactivation requires the tombstone target generation "
                "to match the target activation generation."
            )

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
        _fail("Installation deactivation context must be absolute.")
    legacy_root_path = Path(legacy_root)
    if not _path_is_fully_qualified(legacy_root_path):
        _fail("Installation deactivation legacy root must be absolute.")
    legacy_root_path = Path(os.path.abspath(os.fspath(legacy_root_path)))
    resolved_legacy_root = canonical_path(legacy_root_path)

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
    tombstone_path = legacy_root_path / ".installation-ownership.json"
    record_path = _deactivation_record_path(plugin_root, expected_activation_generation)

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
    with (legacy_lock or nullcontext()), genesis_lock, install_lock:
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
        deactivation_record = _load_deactivation_record(
            record_path=record_path,
            install_path=install_path,
            plugin_root=plugin_root,
            marketplace_id=marketplace_id,
            plugin_id=plugin_id,
            current_environment=current_environment,
        )
        activation = _activation_result(
            plugin_root=plugin_root,
            durable_home=durable,
            marketplace_id=marketplace_id,
            plugin_id=plugin_id,
            current_environment=current_environment,
            legacy_root=resolved_legacy_root,
        )
        if activation["state"] == "missing":
            actual_activation_generation = 0
            existing = None
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

        target_kind = (
            "legacy-attribution-rollback"
            if expected_tombstone_activation_generation is not None
            else "cell-deactivation"
        )
        if (
            activation["state"] == "valid"
            and activation["actualMode"] == "legacy"
            and existing is not None
        ):
            if deactivation_record is None:
                return {
                    "action": "deactivate-installation",
                    "status": "preserved",
                    "reason": "deactivation-record-missing",
                    "legacyRoot": str(legacy_root_path),
                    "context": str(install_path),
                    "record": None,
                    "recordChanged": False,
                    "tombstone": (
                        str(canonical_path(tombstone_path))
                        if os.path.lexists(tombstone_path)
                        else None
                    ),
                    "tombstoneChanged": False,
                    "activation": activation["path"],
                    "activationChanged": False,
                    "activationGeneration": actual_activation_generation,
                    "namespaceGeneration": actual_namespace_generation,
                    "installGeneration": actual_install_generation,
                    "target": target_kind,
                    "operative": False,
                }
            existing_legacy = _exact_property(existing, "legacy")
            if not isinstance(existing_legacy, Mapping):
                _fail("Existing activation legacy evidence must be a JSON object.")
            replay_matches = (
                deactivation_record["targetKind"] == target_kind
                and deactivation_record["targetActivationGeneration"]
                == expected_activation_generation
                and deactivation_record["targetNamespaceGeneration"]
                == expected_namespace_generation
                and deactivation_record["targetInstallGeneration"]
                == expected_install_generation
                and deactivation_record["resultActivationGeneration"]
                == actual_activation_generation
                and deactivation_record["resultLegacyDisposition"]
                == _required_string(
                    existing_legacy,
                    "disposition",
                    "installation activation.legacy",
                )
                and (
                    (
                        expected_tombstone_activation_generation is None
                        and deactivation_record["targetTombstone"] is None
                        and not os.path.lexists(tombstone_path)
                    )
                    or (
                        expected_tombstone_activation_generation is not None
                        and deactivation_record["targetTombstone"] is not None
                        and deactivation_record["targetTombstone"]["activationGeneration"]
                        == expected_tombstone_activation_generation
                        and deactivation_record["clearedTombstone"]
                        and not os.path.lexists(tombstone_path)
                    )
                )
            )
            if replay_matches:
                return {
                    "action": "deactivate-installation",
                    "status": "ready",
                    "reason": (
                        "already-rolled-back"
                        if expected_tombstone_activation_generation is not None
                        else "already-deactivated"
                    ),
                    "legacyRoot": str(legacy_root_path),
                    "context": str(install_path),
                    "record": deactivation_record["path"],
                    "recordChanged": False,
                    "tombstone": None,
                    "tombstoneChanged": False,
                    "activation": activation["path"],
                    "activationChanged": False,
                    "activationGeneration": actual_activation_generation,
                    "namespaceGeneration": actual_namespace_generation,
                    "installGeneration": actual_install_generation,
                    "target": target_kind,
                    "operative": False,
                }

        actual_generations = {
            "namespace": actual_namespace_generation,
            "install": actual_install_generation,
            "activation": actual_activation_generation,
        }
        if actual_generations != expected_generations:
            return {
                "action": "deactivate-installation",
                "status": "revalidation-required",
                "reason": "generation-changed",
                "legacyRoot": str(legacy_root_path),
                "context": str(install_path),
                "record": (
                    deactivation_record["path"]
                    if deactivation_record is not None
                    else None
                ),
                "recordChanged": False,
                "tombstone": (
                    str(canonical_path(tombstone_path))
                    if os.path.lexists(tombstone_path)
                    else None
                ),
                "tombstoneChanged": False,
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
                "target": target_kind,
                "operative": False,
            }

        if activation["state"] != "valid" or activation["actualMode"] != "namespaced":
            _fail("Installation deactivation requires a valid active namespaced activation.")
        assert existing is not None
        namespace_receipt = read_json(validated["namespaceReceipt"])
        if not isinstance(namespace_receipt, Mapping):
            _fail("namespace.json must be a JSON object.")
        if (
            _string_property(namespace_receipt, "state") != "active"
            or _string_property(validated, "state") != "active"
        ):
            _fail("Installation deactivation requires active namespace and install receipts.")

        if expected_tombstone_activation_generation is not None:
            if legacy_lock is None:
                _fail(
                    "Legacy attribution rollback requires the caller to hold "
                    "the legacy lock."
                )
            tombstone = _load_owned_tombstone(
                tombstone_path=tombstone_path,
                install_path=install_path,
                plugin_root=plugin_root,
                marketplace_id=marketplace_id,
                plugin_id=plugin_id,
                current_environment=current_environment,
                expected_activation_generation=expected_tombstone_activation_generation,
            )
            if tombstone is None:
                return {
                    "action": "deactivate-installation",
                    "status": "preserved",
                    "reason": "tombstone-absent",
                    "legacyRoot": str(legacy_root_path),
                    "context": str(install_path),
                    "record": (
                        deactivation_record["path"]
                        if deactivation_record is not None
                        else None
                    ),
                    "recordChanged": False,
                    "tombstone": None,
                    "tombstoneChanged": False,
                    "activation": activation["path"],
                    "activationChanged": False,
                    "activationGeneration": actual_activation_generation,
                    "namespaceGeneration": actual_namespace_generation,
                    "installGeneration": actual_install_generation,
                    "target": target_kind,
                    "operative": False,
                }
            tombstone_evidence = _path_evidence(tombstone_path)
            if tombstone_evidence is None:
                _fail("Legacy ownership tombstone disappeared during validation.")
            legacy_disposition = "restored"
        else:
            if os.path.lexists(tombstone_path):
                return {
                    "action": "deactivate-installation",
                    "status": "preserved",
                    "reason": "tombstone-present",
                    "legacyRoot": str(legacy_root_path),
                    "context": str(install_path),
                    "record": (
                        deactivation_record["path"]
                        if deactivation_record is not None
                        else None
                    ),
                    "recordChanged": False,
                    "tombstone": str(canonical_path(tombstone_path)),
                    "tombstoneChanged": False,
                    "activation": activation["path"],
                    "activationChanged": False,
                    "activationGeneration": actual_activation_generation,
                    "namespaceGeneration": actual_namespace_generation,
                    "installGeneration": actual_install_generation,
                    "target": target_kind,
                    "operative": False,
                }
            tombstone = None
            if recorded_probe["result"] != "absent":
                return {
                    "action": "deactivate-installation",
                    "status": "preserved",
                    "reason": "legacy-state-not-explicitly-restored",
                    "legacyRoot": str(legacy_root_path),
                    "context": str(install_path),
                    "record": (
                        deactivation_record["path"]
                        if deactivation_record is not None
                        else None
                    ),
                    "recordChanged": False,
                    "tombstone": None,
                    "tombstoneChanged": False,
                    "activation": activation["path"],
                    "activationChanged": False,
                    "activationGeneration": actual_activation_generation,
                    "namespaceGeneration": actual_namespace_generation,
                    "installGeneration": actual_install_generation,
                    "target": target_kind,
                    "operative": False,
                }
            legacy_disposition = "absent"

        previous_legacy = _exact_property(existing, "legacy")
        if not isinstance(previous_legacy, Mapping):
            _fail("Existing activation legacy evidence must be a JSON object.")
        target_activation = {
            "path": str(canonical_path(activation_path)),
            "generation": actual_activation_generation,
            "mode": _required_string(existing, "mode", "installation activation"),
            "state": _required_string(existing, "state", "installation activation"),
            "namespaceGeneration": _required_integer(
                existing,
                "namespaceGeneration",
                "installation activation",
            ),
            "installGeneration": _required_integer(
                existing,
                "installGeneration",
                "installation activation",
            ),
            "legacyDisposition": _required_string(
                previous_legacy,
                "disposition",
                "installation activation.legacy",
            ),
        }
        if (target_activation["mode"], target_activation["state"]) != (
            "namespaced",
            "active",
        ):
            _fail("Installation deactivation target activation is not namespaced and active.")
        target_tombstone = (
            None
            if tombstone is None
            else {
                "path": tombstone["path"],
                "activationGeneration": tombstone["activationGeneration"],
                "transferredAt": tombstone["transferredAt"],
                "attribution": tombstone["attribution"],
            }
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
            activation_mode="legacy",
            activation_state="deactivated",
            legacy_disposition=legacy_disposition,
            recorded_probe=recorded_probe,
            existing=existing,
            locks=(genesis_lock, install_lock),
        )
        published_record = _write_deactivation_record_locked(
            record_path=record_path,
            install_path=install_path,
            plugin_root=plugin_root,
            marketplace_id=marketplace_id,
            plugin_id=plugin_id,
            current_environment=current_environment,
            target_kind=target_kind,
            target_activation=target_activation,
            target_tombstone=target_tombstone,
            result_activation_generation=activation_result["activationGeneration"],
            result_legacy_disposition=legacy_disposition,
            locks=(genesis_lock, install_lock),
        )
        tombstone_changed = False
        if tombstone is not None:
            if _path_evidence(tombstone_path) != tombstone_evidence:
                _fail("Legacy ownership tombstone changed before rollback completed.")
            tombstone_path.unlink()
            tombstone_changed = True
            if os.path.lexists(tombstone_path):
                _fail("Legacy ownership tombstone remained after rollback.")
        return {
            "action": "deactivate-installation",
            "status": "ready",
            "reason": (
                "legacy-attribution-rolled-back"
                if tombstone is not None
                else "cell-deactivated"
            ),
            "legacyRoot": str(legacy_root_path),
            "context": str(install_path),
            "record": published_record["path"],
            "recordChanged": published_record["changed"],
            "tombstone": (
                None
                if tombstone_changed
                else (tombstone["path"] if tombstone is not None else None)
            ),
            "tombstoneChanged": tombstone_changed,
            "activation": activation_result["activation"],
            "activationChanged": True,
            "activationGeneration": activation_result["activationGeneration"],
            "namespaceGeneration": actual_namespace_generation,
            "installGeneration": actual_install_generation,
            "target": target_kind,
            "operative": False,
        }
