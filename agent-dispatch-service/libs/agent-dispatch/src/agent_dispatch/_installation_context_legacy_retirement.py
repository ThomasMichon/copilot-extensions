# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

def _write_retirement_record_locked(
    *,
    record_path: Path,
    install_path: Path,
    plugin_root: Path,
    marketplace_id: str,
    plugin_id: str,
    current_environment: Mapping[str, Any],
    retirement_id: str,
    target_activation: Mapping[str, Any],
    target_tombstone: Mapping[str, Any],
    target_health: Mapping[str, Any],
    target_items: Sequence[Mapping[str, Any]],
    result_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    desired: dict[str, Any] = {
        "schema": RETIREMENT_SCHEMA,
        "version": 1,
        "marketplaceId": marketplace_id,
        "pluginId": plugin_id,
        "context": str(install_path),
        "environment": current_environment,
        "target": {
            "id": retirement_id,
            "activation": target_activation,
            "tombstone": target_tombstone,
            "health": target_health,
            "items": [
                {
                    "kind": item["kind"],
                    "identity": item["identity"],
                    "path": str(item["path"]),
                }
                for item in target_items
            ],
        },
        "result": {
            "items": [
                {
                    "kind": item["kind"],
                    "identity": item["identity"],
                    "path": str(item["path"]),
                    "disposition": item["disposition"],
                }
                for item in result_items
            ],
        },
        "createdAt": _utc_now(),
    }
    if record_path.exists():
        _fail("Legacy retirement record already exists for the requested target.")
    _atomic_write_json(record_path, desired)
    published = _load_retirement_record(
        record_path=record_path,
        install_path=install_path,
        plugin_root=plugin_root,
        marketplace_id=marketplace_id,
        plugin_id=plugin_id,
        current_environment=current_environment,
    )
    if published is None:
        _fail("Published legacy retirement record did not validate.")
    return {"path": published["path"], "changed": True}


@_validation_scope
def retire_legacy_compatibility(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    expected_namespace_generation: int,
    expected_install_generation: int,
    expected_activation_generation: int,
    legacy_root: str | os.PathLike[str],
    retirement_id: str,
    legacy_items: Sequence[Mapping[str, Any]],
    health_report: Mapping[str, Any],
    legacy_lock: AbstractContextManager[Any] | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    maintenance_token: str | None = None,
    environment: Mapping[str, str] | None = None,
    os_profile: str | os.PathLike[str] | None = None,
    platform: str | None = None,
    wsl_distro: str | None = None,
) -> dict[str, Any]:
    """Retire explicit legacy compatibility artifacts only after health passes."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    retirement_id = _validate_retirement_id(retirement_id)
    for name, value in (
        ("namespace", expected_namespace_generation),
        ("install", expected_install_generation),
        ("activation", expected_activation_generation),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail(f"Expected {name} generation must be a non-negative integer.")
    items = _validate_legacy_attribution_items(legacy_items)
    if not items:
        _fail("Legacy retirement requires at least one explicit target item.")
    recorded_health = _validate_retirement_health_report(
        health_report,
        label="legacy retirement health",
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
        _fail("Legacy retirement context must be absolute.")
    legacy_root_path = Path(legacy_root)
    if not _path_is_fully_qualified(legacy_root_path):
        _fail("Legacy retirement legacy root must be absolute.")
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
    record_path = _retirement_record_path(
        plugin_root,
        expected_activation_generation,
        retirement_id,
    )

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
        requested_keys = {
            (item["identity"], str(item["path"])) for item in items
        }
        existing_record = _load_retirement_record(
            record_path=record_path,
            install_path=install_path,
            plugin_root=plugin_root,
            marketplace_id=marketplace_id,
            plugin_id=plugin_id,
            current_environment=current_environment,
        )
        if existing_record is not None:
            recorded_keys = {
                (item["identity"], str(item["path"]))
                for item in existing_record["targetItems"]
            }
            if (
                existing_record["retirementId"] != retirement_id
                or existing_record["targetActivationGeneration"]
                != expected_activation_generation
                or existing_record["targetNamespaceGeneration"]
                != expected_namespace_generation
                or existing_record["targetInstallGeneration"]
                != expected_install_generation
                or recorded_keys != requested_keys
            ):
                _fail("Legacy retirement record already exists with different content.")
            if any(os.path.lexists(Path(item["path"])) for item in items):
                return {
                    "action": "retire-legacy-compatibility",
                    "status": "preserved",
                    "reason": "retired-artifact-reappeared",
                    "legacyRoot": str(legacy_root_path),
                    "context": str(install_path),
                    "record": existing_record["path"],
                    "recordChanged": False,
                    "activation": (
                        str(canonical_path(activation_path))
                        if os.path.lexists(activation_path)
                        else None
                    ),
                    "activationGeneration": existing_record["targetActivationGeneration"],
                    "namespaceGeneration": actual_namespace_generation,
                    "installGeneration": actual_install_generation,
                    "health": existing_record["targetHealth"],
                    "retiredItems": [
                        {
                            "kind": item["kind"],
                            "identity": item["identity"],
                            "path": str(item["path"]),
                            "disposition": item["disposition"],
                        }
                        for item in existing_record["retiredItems"]
                    ],
                    "operative": False,
                }
            return {
                "action": "retire-legacy-compatibility",
                "status": "ready",
                "reason": "already-retired",
                "legacyRoot": str(legacy_root_path),
                "context": str(install_path),
                "record": existing_record["path"],
                "recordChanged": False,
                "activation": (
                    str(canonical_path(activation_path))
                    if os.path.lexists(activation_path)
                    else None
                ),
                "activationGeneration": existing_record["targetActivationGeneration"],
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "health": existing_record["targetHealth"],
                "retiredItems": [
                    {
                        "kind": item["kind"],
                        "identity": item["identity"],
                        "path": str(item["path"]),
                        "disposition": item["disposition"],
                    }
                    for item in existing_record["retiredItems"]
                ],
                "operative": False,
            }

        if (
            actual_namespace_generation != expected_namespace_generation
            or actual_install_generation != expected_install_generation
        ):
            return {
                "action": "retire-legacy-compatibility",
                "status": "revalidation-required",
                "reason": "generation-changed",
                "legacyRoot": str(legacy_root_path),
                "context": str(install_path),
                "record": None,
                "recordChanged": False,
                "activation": (
                    str(canonical_path(activation_path))
                    if os.path.lexists(activation_path)
                    else None
                ),
                "activationGeneration": None,
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "health": recorded_health,
                "retiredItems": [],
                "operative": False,
            }

        activation = _activation_result(
            plugin_root=plugin_root,
            durable_home=durable,
            marketplace_id=marketplace_id,
            plugin_id=plugin_id,
            current_environment=current_environment,
            legacy_root=resolved_legacy_root,
        )
        if (
            activation["state"] != "valid"
            or activation["actualMode"] != "namespaced"
            or activation["activationGeneration"] != expected_activation_generation
        ):
            return {
                "action": "retire-legacy-compatibility",
                "status": "preserved",
                "reason": "activation-not-retireable",
                "legacyRoot": str(legacy_root_path),
                "context": str(install_path),
                "record": None,
                "recordChanged": False,
                "activation": activation["path"],
                "activationGeneration": activation["activationGeneration"],
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "health": recorded_health,
                "retiredItems": [],
                "operative": False,
            }

        legacy = _tombstone_result(
            legacy_root=resolved_legacy_root,
            durable_home=durable,
            plugin_id=plugin_id,
            current_marketplace_id=marketplace_id,
            current_environment=current_environment,
        )
        if legacy["disposition"] != "owned-by-current-cell":
            return {
                "action": "retire-legacy-compatibility",
                "status": "preserved",
                "reason": legacy["reason"] or "ownership-unproven",
                "legacyRoot": str(legacy_root_path),
                "context": str(install_path),
                "record": None,
                "recordChanged": False,
                "activation": activation["path"],
                "activationGeneration": activation["activationGeneration"],
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "health": recorded_health,
                "retiredItems": [],
                "operative": False,
            }
        try:
            tombstone = _load_owned_tombstone(
                tombstone_path=tombstone_path,
                install_path=install_path,
                plugin_root=plugin_root,
                marketplace_id=marketplace_id,
                plugin_id=plugin_id,
                current_environment=current_environment,
                expected_activation_generation=expected_activation_generation,
            )
        except InstallationContextError:
            tombstone = None
        if tombstone is None:
            return {
                "action": "retire-legacy-compatibility",
                "status": "preserved",
                "reason": "ownership-unproven",
                "legacyRoot": str(legacy_root_path),
                "context": str(install_path),
                "record": None,
                "recordChanged": False,
                "activation": activation["path"],
                "activationGeneration": activation["activationGeneration"],
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "health": recorded_health,
                "retiredItems": [],
                "operative": False,
            }
        attribution_items = _property(tombstone["attribution"], "items")
        recorded_item_keys: set[tuple[str, str]] = set()
        if isinstance(attribution_items, Sequence) and not isinstance(
            attribution_items, (str, bytes, bytearray)
        ):
            for recorded in _validate_legacy_attribution_items(attribution_items):
                recorded_item_keys.add((recorded["identity"], str(recorded["path"])))
        mismatched_items = [
            {
                "kind": item["kind"],
                "identity": item["identity"],
                "path": str(item["path"]),
                "disposition": "preserved",
            }
            for item in items
            if os.path.lexists(Path(item["path"]))
            and (item["identity"], str(item["path"])) not in recorded_item_keys
        ]
        if mismatched_items:
            return {
                "action": "retire-legacy-compatibility",
                "status": "preserved",
                "reason": "attribution-mismatch",
                "legacyRoot": str(legacy_root_path),
                "context": str(install_path),
                "record": None,
                "recordChanged": False,
                "activation": activation["path"],
                "activationGeneration": activation["activationGeneration"],
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "health": recorded_health,
                "retiredItems": mismatched_items,
                "operative": False,
            }
        if recorded_health["status"] != "ready":
            return {
                "action": "retire-legacy-compatibility",
                "status": "preserved",
                "reason": recorded_health["reason"],
                "legacyRoot": str(legacy_root_path),
                "context": str(install_path),
                "record": None,
                "recordChanged": False,
                "activation": activation["path"],
                "activationGeneration": activation["activationGeneration"],
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "health": recorded_health,
                "retiredItems": [],
                "operative": False,
            }

        evidence_by_path: dict[Path, tuple[Any, ...]] = {}
        retired_items: list[dict[str, Any]] = []
        for item in items:
            path = Path(item["path"])
            if not os.path.lexists(path):
                retired_items.append(
                    {
                        "kind": item["kind"],
                        "identity": item["identity"],
                        "path": str(path),
                        "disposition": "already-absent",
                    }
                )
                continue
            evidence_by_path[path] = _path_evidence(path)
        for item in items:
            path = Path(item["path"])
            evidence = evidence_by_path.get(path)
            if evidence is None:
                continue
            for lock in (genesis_lock, install_lock):
                lock.assert_owned()
            if _path_evidence(path) != evidence:
                _fail("Legacy retirement evidence changed or was replaced.")
            if path.is_dir():
                path.rmdir()
            else:
                _invalidate_validated_file_digest(path)
                path.unlink()
            if os.path.lexists(path):
                _fail("Legacy retirement item remained present after removal.")
            retired_items.append(
                {
                    "kind": item["kind"],
                    "identity": item["identity"],
                    "path": str(path),
                    "disposition": "removed",
                }
            )

        record = _write_retirement_record_locked(
            record_path=record_path,
            install_path=install_path,
            plugin_root=plugin_root,
            marketplace_id=marketplace_id,
            plugin_id=plugin_id,
            current_environment=current_environment,
            retirement_id=retirement_id,
            target_activation={
                "path": activation["path"],
                "generation": expected_activation_generation,
                "mode": "namespaced",
                "state": "active",
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "legacyDisposition": "retained-inert",
            },
            target_tombstone={
                "path": tombstone["path"],
                "activationGeneration": tombstone["activationGeneration"],
                "transferredAt": tombstone["transferredAt"],
                "attribution": tombstone["attribution"],
            },
            target_health=recorded_health,
            target_items=items,
            result_items=retired_items,
        )
        return {
            "action": "retire-legacy-compatibility",
            "status": "ready",
            "reason": (
                "legacy-compatibility-retired"
                if any(item["disposition"] == "removed" for item in retired_items)
                else "legacy-compatibility-recorded"
            ),
            "legacyRoot": str(legacy_root_path),
            "context": str(install_path),
            "record": record["path"],
            "recordChanged": record["changed"],
            "activation": activation["path"],
            "activationGeneration": expected_activation_generation,
            "namespaceGeneration": actual_namespace_generation,
            "installGeneration": actual_install_generation,
            "health": recorded_health,
            "retiredItems": retired_items,
            "operative": False,
        }
