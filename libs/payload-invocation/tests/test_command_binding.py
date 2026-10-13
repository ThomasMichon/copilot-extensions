"""Source-qualified catalogs must select only lifecycle-owned installations."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
PLUGIN_ID = "agent-worktrees"
POWERSHELL = shutil.which(
    os.environ.get("INSTALLATION_CONTEXT_POWERSHELL", "pwsh")
) or shutil.which("powershell")


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _snapshot(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): (
            path.is_dir(), path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in [root, *root.rglob("*")]
    }


@pytest.fixture
def binding_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    home = tmp_path / "home"
    payload = home / ".copilot" / "installed-plugins" / "example" / PLUGIN_ID
    payload.mkdir(parents=True)
    legacy = home / ".agent-worktrees"
    legacy.mkdir()
    _write(home / ".copilot" / "settings.json", {
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}}
        },
    })
    _write(legacy / "retained-state.json", {"retained": True})
    environment = {"HOME": str(home), "USERPROFILE": str(home)}
    primitive = _load(
        ROOT / "libs/installation-context/installation_context.py", "binding_primitive"
    )
    original = primitive._current_environment

    def current_environment(**arguments: Any) -> Any:
        return original(**{**arguments, "os_profile": home})

    monkeypatch.setattr(primitive, "_current_environment", current_environment)
    binding = _load(LIB / "command_binding.py", "binding_reference")
    return {
        "home": home, "payload": payload, "legacy": legacy,
        "environment": environment, "primitive": primitive, "binding": binding,
        "arguments": {
            "payload_root": payload, "plugin_id": PLUGIN_ID,
            "legacy_root": legacy, "environment": environment,
        },
    }


def _publish(fixture: dict) -> dict:
    receipt = fixture["binding"].publish_legacy_binding(
        fixture["primitive"], **fixture["arguments"]
    )
    _write(fixture["legacy"] / "deploy-manifest.json", {
        "schema_version": 3, "service": PLUGIN_ID, "commandBinding": receipt,
    })
    return receipt


def _resolve(fixture: dict) -> dict:
    before = _snapshot(fixture["home"])
    result = fixture["binding"].resolve_command_binding(
        fixture["primitive"], **fixture["arguments"]
    )
    assert _snapshot(fixture["home"]) == before
    return result


def test_command_binding_unowned_legacy_is_explicitly_unresolved(binding_fixture: dict) -> None:
    result = _resolve(binding_fixture)
    assert result["status"] == "unresolved"
    assert result["reason"] == "legacy-ownership-missing"
    assert result["source"]["identity"]["canonical"] == "github:example/plugins"
    assert result["installation"] is None
    _write(binding_fixture["legacy"] / "deploy-manifest.json", {
        "schema_version": 3, "service": PLUGIN_ID,
        "source": {"path": str(binding_fixture["payload"]), "repo": "Example/Plugins"},
    })
    assert _resolve(binding_fixture)["reason"] == "legacy-ownership-missing"


def test_command_binding_owned_legacy_and_retained_pointer(binding_fixture: dict) -> None:
    receipt = _publish(binding_fixture)
    pointer = binding_fixture["home"] / ".copilot-extensions/agent-worktrees/runtime-root"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("retained-foreign-pointer\n", encoding="utf-8")
    result = _resolve(binding_fixture)
    assert result["status"] == "selected"
    assert result["installation"]["mode"] == "legacy"
    assert result["installation"]["runtimeRoot"] == str(binding_fixture["legacy"])
    assert result["installation"]["installationId"] == receipt["installationId"]
    assert not (binding_fixture["home"] / ".copilot-extensions/marketplaces").exists()


@pytest.mark.parametrize("field,value", [
    ("version", True),
    ("pluginId", "agent-index"),
    ("sourceFingerprint", "sha256:" + "0" * 64),
    ("payloadRoot", "/foreign/payload"),
    ("runtimeRoot", "/foreign/runtime"),
    ("installationId", "sha256:" + "0" * 64),
])
def test_command_binding_foreign_receipt_refuses(
    binding_fixture: dict, field: str, value: Any,
) -> None:
    receipt = _publish(binding_fixture)
    receipt[field] = value
    _write(binding_fixture["legacy"] / "deploy-manifest.json", {
        "schema_version": 3, "service": PLUGIN_ID, "commandBinding": receipt,
    })
    result = _resolve(binding_fixture)
    assert result["status"] == "blocked"
    assert result["reason"] == "legacy-ownership-mismatch"
    assert result["installation"] is None


def test_command_binding_publisher_refuses_other_marketplace(binding_fixture: dict) -> None:
    _publish(binding_fixture)
    other = binding_fixture["home"] / ".copilot/installed-plugins/other" / PLUGIN_ID
    other.mkdir(parents=True)
    _write(binding_fixture["home"] / ".copilot/settings.json", {
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
            "other": {"source": {"source": "github", "repo": "Other/Plugins"}},
        },
    })
    arguments = {**binding_fixture["arguments"], "payload_root": other}
    before = _snapshot(binding_fixture["home"])
    with pytest.raises(binding_fixture["binding"].CommandOwnershipConflict, match="transfer"):
        binding_fixture["binding"].publish_legacy_binding(
            binding_fixture["primitive"], **arguments,
        )
    result = binding_fixture["binding"].resolve_command_binding(
        binding_fixture["primitive"], **arguments,
    )
    assert result["status"] == "blocked"
    assert result["reason"] == "legacy-ownership-mismatch"
    assert _snapshot(binding_fixture["home"]) == before


def test_command_binding_payload_replacement_keeps_installation_identity(binding_fixture: dict) -> None:
    old = _publish(binding_fixture)
    replacement = binding_fixture["home"] / ".copilot/installed-plugins/alias" / PLUGIN_ID
    replacement.mkdir(parents=True)
    _write(binding_fixture["home"] / ".copilot/settings.json", {
        "extraKnownMarketplaces": {
            "alias": {"source": {"source": "github", "repo": "Example/Plugins"}}
        },
    })
    arguments = {**binding_fixture["arguments"], "payload_root": replacement}
    assert binding_fixture["binding"].resolve_command_binding(
        binding_fixture["primitive"], **arguments,
    )["status"] == "blocked"
    refreshed = binding_fixture["binding"].publish_legacy_binding(
        binding_fixture["primitive"], **arguments,
    )
    assert refreshed["installationId"] == old["installationId"]
    assert refreshed["payloadRoot"] != old["payloadRoot"]
    _write(binding_fixture["legacy"] / "deploy-manifest.json", {
        "schema_version": 3, "service": PLUGIN_ID, "commandBinding": refreshed,
    })
    binding_fixture["arguments"] = arguments
    assert _resolve(binding_fixture)["status"] == "selected"


@pytest.mark.parametrize("blocked", ["policy", "maintenance", "source", "context"])
def test_command_binding_blocked_governance_never_selects(
    binding_fixture: dict, blocked: str,
) -> None:
    _publish(binding_fixture)
    if blocked == "policy":
        _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
            "schema": "invalid",
        })
    elif blocked == "maintenance":
        (binding_fixture["home"] / ".copilot-extensions/maintenance").mkdir(parents=True)
    elif blocked == "source":
        _write(binding_fixture["home"] / ".copilot/settings.json", {
            "extraKnownMarketplaces": {
                "example": {"source": {"source": "unsupported", "repo": "Example/Plugins"}}
            },
        })
    else:
        binding_fixture["environment"]["COPILOT_EXTENSIONS_CONTEXT"] = str(
            binding_fixture["home"] / "missing-install.json"
        )
    result = _resolve(binding_fixture)
    assert result["status"] in {"blocked", "unresolved"}
    assert result["installation"] is None


def _activate(fixture: dict, payload: Path) -> dict:
    primitive = fixture["primitive"]
    environment = fixture["environment"]
    stamped = primitive.stamp_context(
        payload_version="1.0.0", payload_origin="installed",
        expected_namespace_generation=0, expected_install_generation=0,
        payload_root=payload, plugin_id=PLUGIN_ID, environment=environment,
    )
    activated = primitive.compare_and_swap_activation(
        context=stamped["installReceipt"],
        expected_marketplace_id=stamped["marketplaceId"],
        expected_plugin_id=PLUGIN_ID,
        expected_namespace_generation=stamped["namespaceGeneration"],
        expected_install_generation=stamped["generation"],
        expected_activation_generation=0,
        activation_mode="namespaced", activation_state="active",
        legacy_disposition="absent",
        legacy_probe={"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
        legacy_root=fixture["legacy"], environment=environment,
    )
    return {**stamped, **activated}


def test_command_binding_two_cells_are_owned_and_generation_pinned(binding_fixture: dict) -> None:
    other = binding_fixture["home"] / ".copilot/installed-plugins/other" / PLUGIN_ID
    other.mkdir(parents=True)
    _write(binding_fixture["home"] / ".copilot/settings.json", {
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
            "other": {"source": {"source": "github", "repo": "Other/Plugins"}},
        },
    })
    first = _activate(binding_fixture, binding_fixture["payload"])
    second = _activate(binding_fixture, other)
    _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
        "schema": "copilot-extensions.installation-mode",
        "version": 1, "installationMode": {"enabled": True, "marketplaces": {}},
    })
    selected = []
    for payload, cell in ((binding_fixture["payload"], first), (other, second)):
        binding_fixture["arguments"]["payload_root"] = payload
        result = _resolve(binding_fixture)
        assert result["status"] == "selected", result
        installation = result["installation"]
        assert installation["mode"] == "namespaced"
        assert installation["installReceipt"] == cell["installReceipt"]
        assert installation["installGeneration"] == cell["installGeneration"]
        assert installation["activationGeneration"] == cell["activationGeneration"]
        selected.append(installation)
    assert selected[0]["installationId"] != selected[1]["installationId"]
    assert selected[0]["runtimeRoot"] != selected[1]["runtimeRoot"]


def _prepare_catalog(fixture: dict) -> None:
    payload = fixture["payload"]
    scripts = payload / "scripts"
    scripts.mkdir()
    for name in ("install", "resolve-runtime", "invoke-payload-runtime"):
        for suffix in ("sh", "ps1"):
            (scripts / f"{name}.{suffix}").write_text("# fixture\n", encoding="utf-8")
    context = scripts / "installation-context"
    context.mkdir()
    for path in (ROOT / "libs/installation-context").glob("*.py"):
        shutil.copyfile(path, context / path.name)
    data = json.loads((ROOT / "plugins/agent-worktrees/payload-invocation.json").read_text())
    _write(payload / "payload-invocation.json", data)
    generator = _load(LIB / "generate.py", "binding_generator")
    generator.process_manifest(payload / "payload-invocation.json", check=False)


def _catalog_environment(fixture: dict) -> dict[str, str]:
    environment = {
        **os.environ, **fixture["environment"],
        "COPILOT_PLUGIN_ROOT": str(fixture["payload"]),
        "DOTNET_MultiCoreJitNoProfileGather": "1",
        "COMPlus_MultiCoreJitNoProfileGather": "1",
        "POWERSHELL_TELEMETRY_OPTOUT": "1",
        "POWERSHELL_UPDATECHECK": "Off",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for relative in (".cache/powershell", ".local/share/powershell/Modules"):
        (fixture["home"] / relative).mkdir(parents=True)
    if os.name == "nt":
        (fixture["home"] / "AppData/Local/Microsoft/PowerShell").mkdir(parents=True)
        return environment
    wrapper_dir = fixture["home"] / "test-bin"
    wrapper_dir.mkdir()
    bootstrap = (
        f"#!{sys.executable}\n"
        "import os,pwd,runpy,sys\n"
        "sys.dont_write_bytecode=True\n"
        "original=pwd.getpwuid\n"
        "def getpwuid(uid):\n"
        "    record=original(uid)\n"
        "    return pwd.struct_passwd((*record[:5],os.environ['HOME'],record.pw_shell))\n"
        "pwd.getpwuid=getpwuid\n"
        "args=sys.argv[1:]\n"
        "if args and args[0]=='-c':\n"
        "    code=args[1]\n"
        "    sys.argv=['-c',*args[2:]]\n"
        "    exec(compile(code,'<catalog-command>','exec'),{'__name__':'__main__'})\n"
        "    raise SystemExit(0)\n"
        "while args and args[0].startswith('-'):\n"
        "    option=args.pop(0)\n"
        "    if option=='-X': args.pop(0)\n"
        "if args:\n"
        "    sys.argv=args\n"
        "    runpy.run_path(args[0],run_name='__main__')\n"
        "else:\n"
        "    exec(compile(sys.stdin.read(),'<catalog-stdin>','exec'),{'__name__':'__main__'})\n"
    )
    for name in ("python3", "python"):
        wrapper = wrapper_dir / name
        wrapper.write_text(bootstrap, encoding="utf-8")
        wrapper.chmod(0o755)
    environment["PATH"] = str(wrapper_dir) + os.pathsep + os.environ["PATH"]
    return environment


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX emitter")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
@pytest.mark.parametrize("owned", [False, True])
def test_command_binding_real_catalog_read_only(
    binding_fixture: dict, shell: str, owned: bool,
) -> None:
    _prepare_catalog(binding_fixture)
    if owned:
        _publish(binding_fixture)
    environment = _catalog_environment(binding_fixture)
    payload = binding_fixture["payload"]
    argv = (
        ["bash", str(payload / "scripts/emit-command-catalog.sh")]
        if shell == "bash" else
        [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(payload / "scripts/emit-command-catalog.ps1")]
    )
    before = _snapshot(binding_fixture["home"])
    for _ in range(2):
        completed = subprocess.run(
            argv, env=environment, cwd=binding_fixture["home"],
            capture_output=True, text=True, timeout=30, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        envelope = json.loads(completed.stdout)
        match = re.search(r"```json\n(.*)\n```", envelope["additionalContext"])
        assert match, completed.stdout
        catalog = json.loads(match.group(1))
        assert catalog["version"] == 1
        suffix = ".ps1" if shell == "powershell" else ""
        assert catalog["commands"][0]["argv"] == [
            str(payload / "bin/payload" / (PLUGIN_ID + suffix))
        ]
        assert catalog["commands"][0]["availability"] == "ready"
        assert catalog["binding"]["status"] == ("selected" if owned else "unresolved")
    assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX installer")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
def test_command_binding_native_manifest_publication_and_foreign_refusal(
    binding_fixture: dict, shell: str,
) -> None:
    _prepare_catalog(binding_fixture)
    environment = _catalog_environment(binding_fixture)
    environment.update({
        "TEST_PAYLOAD": str(binding_fixture["payload"]),
        "TEST_RUNTIME": str(binding_fixture["legacy"]),
        "TEST_PYTHON": (
            sys.executable if os.name == "nt" else
            str(binding_fixture["home"] / "test-bin/python3")
        ),
        "COMPUTERNAME": "example-host",
    })
    if shell == "bash":
        source = (ROOT / "plugins/agent-worktrees/scripts/install.sh").read_text()
        function = "write_deploy_manifest() {" + source.split(
            "write_deploy_manifest() {", 1
        )[1].split("show_deploy_status() {", 1)[0]
        program = (
            "set -euo pipefail\n"
            "resolve_machine() { printf 'example-host'; }\n"
            "detect_platform() { printf 'linux'; }\n"
            "_source_kind() { printf 'marketplace'; }\n"
            "ok() { :; }\nwarn() { printf '%s\\n' \"$*\" >&2; }\n"
            "err() { printf '%s\\n' \"$*\" >&2; }\n"
            "PLUGIN_DIR=\"$TEST_PAYLOAD\"\nINSTALL_DIR=\"$TEST_RUNTIME\"\n"
            "VENV_PYTHON=\"$TEST_PYTHON\"\nLINK_DIR=\"$TEST_RUNTIME/.venv\"\n"
            + function + "\nwrite_deploy_manifest\n"
        )
        argv = ["bash", "-c", program]
    else:
        source = (ROOT / "plugins/agent-worktrees/scripts/install.ps1").read_text()
        function = "function Write-V3Manifest {" + source.split(
            "function Write-V3Manifest {", 1
        )[1].split("function Test-UvConfiguredIndex {", 1)[0]
        program = (
            "$ErrorActionPreference='Stop'\n"
            "function Get-SourceKind { param($PluginPath); 'marketplace' }\n"
            "function Write-ServiceOk {}\n"
            "function Write-ServiceWarn { param($Message); [Console]::Error.WriteLine($Message) }\n"
            "function Invoke-NativeCapture {\n"
            "    param($Action); $text=& $Action\n"
            "    @{ ExitCode=$LASTEXITCODE; Output=($text -join \"`n\") }\n"
            "}\n"
            "$PluginDir=$env:TEST_PAYLOAD\n$InstallDir=$env:TEST_RUNTIME\n"
            "$VenvPython=$env:TEST_PYTHON\n$LinkDir=Join-Path $InstallDir '.venv'\n"
            + function + "\nWrite-V3Manifest\n"
        )
        program_path = binding_fixture["home"] / "publish-fixture.ps1"
        program_path.write_text(program, encoding="utf-8")
        argv = [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(program_path)]
    completed = subprocess.run(
        argv, env=environment, cwd=binding_fixture["home"],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert _resolve(binding_fixture)["status"] == "selected"
    _write(binding_fixture["home"] / ".copilot/settings.json", {
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Other/Plugins"}}
        },
    })
    before = _snapshot(binding_fixture["home"])
    refused = subprocess.run(
        argv, env=environment, cwd=binding_fixture["home"],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert refused.returncode != 0
    assert "Command ownership" in refused.stderr
    after = _snapshot(binding_fixture["home"])
    assert after.keys() == before.keys()
    for path, metadata in before.items():
        if path != ".agent-worktrees":
            assert after[path] == metadata, path


def test_command_binding_concurrent_lifecycle_publishers_keep_one_owner(binding_fixture: dict) -> None:
    other = binding_fixture["home"] / ".copilot/installed-plugins/other" / PLUGIN_ID
    other.mkdir(parents=True)
    _write(binding_fixture["home"] / ".copilot/settings.json", {
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
            "other": {"source": {"source": "github", "repo": "Other/Plugins"}},
        },
    })
    for payload in (binding_fixture["payload"], other):
        _prepare_catalog({**binding_fixture, "payload": payload})
    bootstrap = """
import importlib.util,json,os,sys,time
from pathlib import Path
sys.dont_write_bytecode=True
if os.name!='nt':
    import pwd
    original=pwd.getpwuid
    def getpwuid(uid):
        record=original(uid)
        return pwd.struct_passwd((*record[:5],os.environ['HOME'],record.pw_shell))
    pwd.getpwuid=getpwuid
payload,root,gate=map(Path,sys.argv[1:])
spec=importlib.util.spec_from_file_location('concurrent_binding',payload/'scripts/command_binding.py')
binding=importlib.util.module_from_spec(spec)
spec.loader.exec_module(binding)
primitive=binding._load_primitive(payload)
write=primitive._atomic_write_json
def delayed_write(*args,**kwargs):
    if Path(args[0]).name=='deploy-manifest.json':
        time.sleep(0.3)
    return write(*args,**kwargs)
primitive._atomic_write_json=delayed_write
deadline=time.monotonic()+10
while not gate.exists():
    if time.monotonic()>deadline:
        raise RuntimeError('Fixture publication gate timed out')
    time.sleep(0.01)
result=binding.write_legacy_manifest(
    primitive,{'schema_version':3,'service':'agent-worktrees'},
    payload_root=payload,plugin_id='agent-worktrees',legacy_root=root)
print(json.dumps(result))
"""
    gate = binding_fixture["home"] / "publication-start"
    processes = []
    for payload in (binding_fixture["payload"], other):
        environment = {**os.environ, **binding_fixture["environment"], "COPILOT_PLUGIN_ROOT": str(payload)}
        processes.append(subprocess.Popen(
            [sys.executable, "-B", "-c", bootstrap, str(payload), str(binding_fixture["legacy"]), str(gate)],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ))
    gate.write_text("start", encoding="utf-8")
    outputs = [process.communicate(timeout=30) for process in processes]
    assert sorted(process.returncode for process in processes) == [0, 1], outputs
    assert any("CommandOwnershipConflict" in stderr for _, stderr in outputs)
    assert not (binding_fixture["legacy"] / ".command-binding-publication.lock").exists()
    selected = []
    for payload in (binding_fixture["payload"], other):
        binding_fixture["arguments"]["payload_root"] = payload
        selected.append(_resolve(binding_fixture)["status"])
    assert sorted(selected) == ["blocked", "selected"]
