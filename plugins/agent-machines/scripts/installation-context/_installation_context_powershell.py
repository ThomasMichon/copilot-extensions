# ruff: noqa: F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

_POWERSHELL_ACTIONS = (
    "guest-register", "guest-status", "source-id", "resolve", "validate", "stamp",
    "activation-cas", "snapshot-stamp", "snapshot-validate", "slot-provision",
    "slot-validate", "slot-complete", "slot-completion-validate", "slot-cutover",
    "slot-release", "status", "deployment-status", "probe-plugin-publication",
    "probe-legacy",
)
_POWERSHELL_RESOLUTION = (
    "SourceJson", "SourceFile", "MarketplaceKey", "PluginId", "PayloadRoot",
    "CopilotHome", "ProjectRoot", "DurableHome", "Context",
    "ExpectedMarketplaceId", "ExpectedPluginId", "ExpectedPayloadRoot",
    "ExpectedCellRoot",
)
_POWERSHELL_SLOT_ACTIONS = (
    "slot-provision", "slot-validate", "slot-complete",
    "slot-completion-validate", "slot-cutover",
)


def _powershell_path_text(text: str) -> str:
    # .NET ExpandEnvironmentVariables uses %NAME% on both supported platforms.
    return re.sub(r"%([^%]+)%", lambda match: os.environ.get(match[1], match[0]), text)


def _powershell_source_descriptor(value: Mapping[str, Any]) -> Mapping[str, Any]:
    kind = (_string_property(value, "kind") or _string_property(value, "source")).strip().lower()
    if (
        kind in {"local", "directory"}
        and not _string_property(value, "canonical")
        and not _string_property(value, "stableId").strip()
    ):
        return {**value, "path": _powershell_path_text(_string_property(value, "path"))}
    return value


def _powershell_arguments(
    action: str,
    parameters: Mapping[str, Any],
    supplied: Sequence[str],
) -> list[str]:
    """Translate the existing PowerShell contract, not the receipt protocol."""
    if action not in _POWERSHELL_ACTIONS:
        _fail(f"Action must be one of: {', '.join(_POWERSHELL_ACTIONS)}.")
    if parameters.get("WithContextValidation"):
        if action != "status":
            _fail("-WithContextValidation is valid only for status.")
        if not parameters.get("ExpectedPluginId") or not parameters.get("ExpectedPayloadRoot"):
            _fail("-WithContextValidation requires -ExpectedPluginId and -ExpectedPayloadRoot.")
    if action != "slot-cutover" and (
        "ExpectedCurrentVersion" in supplied or "ExpectCurrentAbsent" in supplied
    ):
        _fail("-ExpectedCurrentVersion and -ExpectCurrentAbsent are valid only for slot-cutover.")
    if action in _POWERSHELL_SLOT_ACTIONS:
        for name, message in (
            ("ExpectedPayloadRoot", "Expected snapshot payload root must be absolute."),
            ("ExpectedPayloadVersion", "Expected snapshot payload version must be a non-empty string."),
        ):
            if name in supplied and not str(parameters.get(name, "")).strip():
                _fail(message)
    elif "ExpectedPayloadVersion" in supplied:
        _fail("-ExpectedPayloadVersion is valid only for runtime slot actions.")
    if action in {"slot-complete", "slot-completion-validate", "slot-cutover"}:
        for name in ("ExpectedPayloadRoot", "ExpectedPayloadVersion"):
            if name not in supplied:
                _fail(f"{action} requires -{name}.")
    if "ExpectedPayloadRoot" in supplied and action not in (
        *_POWERSHELL_SLOT_ACTIONS, "resolve", "validate", "status",
        "deployment-status", "probe-plugin-publication", "probe-legacy",
    ):
        _fail(f"-ExpectedPayloadRoot is not valid for {action}.")
    if parameters.get("PayloadOrigin", "") not in {"", "installed", "directory", "staged", "explicit"}:
        _fail("payload origin must be installed, directory, staged, or explicit.")
    for name in ("NamespaceState", "InstallState"):
        if parameters[name] not in {"active", "inactive", "orphaned", "removing"}:
            _fail(f"{name} must be active, inactive, orphaned, or removing.")
    if action == "source-id" and not (parameters.get("SourceJson") or parameters.get("SourceFile")):
        _fail("source-id requires -SourceJson or -SourceFile.")
    if action == "guest-status" and "Distribution" in supplied and not parameters.get("Distribution"):
        _fail("distribution must be a non-empty WSL distribution name.")

    if action in {"guest-register", "guest-status"}:
        names = ("Distribution",)
    elif action == "source-id":
        names = ("SourceJson", "SourceFile", "MarketplaceKey")
    elif action == "validate":
        names = (
            "Context", "PayloadRoot", "ExpectedMarketplaceId", "ExpectedPluginId",
            "ExpectedPayloadRoot", "ExpectedCellRoot", "DurableHome",
        )
    elif action in {"resolve", "stamp", "status", "deployment-status", "probe-plugin-publication", "probe-legacy"}:
        names = _POWERSHELL_RESOLUTION
        if action == "stamp":
            names = tuple(name for name in names if name not in {
                "Context", "ExpectedMarketplaceId", "ExpectedPluginId",
                "ExpectedPayloadRoot", "ExpectedCellRoot",
            }) + (
                "PayloadVersion", "PayloadOrigin", "PayloadOriginReceipt",
                "ExpectedNamespaceGeneration", "ExpectedInstallGeneration",
                "NamespaceState", "InstallState",
            )
        elif action != "resolve":
            names += ("LegacyRoot", "LegacyProbeJson", "LegacyProbeFile", "PolicyPath")
    else:
        names = ("Context", "ExpectedMarketplaceId", "ExpectedPluginId", "DurableHome")
        if action == "activation-cas":
            names += (
                "ExpectedNamespaceGeneration", "ExpectedInstallGeneration",
                "ExpectedActivationGeneration", "ActivationMode", "ActivationState",
                "LegacyDisposition", "LegacyProbeJson", "LegacyProbeFile", "LegacyRoot",
            )
        elif action == "slot-release":
            names += (
                "RuntimeVersion", "ReservationRoot", "ExpectedReservationSha256",
                "ExpectedReservationGeneration", "ExpectedNamespaceGeneration", "ExpectedInstallGeneration",
            )
        else:
            names += ("SnapshotId",)
            if action == "snapshot-stamp":
                names += ("ExpectedNamespaceGeneration", "ExpectedInstallGeneration")
            elif action in _POWERSHELL_SLOT_ACTIONS:
                names += ("RuntimeVersion", "ExpectedPayloadRoot", "ExpectedPayloadVersion")
                if action == "slot-cutover":
                    names += ("ExpectedNamespaceGeneration", "ExpectedInstallGeneration", "ExpectedCurrentVersion")
    # Other scalar paths have already passed ConvertTo-InputPath in the shell.
    # Expand only the remaining raw inputs, never a receipt or a derived path.
    raw_paths = {"ExpectedPayloadRoot", "ExpectedCellRoot", "ReservationRoot"}
    if action not in {"status", "deployment-status", "probe-plugin-publication", "probe-legacy"}:
        raw_paths.add("Context")
    pointer = parameters.get("Context") or os.environ.get("COPILOT_EXTENSIONS_CONTEXT")
    if action == "validate" or (
        pointer and action in {
            "resolve", "status", "deployment-status", "probe-plugin-publication", "probe-legacy",
        }
    ):
        raw_paths.add("PayloadRoot")
    if action == "activation-cas":
        raw_paths.add("LegacyRoot")
    arguments = [action]
    for name in names:
        value = parameters.get(name)
        if name == "Context" and not value and action in {
            "resolve", "validate", "status", "deployment-status", "probe-plugin-publication", "probe-legacy",
        }:
            value = os.environ.get("COPILOT_EXTENSIONS_CONTEXT")
        if value is None:
            continue
        if value == "" and name != "ExpectedCurrentVersion":
            continue
        if name in raw_paths and _path_is_fully_qualified(str(value)):
            value = _powershell_path_text(str(value))
        option = re.sub(r"(?<!^)([A-Z])", r"-\1", name).lower()
        arguments.append(f"--{option}={value}")
    if action == "slot-cutover" and parameters["ExpectCurrentAbsent"]:
        arguments.append("--expect-current-absent")
    if parameters["WithContextValidation"]:
        arguments.append("--with-context-validation")
    return arguments


def _powershell_main(
    action: str,
    parameters: Mapping[str, Any],
    supplied: Sequence[str],
) -> int:
    token = _POWERSHELL_PATHS.set(True)
    try:
        try:
            arguments = _powershell_arguments(action, parameters, supplied)
        except InstallationContextError as error:
            print(f"installation-context: {error}", file=sys.stderr)
            return 1
        try:
            return main(arguments)
        except SystemExit as error:
            # PowerShell's existing CLI reports invalid/missing arguments as 1.
            if error.code == 2:
                return 1
            raise
    finally:
        _POWERSHELL_PATHS.reset(token)
