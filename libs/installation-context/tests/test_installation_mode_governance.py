"""Tests for non-operative installation-mode governance."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
PYTHON_SCRIPT = LIB / "installation_context.py"
POSIX_SCRIPT = LIB / "installation-context.sh"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
SOURCE_FIXTURES = LIB / "fixtures" / "source-identities.json"
GOVERNANCE_FIXTURES = LIB / "fixtures" / "installation-mode-governance.json"
PLUGIN_ID = "agent-example"
ALL_RUNNERS = [
    "python",
    "posix",
    pytest.param(
        "powershell",
        marks=pytest.mark.skipif(
            POWERSHELL is None, reason="PowerShell is not installed"
        ),
    ),
]
EXHAUSTIVE_ADAPTERS = (
    os.environ.get("INSTALLATION_CONTEXT_EXHAUSTIVE_ADAPTERS") == "1"
)
REFERENCE_RUNNERS = (
    ALL_RUNNERS
    if EXHAUSTIVE_ADAPTERS
    else ["python"]
)


def _supported_bash() -> str | None:
    if os.name == "nt":
        return None
    candidate = shutil.which("bash")
    if candidate is None:
        return None
    try:
        result = subprocess.run(
            [
                candidate,
                "--noprofile",
                "--norc",
                "-c",
                "((BASH_VERSINFO[0] > 4 || "
                "(BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4)))",
            ],
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return candidate if result.returncode == 0 else None


BASH = _supported_bash()


def _load_module():
    spec = importlib.util.spec_from_file_location("installation_context", PYTHON_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _source_vector(index: int = 0) -> dict[str, object]:
    return json.loads(SOURCE_FIXTURES.read_text(encoding="utf-8"))["vectors"][index]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _environment() -> dict[str, object]:
    if os.name == "nt":
        profile = Path(os.environ["USERPROFILE"]).resolve()
        platform = "windows"
    else:
        import pwd

        profile = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
        platform = "posix"
    return {
        "platform": platform,
        "homeRealPath": str(profile),
        "wslDistro": (
            None
            if platform == "windows"
            else os.environ.get("WSL_DISTRO_NAME") or None
        ),
    }


def _api_environment(profile: Path) -> dict[str, object]:
    return {
        "platform": "posix",
        "homeRealPath": str(profile.resolve()),
        "wslDistro": None,
    }


def _cell_layout(
    tmp_path: Path,
    *,
    vector_index: int = 0,
    namespace_generation: int = 1,
    install_generation: int = 2,
) -> dict[str, object]:
    vector = _source_vector(vector_index)
    normalized = vector["normalized"]
    assert isinstance(normalized, dict)
    marketplace_id = str(vector["marketplaceId"])
    durable = tmp_path / "durable"
    cell = durable / "marketplaces" / marketplace_id
    plugin_root = cell / "plugins" / PLUGIN_ID
    payload = tmp_path / f"payload-{vector_index}"
    payload.mkdir(parents=True)
    namespace = cell / "namespace.json"
    install = plugin_root / "install.json"
    _write_json(
        namespace,
        {
            "schema": "copilot-extensions.marketplace-namespace",
            "version": 1,
            "marketplaceId": marketplace_id,
            "source": {
                "kind": normalized["kind"],
                "canonical": normalized["canonical"],
                "ref": normalized["ref"],
                "fingerprint": f"sha256:{vector['sha256']}",
            },
            "locators": [],
            "generation": namespace_generation,
            "state": "active",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
    )
    _write_json(
        install,
        {
            "schema": "copilot-extensions.plugin-installation",
            "version": 1,
            "marketplaceId": marketplace_id,
            "pluginId": PLUGIN_ID,
            "pluginRoot": str(plugin_root.resolve()),
            "namespaceReceipt": str(namespace.resolve()),
            "payload": {
                "root": str(payload.resolve()),
                "version": "1.0.0",
                "origin": "explicit",
            },
            "roots": {
                "versions": "versions",
                "snapshots": "snapshots",
                "state": "state",
                "run": "run",
                "logs": "logs",
                "cache": "cache",
                "launchers": "launchers",
            },
            "generation": install_generation,
            "state": "active",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
    )
    return {
        "vector": vector,
        "marketplace_id": marketplace_id,
        "durable": durable,
        "cell": cell,
        "plugin_root": plugin_root,
        "payload": payload,
        "namespace": namespace,
        "install": install,
        "namespace_generation": namespace_generation,
        "install_generation": install_generation,
    }


def _activation(
    layout: dict[str, object],
    *,
    mode: str = "namespaced",
    state: str = "active",
    environment: dict[str, object] | None = None,
    namespace_generation: int | None = None,
    install_generation: int | None = None,
    generation: int = 3,
) -> Path:
    plugin_root = Path(layout["plugin_root"])
    activation = plugin_root / "installation-activation.json"
    _write_json(
        activation,
        {
            "schema": "copilot-extensions.installation-activation",
            "version": 1,
            "marketplaceId": layout["marketplace_id"],
            "pluginId": PLUGIN_ID,
            "mode": mode,
            "state": state,
            "environment": environment or _environment(),
            "context": str(Path(layout["install"]).resolve()),
            "namespaceGeneration": (
                layout["namespace_generation"]
                if namespace_generation is None
                else namespace_generation
            ),
            "installGeneration": (
                layout["install_generation"]
                if install_generation is None
                else install_generation
            ),
            "generation": generation,
            "legacy": {
                "disposition": "absent" if mode == "namespaced" else "restored",
                "probe": {
                    "declared": True,
                    "result": "absent",
                    "checkedAt": "2026-01-01T00:00:00Z",
                },
            },
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
    )
    return activation


def _policy(enabled: bool, marketplace_id: str, *, plugin_enabled=None) -> dict:
    marketplace: dict[str, object] = {"enabled": enabled}
    if plugin_enabled is not None:
        marketplace["plugins"] = {PLUGIN_ID: {"enabled": plugin_enabled}}
    return {
        "schema": "copilot-extensions.installation-mode",
        "version": 1,
        "installationMode": {
            "enabled": not enabled,
            "marketplaces": {marketplace_id: marketplace},
        },
    }


def _api_arguments(
    layout: dict[str, object],
    profile: Path,
    legacy: Path,
    **overrides,
) -> dict[str, object]:
    arguments: dict[str, object] = {
        "payload_root": layout["payload"],
        "plugin_id": PLUGIN_ID,
        "durable_home": layout["durable"],
        "legacy_root": legacy,
        "source_descriptor": layout["vector"]["descriptor"],
        "marketplace_key": layout["vector"]["marketplaceKey"],
        "os_profile": profile,
        "platform": "posix",
        "wsl_distro": None,
        "current_time": "2026-01-01T00:30:00Z",
        "host": "test-host.example",
        "pid_is_live": lambda pid: pid == 1234,
        "environment": {},
    }
    arguments.update(overrides)
    return arguments


def _cli_arguments(
    layout: dict[str, object],
    legacy: Path,
    *,
    action: str,
    probe: dict[str, object] | None = None,
    policy_path: Path | None = None,
) -> list[str]:
    arguments = [
        action,
        "--payload-root",
        str(layout["payload"]),
        "--plugin-id",
        PLUGIN_ID,
        "--source-json",
        json.dumps(layout["vector"]["descriptor"], separators=(",", ":")),
        "--marketplace-key",
        str(layout["vector"]["marketplaceKey"]),
        "--durable-home",
        str(layout["durable"]),
        "--legacy-root",
        str(legacy),
    ]
    if probe is not None:
        arguments.extend(
            [
                "--legacy-probe-json",
                json.dumps(probe, separators=(",", ":")),
            ]
        )
    if policy_path is not None:
        arguments.extend(["--policy-path", str(policy_path)])
    return arguments


def _activation_cas_arguments(
    layout: dict[str, object],
    legacy: Path,
    *,
    expected_marketplace_id: str | None = None,
    expected_plugin_id: str = PLUGIN_ID,
    expected_namespace_generation: int | None = None,
    expected_install_generation: int | None = None,
    expected_activation_generation: int = 0,
    mode: str = "namespaced",
    state: str = "active",
    disposition: str = "absent",
    probe: dict[str, object] | None = None,
    legacy_root: str | Path | None = None,
) -> list[str]:
    return [
        "activation-cas",
        "--context",
        str(layout["install"]),
        "--durable-home",
        str(layout["durable"]),
        "--expected-marketplace-id",
        (
            str(layout["marketplace_id"])
            if expected_marketplace_id is None
            else expected_marketplace_id
        ),
        "--expected-plugin-id",
        expected_plugin_id,
        "--expected-namespace-generation",
        str(
            layout["namespace_generation"]
            if expected_namespace_generation is None
            else expected_namespace_generation
        ),
        "--expected-install-generation",
        str(
            layout["install_generation"]
            if expected_install_generation is None
            else expected_install_generation
        ),
        "--expected-activation-generation",
        str(expected_activation_generation),
        "--activation-mode",
        mode,
        "--activation-state",
        state,
        "--legacy-disposition",
        disposition,
        "--legacy-root",
        str(legacy if legacy_root is None else legacy_root),
        "--legacy-probe-json",
        json.dumps(
            probe
            or {
                "declared": True,
                "result": "absent",
                "checkedAt": "2026-01-01T00:00:00Z",
            },
            separators=(",", ":"),
        ),
    ]


def _runner_command(name: str, arguments: list[str]) -> list[str]:
    if name == "python":
        return [sys.executable, str(PYTHON_SCRIPT), *arguments]
    if name == "posix":
        if BASH is None:
            pytest.skip("Bash runner is unavailable")
        return [BASH, str(POSIX_SCRIPT), *arguments]
    assert POWERSHELL is not None
    mapping = {
        "--payload-root": "-PayloadRoot",
        "--plugin-id": "-PluginId",
        "--source-json": "-SourceJson",
        "--marketplace-key": "-MarketplaceKey",
        "--durable-home": "-DurableHome",
        "--legacy-root": "-LegacyRoot",
        "--legacy-probe-json": "-LegacyProbeJson",
        "--legacy-probe-file": "-LegacyProbeFile",
        "--policy-path": "-PolicyPath",
        "--context": "-Context",
        "--expected-marketplace-id": "-ExpectedMarketplaceId",
        "--expected-plugin-id": "-ExpectedPluginId",
        "--expected-payload-root": "-ExpectedPayloadRoot",
        "--expected-cell-root": "-ExpectedCellRoot",
        "--expected-namespace-generation": "-ExpectedNamespaceGeneration",
        "--expected-install-generation": "-ExpectedInstallGeneration",
        "--expected-activation-generation": "-ExpectedActivationGeneration",
        "--activation-mode": "-ActivationMode",
        "--activation-state": "-ActivationState",
        "--legacy-disposition": "-LegacyDisposition",
    }
    converted = [arguments[0]]
    converted.extend(mapping.get(value, value) for value in arguments[1:])
    return [POWERSHELL, "-NoProfile", "-File", str(LIB / "installation-context.ps1"), *converted]


def _run(name: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    return subprocess.run(
        _runner_command(name, arguments),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


def _snapshot(root: Path) -> dict[str, tuple[str, int, str]]:
    snapshot: dict[str, tuple[str, int, str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[relative] = ("directory", 0, "")
        elif path.is_file():
            content = path.read_bytes()
            snapshot[relative] = (
                "file",
                len(content),
                hashlib.sha256(content).hexdigest(),
            )
        else:
            snapshot[relative] = ("other", 0, "")
    return snapshot


def test_governance_fixture_corpus_covers_required_states() -> None:
    fixtures = json.loads(GOVERNANCE_FIXTURES.read_text(encoding="utf-8"))
    assert fixtures["schema"] == "copilot-extensions.installation-governance-fixtures"
    assert {item["name"] for item in fixtures["policies"]} >= {
        "global-false",
        "global-true",
        "marketplace-overrides-global",
        "plugin-overrides-marketplace",
        "unsupported-version",
        "invalid-known-field",
    }
    assert {item["name"] for item in fixtures["tombstones"]} >= {
        "valid-same-cell",
        "valid-other-cell",
        "missing-destination",
        "unreadable-destination",
        "noncanonical-destination",
        "mismatched-destination",
        "foreign-destination",
        "stale-generation",
        "deactivated-destination",
        "non-file-tombstone",
    }
    assert fixtures["statusPrecedence"][0] == "invalid"
    assert fixtures["statusPrecedence"][-1] == "ready"


def test_python_policy_precedence_and_preactivation_semantics(tmp_path: Path) -> None:
    module = _load_module()
    layout = _cell_layout(tmp_path)
    profile = tmp_path / "profile"
    legacy = tmp_path / "legacy"
    profile.mkdir()
    legacy.mkdir()
    policy_path = profile / ".copilot-extensions" / "installation-mode.json"
    _write_json(
        policy_path,
        _policy(True, str(layout["marketplace_id"]), plugin_enabled=False),
    )
    result = module.resolve_installation_mode(
        **_api_arguments(layout, profile, legacy)
    )
    assert result["policy"] == {
        "path": str(policy_path.resolve()),
        "authoritative": True,
        "state": "valid",
        "scope": "plugin",
        "enabled": False,
        "reason": "policy-plugin-false",
    }
    assert result["desiredMode"] == "legacy"
    assert result["reason"] == "policy-plugin-false"

    _write_json(policy_path, _policy(True, str(layout["marketplace_id"])))
    clean_probe = {
        "declared": True,
        "result": "absent",
        "checkedAt": "2026-01-01T00:00:00Z",
    }
    clean = module.resolve_installation_mode(
        **_api_arguments(layout, profile, legacy, legacy_probe=clean_probe)
    )
    assert (clean["desiredMode"], clean["actualMode"]) == ("namespaced", "legacy")
    assert (clean["status"], clean["reason"]) == ("ready", "activation-required")
    refused = module.probe_legacy_entrypoint(
        **_api_arguments(layout, profile, legacy, legacy_probe=clean_probe)
    )
    assert refused["allowMutation"] is False
    assert refused["probeReason"] == "namespaced-requested"

    migration = module.probe_legacy_entrypoint(
        **_api_arguments(
            layout,
            profile,
            legacy,
            legacy_probe={
                "declared": False,
                "result": "unknown",
                "checkedAt": None,
            },
        )
    )
    assert migration["status"] == "migration-required"
    assert migration["allowMutation"] is True
    assert migration["probeReason"] == "migration-required"


@pytest.mark.skipif(os.name == "nt", reason="POSIX environment fixture")
def test_activation_validation_and_policy_invalid_preserve_actual_root(
    tmp_path: Path,
) -> None:
    module = _load_module()
    layout = _cell_layout(tmp_path)
    profile = tmp_path / "profile"
    legacy = tmp_path / "legacy"
    profile.mkdir()
    legacy.mkdir()
    activation = _activation(layout, environment=_api_environment(profile))
    policy_path = profile / ".copilot-extensions" / "installation-mode.json"
    _write_json(
        policy_path,
        {
            "schema": "copilot-extensions.installation-mode",
            "version": 1,
            "installationMode": {"enabled": "invalid"},
        },
    )
    invalid = module.resolve_installation_mode(
        **_api_arguments(layout, profile, legacy)
    )
    assert invalid["status"] == "invalid"
    assert invalid["reason"] == "policy-invalid"
    assert invalid["actualMode"] == "namespaced"
    assert invalid["runtimeRoot"] == str(Path(layout["plugin_root"]).resolve())
    assert invalid["activation"] == str(activation.resolve())

    _write_json(policy_path, _policy(True, str(layout["marketplace_id"])))
    _activation(
        layout,
        environment=_api_environment(profile),
        install_generation=1,
    )
    stale = module.resolve_installation_mode(
        **_api_arguments(layout, profile, legacy)
    )
    assert stale["status"] == "revalidation-required"
    assert stale["actualMode"] == "namespaced"
    assert stale["runtimeRoot"] == str(Path(layout["plugin_root"]).resolve())

    foreign_environment = _api_environment(profile)
    foreign_environment["wslDistro"] = "OtherDistro"
    _activation(layout, environment=foreign_environment)
    foreign = module.resolve_installation_mode(
        **_api_arguments(layout, profile, legacy)
    )
    assert foreign["status"] == "foreign-environment"
    assert foreign["actualMode"] is None
    assert foreign["runtimeRoot"] is None

    _activation(layout, environment=_api_environment(profile))
    _write_json(
        policy_path,
        {
            "schema": "copilot-extensions.installation-mode",
            "version": 1,
            "installationMode": {"enabled": False},
        },
    )
    deactivation = module.resolve_installation_mode(
        **_api_arguments(layout, profile, legacy)
    )
    assert deactivation["status"] == "deactivation-required"
    assert deactivation["actualMode"] == "namespaced"

    _activation(
        layout,
        mode="legacy",
        state="deactivated",
        environment=_api_environment(profile),
    )
    _write_json(policy_path, _policy(True, str(layout["marketplace_id"])))
    reactivation = module.resolve_installation_mode(
        **_api_arguments(layout, profile, legacy)
    )
    assert reactivation["status"] == "migration-required"
    assert reactivation["actualMode"] == "legacy"


@pytest.mark.skipif(os.name == "nt", reason="POSIX environment fixture")
def test_tombstone_validation_blocks_mutation_and_orphans_fail_closed(
    tmp_path: Path,
) -> None:
    module = _load_module()
    current = _cell_layout(tmp_path, vector_index=0)
    destination = _cell_layout(tmp_path, vector_index=1)
    profile = tmp_path / "profile"
    legacy = tmp_path / "legacy"
    profile.mkdir()
    legacy.mkdir()
    destination_activation = _activation(
        destination,
        environment=_api_environment(profile),
        generation=7,
    )
    tombstone = legacy / ".installation-ownership.json"
    _write_json(
        tombstone,
        {
            "schema": "copilot-extensions.legacy-installation-ownership",
            "version": 1,
            "marketplaceId": destination["marketplace_id"],
            "pluginId": PLUGIN_ID,
            "activation": {
                "path": str(destination_activation.resolve()),
                "generation": 7,
            },
            "environment": _api_environment(profile),
            "transferredAt": "2026-01-01T00:00:00Z",
        },
    )
    valid = module.probe_legacy_entrypoint(
        **_api_arguments(current, profile, legacy)
    )
    assert valid["legacy"]["disposition"] == "owned-by-other-cell"
    assert valid["allowMutation"] is False
    assert valid["probeReason"] == "legacy-owned-by-other-cell"

    destination_activation.unlink()
    orphaned = module.resolve_installation_mode(
        **_api_arguments(current, profile, legacy)
    )
    assert orphaned["status"] == "orphaned-transfer"
    assert orphaned["reason"] == "orphaned-transfer"


def test_python_api_rejects_non_string_mapping_keys_with_domain_error(
    tmp_path: Path,
) -> None:
    module = _load_module()
    layout = _cell_layout(tmp_path)
    profile = tmp_path / "profile"
    legacy = tmp_path / "legacy"
    profile.mkdir()
    legacy.mkdir()

    with pytest.raises(
        module.InstallationContextError,
        match="JSON object property names must be strings",
    ):
        module.resolve_installation_mode(
            **_api_arguments(
                layout,
                profile,
                legacy,
                legacy_probe={
                    1: "invalid",
                    "declared": False,
                    "result": "unknown",
                    "checkedAt": None,
                },
            )
        )


@pytest.mark.parametrize(
    "runner",
    ALL_RUNNERS,
)
@pytest.mark.parametrize(
    ("entry_name", "action", "expected_status", "expected_returncode"),
    [
        ("policy", "status", "invalid", 0),
        ("activation", "status", "invalid", 0),
        ("tombstone", "probe-legacy", "orphaned-transfer", 3),
    ],
)
def test_non_file_governance_evidence_fails_closed_across_runners(
    tmp_path: Path,
    runner: str,
    entry_name: str,
    action: str,
    expected_status: str,
    expected_returncode: int,
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    arguments = _cli_arguments(layout, legacy, action=action)

    if entry_name == "policy":
        policy_path = tmp_path / "policy.json"
        policy_path.mkdir()
        arguments.extend(["--policy-path", str(policy_path)])
    elif entry_name == "activation":
        (Path(layout["plugin_root"]) / "installation-activation.json").mkdir()
    else:
        (legacy / ".installation-ownership.json").mkdir()

    result = _run(runner, arguments)

    assert result.returncode == expected_returncode, result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == expected_status
    if entry_name == "tombstone":
        assert value["legacy"]["disposition"] == "orphaned-transfer"
        assert value["allowMutation"] is False


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
def test_unactivated_context_receipt_is_not_reported_as_activation(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()

    result = _run(
        runner,
        _cli_arguments(layout, legacy, action="status"),
    )

    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["actualMode"] == "legacy"
    assert value["context"] is None
    assert value["installGeneration"] is None


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
def test_dangling_plugin_maintenance_marker_fails_closed(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    marker = Path(layout["plugin_root"]) / "maintenance"
    marker.symlink_to(marker.with_name("missing-maintenance-target"))

    result = _run(
        runner,
        _cli_arguments(layout, legacy, action="probe-legacy"),
    )

    assert result.returncode == 3, result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == "maintenance-blocked"
    assert value["maintenance"]["state"] == "stale"
    assert value["maintenance"]["scope"] == "plugin"
    assert value["allowMutation"] is False
    assert value["probeReason"] == "maintenance-stale"


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
def test_revalidation_preserves_namespaced_desired_mode_across_runners(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    _activation(layout, install_generation=1)
    policy_path = tmp_path / "policy.json"
    _write_json(policy_path, _policy(False, str(layout["marketplace_id"])))

    result = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="status",
            policy_path=policy_path,
        ),
    )

    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == "revalidation-required"
    assert value["actualMode"] == "namespaced"
    assert value["desiredMode"] == "namespaced"


@pytest.mark.skipif(os.name == "nt", reason="Windows paths cannot contain newlines")
@pytest.mark.parametrize(
    "runner",
    ALL_RUNNERS,
)
def test_namespaced_paths_with_newlines_round_trip_across_runners(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path / "line\nbreak")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    _activation(layout)

    result = _run(
        runner,
        _cli_arguments(layout, legacy, action="status"),
    )

    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == "deactivation-required"
    assert value["context"] == str(Path(layout["install"]).resolve())
    assert value["runtimeRoot"] == str(Path(layout["plugin_root"]).resolve())


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
@pytest.mark.skipif(os.name == "nt", reason="WSL identity requires a POSIX host")
def test_empty_wsl_identity_is_foreign_across_runners(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    environment = _environment()
    environment["wslDistro"] = ""
    _activation(layout, environment=environment)

    result = _run(
        runner,
        _cli_arguments(layout, legacy, action="status"),
    )

    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == "foreign-environment"
    assert value["reason"] == "foreign-environment"


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
def test_missing_context_is_structured_across_runners(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    missing_context = tmp_path / "missing-context" / "install.json"
    arguments = _cli_arguments(layout, legacy, action="status")
    arguments.extend(["--context", str(missing_context)])

    result = _run(runner, arguments)

    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == "invalid"
    assert value["reason"] == "context-invalid"


@pytest.mark.parametrize(
    "scenario",
    [
        "missing",
        "malformed",
        "noncanonical",
        "generation-mismatch",
        "environment-mismatch",
        "generation-stale",
        "deactivated",
    ],
)
def test_invalid_tombstone_destinations_are_orphaned(
    tmp_path: Path, scenario: str
) -> None:
    module = _load_module()
    current = _cell_layout(tmp_path, vector_index=0)
    destination = _cell_layout(tmp_path, vector_index=1)
    profile = tmp_path / "profile"
    legacy = tmp_path / "legacy"
    profile.mkdir()
    legacy.mkdir()
    destination_environment = _api_environment(profile)
    mode = "legacy" if scenario == "deactivated" else "namespaced"
    state = "deactivated" if scenario == "deactivated" else "active"
    install_generation = 1 if scenario == "generation-stale" else None
    if scenario == "environment-mismatch":
        destination_environment = dict(destination_environment)
        destination_environment["wslDistro"] = "OtherDistro"
    destination_activation = _activation(
        destination,
        mode=mode,
        state=state,
        environment=destination_environment,
        install_generation=install_generation,
        generation=7,
    )
    if scenario == "missing":
        destination_activation.unlink()
    elif scenario == "malformed":
        destination_activation.write_text("{", encoding="utf-8")
    activation_path = destination_activation
    if scenario == "noncanonical":
        activation_path = destination_activation.with_name("activation-copy.json")
        activation_path.write_bytes(destination_activation.read_bytes())
    pinned_generation = 8 if scenario == "generation-mismatch" else 7
    _write_json(
        legacy / ".installation-ownership.json",
        {
            "schema": "copilot-extensions.legacy-installation-ownership",
            "version": 1,
            "marketplaceId": destination["marketplace_id"],
            "pluginId": PLUGIN_ID,
            "activation": {
                "path": str(activation_path.resolve()),
                "generation": pinned_generation,
            },
            "environment": _api_environment(profile),
            "transferredAt": "2026-01-01T00:00:00Z",
        },
    )
    result = module.resolve_installation_mode(
        **_api_arguments(current, profile, legacy)
    )
    assert result["status"] == "orphaned-transfer"
    assert result["legacy"]["disposition"] == "orphaned-transfer"


def test_maintenance_precedence_and_stale_sidecar(tmp_path: Path) -> None:
    module = _load_module()
    layout = _cell_layout(tmp_path)
    profile = tmp_path / "profile"
    legacy = tmp_path / "legacy"
    profile.mkdir()
    legacy.mkdir()
    marker = profile / ".copilot-extensions" / "maintenance"
    marker.parent.mkdir()
    marker.touch()
    _write_json(
        marker.with_suffix(".json"),
        {
            "owner": "test",
            "host": "test-host.example",
            "pid": 1234,
            "reason": "maintenance",
            "enteredAt": "2026-01-01T00:00:00Z",
            "expectedUntil": "2026-01-01T01:00:00Z",
        },
    )
    active = module.resolve_installation_mode(
        **_api_arguments(layout, profile, legacy)
    )
    assert active["status"] == "maintenance-blocked"
    assert active["reason"] == "maintenance-active"
    assert active["maintenance"]["scope"] == "user"

    _write_json(
        profile / ".copilot-extensions" / "installation-mode.json",
        {
            "schema": "copilot-extensions.installation-mode",
            "version": 1,
            "installationMode": {"enabled": "invalid"},
        },
    )
    invalid_wins = module.resolve_installation_mode(
        **_api_arguments(layout, profile, legacy)
    )
    assert invalid_wins["status"] == "invalid"
    assert invalid_wins["maintenance"]["state"] == "active"

    marker.with_suffix(".json").write_text("{", encoding="utf-8")
    stale = module.resolve_installation_mode(
        **_api_arguments(
            layout,
            profile,
            legacy,
            policy_path=tmp_path / "missing-policy.json",
        )
    )
    assert stale["status"] == "maintenance-blocked"
    assert stale["reason"] == "maintenance-stale"


@pytest.mark.parametrize(
    "runner",
    ALL_RUNNERS,
)
def test_status_and_probe_cli_parity_and_read_only(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    policy = tmp_path / "policy.json"
    _write_json(policy, _policy(True, str(layout["marketplace_id"])))
    probe = {"declared": True, "result": "absent", "checkedAt": None}
    before = _snapshot(tmp_path)
    status = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="status",
            probe=probe,
            policy_path=policy,
        ),
    )
    assert status.returncode == 0, status.stderr
    value = json.loads(status.stdout)
    assert value["schema"] == "copilot-extensions.installation-resolution"
    assert value["policy"] == {
        "path": str(policy.resolve()),
        "authoritative": False,
        "state": "valid",
        "scope": "marketplace",
        "enabled": True,
        "reason": "policy-injected-non-authoritative",
    }
    assert value["status"] == "ready"
    assert value["desiredMode"] == "legacy"
    assert set(value["maintenance"]) == {"state", "scope", "marker", "sidecar"}

    allowed = _run(
        runner,
        _cli_arguments(layout, legacy, action="probe-legacy"),
    )
    assert allowed.returncode == 0, allowed.stderr
    decision = json.loads(allowed.stdout)
    assert decision["allowMutation"] is True
    assert decision["probeReason"] == "legacy-active"
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "runner",
    ALL_RUNNERS,
)
def test_policy_precedence_evidence_matches_across_runners(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    marketplace_id = str(layout["marketplace_id"])
    cases = [
        (
            "default",
            {
                "schema": "copilot-extensions.installation-mode",
                "version": 1,
            },
            ("valid", "default", False, "policy-injected-non-authoritative"),
        ),
        (
            "global",
            {
                "schema": "copilot-extensions.installation-mode",
                "version": 1,
                "installationMode": {"enabled": True},
            },
            ("valid", "global", True, "policy-injected-non-authoritative"),
        ),
        (
            "marketplace",
            {
                "schema": "copilot-extensions.installation-mode",
                "version": 1,
                "installationMode": {
                    "enabled": True,
                    "marketplaces": {marketplace_id: {"enabled": False}},
                },
            },
            ("valid", "marketplace", False, "policy-injected-non-authoritative"),
        ),
        (
            "plugin",
            {
                "schema": "copilot-extensions.installation-mode",
                "version": 1,
                "installationMode": {
                    "enabled": False,
                    "marketplaces": {
                        marketplace_id: {
                            "enabled": False,
                            "plugins": {PLUGIN_ID: {"enabled": True}},
                        }
                    },
                },
            },
            ("valid", "plugin", True, "policy-injected-non-authoritative"),
        ),
        (
            "unsupported-version",
            {
                "schema": "copilot-extensions.installation-mode",
                "version": 2,
                "installationMode": {"enabled": True},
            },
            ("unsupported", "default", None, "policy-version-unsupported"),
        ),
        (
            "invalid",
            {
                "schema": "copilot-extensions.installation-mode",
                "version": 1,
                "installationMode": {"enabled": "true"},
            },
            ("invalid", "default", None, "policy-invalid"),
        ),
    ]
    if runner != "python" and not EXHAUSTIVE_ADAPTERS:
        cases = [
            case
            for case in cases
            if case[0] in {"default", "unsupported-version", "invalid"}
        ]
    for name, document, expected in cases:
        policy = tmp_path / f"policy-{name}.json"
        _write_json(policy, document)
        result = _run(
            runner,
            _cli_arguments(
                layout,
                legacy,
                action="status",
                policy_path=policy,
            ),
        )
        assert result.returncode == 0, result.stderr
        evidence = json.loads(result.stdout)["policy"]
        assert (
            evidence["state"],
            evidence["scope"],
            evidence["enabled"],
            evidence["reason"],
        ) == expected


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
def test_active_namespaced_cli_is_visible_and_probe_refuses(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    activation = _activation(layout)
    injected_policy = tmp_path / "missing-policy.json"
    status = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="status",
            policy_path=injected_policy,
        ),
    )
    assert status.returncode == 0, status.stderr
    value = json.loads(status.stdout)
    assert value["status"] == "ready"
    assert value["reason"] == "namespaced-active"
    assert value["desiredMode"] == "namespaced"
    assert value["actualMode"] == "namespaced"
    assert value["runtimeRoot"] == str(Path(layout["plugin_root"]).resolve())
    assert value["activation"] == str(activation.resolve())

    refused = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="probe-legacy",
            policy_path=injected_policy,
        ),
    )
    assert refused.returncode == 3, refused.stderr
    decision = json.loads(refused.stdout)
    assert decision["allowMutation"] is False
    assert decision["probeReason"] == "namespaced-active"


@pytest.mark.parametrize(
    "runner",
    ALL_RUNNERS,
)
def test_malformed_invocation_probe_is_exit_one(tmp_path: Path, runner: str) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    arguments = _cli_arguments(layout, legacy, action="status")
    arguments.extend(["--legacy-probe-json", "{"])
    result = _run(runner, arguments)
    assert result.returncode == 1
    assert not result.stdout


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
def test_probe_input_requires_checked_at_and_one_source(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    missing_checked = _cli_arguments(layout, legacy, action="status")
    missing_checked.extend(
        [
            "--legacy-probe-json",
            '{"declared":true,"result":"absent"}',
        ]
    )
    missing_result = _run(runner, missing_checked)
    assert missing_result.returncode == 1
    assert not missing_result.stdout

    probe_file = tmp_path / "probe.json"
    _write_json(
        probe_file,
        {"declared": False, "result": "unknown", "checkedAt": None},
    )
    duplicate_source = _cli_arguments(layout, legacy, action="status")
    duplicate_source.extend(
        [
            "--legacy-probe-json",
            '{"declared":false,"result":"unknown","checkedAt":null}',
            "--legacy-probe-file",
            str(probe_file),
        ]
    )
    duplicate_result = _run(runner, duplicate_source)
    assert duplicate_result.returncode == 1
    assert not duplicate_result.stdout

    relative_policy = _cli_arguments(layout, legacy, action="status")
    relative_policy.extend(["--policy-path", "relative-policy.json"])
    relative_result = _run(runner, relative_policy)
    assert relative_result.returncode == 1
    assert not relative_result.stdout


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
def test_expected_on_disk_corruption_is_structured(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    policy = tmp_path / "policy.json"
    policy.write_text("{", encoding="utf-8")
    invalid_policy = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="status",
            policy_path=policy,
        ),
    )
    assert invalid_policy.returncode == 0, invalid_policy.stderr
    assert json.loads(invalid_policy.stdout)["status"] == "invalid"

    policy.unlink()
    activation = Path(layout["plugin_root"]) / "installation-activation.json"
    activation.write_text("{", encoding="utf-8")
    invalid_activation = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="status",
            policy_path=policy,
        ),
    )
    assert invalid_activation.returncode == 0, invalid_activation.stderr
    assert json.loads(invalid_activation.stdout)["status"] == "invalid"

    activation_value = {
        "schema": "copilot-extensions.installation-activation",
        "version": 1,
        "marketplaceId": layout["marketplace_id"],
        "pluginId": PLUGIN_ID,
        "mode": "namespaced",
        "state": "active",
        "environment": _environment(),
        "context": str(Path(layout["install"]).resolve()),
        "namespaceGeneration": layout["namespace_generation"],
        "installGeneration": layout["install_generation"],
        "generation": 3,
        "legacy": {
            "disposition": "absent",
            "probe": {"declared": True, "result": "absent"},
        },
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }
    _write_json(activation, activation_value)
    missing_checked = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="status",
            policy_path=policy,
        ),
    )
    assert missing_checked.returncode == 0, missing_checked.stderr
    missing_value = json.loads(missing_checked.stdout)
    assert missing_value["status"] == "invalid"
    assert missing_value["reason"] == "activation-invalid"

    activation_value["legacy"]["probe"]["checkedAt"] = None
    del activation_value["environment"]["wslDistro"]
    _write_json(activation, activation_value)
    missing_environment_field = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="status",
            policy_path=policy,
        ),
    )
    assert missing_environment_field.returncode == 0
    missing_environment_value = json.loads(missing_environment_field.stdout)
    assert missing_environment_value["status"] == "invalid"
    assert missing_environment_value["reason"] == "activation-invalid"

    activation.unlink()
    _write_json(
        legacy / ".installation-ownership.json",
        {
            "schema": "copilot-extensions.legacy-installation-ownership",
            "version": 1,
            "marketplaceId": layout["marketplace_id"],
            "pluginId": PLUGIN_ID,
            "activation": {
                "path": str(activation.resolve()),
                "generation": 1,
            },
            "environment": _environment(),
            "transferredAt": "2026-01-01T00:00:00Z",
        },
    )
    orphaned = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="status",
            policy_path=policy,
        ),
    )
    assert orphaned.returncode == 0, orphaned.stderr
    assert json.loads(orphaned.stdout)["status"] == "orphaned-transfer"

    (legacy / ".installation-ownership.json").unlink()
    marker = Path(layout["plugin_root"]) / "maintenance"
    marker.touch()
    marker.with_suffix(".json").write_text("{", encoding="utf-8")
    stale = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="status",
            policy_path=policy,
        ),
    )
    assert stale.returncode == 0, stale.stderr
    stale_value = json.loads(stale.stdout)
    assert stale_value["status"] == "maintenance-blocked"
    assert stale_value["reason"] == "maintenance-stale"


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
def test_invalid_context_is_structured_not_invocation_failure(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    Path(layout["install"]).write_text("{", encoding="utf-8")
    arguments = [
        "status",
        "--context",
        str(layout["install"]),
        "--expected-marketplace-id",
        str(layout["marketplace_id"]),
        "--expected-plugin-id",
        PLUGIN_ID,
        "--durable-home",
        str(layout["durable"]),
        "--legacy-root",
        str(legacy),
        "--policy-path",
        str(tmp_path / "missing-policy.json"),
    ]
    result = _run(runner, arguments)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == "invalid"
    assert value["pluginId"] == PLUGIN_ID
    assert value["actualMode"] is None
    assert value["runtimeRoot"] is None


@pytest.mark.parametrize(
    "runner",
    ALL_RUNNERS,
)
def test_activation_cas_publishes_exact_environment_receipt(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    result = _run(runner, _activation_cas_arguments(layout, legacy))
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == "ready"
    assert value["activationChanged"] is True
    assert value["activationGeneration"] == 1
    assert value["namespaceGeneration"] == layout["namespace_generation"]
    assert value["installGeneration"] == layout["install_generation"]
    assert value["environment"] == _environment()
    assert value["operative"] is False

    activation = Path(value["activation"])
    receipt = json.loads(activation.read_text(encoding="utf-8"))
    assert receipt["environment"] == _environment()
    assert receipt["namespaceGeneration"] == layout["namespace_generation"]
    assert receipt["installGeneration"] == layout["install_generation"]
    assert receipt["generation"] == 1
    assert activation.read_bytes().endswith(b"\n")
    assert not activation.read_bytes().startswith(b"\xef\xbb\xbf")
    status = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="status",
            policy_path=tmp_path / "missing-policy.json",
        ),
    )
    assert status.returncode == 0, status.stderr
    status_value = json.loads(status.stdout)
    assert status_value["status"] == "ready"
    assert status_value["actualMode"] == "namespaced"
    assert status_value["activationGeneration"] == 1


@pytest.mark.parametrize(
    "runner",
    ALL_RUNNERS,
)
@pytest.mark.parametrize(
    ("expected_marketplace_id", "expected_plugin_id"),
    [
        ("", PLUGIN_ID),
        ("../escape", PLUGIN_ID),
        (None, ""),
        (None, "../escape"),
    ],
)
def test_activation_cas_rejects_invalid_expected_identity_before_locking(
    tmp_path: Path,
    runner: str,
    expected_marketplace_id: str | None,
    expected_plugin_id: str,
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    durable = Path(layout["durable"])
    before = sorted(
        path.relative_to(durable).as_posix() for path in durable.rglob("*")
    )
    result = _run(
        runner,
        _activation_cas_arguments(
            layout,
            legacy,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
        ),
    )
    assert result.returncode != 0
    after = sorted(
        path.relative_to(durable).as_posix() for path in durable.rglob("*")
    )
    assert after == before


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
def test_activation_cas_requires_absolute_legacy_root(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    result = _run(
        runner,
        _activation_cas_arguments(
            layout,
            legacy,
            legacy_root="relative-legacy",
        ),
    )
    assert result.returncode != 0
    assert "absolute" in result.stderr
    assert not (Path(layout["plugin_root"]) / "installation-activation.json").exists()


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
@pytest.mark.parametrize("context_value", [None, ""])
def test_activation_cas_requires_explicit_context_argument(
    tmp_path: Path, runner: str, context_value: str | None
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    arguments = _activation_cas_arguments(layout, legacy)
    context_index = arguments.index("--context")
    if context_value is None:
        del arguments[context_index : context_index + 2]
    else:
        arguments[context_index + 1] = context_value
    environment = os.environ.copy()
    environment["COPILOT_EXTENSIONS_CONTEXT"] = str(layout["install"])
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    result = subprocess.run(
        _runner_command(runner, arguments),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    assert result.returncode != 0
    assert not (Path(layout["plugin_root"]) / "installation-activation.json").exists()


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
def test_activation_cas_rejects_each_stale_generation_without_publication(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    first = _run(runner, _activation_cas_arguments(layout, legacy))
    assert first.returncode == 0, first.stderr
    activation = Path(json.loads(first.stdout)["activation"])
    original = activation.read_bytes()

    stale_activation = _run(runner, _activation_cas_arguments(layout, legacy))
    assert stale_activation.returncode == 0, stale_activation.stderr
    stale_activation_value = json.loads(stale_activation.stdout)
    assert stale_activation_value["status"] == "revalidation-required"
    assert stale_activation_value["activationGeneration"] == 1
    assert activation.read_bytes() == original

    namespace = Path(layout["namespace"])
    namespace_receipt = json.loads(namespace.read_text(encoding="utf-8"))
    namespace_receipt["generation"] = int(layout["namespace_generation"]) + 1
    _write_json(namespace, namespace_receipt)
    stale_namespace = _run(
        runner,
        _activation_cas_arguments(
            layout,
            legacy,
            expected_activation_generation=1,
        ),
    )
    assert stale_namespace.returncode == 0, stale_namespace.stderr
    stale_namespace_value = json.loads(stale_namespace.stdout)
    assert stale_namespace_value["status"] == "revalidation-required"
    assert stale_namespace_value["namespaceGeneration"] == (
        int(layout["namespace_generation"]) + 1
    )
    assert activation.read_bytes() == original

    namespace_receipt["generation"] = int(layout["namespace_generation"])
    _write_json(namespace, namespace_receipt)
    install = Path(layout["install"])
    install_receipt = json.loads(install.read_text(encoding="utf-8"))
    install_receipt["generation"] = int(layout["install_generation"]) + 1
    _write_json(install, install_receipt)
    stale_install = _run(
        runner,
        _activation_cas_arguments(
            layout,
            legacy,
            expected_activation_generation=1,
        ),
    )
    assert stale_install.returncode == 0, stale_install.stderr
    stale_install_value = json.loads(stale_install.stdout)
    assert stale_install_value["status"] == "revalidation-required"
    assert stale_install_value["installGeneration"] == (
        int(layout["install_generation"]) + 1
    )
    assert activation.read_bytes() == original


@pytest.mark.parametrize(
    "runner",
    ALL_RUNNERS,
)
def test_concurrent_activation_cas_has_one_atomic_winner(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    command = _runner_command(
        runner,
        _activation_cas_arguments(layout, legacy),
    )
    environment = os.environ.copy()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    processes = [
        subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=30) for process in processes]
    successful = [
        json.loads(stdout)
        for process, (stdout, _) in zip(processes, results, strict=True)
        if process.returncode == 0
    ]
    assert any(value["status"] == "ready" for value in successful), results
    for process, (_, stderr) in zip(processes, results, strict=True):
        if process.returncode:
            assert "remained busy" in stderr, results
    if len(successful) == 2:
        assert sorted(value["status"] for value in successful) == [
            "ready",
            "revalidation-required",
        ]
    activation = (
        Path(layout["plugin_root"]) / "installation-activation.json"
    )
    receipt = json.loads(activation.read_text(encoding="utf-8"))
    assert receipt["generation"] == 1
    assert not list(Path(layout["durable"]).rglob("*.tmp-*"))
    assert not list(Path(layout["durable"]).rglob("*.claim-*"))
    assert not (
        Path(layout["durable"])
        / "marketplaces"
        / ".locks"
        / f"{layout['marketplace_id']}.genesis"
    ).exists()
    assert not (
        Path(layout["cell"]) / ".locks" / f"{PLUGIN_ID}.install.lock"
    ).exists()


@pytest.mark.parametrize(
    "runner",
    ALL_RUNNERS,
)
def test_foreign_windows_wsl_and_posix_activation_receipts_fail_closed(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    current = _environment()
    foreign_environments = [
        {
            "platform": "windows",
            "homeRealPath": r"C:\Users\example",
            "wslDistro": None,
        },
        {
            "platform": "posix",
            "homeRealPath": "/home/example",
            "wslDistro": None,
        },
        {
            "platform": "posix",
            "homeRealPath": "/home/example",
            "wslDistro": "ExampleDistro",
        },
    ]
    foreign_environments = [
        environment
        for environment in foreign_environments
        if environment != current
    ]
    if runner != "python" and not EXHAUSTIVE_ADAPTERS:
        foreign_environments = foreign_environments[:1]
    for environment in foreign_environments:
        _activation(layout, environment=environment)
        result = _run(
            runner,
            _cli_arguments(
                layout,
                legacy,
                action="status",
                policy_path=tmp_path / "missing-policy.json",
            ),
        )
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        assert value["status"] == "foreign-environment"
        assert value["actualMode"] is None
        assert value["runtimeRoot"] is None


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
def test_activation_cas_never_overwrites_foreign_environment_receipt(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    foreign = {
        "platform": "windows",
        "homeRealPath": r"C:\Users\example",
        "wslDistro": None,
    }
    if foreign == _environment():
        foreign = {
            "platform": "posix",
            "homeRealPath": "/home/example",
            "wslDistro": "ExampleDistro",
        }
    activation = _activation(layout, environment=foreign)
    original = activation.read_bytes()
    result = _run(
        runner,
        _activation_cas_arguments(
            layout,
            legacy,
            expected_activation_generation=3,
        ),
    )
    assert result.returncode != 0
    assert "foreign environment" in result.stderr
    assert activation.read_bytes() == original


@pytest.mark.parametrize(
    "runner",
    ALL_RUNNERS,
)
@pytest.mark.parametrize(
    "home_real_path",
    [r"\\", r"\\server", "//server/share"],
)
def test_activation_cas_never_overwrites_invalid_windows_network_path_receipt(
    tmp_path: Path, runner: str, home_real_path: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    activation = _activation(
        layout,
        environment={
            "platform": "windows",
            "homeRealPath": home_real_path,
            "wslDistro": None,
        },
    )
    original = activation.read_bytes()
    result = _run(
        runner,
        _activation_cas_arguments(
            layout,
            legacy,
            expected_activation_generation=3,
        ),
    )
    assert result.returncode != 0
    assert activation.read_bytes() == original


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
def test_activation_cas_never_overwrites_malformed_receipt(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    activation = _activation(layout)
    receipt = json.loads(activation.read_text(encoding="utf-8"))
    receipt["schema"] = "example.invalid"
    _write_json(activation, receipt)
    original = activation.read_bytes()
    result = _run(
        runner,
        _activation_cas_arguments(
            layout,
            legacy,
            expected_activation_generation=3,
        ),
    )
    assert result.returncode != 0
    assert activation.read_bytes() == original


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
@pytest.mark.parametrize("receipt_name", ["namespace", "install"])
def test_activation_cas_requires_active_context_receipts(
    tmp_path: Path, runner: str, receipt_name: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    receipt_path = Path(layout[receipt_name])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["state"] = "removing"
    _write_json(receipt_path, receipt)
    result = _run(runner, _activation_cas_arguments(layout, legacy))
    assert result.returncode != 0
    assert "requires active namespace and install receipts" in result.stderr
    assert not (Path(layout["plugin_root"]) / "installation-activation.json").exists()


@pytest.mark.parametrize(
    "runner",
    ALL_RUNNERS,
)
def test_activation_cas_refuses_generation_overflow_without_replacement(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    maximum = 9223372036854775807
    activation = _activation(layout, generation=maximum)
    original = activation.read_bytes()
    result = _run(
        runner,
        _activation_cas_arguments(
            layout,
            legacy,
            expected_activation_generation=maximum,
            mode="legacy",
            state="deactivated",
            disposition="restored",
        ),
    )
    assert result.returncode != 0
    assert "cannot be incremented" in result.stderr
    assert activation.read_bytes() == original


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
@pytest.mark.skipif(os.name == "nt", reason="WSL identity requires a POSIX host")
def test_activation_generation_and_environment_classification_match(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    policy = tmp_path / "missing-policy.json"
    _activation(layout, install_generation=1)
    stale = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="status",
            policy_path=policy,
        ),
    )
    assert stale.returncode == 0, stale.stderr
    stale_value = json.loads(stale.stdout)
    assert stale_value["status"] == "revalidation-required"
    assert stale_value["actualMode"] == "namespaced"
    assert stale_value["runtimeRoot"] == str(Path(layout["plugin_root"]).resolve())

    foreign_environment = _environment()
    foreign_environment["wslDistro"] = "OtherDistro"
    _activation(layout, environment=foreign_environment)
    foreign = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="status",
            policy_path=policy,
        ),
    )
    assert foreign.returncode == 0, foreign.stderr
    foreign_value = json.loads(foreign.stdout)
    assert foreign_value["status"] == "foreign-environment"
    assert foreign_value["actualMode"] is None
    assert foreign_value["runtimeRoot"] is None


@pytest.mark.parametrize(
    "runner",
    ALL_RUNNERS,
)
def test_deactivated_activation_pins_legacy_diagnostically(
    tmp_path: Path, runner: str
) -> None:
    layout = _cell_layout(tmp_path)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    activation = _activation(layout, mode="legacy", state="deactivated")
    policy = tmp_path / "missing-policy.json"
    status = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="status",
            policy_path=policy,
        ),
    )
    assert status.returncode == 0, status.stderr
    value = json.loads(status.stdout)
    assert value["status"] == "ready"
    assert value["actualMode"] == "legacy"
    assert value["runtimeRoot"] == str(legacy.resolve())
    assert value["activation"] == str(activation.resolve())

    allowed = _run(
        runner,
        _cli_arguments(
            layout,
            legacy,
            action="probe-legacy",
            policy_path=policy,
        ),
    )
    assert allowed.returncode == 0, allowed.stderr
    decision = json.loads(allowed.stdout)
    assert decision["allowMutation"] is True
    assert decision["probeReason"] == "legacy-active"


@pytest.mark.parametrize(
    "runner",
    ALL_RUNNERS,
)
def test_valid_other_cell_tombstone_blocks_legacy_probe(
    tmp_path: Path, runner: str
) -> None:
    current = _cell_layout(tmp_path, vector_index=0)
    destination = _cell_layout(tmp_path, vector_index=1)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    destination_activation = _activation(destination, generation=7)
    _write_json(
        legacy / ".installation-ownership.json",
        {
            "schema": "copilot-extensions.legacy-installation-ownership",
            "version": 1,
            "marketplaceId": destination["marketplace_id"],
            "pluginId": PLUGIN_ID,
            "activation": {
                "path": str(destination_activation.resolve()),
                "generation": 7,
            },
            "environment": _environment(),
            "transferredAt": "2026-01-01T00:00:00Z",
        },
    )
    result = _run(
        runner,
        _cli_arguments(
            current,
            legacy,
            action="probe-legacy",
            policy_path=tmp_path / "missing-policy.json",
        ),
    )
    assert result.returncode == 3, result.stderr
    value = json.loads(result.stdout)
    assert value["legacy"]["disposition"] == "owned-by-other-cell"
    assert value["allowMutation"] is False
    assert value["probeReason"] == "legacy-owned-by-other-cell"


def test_provenance_blocked_retains_trustworthy_plugin_id(tmp_path: Path) -> None:
    module = _load_module()
    profile = tmp_path / "profile"
    payload = tmp_path / "payload" / PLUGIN_ID
    legacy = tmp_path / "legacy"
    profile.mkdir()
    payload.mkdir(parents=True)
    legacy.mkdir()
    result = module.resolve_installation_mode(
        payload_root=payload,
        plugin_id=PLUGIN_ID,
        durable_home=tmp_path / "durable",
        legacy_root=legacy,
        os_profile=profile,
        platform="posix",
        environment={},
    )
    assert result["status"] == "provenance-blocked"
    assert result["marketplaceId"] is None
    assert result["pluginId"] == PLUGIN_ID
    assert result["desiredMode"] is None
    assert result["actualMode"] is None
    assert result["runtimeRoot"] is None


@pytest.mark.parametrize(
    "runner",
    REFERENCE_RUNNERS,
)
def test_provenance_blocked_cli_retains_plugin_id(
    tmp_path: Path, runner: str
) -> None:
    payload = tmp_path / "payload" / PLUGIN_ID
    legacy = tmp_path / "legacy"
    payload.mkdir(parents=True)
    legacy.mkdir()
    arguments = [
        "status",
        "--payload-root",
        str(payload),
        "--plugin-id",
        PLUGIN_ID,
        "--durable-home",
        str(tmp_path / "durable"),
        "--legacy-root",
        str(legacy),
        "--policy-path",
        str(tmp_path / "missing-policy.json"),
    ]
    result = _run(runner, arguments)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == "provenance-blocked"
    assert value["marketplaceId"] is None
    assert value["pluginId"] == PLUGIN_ID
    assert value["desiredMode"] is None
    assert value["actualMode"] is None
    assert value["runtimeRoot"] is None
