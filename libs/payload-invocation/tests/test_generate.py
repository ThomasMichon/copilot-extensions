"""Tests for canonical payload-local command generation."""
from __future__ import annotations

import importlib.util
from collections.abc import Callable
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from types import ModuleType
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "generate.py"
REPO = SCRIPT.parents[2]
_spec = importlib.util.spec_from_file_location("payload_invocation_generate", SCRIPT)
generator = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(generator)
# Fixture setup uses the receipt owner API; invocation still exercises the real PowerShell dispatcher.
_context_spec = importlib.util.spec_from_file_location(
    "payload_invocation_context", REPO / "libs" / "installation-context" / "installation_context.py",
)
assert _context_spec and _context_spec.loader
installation_context = importlib.util.module_from_spec(_context_spec)
sys.modules[_context_spec.name] = installation_context
_context_spec.loader.exec_module(installation_context)


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "plugin" / "payload-invocation.json"
    path.parent.mkdir()
    scripts = path.parent / "scripts"
    scripts.mkdir()
    (scripts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (scripts / "install.ps1").write_text("# generated fixture\n", encoding="utf-8")
    (scripts / "resolve-runtime.sh").write_text(
        'AGENT_RT_PY=""\n', encoding="utf-8"
    )
    (scripts / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = $null\n", encoding="utf-8"
    )
    path.write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.payload-invocation",
                "version": 1,
                "command": "agent-example",
                "module": "agent_example",
                "runtimeRoot": ".agent-example",
                "noSelfProvisionEnv": "AGENT_EXAMPLE_NO_SELFPROVISION",
                "purpose": "Exercise an example runtime",
            }
        ),
        encoding="utf-8",
    )
    return path


def _multi_manifest(tmp_path: Path) -> Path:
    path = _manifest(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    primary = {
        field: data.pop(field) for field in ("command", "module", "purpose")
    }
    data["plugin"] = "agent-example"
    data["commands"] = [
        primary,
        {
            "command": "example-helper",
            "module": "agent_example.helper",
            "purpose": "Exercise an example helper",
        },
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _required_context_manifest(tmp_path: Path) -> Path:
    path = _manifest(tmp_path)
    scripts = path.parent / "scripts"
    (scripts / "invoke-payload-runtime.sh").write_text(
        "#!/usr/bin/env bash\n", encoding="utf-8"
    )
    (scripts / "invoke-payload-runtime.ps1").write_text(
        "# generated fixture\n", encoding="utf-8"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(
        {
            "version": 2,
            "command": "agent-machines",
            "module": "agent_machines",
            "legacyRuntimeRoot": data.pop("runtimeRoot"),
            "installationContext": "required",
            "payloadRootEnv": "AGENT_MACHINES_PAYLOAD_ROOT",
            "payloadDispatcher": {
                "posix": "scripts/invoke-payload-runtime.sh",
                "windows": "scripts/invoke-payload-runtime.ps1",
            },
        }
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _required_context_multi_manifest(tmp_path: Path) -> Path:
    path = _required_context_manifest(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    primary = {
        field: data.pop(field) for field in ("command", "module", "purpose")
    }
    data["plugin"] = "agent-machines"
    data["commands"] = [
        primary,
        {
            "command": "agent-machines-helper",
            "module": "agent_machines.helper",
            "purpose": "Exercise an example helper",
        },
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_binding_human_assets_do_not_require_worktrees_source(tmp_path, monkeypatch):
    manifest = _required_context_manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["catalogBinding"] = True
    data["catalogBindingModule"] = "src/agent_machines/_command_binding.py"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    primitive = manifest.parent / "scripts/installation-context/installation_context.py"
    primitive.parent.mkdir()
    primitive.write_text("# fixture\n", encoding="utf-8")
    root = tmp_path / "source"
    shared = root / "libs/update-telemetry/src/update_telemetry"
    shared.mkdir(parents=True)
    for name in ("human.py", "redact.py", "capabilities.py"):
        shutil.copyfile(REPO / "libs/update-telemetry/src/update_telemetry" / name, shared / name)
    roster = root / ".github/plugin/marketplace.json"
    roster.parent.mkdir(parents=True)
    roster.write_text(json.dumps({"plugins": [
        {"name": "agent-machines", "source": "./plugins/agent-machines"},
    ]}), encoding="utf-8")
    project = root / "plugins/agent-machines/pyproject.toml"
    project.parent.mkdir(parents=True)
    project.write_text("", encoding="utf-8")
    monkeypatch.setattr(generator, "REPO", root)

    assert not (root / "plugins/agent-worktrees").exists()
    assert generator.process_manifest(manifest, check=False) == []
    assert generator.process_manifest(manifest, check=True) == []
    for parent in (manifest.parent / "scripts", manifest.parent / "src/agent_machines"):
        assert (parent / "_lifecycle_human/capabilities.py").read_text(encoding="utf-8") == (
            shared / "capabilities.py"
        ).read_text(encoding="utf-8")


def _write_runtime_completion(slot: Path, version: str) -> None:
    (slot / ".install-complete.json").write_text(
        json.dumps(
            {
                "version": version,
                "completed_at": "2026-01-01T00:00:00Z",
                "pid": 123,
            }
        ),
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_generates_three_payload_local_shims(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    assert generator.process_manifest(manifest, check=False) == []
    generated = generator.expected_files(manifest)
    assert {path.name for path in generated} == {
        "agent-example",
        "agent-example.cmd",
        "agent-example.ps1",
        "emit-command-catalog.ps1",
        "emit-command-catalog.sh",
    }
    for path, expected in generated.items():
        assert path.read_text(encoding="utf-8") == expected
        assert ".local/bin" not in expected
        assert "installed-plugins/*" not in expected
    if os.name != "nt":
        assert (manifest.parent / "bin" / "agent-example").stat().st_mode & 0o100
        assert (
            manifest.parent / "scripts" / "emit-command-catalog.sh"
        ).stat().st_mode & 0o100


def test_accepts_non_agent_runtime_plugin_identity(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["command"] = "budget-guidance"
    data["module"] = "budget_guidance"
    data["runtimeRoot"] = ".budget-guidance"
    data["noSelfProvisionEnv"] = "BUDGET_GUIDANCE_NO_SELFPROVISION"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    loaded = generator.load_manifest(manifest)

    assert loaded["plugin"] == "budget-guidance"


def test_shell_catalog_mode_is_python_independent(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["posixCatalogMode"] = "shell"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    generated = generator.expected_files(manifest)
    catalog = generated[manifest.parent / "scripts" / "emit-command-catalog.sh"]

    assert "command -v python" not in catalog
    assert "json_escape" in catalog


def test_payload_root_env_is_opt_in_and_preserves_defaults(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    baseline = generator.expected_files(manifest)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["payloadRootEnv"] = "AGENT_EXAMPLE_PAYLOAD_ROOT"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    generated = generator.expected_files(manifest)

    posix_path = manifest.parent / "bin" / "agent-example"
    powershell_path = manifest.parent / "bin" / "agent-example.ps1"
    assert "AGENT_EXAMPLE_PAYLOAD_ROOT" not in baseline[posix_path]
    assert "AGENT_EXAMPLE_PAYLOAD_ROOT" not in baseline[powershell_path]
    assert (
        'export AGENT_EXAMPLE_PAYLOAD_ROOT="$_payload_root"'
        in generated[posix_path]
    )
    assert (
        "$env:AGENT_EXAMPLE_PAYLOAD_ROOT = $_payloadRoot"
        in generated[powershell_path]
    )
    assert (
        "$env:AGENT_EXAMPLE_PAYLOAD_ROOT = $_payloadRoot\n    try {"
        in generated[powershell_path]
    )
    assert "} finally {" in generated[powershell_path]


def test_boot_trace_log_file_defaults_and_can_be_overridden(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    baseline = generator.expected_files(manifest)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["bootTraceLogFile"] = "logs/activity.jsonl"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    generated = generator.expected_files(manifest)

    posix_path = manifest.parent / "bin" / "agent-example"
    powershell_path = manifest.parent / "bin" / "agent-example.ps1"
    assert "logs/boot-trace.jsonl" in baseline[posix_path]
    assert "logs\\boot-trace.jsonl" in baseline[powershell_path]
    assert "logs/activity.jsonl" in generated[posix_path]
    assert "logs\\activity.jsonl" in generated[powershell_path]


@pytest.mark.parametrize(
    "boot_trace_log_file",
    [
        123,
        True,
        None,
        "",
        "logs//boot.jsonl",
        "/logs/boot.jsonl",
        "logs/boot.jsonl/",
        "../logs/boot.jsonl",
        "logs/../boot.jsonl",
        "logs/..",
    ],
)
def test_boot_trace_log_file_rejects_unsafe_values(
    tmp_path: Path,
    boot_trace_log_file: object,
) -> None:
    """Regression (Copilot review, PR #3310): non-string values, `..`
    traversal, and empty path components (a leading/trailing/doubled `/`)
    must all be rejected -- an empty component would silently break the
    writer's directory-creation logic at runtime, and `..` would let a
    plugin manifest point the durable log outside its own runtime root."""
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["bootTraceLogFile"] = boot_trace_log_file
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid bootTraceLogFile"):
        generator.load_manifest(manifest)


def test_session_start_bootstrap_defaults_true_and_requires_boolean(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    assert generator.load_manifest(manifest)["sessionStartBootstrap"] is True
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["sessionStartBootstrap"] = False
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert generator.load_manifest(manifest)["sessionStartBootstrap"] is False
    data["sessionStartBootstrap"] = "false"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="sessionStartBootstrap"):
        generator.load_manifest(manifest)


def test_payload_dispatcher_delegates_both_platform_shims(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    scripts = manifest.parent / "scripts"
    (scripts / "runtime-gate.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (scripts / "runtime-gate.ps1").write_text("# gate\n", encoding="utf-8")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["payloadDispatcher"] = {
        "posix": "scripts/runtime-gate.sh",
        "windows": "scripts/runtime-gate.ps1",
    }
    data["payloadRootEnv"] = "AGENT_EXAMPLE_PAYLOAD_ROOT"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    generated = generator.expected_files(manifest)

    posix = generated[manifest.parent / "bin" / "agent-example"]
    powershell = generated[manifest.parent / "bin" / "agent-example.ps1"]
    assert 'exec "$_payload_root/scripts/runtime-gate.sh" "$@"' in posix
    assert 'export AGENT_EXAMPLE_PAYLOAD_ROOT="$_payload_root"' in posix
    assert "COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN" in posix
    assert "_boot_trace shim-start" in posix
    assert "$_payloadDispatcher = Join-Path $_payloadRoot 'scripts\\runtime-gate.ps1'" in powershell
    assert "$env:AGENT_EXAMPLE_PAYLOAD_ROOT = $_payloadRoot" in powershell
    assert "& $_payloadDispatcher @args" in powershell
    assert "COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN" in powershell
    assert "Write-BootTrace 'shim-start'" in powershell
    assert "_resolve_runtime" not in posix
    assert "Resolve-PayloadRuntime" not in powershell


def test_payload_dispatcher_requires_platform_parity(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    gate = manifest.parent / "scripts" / "runtime-gate.sh"
    gate.write_text("#!/bin/sh\n", encoding="utf-8")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["payloadDispatcher"] = {"posix": "scripts/runtime-gate.sh"}
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="must declare both"):
        generator.load_manifest(manifest)


def test_catalog_gate_is_root_relative_and_rendered_on_both_platforms(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    gate = manifest.parent / "scripts" / "catalog_gate.py"
    gate.write_text("raise SystemExit(0)\n", encoding="utf-8")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["catalogGate"] = "scripts/catalog_gate.py"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    generated = generator.expected_files(manifest)

    posix = generated[manifest.parent / "scripts" / "emit-command-catalog.sh"]
    powershell = generated[
        manifest.parent / "scripts" / "emit-command-catalog.ps1"
    ]
    assert 'catalog_gate="$self_root/scripts/catalog_gate.py"' in posix
    assert '"$py" -E -X utf8 "$catalog_gate" --check --cwd "$PWD"' in posix
    assert "$catalogGate = Join-Path $selfRoot 'scripts\\catalog_gate.py'" in powershell
    assert "-E -X utf8 $catalogGate --check" in powershell

    data["catalogGate"] = "../escape.py"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="catalogGate"):
        generator.load_manifest(manifest)


def test_required_installation_context_uses_fixed_dispatchers(tmp_path: Path) -> None:
    manifest = _required_context_manifest(tmp_path)

    data = generator.load_manifest(manifest)
    generated = generator.expected_files(manifest)

    assert data["installationContext"] == "required"
    assert data["runtimeRoot"] == ".agent-example"
    posix = generated[manifest.parent / "bin" / "agent-machines"]
    powershell = generated[manifest.parent / "bin" / "agent-machines.ps1"]
    assert "if ! command -v bash >/dev/null 2>&1; then" in posix
    assert "[agent-machines] required payload dispatcher needs bash." in posix
    assert 'exec bash "$_payload_root/scripts/invoke-payload-runtime.sh" "$@"' in posix
    assert "COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN" in posix
    assert "$env:AGENT_MACHINES_PAYLOAD_ROOT = $_payloadRoot" in powershell
    assert "COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN" in powershell
    assert "Resolve-PayloadRuntime" not in powershell


def test_required_multi_command_dispatchers_export_command_metadata(
    tmp_path: Path,
) -> None:
    manifest = _required_context_multi_manifest(tmp_path)

    generated = generator.expected_files(manifest)

    posix = generated[manifest.parent / "bin" / "agent-machines-helper"]
    powershell = generated[
        manifest.parent / "bin" / "agent-machines-helper.ps1"
    ]
    assert (
        'export COPILOT_EXTENSIONS_PAYLOAD_COMMAND="$_command"' in posix
    )
    assert (
        'export COPILOT_EXTENSIONS_PAYLOAD_MODULE="agent_machines.helper"' in posix
    )
    assert (
        "$env:COPILOT_EXTENSIONS_PAYLOAD_COMMAND = $_command" in powershell
    )
    assert (
        "$env:COPILOT_EXTENSIONS_PAYLOAD_MODULE = 'agent_machines.helper'"
        in powershell
    )


@pytest.mark.parametrize("version", [True, False])
def test_manifest_version_rejects_json_booleans(
    tmp_path: Path,
    version: bool,
) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["version"] = version
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="version 1 or 2"):
        generator.load_manifest(manifest)


@pytest.mark.parametrize("plugin", ["agent-unrelated", "context-handoff"])
def test_required_installation_context_rejects_ineligible_plugin(
    tmp_path: Path,
    plugin: str,
) -> None:
    manifest = _required_context_manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["command"] = plugin
    data["plugin"] = plugin
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="runtime-bearing core suite plugin identities|invalid plugin",
    ):
        generator.load_manifest(manifest)


@pytest.mark.parametrize("catalog", [None, "{\n"])
def test_required_installation_context_requires_valid_canonical_roster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog: str | None,
) -> None:
    manifest = _required_context_manifest(tmp_path)
    repo = tmp_path / "isolated-repo"
    if catalog is not None:
        marketplace = repo / ".github" / "plugin" / "marketplace.json"
        marketplace.parent.mkdir(parents=True)
        marketplace.write_text(catalog, encoding="utf-8")
    monkeypatch.setattr(generator, "REPO", repo)

    with pytest.raises(ValueError, match="cannot load canonical marketplace roster"):
        generator.load_manifest(manifest)


def test_v1_rejects_installation_context_fields_without_changing_defaults(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    baseline = generator.expected_files(manifest)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["installationContext"] = "required"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="version 1 cannot declare"):
        generator.load_manifest(manifest)

    data.pop("installationContext")
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert generator.expected_files(manifest) == baseline


def _copied_agent_machines_payload(tmp_path: Path) -> Path:
    payload = tmp_path / "agent-machines"
    shutil.copytree(
        REPO / "plugins" / "agent-machines",
        payload,
        ignore=_payload_copy_ignore(REPO / "plugins" / "agent-machines"),
    )
    _stage_payload_dependencies(payload, source_consumer=REPO / "plugins" / "agent-machines")
    return payload


def _agent_library_src(payload: Path, name: str) -> Path:
    local = payload / "libs" / name / "src"
    assert local.is_dir(), f"the copied payload must contain its own {name} dependency"
    return local


def _agent_machines_import_sources(payload: Path) -> list[str]:
    return [str(payload / "src")] + [
        str(_agent_library_src(payload, name))
        for name in ("plugin-resolve", "agent-procutil", "machine-transport", "remote-login-shell")
    ]
def _dependency_materializer() -> ModuleType:
    helper = (
        REPO / "worktree-manager" / "src" / "worktree_manager"
        / "_trusted_pointer_materializer.py"
    )
    spec = importlib.util.spec_from_file_location("payload_dependency_materializer", helper)
    assert spec and spec.loader
    materializer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(materializer)
    return materializer


def _payload_copy_ignore(source_consumer: Path) -> Callable[[str, list[str]], set[str]]:
    external_libs = {
        lib for _name, _path, lib, _editable in
        _dependency_materializer().find_uv_editable_refs(source_consumer)
    }
    ignore_cache = shutil.ignore_patterns(
        ".pytest_cache", ".ruff_cache", "__pycache__", "*.pyc", "*.egg-info", ".venv", ".test-venvs",
    )

    def ignore(directory: str, names: list[str]) -> set[str]:
        excluded = set(ignore_cache(directory, names))
        if Path(directory) == source_consumer / "libs":
            excluded.update(external_libs)
        return excluded

    return ignore


def _stage_payload_dependencies(payload: Path, *, source_consumer: Path) -> None:
    materializer = _dependency_materializer()
    result = materializer.materialize_uv_editable_ref_into(
        source_consumer_dir=source_consumer,
        dest_consumer_dir=payload,
        canonical_root=REPO,
        dest_root=payload,
    )
    assert not any(line.startswith("SKIP ") for line in result), result
    assert (payload / "libs" / "machine-transport" / "src" / "machine_transport").is_dir()
    assert (payload / "libs" / "remote-login-shell" / "src" / "remote_login_shell").is_dir()


def _directory_marketplace_agent_machines_payload(tmp_path: Path) -> Path:
    marketplace = tmp_path / "marketplace"
    payload = marketplace / "plugins" / "agent-machines"
    shutil.copytree(
        REPO / "plugins" / "agent-machines",
        payload,
        ignore=_payload_copy_ignore(REPO / "plugins" / "agent-machines"),
    )
    _stage_payload_dependencies(payload, source_consumer=REPO / "plugins" / "agent-machines")
    write_json = {
        "name": "example",
        "owner": {"name": "Example"},
        "metadata": {"version": "1.0.0"},
        "plugins": [
            {
                "name": "agent-machines",
                "description": "Synthetic directory marketplace fixture",
                "version": json.loads(
                    (payload / "plugin.json").read_text(encoding="utf-8")
                )["version"],
                "source": "plugins/agent-machines",
            }
        ],
    }
    catalog = marketplace / ".github" / "plugin" / "marketplace.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps(write_json), encoding="utf-8")
    return payload


def _stamp_agent_machines_context(
    tmp_path: Path,
    payload: Path,
    *,
    explicit_source: bool = True,
    durable_home: Path | None = None,
) -> Path:
    version = json.loads(
        (payload / "plugin.json").read_text(encoding="utf-8")
    )["version"]
    result = installation_context.stamp_context(
        payload_version=version, payload_origin="explicit",
        expected_namespace_generation=0, expected_install_generation=0,
        plugin_id="agent-machines", payload_root=payload,
        durable_home=durable_home or tmp_path / "durable",
        source_descriptor=(
            {"source": "github", "repo": "example-org/example-marketplace"}
            if explicit_source else None
        ),
        marketplace_key="example" if explicit_source else None,
    )
    return Path(result["installReceipt"])


def _activate_agent_machines_context(
    tmp_path: Path,
    home: Path,
    context: Path,
    *,
    durable_home: Path | None = None,
) -> Path:
    install = json.loads(context.read_text(encoding="utf-8"))
    namespace = json.loads(
        Path(install["namespaceReceipt"]).read_text(encoding="utf-8")
    )
    policy = home / ".copilot-extensions" / "installation-mode.json"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.installation-mode",
                "version": 1,
                "installationMode": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update({"HOME": str(home), "USERPROFILE": str(home)})
    installation_context.compare_and_swap_activation(
        context=context, expected_marketplace_id=install["marketplaceId"],
        expected_plugin_id="agent-machines",
        expected_namespace_generation=namespace["generation"],
        expected_install_generation=install["generation"], expected_activation_generation=0,
        activation_mode="namespaced", activation_state="active", legacy_disposition="absent",
        legacy_probe={"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
        legacy_root=home / ".agent-machines",
        durable_home=durable_home or tmp_path / "durable",
        environment=environment,
    )
    return context.parent


def _copied_agent_worktrees_payload(tmp_path: Path, *, dispatch_only: bool = False) -> Path:
    payload = tmp_path / "agent-worktrees"
    source = REPO / "plugins" / "agent-worktrees"
    ignore_runtime = _payload_copy_ignore(source)

    def ignore(directory, names):
        excluded = ignore_runtime(directory, names)
        if Path(directory) == source:
            # These runtime/dispatch fixtures do not consume the authoring corpus.
            excluded.update({"tests", "docs", "skills"})
            if dispatch_only:
                excluded.update({"src", "libs"})
        return excluded

    shutil.copytree(source, payload, ignore=ignore)
    if not dispatch_only:
        _stage_payload_dependencies(payload, source_consumer=source)
    return payload


def _stamp_agent_worktrees_context(
    tmp_path: Path,
    payload: Path,
) -> Path:
    # Receipt setup uses its real owner API; the contract below is native dispatch,
    # not repeated shell startup for constructing those input receipts.
    version = json.loads(
        (payload / "plugin.json").read_text(encoding="utf-8")
    )["version"]
    result = installation_context.stamp_context(
        payload_version=version, payload_origin="explicit",
        expected_namespace_generation=0, expected_install_generation=0,
        plugin_id="agent-worktrees", payload_root=payload,
        durable_home=tmp_path / "durable",
        source_descriptor={"source": "github", "repo": "example-org/example-marketplace"},
        marketplace_key="example",
    )
    return Path(result["installReceipt"])


def _activate_agent_worktrees_context(
    tmp_path: Path,
    home: Path,
    context: Path,
) -> Path:
    install = json.loads(context.read_text(encoding="utf-8"))
    namespace = json.loads(
        Path(install["namespaceReceipt"]).read_text(encoding="utf-8")
    )
    policy = home / ".copilot-extensions" / "installation-mode.json"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.installation-mode",
                "version": 1,
                "installationMode": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update({"HOME": str(home), "USERPROFILE": str(home)})
    installation_context.compare_and_swap_activation(
        context=context, expected_marketplace_id=install["marketplaceId"],
        expected_plugin_id="agent-worktrees",
        expected_namespace_generation=namespace["generation"],
        expected_install_generation=install["generation"], expected_activation_generation=0,
        activation_mode="namespaced", activation_state="active", legacy_disposition="absent",
        legacy_probe={"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
        legacy_root=home / ".agent-worktrees", durable_home=tmp_path / "durable",
        environment=environment,
    )
    return context.parent


def _agent_worktrees_test_environment(
    home: Path,
    payload: Path,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(payload),
            "PYTHONPATH": os.pathsep.join(
                [
                    str(payload / "src"),
                    *(str(path) for path in sorted((payload / "libs").glob("*/src"))),
                ]
            ),
            "TEST_PYTHON": sys.executable,
            # Copied payloads are disposable; avoid writing an unused bytecode corpus.
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return environment


NATIVE_POWERSHELL_HOSTS = list(dict.fromkeys(filter(None, (
    shutil.which("pwsh"), shutil.which("powershell"),
))))


@pytest.mark.skipif(os.name != "nt", reason="native Windows context semantics")
@pytest.mark.parametrize("host", NATIVE_POWERSHELL_HOSTS or [None])
def test_worktrees_composed_status_preserves_independent_validation(tmp_path: Path, host: str) -> None:
    if host is None:
        pytest.skip("PowerShell is unavailable")
    payload = _copied_agent_worktrees_payload(tmp_path, dispatch_only=True)
    context = _stamp_agent_worktrees_context(tmp_path, payload)
    home = tmp_path / "home"
    home.mkdir()
    _activate_agent_worktrees_context(tmp_path, home, context)
    environment = _agent_worktrees_test_environment(home, payload)
    environment["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    native = payload / "scripts" / "installation-context" / "installation-context.ps1"
    prefix = [host, "-NoProfile", "-File", str(native)]
    status = [
        "status", "-PayloadRoot", str(payload), "-PluginId", "agent-worktrees",
        "-LegacyRoot", str(home / ".agent-worktrees"),
        "-Context", str(context), "-DurableHome", str(tmp_path / "durable"),
    ]
    expectations = ["-ExpectedPluginId", "agent-worktrees", "-ExpectedPayloadRoot", str(payload)]

    def invoke(arguments: list[str]) -> dict[str, object]:
        result = subprocess.run(
            [*prefix, *arguments], env=environment, capture_output=True, text=True,
            timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    ordinary = invoke(status)
    assert ordinary["status"] == "ready"
    assert "contextValidation" not in ordinary
    expected = invoke([
        "validate", "-Context", str(context),
        "-DurableHome", str(tmp_path / "durable"), *expectations,
    ])
    composed = invoke([*status, "-WithContextValidation", *expectations])
    assert composed.pop("contextValidation") == expected
    assert composed == ordinary


@pytest.mark.skipif(os.name != "nt", reason="native Windows context semantics")
@pytest.mark.parametrize("host", NATIVE_POWERSHELL_HOSTS or [None])
def test_worktrees_composed_status_revalidates_changed_receipt(tmp_path: Path, host: str) -> None:
    if host is None:
        pytest.skip("PowerShell is unavailable")
    payload = _copied_agent_worktrees_payload(tmp_path, dispatch_only=True)
    context = _stamp_agent_worktrees_context(tmp_path, payload)
    home = tmp_path / "home"
    home.mkdir()
    _activate_agent_worktrees_context(tmp_path, home, context)
    environment = _agent_worktrees_test_environment(home, payload)
    environment["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    receipt = Path(json.loads(context.read_text(encoding="utf-8"))["namespaceReceipt"])
    changed = json.loads(receipt.read_text(encoding="utf-8"))
    changed["generation"] += 1
    replacement = tmp_path / "replacement.json"
    replacement.write_text(json.dumps(changed), encoding="utf-8")
    environment.update({
        "TEST_NAMESPACE_RECEIPT": str(receipt),
        "TEST_NAMESPACE_REPLACEMENT": str(replacement),
    })
    native = payload / "scripts" / "installation-context" / "installation-context.ps1"
    source = native.read_text(encoding="utf-8")
    status_call = "$result = Resolve-InstallationStatus $resolvedCopilotHome $resolvedProjectRoot $resolvedDurableHome"
    assert source.count(status_call) == 1
    native.write_text(source.replace(
        status_call, status_call + "\n"
        "[IO.File]::WriteAllText($env:TEST_NAMESPACE_RECEIPT, "
        "[IO.File]::ReadAllText($env:TEST_NAMESPACE_REPLACEMENT))",
    ), encoding="utf-8")
    result = subprocess.run(
        [host, "-NoProfile", "-File", str(native), "status",
         "-PayloadRoot", str(payload), "-PluginId", "agent-worktrees",
         "-LegacyRoot", str(home / ".agent-worktrees"),
         "-Context", str(context), "-DurableHome", str(tmp_path / "durable"),
         "-WithContextValidation", "-ExpectedPluginId", "agent-worktrees",
         "-ExpectedPayloadRoot", str(payload)],
        env=environment, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 1
    assert not result.stdout.strip()
    assert "changed after it was validated" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows provision file-lock sharing")
@pytest.mark.parametrize("host", NATIVE_POWERSHELL_HOSTS or [None])
@pytest.mark.parametrize("branch", ["locked-fast", "provisioned"])
def test_worktrees_releases_provision_lock_before_runtime_dispatch(
    tmp_path: Path, host: str, branch: str,
) -> None:
    if host is None:
        pytest.skip("PowerShell is unavailable")
    payload = _copied_agent_worktrees_payload(tmp_path, dispatch_only=True)
    context = _stamp_agent_worktrees_context(tmp_path, payload)
    home = tmp_path / "home"
    home.mkdir()
    plugin_root = _activate_agent_worktrees_context(tmp_path, home, context)
    native = payload / "scripts" / "installation-context" / "installation-context.ps1"
    native_source = native.read_text(encoding="utf-8")
    native.write_text(native_source.replace(
        "$ErrorActionPreference = 'Stop'",
        "[IO.File]::AppendAllText($env:TEST_STATUS_CALLERS, [string]$PID + "
        "[Environment]::NewLine)\n$ErrorActionPreference = 'Stop'", 1,
    ), encoding="utf-8")
    status_callers = tmp_path / "status-callers.txt"
    resolver = payload / "scripts" / "resolve-runtime.ps1"
    if branch == "locked-fast":
        resolver.write_text(
            "$countFile = Join-Path $PSScriptRoot 'resolve-count'\n"
            "$count = if (Test-Path -LiteralPath $countFile) { "
            "[int][IO.File]::ReadAllText($countFile) } else { 0 }\n"
            "[IO.File]::WriteAllText($countFile, [string]($count + 1))\n"
            "$AgentRtPy = if ($count -gt 0) { $env:TEST_PYTHON } else { $null }\n",
            encoding="utf-8",
        )
    else:
        resolver.write_text("$AgentRtPy = $null\n", encoding="utf-8")
        (payload / "scripts" / "install.ps1").write_text(
            "param([Parameter(Position=0)][string]$Action, [string]$InstallDir)\n"
            "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'resolve-runtime.ps1'), "
            "'$AgentRtPy = $env:TEST_PYTHON')\n", encoding="utf-8",
        )
    package = payload / "src" / "agent_worktrees"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "with (Path(os.environ['AGENT_RT_ROOT']) / '.provision.lock').open('r+b'):\n"
        "    print('lock-released')\n", encoding="utf-8",
    )
    environment = _agent_worktrees_test_environment(home, payload)
    environment.update({
        "COPILOT_EXTENSIONS_CONTEXT": str(context),
        "COPILOT_EXTENSIONS_BOOT_TRACE": "1",
        "TEST_STATUS_CALLERS": str(status_callers),
    })
    result = subprocess.run(
        [host, "-NoProfile", "-File", str(payload / "bin" / "payload" / "agent-worktrees.ps1"),
         "--version"],
        env=environment, capture_output=True, text=True, timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "lock-released"
    dispatch = [
        event for event in _read_jsonl(plugin_root / "logs" / "activity.jsonl")
        if event["phase"] == "dispatch"
    ]
    assert [event["path"] for event in dispatch] == [branch]
    callers = status_callers.read_text(encoding="utf-8").splitlines()
    assert len(callers) == (2 if branch == "locked-fast" else 3)
    assert set(callers) == {str(dispatch[0]["pid"])}


@pytest.mark.skipif(os.name != "nt", reason="native Windows governance revalidation")
@pytest.mark.parametrize("host", NATIVE_POWERSHELL_HOSTS or [None])
@pytest.mark.parametrize("boundary", ["before-provision", "after-provision"])
def test_worktrees_in_process_checks_refuse_changed_policy(
    tmp_path: Path, host: str, boundary: str,
) -> None:
    if host is None:
        pytest.skip("PowerShell is unavailable")
    payload = _copied_agent_worktrees_payload(tmp_path, dispatch_only=True)
    context = _stamp_agent_worktrees_context(tmp_path, payload)
    home = tmp_path / "home"
    home.mkdir()
    plugin_root = _activate_agent_worktrees_context(tmp_path, home, context)
    change_policy = (
        "[IO.File]::WriteAllText($env:TEST_POLICY, "
        "'{\"schema\":\"copilot-extensions.installation-mode\",\"version\":1,"
        "\"installationMode\":{\"enabled\":false}}')\n"
    )
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        (change_policy if boundary == "before-provision" else "") + "$AgentRtPy = $null\n",
        encoding="utf-8",
    )
    install_record = tmp_path / "installed.txt"
    (payload / "scripts" / "install.ps1").write_text(
        "param([Parameter(Position=0)][string]$Action, [string]$InstallDir)\n"
        "[IO.File]::WriteAllText($env:TEST_INSTALL_RECORD, 'installed')\n" + change_policy,
        encoding="utf-8",
    )
    environment = _agent_worktrees_test_environment(home, payload)
    environment.update({
        "COPILOT_EXTENSIONS_CONTEXT": str(context),
        "TEST_POLICY": str(home / ".copilot-extensions" / "installation-mode.json"),
        "TEST_INSTALL_RECORD": str(install_record),
    })
    result = subprocess.run(
        [host, "-NoProfile", "-File", str(payload / "bin" / "payload" / "agent-worktrees.ps1"),
         "--version"],
        env=environment, capture_output=True, text=True, timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 126, result.stderr
    assert not result.stdout.strip()
    assert "governance changed" in result.stderr
    assert install_record.exists() == (boundary == "after-provision")
    assert not any(
        event["phase"] == "dispatch"
        for event in _read_jsonl(plugin_root / "logs" / "activity.jsonl")
    )


def test_copied_worktrees_ignores_ambient_materialized_dependencies(tmp_path: Path) -> None:
    source = tmp_path / "plugins" / "agent-worktrees"
    source.mkdir(parents=True)
    (source / "pyproject.toml").write_text(
        '[tool.uv.sources]\n'
        'external = {path = "../../libs/machine-transport", editable = true}\n'
        'owned = {path = "libs/owned", editable = true}\n', encoding="utf-8",
    )
    ignored = _payload_copy_ignore(source)
    assert ignored(str(source / "libs"), ["machine-transport", "owned"]) == {"machine-transport"}
    assert ignored(str(source), [".venv", "src", "libs"]) == {".venv"}


def test_copied_worktrees_import_and_version_use_staged_dependencies(tmp_path: Path) -> None:
    import yaml

    payload = _copied_agent_worktrees_payload(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    environment = _agent_worktrees_test_environment(home, payload)
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    sources = environment["PYTHONPATH"].split(os.pathsep)
    assert all(Path(source).is_relative_to(payload) for source in sources)
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    script = """
import importlib.util, json, pathlib, runpy, sys
# Load only the external YAML dependency, without enabling ambient site-packages.
yaml_file = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "yaml", yaml_file, submodule_search_locations=[str(yaml_file.parent)],
)
module = importlib.util.module_from_spec(spec)
sys.modules["yaml"] = module
spec.loader.exec_module(module)
from agent_worktrees import config
import machine_transport, remote_login_shell
payload = pathlib.Path(sys.argv[2]).resolve()
origins = [pathlib.Path(module.__file__).resolve()
           for module in (config, machine_transport, remote_login_shell)]
assert all(origin.is_relative_to(payload) for origin in origins), origins
print(json.dumps([str(origin.relative_to(payload)) for origin in origins]))
sys.argv = ["agent-worktrees", "--version"]
runpy.run_module("agent_worktrees", run_name="__main__")
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", script, str(Path(yaml.__file__)), str(payload)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    origins, version = result.stdout.strip().splitlines()
    assert len(json.loads(origins)) == 3
    assert version.startswith("agent-worktrees ")


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="canonical Windows profile semantics require native Windows",
)
@pytest.mark.parametrize("policy_state", ["absent", "explicit-false"])
def test_agent_worktrees_required_context_preserves_legacy_default(
    tmp_path: Path,
    policy_state: str,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _copied_agent_worktrees_payload(tmp_path)
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = $env:TEST_PYTHON\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    if policy_state == "explicit-false":
        policy = home / ".copilot-extensions" / "installation-mode.json"
        policy.parent.mkdir(parents=True)
        policy.write_text(
            json.dumps(
                {
                    "schema": "copilot-extensions.installation-mode",
                    "version": 1,
                    "installationMode": {"enabled": False},
                }
            ),
            encoding="utf-8",
        )
    environment = _agent_worktrees_test_environment(home, payload)
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(payload / "bin" / "payload" / "agent-worktrees.ps1"),
            "--version",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("agent-worktrees ")
    assert not (home / ".agent-worktrees").exists()
    if policy_state == "absent":
        assert not (home / ".copilot-extensions").exists()


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_agent_worktrees_requested_context_never_falls_back_to_legacy(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _copied_agent_worktrees_payload(tmp_path)
    context = _stamp_agent_worktrees_context(tmp_path, payload)
    home = tmp_path / "home"
    home.mkdir()
    environment = _agent_worktrees_test_environment(home, payload)
    environment.update(
        {
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "AGENT_WORKTREES_NO_SELFPROVISION": "1",
        }
    )

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(payload / "bin" / "payload" / "agent-worktrees.ps1"),
            "--version",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 126
    assert "requested installation context is not active" in result.stderr
    assert not (home / ".agent-worktrees").exists()


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="canonical Windows profile semantics require native Windows",
)
def test_agent_worktrees_active_context_selects_only_its_cell_root(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _copied_agent_worktrees_payload(tmp_path)
    assert (payload / "src" / "agent_worktrees" / "__main__.py").is_file()
    context = _stamp_agent_worktrees_context(tmp_path, payload)
    home = tmp_path / "home"
    home.mkdir()
    plugin_root = _activate_agent_worktrees_context(tmp_path, home, context)
    root_record = tmp_path / "selected-root.txt"
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "[IO.File]::WriteAllText($env:TEST_ROOT_RECORD, $env:AGENT_RT_ROOT)\n"
        "$AgentRtPy = $env:TEST_PYTHON\n",
        encoding="utf-8",
    )
    environment = _agent_worktrees_test_environment(home, payload)
    environment.update(
        {
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "TEST_ROOT_RECORD": str(root_record),
        }
    )

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(payload / "bin" / "payload" / "agent-worktrees.ps1"),
            "--version",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("agent-worktrees ")
    assert root_record.read_text(encoding="utf-8") == str(plugin_root)
    assert not (home / ".agent-worktrees").exists()


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="canonical Windows profile semantics require native Windows",
)
def test_agent_worktrees_active_context_provisions_only_its_cell_root(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _copied_agent_worktrees_payload(tmp_path)
    assert (payload / "src" / "agent_worktrees" / "__main__.py").is_file()
    context = _stamp_agent_worktrees_context(tmp_path, payload)
    home = tmp_path / "home"
    home.mkdir()
    plugin_root = _activate_agent_worktrees_context(tmp_path, home, context)
    install_record = tmp_path / "install-record.txt"
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = $null\n",
        encoding="utf-8",
    )
    (payload / "scripts" / "install.ps1").write_text(
        "param([Parameter(Position=0)][string]$Action, [string]$InstallDir)\n"
        "[IO.File]::WriteAllText(\n"
        "  $env:TEST_INSTALL_RECORD,\n"
        "  ($Action + '|' + $InstallDir + '|' + $env:COPILOT_EXTENSIONS_CONTEXT)\n"
        ")\n"
        "$resolver = Join-Path $PSScriptRoot 'resolve-runtime.ps1'\n"
        "[IO.File]::WriteAllText($resolver, '$AgentRtPy = $env:TEST_PYTHON' + [Environment]::NewLine)\n",
        encoding="utf-8",
    )
    environment = _agent_worktrees_test_environment(home, payload)
    environment.update(
        {
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "TEST_INSTALL_RECORD": str(install_record),
        }
    )

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(payload / "bin" / "payload" / "agent-worktrees.ps1"),
            "--version",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("agent-worktrees ")
    assert install_record.read_text(encoding="utf-8") == (
        f"install|{plugin_root}|{context}"
    )
    assert not (home / ".agent-worktrees").exists()


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="Windows file-lock serialization",
)
def test_agent_worktrees_namespaced_first_use_is_single_flight(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _copied_agent_worktrees_payload(tmp_path)
    assert (payload / "src" / "agent_worktrees" / "__main__.py").is_file()
    context = _stamp_agent_worktrees_context(tmp_path, payload)
    home = tmp_path / "home"
    home.mkdir()
    _activate_agent_worktrees_context(tmp_path, home, context)
    install_record = tmp_path / "install-record.txt"
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = $null\n",
        encoding="utf-8",
    )
    (payload / "scripts" / "install.ps1").write_text(
        "param([Parameter(Position=0)][string]$Action, [string]$InstallDir)\n"
        "Add-Content -LiteralPath $env:TEST_INSTALL_RECORD -Value $PID\n"
        "Start-Sleep -Seconds 1\n"
        "$resolver = Join-Path $PSScriptRoot 'resolve-runtime.ps1'\n"
        "[IO.File]::WriteAllText($resolver, '$AgentRtPy = $env:TEST_PYTHON' + [Environment]::NewLine)\n",
        encoding="utf-8",
    )
    environment = _agent_worktrees_test_environment(home, payload)
    environment.update(
        {
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "TEST_INSTALL_RECORD": str(install_record),
        }
    )
    command = [
        pwsh,
        "-NoProfile",
        "-File",
        str(payload / "bin" / "payload" / "agent-worktrees.ps1"),
        "--version",
    ]

    first = subprocess.Popen(
        command,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second = subprocess.Popen(
        command,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    first_stdout, first_stderr = first.communicate(timeout=90)
    second_stdout, second_stderr = second.communicate(timeout=90)

    assert first.returncode == 0, first_stderr
    assert second.returncode == 0, second_stderr
    assert first_stdout.startswith("agent-worktrees ")
    assert second_stdout.startswith("agent-worktrees ")
    assert len(install_record.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX payload dispatcher semantics")
def test_agent_worktrees_posix_requested_context_never_uses_legacy(
    tmp_path: Path,
) -> None:
    payload = _copied_agent_worktrees_payload(tmp_path)
    mode_runner = payload / "scripts" / "installation-context" / "installation-context.sh"
    mode_runner.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' "
        "'{\"status\":\"ready\",\"reason\":\"policy-default-false\","
        "\"actualMode\":\"legacy\",\"desiredMode\":\"legacy\"}'\n",
        encoding="utf-8",
    )
    mode_runner.chmod(0o755)
    (payload / "scripts" / "resolve-runtime.sh").write_text(
        'AGENT_RT_PY="$TEST_PYTHON"\n',
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    environment = _agent_worktrees_test_environment(home, payload)
    environment.update(
        {
            "COPILOT_EXTENSIONS_CONTEXT": str(tmp_path / "requested" / "install.json"),
            "AGENT_WORKTREES_NO_SELFPROVISION": "1",
        }
    )

    result = subprocess.run(
        [str(payload / "bin" / "payload" / "agent-worktrees"), "--version"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 126
    assert "requested installation context is not active" in result.stderr
    assert not (home / ".agent-worktrees").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX policy parsing semantics")
@pytest.mark.parametrize(
    ("policy_text", "expected_code"),
    [
        (
            json.dumps(
                {
                    "schema": "copilot-extensions.installation-mode",
                    "version": 1,
                    "installationMode": {"enabled": False},
                }
            ),
            0,
        ),
        ("{\n", 126),
    ],
)
def test_agent_worktrees_posix_simple_legacy_requires_readable_policy(
    tmp_path: Path,
    policy_text: str,
    expected_code: int,
) -> None:
    payload = _copied_agent_worktrees_payload(tmp_path)
    mode_runner = payload / "scripts" / "installation-context" / "installation-context.sh"
    mode_runner.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' "
        "'{\"status\":\"provenance-blocked\",\"reason\":\"source-unresolved\","
        "\"actualMode\":\"legacy\",\"desiredMode\":\"legacy\","
        "\"policy\":{\"state\":\"valid\",\"enabled\":false},"
        "\"legacy\":{\"tombstone\":null,\"disposition\":\"active\"}}'\n",
        encoding="utf-8",
    )
    mode_runner.chmod(0o755)
    fake_python = tmp_path / "fake-python"
    fake_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_python.chmod(0o755)
    (payload / "scripts" / "resolve-runtime.sh").write_text(
        'AGENT_RT_PY="$TEST_PYTHON"\n',
        encoding="utf-8",
    )
    home = tmp_path / "home"
    policy = home / ".copilot-extensions" / "installation-mode.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(policy_text, encoding="utf-8")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "id").write_text(
        "#!/bin/sh\n[ \"${1:-}\" = -u ] && { printf 123; exit 0; }\nexit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getent").write_text(
        f"#!/bin/sh\nprintf 'user:x:123:123::%s:/bin/sh\\n' {shlex.quote(str(home))}\n",
        encoding="utf-8",
    )
    (fake_bin / "id").chmod(0o755)
    (fake_bin / "getent").chmod(0o755)
    environment = _agent_worktrees_test_environment(home, payload)
    environment.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
            "TEST_PYTHON": str(fake_python),
        }
    )
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)

    result = subprocess.run(
        [str(payload / "bin" / "payload" / "agent-worktrees"), "--version"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == expected_code, result.stderr
    if expected_code:
        assert "installation context blocks invocation" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX payload dispatcher semantics")
def test_agent_worktrees_posix_active_context_selects_cell_root(
    tmp_path: Path,
) -> None:
    payload = _copied_agent_worktrees_payload(tmp_path)
    fake_python = tmp_path / "fake-python"
    fake_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_python.chmod(0o755)
    plugin_root = tmp_path / "durable" / "marketplaces" / "example" / "plugins" / "agent-worktrees"
    context = plugin_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    mode_runner = payload / "scripts" / "installation-context" / "installation-context.sh"
    mode_runner.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' "
        f"'{{\"status\":\"ready\",\"reason\":\"namespaced-active\","
        f"\"actualMode\":\"namespaced\",\"desiredMode\":\"namespaced\","
        f"\"runtimeRoot\":\"{plugin_root}\",\"context\":\"{context}\"}}'\n",
        encoding="utf-8",
    )
    mode_runner.chmod(0o755)
    root_record = tmp_path / "selected-root.txt"
    (payload / "scripts" / "resolve-runtime.sh").write_text(
        'printf "%s" "$AGENT_RT_ROOT" >"$TEST_ROOT_RECORD"\n'
        'AGENT_RT_PY="$TEST_PYTHON"\n',
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    environment = _agent_worktrees_test_environment(home, payload)
    environment.update(
        {
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "TEST_ROOT_RECORD": str(root_record),
            "TEST_PYTHON": str(fake_python),
        }
    )

    result = subprocess.run(
        [str(payload / "bin" / "payload" / "agent-worktrees"), "--version"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert root_record.read_text(encoding="utf-8") == str(plugin_root)
    assert not (home / ".agent-worktrees").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group semantics")
def test_agent_worktrees_posix_interrupt_reaps_provisioner_before_unlock(
    tmp_path: Path,
) -> None:
    payload = _copied_agent_worktrees_payload(tmp_path)
    plugin_root = tmp_path / "cell-runtime"
    context = plugin_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    mode_runner = payload / "scripts" / "installation-context" / "installation-context.sh"
    mode_runner.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' "
        f"'{{\"status\":\"ready\",\"reason\":\"namespaced-active\","
        f"\"actualMode\":\"namespaced\",\"desiredMode\":\"namespaced\","
        f"\"runtimeRoot\":\"{plugin_root}\",\"context\":\"{context}\"}}'\n",
        encoding="utf-8",
    )
    mode_runner.chmod(0o755)
    (payload / "scripts" / "resolve-runtime.sh").write_text(
        'AGENT_RT_PY=""\n',
        encoding="utf-8",
    )
    child_pid_record = tmp_path / "child-pid.txt"
    (payload / "scripts" / "install.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "set -m\n"
        "(\n"
        "  trap 'exit 143' INT TERM\n"
        "  printf '%s' \"$BASHPID\" >\"$TEST_CHILD_PID_RECORD\"\n"
        "  sleep 30\n"
        ") &\n"
        "child=$!\n"
        "set +m\n"
        "stop_child() {\n"
        "  kill -- -\"$child\" 2>/dev/null || kill \"$child\" 2>/dev/null || true\n"
        "  wait \"$child\" 2>/dev/null || true\n"
        "  exit 143\n"
        "}\n"
        "trap stop_child INT TERM\n"
        "wait \"$child\"\n",
        encoding="utf-8",
    )
    (payload / "scripts" / "install.sh").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    environment = _agent_worktrees_test_environment(home, payload)
    environment.update(
        {
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "COPILOT_EXT_NO_FLOCK": "1",
            "TEST_CHILD_PID_RECORD": str(child_pid_record),
        }
    )
    process = subprocess.Popen(
        [str(payload / "bin" / "payload" / "agent-worktrees"), "--version"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(100):
        if child_pid_record.exists():
            break
        import time

        time.sleep(0.05)
    assert child_pid_record.exists()
    child_pid = int(child_pid_record.read_text(encoding="utf-8"))

    process.terminate()
    process.communicate(timeout=10)

    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
    assert not (plugin_root / ".provision.lock.pid").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX governance revalidation")
def test_agent_worktrees_posix_revalidates_after_provisioning(
    tmp_path: Path,
) -> None:
    payload = _copied_agent_worktrees_payload(tmp_path)
    plugin_root = tmp_path / "cell-runtime"
    context = plugin_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    status_count = tmp_path / "status-count.txt"
    mode_runner = payload / "scripts" / "installation-context" / "installation-context.sh"
    mode_runner.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"${1:-}\" = validate ]; then\n"
        "  printf '%s\\n' '{\"namespaceGeneration\":1,\"generation\":1}'\n"
        "  exit 0\n"
        "fi\n"
        "count=0\n"
        "[ ! -f \"$TEST_STATUS_COUNT\" ] || count=$(cat \"$TEST_STATUS_COUNT\")\n"
        "count=$((count + 1))\n"
        "printf '%s' \"$count\" >\"$TEST_STATUS_COUNT\"\n"
        "if [ \"$count\" -le 2 ]; then\n"
        "  printf '%s\\n' "
        f"'{{\"status\":\"ready\",\"reason\":\"namespaced-active\","
        f"\"actualMode\":\"namespaced\",\"desiredMode\":\"namespaced\","
        f"\"runtimeRoot\":\"{plugin_root}\",\"context\":\"{context}\","
        f"\"activationGeneration\":1,\"installGeneration\":1}}'\n"
        "else\n"
        "  printf '%s\\n' "
        "'{\"status\":\"maintenance\",\"reason\":\"plugin-maintenance\","
        "\"actualMode\":\"namespaced\",\"desiredMode\":\"namespaced\"}'\n"
        "fi\n",
        encoding="utf-8",
    )
    mode_runner.chmod(0o755)
    (payload / "scripts" / "resolve-runtime.sh").write_text(
        'AGENT_RT_PY=""\n',
        encoding="utf-8",
    )
    install_record = tmp_path / "install-record.txt"
    runtime_record = tmp_path / "runtime-record.txt"
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/bin/sh\nprintf invoked >\"$TEST_RUNTIME_RECORD\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (payload / "scripts" / "install.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "printf installed >\"$TEST_INSTALL_RECORD\"\n"
        "cat >\"$(dirname \"$0\")/resolve-runtime.sh\" <<'EOF'\n"
        'AGENT_RT_PY=\"$TEST_PYTHON\"\n'
        "EOF\n",
        encoding="utf-8",
    )
    (payload / "scripts" / "install.sh").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    environment = _agent_worktrees_test_environment(home, payload)
    environment.update(
        {
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "TEST_STATUS_COUNT": str(status_count),
            "TEST_INSTALL_RECORD": str(install_record),
            "TEST_RUNTIME_RECORD": str(runtime_record),
            "TEST_PYTHON": str(fake_python),
        }
    )

    result = subprocess.run(
        [str(payload / "bin" / "payload" / "agent-worktrees"), "--version"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 126
    assert "governance changed during provisioning" in result.stderr
    assert install_record.read_text(encoding="utf-8") == "installed"
    assert not runtime_record.exists()
    assert status_count.read_text(encoding="utf-8") == "3"


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="canonical Windows profile semantics require native Windows",
)
@pytest.mark.parametrize("policy_state", ["absent", "explicit-false"])
def test_agent_machines_required_context_preserves_absent_policy_legacy_use(
    tmp_path: Path,
    policy_state: str,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _copied_agent_machines_payload(tmp_path)
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = $env:TEST_PYTHON\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    if policy_state == "explicit-false":
        policy = home / ".copilot-extensions" / "installation-mode.json"
        policy.parent.mkdir(parents=True)
        policy.write_text(
            json.dumps(
                {
                    "schema": "copilot-extensions.installation-mode",
                    "version": 1,
                    "installationMode": {"enabled": False},
                }
            ),
            encoding="utf-8",
        )
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(payload),
            "PYTHONPATH": os.pathsep.join(_agent_machines_import_sources(payload)),
            "TEST_PYTHON": sys.executable,
        }
    )
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(payload / "bin" / "agent-machines.ps1"),
            "--version",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads((payload / "plugin.json").read_text())["version"] in result.stdout
    assert not (home / ".agent-machines").exists()
    if policy_state == "absent":
        assert not (home / ".copilot-extensions").exists()


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_agent_machines_requested_context_never_falls_back_to_legacy(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _copied_agent_machines_payload(tmp_path)
    context = _stamp_agent_machines_context(tmp_path, payload)
    home = tmp_path / "home"
    home.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(payload),
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "AGENT_MACHINES_NO_SELFPROVISION": "1",
        }
    )

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(payload / "bin" / "agent-machines.ps1"),
            "--version",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 126
    assert "requested installation context is not active" in result.stderr
    assert not (home / ".agent-machines").exists()


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_agent_machines_active_context_selects_only_its_cell_root(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _copied_agent_machines_payload(tmp_path)
    context = _stamp_agent_machines_context(tmp_path, payload)
    home = tmp_path / "home"
    home.mkdir()
    plugin_root = _activate_agent_machines_context(tmp_path, home, context)
    root_record = tmp_path / "selected-root.txt"
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "[IO.File]::WriteAllText($env:TEST_ROOT_RECORD, $env:AGENT_RT_ROOT)\n"
        "$AgentRtPy = $env:TEST_PYTHON\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(payload),
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "PYTHONPATH": os.pathsep.join(_agent_machines_import_sources(payload)),
            "TEST_PYTHON": sys.executable,
            "TEST_ROOT_RECORD": str(root_record),
        }
    )

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(payload / "bin" / "agent-machines.ps1"),
            "--version",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert root_record.read_text(encoding="utf-8") == str(plugin_root)
    assert not (home / ".agent-machines").exists()


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="canonical Windows profile semantics require native Windows",
)
@pytest.mark.parametrize("blocked_state", [
    "malformed policy", "maintenance", "foreign activation", "orphaned transfer",
])
def test_agent_machines_blocked_context_states_never_run_legacy(
    tmp_path: Path, blocked_state: str,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _copied_agent_machines_payload(tmp_path)
    context = _stamp_agent_machines_context(tmp_path, payload)
    home = tmp_path / "home"
    home.mkdir()
    plugin_root = _activate_agent_machines_context(tmp_path, home, context)
    root_record = tmp_path / "selected-root.txt"
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "[IO.File]::WriteAllText($env:TEST_ROOT_RECORD, $env:AGENT_RT_ROOT)\n"
        "$AgentRtPy = $env:TEST_PYTHON\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(payload),
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "PYTHONPATH": os.pathsep.join(_agent_machines_import_sources(payload)),
            "TEST_PYTHON": sys.executable,
            "TEST_ROOT_RECORD": str(root_record),
        }
    )

    activation = plugin_root / "installation-activation.json"
    original_activation = json.loads(activation.read_text(encoding="utf-8"))
    if blocked_state == "malformed policy":
        policy = home / ".copilot-extensions" / "installation-mode.json"
        policy.write_text("{\n", encoding="utf-8")
    elif blocked_state == "maintenance":
        (plugin_root / "maintenance").write_text("maintenance\n", encoding="utf-8")
    elif blocked_state == "foreign activation":
        foreign_activation = dict(original_activation)
        foreign_activation["environment"] = dict(original_activation["environment"])
        foreign_activation["environment"]["platform"] = "posix"
        activation.write_text(json.dumps(foreign_activation), encoding="utf-8")
    else:
        activation.unlink()
        legacy = home / ".agent-machines"
        legacy.mkdir()
        (legacy / ".installation-ownership.json").write_text(
            json.dumps(
                {
                    "schema": "copilot-extensions.legacy-installation-ownership",
                    "version": 1,
                    "marketplaceId": original_activation["marketplaceId"],
                    "pluginId": "agent-machines",
                    "activation": {
                        "path": str(plugin_root / "missing-activation.json"),
                        "generation": 1,
                    },
                    "environment": original_activation["environment"],
                    "transferredAt": "2026-01-01T00:00:00Z",
                },
            ),
            encoding="utf-8",
        )
    result = subprocess.run(
        [
            pwsh, "-NoProfile", "-File",
            str(payload / "bin" / "agent-machines.ps1"), "--version",
        ],
        env=environment, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 126, f"{blocked_state}: {result.stderr}"
    assert "blocks invocation" in result.stderr
    assert not root_record.exists(), blocked_state


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_agent_machines_removed_policy_keeps_active_cell_authoritative(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _directory_marketplace_agent_machines_payload(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    durable_home = home / ".copilot-extensions"
    context = _stamp_agent_machines_context(
        tmp_path,
        payload,
        explicit_source=False,
        durable_home=durable_home,
    )
    plugin_root = _activate_agent_machines_context(
        tmp_path,
        home,
        context,
        durable_home=durable_home,
    )
    (home / ".copilot-extensions" / "installation-mode.json").unlink()
    root_record = tmp_path / "selected-root.txt"
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "[IO.File]::WriteAllText($env:TEST_ROOT_RECORD, $env:AGENT_RT_ROOT)\n"
        "$AgentRtPy = $env:TEST_PYTHON\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(payload),
            "PYTHONPATH": os.pathsep.join(_agent_machines_import_sources(payload)),
            "TEST_PYTHON": sys.executable,
            "TEST_ROOT_RECORD": str(root_record),
        }
    )
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(payload / "bin" / "agent-machines.ps1"),
            "--version",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert root_record.read_text(encoding="utf-8") == str(plugin_root)
    assert not (home / ".agent-machines").exists()


def test_generates_multiple_commands_and_one_catalog(tmp_path: Path) -> None:
    manifest = _multi_manifest(tmp_path)
    assert generator.process_manifest(manifest, check=False) == []
    generated = generator.expected_files(manifest)
    assert {path.name for path in generated} == {
        "agent-example",
        "agent-example.cmd",
        "agent-example.ps1",
        "example-helper",
        "example-helper.cmd",
        "example-helper.ps1",
        "emit-command-catalog.ps1",
        "emit-command-catalog.sh",
    }
    helper = generated[manifest.parent / "bin" / "example-helper"]
    assert '_command="example-helper"' in helper
    assert '_module="agent_example.helper"' in helper
    catalog = generated[manifest.parent / "scripts" / "emit-command-catalog.sh"]
    assert '"id":"agent-example"' in catalog
    assert '"id":"example-helper"' in catalog
    assert '"plugin": "agent-example"' in catalog


def test_commands_schema_preserves_plugin_identity_with_one_command(
    tmp_path: Path,
) -> None:
    manifest = _multi_manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["commands"] = [
        {
            "command": "example-helper",
            "module": "agent_example.helper",
            "purpose": "Exercise an example helper",
        }
    ]
    manifest.write_text(json.dumps(data), encoding="utf-8")

    generated = generator.expected_files(manifest)
    catalog = generated[manifest.parent / "scripts" / "emit-command-catalog.sh"]
    assert '"plugin": "agent-example"' in catalog
    assert '"id":"example-helper"' in catalog


def test_manifest_can_select_cmd_for_windows_catalog(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["windowsCatalogShim"] = "cmd"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    generated = generator.expected_files(manifest)
    catalog = generated[manifest.parent / "scripts" / "emit-command-catalog.ps1"]
    cmd = generated[manifest.parent / "bin" / "agent-example.cmd"]
    assert r"bin\agent-example.cmd" in catalog
    assert "shell = 'cmd'" in catalog
    assert r'where.exe" pwsh 2^>nul' in cmd
    assert 'set "_PSHOST=%%I"' in cmd
    assert r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" in cmd
    assert '"%_PSHOST%" -NoProfile' in cmd


def test_a_cmd_catalog_warns_that_a_newline_ends_the_command(tmp_path: Path) -> None:
    """cmd.exe ends a command at a newline, so a multi-line argument to a .cmd
    shim is silently cut off: the catalog an agent reads says so -- in the
    single-command and the `commands` manifest forms alike. A PowerShell shim
    passes arguments intact and carries no such note."""
    for build in (_manifest, _multi_manifest):
        root = tmp_path / build.__name__
        root.mkdir()
        manifest = build(root)
        catalog_path = manifest.parent / "scripts" / "emit-command-catalog.ps1"
        plain = generator.expected_files(manifest)[catalog_path]
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["windowsCatalogShim"] = "cmd"
        manifest.write_text(json.dumps(data), encoding="utf-8")
        cmd_catalog = generator.expected_files(manifest)[catalog_path]
        assert "cut off" not in plain, build.__name__
        assert "`cmd.exe` ends the command at a newline" in cmd_catalog, build.__name__
        assert "stdin or a file option" in cmd_catalog, build.__name__


def test_manifest_rejects_unknown_windows_catalog_shim(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["windowsCatalogShim"] = "exe"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid windowsCatalogShim"):
        generator.load_manifest(manifest)


def test_manifest_can_provision_directly_from_self_staging_installer(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["provisionMode"] = "direct"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    generated = generator.expected_files(manifest)
    posix = generated[manifest.parent / "bin" / "agent-example"]
    powershell = generated[manifest.parent / "bin" / "agent-example.ps1"]
    assert 'bash "$_installer" provision' in posix
    assert "payload-dir" not in posix
    assert "$_installer provision" in powershell
    assert "payload-dir" not in powershell


def test_manifest_rejects_unknown_provision_mode(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["provisionMode"] = "ambient"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid provisionMode"):
        generator.load_manifest(manifest)


@pytest.mark.skipif(os.name == "nt", reason="POSIX catalog execution test")
def test_posix_catalog_emits_every_command_id(tmp_path: Path) -> None:
    manifest = _multi_manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    (manifest.parent / "plugin.json").write_text(
        '{"name":"agent-example"}\n', encoding="utf-8"
    )
    env = os.environ.copy()
    env["COPILOT_PLUGIN_ROOT"] = str(manifest.parent)
    result = subprocess.run(
        [str(manifest.parent / "scripts" / "emit-command-catalog.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    outer = json.loads(result.stdout)
    match = re.search(r"```json\n(.*?)\n```", outer["additionalContext"], re.S)
    assert match
    catalog = json.loads(match.group(1))
    assert catalog["plugin"] == "agent-example"
    assert [command["id"] for command in catalog["commands"]] == [
        "agent-example",
        "example-helper",
    ]
    assert all(command["availability"] == "ready" for command in catalog["commands"])


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_powershell_catalog_emits_every_command_id(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    manifest = _multi_manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    (manifest.parent / "plugin.json").write_text(
        '{"name":"agent-example"}\n', encoding="utf-8"
    )
    env = os.environ.copy()
    env.update(
        {
            "COPILOT_PLUGIN_ROOT": str(manifest.parent),
            "USERPROFILE": str(tmp_path / "home"),
        }
    )
    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(manifest.parent / "scripts" / "emit-command-catalog.ps1"),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    outer = json.loads(result.stdout)
    match = re.search(r"```json\n(.*?)\n```", outer["additionalContext"], re.S)
    assert match
    catalog = json.loads(match.group(1))
    assert catalog["plugin"] == "agent-example"
    assert [command["id"] for command in catalog["commands"]] == [
        "agent-example",
        "example-helper",
    ]
    assert all(command["argv"][0].endswith(".ps1") for command in catalog["commands"])
    assert all(command["availability"] == "ready" for command in catalog["commands"])


def test_check_detects_drift(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    assert generator.process_manifest(manifest, check=True) == []
    (manifest.parent / "bin" / "agent-example.ps1").write_text(
        "stale\n", encoding="utf-8"
    )
    assert generator.process_manifest(manifest, check=True)


def test_manifest_validation_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["command"] = "../agent-example"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    try:
        generator.load_manifest(manifest)
    except ValueError as error:
        assert "invalid command" in str(error)
    else:
        raise AssertionError("invalid command was accepted")

    legacy_root = tmp_path / "legacy-plugin"
    legacy_root.mkdir()
    manifest = _manifest(legacy_root)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["plugin"] = "agent-different"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="legacy plugin must equal command"):
        generator.load_manifest(manifest)


def test_multi_command_manifest_rejects_ambiguous_or_duplicate_commands(
    tmp_path: Path,
) -> None:
    manifest = _multi_manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["command"] = "agent-example"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be combined"):
        generator.load_manifest(manifest)

    data.pop("command")
    data["commands"].append(dict(data["commands"][0]))
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate command"):
        generator.load_manifest(manifest)


@pytest.mark.parametrize(
    "command",
    ["install", "resolve-runtime", "emit-command-catalog"],
)
def test_manifest_rejects_generated_script_collisions(
    tmp_path: Path,
    command: str,
) -> None:
    manifest = _multi_manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["outputDir"] = "scripts"
    data["commands"] = [
        {
            "command": command,
            "module": "agent_example.helper",
            "purpose": "Exercise a colliding command",
        }
    ]
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="generated path collision"):
        generator.expected_files(manifest)


def test_manifest_selects_and_requires_installer_entrypoint(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["installer"] = "init"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="installer not found"):
        generator.expected_files(manifest)

    scripts = manifest.parent / "scripts"
    (scripts / "init.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (scripts / "init.ps1").write_text("# generated fixture\n", encoding="utf-8")
    generated = generator.expected_files(manifest)
    posix = generated[manifest.parent / "bin" / "agent-example"]
    powershell = generated[manifest.parent / "bin" / "agent-example.ps1"]
    assert 'scripts/init.sh"' in posix
    assert "'scripts\\init.ps1'" in powershell


@pytest.mark.parametrize("suffix", [".sh", ".ps1"])
def test_manifest_requires_runtime_resolver_pair(tmp_path: Path, suffix: str) -> None:
    manifest = _manifest(tmp_path)
    (manifest.parent / "scripts" / f"resolve-runtime{suffix}").unlink()

    with pytest.raises(ValueError, match="runtime resolver not found"):
        generator.expected_files(manifest)


def test_manifest_supports_nested_payload_output(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["outputDir"] = "bin/payload"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    generated = generator.expected_files(manifest)
    assert manifest.parent / "bin" / "payload" / "agent-example" in generated
    catalog = generated[manifest.parent / "scripts" / "emit-command-catalog.sh"]
    assert 'command_path="$self_root/bin/payload/agent-example"' in catalog


@pytest.mark.parametrize(
    "plugin",
    [
        "agent-bridge",
        "agent-codespaces",
        "agent-containers",
        "agent-dispatch",
        "agent-logger",
        "agent-machines",
        "agent-mcp",
        "agent-ssh",
        "agent-vault",
        "agent-worktrees",
    ],
)
def test_payload_catalog_adopters_publish_payload_catalogs(plugin: str) -> None:
    plugin_root = REPO / "plugins" / plugin
    manifest = plugin_root / "payload-invocation.json"
    data = generator.load_manifest(manifest)
    assert data["plugin"] == plugin
    command_ids = {
        command["command"] for command in data["commands"]
    }
    assert plugin in command_ids

    generated = generator.expected_files(manifest)
    assert generator.process_manifest(manifest, check=True) == []
    output_dir = plugin_root / str(data["outputDir"])
    for command_id in command_ids:
        assert output_dir / command_id in generated

    hooks = json.loads((plugin_root / "hooks.json").read_text(encoding="utf-8"))
    session_hooks = hooks["hooks"]["sessionStart"]
    for shell in ("bash", "powershell"):
        direct_hooks = [
            hook for hook in session_hooks if "emit-command-catalog" in hook[shell]
        ]
        if direct_hooks:
            # Legacy direct-output wiring: the sessionStart hook itself invokes
            # emit-command-catalog.
            assert len(direct_hooks) == 1
            assert "COPILOT_PLUGIN_ROOT" in direct_hooks[0][shell]
            continue
        # Exact-session-writer wiring: the sessionStart hook invokes a writer
        # (write-session-guidance.* or hook_client.py) that composes
        # emit-command-catalog internally rather than emitting it directly.
        writer_hooks = [
            hook
            for hook in session_hooks
            if "write-session-guidance." in hook[shell]
            or "hook_client.py" in hook[shell]
        ]
        assert len(writer_hooks) == 1, (
            f"{plugin} ({shell}): no direct emit-command-catalog hook and no "
            "write-session-guidance/hook_client.py writer hook found"
        )
        assert "COPILOT_PLUGIN_ROOT" in writer_hooks[0][shell]
        for writer_name in (
            "write_session_guidance.py",
            "hook_client.py",
        ):
            writer_path = plugin_root / "scripts" / writer_name
            if writer_path.is_file():
                assert "emit-command-catalog" in writer_path.read_text(
                    encoding="utf-8"
                ), (
                    f"{plugin}: {writer_name} no longer composes "
                    "emit-command-catalog"
                )
                break
        else:
            pytest.fail(
                f"{plugin}: no write_session_guidance.py or hook_client.py "
                "found to compose emit-command-catalog"
            )


def test_skill_catalog_references_name_payload_adopters() -> None:
    reference = re.compile(
        r'<(agent-[a-z0-9-]+) catalog(?: "([a-z][a-z0-9-]*)")? argv\[0\]>'
    )
    references: dict[str, dict[str, list[Path]]] = {}
    capability_paths = [
        *(REPO / "plugins").glob("*/skills/**/*.md"),
        *(REPO / "plugins").glob("*/agents/**/*.md"),
    ]
    for skill in capability_paths:
        for plugin, command in reference.findall(skill.read_text(encoding="utf-8")):
            command_id = command or plugin
            references.setdefault(plugin, {}).setdefault(command_id, []).append(
                skill.relative_to(REPO)
            )

    missing = {
        plugin: paths
        for plugin, paths in references.items()
        if not (REPO / "plugins" / plugin / "payload-invocation.json").is_file()
    }
    assert missing == {}
    missing_commands = {}
    for plugin, command_paths in references.items():
        manifest = generator.load_manifest(
            REPO / "plugins" / plugin / "payload-invocation.json"
        )
        command_ids = {
            command["command"] for command in manifest["commands"]
        }
        unknown = {
            command: paths
            for command, paths in command_paths.items()
            if command not in command_ids
        }
        if unknown:
            missing_commands[plugin] = unknown
    assert missing_commands == {}
    missing_hooks = {}
    for plugin, paths in references.items():
        hooks_path = REPO / "plugins" / plugin / "hooks.json"
        if not hooks_path.is_file():
            missing_hooks[plugin] = paths
            continue
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        session_hooks = hooks.get("hooks", {}).get("sessionStart", [])
        direct_wired = any(
            "emit-command-catalog" in hook.get("bash", "")
            and "emit-command-catalog" in hook.get("powershell", "")
            for hook in session_hooks
        )
        writer_wired = False
        if not direct_wired:
            plugin_root = REPO / "plugins" / plugin
            has_writer_hook = any(
                (
                    "write-session-guidance." in hook.get("bash", "")
                    or "hook_client.py" in hook.get("bash", "")
                )
                and (
                    "write-session-guidance." in hook.get("powershell", "")
                    or "hook_client.py" in hook.get("powershell", "")
                )
                for hook in session_hooks
            )
            composes_catalog = any(
                (plugin_root / "scripts" / writer_name).is_file()
                and "emit-command-catalog"
                in (plugin_root / "scripts" / writer_name).read_text(
                    encoding="utf-8"
                )
                for writer_name in (
                    "write_session_guidance.py",
                    "hook_client.py",
                )
            )
            writer_wired = has_writer_hook and composes_catalog
        if not (direct_wired or writer_wired):
            missing_hooks[plugin] = paths
    assert missing_hooks == {}


@pytest.mark.skipif(os.name == "nt", reason="POSIX catalog test")
def test_posix_catalog_fails_open_when_python_fails(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    python = fake_bin / "python3"
    python.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    python.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["COPILOT_PLUGIN_ROOT"] = str(manifest.parent)
    result = subprocess.run(
        [str(manifest.parent / "scripts" / "emit-command-catalog.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_posix_shim_preserves_args_exit_and_project_cwd(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["outputDir"] = "bin/payload"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "resolve-runtime.sh").write_text(
        'AGENT_RT_PY="$AGENT_RT_ROOT/versions/test/bin/python"\n',
        encoding="utf-8",
    )

    home = tmp_path / "home"
    fake_python = home / ".agent-example" / "versions" / "test" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$PWD|$*"\nexit 23\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    project = tmp_path / "project"
    project.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "COPILOT_PROJECT_DIR": str(project),
        }
    )
    result = subprocess.run(
        [str(plugin / "bin" / "payload" / "agent-example"), "search", "two words"],
        cwd=plugin,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 23
    assert result.stdout.strip() == f"{project}|-m agent_example search two words"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_posix_shim_boot_trace_is_durable_by_default_and_stderr_is_still_opt_in(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    canonical_resolver = (
        REPO / "libs" / "versioned-runtime" / "resolve-runtime.sh"
    ).read_text(encoding="utf-8")
    (scripts / "resolve-runtime.sh").write_text(canonical_resolver, encoding="utf-8")

    home = tmp_path / "home"
    runtime_root = home / ".agent-example"
    version = "1.2.3"
    slot = runtime_root / "versions" / version
    fake_python = slot / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    _write_runtime_completion(slot, version)
    (runtime_root / "current-version").write_text(f"{version}\n", encoding="utf-8")
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "agent_example.py").write_text(
        "import sys\n"
        "print('stdout:' + '|'.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "PYTHONPATH": str(module_dir),
        }
    )
    command = [str(plugin / "bin" / "agent-example"), "alpha", "beta"]

    baseline = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert baseline.stdout.strip() == "stdout:alpha|beta"
    assert baseline.stderr == ""
    durable = _read_jsonl(runtime_root / "logs" / "boot-trace.jsonl")
    assert [rec["phase"] for rec in durable] == [
        "shim-start",
        "resolver-marker-start",
        "resolver-marker-result",
        "resolver-slot-result",
        "resolver-loaded",
        "dispatch",
    ]
    assert [rec["source"] for rec in durable] == [
        "shim",
        "resolver",
        "resolver",
        "resolver",
        "shim",
        "shim",
    ]
    assert [int(rec["t_ms"]) for rec in durable] == sorted(
        int(rec["t_ms"]) for rec in durable
    )
    assert durable[1]["resolution_source"] == "current-version"
    assert durable[2]["result"] == "hit"
    assert durable[2]["version"] == version
    assert durable[-1]["path"] == "fast"

    traced = subprocess.run(
        command,
        env={**env, "COPILOT_EXTENSIONS_BOOT_TRACE": "1"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert traced.stdout == baseline.stdout
    assert traced.stderr
    phases = [
        match.group(1)
        for match in re.finditer(r"phase=([a-z-]+)", traced.stderr)
    ]
    assert phases == [
        "shim-start",
        "resolver-marker-start",
        "resolver-marker-result",
        "resolver-slot-result",
        "resolver-loaded",
        "dispatch",
    ]
    assert "::boot-trace:: plugin=agent-example" in traced.stderr
    assert "stdout:" not in traced.stderr
    assert len(_read_jsonl(runtime_root / "logs" / "boot-trace.jsonl")) == 12


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_posix_shim_boot_trace_survives_first_use_before_runtime_root_exists(
    tmp_path: Path,
) -> None:
    """Regression: the earliest phases (``shim-start``, the resolver's own
    marker/slot phases) fire before ``_runtime_root``/``$_rt_root`` has ever
    been created on this machine -- that is the entire first-use case this
    always-on facility exists to observe. An earlier revision gated the
    durable-log append on ``[ -d "$_runtime_root" ]``/``[ -d "$_rt_root" ]``,
    which returned before the very ``mkdir -p`` that would have created it,
    silently dropping every phase from a real first launch (Copilot review,
    PR #3310)."""
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    canonical_resolver = (
        REPO / "libs" / "versioned-runtime" / "resolve-runtime.sh"
    ).read_text(encoding="utf-8")
    (scripts / "resolve-runtime.sh").write_text(canonical_resolver, encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir()
    runtime_root = home / ".agent-example"
    assert not runtime_root.exists()  # the exact first-use precondition

    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "agent_example.py").write_text(
        "import sys\nprint('stdout:' + '|'.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "PYTHONPATH": str(module_dir),
            "AGENT_EXAMPLE_NO_SELFPROVISION": "1",
        }
    )
    command = [str(plugin / "bin" / "agent-example"), "alpha"]

    # No installed runtime and self-provisioning disabled -> the shim exits
    # non-zero, but the durable phases up to that failure must still have
    # been recorded, proving the log append never depended on the runtime
    # root already existing.
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode != 0

    log_path = runtime_root / "logs" / "boot-trace.jsonl"
    assert log_path.exists(), "runtime root + log dir must be created on demand"
    durable = _read_jsonl(log_path)
    phases = [rec["phase"] for rec in durable]
    assert "shim-start" in phases
    assert "resolver-marker-start" in phases
    assert "resolver-marker-result" in phases


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_posix_shim_boot_trace_write_failures_are_non_fatal(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    canonical_resolver = (
        REPO / "libs" / "versioned-runtime" / "resolve-runtime.sh"
    ).read_text(encoding="utf-8")
    (scripts / "resolve-runtime.sh").write_text(canonical_resolver, encoding="utf-8")

    home = tmp_path / "home"
    runtime_root = home / ".agent-example"
    version = "1.2.3"
    slot = runtime_root / "versions" / version
    fake_python = slot / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    _write_runtime_completion(slot, version)
    (runtime_root / "current-version").write_text(f"{version}\n", encoding="utf-8")
    (runtime_root / "logs").write_text("not-a-directory\n", encoding="utf-8")
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "agent_example.py").write_text(
        "print('ok')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "PYTHONPATH": str(module_dir),
        }
    )

    result = subprocess.run(
        [str(plugin / "bin" / "agent-example")],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "ok"
    assert result.stderr == ""
    assert (runtime_root / "logs").is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_multi_command_shim_dispatches_its_own_module(tmp_path: Path) -> None:
    manifest = _multi_manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    (scripts / "resolve-runtime.sh").write_text(
        'AGENT_RT_PY="$AGENT_RT_ROOT/versions/test/bin/python"\n',
        encoding="utf-8",
    )
    home = tmp_path / "home"
    fake_python = home / ".agent-example" / "versions" / "test" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*"\nexit 0\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
        }
    )
    result = subprocess.run(
        [str(plugin / "bin" / "example-helper"), "two words"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "-m agent_example.helper two words"


def test_multi_command_traces_use_manifest_plugin_identity(tmp_path: Path) -> None:
    manifest = _multi_manifest(tmp_path)

    generated = generator.expected_files(manifest)

    posix = generated[manifest.parent / "bin" / "example-helper"]
    powershell = generated[manifest.parent / "bin" / "example-helper.ps1"]
    assert '_plugin="agent-example"' in posix
    assert 'COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN="$_plugin"' in posix
    assert "$_plugin = 'agent-example'" in powershell
    assert "$env:COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN = $_plugin" in powershell


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_posix_shim_ignores_a_different_plugins_copilot_plugin_root(
    tmp_path: Path,
) -> None:
    """A caller plugin's own ``COPILOT_PLUGIN_ROOT`` must never block a
    cross-plugin subprocess invocation of a *different* plugin's shim -- e.g.
    a picker running under one plugin shelling out to another's CLI. The shim
    derives its payload strictly from its own file location and no longer
    consults ``COPILOT_PLUGIN_ROOT`` at all (copilot-extensions #2650 fallout:
    that check produced a false-positive "payload context mismatch" for every
    such legitimate cross-plugin call)."""
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    env = os.environ.copy()
    env.update({"HOME": str(tmp_path / "home"), "COPILOT_PLUGIN_ROOT": str(other)})
    result = subprocess.run(
        [str(plugin / "bin" / "agent-example"), "status"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "payload context mismatch" not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_first_use_provision_is_serialized(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "resolve-runtime.sh").write_text(
        'AW_PY=""\n'
        'p="$AGENT_RT_ROOT/versions/test/bin/python"\n'
        '[ -x "$p" ] && AW_PY="$p"\n'
        'AGENT_RT_PY="$AW_PY"\n'
        "true\n",
        encoding="utf-8",
    )
    installer = scripts / "install.sh"
    installer.write_text(
        '#!/bin/bash\nset -eu\nroot="$HOME/.agent-example"\n'
        'plugin="$(cd "$(dirname "$0")/.." && pwd)"\n'
        'case "$1" in\n'
        '  stamp) mkdir -p "$root"; printf "%s\\n" "$plugin" > "$root/payload-dir" ;;\n'
        '  provision)\n'
        '    printf "provision\\n" >> "$root/provision-count"\n'
        '    sleep 0.5\n'
        '    mkdir -p "$root/versions/test/bin"\n'
        '    printf "%s\\n" "#!/bin/sh" "exit 0" > "$root/versions/test/bin/python"\n'
        '    chmod +x "$root/versions/test/bin/python" ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    installer.chmod(0o755)

    home = tmp_path / "home"
    home.mkdir()
    shadow_bin = tmp_path / "shadow-bin"
    shadow_bin.mkdir()
    shadow_marker = tmp_path / "shadow-called"
    shadow = shadow_bin / "agent-example"
    shadow.write_text(
        f'#!/bin/sh\nprintf called > "{shadow_marker}"\nexit 99\n',
        encoding="utf-8",
    )
    shadow.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "COPILOT_EXT_NO_FLOCK": "1",
            "PATH": f"{shadow_bin}{os.pathsep}{env['PATH']}",
        }
    )
    lock = home / ".agent-example" / ".provision.lock.pid"
    lock.parent.mkdir(parents=True)
    lock.symlink_to("999999999")
    command = [str(plugin / "bin" / "agent-example"), "status"]
    first = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    second = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    _first_out, first_err = first.communicate(timeout=10)
    _second_out, second_err = second.communicate(timeout=10)
    assert first.returncode == 0, first_err
    assert second.returncode == 0, second_err
    count = (home / ".agent-example" / "provision-count").read_text(
        encoding="utf-8"
    )
    assert count.splitlines() == ["provision"]
    assert not shadow_marker.exists()


def test_agent_machines_dispatchers_share_the_legacy_installer_lock() -> None:
    scripts = REPO / "plugins" / "agent-machines" / "scripts"
    posix = (scripts / "invoke-payload-runtime.sh").read_text(encoding="utf-8")
    powershell = (scripts / "invoke-payload-runtime.ps1").read_text(encoding="utf-8")

    assert '$RUNTIME_ROOT/.provision.lock"' in posix
    assert '$RUNTIME_ROOT/.provision.lock.pid"' in posix
    assert "Join-Path $runtimeRoot '.provision.lock'" in powershell
    assert ".payload-provision.lock" not in posix
    assert ".payload-provision.lock" not in powershell


def test_windows_templates_preserve_context_and_release_payload_cwd(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    generated = generator.expected_files(manifest)
    powershell = next(
        content for path, content in generated.items() if path.suffix == ".ps1"
    )
    assert "[IO.Directory]::SetCurrentDirectory($_outside)" in powershell
    assert "[IO.Directory]::GetCurrentDirectory()" in powershell
    assert "StartsWith($_payloadPrefix" in powershell
    assert "[IO.FileShare]::None" in powershell


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_powershell_shim_preserves_sibling_cwd_and_leaves_payload(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["outputDir"] = "bin/payload"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    scripts.mkdir(exist_ok=True)
    python_literal = str(Path(sys.executable)).replace("'", "''")
    (scripts / "resolve-runtime.ps1").write_text(
        f"$AgentRtPy = '{python_literal}'\n",
        encoding="utf-8",
    )
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "agent_example.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "print(f\"{Path.cwd()}|{' '.join(sys.argv[1:])}\")\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    sibling = tmp_path / "plugin-backup"
    project.mkdir()
    sibling.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "COPILOT_PROJECT_DIR": str(project),
            "PYTHONPATH": str(module_dir),
        }
    )
    command = [
        pwsh,
        "-NoProfile",
        "-File",
        str(plugin / "bin" / "payload" / "agent-example.ps1"),
        "status",
    ]
    sibling_result = subprocess.run(
        command, cwd=sibling, env=env, capture_output=True, text=True, check=True
    )
    assert sibling_result.stdout.strip() == (
        f"{sibling}|status"
    )
    payload_result = subprocess.run(
        command, cwd=plugin, env=env, capture_output=True, text=True, check=True
    )
    assert payload_result.stdout.strip() == (
        f"{project}|status"
    )
    split_location_command = [
        pwsh, "-NoProfile", "-Command",
        f"Set-Location -LiteralPath '{str(sibling).replace(chr(39), chr(39) * 2)}'; "
        f"& '{str(plugin / 'bin' / 'payload' / 'agent-example.ps1').replace(chr(39), chr(39) * 2)}' status",
    ]
    split_result = subprocess.run(
        split_location_command, cwd=plugin, env=env,
        capture_output=True, text=True, check=True,
    )
    assert split_result.stdout.strip() == f"{sibling}|status"
    for unsafe_project in (plugin, plugin / "scripts"):
        env["COPILOT_PROJECT_DIR"] = str(unsafe_project)
        fallback_result = subprocess.run(
            command, cwd=plugin, env=env,
            capture_output=True, text=True, check=True,
        )
        assert fallback_result.stdout.strip() == f"{home}|status"
    env.update({"HOME": str(plugin), "USERPROFILE": str(plugin)})
    unsafe_home = subprocess.run(
        command, cwd=plugin, env=env, capture_output=True, text=True,
    )
    assert unsafe_home.returncode == 1
    assert "HOME is unavailable or inside the payload" in unsafe_home.stderr


@pytest.mark.skipif(os.name != "nt" or shutil.which("pwsh") is None, reason="Windows registry provider")
@pytest.mark.parametrize("template", ["powershell-shim.tmpl", "dispatcher-powershell.tmpl"])
def test_powershell_cwd_boundary_rejects_registry_provider(
    tmp_path: Path, template: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    payload = tmp_path / "payload"
    payload.mkdir()
    source = (REPO / "libs" / "payload-invocation" / "templates" / template).read_text()
    boundary = source[source.index("$_location = Get-Location"):source.index("\n}\n", source.index("} elseif ($_logicalInPayload")) + 3]
    script = home / "boundary.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$_payloadRoot = '{str(payload).replace(chr(39), chr(39) * 2)}'\n"
        "$_command = 'test'\nSet-Location -LiteralPath 'HKCU:\\Software'\n"
        + boundary + "\n[IO.Directory]::GetCurrentDirectory()\n",
        encoding="utf-8",
    )
    for project, expected in ((str(home), home), ("HKCU:\\Software", home)):
        result = subprocess.run(
            [shutil.which("pwsh"), "-NoProfile", "-File", str(script)],
            cwd=payload, env={**os.environ, "HOME": str(home), "USERPROFILE": str(home), "COPILOT_PROJECT_DIR": project},
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert Path(result.stdout.strip()).resolve() == expected.resolve()


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="native Windows PowerShell shim test",
)
def test_required_dispatcher_shim_defers_boot_trace_to_the_inner_dispatcher(
    tmp_path: Path,
) -> None:
    """Regression (Copilot review, PR #3310): the outer dispatcher shim's
    own `$_runtimeRoot`/`$_runtime_root` is ALWAYS the legacy root -- an
    installationContext=required plugin's active root may actually be a
    resolved namespaced context only the inner, installation-context-aware
    dispatcher knows how to determine. The outer shim must therefore never
    itself write shim-start/dispatch durably (that would either lose the
    record or misroute it into a stale legacy log); it must instead forward
    `shim-start`'s own timestamp via `COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_
    START_MS` and let the inner dispatcher log both phases once it has
    resolved the genuinely active root."""
    pwsh = shutil.which("pwsh")
    assert pwsh
    manifest = _required_context_manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-machines"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    # A fake inner dispatcher standing in for a real installation-context-
    # aware one (e.g. agent-worktrees' own invoke-payload-runtime.ps1) --
    # this one does NOT itself consume the forwarded env var, so this test
    # asserts what the OUTER shim's own contract guarantees regardless of
    # whether any given inner dispatcher has adopted forwarding yet.
    (scripts / "invoke-payload-runtime.ps1").write_text(
        "Write-Output ('stdout:' + ($args -join '|'))\n"
        "Write-Output ('shim_start_ms=' + $env:COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS)\n",
        encoding="utf-8",
    )

    home = tmp_path / "home"
    (home / ".agent-example").mkdir(parents=True)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
        }
    )
    command = [
        pwsh,
        "-NoProfile",
        "-File",
        str(plugin / "bin" / "agent-machines.ps1"),
        "alpha",
        "beta",
    ]

    baseline = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    stdout_lines = baseline.stdout.strip().splitlines()
    assert stdout_lines[0] == "stdout:alpha|beta"
    # The outer shim forwarded a real, non-empty timestamp to the inner
    # dispatcher via the env var -- proving the forwarding contract works,
    # even though this fake inner dispatcher doesn't itself log it.
    assert stdout_lines[1].startswith("shim_start_ms=")
    forwarded_ms = stdout_lines[1].removeprefix("shim_start_ms=")
    assert forwarded_ms.isdigit()
    assert baseline.stderr == ""
    # The outer shim must NEVER itself write a durable record for these two
    # phases -- only the inner dispatcher may, once it has resolved the
    # genuinely active root. This fake inner dispatcher doesn't, so no
    # boot-trace.jsonl should exist at all.
    assert not (home / ".agent-example" / "logs" / "boot-trace.jsonl").exists()

    traced = subprocess.run(
        command,
        env={**env, "COPILOT_EXTENSIONS_BOOT_TRACE": "1"},
        capture_output=True,
        text=True,
        check=True,
    )
    traced_lines = traced.stdout.strip().splitlines()
    assert traced_lines[0] == "stdout:alpha|beta"
    assert traced_lines[1].startswith("shim_start_ms=")
    assert [
        match.group(1)
        for match in re.finditer(r"phase=([a-z-]+)", traced.stderr)
    ] == ["shim-start", "dispatch"]
    # Still no durable write, opt-in stderr trace or not.
    assert not (home / ".agent-example" / "logs" / "boot-trace.jsonl").exists()


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="native Windows PowerShell shim test",
)
def test_powershell_shim_boot_trace_is_opt_in_and_stderr_only(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    canonical_resolver = (
        REPO / "libs" / "versioned-runtime" / "resolve-runtime.ps1"
    ).read_text(encoding="utf-8")
    (scripts / "resolve-runtime.ps1").write_text(canonical_resolver, encoding="utf-8")

    home = tmp_path / "home"
    runtime_root = home / ".agent-example"
    version = "2.3.4"
    slot = runtime_root / "versions" / version
    fake_python = slot / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, fake_python)
    _write_runtime_completion(slot, version)
    (runtime_root / "current-version").write_text(f"{version}\n", encoding="utf-8")
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "agent_example.py").write_text(
        "import sys\n"
        "print('stdout:' + '|'.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "PYTHONPATH": str(module_dir),
        }
    )
    command = [
        pwsh,
        "-NoProfile",
        "-File",
        str(plugin / "bin" / "agent-example.ps1"),
        "alpha",
        "beta",
    ]

    baseline = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert baseline.stdout.strip() == "stdout:alpha|beta"
    assert baseline.stderr == ""
    durable = _read_jsonl(runtime_root / "logs" / "boot-trace.jsonl")
    assert [rec["phase"] for rec in durable] == [
        "shim-start",
        "resolver-marker-start",
        "resolver-marker-result",
        "resolver-slot-result",
        "resolver-loaded",
        "dispatch",
    ]
    assert [rec["source"] for rec in durable] == [
        "shim",
        "resolver",
        "resolver",
        "resolver",
        "shim",
        "shim",
    ]
    assert [int(rec["t_ms"]) for rec in durable] == sorted(
        int(rec["t_ms"]) for rec in durable
    )
    assert durable[1]["resolution_source"] == "current-version"
    assert durable[2]["result"] == "hit"
    assert durable[2]["version"] == version
    assert durable[-1]["path"] == "fast"

    traced = subprocess.run(
        command,
        env={**env, "COPILOT_EXTENSIONS_BOOT_TRACE": "1"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert traced.stdout == baseline.stdout
    assert traced.stderr
    phases = [
        match.group(1)
        for match in re.finditer(r"phase=([a-z-]+)", traced.stderr)
    ]
    assert phases == [
        "shim-start",
        "resolver-marker-start",
        "resolver-marker-result",
        "resolver-slot-result",
        "resolver-loaded",
        "dispatch",
    ]
    assert "::boot-trace:: plugin=agent-example" in traced.stderr
    assert "stdout:" not in traced.stderr
    assert len(_read_jsonl(runtime_root / "logs" / "boot-trace.jsonl")) == 12


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="native Windows PowerShell shim test",
)
def test_powershell_shim_boot_trace_write_failures_are_non_fatal(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    canonical_resolver = (
        REPO / "libs" / "versioned-runtime" / "resolve-runtime.ps1"
    ).read_text(encoding="utf-8")
    (scripts / "resolve-runtime.ps1").write_text(canonical_resolver, encoding="utf-8")

    home = tmp_path / "home"
    runtime_root = home / ".agent-example"
    version = "2.3.4"
    slot = runtime_root / "versions" / version
    fake_python = slot / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, fake_python)
    _write_runtime_completion(slot, version)
    (runtime_root / "current-version").write_text(f"{version}\n", encoding="utf-8")
    (runtime_root / "logs").write_text("not-a-directory\n", encoding="utf-8")
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "agent_example.py").write_text("print('ok')\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "PYTHONPATH": str(module_dir),
        }
    )

    result = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(plugin / "bin" / "agent-example.ps1")],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "ok"
    assert result.stderr == ""
    assert (runtime_root / "logs").is_file()


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="native Windows PowerShell shim test",
)
def test_powershell_shim_boot_trace_lines_survive_concurrent_invocations(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    canonical_resolver = (
        REPO / "libs" / "versioned-runtime" / "resolve-runtime.ps1"
    ).read_text(encoding="utf-8")
    (scripts / "resolve-runtime.ps1").write_text(canonical_resolver, encoding="utf-8")

    home = tmp_path / "home"
    runtime_root = home / ".agent-example"
    version = "2.3.4"
    slot = runtime_root / "versions" / version
    fake_python = slot / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, fake_python)
    _write_runtime_completion(slot, version)
    (runtime_root / "current-version").write_text(f"{version}\n", encoding="utf-8")
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "agent_example.py").write_text("print('ok')\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "PYTHONPATH": str(module_dir),
        }
    )
    command = [pwsh, "-NoProfile", "-File", str(plugin / "bin" / "agent-example.ps1")]

    procs = [
        subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    for proc in procs:
        out, err = proc.communicate(timeout=15)
        assert proc.returncode == 0, err
        assert out.strip() == "ok"
        assert err == ""

    records = _read_jsonl(runtime_root / "logs" / "boot-trace.jsonl")
    assert len(records) >= 9
    by_pid: dict[int, list[dict[str, object]]] = {}
    for rec in records:
        by_pid.setdefault(int(rec["pid"]), []).append(rec)
    assert len(by_pid) == 3
    for pid_records in by_pid.values():
        phases = [str(rec["phase"]) for rec in pid_records]
        expected = [
            "shim-start",
            "resolver-marker-start",
            "resolver-marker-result",
            "resolver-slot-result",
            "resolver-loaded",
            "dispatch",
        ]
        cursor = 0
        for phase in phases:
            while cursor < len(expected) and expected[cursor] != phase:
                cursor += 1
            assert cursor < len(expected), phases
            cursor += 1
