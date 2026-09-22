# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
def _trusted_plugin_id(
    *,
    plugin_id: str | None,
    expected_plugin_id: str | None,
    payload_root: str | os.PathLike[str] | None,
    context: str | os.PathLike[str] | None,
) -> str | None:
    candidates: list[str] = []
    if plugin_id:
        candidates.append(plugin_id)
    if expected_plugin_id:
        candidates.append(expected_plugin_id)
    if payload_root:
        candidates.append(Path(payload_root).name)
    if context:
        pointer = Path(context)
        if pointer.name == "install.json" and pointer.parent.parent.name == "plugins":
            candidates.append(pointer.parent.name)
    for candidate in candidates:
        try:
            _assert_plugin_id(candidate)
            return candidate
        except InstallationContextError:
            continue
    return None


@_validation_scope
def resolve_installation_mode(
    *,
    legacy_root: str | os.PathLike[str],
    legacy_probe: Mapping[str, Any] | None = None,
    policy_path: str | os.PathLike[str] | None = None,
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
    os_profile: str | os.PathLike[str] | None = None,
    platform: str | None = None,
    wsl_distro: str | None = None,
    current_time: datetime | str | None = None,
    host: str | None = None,
    pid_is_live: Callable[[int], bool] | None = None,
) -> dict[str, Any]:
    """Resolve desired and actual installation mode without mutating state."""

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
    legacy_value = Path(legacy_root)
    if not _path_is_fully_qualified(legacy_value):
        _fail("legacy_root must be absolute.")
    resolved_legacy_root = canonical_path(legacy_value)
    probe = _validate_legacy_probe(
        legacy_probe
        if legacy_probe is not None
        else {"declared": False, "result": "unknown", "checkedAt": None},
        "legacy probe",
    )
    resolved_durable_home = canonical_path(
        durable_home or profile / ".copilot-extensions"
    )
    trusted_plugin_id = _trusted_plugin_id(
        plugin_id=plugin_id,
        expected_plugin_id=expected_plugin_id,
        payload_root=payload_root,
        context=context,
    )
    resolved_context: dict[str, Any] | None = None
    identity_reason: str | None = None
    try:
        resolved_context = resolve_context(
            payload_root=payload_root,
            plugin_id=plugin_id,
            copilot_home=copilot_home,
            project_root=project_root,
            durable_home=resolved_durable_home,
            context=context,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            expected_payload_root=expected_payload_root,
            expected_cell_root=expected_cell_root,
            source_descriptor=source_descriptor,
            marketplace_key=marketplace_key,
            environment=environment,
        )
        trusted_plugin_id = str(resolved_context["pluginId"])
    except InstallationContextError:
        identity_reason = (
            "context-invalid"
            if context or environment.get("COPILOT_EXTENSIONS_CONTEXT")
            else "provenance-blocked"
        )

    marketplace_id = (
        str(resolved_context["marketplaceId"]) if resolved_context is not None else None
    )
    plugin_root = (
        canonical_path(resolved_context["pluginRoot"])
        if resolved_context is not None
        else None
    )
    canonical_policy_entry = (
        profile / ".copilot-extensions" / "installation-mode.json"
    )
    canonical_policy = canonical_path(canonical_policy_entry)
    if policy_path is not None:
        policy_value = Path(policy_path)
        if not _path_is_fully_qualified(policy_value):
            _fail("policy_path must be absolute.")
        policy_entry_present = os.path.lexists(policy_value)
        policy_entry_is_file = policy_value.is_file() and not policy_value.is_symlink()
        selected_policy = canonical_path(policy_value)
        policy_authoritative = False
    else:
        policy_entry_present = os.path.lexists(canonical_policy_entry)
        policy_entry_is_file = (
            canonical_policy_entry.is_file()
            and not canonical_policy_entry.is_symlink()
        )
        selected_policy = canonical_policy
        policy_authoritative = True
    policy, policy_enabled = _policy_result(
        selected_policy,
        authoritative=policy_authoritative,
        marketplace_id=marketplace_id,
        plugin_id=trusted_plugin_id,
        entry_present=policy_entry_present,
        entry_is_file=policy_entry_is_file,
    )

    activation = (
        _activation_result(
            plugin_root=plugin_root,
            durable_home=resolved_durable_home,
            marketplace_id=marketplace_id,
            plugin_id=trusted_plugin_id,
            current_environment=current_environment,
            legacy_root=resolved_legacy_root,
        )
        if plugin_root is not None
        and marketplace_id is not None
        and trusted_plugin_id is not None
        else {
            "state": "missing",
            "path": None,
            "actualMode": None,
            "runtimeRoot": None,
            "context": None,
            "activationGeneration": None,
            "installGeneration": None,
            "reason": None,
        }
    )
    legacy = _tombstone_result(
        legacy_root=resolved_legacy_root,
        durable_home=resolved_durable_home,
        plugin_id=trusted_plugin_id,
        current_marketplace_id=marketplace_id,
        current_environment=current_environment,
    )
    legacy["probe"] = probe
    maintenance = _maintenance_result(
        profile=profile,
        plugin_root=plugin_root,
        current_time=now,
        host=current_host,
        pid_is_live=liveness,
    )

    desired_mode: str | None
    if marketplace_id is None or policy_enabled is None:
        desired_mode = None
    else:
        desired_mode = "namespaced" if policy_enabled else "legacy"
    actual_mode = activation["actualMode"]
    runtime_root = activation["runtimeRoot"]
    if (
        not policy_authoritative
        and activation["state"] in {"valid", "revalidation"}
        and activation["actualMode"] == "namespaced"
    ):
        desired_mode = "namespaced"

    invalid_reason: str | None = None
    if policy["state"] in {"invalid", "unsupported"}:
        invalid_reason = str(policy["reason"])
    elif identity_reason == "context-invalid":
        invalid_reason = identity_reason
    elif activation["state"] == "invalid":
        invalid_reason = str(activation["reason"])

    status = "ready"
    reason = str(policy["reason"])
    if invalid_reason is not None:
        status = "invalid"
        reason = invalid_reason
    elif maintenance["state"] in {"active", "stale"}:
        status = "maintenance-blocked"
        reason = f"maintenance-{maintenance['state']}"
    elif activation["state"] == "foreign" or legacy["status"] == "foreign-environment":
        status = "foreign-environment"
        reason = "foreign-environment"
    elif legacy["status"] == "orphaned-transfer":
        status = "orphaned-transfer"
        reason = "orphaned-transfer"
    elif activation["state"] == "revalidation":
        status = "revalidation-required"
        reason = "revalidation-required"
    elif identity_reason == "provenance-blocked":
        status = "provenance-blocked"
        reason = "provenance-blocked"
        desired_mode = None
        actual_mode = None
        runtime_root = None
    elif desired_mode == "legacy" and actual_mode == "namespaced":
        status = "deactivation-required"
        reason = "deactivation-required"
    elif desired_mode == "namespaced" and actual_mode == "legacy":
        if (
            activation["state"] == "missing"
            and probe["declared"]
            and probe["result"] == "absent"
        ):
            reason = "activation-required"
        else:
            status = "migration-required"
            reason = "migration-required"
    elif actual_mode == "namespaced":
        reason = "namespaced-active"

    return {
        "schema": RESOLUTION_SCHEMA,
        "version": 1,
        "marketplaceId": marketplace_id,
        "pluginId": trusted_plugin_id,
        "environment": current_environment,
        "desiredMode": desired_mode,
        "actualMode": actual_mode,
        "status": status,
        "maintenance": maintenance,
        "runtimeRoot": runtime_root,
        "context": activation["context"],
        "activation": activation["path"],
        "activationGeneration": activation["activationGeneration"],
        "installGeneration": activation["installGeneration"],
        "reason": reason,
        "policy": policy,
        "legacy": {
            "root": legacy["root"],
            "probe": probe,
            "tombstone": legacy["tombstone"],
            "disposition": legacy["disposition"],
            "ownerMarketplaceId": legacy["ownerMarketplaceId"],
        },
    }


@_validation_scope
def probe_legacy_entrypoint(**arguments: Any) -> dict[str, Any]:
    """Resolve installation mode and decide whether legacy mutation is allowed."""

    result = resolve_installation_mode(**arguments)
    allow_mutation = False
    probe_reason = str(result["reason"])
    legacy = result["legacy"]
    if result["status"] == "migration-required":
        allow_mutation = legacy["tombstone"] is None
        probe_reason = (
            "migration-required"
            if allow_mutation
            else "legacy-owned-by-other-cell"
        )
    elif (
        result["status"] == "provenance-blocked"
        and result["policy"]["state"] == "missing"
        and result["policy"]["enabled"] is False
        and result["policy"]["reason"] == "policy-default-false"
        and legacy["tombstone"] is None
        and legacy["disposition"] == "active"
    ):
        allow_mutation = True
        probe_reason = "legacy-active"
    elif result["status"] == "ready" and result["actualMode"] == "legacy":
        if legacy["tombstone"] is not None:
            probe_reason = "legacy-owned-by-other-cell"
        elif result["desiredMode"] == "namespaced":
            probe_reason = "namespaced-requested"
        else:
            allow_mutation = True
            probe_reason = "legacy-active"
    elif result["actualMode"] == "namespaced":
        probe_reason = "namespaced-active"
    decision = dict(result)
    decision["allowMutation"] = allow_mutation
    decision["probeReason"] = probe_reason
    return decision


def _source_descriptor(arguments: argparse.Namespace) -> Mapping[str, Any] | None:
    if arguments.source_json and arguments.source_file:
        _fail("Specify only one of --source-json and --source-file.")
    if arguments.source_file:
        value = read_json(arguments.source_file)
    elif arguments.source_json:
        try:
            value = json.loads(
                arguments.source_json,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except json.JSONDecodeError as error:
            _fail(f"Invalid --source-json: {error}")
    else:
        return None
    if not isinstance(value, Mapping):
        _fail("A source descriptor must be a JSON object.")
    return value


def _legacy_probe_argument(arguments: argparse.Namespace) -> Mapping[str, Any] | None:
    if arguments.legacy_probe_json and arguments.legacy_probe_file:
        _fail("Specify only one of --legacy-probe-json and --legacy-probe-file.")
    if arguments.legacy_probe_file:
        value = read_json(arguments.legacy_probe_file)
    elif arguments.legacy_probe_json:
        try:
            value = json.loads(
                arguments.legacy_probe_json,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except json.JSONDecodeError as error:
            _fail(f"Invalid --legacy-probe-json: {error}")
    else:
        return None
    if not isinstance(value, Mapping):
        _fail("Legacy probe evidence must be a JSON object.")
    return value


def _add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--copilot-home")
    parser.add_argument("--durable-home")
    parser.add_argument("--project-root")


def _add_resolution_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-json")
    parser.add_argument("--source-file")
    parser.add_argument("--marketplace-key")
    parser.add_argument("--plugin-id")
    parser.add_argument("--payload-root")
    parser.add_argument("--context")
    parser.add_argument("--expected-marketplace-id")
    parser.add_argument("--expected-plugin-id")
    parser.add_argument("--expected-payload-root")
    parser.add_argument("--expected-cell-root")
    _add_common_paths(parser)


def _add_mode_arguments(parser: argparse.ArgumentParser) -> None:
    _add_resolution_arguments(parser)
    parser.add_argument("--legacy-root", required=True)
    parser.add_argument("--legacy-probe-json")
    parser.add_argument("--legacy-probe-file")
    parser.add_argument("--policy-path")


def _parse_cli_generation(value: str) -> int:
    if re.fullmatch(r"[0-9]+", value, flags=re.ASCII) is None:
        raise argparse.ArgumentTypeError(
            "generation must be an unsigned ASCII decimal integer"
        )
    parsed = int(value, 10)
    if parsed > MAX_RECEIPT_GENERATION:
        raise argparse.ArgumentTypeError(
            "generation exceeds the portable signed 64-bit maximum"
        )
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    source_parser = subparsers.add_parser("source-id")
    source_parser.add_argument("--source-json")
    source_parser.add_argument("--source-file")
    source_parser.add_argument("--marketplace-key")

    resolve_parser = subparsers.add_parser("resolve")
    _add_resolution_arguments(resolve_parser)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--context")
    validate_parser.add_argument("--payload-root")
    validate_parser.add_argument("--expected-marketplace-id")
    validate_parser.add_argument("--expected-plugin-id")
    validate_parser.add_argument("--expected-payload-root")
    validate_parser.add_argument("--expected-cell-root")
    _add_common_paths(validate_parser)

    stamp_parser = subparsers.add_parser("stamp")
    stamp_parser.add_argument("--source-json")
    stamp_parser.add_argument("--source-file")
    stamp_parser.add_argument("--marketplace-key")
    stamp_parser.add_argument("--plugin-id")
    stamp_parser.add_argument("--payload-root")
    stamp_parser.add_argument("--payload-version", required=True)
    stamp_parser.add_argument(
        "--payload-origin",
        required=True,
        choices=("installed", "directory", "staged", "explicit"),
    )
    stamp_parser.add_argument("--payload-origin-receipt")
    stamp_parser.add_argument(
        "--expected-namespace-generation",
        required=True,
        type=_parse_cli_generation,
    )
    stamp_parser.add_argument(
        "--expected-install-generation",
        required=True,
        type=_parse_cli_generation,
    )
    stamp_parser.add_argument(
        "--namespace-state",
        default="active",
        choices=("active", "inactive", "orphaned", "removing"),
    )
    stamp_parser.add_argument(
        "--install-state",
        default="active",
        choices=("active", "inactive", "orphaned", "removing"),
    )
    _add_common_paths(stamp_parser)

    activation_parser = subparsers.add_parser("activation-cas")
    activation_parser.add_argument("--context", required=True)
    activation_parser.add_argument("--expected-marketplace-id", required=True)
    activation_parser.add_argument("--expected-plugin-id", required=True)
    activation_parser.add_argument(
        "--expected-namespace-generation",
        required=True,
        type=_parse_cli_generation,
    )
    activation_parser.add_argument(
        "--expected-install-generation",
        required=True,
        type=_parse_cli_generation,
    )
    activation_parser.add_argument(
        "--expected-activation-generation",
        required=True,
        type=_parse_cli_generation,
    )
    activation_parser.add_argument(
        "--activation-mode",
        required=True,
        choices=("namespaced", "legacy"),
    )
    activation_parser.add_argument(
        "--activation-state",
        required=True,
        choices=("active", "deactivated"),
    )
    activation_parser.add_argument(
        "--legacy-disposition",
        required=True,
        choices=("absent", "quiesced", "retained-inert", "restored"),
    )
    activation_parser.add_argument("--legacy-probe-json")
    activation_parser.add_argument("--legacy-probe-file")
    activation_parser.add_argument("--legacy-root")
    activation_parser.add_argument("--durable-home")
    activation_parser.add_argument("--maintenance-token")

    snapshot_stamp_parser = subparsers.add_parser("snapshot-stamp")
    snapshot_stamp_parser.add_argument("--context", required=True)
    snapshot_stamp_parser.add_argument("--expected-marketplace-id", required=True)
    snapshot_stamp_parser.add_argument("--expected-plugin-id", required=True)
    snapshot_stamp_parser.add_argument(
        "--expected-namespace-generation",
        required=True,
        type=_parse_cli_generation,
    )
    snapshot_stamp_parser.add_argument(
        "--expected-install-generation",
        required=True,
        type=_parse_cli_generation,
    )
    snapshot_stamp_parser.add_argument("--snapshot-id", required=True)
    snapshot_stamp_parser.add_argument("--durable-home")

    snapshot_validate_parser = subparsers.add_parser("snapshot-validate")
    snapshot_validate_parser.add_argument("--context", required=True)
    snapshot_validate_parser.add_argument("--expected-marketplace-id", required=True)
    snapshot_validate_parser.add_argument("--expected-plugin-id", required=True)
    snapshot_validate_parser.add_argument("--snapshot-id", required=True)
    snapshot_validate_parser.add_argument("--durable-home")

    slot_provision_parser = subparsers.add_parser("slot-provision")
    slot_provision_parser.add_argument("--context", required=True)
    slot_provision_parser.add_argument("--expected-marketplace-id", required=True)
    slot_provision_parser.add_argument("--expected-plugin-id", required=True)
    slot_provision_parser.add_argument("--snapshot-id", required=True)
    slot_provision_parser.add_argument("--runtime-version", required=True)
    slot_provision_parser.add_argument("--expected-payload-root")
    slot_provision_parser.add_argument("--expected-payload-version")
    slot_provision_parser.add_argument("--durable-home")

    slot_validate_parser = subparsers.add_parser("slot-validate")
    slot_validate_parser.add_argument("--context", required=True)
    slot_validate_parser.add_argument("--expected-marketplace-id", required=True)
    slot_validate_parser.add_argument("--expected-plugin-id", required=True)
    slot_validate_parser.add_argument("--snapshot-id", required=True)
    slot_validate_parser.add_argument("--runtime-version", required=True)
    slot_validate_parser.add_argument("--expected-payload-root")
    slot_validate_parser.add_argument("--expected-payload-version")
    slot_validate_parser.add_argument("--durable-home")

    release_parser = subparsers.add_parser("slot-release")
    for name in ("context", "expected-marketplace-id", "expected-plugin-id",
                 "runtime-version", "reservation-root", "expected-reservation-sha256", "durable-home"):
        release_parser.add_argument(f"--{name}", required=True)
    for name in ("expected-reservation-generation", "expected-namespace-generation", "expected-install-generation"):
        release_parser.add_argument(f"--{name}", required=True, type=_parse_cli_generation)

    slot_complete_parser = subparsers.add_parser("slot-complete")
    slot_complete_parser.add_argument("--context", required=True)
    slot_complete_parser.add_argument("--expected-marketplace-id", required=True)
    slot_complete_parser.add_argument("--expected-plugin-id", required=True)
    slot_complete_parser.add_argument("--expected-payload-root", required=True)
    slot_complete_parser.add_argument("--expected-payload-version", required=True)
    slot_complete_parser.add_argument("--snapshot-id", required=True)
    slot_complete_parser.add_argument("--runtime-version", required=True)
    slot_complete_parser.add_argument("--durable-home")

    slot_completion_validate_parser = subparsers.add_parser(
        "slot-completion-validate"
    )
    slot_completion_validate_parser.add_argument("--context", required=True)
    slot_completion_validate_parser.add_argument(
        "--expected-marketplace-id",
        required=True,
    )
    slot_completion_validate_parser.add_argument(
        "--expected-plugin-id",
        required=True,
    )
    slot_completion_validate_parser.add_argument(
        "--expected-payload-root",
        required=True,
    )
    slot_completion_validate_parser.add_argument(
        "--expected-payload-version",
        required=True,
    )
    slot_completion_validate_parser.add_argument("--snapshot-id", required=True)
    slot_completion_validate_parser.add_argument("--runtime-version", required=True)
    slot_completion_validate_parser.add_argument("--durable-home")

    slot_cutover_parser = subparsers.add_parser("slot-cutover")
    slot_cutover_parser.add_argument("--context", required=True)
    slot_cutover_parser.add_argument("--expected-marketplace-id", required=True)
    slot_cutover_parser.add_argument("--expected-plugin-id", required=True)
    slot_cutover_parser.add_argument("--expected-payload-root", required=True)
    slot_cutover_parser.add_argument("--expected-payload-version", required=True)
    slot_cutover_parser.add_argument("--snapshot-id", required=True)
    slot_cutover_parser.add_argument("--runtime-version", required=True)
    slot_cutover_parser.add_argument(
        "--expected-namespace-generation",
        required=True,
        type=_parse_cli_generation,
    )
    slot_cutover_parser.add_argument(
        "--expected-install-generation",
        required=True,
        type=_parse_cli_generation,
    )
    current_expectation = slot_cutover_parser.add_mutually_exclusive_group(
        required=True
    )
    current_expectation.add_argument("--expected-current-version")
    current_expectation.add_argument(
        "--expect-current-absent",
        action="store_true",
    )
    slot_cutover_parser.add_argument("--durable-home")

    status_parser = subparsers.add_parser("status")
    _add_mode_arguments(status_parser)

    probe_parser = subparsers.add_parser("probe-legacy")
    _add_mode_arguments(probe_parser)

    maintenance_status_parser = subparsers.add_parser("maintenance-status")
    maintenance_status_parser.add_argument(
        "--scope",
        choices=("user", "plugin"),
        default="user",
    )
    maintenance_status_parser.add_argument("--context")
    maintenance_status_parser.add_argument("--expected-marketplace-id")
    maintenance_status_parser.add_argument("--expected-plugin-id")
    maintenance_status_parser.add_argument("--durable-home")

    maintenance_enter_parser = subparsers.add_parser("maintenance-enter")
    maintenance_enter_parser.add_argument(
        "--scope",
        required=True,
        choices=("user", "plugin"),
    )
    maintenance_enter_parser.add_argument("--owner", required=True)
    maintenance_enter_parser.add_argument("--reason", required=True)
    maintenance_enter_parser.add_argument(
        "--expected-duration-seconds",
        required=True,
        type=_parse_cli_generation,
    )
    maintenance_enter_parser.add_argument("--context")
    maintenance_enter_parser.add_argument("--expected-marketplace-id")
    maintenance_enter_parser.add_argument("--expected-plugin-id")
    maintenance_enter_parser.add_argument("--durable-home")

    maintenance_release_parser = subparsers.add_parser("maintenance-release")
    maintenance_release_parser.add_argument(
        "--scope",
        required=True,
        choices=("user", "plugin"),
    )
    maintenance_release_parser.add_argument("--maintenance-token", required=True)
    maintenance_release_parser.add_argument("--context")
    maintenance_release_parser.add_argument("--expected-marketplace-id")
    maintenance_release_parser.add_argument("--expected-plugin-id")
    maintenance_release_parser.add_argument("--durable-home")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        descriptor = _source_descriptor(arguments) if hasattr(arguments, "source_json") else None
        legacy_probe = (
            _legacy_probe_argument(arguments)
            if hasattr(arguments, "legacy_probe_json")
            else None
        )
        if arguments.action == "source-id":
            if descriptor is None:
                _fail("source-id requires --source-json or --source-file.")
            result = source_identity(
                normalize_source(descriptor),
                arguments.marketplace_key or "marketplace",
            )
        elif arguments.action == "validate":
            pointer = arguments.context or os.environ.get("COPILOT_EXTENSIONS_CONTEXT")
            if not pointer:
                _fail("validate requires --context or COPILOT_EXTENSIONS_CONTEXT.")
            result = validate_context_receipt(
                pointer,
                arguments.durable_home or Path.home() / ".copilot-extensions",
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_payload_root=(
                    arguments.expected_payload_root or arguments.payload_root
                ),
                expected_cell_root=arguments.expected_cell_root,
            )
        elif arguments.action == "resolve":
            result = resolve_context(
                payload_root=arguments.payload_root,
                plugin_id=arguments.plugin_id,
                copilot_home=arguments.copilot_home,
                project_root=arguments.project_root,
                durable_home=arguments.durable_home,
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_payload_root=arguments.expected_payload_root,
                expected_cell_root=arguments.expected_cell_root,
                source_descriptor=descriptor,
                marketplace_key=arguments.marketplace_key,
            )
        elif arguments.action == "stamp":
            result = stamp_context(
                payload_version=arguments.payload_version,
                payload_origin=arguments.payload_origin,
                expected_namespace_generation=arguments.expected_namespace_generation,
                expected_install_generation=arguments.expected_install_generation,
                payload_origin_receipt=arguments.payload_origin_receipt,
                payload_root=arguments.payload_root,
                plugin_id=arguments.plugin_id,
                copilot_home=arguments.copilot_home,
                project_root=arguments.project_root,
                durable_home=arguments.durable_home,
                source_descriptor=descriptor,
                marketplace_key=arguments.marketplace_key,
                namespace_state=arguments.namespace_state,
                install_state=arguments.install_state,
            )
        elif arguments.action == "activation-cas":
            if legacy_probe is None:
                _fail(
                    "activation-cas requires --legacy-probe-json or "
                    "--legacy-probe-file."
                )
            result = compare_and_swap_activation(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_namespace_generation=arguments.expected_namespace_generation,
                expected_install_generation=arguments.expected_install_generation,
                expected_activation_generation=arguments.expected_activation_generation,
                activation_mode=arguments.activation_mode,
                activation_state=arguments.activation_state,
                legacy_disposition=arguments.legacy_disposition,
                legacy_probe=legacy_probe,
                durable_home=arguments.durable_home,
                legacy_root=arguments.legacy_root,
                maintenance_token=arguments.maintenance_token,
            )
        elif arguments.action == "maintenance-status":
            plugin_root = None
            profile = _current_environment(
                environment=os.environ,
                os_profile=None,
                platform=None,
                wsl_distro=None,
            )[1]
            if arguments.scope == "plugin":
                if not (arguments.context and arguments.expected_marketplace_id and arguments.expected_plugin_id):
                    _fail(
                        "maintenance-status --scope plugin requires --context, "
                        "--expected-marketplace-id, and --expected-plugin-id."
                    )
                validated = validate_context_receipt(
                    arguments.context,
                    arguments.durable_home or profile / ".copilot-extensions",
                    expected_marketplace_id=arguments.expected_marketplace_id,
                    expected_plugin_id=arguments.expected_plugin_id,
                    environment={},
                )
                plugin_root = canonical_path(validated["pluginRoot"])
            result = _maintenance_inspection(
                profile=profile,
                plugin_root=plugin_root,
                current_time=datetime.now(timezone.utc),
                host=socket.gethostname(),
                pid_is_live=_pid_is_live,
            )
        elif arguments.action == "maintenance-enter":
            result = enter_maintenance(
                scope=arguments.scope,
                owner=arguments.owner,
                reason=arguments.reason,
                expected_duration_seconds=arguments.expected_duration_seconds,
                durable_home=arguments.durable_home,
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
            )
        elif arguments.action == "maintenance-release":
            result = release_maintenance(
                scope=arguments.scope,
                maintenance_token=arguments.maintenance_token,
                durable_home=arguments.durable_home,
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
            )
        elif arguments.action == "snapshot-stamp":
            result = stamp_snapshot_provenance(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_namespace_generation=arguments.expected_namespace_generation,
                expected_install_generation=arguments.expected_install_generation,
                snapshot_id=arguments.snapshot_id,
                durable_home=arguments.durable_home,
            )
        elif arguments.action == "snapshot-validate":
            result = validate_snapshot_provenance(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                snapshot_id=arguments.snapshot_id,
                durable_home=arguments.durable_home,
            )
        elif arguments.action == "slot-provision":
            result = provision_runtime_slot(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                snapshot_id=arguments.snapshot_id,
                runtime_version=arguments.runtime_version,
                expected_payload_root=arguments.expected_payload_root,
                expected_payload_version=arguments.expected_payload_version,
                durable_home=arguments.durable_home,
            )
        elif arguments.action == "slot-release":
            result = release_runtime_slot(**{
                key: value for key, value in vars(arguments).items() if key != "action"
            })
        elif arguments.action == "slot-validate":
            result = validate_runtime_slot_ownership(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                snapshot_id=arguments.snapshot_id,
                runtime_version=arguments.runtime_version,
                expected_payload_root=arguments.expected_payload_root,
                expected_payload_version=arguments.expected_payload_version,
                durable_home=arguments.durable_home,
            )
        elif arguments.action == "slot-complete":
            result = complete_runtime_slot(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_payload_root=arguments.expected_payload_root,
                expected_payload_version=arguments.expected_payload_version,
                snapshot_id=arguments.snapshot_id,
                runtime_version=arguments.runtime_version,
                durable_home=arguments.durable_home,
            )
        elif arguments.action == "slot-completion-validate":
            result = validate_runtime_slot_completion(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_payload_root=arguments.expected_payload_root,
                expected_payload_version=arguments.expected_payload_version,
                snapshot_id=arguments.snapshot_id,
                runtime_version=arguments.runtime_version,
                durable_home=arguments.durable_home,
            )
        elif arguments.action == "slot-cutover":
            result = cutover_runtime_slot(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_payload_root=arguments.expected_payload_root,
                expected_payload_version=arguments.expected_payload_version,
                snapshot_id=arguments.snapshot_id,
                runtime_version=arguments.runtime_version,
                expected_namespace_generation=arguments.expected_namespace_generation,
                expected_install_generation=arguments.expected_install_generation,
                expected_current_version=arguments.expected_current_version,
                expect_current_absent=arguments.expect_current_absent,
                durable_home=arguments.durable_home,
            )
        else:
            mode_arguments = {
                "legacy_root": arguments.legacy_root,
                "legacy_probe": legacy_probe,
                "policy_path": arguments.policy_path,
                "payload_root": arguments.payload_root,
                "plugin_id": arguments.plugin_id,
                "copilot_home": arguments.copilot_home,
                "project_root": arguments.project_root,
                "durable_home": arguments.durable_home,
                "context": arguments.context,
                "expected_marketplace_id": arguments.expected_marketplace_id,
                "expected_plugin_id": arguments.expected_plugin_id,
                "expected_payload_root": arguments.expected_payload_root,
                "expected_cell_root": arguments.expected_cell_root,
                "source_descriptor": descriptor,
                "marketplace_key": arguments.marketplace_key,
            }
            if arguments.action == "status":
                result = resolve_installation_mode(**mode_arguments)
            else:
                result = probe_legacy_entrypoint(**mode_arguments)
        json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
        if arguments.action == "probe-legacy" and not result["allowMutation"]:
            return 3
        return 0
    except InstallationContextError as error:
        print(f"installation-context: {error}", file=sys.stderr)
        return 1
