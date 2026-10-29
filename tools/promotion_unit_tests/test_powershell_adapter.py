"""PowerShell argument translation does not change ordinary Python semantics."""
from __future__ import annotations

import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ENTRY = Path(__file__).resolve().parents[2] / "libs/installation-context/installation_context.py"


@pytest.fixture
def sdk() -> ModuleType:
    spec = importlib.util.spec_from_file_location("powershell_adapter_sdk", ENTRY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _parameters(**values: object) -> dict[str, object]:
    return {
        "NamespaceState": "active", "InstallState": "active",
        "ExpectCurrentAbsent": False, "WithContextValidation": False,
        **values,
    }


@pytest.mark.parametrize(
    ("action", "values", "expected"),
    [
        ("source-id", {"SourceJson": '{"source":"github","repo":"example/repo"}',
                       "MarketplaceKey": '-quoted "value" \u03bb'},
         ['--source-json={"source":"github","repo":"example/repo"}',
          '--marketplace-key=-quoted "value" \u03bb']),
        ("status", {"LegacyRoot": "/legacy", "ExpectedPayloadRoot": "/payload",
                    "ExpectedPluginId": "agent-example", "WithContextValidation": True},
         ["--expected-plugin-id=agent-example", "--expected-payload-root=/payload",
          "--legacy-root=/legacy", "--with-context-validation"]),
        ("slot-cutover", {"ExpectedCurrentVersion": "", "SnapshotId": "one",
                          "ExpectedNamespaceGeneration": "00",
                          "ExpectedInstallGeneration": "9223372036854775807",
                          "RuntimeVersion": "1.0", "ExpectedPayloadRoot": "/payload",
                          "ExpectedPayloadVersion": "1.0"},
         ["--snapshot-id=one", "--runtime-version=1.0", "--expected-payload-root=/payload",
          "--expected-payload-version=1.0", "--expected-namespace-generation=00",
          "--expected-install-generation=9223372036854775807", "--expected-current-version="]),
        ("source-id", {"SourceJson": "{}", "Context": "/ignored",
                       "ExpectedNamespaceGeneration": False},
         ["--source-json={}"]),
    ],
)
def test_argument_translation_preserves_values_and_ignored_parameters(
    sdk: ModuleType, action: str, values: dict[str, object], expected: list[str],
) -> None:
    assert sdk._powershell_arguments(action, _parameters(**values), list(values)) == [action, *expected]


@pytest.mark.parametrize(
    ("action", "values", "message"),
    [
        ("Status", {}, "Action must be one of"),
        ("status", {"ExpectedPayloadVersion": ""}, "valid only for runtime slot"),
        ("status", {"ExpectedCurrentVersion": ""}, "valid only for slot-cutover"),
        ("status", {"ExpectCurrentAbsent": False}, "valid only for slot-cutover"),
        ("source-id", {"SourceJson": "{}", "ExpectedPayloadRoot": ""}, "not valid for source-id"),
        ("slot-provision", {"ExpectedPayloadRoot": ""}, "root must be absolute"),
        ("slot-provision", {"ExpectedPayloadVersion": " "}, "non-empty string"),
        ("slot-cutover", {}, "requires -ExpectedPayloadRoot"),
        ("guest-status", {"Distribution": ""}, "non-empty WSL"),
        ("resolve", {"WithContextValidation": True}, "valid only for status"),
    ],
)
def test_supplied_empty_and_false_flags_retain_their_contract(
    sdk: ModuleType, action: str, values: dict[str, object], message: str,
) -> None:
    with pytest.raises(sdk.InstallationContextError, match=message):
        sdk._powershell_arguments(action, _parameters(**values), list(values))


@pytest.mark.parametrize("failed", [False, True])
def test_path_expansion_is_scoped_and_restored(
    sdk: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: bool,
) -> None:
    monkeypatch.setenv("SDK_PATH_COMPONENT", "expanded")
    raw = str(tmp_path / "%SDK_PATH_COMPONENT%" / "missing")
    seen: list[Path] = []

    def main(arguments: list[str]) -> int:
        value = next(arg.split("=", 1)[1] for arg in arguments if arg.startswith("--expected-payload-root="))
        seen.append(sdk.canonical_path(value))
        assert sdk.canonical_path(raw) == Path(raw)
        if failed:
            raise RuntimeError("controlled failure")
        return 0

    monkeypatch.setattr(sdk, "main", main)
    assert sdk.canonical_path(raw) == Path(raw)
    if failed:
        with pytest.raises(RuntimeError, match="controlled failure"):
            sdk._powershell_main("validate", _parameters(ExpectedPayloadRoot=raw), ["ExpectedPayloadRoot"])
    else:
        assert sdk._powershell_main("validate", _parameters(ExpectedPayloadRoot=raw), ["ExpectedPayloadRoot"]) == 0
    assert seen == [tmp_path / "expanded" / "missing"]
    assert sdk.canonical_path(raw) == Path(raw)
    assert not sdk._POWERSHELL_PATHS.get()


def test_expanded_inputs_and_receipts_keep_literal_environment_tokens(
    sdk: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SDK_OUTER", "%SDK_INNER%")
    monkeypatch.setenv("SDK_INNER", "wrong-directory")
    raw = str(tmp_path / "%SDK_OUTER%")
    expanded = str(tmp_path / "%SDK_INNER%")
    parameters = _parameters(
        CopilotHome=expanded, DurableHome=expanded, PayloadRoot=raw,
        Context=expanded + "/install.json", ExpectedPayloadRoot=raw,
        ExpectedCellRoot=raw, ProjectRoot=expanded,
    )

    def main(arguments: list[str]) -> int:
        assert f"--expected-payload-root={expanded}" in arguments
        assert f"--expected-cell-root={expanded}" in arguments
        assert f"--payload-root={expanded}" in arguments
        assert f"--context={expanded}/install.json" in arguments
        path = sdk.canonical_path(expanded)
        assert sdk.canonical_path(path) == tmp_path / "%SDK_INNER%"
        assert sdk.canonical_path(str(path / "install.json")) == path / "install.json"
        descriptor = {"kind": "directory", "canonical": "directory:" + expanded}
        assert sdk.normalize_source(descriptor, from_receipt=True).canonical == descriptor["canonical"]
        return 0

    monkeypatch.setattr(sdk, "main", main)
    assert sdk._powershell_main(
        "status", parameters, ["ExpectedPayloadRoot", "ExpectedCellRoot"],
    ) == 0


@pytest.mark.parametrize("action", [
    "resolve", "status", "deployment-status", "probe-plugin-publication", "probe-legacy",
])
@pytest.mark.parametrize("pointer", ["explicit", "inherited", "absent"])
def test_payload_root_expands_once_only_when_not_normalized_by_shell(
    sdk: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    action: str, pointer: str,
) -> None:
    monkeypatch.setenv("SDK_OUTER", "%SDK_INNER%")
    monkeypatch.setenv("SDK_INNER", "wrong-directory")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    expanded = str(tmp_path / "%SDK_INNER%")
    values: dict[str, object] = {
        "PayloadRoot": expanded if pointer == "absent" else str(tmp_path / "%SDK_OUTER%"),
    }
    if pointer == "explicit":
        values["Context"] = str(tmp_path / "install.json")
    elif pointer == "inherited":
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "install.json"))
    arguments = sdk._powershell_arguments(action, _parameters(**values), list(values))
    assert f"--payload-root={expanded}" in arguments


@pytest.mark.parametrize("action", [
    "resolve", "validate", "status", "deployment-status", "probe-plugin-publication", "probe-legacy",
])
@pytest.mark.parametrize("pointer", ["explicit", "inherited"])
@pytest.mark.parametrize("root_state", ["raw", "normalized", "relative"])
def test_worker_expands_only_unprocessed_inherited_plugin_root(
    sdk: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    action: str, pointer: str, root_state: str,
) -> None:
    monkeypatch.setenv("SDK_OUTER", "%SDK_INNER%")
    monkeypatch.setenv("SDK_INNER", "wrong-directory")
    monkeypatch.setenv("SDK_ABSOLUTE", str(tmp_path / "not-admitted"))
    expanded = str(tmp_path / "%SDK_INNER%")
    raw = str(tmp_path / "%SDK_OUTER%")
    inherited = "%SDK_ABSOLUTE%" if root_state == "relative" else raw
    expected = inherited if root_state == "relative" else expanded
    processed = {"COPILOT_PLUGIN_ROOT": expanded} if root_state == "normalized" else {}
    environment = {"COPILOT_PLUGIN_ROOT": inherited}
    context = str(tmp_path / "install.json")
    parameters = {"Context": context} if pointer == "explicit" else {}
    if pointer == "inherited":
        environment["COPILOT_EXTENSIONS_CONTEXT"] = context
    spec = importlib.util.spec_from_file_location(
        "unit_powershell_worker", ENTRY.with_name("_installation_context_worker.py"),
    )
    assert spec and spec.loader
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setattr(worker, "os", SimpleNamespace(
        environ=environment, chdir=lambda directory: None,
    ))
    monkeypatch.setattr(worker.sys, "path", list(sys.path))
    monkeypatch.setitem(sys.modules, "agent_procutil", ModuleType("agent_procutil"))
    monkeypatch.setitem(sys.modules, "agent_procutil._shell_caller_owned", SimpleNamespace(
        serve=lambda callback, **lifetime: callback(),
    ))

    def invoke(
        received_action: str, received_parameters: dict[str, object], supplied: list[str],
    ) -> int:
        assert received_action == action and received_parameters == parameters
        assert environment["COPILOT_PLUGIN_ROOT"] == expected
        assert sdk.canonical_path(str(tmp_path / "%SDK_INNER%")) == tmp_path / "%SDK_INNER%"
        return 7

    monkeypatch.setattr(worker.runpy, "run_path", lambda path, **kwargs: {
        "_powershell_main": invoke,
        "_path_is_fully_qualified": sdk._path_is_fully_qualified,
        "_powershell_path_text": sdk._powershell_path_text,
    })
    assert worker.main({
        "sdk": str(ENTRY), "action": action, "parameters": parameters,
        "supplied": list(parameters), "environment": processed,
        "processDirectory": str(tmp_path), "lifetime": {},
    }) == 7


@pytest.mark.parametrize("action", [
    "activation-cas", "snapshot-stamp", "snapshot-validate", "slot-provision",
    "slot-validate", "slot-complete", "slot-completion-validate", "slot-cutover", "slot-release",
])
@pytest.mark.parametrize("context", [None, ""])
def test_management_actions_never_adopt_ambient_context_as_authorization(
    sdk: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    action: str, context: str | None,
) -> None:
    ambient = str(tmp_path / "ambient/install.json")
    explicit = str(tmp_path / "explicit/install.json")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", ambient)
    parameters = _parameters(
        ExpectedPayloadRoot=str(tmp_path / "payload"), ExpectedPayloadVersion="1.0",
    )
    supplied: list[str] = []
    if action.startswith("slot-") and action != "slot-release":
        supplied += ["ExpectedPayloadRoot", "ExpectedPayloadVersion"]
    else:
        parameters.pop("ExpectedPayloadRoot")
        parameters.pop("ExpectedPayloadVersion")
    if context is not None:
        parameters["Context"] = context
        supplied.append("Context")
    arguments = sdk._powershell_arguments(action, parameters, supplied)
    assert not any(value.startswith("--context=") for value in arguments)
    arguments = sdk._powershell_arguments(
        action, {**parameters, "Context": explicit}, [*supplied, "Context"],
    )
    assert f"--context={explicit}" in arguments


@pytest.mark.parametrize("action", [
    "resolve", "validate", "status", "deployment-status", "probe-plugin-publication", "probe-legacy",
])
def test_read_only_actions_preserve_ambient_context_selection(
    sdk: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    ambient = str(tmp_path / "ambient/install.json")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", ambient)
    assert f"--context={ambient}" in sdk._powershell_arguments(action, _parameters(), [])


@pytest.mark.parametrize("from_file", [False, True])
def test_raw_directory_declaration_expands_once_only_at_cli_intake(
    sdk: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, from_file: bool,
) -> None:
    monkeypatch.setenv("SDK_OUTER", "%SDK_INNER%")
    monkeypatch.setenv("SDK_INNER", "wrong-directory")
    descriptor = {"source": "directory", "path": str(tmp_path / "%SDK_OUTER%")}
    arguments = Namespace(
        source_json=None if from_file else json.dumps(descriptor),
        source_file="/source.json" if from_file else None,
    )
    monkeypatch.setattr(sdk, "read_json", lambda path: descriptor)
    assert sdk._source_descriptor(arguments) == descriptor
    token = sdk._POWERSHELL_PATHS.set(True)
    try:
        expected = {**descriptor, "path": str(tmp_path / "%SDK_INNER%")}
        assert sdk._source_descriptor(arguments) == expected
        canonical = {"kind": "directory", "canonical": "directory:" + expected["path"]}
        assert sdk._powershell_source_descriptor(canonical) == canonical
        assert sdk.normalize_source(canonical, from_receipt=True).canonical == canonical["canonical"]
    finally:
        sdk._POWERSHELL_PATHS.reset(token)


def test_settings_declaration_expands_only_its_raw_path_not_its_base(
    sdk: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SDK_OUTER", "%SDK_INNER%")
    monkeypatch.setenv("SDK_INNER", "wrong-directory")
    base = tmp_path / "%SDK_INNER%"
    descriptor = {"source": "directory", "path": "%SDK_OUTER%"}
    monkeypatch.setattr(Path, "is_file", lambda path: path.name == "settings.json")
    monkeypatch.setattr(sdk, "canonical_path", lambda path, **kwargs: Path(path))
    monkeypatch.setattr(sdk, "read_json", lambda path: {
        "extraKnownMarketplaces": {"example": {"source": descriptor}},
    })
    token = sdk._POWERSHELL_PATHS.set(True)
    try:
        declarations = sdk._get_declarations("example", base, None)
        assert declarations[0]["source"].canonical == f"directory:{base / '%SDK_INNER%'}"
        assert declarations[0]["settingsPath"] == base / "settings.json"
    finally:
        sdk._POWERSHELL_PATHS.reset(token)


@pytest.mark.parametrize("action,name", [
    ("validate", "ExpectedPayloadRoot"), ("validate", "ExpectedCellRoot"),
    ("validate", "PayloadRoot"), ("validate", "Context"),
    ("activation-cas", "LegacyRoot"), ("slot-release", "ReservationRoot"),
])
def test_remaining_raw_path_inputs_expand_once_without_relaxing_absolute_admission(
    sdk: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str, name: str,
) -> None:
    monkeypatch.setenv("SDK_OUTER", "%SDK_INNER%")
    monkeypatch.setenv("SDK_INNER", str(tmp_path / "wrong-directory"))
    option = {
        "ExpectedPayloadRoot": "expected-payload-root", "ExpectedCellRoot": "expected-cell-root",
        "PayloadRoot": "payload-root", "Context": "context",
        "LegacyRoot": "legacy-root", "ReservationRoot": "reservation-root",
    }[name]
    raw = str(tmp_path / "%SDK_OUTER%")
    arguments = sdk._powershell_arguments(action, _parameters(**{name: raw}), [name])
    assert f"--{option}={tmp_path / '%SDK_INNER%'}" in arguments
    arguments = sdk._powershell_arguments(action, _parameters(**{name: "%SDK_INNER%"}), [name])
    assert f"--{option}=%SDK_INNER%" in arguments


def test_path_expansion_does_not_relax_receipt_admission(
    sdk: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SDK_ABSOLUTE", str(tmp_path))

    def main(arguments: list[str]) -> int:
        with pytest.raises(sdk.InstallationContextError, match="receipt pointer must be absolute"):
            sdk.validate_context_receipt("%SDK_ABSOLUTE%/install.json", tmp_path)
        return 0

    monkeypatch.setattr(sdk, "main", main)
    assert sdk._powershell_main("source-id", _parameters(SourceJson="{}"), ["SourceJson"]) == 0


@pytest.mark.parametrize("status", ["ready", "deactivation-required"])
def test_composed_status_reuses_one_validation_scope(
    sdk: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str,
) -> None:
    pointer = tmp_path / "marketplaces/example/plugins/agent-example/install.json"
    arguments = Namespace(
        with_context_validation=True,
        expected_marketplace_id="example--0123456789abcdef",
        expected_plugin_id="agent-example",
        expected_payload_root="/expected-payload",
        expected_cell_root=str(tmp_path / "expected-cell"),
    )
    caches: list[dict[str, object]] = []

    def resolve(**values: object) -> dict[str, object]:
        cache = sdk._VALIDATED_FILE_SHA256.get()
        assert cache is not None
        cache["observed-before-validation"] = object()
        caches.append(cache)
        return {"actualMode": "namespaced", "status": status, "context": str(pointer)}

    def validate(context: Path, durable: Path, **values: object) -> dict[str, object]:
        assert sdk._VALIDATED_FILE_SHA256.get() is caches[0]
        assert "observed-before-validation" in caches[0]
        assert context == pointer and durable == tmp_path
        assert values == {
            "expected_marketplace_id": "example--0123456789abcdef",
            "expected_plugin_id": "agent-example", "expected_payload_root": "/expected-payload",
            "expected_cell_root": str(tmp_path / "expected-cell"),
        }
        return {"action": "validate", "generation": 1}

    monkeypatch.setattr(sdk, "resolve_installation_mode", resolve)
    monkeypatch.setattr(sdk, "validate_context_receipt", validate)
    result = sdk._resolve_cli_status(arguments, {})
    assert result["contextValidation"] == {"action": "validate", "generation": 1}
    assert sdk._VALIDATED_FILE_SHA256.get() is None


@pytest.mark.parametrize("powershell,must_exist", [(True, True), (False, True), (True, False)])
def test_missing_path_diagnostic_preserves_the_powershell_contract(
    sdk: ModuleType, monkeypatch: pytest.MonkeyPatch, powershell: bool, must_exist: bool,
) -> None:
    class MissingPath:
        def resolve(self, *, strict: bool) -> Path:
            assert strict is must_exist
            raise FileNotFoundError(2, "controlled missing path")

    monkeypatch.setattr(sdk, "Path", lambda text: MissingPath())
    token = sdk._POWERSHELL_PATHS.set(powershell)
    try:
        expected = "Path does not exist" if powershell and must_exist else "Cannot resolve path"
        with pytest.raises(sdk.InstallationContextError, match=expected):
            sdk.canonical_path("/controlled-missing", must_exist=must_exist)
    finally:
        sdk._POWERSHELL_PATHS.reset(token)


@pytest.mark.parametrize("error", [
    PermissionError("controlled denied path"),
    OSError("controlled unavailable path"),
    RuntimeError("controlled path loop"),
])
def test_path_resolution_errors_are_not_relabelled_as_missing(
    sdk: ModuleType, monkeypatch: pytest.MonkeyPatch, error: Exception,
) -> None:
    class FailedPath:
        def resolve(self, *, strict: bool) -> Path:
            assert strict is True
            raise error

    monkeypatch.setattr(sdk, "Path", lambda text: FailedPath())
    token = sdk._POWERSHELL_PATHS.set(True)
    try:
        with pytest.raises(sdk.InstallationContextError, match="Cannot resolve path") as caught:
            sdk.canonical_path("/controlled-path", must_exist=True)
        assert str(error) in str(caught.value)
    finally:
        sdk._POWERSHELL_PATHS.reset(token)
