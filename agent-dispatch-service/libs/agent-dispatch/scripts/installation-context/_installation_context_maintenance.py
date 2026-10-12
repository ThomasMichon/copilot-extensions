# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

def _maintenance_inspection(
    *,
    profile: Path,
    plugin_root: Path | None,
    current_time: datetime,
    host: str,
    pid_is_live: Callable[[int], bool],
) -> dict[str, Any]:
    inactive = {
        "state": "inactive",
        "scope": "none",
        "marker": None,
        "sidecar": None,
        "reason": None,
        "owner": None,
        "host": None,
        "pid": None,
        "enteredAt": None,
        "expectedUntil": None,
        "authorization": {
            "state": "unneeded",
            "marketplaceId": None,
            "pluginId": None,
            "context": None,
            "namespaceGeneration": None,
            "installGeneration": None,
            "activationGeneration": None,
            "tokenPresent": False,
        },
    }
    for scope, marker_entry in _maintenance_candidates(profile, plugin_root):
        if not os.path.lexists(marker_entry):
            continue
        marker = canonical_path(marker_entry)
        sidecar_entry = marker_entry.with_name(f"{marker_entry.name}.json")
        result: dict[str, Any] = {
            "state": "stale",
            "scope": scope,
            "marker": str(marker),
            "sidecar": str(canonical_path(sidecar_entry)),
            "reason": "sidecar-missing",
            "owner": None,
            "host": None,
            "pid": None,
            "enteredAt": None,
            "expectedUntil": None,
            "authorization": {
                "state": "unavailable",
                "marketplaceId": None,
                "pluginId": None,
                "context": None,
                "namespaceGeneration": None,
                "installGeneration": None,
                "activationGeneration": None,
                "tokenPresent": False,
            },
        }
        if marker_entry.is_symlink():
            result["reason"] = "marker-linked"
            return result
        if sidecar_entry.is_symlink() or not sidecar_entry.is_file():
            return result
        try:
            value = read_json(sidecar_entry)
            if not isinstance(value, Mapping):
                _fail("Maintenance sidecar must be a JSON object.")
            owner = _required_string(value, "owner", "maintenance")
            sidecar_host = _required_string(value, "host", "maintenance")
            pid = _required_integer(value, "pid", "maintenance")
            _required_string(value, "reason", "maintenance")
            entered_at = _parse_rfc3339_utc(
                _exact_property(value, "enteredAt"), "maintenance.enteredAt"
            )
            expected_until = _parse_rfc3339_utc(
                _exact_property(value, "expectedUntil"),
                "maintenance.expectedUntil",
            )
            schema = _optional_text(value, "schema", "maintenance")
            if schema is not None and schema != MAINTENANCE_SCHEMA:
                _fail("Maintenance sidecar schema is invalid.")
            version = _optional_integer(value, "version", "maintenance")
            if version is not None and version != 1:
                _fail("Maintenance sidecar version is invalid.")
            token = _optional_text(value, "token", "maintenance")
            authorization = {
                "state": (
                    "ready"
                    if token is not None and len(token) >= 32
                    else "unavailable"
                ),
                "marketplaceId": _optional_text(value, "marketplaceId", "maintenance"),
                "pluginId": _optional_text(value, "pluginId", "maintenance"),
                "context": _optional_text(value, "context", "maintenance"),
                "namespaceGeneration": _optional_integer(value, "namespaceGeneration", "maintenance"),
                "installGeneration": _optional_integer(value, "installGeneration", "maintenance"),
                "activationGeneration": _optional_integer(value, "activationGeneration", "maintenance"),
                "tokenPresent": token is not None,
            }
            result.update(
                owner=owner,
                host=sidecar_host,
                pid=pid,
                enteredAt=entered_at.isoformat().replace("+00:00", "Z"),
                expectedUntil=expected_until.isoformat().replace("+00:00", "Z"),
                authorization=authorization,
            )
            if _normalize_short_host(sidecar_host) != _normalize_short_host(host):
                result["reason"] = "foreign-host"
            elif entered_at > current_time:
                result["reason"] = "not-yet-active"
            elif current_time > expected_until:
                result["reason"] = "expired"
            elif not pid_is_live(pid):
                result["reason"] = "owner-dead"
            else:
                result["state"] = "active"
                result["reason"] = "maintenance-active"
            return result
        except (InstallationContextError, OSError):
            result["reason"] = "sidecar-invalid"
            return result
    return inactive


def _maintenance_result(
    *,
    profile: Path,
    plugin_root: Path | None,
    current_time: datetime,
    host: str,
    pid_is_live: Callable[[int], bool],
) -> dict[str, Any]:
    maintenance = _maintenance_inspection(
        profile=profile,
        plugin_root=plugin_root,
        current_time=current_time,
        host=host,
        pid_is_live=pid_is_live,
    )
    return {
        "state": maintenance["state"],
        "scope": maintenance["scope"],
        "marker": maintenance["marker"],
        "sidecar": maintenance["sidecar"],
    }


def _require_management_authorization(
    *,
    profile: Path,
    plugin_root: Path | None,
    maintenance_token: str | None,
    current_time: datetime,
    host: str,
    pid_is_live: Callable[[int], bool],
) -> dict[str, Any]:
    maintenance = _maintenance_inspection(
        profile=profile,
        plugin_root=plugin_root,
        current_time=current_time,
        host=host,
        pid_is_live=pid_is_live,
    )
    if maintenance["state"] == "inactive":
        return maintenance
    if maintenance["state"] != "active":
        _fail(
            "Applicable maintenance is stale or invalid "
            f"(scope={maintenance['scope']} reason={maintenance['reason']})."
        )
    if not maintenance_token:
        _fail(
            "Applicable maintenance requires an explicit matching "
            "--maintenance-token."
        )
    if maintenance["authorization"]["state"] != "ready":
        _fail(
            "Applicable maintenance sidecar cannot authorize management commands; "
            "explicit repair is required."
        )
    sidecar_value = read_json(Path(maintenance["sidecar"]))
    if not isinstance(sidecar_value, Mapping):
        _fail("Maintenance sidecar must be a JSON object.")
    if _required_string(sidecar_value, "token", "maintenance") != maintenance_token:
        _fail("Maintenance token does not match the applicable maintenance owner.")
    return maintenance


@_validation_scope
def enter_maintenance(
    *,
    scope: str,
    owner: str,
    reason: str,
    expected_duration_seconds: int,
    durable_home: str | os.PathLike[str] | None = None,
    context: str | os.PathLike[str] | None = None,
    expected_marketplace_id: str | None = None,
    expected_plugin_id: str | None = None,
    environment: Mapping[str, str] | None = None,
    os_profile: str | os.PathLike[str] | None = None,
    platform: str | None = None,
    wsl_distro: str | None = None,
) -> dict[str, Any]:
    if scope not in {"user", "plugin"}:
        _fail("Maintenance scope must be 'user' or 'plugin'.")
    if not owner.strip():
        _fail("Maintenance owner must be a non-empty string.")
    if not reason.strip():
        _fail("Maintenance reason must be a non-empty string.")
    if expected_duration_seconds < 1:
        _fail("Maintenance duration must be at least one second.")
    environment = environment if environment is not None else os.environ
    current_environment, profile = _current_environment(
        environment=environment,
        os_profile=os_profile,
        platform=platform,
        wsl_distro=wsl_distro,
    )
    plugin_root: Path | None = None
    metadata: dict[str, Any] = {}
    resolved_durable_home = canonical_path(durable_home or profile / ".copilot-extensions")
    if scope == "plugin":
        if context is None or expected_marketplace_id is None or expected_plugin_id is None:
            _fail(
                "Plugin-scoped maintenance requires --context, "
                "--expected-marketplace-id, and --expected-plugin-id."
            )
        validated = validate_context_receipt(
            context,
            resolved_durable_home,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            environment={},
        )
        plugin_root = canonical_path(validated["pluginRoot"])
        metadata.update(
            marketplaceId=_string_property(validated, "marketplaceId"),
            pluginId=_string_property(validated, "pluginId"),
            context=str(canonical_path(validated["installReceipt"])),
            namespaceGeneration=int(validated["namespaceGeneration"]),
            installGeneration=int(validated["generation"]),
        )
        activation = _activation_result(
            plugin_root=plugin_root,
            durable_home=resolved_durable_home,
            marketplace_id=metadata["marketplaceId"],
            plugin_id=metadata["pluginId"],
            current_environment=current_environment,
            legacy_root=canonical_path(
                Path(current_environment["homeRealPath"]) / f".{metadata['pluginId']}"
            ),
        )
        if activation["state"] in {"valid", "revalidation"}:
            metadata["activationGeneration"] = int(activation["activationGeneration"])
    marker_entry = (
        profile / ".copilot-extensions" / "maintenance"
        if scope == "user"
        else plugin_root / "maintenance"
    )
    marker_entry.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(marker_entry):
        existing = _maintenance_inspection(
            profile=profile,
            plugin_root=plugin_root if scope == "plugin" else None,
            current_time=datetime.now(timezone.utc),
            host=socket.gethostname(),
            pid_is_live=_pid_is_live,
        )
        _fail(
            "Applicable maintenance already exists "
            f"(scope={existing['scope']} reason={existing['reason']})."
        )
    descriptor = os.open(
        marker_entry,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    os.close(descriptor)
    token = secrets.token_hex(24)
    entered_at = datetime.now(timezone.utc).replace(microsecond=0)
    sidecar = {
        "schema": MAINTENANCE_SCHEMA,
        "version": 1,
        "owner": owner,
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "reason": reason,
        "enteredAt": entered_at.isoformat().replace("+00:00", "Z"),
        "expectedUntil": datetime.fromtimestamp(
            entered_at.timestamp() + expected_duration_seconds,
            timezone.utc,
        ).isoformat().replace("+00:00", "Z"),
        "token": token,
        **metadata,
    }
    sidecar_entry = marker_entry.with_name(f"{marker_entry.name}.json")
    try:
        _atomic_write_json(sidecar_entry, sidecar)
    except BaseException:
        marker_entry.unlink(missing_ok=True)
        raise
    return {
        "action": "maintenance-enter",
        "status": "ready",
        "scope": scope,
        "marker": str(canonical_path(marker_entry)),
        "sidecar": str(canonical_path(sidecar_entry)),
        "token": token,
    }


@_validation_scope
def release_maintenance(
    *,
    scope: str,
    maintenance_token: str,
    durable_home: str | os.PathLike[str] | None = None,
    context: str | os.PathLike[str] | None = None,
    expected_marketplace_id: str | None = None,
    expected_plugin_id: str | None = None,
    environment: Mapping[str, str] | None = None,
    os_profile: str | os.PathLike[str] | None = None,
    platform: str | None = None,
    wsl_distro: str | None = None,
) -> dict[str, Any]:
    if scope not in {"user", "plugin"}:
        _fail("Maintenance scope must be 'user' or 'plugin'.")
    if not maintenance_token:
        _fail("Maintenance release requires --maintenance-token.")
    environment = environment if environment is not None else os.environ
    _current, profile = _current_environment(
        environment=environment,
        os_profile=os_profile,
        platform=platform,
        wsl_distro=wsl_distro,
    )
    plugin_root: Path | None = None
    resolved_durable_home = canonical_path(durable_home or profile / ".copilot-extensions")
    if scope == "plugin":
        if context is None or expected_marketplace_id is None or expected_plugin_id is None:
            _fail(
                "Plugin-scoped maintenance release requires --context, "
                "--expected-marketplace-id, and --expected-plugin-id."
            )
        validated = validate_context_receipt(
            context,
            resolved_durable_home,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            environment={},
        )
        plugin_root = canonical_path(validated["pluginRoot"])
    marker_entry = (
        profile / ".copilot-extensions" / "maintenance"
        if scope == "user"
        else plugin_root / "maintenance"
    )
    if not os.path.lexists(marker_entry):
        return {
            "action": "maintenance-release",
            "status": "ready",
            "scope": scope,
            "released": False,
            "reason": "maintenance-absent",
        }
    maintenance = _require_management_authorization(
        profile=profile,
        plugin_root=plugin_root if scope == "plugin" else None,
        maintenance_token=maintenance_token,
        current_time=datetime.now(timezone.utc),
        host=socket.gethostname(),
        pid_is_live=_pid_is_live,
    )
    sidecar_entry = marker_entry.with_name(f"{marker_entry.name}.json")
    sidecar_entry.unlink()
    marker_entry.unlink()
    return {
        "action": "maintenance-release",
        "status": "ready",
        "scope": scope,
        "released": True,
        "reason": "maintenance-released",
        "marker": maintenance["marker"],
    }


def require_management_authorization(
    *,
    profile: Path,
    plugin_root: Path | None,
    maintenance_token: str | None,
    current_time: datetime,
    host: str,
    pid_is_live: Callable[[int], bool],
) -> dict[str, Any]:
    return _require_management_authorization(
        profile=profile,
        plugin_root=plugin_root,
        maintenance_token=maintenance_token,
        current_time=current_time,
        host=host,
        pid_is_live=pid_is_live,
    )


def probe_remote_maintenance(
    command: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    unknown = {
        "state": "unknown",
        "scope": "remote",
        "marker": None,
        "sidecar": None,
        "reason": "maintenance-unknown",
    }
    try:
        result = subprocess.run(
            list(command),
            cwd=None if cwd is None else os.fspath(cwd),
            env=None if environment is None else dict(environment),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return unknown
    if result.returncode != 0:
        return unknown
    try:
        payload = json.loads(result.stdout, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError):
        return unknown
    if not isinstance(payload, Mapping):
        return unknown
    state = _exact_property(payload, "state")
    scope = _exact_property(payload, "scope")
    marker = _exact_property(payload, "marker")
    sidecar = _exact_property(payload, "sidecar")
    if state not in {"inactive", "active", "stale"}:
        return unknown
    if scope not in {"none", "user", "plugin"}:
        return unknown
    if marker is not None and not isinstance(marker, str):
        return unknown
    if sidecar is not None and not isinstance(sidecar, str):
        return unknown
    return {
        "state": state,
        "scope": scope,
        "marker": marker,
        "sidecar": sidecar,
        "reason": _exact_property(payload, "reason")
        if "reason" in payload
        else None,
    }


def _loop_baseline(
    *,
    context: str,
    marketplace_id: str,
    plugin_id: str,
    actual_mode: str | None,
    namespace_generation: int | None,
    install_generation: int | None,
    activation_generation: int | None,
    legacy: Mapping[str, Any],
) -> dict[str, Any]:
    tombstone_path = legacy.get("tombstone")
    tombstone_digest: str | None = None
    if isinstance(tombstone_path, str) and tombstone_path:
        try:
            canonical_tombstone = canonical_path(tombstone_path, must_exist=True)
            content, validated_stat = _read_regular_file(
                canonical_tombstone,
                label="legacy ownership tombstone",
                require_stable_identity=True,
            )
            tombstone_digest = _cache_validated_file_digest(
                canonical_tombstone,
                content,
                validated_stat,
            )
            tombstone_path = str(canonical_tombstone)
        except InstallationContextError:
            tombstone_digest = None
    else:
        tombstone_path = None
    return {
        "schema": LOOP_BASELINE_SCHEMA,
        "version": 1,
        "context": context,
        "marketplaceId": marketplace_id,
        "pluginId": plugin_id,
        "actualMode": actual_mode,
        "namespaceGeneration": namespace_generation,
        "installGeneration": install_generation,
        "activationGeneration": activation_generation,
        "legacy": {
            "tombstone": tombstone_path,
            "disposition": legacy.get("disposition"),
            "ownerMarketplaceId": legacy.get("ownerMarketplaceId"),
            "digest": tombstone_digest,
        },
    }


def _validated_loop_baseline(
    baseline: Mapping[str, Any] | None,
    *,
    context: str,
    marketplace_id: str,
    plugin_id: str,
) -> Mapping[str, Any] | None:
    if baseline is None:
        return None
    if not isinstance(baseline, Mapping):
        _fail("Loop baseline must be a JSON object.")
    if _required_string(baseline, "schema", "loop baseline") != LOOP_BASELINE_SCHEMA:
        _fail(f"Loop baseline schema must be '{LOOP_BASELINE_SCHEMA}'.")
    version = _required_integer(baseline, "version", "loop baseline")
    if version != 1:
        _fail("Loop baseline version must be 1.")
    if _required_string(baseline, "context", "loop baseline") != context:
        _fail("Loop baseline context does not match the requested installation.")
    if (
        _required_string(baseline, "marketplaceId", "loop baseline")
        != marketplace_id
    ):
        _fail("Loop baseline marketplaceId does not match the requested installation.")
    if _required_string(baseline, "pluginId", "loop baseline") != plugin_id:
        _fail("Loop baseline pluginId does not match the requested installation.")
    actual_mode = _property(baseline, "actualMode")
    if actual_mode is not None and actual_mode not in {"legacy", "namespaced"}:
        _fail("Loop baseline actualMode must be 'legacy', 'namespaced', or null.")
    for field in (
        "namespaceGeneration",
        "installGeneration",
        "activationGeneration",
    ):
        value = _property(baseline, field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            _fail(f"Loop baseline {field} must be a non-negative integer or null.")
    legacy = _property(baseline, "legacy")
    if not isinstance(legacy, Mapping):
        _fail("Loop baseline legacy must be a JSON object.")
    for field in ("tombstone", "disposition", "ownerMarketplaceId", "digest"):
        value = _property(legacy, field)
        if value is not None and not isinstance(value, str):
            _fail(f"Loop baseline legacy.{field} must be a string or null.")
    digest = _property(legacy, "digest")
    if digest is not None and not LOWER_SHA256.fullmatch(digest):
        _fail("Loop baseline legacy.digest must be a lowercase SHA-256 hex digest.")
    return baseline


@_validation_scope
def recheck_loop_governance(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    legacy_root: str | os.PathLike[str],
    durable_home: str | os.PathLike[str] | None = None,
    baseline: Mapping[str, Any] | None = None,
    environment: Mapping[str, str] | None = None,
    os_profile: str | os.PathLike[str] | None = None,
    platform: str | None = None,
    wsl_distro: str | None = None,
    current_time: datetime | str | None = None,
    host: str | None = None,
    pid_is_live: Callable[[int], bool] | None = None,
) -> dict[str, Any]:
    """Recheck loop mutation governance for one installation context."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    environment = environment if environment is not None else os.environ
    current_environment, profile = _current_environment(
        environment=environment,
        os_profile=os_profile,
        platform=platform,
        wsl_distro=wsl_distro,
    )
    now = _coerce_current_time(current_time)
    current_host = host or socket.gethostname()
    liveness = pid_is_live or _pid_is_live
    durable = canonical_path(durable_home or profile / ".copilot-extensions")
    validated = validate_context_receipt(
        context,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    marketplace_id = _string_property(validated, "marketplaceId")
    plugin_id = _string_property(validated, "pluginId")
    context_path = str(canonical_path(validated["installReceipt"]))
    resolved = resolve_installation_mode(
        legacy_root=legacy_root,
        durable_home=durable,
        context=context_path,
        expected_marketplace_id=marketplace_id,
        expected_plugin_id=plugin_id,
        environment=environment,
        os_profile=profile,
        platform=platform,
        wsl_distro=wsl_distro,
        current_time=now,
        host=current_host,
        pid_is_live=liveness,
    )
    baseline_now = _loop_baseline(
        context=context_path,
        marketplace_id=marketplace_id,
        plugin_id=plugin_id,
        actual_mode=(
            str(resolved["actualMode"])
            if isinstance(resolved.get("actualMode"), str)
            else None
        ),
        namespace_generation=int(validated["namespaceGeneration"]),
        install_generation=int(validated["generation"]),
        activation_generation=(
            int(resolved["activationGeneration"])
            if isinstance(resolved.get("activationGeneration"), int)
            else None
        ),
        legacy=resolved["legacy"],
    )
    expected_baseline = _validated_loop_baseline(
        baseline,
        context=context_path,
        marketplace_id=marketplace_id,
        plugin_id=plugin_id,
    )
    result = {
        "action": "loop-recheck",
        "marketplaceId": marketplace_id,
        "pluginId": plugin_id,
        "context": context_path,
        "actualMode": resolved["actualMode"],
        "status": "ready",
        "reason": "current" if expected_baseline is not None else "baseline-established",
        "maintenance": resolved["maintenance"],
        "activation": resolved["activation"],
        "activationGeneration": baseline_now["activationGeneration"],
        "namespaceGeneration": baseline_now["namespaceGeneration"],
        "installGeneration": baseline_now["installGeneration"],
        "legacy": {
            "root": resolved["legacy"]["root"],
            "tombstone": baseline_now["legacy"]["tombstone"],
            "disposition": baseline_now["legacy"]["disposition"],
            "ownerMarketplaceId": baseline_now["legacy"]["ownerMarketplaceId"],
            "digest": baseline_now["legacy"]["digest"],
        },
        "baseline": baseline_now,
    }
    if resolved["status"] == "maintenance-blocked":
        result["status"] = "backoff"
        result["reason"] = str(resolved["reason"])
        return result
    if expected_baseline is None:
        if resolved["status"] == "revalidation-required":
            result["status"] = "revalidation-required"
            result["reason"] = "generation-changed"
            return result
        if resolved["status"] != "ready":
            result["status"] = "backoff"
            result["reason"] = str(resolved["reason"])
            return result
        return result
    if (
        baseline_now["namespaceGeneration"] != expected_baseline.get("namespaceGeneration")
        or baseline_now["installGeneration"] != expected_baseline.get("installGeneration")
        or baseline_now["activationGeneration"] != expected_baseline.get("activationGeneration")
    ):
        result["status"] = "revalidation-required"
        result["reason"] = "generation-changed"
        return result
    if result["actualMode"] != expected_baseline.get("actualMode"):
        result["status"] = "revalidation-required"
        result["reason"] = "mode-changed"
        return result
    expected_legacy = _property(expected_baseline, "legacy")
    if not isinstance(expected_legacy, Mapping):
        _fail("Loop baseline legacy must be a JSON object.")
    if (
        baseline_now["legacy"]["tombstone"] != _property(expected_legacy, "tombstone")
        or baseline_now["legacy"]["disposition"] != _property(expected_legacy, "disposition")
        or baseline_now["legacy"]["ownerMarketplaceId"]
        != _property(expected_legacy, "ownerMarketplaceId")
        or baseline_now["legacy"]["digest"] != _property(expected_legacy, "digest")
    ):
        result["status"] = "revalidation-required"
        result["reason"] = "tombstone-changed"
        return result
    if resolved["status"] == "revalidation-required":
        result["status"] = "revalidation-required"
        result["reason"] = "generation-changed"
        return result
    if resolved["status"] != "ready":
        result["status"] = "backoff"
        result["reason"] = str(resolved["reason"])
    return result
