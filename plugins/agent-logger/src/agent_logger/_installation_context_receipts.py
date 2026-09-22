# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

def _assert_positive_integer(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{name} must be an integer.")
    if value < 1:
        _fail(f"{name} must be at least 1.")


def _assert_receipt_state(value: Any, name: str) -> None:
    if value not in {"active", "inactive", "orphaned", "removing"}:
        _fail(f"{name} must be active, inactive, orphaned, or removing.")


def _assert_receipt_generation(value: Any, name: str) -> None:
    _assert_positive_integer(value, name)
    if value > MAX_RECEIPT_GENERATION:
        _fail(f"{name} exceeds the portable signed 64-bit maximum.")


@_validation_scope
def validate_namespace_receipt(
    receipt_path: str | os.PathLike[str],
    durable_home: str | os.PathLike[str],
) -> dict[str, Any]:
    receipt_pointer = Path(receipt_path)
    if not _path_is_fully_qualified(receipt_pointer):
        _fail("The namespace receipt pointer must be absolute.")
    if not _path_is_fully_qualified(durable_home):
        _fail("--durable-home must be absolute.")
    durable = canonical_path(durable_home)
    lexical_marketplaces_root = durable / "marketplaces"
    if _is_link_or_junction(lexical_marketplaces_root):
        _fail("The marketplaces root may not be a symbolic link or reparse point.")
    marketplaces_root = canonical_path(lexical_marketplaces_root)
    if not paths_equal(marketplaces_root.parent, durable):
        _fail("The marketplaces root escapes the durable installation home.")
    lexical_cell_root = receipt_pointer.parent
    if _is_link_or_junction(lexical_cell_root):
        _fail("The marketplace cell root may not be a symbolic link or reparse point.")
    cell_root = canonical_path(lexical_cell_root)
    if not paths_equal(cell_root.parent, marketplaces_root):
        _fail(
            f"Namespace receipt '{receipt_pointer}' is outside the durable "
            "marketplaces root."
        )
    if _is_link_or_junction(receipt_pointer):
        _fail("namespace.json may not be a symbolic link or reparse point.")
    actual_receipt = canonical_path(receipt_pointer, must_exist=True)
    if not paths_equal(actual_receipt.parent, cell_root):
        _fail("namespace.json escapes its canonical marketplace cell.")
    marketplace_id = cell_root.name
    lexical_canonical_receipt = cell_root / "namespace.json"
    if _is_link_or_junction(lexical_canonical_receipt):
        _fail("namespace.json may not be a symbolic link or reparse point.")
    canonical_receipt = canonical_path(lexical_canonical_receipt)
    if not paths_equal(actual_receipt, canonical_receipt):
        _fail(
            f"namespace.json is not at its exact canonical receipt location "
            f"'{canonical_receipt}'."
        )
    namespace = read_json(actual_receipt)
    namespace_version = _property(namespace, "version")
    if (
        _string_property(namespace, "schema")
        != "copilot-extensions.marketplace-namespace"
        or isinstance(namespace_version, bool)
        or not isinstance(namespace_version, int)
        or namespace_version != 1
    ):
        _fail(f"Namespace receipt '{actual_receipt}' has an unsupported schema or version.")
    if _string_property(namespace, "marketplaceId") != marketplace_id:
        _fail(f"Namespace receipt '{actual_receipt}' does not match its cell directory.")
    match = re.fullmatch(r"(.+)--([0-9a-f]{16})", marketplace_id)
    if match is None:
        _fail(f"Invalid source-derived marketplace id '{marketplace_id}'.")
    _assert_receipt_generation(
        _property(namespace, "generation"),
        "namespace.json generation",
    )
    _assert_receipt_state(_property(namespace, "state"), "namespace.json state")
    source_receipt = _property(namespace, "source")
    if not isinstance(source_receipt, Mapping):
        _fail(f"Namespace receipt '{actual_receipt}' has no source identity.")
    normalized = normalize_source(
        {
            "kind": _property(source_receipt, "kind"),
            "canonical": _property(source_receipt, "canonical"),
            "ref": _property(source_receipt, "ref", ""),
        },
        from_receipt=True,
    )
    identity = source_identity(normalized, match.group(1))
    if identity["marketplaceId"] != marketplace_id:
        _fail(f"Namespace receipt '{actual_receipt}' id does not match its normalized source.")
    if _string_property(source_receipt, "fingerprint") != identity["fingerprint"]:
        _fail(
            f"Namespace receipt '{actual_receipt}' fingerprint does not match "
            "its normalized source."
        )
    return {
        "receipt": namespace,
        "receiptPath": actual_receipt,
        "cellRoot": cell_root,
        "marketplaceId": marketplace_id,
        "identity": identity,
    }


def _assert_plugin_id(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", value) or value in {
        ".",
        "..",
    }:
        _fail(f"Invalid filesystem-safe plugin id '{value}'.")
    basename = value.split(".", 1)[0].upper()
    if (
        basename in {"CON", "PRN", "AUX", "NUL"}
        or re.fullmatch(r"COM[1-9]", basename)
        or re.fullmatch(r"LPT[1-9]", basename)
    ):
        _fail(f"Invalid filesystem-safe plugin id '{value}'.")


def _assert_snapshot_id(value: str) -> None:
    if not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9._+-]*[A-Za-z0-9])?",
        value,
    ) or value in {".", ".."}:
        _fail(f"Invalid filesystem-safe snapshot id '{value}'.")
    basename = value.split(".", 1)[0].upper()
    if (
        basename in {"CON", "PRN", "AUX", "NUL"}
        or re.fullmatch(r"COM[1-9]", basename)
        or re.fullmatch(r"LPT[1-9]", basename)
    ):
        _fail(f"Invalid filesystem-safe snapshot id '{value}'.")


def _assert_runtime_version(value: str) -> None:
    if len(value) > 128:
        _fail("Runtime version exceeds the portable 128-character limit.")
    if not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9._+-]*[A-Za-z0-9])?",
        value,
    ) or value in {".", ".."}:
        _fail(f"Invalid filesystem-safe runtime version '{value}'.")
    basename = value.split(".", 1)[0].upper()
    if (
        basename in {"CON", "PRN", "AUX", "NUL"}
        or re.fullmatch(r"COM[1-9]", basename)
        or re.fullmatch(r"LPT[1-9]", basename)
    ):
        _fail(f"Invalid filesystem-safe runtime version '{value}'.")


def _resolve_relative_root(plugin_root: Path, relative: str, name: str) -> Path:
    if not relative.strip() or Path(relative).is_absolute():
        _fail(f"roots.{name} must be a non-empty relative path.")
    raw_parts = re.split(r"[\\/]", relative)
    if "." in raw_parts or ".." in raw_parts:
        _fail(f"roots.{name} may not escape or use dot segments.")
    resolved = canonical_path(plugin_root / relative)
    if resolved.parent != plugin_root and not path_is_within(resolved, plugin_root):
        _fail(f"roots.{name} escapes pluginRoot.")
    return resolved


@_validation_scope
def validate_context_receipt(
    receipt_path: str | os.PathLike[str],
    durable_home: str | os.PathLike[str],
    *,
    expected_marketplace_id: str | None = None,
    expected_plugin_id: str | None = None,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_cell_root: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    environment = environment if environment is not None else os.environ
    if not _path_is_fully_qualified(durable_home):
        _fail("--durable-home must be absolute.")
    receipt_pointer = Path(receipt_path)
    if not _path_is_fully_qualified(receipt_pointer):
        _fail("The installation-context receipt pointer must be absolute.")
    if _is_link_or_junction(receipt_pointer):
        _fail("install.json may not be a symbolic link or reparse point.")
    for name, expectation in (
        ("expected payload root", expected_payload_root),
        ("expected cell root", expected_cell_root),
    ):
        if expectation is not None and not _path_is_fully_qualified(expectation):
            _fail(f"{name} must be absolute.")
    actual_receipt = canonical_path(receipt_pointer, must_exist=True)
    install = read_json(actual_receipt)
    install_version = _property(install, "version")
    if (
        _string_property(install, "schema")
        != "copilot-extensions.plugin-installation"
        or isinstance(install_version, bool)
        or not isinstance(install_version, int)
        or install_version != 1
    ):
        _fail("install.json has an unsupported schema or version.")
    marketplace_id = _string_property(install, "marketplaceId")
    plugin_id = _string_property(install, "pluginId")
    if not marketplace_id or not plugin_id:
        _fail("install.json identity is incomplete.")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*--[0-9a-f]{16}", marketplace_id):
        _fail(f"Invalid source-derived marketplace id '{marketplace_id}'.")
    _assert_plugin_id(plugin_id)
    durable = canonical_path(durable_home)
    lexical_marketplaces_root = durable / "marketplaces"
    if _is_link_or_junction(lexical_marketplaces_root):
        _fail("The marketplaces root may not be a symbolic link or reparse point.")
    marketplaces_root = canonical_path(lexical_marketplaces_root)
    if not paths_equal(marketplaces_root.parent, durable):
        _fail("The marketplaces root escapes the durable installation home.")
    lexical_cell_root = marketplaces_root / marketplace_id
    if _is_link_or_junction(lexical_cell_root):
        _fail("The marketplace cell root may not be a symbolic link or reparse point.")
    cell_root = canonical_path(lexical_cell_root)
    if not paths_equal(cell_root.parent, marketplaces_root):
        _fail("The marketplace cell root escapes the marketplaces root.")
    lexical_plugins_root = cell_root / "plugins"
    if _is_link_or_junction(lexical_plugins_root):
        _fail("The cell plugins root may not be a symbolic link or reparse point.")
    plugins_root = canonical_path(lexical_plugins_root)
    if not paths_equal(plugins_root.parent, cell_root):
        _fail("The cell plugins root escapes the marketplace cell.")
    lexical_plugin_root = plugins_root / plugin_id
    if _is_link_or_junction(lexical_plugin_root):
        _fail("The plugin root may not be a symbolic link or reparse point.")
    plugin_root = canonical_path(lexical_plugin_root)
    if not paths_equal(plugin_root.parent, plugins_root):
        _fail("The plugin root escapes the cell plugins root.")
    lexical_canonical_receipt = plugin_root / "install.json"
    if _is_link_or_junction(lexical_canonical_receipt):
        _fail("install.json may not be a symbolic link or reparse point.")
    canonical_receipt = canonical_path(lexical_canonical_receipt)
    if not paths_equal(actual_receipt, canonical_receipt):
        _fail(
            f"install.json is not at its exact canonical receipt location "
            f"'{canonical_receipt}'."
        )
    if not paths_equal(_string_property(install, "pluginRoot"), plugin_root):
        _fail("install.json pluginRoot does not match its canonical cell/plugin location.")
    if expected_marketplace_id and marketplace_id != expected_marketplace_id:
        _fail(
            f"Expected marketplace '{expected_marketplace_id}', receipt names "
            f"'{marketplace_id}'."
        )
    if expected_plugin_id and plugin_id != expected_plugin_id:
        _fail(f"Expected plugin '{expected_plugin_id}', receipt names '{plugin_id}'.")
    if expected_cell_root and not paths_equal(cell_root, expected_cell_root):
        _fail(f"Expected cell '{expected_cell_root}', receipt belongs to '{cell_root}'.")
    _assert_receipt_generation(_property(install, "generation"), "install.json generation")
    _assert_receipt_state(_property(install, "state"), "install.json state")

    lexical_namespace_path = cell_root / "namespace.json"
    if _is_link_or_junction(lexical_namespace_path):
        _fail("namespace.json may not be a symbolic link or reparse point.")
    namespace_path = canonical_path(lexical_namespace_path)
    if not paths_equal(_string_property(install, "namespaceReceipt"), namespace_path):
        _fail("install.json namespaceReceipt is not the exact namespace receipt in the same cell.")
    validated_namespace = validate_namespace_receipt(lexical_namespace_path, durable)
    if validated_namespace["marketplaceId"] != marketplace_id:
        _fail("namespace.json marketplaceId does not match install.json.")
    identity = validated_namespace["identity"]

    payload_receipt = _property(install, "payload")
    if not isinstance(payload_receipt, Mapping):
        _fail("install.json payload is missing.")
    payload_text = _string_property(payload_receipt, "root")
    if not _path_is_fully_qualified(payload_text):
        _fail("payload.root must be absolute.")
    if not _string_property(payload_receipt, "version").strip():
        _fail("payload.version must be a non-empty string.")
    if _string_property(payload_receipt, "origin") not in {
        "installed",
        "directory",
        "staged",
        "explicit",
    }:
        _fail("payload.origin must be installed, directory, staged, or explicit.")
    origin_receipt = _property(payload_receipt, "originReceipt")
    if origin_receipt is not None:
        if not isinstance(origin_receipt, str):
            _fail("payload.originReceipt must be a string.")
        if not _path_is_fully_qualified(origin_receipt):
            _fail("payload.originReceipt must be absolute.")
    payload_root = canonical_path(payload_text)
    if expected_payload_root and not paths_equal(payload_root, expected_payload_root):
        _fail(f"Expected payload '{expected_payload_root}', receipt names '{payload_root}'.")
    inherited_payload = environment.get("COPILOT_PLUGIN_ROOT")
    if inherited_payload:
        if not _path_is_fully_qualified(inherited_payload):
            _fail("COPILOT_PLUGIN_ROOT must be absolute.")
        if not paths_equal(payload_root, inherited_payload):
            _fail("COPILOT_PLUGIN_ROOT conflicts with the validated payload root.")

    roots_receipt = _property(install, "roots")
    if not isinstance(roots_receipt, Mapping):
        _fail("install.json roots are missing.")
    roots: dict[str, Path] = {}
    for name in ("versions", "snapshots", "state", "run", "logs", "cache", "launchers"):
        roots[f"{name}Root"] = _resolve_relative_root(
            plugin_root,
            _string_property(roots_receipt, name),
            name,
        )
    return {
        "action": "validate",
        "marketplaceId": marketplace_id,
        "marketplaceSlot": marketplace_id,
        "sourceFingerprint": identity["fingerprint"],
        "source": {
            "kind": identity["kind"],
            "canonical": identity["canonical"],
            "ref": identity["ref"],
        },
        "pluginId": plugin_id,
        "payloadRoot": str(payload_root),
        "cellRoot": str(cell_root),
        "pluginRoot": str(plugin_root),
        **{name: str(path) for name, path in roots.items()},
        "reposRoot": str(canonical_path(cell_root / "repos")),
        "namespaceReceipt": str(namespace_path),
        "installReceipt": str(actual_receipt),
        "namespaceGeneration": _property(
            validated_namespace["receipt"],
            "generation",
        ),
        "generation": _property(install, "generation"),
        "state": _property(install, "state"),
    }


def _assert_expected_generation(
    actual: int,
    expected: int,
    receipt_name: str,
) -> None:
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        _fail(f"Expected {receipt_name} generation must be a non-negative integer.")
    if expected > MAX_RECEIPT_GENERATION:
        _fail(
            f"Expected {receipt_name} generation exceeds the portable "
            "signed 64-bit maximum."
        )
    if actual != expected:
        _fail(
            f"{receipt_name} generation changed: expected {expected}, found {actual}; "
            "restart installation-context resolution."
        )


def _normalized_locator(locator: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if locator is None:
        return None
    kind = _string_property(locator, "kind")
    if kind == "installed":
        declared_in = _property(locator, "declaredIn", [])
        if not isinstance(declared_in, Sequence) or isinstance(declared_in, (str, bytes)):
            _fail("Installed locator declaredIn must be an array.")
        declarations: list[str] = []
        for value in declared_in:
            if not isinstance(value, str) or not value:
                _fail("Installed locator declaredIn values must be non-empty strings.")
            if value not in declarations:
                declarations.append(value)
        return {
            "kind": "installed",
            "copilotHome": str(canonical_path(_string_property(locator, "copilotHome"))),
            "marketplaceKey": _string_property(locator, "marketplaceKey"),
            "declaredIn": declarations,
        }
    if kind == "directory":
        return {
            "kind": "directory",
            "marketplaceRoot": str(
                canonical_path(
                    _string_property(locator, "marketplaceRoot"),
                    must_exist=True,
                )
            ),
        }
    _fail(f"Unsupported marketplace locator kind '{kind}'.")


def _locator_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if _string_property(left, "kind") != _string_property(right, "kind"):
        return False
    if left["kind"] == "installed":
        return (
            _string_property(left, "marketplaceKey")
            == _string_property(right, "marketplaceKey")
            and paths_equal(
                _string_property(left, "copilotHome"),
                _string_property(right, "copilotHome"),
            )
        )
    return paths_equal(
        _string_property(left, "marketplaceRoot"),
        _string_property(right, "marketplaceRoot"),
    )


def _namespace_receipt_value(
    resolved: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
    *,
    state: str,
    now: str,
) -> dict[str, Any]:
    _assert_receipt_state(state, "namespace.json state")
    locator = _normalized_locator(_property(resolved, "locator"))
    locators: list[dict[str, Any]] = []
    if existing is not None:
        prior = _property(existing, "locators", [])
        if not isinstance(prior, Sequence) or isinstance(prior, (str, bytes)):
            _fail("namespace.json locators must be an array.")
        for item in prior:
            if not isinstance(item, Mapping):
                _fail("namespace.json locators must contain objects.")
            locators.append(dict(item))
    if locator is not None and not any(_locator_equal(locator, item) for item in locators):
        locators.append(locator)
    locators = locators[-MAX_NAMESPACE_LOCATORS:]
    source = _property(resolved, "source")
    if not isinstance(source, Mapping):
        _fail("Resolved installation source is missing.")
    created_at = _string_property(existing, "createdAt") if existing is not None else now
    return {
        "schema": "copilot-extensions.marketplace-namespace",
        "version": 1,
        "marketplaceId": _string_property(resolved, "marketplaceId"),
        "source": {
            "kind": _string_property(source, "kind"),
            "canonical": _string_property(source, "canonical"),
            "ref": _string_property(source, "ref"),
            "fingerprint": _string_property(resolved, "sourceFingerprint"),
        },
        "locators": locators,
        "generation": _property(existing, "generation") if existing is not None else 1,
        "state": state,
        "createdAt": created_at or now,
        "updatedAt": now,
    }


def _install_receipt_value(
    resolved: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
    *,
    payload_version: str,
    payload_origin: str,
    payload_origin_receipt: str | os.PathLike[str] | None,
    state: str,
    now: str,
) -> dict[str, Any]:
    if not payload_version.strip():
        _fail("payload version must be a non-empty string.")
    if payload_origin not in {"installed", "directory", "staged", "explicit"}:
        _fail("payload origin must be installed, directory, staged, or explicit.")
    _assert_receipt_state(state, "install.json state")
    payload: dict[str, Any] = {
        "root": _string_property(resolved, "payloadRoot"),
        "version": payload_version,
        "origin": payload_origin,
    }
    if payload_origin_receipt is not None:
        origin_receipt = Path(payload_origin_receipt)
        if not _path_is_fully_qualified(origin_receipt):
            _fail("payload origin receipt must be absolute.")
        payload["originReceipt"] = str(canonical_path(origin_receipt, must_exist=True))
    if existing is not None:
        existing_roots = _property(existing, "roots")
        if not isinstance(existing_roots, Mapping):
            _fail("install.json roots are missing.")
        roots = {name: _string_property(existing_roots, name) for name in ROOT_NAMES}
    else:
        roots = {
            name: os.path.relpath(
                _string_property(resolved, f"{name}Root"),
                _string_property(resolved, "pluginRoot"),
            )
            for name in ROOT_NAMES
        }
    created_at = _string_property(existing, "createdAt") if existing is not None else now
    return {
        "schema": "copilot-extensions.plugin-installation",
        "version": 1,
        "marketplaceId": _string_property(resolved, "marketplaceId"),
        "pluginId": _string_property(resolved, "pluginId"),
        "pluginRoot": _string_property(resolved, "pluginRoot"),
        "namespaceReceipt": _string_property(resolved, "namespaceReceipt"),
        "payload": payload,
        "roots": roots,
        "generation": _property(existing, "generation") if existing is not None else 1,
        "state": state,
        "createdAt": created_at or now,
        "updatedAt": now,
    }


def _without_mutation_fields(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in receipt.items()
        if key not in {"generation", "updatedAt"}
    }


@_validation_scope
def stamp_context(
    *,
    payload_version: str,
    payload_origin: str,
    expected_namespace_generation: int,
    expected_install_generation: int,
    payload_origin_receipt: str | os.PathLike[str] | None = None,
    namespace_state: str = "active",
    install_state: str = "active",
    payload_root: str | os.PathLike[str] | None = None,
    plugin_id: str | None = None,
    copilot_home: str | os.PathLike[str] | None = None,
    project_root: str | os.PathLike[str] | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    source_descriptor: Mapping[str, Any] | None = None,
    marketplace_key: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Create or update installation receipts under attributable directory locks."""

    caller_environment = environment if environment is not None else os.environ
    resolution_environment = dict(caller_environment)
    resolution_environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    resolved = resolve_context(
        payload_root=payload_root,
        plugin_id=plugin_id,
        copilot_home=copilot_home,
        project_root=project_root,
        durable_home=durable_home,
        source_descriptor=source_descriptor,
        marketplace_key=marketplace_key,
        environment=resolution_environment,
    )
    durable = canonical_path(
        durable_home
        or Path(caller_environment.get("HOME") or Path.home())
        / ".copilot-extensions"
    )
    marketplace_id = _string_property(resolved, "marketplaceId")
    resolved_plugin_id = _string_property(resolved, "pluginId")
    namespace_path = Path(_string_property(resolved, "namespaceReceipt"))
    install_path = Path(_string_property(resolved, "installReceipt"))
    namespace_changed = False
    install_changed = False

    genesis_lock = _DirectoryLock(
        durable / "marketplaces" / ".locks" / f"{marketplace_id}.genesis",
        kind="genesis",
        marketplace_id=marketplace_id,
    )
    with genesis_lock:
        existing_namespace: Mapping[str, Any] | None = None
        if namespace_path.exists():
            validated = validate_namespace_receipt(namespace_path, durable)
            existing_namespace = validated["receipt"]
            actual_generation = _property(existing_namespace, "generation")
        else:
            actual_generation = 0
        _assert_expected_generation(
            actual_generation,
            expected_namespace_generation,
            "namespace.json",
        )
        desired_namespace = _namespace_receipt_value(
            resolved,
            existing_namespace,
            state=namespace_state,
            now=_utc_now(),
        )
        if (
            existing_namespace is None
            or _without_mutation_fields(existing_namespace)
            != _without_mutation_fields(desired_namespace)
        ):
            if existing_namespace is not None:
                if actual_generation >= MAX_RECEIPT_GENERATION:
                    _fail(
                        "namespace.json generation cannot be incremented; "
                        "explicit repair is required."
                    )
                desired_namespace["generation"] = actual_generation + 1
            _atomic_write_json(namespace_path, desired_namespace, lock=genesis_lock)
            namespace_changed = True

    install_lock = _DirectoryLock(
        Path(_string_property(resolved, "cellRoot"))
        / ".locks"
        / f"{resolved_plugin_id}.install.lock",
        kind="install",
        marketplace_id=marketplace_id,
        plugin_id=resolved_plugin_id,
    )
    with install_lock:
        existing_install: Mapping[str, Any] | None = None
        if install_path.exists():
            validated = validate_context_receipt(
                install_path,
                durable,
                expected_marketplace_id=marketplace_id,
                expected_plugin_id=resolved_plugin_id,
                environment={},
            )
            existing_install = read_json(validated["installReceipt"])
            actual_generation = _property(existing_install, "generation")
        else:
            actual_generation = 0
        _assert_expected_generation(
            actual_generation,
            expected_install_generation,
            "install.json",
        )
        desired_install = _install_receipt_value(
            resolved,
            existing_install,
            payload_version=payload_version,
            payload_origin=payload_origin,
            payload_origin_receipt=payload_origin_receipt,
            state=install_state,
            now=_utc_now(),
        )
        if (
            existing_install is None
            or _without_mutation_fields(existing_install)
            != _without_mutation_fields(desired_install)
        ):
            if existing_install is not None:
                if actual_generation >= MAX_RECEIPT_GENERATION:
                    _fail(
                        "install.json generation cannot be incremented; "
                        "explicit repair is required."
                    )
                desired_install["generation"] = actual_generation + 1
            _atomic_write_json(install_path, desired_install, lock=install_lock)
            install_changed = True

    result = validate_context_receipt(
        install_path,
        durable,
        expected_marketplace_id=marketplace_id,
        expected_plugin_id=resolved_plugin_id,
        expected_payload_root=_string_property(resolved, "payloadRoot"),
        environment=caller_environment,
    )
    result.update(
        {
            "action": "stamp",
            "namespaceChanged": namespace_changed,
            "installChanged": install_changed,
            "operative": False,
        }
    )
    return result
