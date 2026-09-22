# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
def _runtime_slot_paths(
    validated: Mapping[str, Any],
    runtime_version: str,
    *,
    require_existing: bool,
) -> tuple[Path, Path, Path]:
    _assert_runtime_version(runtime_version)
    plugin_root = canonical_path(_string_property(validated, "pluginRoot"))
    install = read_json(_string_property(validated, "installReceipt"))
    roots = _property(install, "roots") if isinstance(install, Mapping) else None
    if not isinstance(roots, Mapping):
        _fail("install.json roots is missing.")
    versions_relative = _string_property(roots, "versions")
    lexical_versions_root = plugin_root / versions_relative
    cursor = plugin_root
    for part in Path(versions_relative).parts:
        cursor /= part
        if _is_link_or_junction(cursor):
            _fail("Versions root may not traverse a symbolic link or reparse point.")
        if cursor.exists() and not cursor.is_dir():
            _fail("Versions root path components must be ordinary directories.")
    if require_existing and not lexical_versions_root.is_dir():
        _fail("Versions root must be an existing directory.")
    resolved_versions_root = canonical_path(lexical_versions_root)
    if not paths_equal(
        resolved_versions_root,
        _string_property(validated, "versionsRoot"),
    ):
        _fail("Versions root does not match the validated install receipt.")
    if paths_equal(resolved_versions_root, plugin_root) or not path_is_within(
        resolved_versions_root,
        plugin_root,
    ):
        _fail("Versions root must remain beneath the canonical plugin root.")
    lexical_slot_root = resolved_versions_root / runtime_version
    if _is_link_or_junction(lexical_slot_root):
        _fail("Runtime slot may not be a symbolic link or reparse point.")
    if require_existing and not lexical_slot_root.is_dir():
        _fail("Runtime slot must be an existing directory.")
    slot_root = canonical_path(lexical_slot_root)
    if not paths_equal(slot_root.parent, resolved_versions_root):
        _fail("Runtime slot must be one direct child of versionsRoot.")
    if os.path.normcase(slot_root.name) != os.path.normcase(runtime_version):
        _fail("Runtime slot does not retain the requested runtime version.")
    ownership_path = slot_root / RUNTIME_SLOT_OWNERSHIP_FILE
    if _is_link_or_junction(ownership_path):
        _fail("Runtime slot ownership may not be a symbolic link or reparse point.")
    return resolved_versions_root, slot_root, ownership_path


def _runtime_slot_ownership_value(
    validated: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    runtime_version: str,
    slot_root: Path,
    *,
    created_at: str,
) -> dict[str, Any]:
    return {
        "schema": RUNTIME_SLOT_OWNERSHIP_SCHEMA,
        "version": 1,
        "marketplaceId": _string_property(validated, "marketplaceId"),
        "pluginId": _string_property(validated, "pluginId"),
        "sourceFingerprint": _string_property(validated, "sourceFingerprint"),
        "runtime": {
            "version": runtime_version,
            "root": str(slot_root),
        },
        "snapshot": {
            "id": _string_property(snapshot, "snapshotId"),
            "root": _string_property(snapshot, "snapshotRoot"),
            "provenance": _string_property(snapshot, "provenance"),
            "provenanceSha256": _sha256_file(
                Path(_string_property(snapshot, "provenance"))
            ),
        },
        "namespaceReceipt": {
            "path": _string_property(snapshot, "namespaceReceipt"),
            "generation": _property(snapshot, "namespaceGeneration"),
        },
        "installReceipt": {
            "path": _string_property(snapshot, "installReceipt"),
            "generation": _property(snapshot, "installGeneration"),
        },
        "createdAt": created_at,
    }


def _validated_runtime_slot_ownership(
    validated: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    runtime_version: str,
    *,
    reservation_root: Path | None = None,
    reservation_generation: int | None = None,
) -> dict[str, Any]:
    _, slot_root, ownership_path = _runtime_slot_paths(
        validated,
        runtime_version,
        require_existing=reservation_root is None,
    )
    if reservation_root is not None:
        ownership_path = reservation_root / RUNTIME_SLOT_RESERVATION_FILE
    elif os.path.lexists(slot_root / RUNTIME_SLOT_RESERVATION_FILE):
        _fail("Runtime slot retains unfinished reservation evidence.")
    if not ownership_path.exists():
        _fail("Runtime slot ownership must exist.")
    actual_ownership = canonical_path(ownership_path, must_exist=True)
    if not paths_equal(actual_ownership, ownership_path):
        _fail(
            "Runtime slot ownership is not at its exact canonical location "
            f"'{ownership_path}'."
        )
    if not actual_ownership.is_file():
        _fail("Runtime slot ownership must be an ordinary file.")
    ownership = read_json(actual_ownership)
    if not isinstance(ownership, Mapping):
        _fail("Runtime slot ownership must be a JSON object.")
    ownership_version = _property(ownership, "version")
    if (
        _string_property(ownership, "schema") != (
            RUNTIME_SLOT_RESERVATION_SCHEMA
            if reservation_root is not None else RUNTIME_SLOT_OWNERSHIP_SCHEMA
        )
        or isinstance(ownership_version, bool)
        or not isinstance(ownership_version, int)
        or ownership_version != 1
    ):
        _fail("Runtime slot ownership has an unsupported schema or version.")
    created_at = _string_property(ownership, "createdAt")
    _parse_rfc3339_utc(created_at, "runtime slot ownership createdAt")
    expected = _runtime_slot_ownership_value(
        validated,
        snapshot,
        runtime_version,
        slot_root,
        created_at=created_at,
    )
    if reservation_root is not None:
        _assert_nonnegative_generation(ownership.get("generation"), "reservation generation")
        expected["schema"] = RUNTIME_SLOT_RESERVATION_SCHEMA
        expected["generation"] = reservation_generation
    runtime = _property(ownership, "runtime")
    recorded_snapshot = _property(ownership, "snapshot")
    namespace_reference = _property(ownership, "namespaceReceipt")
    install_reference = _property(ownership, "installReceipt")
    if not all(
        isinstance(value, Mapping)
        for value in (
            runtime,
            recorded_snapshot,
            namespace_reference,
            install_reference,
        )
    ):
        _fail("Runtime slot ownership identity objects are missing.")
    path_fields = (
        (runtime, expected["runtime"], "root", "runtime.root"),
        (
            recorded_snapshot,
            expected["snapshot"],
            "root",
            "snapshot.root",
        ),
        (
            recorded_snapshot,
            expected["snapshot"],
            "provenance",
            "snapshot.provenance",
        ),
        (
            namespace_reference,
            expected["namespaceReceipt"],
            "path",
            "namespaceReceipt.path",
        ),
        (
            install_reference,
            expected["installReceipt"],
            "path",
            "installReceipt.path",
        ),
    )
    for recorded, expected_container, key, label in path_fields:
        recorded_path = _string_property(recorded, key)
        if not _path_is_fully_qualified(recorded_path):
            _fail(f"Runtime slot ownership {label} must be absolute.")
        if not paths_equal(recorded_path, _string_property(expected_container, key)):
            _fail(
                "Runtime slot ownership does not match the validated snapshot "
                "and installation receipts."
            )
        expected_container[key] = recorded_path
    _assert_receipt_generation(
        _property(namespace_reference, "generation"),
        "runtime slot ownership namespace generation",
    )
    _assert_receipt_generation(
        _property(install_reference, "generation"),
        "runtime slot ownership install generation",
    )
    if dict(ownership) != expected:
        _fail(
            "Runtime slot ownership does not match the validated snapshot "
            "and installation receipts."
        )
    if reservation_root is not None:
        return expected
    namespace = read_json(_string_property(validated, "namespaceReceipt"))
    if not isinstance(namespace, Mapping):
        _fail("namespace.json must be a JSON object.")
    return {
        "action": "slot-validate",
        "status": "ready",
        "reason": "runtime-slot-ownership-valid",
        "slotRoot": str(slot_root),
        "runtimeVersion": runtime_version,
        "ownership": str(actual_ownership),
        "snapshotId": _string_property(snapshot, "snapshotId"),
        "snapshotProvenance": _string_property(snapshot, "provenance"),
        "marketplaceId": _string_property(validated, "marketplaceId"),
        "pluginId": _string_property(validated, "pluginId"),
        "sourceFingerprint": _string_property(validated, "sourceFingerprint"),
        "namespaceReceipt": _string_property(snapshot, "namespaceReceipt"),
        "installReceipt": _string_property(snapshot, "installReceipt"),
        "namespaceGeneration": _property(snapshot, "namespaceGeneration"),
        "installGeneration": _property(snapshot, "installGeneration"),
        "namespaceState": _string_property(namespace, "state"),
        "installState": _string_property(validated, "state"),
        "slotEmpty": not any(
            child.name != RUNTIME_SLOT_OWNERSHIP_FILE
            for child in slot_root.iterdir()
        ),
        "activated": False,
        "operative": False,
    }


@_validation_scope
def validate_runtime_slot_ownership(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    snapshot_id: str,
    runtime_version: str,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_payload_version: str | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate one cell-local runtime slot against snapshot and receipt identity."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_snapshot_id(snapshot_id)
    _assert_runtime_version(runtime_version)
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
        _fail("Runtime slot context must be absolute.")
    validated = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    snapshot = _validate_snapshot_provenance(
        context=context_path,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        snapshot_id=snapshot_id,
        expected_payload_root=expected_payload_root,
        expected_payload_version=expected_payload_version,
        durable_home=durable,
        environment={},
        require_current_receipts=False,
    )
    return _validated_runtime_slot_ownership(
        validated,
        snapshot,
        runtime_version,
    )


@_validation_scope
def provision_runtime_slot(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    snapshot_id: str,
    runtime_version: str,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_payload_version: str | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Create or validate an owned runtime slot without activating it."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_snapshot_id(snapshot_id)
    _assert_runtime_version(runtime_version)
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
        _fail("Runtime slot context must be absolute.")
    validated = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    cell_root = canonical_path(_string_property(validated, "cellRoot"))
    genesis_lock = _DirectoryLock(
        durable
        / "marketplaces"
        / ".locks"
        / f"{expected_marketplace_id}.genesis",
        kind="genesis",
        marketplace_id=expected_marketplace_id,
        timeout_seconds=RUNTIME_SLOT_LOCK_TIMEOUT_SECONDS,
    )
    install_lock = _DirectoryLock(
        cell_root / ".locks" / f"{expected_plugin_id}.install.lock",
        kind="install",
        marketplace_id=expected_marketplace_id,
        plugin_id=expected_plugin_id,
        timeout_seconds=RUNTIME_SLOT_LOCK_TIMEOUT_SECONDS,
    )
    with genesis_lock, install_lock:
        validated = validate_context_receipt(
            context_path,
            durable,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            environment={},
        )
        versions_root, slot_root, _ = _runtime_slot_paths(
            validated,
            runtime_version,
            require_existing=False,
        )
        if slot_root.exists():
            snapshot = _validate_snapshot_provenance(
                context=context_path,
                expected_marketplace_id=expected_marketplace_id,
                expected_plugin_id=expected_plugin_id,
                snapshot_id=snapshot_id,
                expected_payload_root=expected_payload_root,
                expected_payload_version=expected_payload_version,
                durable_home=durable,
                environment={},
                require_current_receipts=False,
            )
            result = _validated_runtime_slot_ownership(
                validated,
                snapshot,
                runtime_version,
            )
            result["action"] = "slot-provision"
            result["reason"] = "runtime-slot-ownership-current"
            result["slotChanged"] = False
            return result
        snapshot = _validate_snapshot_provenance(
            context=context_path,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            snapshot_id=snapshot_id,
            expected_payload_root=expected_payload_root,
            expected_payload_version=expected_payload_version,
            durable_home=durable,
            environment={},
            require_current_receipts=True,
        )
        try:
            versions_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            _fail(f"Cannot create versions root '{versions_root}': {error}")
        verified_versions_root, slot_root, _ = _runtime_slot_paths(
            validated,
            runtime_version,
            require_existing=False,
        )
        if not paths_equal(verified_versions_root, versions_root):
            _fail("Versions root changed during runtime slot provisioning.")
        if not versions_root.is_dir():
            _fail("Versions root must be an ordinary directory.")
        slot_digest = hashlib.sha256(os.fsencode(slot_root)).hexdigest()[:16]
        temporary_slot = versions_root.parent / (
            f".runtime-slot-{slot_digest}-{secrets.token_hex(8)}"
        )
        temporary_ownership = temporary_slot / RUNTIME_SLOT_OWNERSHIP_FILE
        temporary_reservation = temporary_slot / RUNTIME_SLOT_RESERVATION_FILE
        try:
            temporary_slot.mkdir(mode=0o700)
            ownership = _runtime_slot_ownership_value(
                validated,
                snapshot,
                runtime_version,
                slot_root,
                created_at=_utc_now(),
            )
            genesis_lock.assert_owned()
            install_lock.assert_owned()
            reservation = {
                **ownership,
                "schema": RUNTIME_SLOT_RESERVATION_SCHEMA,
                "generation": secrets.randbits(63),
            }
            _write_private_json(temporary_reservation, reservation)
            _write_private_json(temporary_ownership, ownership)
            genesis_lock.assert_owned()
            install_lock.assert_owned()
            _rename_directory_no_replace(temporary_slot, slot_root)
            genesis_lock.assert_owned()
            install_lock.assert_owned()
            (slot_root / RUNTIME_SLOT_RESERVATION_FILE).unlink()
            if os.name != "nt":
                for parent in (versions_root.parent, versions_root):
                    directory = os.open(parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
        except BaseException:
            try:
                temporary_ownership.unlink()
            except OSError:
                pass
            try:
                temporary_reservation.unlink()
            except OSError:
                pass
            try:
                temporary_slot.rmdir()
            except OSError:
                pass
            raise
        result = _validated_runtime_slot_ownership(
            validated,
            snapshot,
            runtime_version,
        )
        result["action"] = "slot-provision"
        result["reason"] = "runtime-slot-ownership-published"
        result["slotChanged"] = True
        return result


def _path_evidence(path: Path) -> tuple[Any, ...] | None:
    """Capture no-follow identity and bytes for a lifecycle CAS."""
    if not os.path.lexists(path):
        return None
    info = path.lstat()
    if _is_link_or_junction(path) or not (
        stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
    ):
        _fail(f"Lifecycle target must be an ordinary file or directory: '{path}'.")
    digest = None
    if stat.S_ISREG(info.st_mode):
        hasher = hashlib.sha256()
        _, info = _read_regular_file(
            path, label="Lifecycle evidence", require_stable_identity=True,
            consume_chunk=hasher.update,
        )
        digest = hasher.hexdigest()
    return (_stat_identity(info), _stat_metadata(info), digest)


def _assert_nonnegative_generation(value: Any, label: str) -> None:
    if type(value) is not int or not 0 <= value <= MAX_RECEIPT_GENERATION:
        _fail(f"{label} must be an integer from 0 through {MAX_RECEIPT_GENERATION}.")


def _reservation_target(
    validated: Mapping[str, Any], runtime_version: str, reservation_root: str | os.PathLike[str]
) -> tuple[Path, Path]:
    versions, slot, _ = _runtime_slot_paths(validated, runtime_version, require_existing=False)
    target = Path(reservation_root)
    if not _path_is_fully_qualified(target) or _is_link_or_junction(target):
        _fail("Reservation root must be absolute and may not be linked or reparsed.")
    digest = hashlib.sha256(str(slot).encode("utf-8")).hexdigest()[:16]
    hidden = (
        paths_equal(target.parent, versions.parent)
        and re.fullmatch(rf"\.runtime-slot-{digest}-(?:[0-9a-f]{{16}}|[0-9a-f]{{32}})", target.name)
    )
    if not paths_equal(target, slot) and not hidden:
        _fail("Reservation root must be the exact slot or its attributable hidden sibling.")
    if str(canonical_path(target)) != str(target):
        _fail("Reservation root must retain its exact canonical spelling.")
    return target, versions.parent


@_validation_scope
def release_runtime_slot(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    runtime_version: str,
    reservation_root: str | os.PathLike[str],
    expected_reservation_generation: int,
    expected_reservation_sha256: str,
    expected_namespace_generation: int,
    expected_install_generation: int,
    durable_home: str | os.PathLike[str],
) -> dict[str, Any]:
    """Release only an exact receipt-only reservation, never an owned runtime."""
    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_runtime_version(runtime_version)
    for name, generation in (
        ("reservation", expected_reservation_generation),
        ("namespace", expected_namespace_generation),
        ("install", expected_install_generation),
    ):
        _assert_nonnegative_generation(generation, f"expected {name} generation")
    if not LOWER_SHA256.fullmatch(expected_reservation_sha256):
        _fail("Expected reservation SHA-256 must be lowercase 64-hex.")
    if not _path_is_fully_qualified(context) or not _path_is_fully_qualified(durable_home):
        _fail("Release requires explicit absolute context and durable home.")
    durable = canonical_path(durable_home)

    def validate() -> dict[str, Any]:
        return validate_context_receipt(
            context, durable, expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id, environment={},
        )

    validated = validate()
    locks = (
        _DirectoryLock(
            durable / "marketplaces" / ".locks" / f"{expected_marketplace_id}.genesis",
            kind="genesis", marketplace_id=expected_marketplace_id,
            timeout_seconds=RUNTIME_SLOT_LOCK_TIMEOUT_SECONDS,
        ),
        _DirectoryLock(
            Path(validated["cellRoot"]) / ".locks" / f"{expected_plugin_id}.install.lock",
            kind="install", marketplace_id=expected_marketplace_id,
            plugin_id=expected_plugin_id, timeout_seconds=RUNTIME_SLOT_LOCK_TIMEOUT_SECONDS,
        ),
    )
    with locks[0], locks[1]:
        validated = validate()
        target, marker_root = _reservation_target(validated, runtime_version, reservation_root)
        result = {
            "action": "slot-release", "status": "ready", "reason": "runtime-slot-reservation-absent",
            "released": False, "slotRoot": str(target), "runtimeVersion": runtime_version,
            "reservationGeneration": expected_reservation_generation,
            "namespaceGeneration": validated["namespaceGeneration"],
            "installGeneration": validated["generation"], "activated": False, "operative": False,
        }
        if (validated["namespaceGeneration"], validated["generation"]) != (
            expected_namespace_generation, expected_install_generation
        ):
            return {**result, "status": "revalidation-required", "reason": "generation-changed"}
        markers = [marker_root / CURRENT_VERSION_FILE, marker_root / LAST_KNOWN_GOOD_FILE]
        selected = [_read_runtime_marker(path, path.name) for path in markers]
        if runtime_version in selected:
            _fail("A current or last-known-good reservation cannot be released.")
        if not os.path.lexists(target):
            return result
        receipt = target / RUNTIME_SLOT_RESERVATION_FILE
        if not target.is_dir() or {p.name for p in target.iterdir()} != {receipt.name}:
            _fail("Release requires exactly one matching reservation receipt and no other entries.")
        evidence_paths = [
            Path(validated["namespaceReceipt"]), Path(validated["installReceipt"]),
            *markers, target, receipt,
        ]
        evidence = [_path_evidence(path) for path in evidence_paths]
        record, digest = _read_regular_json_object(
            receipt, label="Runtime slot reservation",
            required_keys={
                "schema", "version", "marketplaceId", "pluginId", "sourceFingerprint",
                "runtime", "snapshot", "namespaceReceipt", "installReceipt", "createdAt", "generation",
            }, require_stable_identity=True,
        )
        if digest != expected_reservation_sha256:
            _fail("Runtime slot reservation receipt changed.")
        snapshot_id = _string_property(_property(record, "snapshot"), "id")

        def revalidate() -> None:
            current = validate()
            if current != validated:
                _fail("Installation context changed during release.")
            _reservation_target(current, runtime_version, reservation_root)
            snapshot = _validate_snapshot_provenance(
                context=context, expected_marketplace_id=expected_marketplace_id,
                expected_plugin_id=expected_plugin_id, snapshot_id=snapshot_id,
                durable_home=durable, environment={}, require_current_receipts=False,
            )
            _validated_runtime_slot_ownership(
                current, snapshot, runtime_version, reservation_root=target,
                reservation_generation=expected_reservation_generation,
            )
            for lock in locks:
                lock.assert_owned()
            if [_path_evidence(path) for path in evidence_paths] != evidence:
                _fail("Runtime slot release evidence changed or was replaced.")
            if {p.name for p in target.iterdir()} != {receipt.name}:
                _fail("Runtime slot reservation contents changed.")
            if [_read_runtime_marker(path, path.name) for path in markers] != selected:
                _fail("Runtime selection changed during release.")

        revalidate()
        receipt.unlink()
        # Non-recursive removal cannot consume any concurrently added content.
        _reservation_target(validate(), runtime_version, reservation_root)
        for lock in locks:
            lock.assert_owned()
        if [_path_evidence(path) for path in evidence_paths[:-2]] != evidence[:-2]:
            _fail("Runtime slot release evidence changed before directory removal.")
        if _is_link_or_junction(target) or _stat_identity(target.lstat()) != evidence[-2][0]:
            _fail("Runtime reservation directory was replaced.")
        target.rmdir()
        return {**result, "released": True, "reason": "runtime-slot-reservation-released"}
