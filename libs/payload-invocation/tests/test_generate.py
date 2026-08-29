"""Tests for canonical payload-local command generation."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "generate.py"
REPO = SCRIPT.parents[2]
_spec = importlib.util.spec_from_file_location("payload_invocation_generate", SCRIPT)
generator = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(generator)


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


def _extract_catalog(stdout: str) -> dict:
    outer = json.loads(stdout)
    match = re.search(r"```json\n(.*?)\n```", outer["additionalContext"], re.S)
    assert match
    return json.loads(match.group(1))


def _write_capture_module(root: Path, module: str) -> None:
    package = root
    for part in module.split("."):
        package /= part
        package.mkdir(exist_ok=True)
        (package / "__init__.py").touch()
    (package / "__main__.py").write_text(
        "import json\n"
        "import sys\n"
        "print(json.dumps("
        "{'args': sys.argv[1:], 'stdin': sys.stdin.read()}, "
        "ensure_ascii=False))\n",
        encoding="utf-8",
    )


def _run_catalog_launch(
    tmp_path: Path,
    command: list[str],
    env: dict[str, str],
    payload: str,
    launch_number: int,
) -> list[str]:
    helper = tmp_path / "catalog-launch.py"
    helper.write_text(
        "import json\n"
        "import os\n"
        "import subprocess\n"
        "command = json.loads(os.environ['CATALOG_COMMAND'])\n"
        "payload = os.environ['CATALOG_PAYLOAD']\n"
        "outputs = []\n"
        "for _ in range(2):\n"
        "    result = subprocess.run(\n"
        "        command,\n"
        "        input=payload,\n"
        "        env=os.environ,\n"
        "        capture_output=True,\n"
        "        text=True,\n"
        "        check=True,\n"
        "    )\n"
        "    outputs.append(result.stdout)\n"
        "print(json.dumps(outputs))\n",
        encoding="utf-8",
    )
    launch_env = {
        **env,
        "CATALOG_COMMAND": json.dumps(command),
        "CATALOG_PAYLOAD": payload,
        "CATALOG_LAUNCH_NUMBER": str(launch_number),
    }
    result = subprocess.run(
        [sys.executable, str(helper)],
        env=launch_env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _powershell_version(pwsh: str) -> tuple[int, int]:
    version = subprocess.run(
        [pwsh, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    major, minor, *_rest = map(int, version.split("."))
    return major, minor


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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
    assert "$_payloadDispatcher = Join-Path $_payloadRoot 'scripts\\runtime-gate.ps1'" in powershell
    assert "$env:AGENT_EXAMPLE_PAYLOAD_ROOT = $_payloadRoot" in powershell
    assert "& $_payloadDispatcher @args" in powershell
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
    assert r'"relativePath":"bin\\agent-example.cmd"' in catalog
    assert "$catalogShim = 'cmd'" in catalog
    assert r'where.exe" pwsh 2^>nul' in cmd
    assert 'set "_PSHOST=%%I"' in cmd
    assert r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" in cmd
    assert '"%_PSHOST%" -NoProfile' in cmd


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
    if _powershell_version(pwsh) < (7, 3):
        pytest.skip("PowerShell 7.3+ is required for lossless native argv")
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
    catalog = _extract_catalog(result.stdout)
    assert catalog["plugin"] == "agent-example"
    assert [command["id"] for command in catalog["commands"]] == [
        "agent-example",
        "example-helper",
    ]
    assert all(command["argv"][-1].endswith(".ps1") for command in catalog["commands"])
    assert all(Path(command["argv"][0]).is_absolute() for command in catalog["commands"])
    assert all(command["argv"][1:5] == [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
    ] for command in catalog["commands"])
    assert all(command["availability"] == "ready" for command in catalog["commands"])


def test_catalog_deduplicates_per_launch_and_reemits_on_resume(
    tmp_path: Path,
) -> None:
    manifest = _multi_manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    (manifest.parent / "plugin.json").write_text(
        '{"name":"agent-example"}\n', encoding="utf-8"
    )
    env = os.environ.copy()
    env.update(
        {
            "COPILOT_PLUGIN_ROOT": str(manifest.parent),
            "HOME": str(tmp_path / "home"),
            "USERPROFILE": str(tmp_path / "home"),
            "TMP": str(tmp_path / "temp"),
            "TEMP": str(tmp_path / "temp"),
            "TMPDIR": str(tmp_path / "temp"),
        }
    )
    (tmp_path / "temp").mkdir()
    payload = '{"sessionId":"session with spaces \u96ea"}'
    if os.name == "nt":
        pwsh = shutil.which("pwsh")
        if not pwsh:
            pytest.skip("pwsh is not installed")
        command = [
            pwsh,
            "-NoProfile",
            "-File",
            str(manifest.parent / "scripts" / "emit-command-catalog.ps1"),
        ]
    else:
        command = [
            str(manifest.parent / "scripts" / "emit-command-catalog.sh")
        ]

    first_launch = _run_catalog_launch(tmp_path, command, env, payload, 1)
    resumed_launch = _run_catalog_launch(tmp_path, command, env, payload, 2)

    for launch in (first_launch, resumed_launch):
        assert _extract_catalog(launch[0])["plugin"] == "agent-example"
        assert json.loads(launch[1]) == {}


@pytest.mark.parametrize(
    "manifest",
    sorted((REPO / "plugins").glob("agent-*/payload-invocation.json")),
    ids=lambda path: path.parent.name,
)
def test_every_advertised_entrypoint_round_trips_exact_arguments(
    manifest: Path,
    tmp_path: Path,
) -> None:
    source = generator.load_manifest(manifest)
    plugin = tmp_path / "payload space \u96ea" / manifest.parent.name
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    staged_manifest = plugin / "payload-invocation.json"
    staged_manifest.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    (plugin / "plugin.json").write_text(
        json.dumps({"name": source["plugin"]}), encoding="utf-8"
    )
    installer = str(source["installer"])
    (scripts / f"{installer}.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (scripts / f"{installer}.ps1").write_text(
        "# generated fixture\n", encoding="utf-8"
    )
    python_path = str(Path(sys.executable).resolve())
    (scripts / "resolve-runtime.sh").write_text(
        f"AGENT_RT_PY={json.dumps(python_path)}\n", encoding="utf-8"
    )
    escaped_python = python_path.replace("'", "''")
    (scripts / "resolve-runtime.ps1").write_text(
        f"$AgentRtPy = '{escaped_python}'\n", encoding="utf-8"
    )
    generator.process_manifest(staged_manifest, check=False)

    modules = tmp_path / "modules"
    modules.mkdir(exist_ok=True)
    for command_spec in source["commands"]:
        _write_capture_module(modules, str(command_spec["module"]))

    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "COPILOT_PROJECT_DIR": str(tmp_path),
            "HOME": str(home),
            "USERPROFILE": str(home),
            "PYTHONPATH": str(modules),
            "PYTHONUTF8": "1",
        }
    )
    if os.name == "nt":
        pwsh = shutil.which("pwsh")
        if not pwsh:
            pytest.skip("pwsh is not installed")
        lossless_powershell = _powershell_version(pwsh) >= (7, 3)
        catalog_command = [
            pwsh,
            "-NoProfile",
            "-File",
            str(scripts / "emit-command-catalog.ps1"),
        ]
    else:
        catalog_command = [str(scripts / "emit-command-catalog.sh")]
    emitted = subprocess.run(
        catalog_command,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    catalog = _extract_catalog(emitted.stdout)
    expected_ids = {
        str(command_spec["command"]) for command_spec in source["commands"]
    }
    assert {command["id"] for command in catalog["commands"]} == expected_ids

    if (
        os.name == "nt"
        and source["windowsCatalogShim"] == "powershell"
        and not lossless_powershell
    ):
        for command in catalog["commands"]:
            assert command["availability"] == "unavailable"
            assert command["shell"] == "powershell"
            assert command["argv"] == [
                str(plugin / str(source["outputDir"]) / f"{command['id']}.ps1")
            ]
        return

    edge_args = [
        "two words",
        'embedded"quote',
        "embedded'quote",
        "unicode-\u96ea",
        "",
        "$&|;<>*?(){}[]!^%",
    ]
    stdin = "stdin with spaces, quotes, and unicode \u96ea"
    for command in catalog["commands"]:
        prefix = command["argv"]
        assert command["availability"] == "ready"
        entrypoint = Path(prefix[-1])
        assert entrypoint.is_absolute()
        assert entrypoint.resolve().is_relative_to(plugin.resolve())
        if os.name == "nt":
            assert Path(prefix[0]).is_absolute()
            assert prefix[1:5] == [
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
            ]
            invoke = [*prefix, *edge_args]
        else:
            invoke = [*prefix, *edge_args]
        result = subprocess.run(
            invoke,
            input=stdin,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        captured = json.loads(result.stdout)
        assert captured["args"] == edge_args
        assert captured["stdin"] == stdin
        if os.name == "nt":
            rendered = "& " + " ".join(
                _powershell_literal(value) for value in [*prefix, *edge_args]
            )
            shell_result = subprocess.run(
                [pwsh, "-NoProfile", "-Command", rendered],
                input=stdin,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            shell_captured = json.loads(shell_result.stdout)
            assert shell_captured["args"] == edge_args
            assert shell_captured["stdin"] == stdin


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell compatibility test")
def test_windows_powershell_51_catalog_refuses_lossy_direct_invocation(
    tmp_path: Path,
) -> None:
    legacy_host = Path(
        os.environ.get("SystemRoot", r"C:\Windows")
    ) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not legacy_host.is_file():
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    (manifest.parent / "plugin.json").write_text(
        '{"name":"agent-example"}\n', encoding="utf-8"
    )
    env = {
        **os.environ,
        "COPILOT_PLUGIN_ROOT": str(manifest.parent),
    }
    result = subprocess.run(
        [
            str(legacy_host),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(manifest.parent / "scripts" / "emit-command-catalog.ps1"),
        ],
        input="",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    command = _extract_catalog(result.stdout)["commands"][0]
    assert command["availability"] == "unavailable"
    assert command["shell"] == "powershell"
    assert command["argv"] == [
        str(manifest.parent / "bin" / "agent-example.ps1")
    ]


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_powershell_pre_73_catalog_is_unavailable(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    (manifest.parent / "plugin.json").write_text(
        '{"name":"agent-example"}\n', encoding="utf-8"
    )
    emitter = manifest.parent / "scripts" / "emit-command-catalog.ps1"
    source = emitter.read_text(encoding="utf-8")
    assert "$PSVersionTable.PSVersion -ge [Version]'7.3'" in source
    compatibility_emitter = emitter.with_name("emit-command-catalog-pre73.ps1")
    compatibility_emitter.write_text(
        source.replace(
            "$PSVersionTable.PSVersion -ge [Version]'7.3'",
            "[Version]'7.2.99' -ge [Version]'7.3'",
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "COPILOT_PLUGIN_ROOT": str(manifest.parent),
    }
    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(compatibility_emitter),
        ],
        input="",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    command = _extract_catalog(result.stdout)["commands"][0]
    assert command["availability"] == "unavailable"
    assert command["shell"] == "powershell"
    assert command["argv"] == [
        str(manifest.parent / "bin" / "agent-example.ps1")
    ]


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
    assert '"relativePath":"bin/payload/agent-example"' in catalog


@pytest.mark.parametrize(
    "plugin",
    [
        "agent-bridge",
        "agent-codespaces",
        "agent-containers",
        "agent-dispatch",
        "agent-index",
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
        catalog_hooks = [
            hook for hook in session_hooks if "emit-command-catalog" in hook[shell]
        ]
        assert len(catalog_hooks) == 1
        assert "COPILOT_PLUGIN_ROOT" in catalog_hooks[0][shell]


def test_skill_catalog_references_name_payload_adopters() -> None:
    reference = re.compile(
        r'<(agent-[a-z0-9-]+) catalog(?: "([a-z][a-z0-9-]*)")? argv prefix>'
    )
    references: dict[str, dict[str, list[Path]]] = {}
    capability_paths = sorted({
        *(REPO / "plugins").glob("*/skills/**/*.md"),
        *(REPO / "plugins").glob("*/agents/**/*.md"),
        *(REPO / "plugins").glob("*/extensions/**/*.mjs"),
        *(REPO / "plugins").glob("*/scripts/*.sh"),
        *(REPO / "plugins").glob("*/scripts/*.ps1"),
    })
    stale_references = []
    invalid_powershell_renderings = []
    incomplete_powershell_contracts = []
    invalid_powershell_sites = []
    for skill in capability_paths:
        text = skill.read_text(encoding="utf-8")
        preamble = text[:3000]
        normalized_preamble = " ".join(preamble.replace(">", " ").split())
        if re.search(r"catalog[^\n`]*argv\[0\]|<[^>]*argv\[0\]>", text):
            stale_references.append(skill.relative_to(REPO))
        if re.search(
            r"`<agent-[^`]+ catalog(?: \"[^\"]+\")? argv prefix> <args>`",
            text,
        ):
            invalid_powershell_renderings.append(skill.relative_to(REPO))
        if (
            "catalog argv prefix" in preamble
            and "PowerShell" in preamble
            and (
                "quote each prefix element separately and prepend `&` in PowerShell"
                not in normalized_preamble
            )
        ):
            incomplete_powershell_contracts.append(skill.relative_to(REPO))
        in_powershell_fence = False
        for line_number, line in enumerate(text.splitlines(), start=1):
            if re.match(r"^\s*```powershell(?:\s|$)", line, re.IGNORECASE):
                in_powershell_fence = True
                continue
            if in_powershell_fence and re.match(r"^\s*```", line):
                in_powershell_fence = False
                continue
            has_prefix = bool(reference.search(line))
            missing_call_operator = not re.search(r"&\s*<agent-", line)
            if (
                has_prefix
                and missing_call_operator
                and (
                    in_powershell_fence
                    or re.search(r"powershell\(command:\s*['\"]", line)
                )
            ):
                invalid_powershell_sites.append(
                    (skill.relative_to(REPO), line_number)
                )
        for plugin, command in reference.findall(text):
            command_id = command or plugin
            references.setdefault(plugin, {}).setdefault(command_id, []).append(
                skill.relative_to(REPO)
            )

    assert stale_references == []
    assert invalid_powershell_renderings == []
    assert incomplete_powershell_contracts == []
    assert invalid_powershell_sites == []
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
        if not any(
            "emit-command-catalog" in hook.get("bash", "")
            and "emit-command-catalog" in hook.get("powershell", "")
            for hook in session_hooks
        ):
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_posix_shim_rejects_conflicting_payload_context(tmp_path: Path) -> None:
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
    assert result.returncode == 126
    assert "payload context mismatch" in result.stderr


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


def test_windows_templates_preserve_context_and_release_payload_cwd(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    generated = generator.expected_files(manifest)
    powershell = next(
        content for path, content in generated.items() if path.suffix == ".ps1"
    )
    cmd = next(
        content for path, content in generated.items() if path.suffix == ".cmd"
    )
    assert "[IO.Directory]::SetCurrentDirectory($_outside)" in powershell
    assert "StartsWith($_payloadPrefix" in powershell
    assert "[IO.FileShare]::None" in powershell
    assert "if not defined COPILOT_PLUGIN_ROOT" in cmd


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
