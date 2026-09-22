# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
def _runtime_slot_completion_paths(
    validated: Mapping[str, Any],
    runtime_version: str,
) -> tuple[Path, Path, Path, Path]:
    _, slot_root, ownership_path = _runtime_slot_paths(
        validated,
        runtime_version,
        require_existing=True,
    )
    build_path = slot_root / BUILD_COMPLETION_FILE
    completion_path = slot_root / RUNTIME_SLOT_COMPLETION_FILE
    if _is_link_or_junction(completion_path):
        _fail("Runtime slot completion may not be a symbolic link or reparse point.")
    return slot_root, ownership_path, build_path, completion_path


def _read_regular_json_object(
    path: Path,
    *,
    label: str,
    required_keys: set[str],
    require_stable_identity: bool = False,
) -> tuple[dict[str, Any], str]:
    if not os.path.lexists(path):
        _fail(f"{label} must exist.")
    if _is_link_or_junction(path):
        _fail(f"{label} may not be a symbolic link or reparse point.")
    actual = canonical_path(path, must_exist=True)
    if not paths_equal(actual, path):
        _fail(f"{label} is not at its exact canonical location '{path}'.")
    try:
        content, _validated_stat = _read_regular_file(
            actual,
            label=label,
            require_stable_identity=require_stable_identity,
        )
    except OSError as error:
        _fail(f"Cannot read {label.lower()} '{actual}': {error}")
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        _fail(f"Invalid JSON in '{actual}': {error}")
    if not isinstance(value, Mapping):
        _fail(f"{label} must be a JSON object.")
    if set(value) != required_keys:
        _fail(f"{label} contains unknown or missing fields.")
    digest = hashlib.sha256(content).hexdigest()
    return dict(value), digest


def _validated_build_completion(
    build_path: Path,
    runtime_version: str,
    snapshot_content_sha256: str,
) -> dict[str, Any]:
    build, receipt_sha256 = _read_regular_json_object(
        build_path,
        label="Build completion evidence",
        required_keys={"version", "completed_at", "pid", "payload_hash"},
    )
    version = build["version"]
    if not isinstance(version, str) or version != runtime_version:
        _fail("Build completion evidence version must match the runtime version.")
    completed_at = build["completed_at"]
    _parse_rfc3339_utc(completed_at, "build completion completed_at")
    pid = build["pid"]
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid < 0
        or pid > MAX_RECEIPT_PID
    ):
        _fail(
            "Build completion evidence pid must be an integer from 0 through "
            f"{MAX_RECEIPT_PID}."
        )
    payload_hash = build["payload_hash"]
    if not isinstance(payload_hash, str) or LOWER_SHA256.fullmatch(payload_hash) is None:
        _fail("Build completion evidence payload_hash must be lowercase 64-hex.")
    if payload_hash != snapshot_content_sha256:
        _fail(
            "Build completion evidence payload_hash does not match the snapshot "
            "content digest."
        )
    return {
        "path": str(build_path),
        "receiptSha256": receipt_sha256,
        "payloadSha256": payload_hash,
        "pid": pid,
        "completedAt": completed_at,
        "value": build,
    }


def _runtime_slot_completion_value(
    validated: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    ownership: Mapping[str, Any],
    build: Mapping[str, Any],
    runtime_version: str,
    slot_root: Path,
    snapshot_content_sha256: str,
) -> dict[str, Any]:
    ownership_path = Path(_string_property(ownership, "ownership"))
    return {
        "schema": RUNTIME_SLOT_COMPLETION_SCHEMA,
        "marketplaceId": _string_property(validated, "marketplaceId"),
        "pluginId": _string_property(validated, "pluginId"),
        "sourceFingerprint": _string_property(validated, "sourceFingerprint"),
        "runtime": {
            "version": runtime_version,
            "root": str(slot_root),
        },
        "snapshot": {
            "id": _string_property(snapshot, "snapshotId"),
            "provenance": _string_property(snapshot, "provenance"),
            "provenanceSha256": _sha256_file(
                Path(_string_property(snapshot, "provenance"))
            ),
            "contentSha256": snapshot_content_sha256,
        },
        "ownership": {
            "path": str(ownership_path),
            "sha256": _sha256_file(ownership_path),
        },
        "build": {
            "receipt": _string_property(build, "path"),
            "receiptSha256": _string_property(build, "receiptSha256"),
            "payloadSha256": _string_property(build, "payloadSha256"),
            "pid": _property(build, "pid"),
        },
        "namespaceReceipt": {
            "path": _string_property(ownership, "namespaceReceipt"),
            "generation": _property(ownership, "namespaceGeneration"),
        },
        "installReceipt": {
            "path": _string_property(ownership, "installReceipt"),
            "generation": _property(ownership, "installGeneration"),
        },
        "completedAt": _string_property(build, "completedAt"),
    }


def _validated_runtime_slot_completion(
    validated: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    ownership: Mapping[str, Any],
    runtime_version: str,
) -> dict[str, Any]:
    slot_root, ownership_path, build_path, completion_path = (
        _runtime_slot_completion_paths(validated, runtime_version)
    )
    snapshot_content_sha256 = _snapshot_content_sha256(
        Path(_string_property(snapshot, "snapshotRoot"))
    )
    completion, _ = _read_regular_json_object(
        completion_path,
        label="Runtime slot completion",
        require_stable_identity=True,
        required_keys={
            "schema",
            "marketplaceId",
            "pluginId",
            "sourceFingerprint",
            "runtime",
            "snapshot",
            "ownership",
            "build",
            "namespaceReceipt",
            "installReceipt",
            "completedAt",
        },
    )
    nested_shapes = (
        ("runtime", {"version", "root"}),
        ("snapshot", {"id", "provenance", "provenanceSha256", "contentSha256"}),
        ("ownership", {"path", "sha256"}),
        ("build", {"receipt", "receiptSha256", "payloadSha256", "pid"}),
        ("namespaceReceipt", {"path", "generation"}),
        ("installReceipt", {"path", "generation"}),
    )
    for name, keys in nested_shapes:
        nested = completion[name]
        if not isinstance(nested, Mapping) or set(nested) != keys:
            _fail(f"Runtime slot completion {name} contains unknown or missing fields.")
    if completion["schema"] != RUNTIME_SLOT_COMPLETION_SCHEMA:
        _fail("Runtime slot completion has an unsupported schema.")
    _parse_rfc3339_utc(
        completion["completedAt"],
        "runtime slot completion completedAt",
    )
    for container_name, key in (
        ("namespaceReceipt", "generation"),
        ("installReceipt", "generation"),
    ):
        _assert_receipt_generation(
            completion[container_name][key],
            f"runtime slot completion {container_name} generation",
        )
    for container_name, fields in (
        ("snapshot", ("provenanceSha256", "contentSha256")),
        ("ownership", ("sha256",)),
        ("build", ("receiptSha256", "payloadSha256")),
    ):
        for field in fields:
            value = completion[container_name][field]
            if not isinstance(value, str) or LOWER_SHA256.fullmatch(value) is None:
                _fail(
                    f"Runtime slot completion {container_name}.{field} must be "
                    "lowercase 64-hex."
                )
    pid = completion["build"]["pid"]
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid < 0
        or pid > MAX_RECEIPT_PID
    ):
        _fail(
            "Runtime slot completion build.pid must be an integer from 0 through "
            f"{MAX_RECEIPT_PID}."
        )
    expected_paths = {
        ("runtime", "root"): str(slot_root),
        ("snapshot", "provenance"): _string_property(snapshot, "provenance"),
        ("ownership", "path"): str(ownership_path),
        ("build", "receipt"): str(build_path),
        ("namespaceReceipt", "path"): _string_property(
            ownership, "namespaceReceipt"
        ),
        ("installReceipt", "path"): _string_property(ownership, "installReceipt"),
    }
    path_fields = (
        ("runtime", "root"),
        ("snapshot", "provenance"),
        ("ownership", "path"),
        ("build", "receipt"),
        ("namespaceReceipt", "path"),
        ("installReceipt", "path"),
    )
    for container_name, key in path_fields:
        recorded = completion[container_name][key]
        if not isinstance(recorded, str) or not _path_is_fully_qualified(recorded):
            _fail(
                f"Runtime slot completion {container_name}.{key} must be absolute."
            )
        if recorded != expected_paths[(container_name, key)]:
            _fail(
                "Runtime slot completion does not match the validated snapshot, "
                "ownership, and installation receipts."
            )
    if (
        completion["marketplaceId"] != _string_property(validated, "marketplaceId")
        or completion["pluginId"] != _string_property(validated, "pluginId")
        or completion["sourceFingerprint"]
        != _string_property(validated, "sourceFingerprint")
        or completion["runtime"]["version"] != runtime_version
        or completion["snapshot"]["id"] != _string_property(snapshot, "snapshotId")
        or completion["snapshot"]["provenanceSha256"]
        != _sha256_file(Path(_string_property(snapshot, "provenance")))
        or completion["snapshot"]["contentSha256"] != snapshot_content_sha256
        or completion["ownership"]["sha256"] != _sha256_file(ownership_path)
        or completion["build"]["payloadSha256"] != snapshot_content_sha256
        or completion["namespaceReceipt"]["generation"]
        != _property(ownership, "namespaceGeneration")
        or completion["installReceipt"]["generation"]
        != _property(ownership, "installGeneration")
    ):
        _fail(
            "Runtime slot completion does not match the validated snapshot, "
            "ownership, and installation receipts."
        )
    return {
        "action": "slot-completion-validate",
        "status": "ready",
        "reason": "runtime-slot-completion-valid",
        "slotRoot": str(slot_root),
        "runtimeVersion": runtime_version,
        "ownership": str(ownership_path),
        "completion": str(completion_path),
        "buildReceipt": str(build_path),
        "receipt": completion,
        "snapshotId": _string_property(snapshot, "snapshotId"),
        "snapshotProvenance": _string_property(snapshot, "provenance"),
        "marketplaceId": _string_property(validated, "marketplaceId"),
        "pluginId": _string_property(validated, "pluginId"),
        "sourceFingerprint": _string_property(validated, "sourceFingerprint"),
        "namespaceReceipt": _string_property(ownership, "namespaceReceipt"),
        "installReceipt": _string_property(ownership, "installReceipt"),
        "namespaceGeneration": _property(ownership, "namespaceGeneration"),
        "installGeneration": _property(ownership, "installGeneration"),
        "completedAt": completion["completedAt"],
        "payloadSha256": completion["build"]["payloadSha256"],
        "activated": False,
        "operative": False,
    }


@_validation_scope
def validate_runtime_slot_completion(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    expected_payload_root: str | os.PathLike[str],
    expected_payload_version: str,
    snapshot_id: str,
    runtime_version: str,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate immutable runtime build completion without activating the slot."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_snapshot_id(snapshot_id)
    _assert_runtime_version(runtime_version)
    if not _path_is_fully_qualified(expected_payload_root):
        _fail("Expected snapshot payload root must be absolute.")
    if not isinstance(expected_payload_version, str) or not expected_payload_version.strip():
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
    ownership = _validated_runtime_slot_ownership(
        validated,
        snapshot,
        runtime_version,
    )
    return _validated_runtime_slot_completion(
        validated,
        snapshot,
        ownership,
        runtime_version,
    )


@_validation_scope
def complete_runtime_slot(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    expected_payload_root: str | os.PathLike[str],
    expected_payload_version: str,
    snapshot_id: str,
    runtime_version: str,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Publish immutable runtime build completion without activating the slot."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_snapshot_id(snapshot_id)
    _assert_runtime_version(runtime_version)
    if not _path_is_fully_qualified(expected_payload_root):
        _fail("Expected snapshot payload root must be absolute.")
    if not isinstance(expected_payload_version, str) or not expected_payload_version.strip():
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
        timeout_seconds=RUNTIME_SLOT_COMPLETION_LOCK_TIMEOUT_SECONDS,
    )
    install_lock = _DirectoryLock(
        cell_root / ".locks" / f"{expected_plugin_id}.install.lock",
        kind="install",
        marketplace_id=expected_marketplace_id,
        plugin_id=expected_plugin_id,
        timeout_seconds=RUNTIME_SLOT_COMPLETION_LOCK_TIMEOUT_SECONDS,
    )
    with genesis_lock, install_lock:
        validated = validate_context_receipt(
            context_path,
            durable,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            expected_cell_root=cell_root,
            environment={},
        )
        slot_root, _, build_path, completion_path = _runtime_slot_completion_paths(
            validated,
            runtime_version,
        )
        if os.path.lexists(completion_path):
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
            ownership = _validated_runtime_slot_ownership(
                validated,
                snapshot,
                runtime_version,
            )
            result = _validated_runtime_slot_completion(
                validated,
                snapshot,
                ownership,
                runtime_version,
            )
            created = False
        else:
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
            ownership = _validated_runtime_slot_ownership(
                validated,
                snapshot,
                runtime_version,
            )
            snapshot_content_sha256 = _snapshot_content_sha256(
                Path(_string_property(snapshot, "snapshotRoot"))
            )
            build = _validated_build_completion(
                build_path,
                runtime_version,
                snapshot_content_sha256,
            )
            desired = _runtime_slot_completion_value(
                validated,
                snapshot,
                ownership,
                build,
                runtime_version,
                slot_root,
                snapshot_content_sha256,
            )
            confirmed_snapshot_content_sha256 = _snapshot_content_sha256(
                Path(_string_property(snapshot, "snapshotRoot"))
            )
            if confirmed_snapshot_content_sha256 != snapshot_content_sha256:
                _fail("Snapshot content changed before completion publication.")
            created = _publish_json_no_replace(
                completion_path,
                desired,
                locks=(genesis_lock, install_lock),
            )
            result = _validated_runtime_slot_completion(
                validated,
                snapshot,
                ownership,
                runtime_version,
            )
        result["action"] = "slot-complete"
        result["reason"] = (
            "runtime-slot-completion-published"
            if created
            else "runtime-slot-completion-current"
        )
        result["created"] = created
        return result


def _read_runtime_marker(path: Path, label: str) -> str | None:
    if not os.path.lexists(path):
        return None
    if _is_link_or_junction(path):
        _fail(f"{label} may not be a symbolic link or reparse point.")
    actual = canonical_path(path, must_exist=True)
    if not paths_equal(actual, path):
        _fail(f"{label} is not at its exact canonical location '{path}'.")
    try:
        content, _ = _read_regular_file(
            actual,
            label=label,
            require_stable_identity=True,
        )
        value = content.decode("utf-8")
    except (OSError, UnicodeError) as error:
        _fail(f"Cannot read {label.lower()} '{actual}': {error}")
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    if not value or "\n" in value or "\r" in value or value != value.strip():
        _fail(f"{label} must contain exactly one runtime version.")
    _assert_runtime_version(value)
    return value


@_validation_scope
def cutover_runtime_slot(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    expected_payload_root: str | os.PathLike[str],
    expected_payload_version: str,
    snapshot_id: str,
    runtime_version: str,
    expected_namespace_generation: int,
    expected_install_generation: int,
    expected_current_version: str | None = None,
    expect_current_absent: bool = False,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """CAS-cut over one completed owned runtime slot without activating it."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_snapshot_id(snapshot_id)
    _assert_runtime_version(runtime_version)
    if not _path_is_fully_qualified(expected_payload_root):
        _fail("Expected snapshot payload root must be absolute.")
    if not isinstance(expected_payload_version, str) or not expected_payload_version.strip():
        _fail("Expected snapshot payload version must be a non-empty string.")
    if (expected_current_version is None) == (not expect_current_absent):
        _fail(
            "Specify exactly one of expected_current_version and "
            "expect_current_absent."
        )
    if expected_current_version is not None:
        _assert_runtime_version(expected_current_version)
    expected_generations = {
        "namespace": expected_namespace_generation,
        "install": expected_install_generation,
    }
    for name, value in expected_generations.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail(f"Expected {name} generation must be a non-negative integer.")
        if value > MAX_RECEIPT_GENERATION:
            _fail(
                f"Expected {name} generation exceeds the portable signed "
                "64-bit maximum."
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
        _fail("Runtime slot context must be absolute.")
    validated = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    cell_root = canonical_path(_string_property(validated, "cellRoot"))
    install_path = canonical_path(_string_property(validated, "installReceipt"))
    genesis_lock = _DirectoryLock(
        durable
        / "marketplaces"
        / ".locks"
        / f"{expected_marketplace_id}.genesis",
        kind="genesis",
        marketplace_id=expected_marketplace_id,
        timeout_seconds=RUNTIME_SLOT_COMPLETION_LOCK_TIMEOUT_SECONDS,
    )
    install_lock = _DirectoryLock(
        cell_root / ".locks" / f"{expected_plugin_id}.install.lock",
        kind="install",
        marketplace_id=expected_marketplace_id,
        plugin_id=expected_plugin_id,
        timeout_seconds=RUNTIME_SLOT_COMPLETION_LOCK_TIMEOUT_SECONDS,
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
        runtime_root = canonical_path(
            _string_property(validated, "versionsRoot")
        ).parent
        current_path = runtime_root / CURRENT_VERSION_FILE
        last_known_good_path = runtime_root / LAST_KNOWN_GOOD_FILE
        actual_namespace_generation = int(validated["namespaceGeneration"])
        actual_install_generation = int(validated["generation"])
        actual_current_version = _read_runtime_marker(
            current_path,
            "Current version marker",
        )
        actual_last_known_good = _read_runtime_marker(
            last_known_good_path,
            "Last-known-good marker",
        )
        actual_generations = {
            "namespace": actual_namespace_generation,
            "install": actual_install_generation,
        }
        if actual_generations != expected_generations:
            return {
                "action": "slot-cutover",
                "status": "revalidation-required",
                "reason": "generation-changed",
                "cutoverChanged": False,
                "runtimeVersion": runtime_version,
                "currentVersion": actual_current_version,
                "lastKnownGoodVersion": actual_last_known_good,
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "expectedNamespaceGeneration": expected_namespace_generation,
                "expectedInstallGeneration": expected_install_generation,
                "activated": False,
                "operative": False,
            }
        namespace_receipt = read_json(validated["namespaceReceipt"])
        if not isinstance(namespace_receipt, Mapping):
            _fail("namespace.json must be a JSON object.")
        if (
            _string_property(namespace_receipt, "state") != "active"
            or _string_property(validated, "state") != "active"
        ):
            _fail("Runtime slot cutover requires active namespace and install receipts.")
        current_matches = (
            actual_current_version is None
            if expect_current_absent
            else actual_current_version == expected_current_version
        )
        if not current_matches:
            return {
                "action": "slot-cutover",
                "status": "revalidation-required",
                "reason": "current-version-changed",
                "cutoverChanged": False,
                "runtimeVersion": runtime_version,
                "currentVersion": actual_current_version,
                "lastKnownGoodVersion": actual_last_known_good,
                "expectedCurrentVersion": expected_current_version,
                "expectedCurrentAbsent": expect_current_absent,
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "activated": False,
                "operative": False,
            }

        completion = validate_runtime_slot_completion(
            context=install_path,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            expected_payload_root=expected_payload_root,
            expected_payload_version=expected_payload_version,
            snapshot_id=snapshot_id,
            runtime_version=runtime_version,
            durable_home=durable,
            environment={},
        )
        confirmed_current_version = _read_runtime_marker(
            current_path,
            "Current version marker",
        )
        confirmed_last_known_good = _read_runtime_marker(
            last_known_good_path,
            "Last-known-good marker",
        )
        if (
            confirmed_current_version != actual_current_version
            or confirmed_last_known_good != actual_last_known_good
        ):
            return {
                "action": "slot-cutover",
                "status": "revalidation-required",
                "reason": "runtime-marker-changed",
                "cutoverChanged": False,
                "runtimeVersion": runtime_version,
                "currentVersion": confirmed_current_version,
                "lastKnownGoodVersion": confirmed_last_known_good,
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "activated": False,
                "operative": False,
            }

        desired_last_known_good = runtime_version
        locks = (genesis_lock, install_lock)
        changed = (
            actual_current_version != runtime_version
            or actual_last_known_good != desired_last_known_good
        )
        if actual_current_version != runtime_version:
            _atomic_write_text(current_path, runtime_version, lock=locks)
        if actual_last_known_good != desired_last_known_good:
            _atomic_write_text(
                last_known_good_path,
                desired_last_known_good,
                lock=locks,
            )
        published_current = _read_runtime_marker(
            current_path,
            "Current version marker",
        )
        published_last_known_good = _read_runtime_marker(
            last_known_good_path,
            "Last-known-good marker",
        )
        if (
            published_current != runtime_version
            or published_last_known_good != desired_last_known_good
        ):
            _fail(
                "Published runtime cutover markers did not validate as current: "
                f"current={published_current!r}, "
                f"last-known-good={published_last_known_good!r}, "
                f"expected={runtime_version!r}."
            )
        return {
            "action": "slot-cutover",
            "status": "ready",
            "reason": (
                "runtime-slot-cutover-published"
                if changed
                else "runtime-slot-cutover-current"
            ),
            "cutoverChanged": changed,
            "runtimeVersion": runtime_version,
            "previousVersion": actual_current_version,
            "currentVersion": published_current,
            "lastKnownGoodVersion": published_last_known_good,
            "currentMarker": str(current_path),
            "lastKnownGoodMarker": str(last_known_good_path),
            "completion": completion["completion"],
            "namespaceGeneration": actual_namespace_generation,
            "installGeneration": actual_install_generation,
            "activated": False,
            "operative": False,
        }
