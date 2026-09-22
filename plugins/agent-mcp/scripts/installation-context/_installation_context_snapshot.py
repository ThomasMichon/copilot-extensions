# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

def _snapshot_provenance_paths(
    validated: Mapping[str, Any],
    snapshot_id: str,
) -> tuple[Path, Path]:
    _assert_snapshot_id(snapshot_id)
    snapshots_root = canonical_path(_string_property(validated, "snapshotsRoot"))
    lexical_snapshot_root = snapshots_root / snapshot_id
    if _is_link_or_junction(lexical_snapshot_root):
        _fail("Snapshot root may not be a symbolic link or reparse point.")
    if not lexical_snapshot_root.is_dir():
        _fail("Snapshot root must be an existing materialized directory.")
    snapshot_root = canonical_path(lexical_snapshot_root, must_exist=True)
    if not paths_equal(snapshot_root.parent, snapshots_root):
        _fail("Snapshot root must be one direct child of snapshotsRoot.")
    if os.path.normcase(snapshot_root.name) != os.path.normcase(snapshot_id):
        _fail("Snapshot root does not retain the requested snapshot id.")
    if not any(path.name != SNAPSHOT_PROVENANCE_FILE for path in snapshot_root.iterdir()):
        _fail("Snapshot root must contain materialized payload content.")
    lexical_provenance_path = snapshot_root / SNAPSHOT_PROVENANCE_FILE
    if _is_link_or_junction(lexical_provenance_path):
        _fail("Snapshot provenance may not be a symbolic link or reparse point.")
    provenance_path = canonical_path(lexical_provenance_path)
    if not path_is_within(provenance_path, snapshots_root):
        _fail("Snapshot provenance path escapes snapshotsRoot.")
    return snapshot_root, provenance_path


def _payload_identity_from_install(
    install_path: str | os.PathLike[str],
) -> dict[str, Any]:
    install = read_json(install_path)
    if not isinstance(install, Mapping):
        _fail("install.json must be a JSON object.")
    payload = _property(install, "payload")
    if not isinstance(payload, Mapping):
        _fail("install.json payload is missing.")
    root = _string_property(payload, "root")
    if not _path_is_fully_qualified(root):
        _fail("payload.root must be absolute.")
    version = _string_property(payload, "version")
    if not version.strip():
        _fail("payload.version must be a non-empty string.")
    origin = _string_property(payload, "origin")
    if origin not in {"installed", "directory", "staged", "explicit"}:
        _fail("payload.origin must be installed, directory, staged, or explicit.")
    origin_receipt = _property(payload, "originReceipt")
    if origin_receipt is not None:
        if not isinstance(origin_receipt, str):
            _fail("payload.originReceipt must be a string.")
        if not _path_is_fully_qualified(origin_receipt):
            _fail("payload.originReceipt must be absolute.")
        origin_receipt = str(canonical_path(origin_receipt))
    return {
        "root": str(canonical_path(root)),
        "version": version,
        "origin": origin,
        "originReceipt": origin_receipt,
    }


def _validate_snapshot_provenance(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    snapshot_id: str,
    require_current_receipts: bool,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_payload_version: str | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate cell-local snapshot provenance against current canonical receipts."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_snapshot_id(snapshot_id)
    if expected_payload_root is not None and not _path_is_fully_qualified(
        expected_payload_root
    ):
        _fail("Expected snapshot payload root must be absolute.")
    if expected_payload_version is not None and (
        not isinstance(expected_payload_version, str)
        or not expected_payload_version.strip()
    ):
        _fail("Expected snapshot payload version must be a non-empty string.")
    caller_environment = environment if environment is not None else os.environ
    if durable_home is not None and not _path_is_fully_qualified(durable_home):
        _fail("--durable-home must be absolute.")
    durable = canonical_path(
        durable_home
        or Path(caller_environment.get("HOME") or Path.home())
        / ".copilot-extensions"
    )
    context_path = Path(context)
    if not _path_is_fully_qualified(context_path):
        _fail("Snapshot context must be absolute.")
    validated = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    snapshot_root, provenance_path = _snapshot_provenance_paths(
        validated,
        snapshot_id,
    )
    actual_provenance = canonical_path(provenance_path, must_exist=True)
    if not paths_equal(actual_provenance, provenance_path):
        _fail(
            "Snapshot provenance is not at its exact canonical location "
            f"'{provenance_path}'."
        )
    provenance = read_json(actual_provenance)
    if not isinstance(provenance, Mapping):
        _fail("Snapshot provenance must be a JSON object.")
    provenance_version = _property(provenance, "version")
    if (
        _string_property(provenance, "schema") != SNAPSHOT_PROVENANCE_SCHEMA
        or isinstance(provenance_version, bool)
        or not isinstance(provenance_version, int)
        or provenance_version != 1
    ):
        _fail("Snapshot provenance has an unsupported schema or version.")
    marketplace_id = _string_property(provenance, "marketplaceId")
    plugin_id = _string_property(provenance, "pluginId")
    if marketplace_id != expected_marketplace_id:
        _fail(
            f"Expected marketplace '{expected_marketplace_id}', snapshot provenance "
            f"names '{marketplace_id}'."
        )
    if plugin_id != expected_plugin_id:
        _fail(
            f"Expected plugin '{expected_plugin_id}', snapshot provenance names "
            f"'{plugin_id}'."
        )

    source = _property(provenance, "source")
    if not isinstance(source, Mapping):
        _fail("Snapshot provenance source is missing.")
    normalized = normalize_source(
        {
            "kind": _string_property(source, "kind"),
            "canonical": _string_property(source, "canonical"),
            "ref": _string_property(source, "ref"),
        },
        from_receipt=True,
    )
    identity = source_identity(normalized, marketplace_id.rsplit("--", 1)[0])
    fingerprint = _string_property(source, "fingerprint")
    if identity["marketplaceId"] != marketplace_id:
        _fail("Snapshot provenance marketplaceId does not match its normalized source.")
    if identity["fingerprint"] != fingerprint:
        _fail("Snapshot provenance fingerprint does not match its normalized source.")
    if (
        fingerprint != _string_property(validated, "sourceFingerprint")
        or normalized.kind != _string_property(validated["source"], "kind")
        or normalized.canonical != _string_property(validated["source"], "canonical")
        or normalized.ref != _string_property(validated["source"], "ref")
    ):
        _fail("Snapshot provenance source does not match the canonical namespace receipt.")

    snapshot = _property(provenance, "snapshot")
    if not isinstance(snapshot, Mapping):
        _fail("Snapshot provenance snapshot identity is missing.")
    if _string_property(snapshot, "id") != snapshot_id:
        _fail("Snapshot provenance id does not match its canonical snapshot directory.")
    recorded_snapshot_root = _string_property(snapshot, "root")
    if not _path_is_fully_qualified(recorded_snapshot_root):
        _fail("Snapshot provenance snapshot.root must be absolute.")
    if not paths_equal(recorded_snapshot_root, snapshot_root):
        _fail("Snapshot provenance snapshot.root is not its exact canonical location.")

    namespace_reference = _property(provenance, "namespaceReceipt")
    install_reference = _property(provenance, "installReceipt")
    if not isinstance(namespace_reference, Mapping) or not isinstance(
        install_reference,
        Mapping,
    ):
        _fail("Snapshot provenance receipt references are missing.")
    namespace_path = _string_property(namespace_reference, "path")
    install_path = _string_property(install_reference, "path")
    if not _path_is_fully_qualified(namespace_path) or not _path_is_fully_qualified(
        install_path
    ):
        _fail("Snapshot provenance receipt paths must be absolute.")
    if not paths_equal(namespace_path, _string_property(validated, "namespaceReceipt")):
        _fail("Snapshot provenance namespace receipt does not match the current context.")
    if not paths_equal(install_path, _string_property(validated, "installReceipt")):
        _fail("Snapshot provenance install receipt does not match the current context.")
    namespace_generation = _property(namespace_reference, "generation")
    install_generation = _property(install_reference, "generation")
    _assert_receipt_generation(
        namespace_generation,
        "snapshot provenance namespace generation",
    )
    _assert_receipt_generation(
        install_generation,
        "snapshot provenance install generation",
    )
    current_namespace_generation = _property(validated, "namespaceGeneration")
    current_install_generation = _property(validated, "generation")
    if require_current_receipts:
        if namespace_generation != current_namespace_generation:
            _fail(
                "Snapshot provenance namespace generation is stale; "
                "restart snapshot production."
            )
        if install_generation != current_install_generation:
            _fail(
                "Snapshot provenance install generation is stale; "
                "restart snapshot production."
            )
    elif (
        current_namespace_generation < namespace_generation
        or current_install_generation < install_generation
    ):
        _fail("Current receipt generation predates the owned runtime slot.")

    namespace = read_json(namespace_path)
    if not isinstance(namespace, Mapping):
        _fail("namespace.json must be a JSON object.")
    if require_current_receipts and (
        _string_property(namespace, "state") != "active"
        or _string_property(validated, "state") != "active"
    ):
        _fail("Snapshot provenance requires active namespace and install receipts.")

    payload = _property(provenance, "payload")
    if not isinstance(payload, Mapping):
        _fail("Snapshot provenance payload identity is missing.")
    if "originReceipt" not in payload:
        _fail("Snapshot provenance payload.originReceipt must be present.")
    recorded_payload = {
        "root": _string_property(payload, "root"),
        "version": _string_property(payload, "version"),
        "origin": _string_property(payload, "origin"),
        "originReceipt": _property(payload, "originReceipt"),
    }
    if not _path_is_fully_qualified(recorded_payload["root"]):
        _fail("Snapshot provenance payload.root must be absolute.")
    recorded_payload["root"] = str(canonical_path(recorded_payload["root"]))
    origin_receipt = recorded_payload["originReceipt"]
    if origin_receipt is not None:
        if not isinstance(origin_receipt, str):
            _fail("Snapshot provenance payload.originReceipt must be a string or null.")
        if not _path_is_fully_qualified(origin_receipt):
            _fail("Snapshot provenance payload.originReceipt must be absolute.")
        recorded_payload["originReceipt"] = str(canonical_path(origin_receipt))
    if expected_payload_root is not None and not paths_equal(
        recorded_payload["root"],
        expected_payload_root,
    ):
        _fail(
            f"Expected snapshot payload root '{expected_payload_root}', provenance "
            f"names '{recorded_payload['root']}'."
        )
    if (
        expected_payload_version is not None
        and recorded_payload["version"] != expected_payload_version
    ):
        _fail(
            f"Expected snapshot payload version '{expected_payload_version}', "
            f"provenance names '{recorded_payload['version']}'."
        )
    current_payload = _payload_identity_from_install(validated["installReceipt"])
    if require_current_receipts and recorded_payload != current_payload:
        _fail("Snapshot provenance payload does not match the pinned install receipt.")
    _parse_rfc3339_utc(
        _string_property(provenance, "createdAt"),
        "snapshot provenance createdAt",
    )
    return {
        "action": "snapshot-validate",
        "status": "ready",
        "reason": "snapshot-provenance-valid",
        "provenance": str(actual_provenance),
        "snapshotRoot": str(snapshot_root),
        "snapshotId": snapshot_id,
        "marketplaceId": marketplace_id,
        "pluginId": plugin_id,
        "sourceFingerprint": fingerprint,
        "namespaceReceipt": str(canonical_path(namespace_path)),
        "installReceipt": str(canonical_path(install_path)),
        "namespaceGeneration": namespace_generation,
        "installGeneration": install_generation,
        "payload": current_payload if require_current_receipts else recorded_payload,
        "operative": False,
    }


@_validation_scope
def validate_snapshot_provenance(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    snapshot_id: str,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_payload_version: str | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate snapshot provenance against the current canonical receipts."""

    return _validate_snapshot_provenance(
        context=context,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        snapshot_id=snapshot_id,
        expected_payload_root=expected_payload_root,
        expected_payload_version=expected_payload_version,
        durable_home=durable_home,
        environment=environment,
        require_current_receipts=True,
    )


@_validation_scope
def stamp_snapshot_provenance(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    expected_namespace_generation: int,
    expected_install_generation: int,
    snapshot_id: str,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Atomically publish immutable snapshot provenance under both receipt locks."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_snapshot_id(snapshot_id)
    _assert_expected_generation(
        expected_namespace_generation,
        expected_namespace_generation,
        "namespace.json",
    )
    _assert_expected_generation(
        expected_install_generation,
        expected_install_generation,
        "install.json",
    )
    caller_environment = environment if environment is not None else os.environ
    if durable_home is not None and not _path_is_fully_qualified(durable_home):
        _fail("--durable-home must be absolute.")
    durable = canonical_path(
        durable_home
        or Path(caller_environment.get("HOME") or Path.home())
        / ".copilot-extensions"
    )
    context_path = Path(context)
    if not _path_is_fully_qualified(context_path):
        _fail("Snapshot context must be absolute.")
    validated = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    cell_root = canonical_path(_string_property(validated, "cellRoot"))
    plugin_root = canonical_path(_string_property(validated, "pluginRoot"))
    install_path = canonical_path(_string_property(validated, "installReceipt"))
    genesis_lock = _DirectoryLock(
        durable
        / "marketplaces"
        / ".locks"
        / f"{expected_marketplace_id}.genesis",
        kind="genesis",
        marketplace_id=expected_marketplace_id,
    )
    install_lock = _DirectoryLock(
        cell_root / ".locks" / f"{expected_plugin_id}.install.lock",
        kind="install",
        marketplace_id=expected_marketplace_id,
        plugin_id=expected_plugin_id,
    )
    with genesis_lock, install_lock:
        validated = validate_context_receipt(
            install_path,
            durable,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            expected_cell_root=cell_root,
            environment={},
        )
        _assert_expected_generation(
            int(validated["namespaceGeneration"]),
            expected_namespace_generation,
            "namespace.json",
        )
        _assert_expected_generation(
            int(validated["generation"]),
            expected_install_generation,
            "install.json",
        )
        namespace = read_json(validated["namespaceReceipt"])
        if not isinstance(namespace, Mapping):
            _fail("namespace.json must be a JSON object.")
        if (
            _string_property(namespace, "state") != "active"
            or _string_property(validated, "state") != "active"
        ):
            _fail("Snapshot provenance requires active namespace and install receipts.")
        snapshot_root, provenance_path = _snapshot_provenance_paths(
            validated,
            snapshot_id,
        )
        snapshot_changed = False
        if os.path.lexists(provenance_path):
            published = validate_snapshot_provenance(
                context=install_path,
                expected_marketplace_id=expected_marketplace_id,
                expected_plugin_id=expected_plugin_id,
                snapshot_id=snapshot_id,
                durable_home=durable,
                environment={},
            )
        else:
            source = validated["source"]
            payload = _payload_identity_from_install(install_path)
            desired = {
                "schema": SNAPSHOT_PROVENANCE_SCHEMA,
                "version": 1,
                "marketplaceId": expected_marketplace_id,
                "pluginId": expected_plugin_id,
                "source": {
                    "kind": _string_property(source, "kind"),
                    "canonical": _string_property(source, "canonical"),
                    "ref": _string_property(source, "ref"),
                    "fingerprint": _string_property(validated, "sourceFingerprint"),
                },
                "snapshot": {
                    "id": snapshot_id,
                    "root": str(snapshot_root),
                },
                "payload": payload,
                "namespaceReceipt": {
                    "path": _string_property(validated, "namespaceReceipt"),
                    "generation": int(validated["namespaceGeneration"]),
                },
                "installReceipt": {
                    "path": _string_property(validated, "installReceipt"),
                    "generation": int(validated["generation"]),
                },
                "createdAt": _utc_now(),
            }
            _atomic_write_json(
                provenance_path,
                desired,
                lock=(genesis_lock, install_lock),
            )
            snapshot_changed = True
            published = validate_snapshot_provenance(
                context=install_path,
                expected_marketplace_id=expected_marketplace_id,
                expected_plugin_id=expected_plugin_id,
                snapshot_id=snapshot_id,
                durable_home=durable,
                environment={},
            )
        published.update(
            {
                "action": "snapshot-stamp",
                "reason": (
                    "snapshot-provenance-published"
                    if snapshot_changed
                    else "snapshot-provenance-current"
                ),
                "snapshotChanged": snapshot_changed,
                "pluginRoot": str(plugin_root),
            }
        )
        return published
