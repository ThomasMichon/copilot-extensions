# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
def _find_existing_source(
    durable_home: Path,
    fingerprint: str,
    desired_id: str,
    locator: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    marketplaces = durable_home / "marketplaces"
    if not marketplaces.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for cell_directory in sorted(path for path in marketplaces.iterdir() if path.is_dir()):
        receipt_path = cell_directory / "namespace.json"
        if not receipt_path.is_file():
            continue
        validated = validate_namespace_receipt(receipt_path, durable_home)
        receipt_fingerprint = validated["identity"]["fingerprint"]
        receipt_id = validated["marketplaceId"]
        if cell_directory.name == desired_id and receipt_fingerprint != fingerprint:
            _fail(
                f"Marketplace id '{desired_id}' is already occupied by a different "
                "full source fingerprint."
            )
        if receipt_fingerprint != fingerprint:
            continue
        locator_match = locator is None
        if locator is not None:
            receipt_locators = _property(validated["receipt"], "locators", [])
            locator_match = any(
                isinstance(known, Mapping) and _locator_matches(locator, known)
                for known in receipt_locators
            )
        results.append(
            {
                "marketplaceId": receipt_id,
                "namespaceReceipt": str(canonical_path(receipt_path)),
                "sameId": receipt_id == desired_id,
                "locatorMatch": locator_match,
            }
        )
    return results


@_validation_scope
def resolve_context(
    *,
    payload_root: str | os.PathLike[str] | None = None,
    plugin_id: str | None = None,
    copilot_home: str | os.PathLike[str] | None = None,
    project_root: str | os.PathLike[str] | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    context: str | os.PathLike[str] | None = None,
    expected_marketplace_id: str | None = None,
    expected_plugin_id: str | None = None,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_cell_root: str | os.PathLike[str] | None = None,
    source_descriptor: Mapping[str, Any] | None = None,
    marketplace_key: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve existing evidence into a non-operative installation context."""

    environment = environment if environment is not None else os.environ
    home = Path(environment.get("HOME") or Path.home())
    copilot_value = Path(copilot_home or home / ".copilot")
    durable_value = Path(durable_home or home / ".copilot-extensions")
    if not _path_is_fully_qualified(copilot_value) or not _path_is_fully_qualified(
        durable_value
    ):
        _fail("--copilot-home and --durable-home must be absolute.")
    copilot = canonical_path(copilot_value)
    durable = canonical_path(durable_value)
    project = (
        canonical_path(project_root, must_exist=True) if project_root is not None else None
    )
    pointer = context or environment.get("COPILOT_EXTENSIONS_CONTEXT")
    if pointer:
        payload_expectation = (
            expected_payload_root
            or payload_root
            or environment.get("COPILOT_PLUGIN_ROOT")
        )
        plugin_expectation = plugin_id or expected_plugin_id
        if not plugin_expectation:
            _fail("resolve with an explicit context requires an expected plugin id.")
        if (
            payload_expectation is None
            and expected_marketplace_id is None
            and expected_cell_root is None
        ):
            _fail(
                "resolve with an explicit context requires an expected payload, "
                "marketplace, or cell identity."
            )
        result = validate_context_receipt(
            pointer,
            durable,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=plugin_expectation,
            expected_payload_root=payload_expectation,
            expected_cell_root=expected_cell_root,
            environment=environment,
        )
        result["action"] = "resolve"
        return result

    payload_value = payload_root or environment.get("COPILOT_PLUGIN_ROOT")
    if payload_value is None:
        _fail("resolve requires --payload-root or COPILOT_PLUGIN_ROOT.")
    payload_input = Path(payload_value)
    if not _path_is_fully_qualified(payload_input):
        _fail("The payload root must be absolute.")
    payload = canonical_path(payload_input, must_exist=True)
    if not payload.is_dir():
        _fail(f"The payload root must be an existing directory: {payload}")
    inherited_payload = environment.get("COPILOT_PLUGIN_ROOT")
    if inherited_payload:
        if not _path_is_fully_qualified(inherited_payload):
            _fail("COPILOT_PLUGIN_ROOT must be absolute.")
        if not paths_equal(payload, inherited_payload):
            _fail("COPILOT_PLUGIN_ROOT conflicts with --payload-root.")

    if source_descriptor is not None:
        if not plugin_id:
            _fail("Explicit source resolution requires --plugin-id.")
        evidence = {
            "source": normalize_source(source_descriptor),
            "pluginId": plugin_id,
            "readableName": marketplace_key or "marketplace",
            "locator": None,
        }
    else:
        evidence = _resolve_installed_evidence(payload, copilot, project)
        if evidence is None:
            evidence = _resolve_directory_evidence(payload, plugin_id)
        if evidence is None:
            _fail(
                f"Cannot establish marketplace provenance for payload '{payload}'. "
                "Supply an explicit source descriptor for management/development mode."
            )
        if plugin_id and plugin_id != evidence["pluginId"]:
            _fail(
                f"Expected plugin '{plugin_id}', payload evidence identifies "
                f"'{evidence['pluginId']}'."
            )

    source = evidence["source"]
    identity = source_identity(source, evidence["readableName"])
    resolved_plugin_id = str(evidence["pluginId"])
    _assert_plugin_id(resolved_plugin_id)
    existing = _find_existing_source(
        durable,
        identity["fingerprint"],
        identity["marketplaceId"],
        evidence["locator"],
    )
    rebind = [
        entry
        for entry in existing
        if not entry["sameId"] or not entry["locatorMatch"]
    ]
    if rebind:
        owners = ", ".join(str(entry["marketplaceId"]) for entry in rebind)
        _fail(
            f"Source '{identity['fingerprint']}' already owns cell/locator '{owners}'; "
            "explicit rebind or new-cell intent is required."
        )
    cell_root = canonical_path(durable / "marketplaces" / identity["marketplaceId"])
    plugin_root_path = canonical_path(cell_root / "plugins" / resolved_plugin_id)
    return {
        "action": "resolve",
        "source": {
            "kind": identity["kind"],
            "canonical": identity["canonical"],
            "ref": identity["ref"],
            "record": identity["record"],
        },
        "sourceFingerprint": identity["fingerprint"],
        "marketplaceId": identity["marketplaceId"],
        "marketplaceSlot": identity["marketplaceId"],
        "pluginId": resolved_plugin_id,
        "payloadRoot": str(payload),
        "cellRoot": str(cell_root),
        "pluginRoot": str(plugin_root_path),
        "versionsRoot": str(canonical_path(plugin_root_path / "versions")),
        "snapshotsRoot": str(canonical_path(plugin_root_path / "snapshots")),
        "stateRoot": str(canonical_path(plugin_root_path / "state")),
        "runRoot": str(canonical_path(plugin_root_path / "run")),
        "logsRoot": str(canonical_path(plugin_root_path / "logs")),
        "cacheRoot": str(canonical_path(plugin_root_path / "cache")),
        "launchersRoot": str(canonical_path(plugin_root_path / "launchers")),
        "reposRoot": str(canonical_path(cell_root / "repos")),
        "namespaceReceipt": str(canonical_path(cell_root / "namespace.json")),
        "installReceipt": str(canonical_path(plugin_root_path / "install.json")),
        "locator": evidence["locator"],
        "existingCells": existing,
        "rebindRequired": False,
        "operative": False,
    }


_MISSING = object()


def _exact_property(value: Mapping[str, Any], name: str) -> Any:
    if not isinstance(value, Mapping):
        _fail(f"{name} belongs to a JSON object.")
    for candidate in value:
        if not isinstance(candidate, str):
            _fail("JSON object property names must be strings.")
        if candidate != name and candidate.casefold() == name.casefold():
            _fail(f"JSON property '{candidate}' conflicts with exact case '{name}'.")
    return value[name] if name in value else _MISSING


def _required_string(value: Mapping[str, Any], name: str, label: str) -> str:
    result = _exact_property(value, name)
    if result is _MISSING or not isinstance(result, str) or not result:
        _fail(f"{label}.{name} must be a non-empty string.")
    if "\0" in result:
        _fail(f"{label}.{name} may not contain NUL.")
    return result


def _required_integer(value: Mapping[str, Any], name: str, label: str) -> int:
    result = _exact_property(value, name)
    if isinstance(result, bool) or not isinstance(result, int):
        _fail(f"{label}.{name} must be an integer.")
    if result < 1 or result > MAX_RECEIPT_GENERATION:
        _fail(
            f"{label}.{name} must be a positive signed 64-bit integer."
        )
    return result


def _parse_rfc3339_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or RFC3339_UTC.fullmatch(value) is None:
        _fail(f"{label} must be an RFC3339 UTC timestamp with second precision.")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        _fail(f"{label} is not a valid RFC3339 UTC timestamp: {error}")


def _coerce_current_time(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        return _parse_rfc3339_utc(value, "current time")
    if not isinstance(value, datetime):
        _fail("current_time must be a datetime or RFC3339 UTC string.")
    if value.tzinfo is None:
        _fail("current_time must include a UTC offset.")
    return value.astimezone(timezone.utc)


def _maintenance_candidates(
    profile: Path,
    plugin_root: Path | None,
) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = [
        ("user", profile / ".copilot-extensions" / "maintenance"),
    ]
    if plugin_root is not None:
        candidates.append(("plugin", plugin_root / "maintenance"))
    return candidates


def _optional_integer(value: Mapping[str, Any], name: str, label: str) -> int | None:
    result = _exact_property(value, name)
    if result is _MISSING:
        return None
    if isinstance(result, bool) or not isinstance(result, int):
        _fail(f"{label}.{name} must be an integer.")
    return result


def _optional_text(
    value: Mapping[str, Any],
    name: str,
    label: str,
) -> str | None:
    result = _exact_property(value, name)
    if result is _MISSING:
        return None
    if not isinstance(result, str) or not result:
        _fail(f"{label}.{name} must be a non-empty string.")
    return result


def _normalize_short_host(value: str) -> str:
    return value.split(".", 1)[0].casefold()


def _current_environment(
    *,
    environment: Mapping[str, str],
    os_profile: str | os.PathLike[str] | None,
    platform: str | None,
    wsl_distro: str | None,
) -> tuple[dict[str, Any], Path]:
    selected_platform = platform or ("windows" if os.name == "nt" else "posix")
    if selected_platform not in {"windows", "posix"}:
        _fail("platform must be exactly 'windows' or 'posix'.")
    if os_profile is not None:
        profile_value = Path(os_profile)
    elif selected_platform == "windows":
        user_profile = environment.get("USERPROFILE")
        if not user_profile:
            _fail("Cannot determine the canonical Windows user profile.")
        profile_value = Path(user_profile)
    else:
        try:
            import pwd

            profile_value = Path(pwd.getpwuid(os.getuid()).pw_dir)
        except (ImportError, KeyError, OSError) as error:
            _fail(f"Cannot determine the passwd-database account home: {error}")
    if not _path_is_fully_qualified(profile_value):
        _fail("The canonical operating-system user profile must be absolute.")
    profile = canonical_path(profile_value, must_exist=True)
    selected_wsl = (
        None
        if selected_platform == "windows"
        else (
            wsl_distro
            if wsl_distro is not None
            else environment.get("WSL_DISTRO_NAME") or None
        )
    )
    if selected_wsl is not None and not isinstance(selected_wsl, str):
        _fail("wslDistro must be a string or null.")
    return (
        {
            "platform": selected_platform,
            "homeRealPath": str(profile),
            "wslDistro": selected_wsl,
        },
        profile,
    )


def _validate_environment_record(
    value: Any,
    current: Mapping[str, Any],
    label: str,
) -> tuple[dict[str, Any], bool]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be a JSON object.")
    platform = _required_string(value, "platform", label)
    if platform not in {"windows", "posix"}:
        _fail(f"{label}.platform must be exactly 'windows' or 'posix'.")
    home = _required_string(value, "homeRealPath", label)
    if platform == "windows":
        absolute = bool(
            re.match(r"^[A-Za-z]:[\\/]", home)
            or re.match(r"^\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$)", home)
        )
    else:
        absolute = PurePosixPath(home).is_absolute()
    if not absolute:
        _fail(f"{label}.homeRealPath must be absolute.")
    distro_value = _exact_property(value, "wslDistro")
    if distro_value is _MISSING:
        _fail(f"{label}.wslDistro is required.")
    if distro_value is not None and not isinstance(distro_value, str):
        _fail(f"{label}.wslDistro must be a string or null.")
    if platform == "windows" and distro_value is not None:
        _fail(f"{label}.wslDistro must be null on Windows.")
    normalized = {
        "platform": platform,
        "homeRealPath": home,
        "wslDistro": distro_value,
    }
    return normalized, normalized != dict(current)


def _validate_legacy_probe(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be a JSON object.")
    declared = _exact_property(value, "declared")
    if not isinstance(declared, bool):
        _fail(f"{label}.declared must be a JSON boolean.")
    result = _exact_property(value, "result")
    if result not in {"absent", "present", "unknown"}:
        _fail(f"{label}.result must be absent, present, or unknown.")
    if not declared and result != "unknown":
        _fail(f"{label}.result must be unknown when declared is false.")
    checked_at = _exact_property(value, "checkedAt")
    if checked_at is _MISSING:
        _fail(f"{label}.checkedAt is required.")
    if checked_at is not None:
        _parse_rfc3339_utc(checked_at, f"{label}.checkedAt")
    return {
        "declared": declared,
        "result": result,
        "checkedAt": checked_at,
    }


def _validate_marketplace_id(value: str) -> None:
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*--[0-9a-f]{16}", value) is None:
        _fail(f"Invalid source-derived marketplace id '{value}'.")


def _policy_result(
    path: Path,
    *,
    authoritative: bool,
    marketplace_id: str | None,
    plugin_id: str | None,
    entry_present: bool | None = None,
    entry_is_file: bool | None = None,
) -> tuple[dict[str, Any], bool | None]:
    evidence: dict[str, Any] = {
        "path": str(path),
        "authoritative": authoritative,
        "state": "missing",
        "scope": "default",
        "enabled": False,
        "reason": "policy-default-false",
    }
    present = os.path.lexists(path) if entry_present is None else entry_present
    is_file = path.is_file() if entry_is_file is None else entry_is_file
    if not present:
        if not authoritative:
            evidence["reason"] = "policy-injected-non-authoritative"
        return evidence, False
    if not is_file:
        evidence.update(
            state="invalid",
            enabled=None,
            reason="policy-invalid",
        )
        return evidence, None
    try:
        policy = read_json(path)
        if not isinstance(policy, Mapping):
            _fail("Installation-mode policy must be a JSON object.")
        schema = _exact_property(policy, "schema")
        version = _exact_property(policy, "version")
        if schema != POLICY_SCHEMA:
            _fail(f"Installation-mode policy schema must be '{POLICY_SCHEMA}'.")
        if isinstance(version, bool) or not isinstance(version, int):
            _fail("Installation-mode policy version must be an integer.")
        if version > 1:
            evidence.update(
                state="unsupported",
                enabled=None,
                reason="policy-version-unsupported",
            )
            return evidence, None
        if version != 1:
            _fail("Installation-mode policy version must be 1.")
        installation_mode = _exact_property(policy, "installationMode")
        if installation_mode is _MISSING:
            installation_mode = {}
        if not isinstance(installation_mode, Mapping):
            _fail("installationMode must be a JSON object.")

        global_enabled = _exact_property(installation_mode, "enabled")
        if global_enabled is not _MISSING and not isinstance(global_enabled, bool):
            _fail("installationMode.enabled must be a JSON boolean.")
        marketplaces = _exact_property(installation_mode, "marketplaces")
        if marketplaces is _MISSING:
            marketplaces = {}
        if not isinstance(marketplaces, Mapping):
            _fail("installationMode.marketplaces must be a JSON object.")

        for candidate_marketplace, marketplace_value in marketplaces.items():
            _validate_marketplace_id(candidate_marketplace)
            if not isinstance(marketplace_value, Mapping):
                _fail(
                    f"Marketplace policy '{candidate_marketplace}' must be a JSON object."
                )
            marketplace_enabled = _exact_property(marketplace_value, "enabled")
            if marketplace_enabled is not _MISSING and not isinstance(
                marketplace_enabled, bool
            ):
                _fail(
                    f"Marketplace policy '{candidate_marketplace}'.enabled must be "
                    "a JSON boolean."
                )
            plugins = _exact_property(marketplace_value, "plugins")
            if plugins is _MISSING:
                plugins = {}
            if not isinstance(plugins, Mapping):
                _fail(
                    f"Marketplace policy '{candidate_marketplace}'.plugins must be "
                    "a JSON object."
                )
            for candidate_plugin, plugin_value in plugins.items():
                _assert_plugin_id(candidate_plugin)
                if not isinstance(plugin_value, Mapping):
                    _fail(
                        f"Plugin policy '{candidate_plugin}' must be a JSON object."
                    )
                plugin_enabled = _exact_property(plugin_value, "enabled")
                if plugin_enabled is not _MISSING and not isinstance(
                    plugin_enabled, bool
                ):
                    _fail(
                        f"Plugin policy '{candidate_plugin}'.enabled must be a "
                        "JSON boolean."
                    )

        scope = "default"
        enabled = False
        reason = "policy-default-false"
        if global_enabled is not _MISSING:
            scope = "global"
            enabled = global_enabled
            reason = f"policy-global-{'true' if enabled else 'false'}"
        if marketplace_id is not None and marketplace_id in marketplaces:
            selected_marketplace = marketplaces[marketplace_id]
            selected_marketplace_enabled = _exact_property(
                selected_marketplace, "enabled"
            )
            if selected_marketplace_enabled is not _MISSING:
                scope = "marketplace"
                enabled = selected_marketplace_enabled
                reason = f"policy-marketplace-{'true' if enabled else 'false'}"
            selected_plugins = _exact_property(selected_marketplace, "plugins")
            if selected_plugins is _MISSING:
                selected_plugins = {}
            if plugin_id is not None and plugin_id in selected_plugins:
                selected_plugin_enabled = _exact_property(
                    selected_plugins[plugin_id], "enabled"
                )
                if selected_plugin_enabled is not _MISSING:
                    scope = "plugin"
                    enabled = selected_plugin_enabled
                    reason = f"policy-plugin-{'true' if enabled else 'false'}"

        evidence.update(
            state="valid",
            scope=scope,
            enabled=enabled,
            reason=(
                reason if authoritative else "policy-injected-non-authoritative"
            ),
        )
        return evidence, enabled if authoritative else False
    except InstallationContextError:
        evidence.update(
            state="invalid",
            enabled=None,
            reason="policy-invalid",
        )
        return evidence, None
