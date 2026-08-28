from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from installation_context import normalize_source, source_identity, stamp_context

from installer_readiness import (
    ConfigurationEmpty,
    MarketplaceProvenance,
    PlanState,
    Platform,
    PluginInstallation,
    ReadinessResult,
    ReadinessState,
    SettingsGroup,
    SettingsLayer,
    build_plan,
    discover_from_settings,
    discover_modules,
    parse_readiness,
)

PAYLOAD_GENERATOR = (
    Path(__file__).resolve().parents[2] / "payload-invocation" / "generate.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "installer_readiness_payload_generator",
    PAYLOAD_GENERATOR,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load payload-invocation generator")
payload_generator = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(payload_generator)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _command_id(plugin: str) -> str:
    return plugin if plugin.startswith("agent-") else f"agent-{plugin}"


def _invocations(
    plugin: str,
    platforms: tuple[str, ...] = ("windows", "linux"),
) -> tuple[dict[str, object], dict[str, object]]:
    installer: dict[str, object] = {}
    readiness: dict[str, object] = {}
    for platform in platforms:
        suffix = "ps1" if platform == "windows" else "sh"
        installer[platform] = {
            "kind": "payload-script",
            "path": f"scripts/install.{suffix}",
            "arguments": ["update"],
        }
        readiness[platform] = {
            "kind": "payload-command",
            "command": _command_id(plugin),
            "arguments": ["status", "--json"],
        }
    return installer, readiness


def _module(
    plugin: str,
    local_id: str = "runtime",
    *,
    dependencies: tuple[str, ...] = (),
    classification: str = "required",
    configuration_empty: str = "satisfied",
    platforms: tuple[str, ...] = ("windows", "linux"),
) -> dict[str, object]:
    installer, readiness = _invocations(plugin, platforms)
    return {
        "id": f"{plugin}/{local_id}",
        "platforms": list(platforms),
        "classification": classification,
        "installer": installer,
        "readiness": {
            "schema": "copilot-extensions.module-readiness",
            "version": 1,
            "configurationEmpty": configuration_empty,
            "invocations": readiness,
        },
        "dependsOn": list(dependencies),
        "restart": "none",
    }


def _script_module(
    plugin: str,
    *,
    platforms: tuple[str, ...] = ("windows", "linux"),
) -> dict[str, object]:
    module = _module(plugin, platforms=platforms)
    readiness = module["readiness"]
    assert isinstance(readiness, dict)
    invocations = readiness["invocations"]
    assert isinstance(invocations, dict)
    for platform in platforms:
        suffix = "ps1" if platform == "windows" else "sh"
        invocations[platform] = {
            "kind": "payload-script",
            "path": f"scripts/readiness.{suffix}",
            "arguments": ["--json"],
        }
    return module


def _payload(
    root: Path,
    plugin: str,
    *,
    modules: list[dict[str, object]] | None = None,
    declined: str | None = None,
    include_reference: bool = True,
    include_command_manifest: bool = True,
) -> Path:
    manifest: dict[str, object] = {
        "name": plugin,
        "version": "1.0.0",
        "runtimeScope": "machine-gated",
    }
    if include_reference:
        manifest["installerReadiness"] = "installer-readiness.json"
    _write(root / "plugin.json", manifest)
    if include_command_manifest:
        command = _command_id(plugin)
        _write(
            root / "payload-invocation.json",
            {
                "schema": "copilot-extensions.payload-invocation",
                "version": 1,
                "command": command,
                "module": command.replace("-", "_"),
                "runtimeRoot": f".{command}",
                "noSelfProvisionEnv": (
                    f"{command.replace('-', '_').upper()}_NO_SELFPROVISION"
                ),
                "purpose": "Fixture command",
            },
        )
    for path in (
        root / "scripts" / "install.ps1",
        root / "scripts" / "install.sh",
        root / "scripts" / "readiness.ps1",
        root / "scripts" / "readiness.sh",
        root / "bin" / f"{_command_id(plugin)}.ps1",
        root / "bin" / _command_id(plugin),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
        if path.suffix == "":
            path.chmod(0o755)
    if include_reference:
        contract: dict[str, object] = {
            "schema": "copilot-extensions.installer-readiness",
            "version": 1,
            "owner": {"plugin": plugin},
        }
        if declined is not None:
            contract.update({"state": "declined", "reason": declined})
        else:
            contract.update(
                {"state": "supported", "modules": modules or [_module(plugin)]}
            )
        _write(root / "installer-readiness.json", contract)
    return root


def _installation(
    root: Path,
    plugin: str,
    *,
    marketplace_key: str = "example",
    repository: str = "example/marketplace",
) -> PluginInstallation:
    normalized = normalize_source({"source": "github", "repo": repository})
    identity = source_identity(normalized, marketplace_key)
    return PluginInstallation(
        plugin_id=plugin,
        payload_root=root,
        provenance=MarketplaceProvenance(
            marketplace_id=identity["marketplaceId"],
            source_fingerprint=identity["fingerprint"],
            source_kind=normalized.kind,
            source_canonical=normalized.canonical,
        ),
        scopes=("fixture",),
    )


def _stamp(
    durable: Path,
    payload: Path,
    plugin: str,
    *,
    marketplace_key: str = "example",
) -> None:
    descriptor = {"source": "github", "repo": "example/marketplace"}
    identity = source_identity(normalize_source(descriptor), marketplace_key)
    namespace = (
        durable
        / "marketplaces"
        / identity["marketplaceId"]
        / "namespace.json"
    )
    stamp_context(
        payload_version="1.0.0",
        payload_origin="explicit",
        expected_namespace_generation=1 if namespace.exists() else 0,
        expected_install_generation=0,
        payload_root=payload,
        plugin_id=plugin,
        durable_home=durable,
        source_descriptor=descriptor,
        marketplace_key=marketplace_key,
        environment={},
    )


def _settings(repo: Path, plugins: tuple[str, ...], *, key: str = "example") -> None:
    _write(
        repo / ".github" / "copilot" / "settings.json",
        {
            "extraKnownMarketplaces": {
                key: {
                    "source": {
                        "source": "github",
                        "repo": "example/marketplace",
                    }
                }
            },
            "enabledPlugins": {f"{plugin}@{key}": True for plugin in plugins},
        },
    )


def _user_settings(
    copilot_home: Path,
    plugins: tuple[str, ...],
    *,
    key: str = "example",
) -> None:
    _write(
        copilot_home / "settings.json",
        {
            "extraKnownMarketplaces": {
                key: {
                    "source": {
                        "source": "github",
                        "repo": "example/marketplace",
                    }
                }
            },
            "enabledPlugins": {f"{plugin}@{key}": True for plugin in plugins},
        },
    )


def test_user_settings_discover_enabled_plugin(tmp_path):
    durable = tmp_path / "durable"
    copilot_home = tmp_path / "copilot"
    _user_settings(copilot_home, ("demo",))
    _stamp(durable, _payload(tmp_path / "demo", "demo"), "demo")

    report = discover_from_settings(
        [SettingsGroup(copilot_home, "global", SettingsLayer.USER)],
        durable,
    )

    assert report.valid
    assert [module.module_id for module in report.modules] == ["demo/runtime"]
    assert report.modules[0].owner.scopes == ("global",)


def test_project_disable_overrides_user_enablement_before_filtering(tmp_path):
    durable = tmp_path / "durable"
    copilot_home = tmp_path / "copilot"
    repo = tmp_path / "repo"
    _user_settings(copilot_home, ("demo",))
    _write(
        repo / ".claude" / "settings.local.json",
        {"enabledPlugins": {"demo@example": True}},
    )
    _write(
        repo / ".github" / "copilot" / "settings.json",
        {"enabledPlugins": {"demo@example": False}},
    )
    _stamp(durable, _payload(tmp_path / "demo", "demo"), "demo")

    report = discover_from_settings(
        [
            SettingsGroup(repo, "project:fixture", SettingsLayer.PROJECT),
            SettingsGroup(copilot_home, "global", SettingsLayer.USER),
        ],
        durable,
    )

    assert report.valid
    assert report.modules == ()
    assert report.machine_gated_owners == ()


def test_project_group_does_not_read_top_level_settings(tmp_path):
    durable = tmp_path / "durable"
    repo = tmp_path / "repo"
    _user_settings(repo, ("demo",))
    _stamp(durable, _payload(tmp_path / "demo", "demo"), "demo")

    report = discover_from_settings(
        [SettingsGroup(repo, "project:fixture", SettingsLayer.PROJECT)],
        durable,
    )

    assert report.valid
    assert report.modules == ()


def test_fixture_inventory_covers_every_enabled_machine_gated_plugin(tmp_path):
    durable = tmp_path / "home"
    repo = tmp_path / "repo"
    _settings(repo, ("supported", "declined"))
    _stamp(durable, _payload(tmp_path / "supported", "supported"), "supported")
    _stamp(
        durable,
        _payload(
            tmp_path / "declined",
            "declined",
            declined="Runtime setup is intentionally delegated.",
        ),
        "declined",
    )

    report = discover_from_settings(
        [SettingsGroup(repo, "project:fixture")],
        durable,
    )

    assert report.valid
    assert report.machine_gated_owners == tuple(sorted(report.covered_owners))
    assert [module.module_id for module in report.modules] == ["supported/runtime"]
    assert [decline.owner.plugin_id for decline in report.declines] == ["declined"]


def test_fixture_inventory_rejects_silent_omission(tmp_path):
    durable = tmp_path / "home"
    repo = tmp_path / "repo"
    _settings(repo, ("declared", "omitted"))
    _stamp(durable, _payload(tmp_path / "declared", "declared"), "declared")
    _stamp(
        durable,
        _payload(
            tmp_path / "omitted",
            "omitted",
            include_reference=False,
        ),
        "omitted",
    )

    report = discover_from_settings(
        [SettingsGroup(repo, "project:fixture")],
        durable,
    )

    assert not report.valid
    assert any(
        finding.code == "missing-module-metadata"
        and finding.owner
        and finding.owner.endswith("::omitted")
        for finding in report.findings
    )


def test_settings_join_uses_provenance_not_marketplace_key(tmp_path):
    durable = tmp_path / "home"
    repo = tmp_path / "repo"
    _settings(repo, ("demo",), key="renamed")
    payload = _payload(tmp_path / "demo", "demo")
    _stamp(durable, payload, "demo", marketplace_key="original")

    report = discover_from_settings(
        [SettingsGroup(repo, "project:fixture")],
        durable,
    )

    assert report.valid
    assert report.modules[0].owner.payload_root == payload.resolve()
    assert report.modules[0].owner.provenance.marketplace_id.startswith("original--")


def test_direct_discovery_rejects_ambiguous_payload_roots(tmp_path):
    first = _installation(_payload(tmp_path / "one", "demo"), "demo")
    second = _installation(_payload(tmp_path / "two", "demo"), "demo")

    report = discover_modules([first, second])

    assert not report.valid
    assert report.modules == ()
    assert report.findings[0].code == "ambiguous-installation-owner"


def test_same_module_id_in_different_cells_is_not_a_duplicate(tmp_path):
    first = _installation(_payload(tmp_path / "one", "demo"), "demo")
    second = _installation(
        _payload(tmp_path / "two", "demo"),
        "demo",
        marketplace_key="other",
        repository="example/other-marketplace",
    )

    report = discover_modules([second, first])

    assert report.valid
    assert len({module.qualified_id for module in report.modules}) == 2
    assert all(module.qualified_id.endswith("::demo/runtime") for module in report.modules)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"state": "unknown"}, "invalid-module-metadata"),
        ({"state": "supported", "reason": "contradiction"}, "invalid-module-metadata"),
        (
            {"state": "declined", "reason": "no", "modules": []},
            "invalid-module-metadata",
        ),
    ],
)
def test_invalid_or_contradictory_declaration_fails(tmp_path, change, expected):
    payload = _payload(tmp_path / "demo", "demo")
    path = payload / "installer-readiness.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract.update(change)
    _write(path, contract)

    report = discover_modules([_installation(payload, "demo")])

    assert not report.valid
    assert report.findings[0].code == expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("platforms", ["plan9"]),
        ("classification", "recommended"),
        ("restart", "daemon"),
    ],
)
def test_invalid_module_enums_fail(tmp_path, field, value):
    module = _module("demo")
    module[field] = value
    payload = _payload(tmp_path / "demo", "demo", modules=[module])

    report = discover_modules([_installation(payload, "demo")])

    assert not report.valid
    assert report.findings[0].code == "invalid-module-metadata"


def test_unknown_payload_command_fails(tmp_path):
    module = _module("demo")
    module["readiness"]["invocations"]["windows"]["command"] = "other"
    payload = _payload(tmp_path / "demo", "demo", modules=[module])

    report = discover_modules([_installation(payload, "demo")])

    assert not report.valid
    assert "not declared" in report.findings[0].message


def test_script_only_module_does_not_require_payload_command_manifest(tmp_path):
    payload = _payload(
        tmp_path / "demo",
        "demo",
        modules=[_script_module("demo")],
        include_command_manifest=False,
    )

    report = discover_modules([_installation(payload, "demo")])

    assert report.valid
    assert report.modules[0].installer[Platform.WINDOWS].kind == "payload-script"
    assert report.modules[0].readiness[Platform.LINUX].kind == "payload-script"


def test_payload_command_requires_payload_command_manifest(tmp_path):
    payload = _payload(
        tmp_path / "demo",
        "demo",
        include_command_manifest=False,
    )

    report = discover_modules([_installation(payload, "demo")])

    assert not report.valid
    assert "payload-command requires payload-invocation.json" in (
        report.findings[0].message
    )


def test_contradictory_payload_command_shapes_match_canonical_rejection(tmp_path):
    payload = _payload(tmp_path / "agent-demo", "agent-demo")
    manifest = payload / "payload-invocation.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["plugin"] = "agent-demo"
    data["commands"] = [
        {
            "command": "agent-demo",
            "module": "agent_demo",
            "purpose": "Fixture command",
        }
    ]
    _write(manifest, data)

    with pytest.raises(ValueError, match="cannot be combined"):
        payload_generator.load_manifest(manifest)
    report = discover_modules([_installation(payload, "agent-demo")])

    assert not report.valid
    assert "cannot be combined" in report.findings[0].message


def test_payload_command_cannot_escape_through_linked_output_dir(tmp_path):
    payload = _payload(tmp_path / "demo", "demo")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "agent-demo").write_text("fixture\n", encoding="utf-8")
    (outside / "agent-demo").chmod(0o755)
    (outside / "agent-demo.ps1").write_text("fixture\n", encoding="utf-8")
    (payload / "bin" / "agent-demo").unlink()
    (payload / "bin" / "agent-demo.ps1").unlink()
    (payload / "bin").rmdir()
    try:
        (payload / "bin").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory links unavailable: {error}")

    report = discover_modules([_installation(payload, "demo")])

    assert not report.valid
    assert "not contained in the payload" in report.findings[0].message


def test_contract_version_boolean_is_invalid(tmp_path):
    payload = _payload(tmp_path / "demo", "demo")
    path = payload / "installer-readiness.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["version"] = True
    _write(path, contract)

    report = discover_modules([_installation(payload, "demo")])

    assert not report.valid
    assert "contract version must be integer 1" in report.findings[0].message


def test_depends_on_is_required(tmp_path):
    module = _module("demo")
    del module["dependsOn"]
    payload = _payload(tmp_path / "demo", "demo", modules=[module])

    report = discover_modules([_installation(payload, "demo")])

    assert not report.valid
    assert "module.dependsOn is required" in report.findings[0].message


def test_duplicate_unknown_self_and_cycle_dependencies_are_findings(tmp_path):
    duplicate = _payload(
        tmp_path / "duplicate",
        "duplicate",
        modules=[_module("duplicate"), _module("duplicate")],
    )
    unknown = _payload(
        tmp_path / "unknown",
        "unknown",
        modules=[_module("unknown", dependencies=("missing/runtime",))],
    )
    self_dep = _payload(
        tmp_path / "self",
        "self",
        modules=[_module("self", dependencies=("self/runtime",))],
    )
    left = _payload(
        tmp_path / "left",
        "left",
        modules=[_module("left", dependencies=("right/runtime",))],
    )
    right = _payload(
        tmp_path / "right",
        "right",
        modules=[_module("right", dependencies=("left/runtime",))],
    )
    report = discover_modules(
        [
            _installation(duplicate, "duplicate"),
            _installation(unknown, "unknown"),
            _installation(self_dep, "self"),
            _installation(left, "left"),
            _installation(right, "right"),
        ]
    )

    codes = {finding.code for finding in report.findings}
    assert "duplicate-module-id" in codes
    assert "unknown-dependency" in codes
    assert "self-dependency" in codes
    assert "dependency-cycle" in codes


def test_failed_prerequisite_blocks_dependent_but_not_independent(tmp_path):
    base = _payload(tmp_path / "base", "base")
    dependent = _payload(
        tmp_path / "dependent",
        "dependent",
        modules=[_module("dependent", dependencies=("base/runtime",))],
    )
    independent = _payload(tmp_path / "independent", "independent")
    report = discover_modules(
        [
            _installation(base, "base"),
            _installation(dependent, "dependent"),
            _installation(independent, "independent"),
        ]
    )
    base_id = report.modules[0].owner.provenance.marketplace_id + "::base/runtime"
    plan = build_plan(
        report,
        Platform.WINDOWS,
        {
            base_id: ReadinessResult(
                module_id="base/runtime",
                state=ReadinessState.FAILED,
            )
        },
    )
    states = {step.module.module_id: step.state for step in plan.steps}

    assert states == {
        "base/runtime": PlanState.FAILED,
        "dependent/runtime": PlanState.BLOCKED,
        "independent/runtime": PlanState.PLANNED,
    }


def test_configuration_empty_semantics_control_dependency_blocking(tmp_path):
    base_module = _module("base", configuration_empty="unsatisfied")
    base = _payload(tmp_path / "base", "base", modules=[base_module])
    dependent = _payload(
        tmp_path / "dependent",
        "dependent",
        modules=[_module("dependent", dependencies=("base/runtime",))],
    )
    report = discover_modules(
        [_installation(base, "base"), _installation(dependent, "dependent")]
    )
    base_id = report.modules[0].owner.provenance.marketplace_id + "::base/runtime"
    plan = build_plan(
        report,
        "linux",
        {
            base_id: ReadinessResult(
                module_id="base/runtime",
                state=ReadinessState.CONFIGURATION_EMPTY,
            )
        },
    )

    assert plan.steps[0].state is PlanState.CONFIGURATION_EMPTY
    assert plan.steps[1].state is PlanState.BLOCKED
    assert plan.steps[0].module.configuration_empty is ConfigurationEmpty.UNSATISFIED


def test_plan_is_deterministic_and_marks_unsupported_platform(tmp_path):
    zeta = _payload(tmp_path / "zeta", "zeta")
    alpha = _payload(tmp_path / "alpha", "alpha")
    report = discover_modules(
        [_installation(zeta, "zeta"), _installation(alpha, "alpha")]
    )

    first = build_plan(report, "macos")
    second = build_plan(report, "macos")

    assert [step.module.module_id for step in first.steps] == [
        "alpha/runtime",
        "zeta/runtime",
    ]
    assert first == second
    assert all(step.state is PlanState.UNSUPPORTED for step in first.steps)


def test_readiness_parser_accepts_only_contract_states():
    result = parse_readiness(
        {
            "schema": "copilot-extensions.module-readiness",
            "version": 1,
            "module": "demo/runtime",
            "state": "configuration-empty",
            "detail": "No configured instances.",
        }
    )
    assert result.state is ReadinessState.CONFIGURATION_EMPTY

    with pytest.raises(ValueError, match="readiness state"):
        parse_readiness(
            {
                "schema": "copilot-extensions.module-readiness",
                "version": 1,
                "module": "demo/runtime",
                "state": "skipped",
            }
        )

    with pytest.raises(ValueError, match="schema/version"):
        parse_readiness(
            {
                "schema": "copilot-extensions.module-readiness",
                "version": True,
                "module": "demo/runtime",
                "state": "ready",
            }
        )


def test_readiness_parser_rejects_non_string_mapping_keys_as_value_error():
    with pytest.raises(ValueError, match="property names must be strings"):
        parse_readiness(
            {
                "schema": "copilot-extensions.module-readiness",
                "version": 1,
                "module": "demo/runtime",
                "state": "ready",
                1: "invalid",
            }
        )


def test_invalid_enabled_cell_is_reported_by_fingerprint_prefix(tmp_path):
    durable = tmp_path / "home"
    repo = tmp_path / "repo"
    _settings(repo, ("demo",))
    normalized = normalize_source(
        {"source": "github", "repo": "example/marketplace"}
    )
    identity = source_identity(normalized, "example")
    cell = durable / "marketplaces" / identity["marketplaceId"]
    _write(cell / "namespace.json", {})

    report = discover_from_settings(
        [SettingsGroup(repo, "project:fixture")],
        durable,
    )

    assert not report.valid
    assert any(
        finding.code == "invalid-installation-cell"
        for finding in report.findings
    )
