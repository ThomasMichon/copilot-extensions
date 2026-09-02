"""Tests for the reusable exhaustive sessionStart conformance scanner."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "scripts" / "aggregate_context.py"
SCANNER = PLUGIN / "scripts" / "session_context_conformance.py"
SPEC = importlib.util.spec_from_file_location(
    "session_context_conformance_tests",
    SCANNER,
)
assert SPEC is not None and SPEC.loader is not None
CONFORMANCE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CONFORMANCE
SPEC.loader.exec_module(CONFORMANCE)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _broken_runtime_plugin(root: Path) -> Path:
    plugin = root / "agent-example"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    (plugin / "pyproject.toml").write_text(
        '[project]\nname = "agent-example"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    _write_json(
        plugin / "plugin.json",
        {
            "name": "agent-example",
            "hooks": "hooks.json",
            "sessionContext": "session-context.json",
        },
    )
    _write_json(
        plugin / "hooks.json",
        {
            "version": 1,
            "hooks": {
                "sessionStart": [
                    {
                        "type": "command",
                        "bash": (
                            "bash scripts/invoke-context-contributor.sh "
                            "wrong@market wrong scripts/missing.sh"
                        ),
                        "powershell": (
                            "& scripts/invoke-context-contributor.ps1 "
                            "wrong@market wrong scripts/missing.ps1"
                        ),
                        "timeoutSec": 15,
                    }
                ]
            },
        },
    )
    _write_json(
        plugin / "session-context.json",
        {
            "schema": "wrong.schema",
            "version": 1,
            "complete": False,
            "sessionStart": {
                "sideEffects": "restart-safe-idempotent",
                "context": "none",
            },
            "contributors": [
                {
                    "id": "main",
                    "pure": False,
                    "timeoutSeconds": 0,
                    "maxBytes": 0,
                    "bash": ["scripts/missing.sh"],
                    "powershell": ["scripts/missing.ps1"],
                }
            ],
        },
    )
    _write_json(
        plugin / "payload-invocation.json",
        {
            "schema": "copilot-extensions.payload-invocation",
            "version": 1,
            "command": "agent-example",
            "purpose": "Operate the example runtime",
            "runtimeRoot": ".agent-example",
        },
    )
    (scripts / "invoke-context-contributor.sh").write_text(
        "stale\n",
        encoding="utf-8",
    )
    (scripts / "invoke-context-contributor.ps1").write_text(
        "stale\n",
        encoding="utf-8",
    )
    return plugin


def _retarget_contributor_hooks(plugin: Path, source: str) -> None:
    declaration = json.loads(
        (plugin / "session-context.json").read_text(encoding="utf-8")
    )
    hooks = json.loads((plugin / "hooks.json").read_text(encoding="utf-8"))
    entries = hooks["hooks"]["sessionStart"]
    for contributor in declaration["contributors"]:
        entry = next(
            item
            for item in entries
            if contributor["id"] in str(item.get("bash", ""))
            and "invoke-context-contributor.sh" in str(item.get("bash", ""))
        )
        entry["bash"] = CONFORMANCE.canonical_bash_hook(
            source,
            contributor,
        )
        entry["powershell"] = CONFORMANCE.canonical_powershell_hook(
            source,
            contributor,
        )
    (plugin / "hooks.json").write_text(json.dumps(hooks), encoding="utf-8")


def test_library_reports_all_independent_violations(tmp_path: Path) -> None:
    broken = _broken_runtime_plugin(tmp_path)
    report = CONFORMANCE.scan_plugins(
        [
            CONFORMANCE.PluginTarget(
                "agent-example@example-marketplace",
                broken,
            ),
            CONFORMANCE.PluginTarget(
                "missing@example-marketplace",
                tmp_path / "missing",
            ),
        ],
        authority_source="context-injection@example-marketplace",
        wrapper_root=PLUGIN,
    )

    codes = {item.code for item in report.violations}
    assert report.ok is False
    assert {
        "plugin-payload-missing",
        "context-declaration-incomplete",
        "session-start-behavior-incompatible",
        "contributor-not-pure",
        "contributor-bounds-invalid",
        "contributor-command-missing",
        "contributor-hook-drift",
        "producer-wrapper-drift",
        "runtime-command-catalog-missing",
        "runtime-command-catalog-payload-missing",
    } <= codes


def test_cross_marketplace_scanner_proves_external_authority_topology(
    tmp_path: Path,
) -> None:
    marketplace = tmp_path / "market-b"
    producer = marketplace / "plugins" / "agent-index"
    shutil.copytree(PLUGIN.parent / "agent-index", producer)
    _retarget_contributor_hooks(producer, "agent-index@market-b")
    authority = tmp_path / "authority" / "context-injection"
    shutil.copytree(PLUGIN, authority)
    _write_json(
        marketplace / ".github" / "plugin" / "marketplace.json",
        {
            "name": "market-b",
            "plugins": [
                {
                    "name": "agent-index",
                    "source": "plugins/agent-index",
                }
            ],
        },
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate",
            "--marketplace-root",
            str(marketplace),
            "--authority-root",
            str(authority),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["ok"] is True

    unadopted = marketplace / "plugins" / "context-injection"
    shutil.copytree(PLUGIN, unadopted)
    marketplace_path = (
        marketplace / ".github" / "plugin" / "marketplace.json"
    )
    marketplace_data = json.loads(
        marketplace_path.read_text(encoding="utf-8")
    )
    marketplace_data["plugins"].append(
        {
            "name": "context-injection",
            "source": "plugins/context-injection",
        }
    )
    marketplace_path.write_text(
        json.dumps(marketplace_data),
        encoding="utf-8",
    )
    ambiguous = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate",
            "--marketplace-root",
            str(marketplace),
            "--authority-root",
            str(authority),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ambiguous.returncode == 1
    assert "aggregate-authority-missing" in {
        item["code"] for item in json.loads(ambiguous.stdout)["violations"]
    }

    shutil.rmtree(unadopted)
    marketplace_data["plugins"].pop()
    marketplace_path.write_text(
        json.dumps(marketplace_data),
        encoding="utf-8",
    )
    (producer / "scripts" / "resolve_context_authority.py").unlink()
    targets = [
        CONFORMANCE.PluginTarget("agent-index@market-b", producer),
        CONFORMANCE.PluginTarget(
            "context-injection@copilot-extensions",
            authority,
        ),
    ]
    report = CONFORMANCE.scan_plugins(
        targets,
        authority_source="context-injection@copilot-extensions",
        wrapper_root=authority,
    )
    assert "producer-authority-resolver-missing" in {
        item.code for item in report.violations
    }


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing-engine-path", "aggregate-authority-engine-missing"),
        (
            "incompatible-engine",
            "aggregate-authority-engine-incompatible",
        ),
        ("unrelated-hook", "aggregate-authority-hook-drift"),
        ("duplicate-hook", "aggregate-authority-hook-count"),
        ("short-timeout", "aggregate-authority-hook-timeout"),
    ],
)
def test_aggregate_authority_contract_is_exact(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    authority = tmp_path / "context-injection"
    shutil.copytree(PLUGIN, authority)
    manifest_path = authority / "plugin.json"
    hooks_path = authority / "hooks.json"
    if mutation == "missing-engine-path":
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.pop("sessionContextEngine")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "incompatible-engine":
        _write_json(
            authority / "engine.json",
            {
                "schema": "copilot-extensions.context-injection-engine",
                "version": 4,
            },
        )
    else:
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        entries = hooks["hooks"]["sessionStart"]
        if mutation == "unrelated-hook":
            entries[0]["bash"] = "printf '{}'"
            entries[0]["powershell"] = "[Console]::Out.Write('{}')"
        elif mutation == "duplicate-hook":
            entries.append(dict(entries[0]))
        else:
            entries[0]["timeoutSec"] = 1
        hooks_path.write_text(json.dumps(hooks), encoding="utf-8")

    report = CONFORMANCE.scan_plugins(
        [
            CONFORMANCE.PluginTarget(
                "context-injection@copilot-extensions",
                authority,
            )
        ],
        authority_source="context-injection@copilot-extensions",
        wrapper_root=authority,
    )

    assert expected_code in {item.code for item in report.violations}


@pytest.mark.parametrize("platform", ["bash", "powershell"])
@pytest.mark.parametrize(
    "mutation",
    ["comment", "prefix", "suffix", "identity-prefix"],
)
def test_wrapper_matching_rejects_adversarial_command_text(
    tmp_path: Path,
    platform: str,
    mutation: str,
) -> None:
    source_plugin = PLUGIN.parent / "agent-index"
    plugin = tmp_path / "agent-index"
    shutil.copytree(source_plugin, plugin)
    hooks_path = plugin / "hooks.json"
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    entry = next(
        item
        for item in hooks["hooks"]["sessionStart"]
        if "command-catalog" in str(item.get(platform, ""))
    )
    original = entry[platform]
    if mutation == "comment":
        entry[platform] = original + " # accepted-looking comment"
    elif mutation == "prefix":
        entry[platform] = (
            ("printf 'prefix'; " if platform == "bash" else "Write-Output 'prefix'; ")
            + original
        )
    elif mutation == "suffix":
        entry[platform] = original + (
            "; printf 'suffix'" if platform == "bash" else "; Write-Output 'suffix'"
        )
    else:
        entry[platform] = original.replace(
            "agent-index@copilot-extensions",
            "x-agent-index@copilot-extensions",
        )
    hooks_path.write_text(json.dumps(hooks), encoding="utf-8")

    report = CONFORMANCE.scan_plugins(
        [
            CONFORMANCE.PluginTarget(
                "agent-index@copilot-extensions",
                plugin,
            )
        ],
        authority_source="context-injection@copilot-extensions",
        wrapper_root=PLUGIN,
    )

    assert "contributor-hook-drift" in {
        item.code for item in report.violations
    }


def test_canonical_hooks_do_not_fall_back_to_process_cwd() -> None:
    contributor = {
        "id": "main",
        "bash": ["scripts/emit.sh"],
        "powershell": ["scripts/emit.ps1"],
    }

    bash = CONFORMANCE.canonical_bash_hook(
        "example@marketplace",
        contributor,
    )
    powershell = CONFORMANCE.canonical_powershell_hook(
        "example@marketplace",
        contributor,
    )

    assert "$PWD" not in bash
    assert "Get-Location" not in powershell


@pytest.mark.parametrize("platform", ["bash", "powershell"])
def test_absent_plugin_root_cannot_execute_repository_local_wrapper(
    tmp_path: Path,
    platform: str,
) -> None:
    if platform == "bash" and (os.name == "nt" or not shutil.which("bash")):
        pytest.skip("Bash hook execution requires POSIX")
    if platform == "powershell" and not (
        shutil.which("pwsh") or shutil.which("powershell.exe")
    ):
        pytest.skip("PowerShell is unavailable")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "invoke-context-contributor.sh").write_text(
        "#!/usr/bin/env bash\n"
        "printf '{\"additionalContext\":\"UNSAFE\"}'\n",
        encoding="utf-8",
    )
    (scripts / "invoke-context-contributor.ps1").write_text(
        "[Console]::Out.Write('{\"additionalContext\":\"UNSAFE\"}')\n",
        encoding="utf-8",
    )
    contributor = {
        "id": "main",
        "bash": ["scripts/emit.sh"],
        "powershell": ["scripts/emit.ps1"],
    }
    hook = CONFORMANCE.canonical_hook(
        platform,
        "example@marketplace",
        contributor,
    )
    environment = os.environ.copy()
    for key in (
        "COPILOT_PLUGIN_ROOT",
        "PLUGIN_ROOT",
        "CLAUDE_PLUGIN_ROOT",
    ):
        environment.pop(key, None)
    command = (
        ["bash", "-c", hook]
        if platform == "bash"
        else [
            shutil.which("pwsh") or shutil.which("powershell.exe"),
            "-NoProfile",
            "-Command",
            hook,
        ]
    )

    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        input=json.dumps({"cwd": str(tmp_path)}),
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(result.stdout) == {}


def test_runtime_catalog_rejects_unrelated_contributor_commands(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "agent-index"
    shutil.copytree(PLUGIN.parent / "agent-index", plugin)
    declaration_path = plugin / "session-context.json"
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    contributor = next(
        item
        for item in declaration["contributors"]
        if item["id"] == "command-catalog"
    )
    contributor["bash"] = ["scripts/emit-scope-binding.sh"]
    contributor["powershell"] = ["scripts/emit-scope-binding.ps1"]
    declaration_path.write_text(json.dumps(declaration), encoding="utf-8")
    hooks_path = plugin / "hooks.json"
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    hook = next(
        item
        for item in hooks["hooks"]["sessionStart"]
        if "command-catalog" in str(item.get("bash", ""))
    )
    hook["bash"] = CONFORMANCE.canonical_bash_hook(
        "agent-index@copilot-extensions",
        contributor,
    )
    hook["powershell"] = CONFORMANCE.canonical_powershell_hook(
        "agent-index@copilot-extensions",
        contributor,
    )
    hooks_path.write_text(json.dumps(hooks), encoding="utf-8")

    report = CONFORMANCE.scan_plugins(
        [
            CONFORMANCE.PluginTarget(
                "agent-index@copilot-extensions",
                plugin,
            )
        ],
        authority_source="context-injection@copilot-extensions",
        wrapper_root=PLUGIN,
    )

    assert "runtime-command-catalog-command-drift" in {
        item.code for item in report.violations
    }


def test_runtime_catalog_identity_must_match_scanned_plugin(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "agent-index"
    shutil.copytree(PLUGIN.parent / "agent-index", plugin)
    invocation_path = plugin / "payload-invocation.json"
    invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
    invocation["plugin"] = "agent-other"
    invocation_path.write_text(json.dumps(invocation), encoding="utf-8")

    report = CONFORMANCE.scan_plugins(
        [
            CONFORMANCE.PluginTarget(
                "agent-index@copilot-extensions",
                plugin,
            )
        ],
        authority_source="context-injection@copilot-extensions",
        wrapper_root=PLUGIN,
    )

    assert "runtime-command-catalog-identity-drift" in {
        item.code for item in report.violations
    }


def test_runtime_catalog_accepts_installation_context_v2(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "payload-invocation.json"
    _write_json(
        manifest,
        {
            "schema": "copilot-extensions.payload-invocation",
            "version": 2,
            "command": "agent-example",
            "module": "agent_example",
            "purpose": "Operate the example runtime",
            "legacyRuntimeRoot": ".agent-example",
            "installationContext": "required",
            "noSelfProvisionEnv": "AGENT_EXAMPLE_NO_SELFPROVISION",
        },
    )

    contract, error = CONFORMANCE._runtime_catalog_contract(manifest)

    assert error is None
    assert contract is not None
    assert contract["plugin"] == "agent-example"


def test_runtime_catalog_rejects_mixed_installation_context_versions(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "payload-invocation.json"
    _write_json(
        manifest,
        {
            "schema": "copilot-extensions.payload-invocation",
            "version": 2,
            "command": "agent-example",
            "purpose": "Operate the example runtime",
            "runtimeRoot": ".agent-example",
            "legacyRuntimeRoot": ".agent-example",
            "installationContext": "required",
        },
    )

    contract, error = CONFORMANCE._runtime_catalog_contract(manifest)

    assert contract is None
    assert error == "payload invocation version 2 runtime contract is invalid"


@pytest.mark.parametrize("version", [True, False])
def test_runtime_catalog_rejects_boolean_versions(
    tmp_path: Path,
    version: bool,
) -> None:
    manifest = tmp_path / "payload-invocation.json"
    _write_json(
        manifest,
        {
            "schema": "copilot-extensions.payload-invocation",
            "version": version,
            "command": "agent-example",
            "purpose": "Operate the example runtime",
            "runtimeRoot": ".agent-example",
        },
    )

    contract, error = CONFORMANCE._runtime_catalog_contract(manifest)

    assert contract is None
    assert error == "payload invocation schema or version is incompatible"


def test_runtime_catalog_rejects_incomplete_generated_contract(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "agent-logger"
    shutil.copytree(PLUGIN.parent / "agent-logger", plugin)
    for suffix in ("sh", "ps1"):
        emitter = plugin / "scripts" / f"emit-command-catalog.{suffix}"
        lines = emitter.read_text(encoding="utf-8").splitlines()
        if suffix == "sh":
            index = next(
                offset
                for offset, line in enumerate(lines)
                if line.startswith("command_specs='")
            )
            specs = json.loads(lines[index].removeprefix("command_specs='")[:-1])
            lines[index] = (
                "command_specs='"
                + json.dumps(specs[:-1], separators=(",", ":"))
                + "'"
            )
        else:
            index = next(
                offset
                for offset, line in enumerate(lines)
                if line.startswith("$specs = '")
            )
            raw = lines[index].removeprefix("$specs = '").removesuffix(
                "' | ConvertFrom-Json"
            )
            specs = json.loads(raw)
            lines[index] = (
                "$specs = '"
                + json.dumps(specs[:-1], separators=(",", ":"))
                + "' | ConvertFrom-Json"
            )
        emitter.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = CONFORMANCE.scan_plugins(
        [
            CONFORMANCE.PluginTarget(
                "agent-logger@copilot-extensions",
                plugin,
            )
        ],
        authority_source="context-injection@copilot-extensions",
        wrapper_root=PLUGIN,
    )

    incomplete = [
        item
        for item in report.violations
        if item.code == "runtime-command-catalog-incomplete"
    ]
    assert {Path(item.path).suffix for item in incomplete} == {".sh", ".ps1"}


def test_marketplace_validation_cli_emits_json_and_nonzero(
    tmp_path: Path,
) -> None:
    marketplace = tmp_path / "marketplace"
    shutil.copytree(PLUGIN, marketplace / "plugins" / "context-injection")
    broken = marketplace / "plugins" / "plain-plugin"
    broken.mkdir(parents=True)
    _write_json(
        broken / "plugin.json",
        {"name": "plain-plugin", "hooks": "hooks.json"},
    )
    _write_json(
        broken / "hooks.json",
        {"version": 1, "hooks": {"sessionStart": [{"bash": "true"}]}},
    )
    _write_json(
        marketplace / ".github" / "plugin" / "marketplace.json",
        {
            "name": "example-marketplace",
            "plugins": [
                {
                    "name": "context-injection",
                    "source": "plugins/context-injection",
                },
                {"name": "plain-plugin", "source": "plugins/plain-plugin"},
                {"name": "absent-plugin", "source": "plugins/absent-plugin"},
            ],
        },
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate",
            "--marketplace-root",
            str(marketplace),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["ok"] is False
    assert payload["pluginCount"] == 3
    codes = [item["code"] for item in payload["violations"]]
    assert "plugin-payload-missing" in codes
    assert "context-declaration-missing" in codes


def test_effective_repository_scan_reports_every_missing_payload(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (home / ".copilot").mkdir(parents=True)
    _write_json(
        home / ".copilot" / "config.json",
        {"trustedFolders": [str(repo)]},
    )
    _write_json(
        repo / ".github" / "copilot" / "settings.json",
        {
            "enabledPlugins": {
                "missing-one@copilot-extensions": True,
                "missing-two@copilot-extensions": True,
                "context-injection@copilot-extensions": True,
            }
        },
    )
    adoption = repo / ".context-injection" / "config.yaml"
    adoption.parent.mkdir(parents=True)
    adoption.write_text(
        "schema: copilot-extensions.context-injection\n"
        "version: 1\n"
        "authority: context-injection@copilot-extensions\n"
        "engine:\n"
        "  schema: copilot-extensions.context-injection-engine\n"
        "  version: 5\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
    }

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate",
            "--repository",
            str(repo),
            "--json",
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    payload = json.loads(result.stdout)
    missing = {
        item["source"]
        for item in payload["violations"]
        if item["code"] == "plugin-payload-missing"
    }
    assert result.returncode == 1
    assert missing == {
        "context-injection@copilot-extensions",
        "missing-one@copilot-extensions",
        "missing-two@copilot-extensions",
    }
