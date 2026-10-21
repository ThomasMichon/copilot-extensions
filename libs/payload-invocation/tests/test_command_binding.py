"""Source-qualified catalogs must select only lifecycle-owned installations."""
from __future__ import annotations

import importlib.util
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
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


def _lifecycle_lock_path(root: Path) -> Path:
    root = root.resolve()
    return root.with_name(root.name + ".command-binding-publication.lock")


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
    environment = {
        "HOME": str(home), "USERPROFILE": str(home),
        # Keep parent and child identity equal for a named direct WSL fixture.
        "WSL_DISTRO_NAME": "fixture-wsl",
    }
    primitive = _load(
        ROOT / "libs/installation-context/installation_context.py", "binding_primitive"
    )
    if primitive._is_wsl_guest():
        environment["WSL_DISTRO_NAME"] = "Fixture Guest"
        primitive.register_guest_environment(
            "Fixture Guest", environment=environment, os_profile=home,
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


def _activate(fixture: dict, payload: Path, *, durable_home: Path | None = None) -> dict:
    primitive = fixture["primitive"]
    environment = fixture["environment"]
    stamped = primitive.stamp_context(
        payload_version="1.0.0", payload_origin="installed",
        expected_namespace_generation=0, expected_install_generation=0,
        payload_root=payload, plugin_id=PLUGIN_ID, environment=environment,
        durable_home=durable_home,
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
        durable_home=durable_home,
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
    for suffix in ("sh", "ps1"):
        shutil.copyfile(
            ROOT / f"plugins/agent-worktrees/scripts/source-runtime.{suffix}",
            scripts / f"source-runtime.{suffix}",
        )
    shutil.copyfile(
        ROOT / "plugins/agent-worktrees/scripts/versioned_runtime_native.ps1",
        scripts / "versioned_runtime_native.ps1",
    )
    context = scripts / "installation-context"
    context.mkdir()
    for path in (ROOT / "libs/installation-context").glob("*.py"):
        shutil.copyfile(path, context / path.name)
    data = json.loads((ROOT / "plugins/agent-worktrees/payload-invocation.json").read_text())
    _write(payload / "payload-invocation.json", data)
    generator = _load(LIB / "generate.py", "binding_generator")
    generator.process_manifest(payload / "payload-invocation.json", check=False)


def _catalog_environment(fixture: dict, interpreter: str | None = None) -> dict[str, str]:
    interpreter = interpreter or sys.executable
    environment = {
        **os.environ, **fixture["environment"],
        "COPILOT_PLUGIN_ROOT": str(fixture["payload"]),
        "POWERSHELL_TELEMETRY_OPTOUT": "1",
        "POWERSHELL_UPDATECHECK": "Off",
        # The shell's asynchronous cache must not race the product-write snapshot.
        "PSModuleAnalysisCachePath": os.devnull,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for relative in (".cache/powershell", ".local/share/powershell/Modules"):
        (fixture["home"] / relative).mkdir(parents=True)
    if os.name == "nt":
        for relative in (
            "AppData/Local/Microsoft/PowerShell",
            "AppData/Local/Microsoft/Windows/PowerShell", "AppData/Roaming",
        ):
            (fixture["home"] / relative).mkdir(parents=True, exist_ok=True)
        return environment
    wrapper_dir = fixture["home"] / "test-bin"
    wrapper_dir.mkdir()
    bootstrap = (
        "import os,pwd,runpy,sys\n"
        "sys.dont_write_bytecode=True\n"
        "original=pwd.getpwuid\n"
        "def getpwuid(uid):\n"
        "    record=original(uid)\n"
        "    return pwd.struct_passwd((*record[:5],os.environ['HOME'],record.pw_shell))\n"
        "pwd.getpwuid=getpwuid\n"
        # Interpreter admission must retain the same isolated OS-profile wrapper.
        f"sys.executable={str(wrapper_dir / 'python3')!r}\n"
        "args=sys.argv[1:]\n"
        "while args and args[0].startswith('-') and args[0] not in ('-c','-m'):\n"
        "    option=args.pop(0)\n"
        "    if option=='-X': args.pop(0)\n"
        "if args and args[0]=='-c':\n"
        "    code=args[1]\n"
        "    sys.argv=['-c',*args[2:]]\n"
        "    exec(compile(code,'<catalog-command>','exec'),{'__name__':'__main__'})\n"
        "    raise SystemExit(0)\n"
        "if args:\n"
        "    sys.argv=args\n"
        "    runpy.run_path(args[0],run_name='__main__')\n"
        "else:\n"
        "    exec(compile(sys.stdin.read(),'<catalog-stdin>','exec'),{'__name__':'__main__'})\n"
    )
    for name in ("python3", "python"):
        wrapper = wrapper_dir / name
        wrapper.write_text(
            f"#!{interpreter} -I\n"
            "import os,sys\n"
            "args=sys.argv[1:]; flags=[]\n"
            "while args and args[0].startswith('-') and args[0] not in ('-c','-m'):\n"
            "    option=args.pop(0); flags.append(option)\n"
            "    if option=='-X': flags.append(args.pop(0))\n"
            "os.execv(sys.executable,[sys.executable,*flags,'-c',"
            f"{bootstrap!r},*sys.argv[1:]])\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    environment["PATH"] = str(wrapper_dir) + os.pathsep + os.environ["PATH"]
    return environment


def test_command_binding_catalog_disables_shell_metadata_cache(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PSModuleAnalysisCachePath", str(binding_fixture["home"] / "parent-cache"))
    assert _catalog_environment(binding_fixture)["PSModuleAnalysisCachePath"] == os.devnull


@pytest.mark.skipif(os.name == "nt", reason="POSIX catalog interpreter")
def test_command_binding_catalog_isolates_project_and_pythonpath(
    binding_fixture: dict,
) -> None:
    _prepare_catalog(binding_fixture)
    _publish(binding_fixture)
    environment = _catalog_environment(binding_fixture)
    project = binding_fixture["home"] / "project"
    inherited = binding_fixture["home"] / "inherited-python"
    for directory in (project, inherited):
        directory.mkdir()
        (directory / "json.py").write_text(
            "from pathlib import Path\n"
            "Path(__file__).with_name('imported').write_text('unexpected import')\n"
            "raise RuntimeError('project or inherited Python import executed')\n",
            encoding="utf-8",
        )
    environment["PYTHONPATH"] = str(inherited)
    before = _snapshot(binding_fixture["home"])
    for _ in range(2):
        completed = subprocess.run(
            ["bash", str(binding_fixture["payload"] / "scripts/emit-command-catalog.sh")],
            env=environment, cwd=project, capture_output=True, text=True, timeout=30,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        envelope = json.loads(completed.stdout)
        match = re.search(r"```json\n(.*)\n```", envelope["additionalContext"])
        assert match, completed.stdout
        catalog = json.loads(match[1])
        assert catalog["binding"]["status"] == "selected", catalog
        assert catalog["binding"]["source"]["identity"]["canonical"] == "github:example/plugins"
        assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX emitter")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
def test_command_binding_catalog_disables_user_site_startup(
    binding_fixture: dict, shell: str, powershell_host,
) -> None:
    _prepare_catalog(binding_fixture)
    _publish(binding_fixture)
    home = binding_fixture["home"]
    interpreter = sys._base_executable
    environment = _catalog_environment(binding_fixture, interpreter)
    if os.name == "nt":
        environment["APPDATA"] = str(home / "AppData/Roaming")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0}
    query = subprocess.run(
        [interpreter, "-I", "-B", "-c", "import site; print(site.getusersitepackages())"],
        env=environment, cwd=home, capture_output=True, text=True, timeout=10,
        check=True, **kwargs,
    )
    user_site = Path(query.stdout.strip())
    assert user_site.is_relative_to(home), user_site
    user_site.mkdir(parents=True, exist_ok=True)
    sentinel = user_site / "ambient-startup"
    (user_site / "catalog-fixture.pth").write_text(
        f"import pathlib; pathlib.Path({str(sentinel)!r}).write_text('ambient-startup')\n",
        encoding="utf-8",
    )
    control = subprocess.run(
        [interpreter, "-B", "-E", "-c", "pass"], env=environment, cwd=home,
        capture_output=True, text=True, timeout=10, **kwargs,
    )
    assert control.returncode == 0, control.stderr
    assert sentinel.read_text() == "ambient-startup"
    sentinel.unlink()
    if shell == "powershell":
        python = interpreter if os.name == "nt" else str(home / "test-bin/python")
        launcher = home / "site-launcher.ps1"
        launcher.write_text(
            "if ($args[0] -ne '-3') { throw 'Expected Python 3 launcher' }\n"
            "$probeArgs=@($args[1..($args.Count-1)])\n"
            "$null=& $env:TEST_PYTHON @probeArgs\n"
            "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
            "Write-Output $env:TEST_PYTHON\n",
            encoding="utf-8",
        )
        wrapper = home / "site-catalog.ps1"
        wrapper.write_text(
            "function Get-Command {\n"
            "    param($Name,$CommandType,$ErrorAction)\n"
            "    if ($Name -eq 'py') { return @{Source=$env:TEST_LAUNCHER_PATH} }\n"
            "    return $null\n"
            "}\n"
            "& (Join-Path $env:COPILOT_PLUGIN_ROOT 'scripts/emit-command-catalog.ps1')\n",
            encoding="utf-8",
        )
        environment.update({"TEST_PYTHON": python, "TEST_LAUNCHER_PATH": str(launcher)})
        host = powershell_host(binding_fixture, environment)
        command = [POWERSHELL, "-File", str(wrapper)]
    else:
        host = None
        command = ["bash", str(binding_fixture["payload"] / "scripts/emit-command-catalog.sh")]
    before = _snapshot(home)
    for _ in range(2):
        completed = host.run(command) if host else subprocess.run(
            command, env=environment, cwd=home, capture_output=True,
            text=True, timeout=30, **kwargs,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        envelope = json.loads(completed.stdout)
        match = re.search(r"```json\n(.*)\n```", envelope["additionalContext"])
        assert match, completed.stdout
        assert json.loads(match[1])["binding"]["status"] == "selected"
        assert _snapshot(home) == before
    if host:
        host.close()
        assert _snapshot(home) == before


class _PowerShellHost:
    """Initialize only the real host before measuring unchanged product scripts."""

    def __init__(self, home: Path, environment: dict[str, str]) -> None:
        script = home / "initialized-host.ps1"
        script.write_text(
            "$ErrorActionPreference='Stop'\n"
            "[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)\n"
            "$null=Get-Command py,python3,python -ErrorAction SilentlyContinue\n"
            "$null=@{schema='fixture';version=1;commands=@(@{argv=@('example')})}"
            "|ConvertTo-Json -Depth 8\n"
            "$null='{\"path\":\"fixture\",\"arguments\":[]}'|ConvertFrom-Json\n"
            # Stopping recording can itself write; it belongs before READY/baseline.
            "[System.Runtime.ProfileOptimization]::StartProfile($null)\n"
            "[Console]::WriteLine('{\"ready\":true}')\n"
            "while ($null -ne ($line=[Console]::ReadLine())) {\n"
            "    $request=$line|ConvertFrom-Json\n"
            "    $arguments=@($request.arguments)\n"
            "    $global:LASTEXITCODE=0\n"
            "    $code=0; $errors=@(); $output=@()\n"
            "    $consoleOut=[Console]::Out; $consoleErr=[Console]::Error\n"
            "    $capturedOut=New-Object IO.StringWriter\n"
            "    $capturedErr=New-Object IO.StringWriter\n"
            "    [Console]::SetOut($capturedOut); [Console]::SetError($capturedErr)\n"
            "    try {\n"
            "        $records=& $request.path @arguments *>&1\n"
            "        if (-not $?) { $code=$LASTEXITCODE; if (-not $code) { $code=1 } }\n"
            "        foreach ($record in $records) {\n"
            "            if ($record -is [System.Management.Automation.ErrorRecord]) {\n"
            "                $errors+=($record|Out-String)\n"
            "            } else { $output+=[string]$record }\n"
            "        }\n"
            "    } catch { $code=1; $errors+=($_|Out-String) } finally {\n"
            "        [Console]::SetOut($consoleOut); [Console]::SetError($consoleErr)\n"
            "    }\n"
            "    $response=@{returncode=$code;stdout=(($output -join \"`n\")+$capturedOut.ToString());"
            "stderr=(($errors -join \"`n\")+$capturedErr.ToString())}|ConvertTo-Json -Compress\n"
            "    $capturedOut.Dispose(); $capturedErr.Dispose()\n"
            "    [Console]::WriteLine($response)\n"
            "}\n",
            encoding="utf-8",
        )
        self.process = subprocess.Popen(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(script)],
            env=environment, cwd=home, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.errors: list[str] = []
        self.closed = False

        def read_output() -> None:
            assert self.process.stdout
            for line in self.process.stdout:
                self.lines.put(line)
            self.lines.put(None)

        def read_errors() -> None:
            assert self.process.stderr
            self.errors.extend(self.process.stderr)

        self.readers = [
            threading.Thread(target=read_output, daemon=True),
            threading.Thread(target=read_errors, daemon=True),
        ]
        for reader in self.readers:
            reader.start()
        try:
            assert self._response() == {"ready": True}
        except BaseException:
            self.close()
            raise

    def _response(self) -> dict:
        try:
            line = self.lines.get(timeout=30)
        except queue.Empty:
            raise AssertionError("PowerShell fixture host timed out") from None
        assert line is not None, "PowerShell fixture host exited: " + "".join(self.errors)
        return json.loads(line)

    def run(self, argv: list[str]) -> subprocess.CompletedProcess:
        index = argv.index("-File")
        request = {"path": argv[index + 1], "arguments": argv[index + 2:]}
        assert self.process.stdin
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        response = self._response()
        return subprocess.CompletedProcess(
            argv, response["returncode"], response["stdout"], response["stderr"],
        )

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        assert self.process.stdin
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        for reader in self.readers:
            reader.join(timeout=10)
            assert not reader.is_alive(), "PowerShell fixture reader did not exit"
        assert self.process.stdout and self.process.stderr
        self.process.stdout.close()
        self.process.stderr.close()


@pytest.fixture
def powershell_host():
    hosts: list[_PowerShellHost] = []

    def start(fixture: dict, environment: dict[str, str]) -> _PowerShellHost:
        host = _PowerShellHost(fixture["home"], environment)
        hosts.append(host)
        return host

    yield start
    for host in hosts:
        host.close()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("result", ["ignored-native-failure", "exit", "throw"])
def test_command_binding_initialized_host_preserves_script_outcome(
    binding_fixture: dict, result: str, powershell_host,
) -> None:
    environment = _catalog_environment(binding_fixture)
    environment["TEST_PYTHON"] = sys.executable
    script = binding_fixture["home"] / "host-outcome.ps1"
    script.write_text({
        "ignored-native-failure": (
            "& $env:TEST_PYTHON -c 'raise SystemExit(7)'\nWrite-Output 'complete'\n"
        ),
        "exit": "Write-Output 'complete'\nexit 7\n",
        "throw": "throw 'fixture-script-error'\n",
    }[result], encoding="utf-8")
    host = powershell_host(binding_fixture, environment)
    completed = host.run([POWERSHELL, "-File", str(script)])
    assert completed.returncode == {"ignored-native-failure": 0, "exit": 7, "throw": 1}[result]
    if result == "throw":
        assert "fixture-script-error" in completed.stderr
    else:
        assert completed.stdout == "complete"


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX emitter")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
@pytest.mark.parametrize("owned", [False, True])
def test_command_binding_real_catalog_read_only(
    binding_fixture: dict, shell: str, owned: bool, powershell_host,
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
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    for _ in range(2):
        completed = host.run(argv) if host else subprocess.run(
            argv, env=environment, cwd=binding_fixture["home"],
            capture_output=True, text=True, timeout=30, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
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
    if host:
        host.close()
    assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("launcher", ["py", "python", "alias-only"])
def test_command_binding_catalog_python_launcher_avoids_app_alias(
    binding_fixture: dict, launcher: str, powershell_host,
) -> None:
    _prepare_catalog(binding_fixture)
    _publish(binding_fixture)
    environment = _catalog_environment(binding_fixture)
    python = sys.executable if os.name == "nt" else str(binding_fixture["home"] / "test-bin/python")
    launcher_path = binding_fixture["home"] / "py-launcher.ps1"
    launcher_path.write_text(
        "if ($args[0] -ne '-3') { throw 'Python launcher must select Python 3' }\n"
        "if (($args[1..6] -join ' ') -ne '-I -B -E -X utf8 -c') { throw 'Unexpected probe flags' }\n"
        "Write-Output $env:TEST_PYTHON\nexit 0\n",
        encoding="utf-8",
    )
    environment.update({
        "TEST_PYTHON": python, "TEST_LAUNCHER": launcher,
        "TEST_LAUNCHER_PATH": str(launcher_path),
    })
    wrapper = binding_fixture["home"] / "app-alias-fixture.ps1"
    wrapper.write_text(
        "function Get-Command {\n"
        "    param($Name,$CommandType,$ErrorAction)\n"
        "    if ($Name -eq 'py' -and $env:TEST_LAUNCHER -eq 'py') {\n"
        "        return @{Source=$env:TEST_LAUNCHER_PATH}\n"
        "    }\n"
        "    if ($Name -eq 'python3') { return $null }\n"
        "    if ($Name -eq 'python') {\n"
        "        if ($env:TEST_LAUNCHER -eq 'python') { return @{Source=$env:TEST_PYTHON} }\n"
        "        return @{Source='C:\\Users\\example\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe'}\n"
        "    }\n"
        "}\n"
        "& (Join-Path $env:COPILOT_PLUGIN_ROOT 'scripts/emit-command-catalog.ps1')\n",
        encoding="utf-8",
    )
    host = powershell_host(binding_fixture, environment)
    completed = host.run([POWERSHELL, "-File", str(wrapper)])
    assert completed.returncode == 0, completed.stdout + completed.stderr
    envelope = json.loads(completed.stdout)
    match = re.search(r"```json\n(.*)\n```", envelope["additionalContext"])
    assert match, completed.stdout
    binding = json.loads(match.group(1))["binding"]
    if launcher == "alias-only":
        assert binding["status"] == "unresolved"
        assert binding["reason"] == "binding-interpreter-unavailable"
    else:
        assert binding["status"] == "selected", binding


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX installer")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
@pytest.mark.parametrize("contextual", [False, True])
def test_command_binding_native_manifest_publication_and_foreign_refusal(
    binding_fixture: dict, shell: str, contextual: bool, powershell_host,
) -> None:
    _prepare_catalog(binding_fixture)
    runtime = binding_fixture["legacy"]
    if contextual:
        selected = _activate(binding_fixture, binding_fixture["payload"])
        runtime = Path(selected["pluginRoot"])
        binding_fixture["environment"]["COPILOT_EXTENSIONS_CONTEXT"] = selected["installReceipt"]
        _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": True, "marketplaces": {}},
        })
    environment = _catalog_environment(binding_fixture)
    environment.update({
        "TEST_PAYLOAD": str(binding_fixture["payload"]),
        "TEST_RUNTIME": str(runtime),
        "TEST_SOURCE_SCRIPTS": str(ROOT / "plugins/agent-worktrees/scripts"),
        "TEST_PYTHON": (
            sys.executable if os.name == "nt" else
            str(binding_fixture["home"] / "test-bin/python3")
        ),
        "COMPUTERNAME": "example-host",
    })
    if shell == "bash":
        source = (ROOT / "plugins/agent-worktrees/scripts/install.sh").read_text()
        function = "_build_deploy_manifest_json() {" + source.split(
            "_build_deploy_manifest_json() {", 1
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
            "SCRIPT_DIR=\"$TEST_SOURCE_SCRIPTS\"\nSRC_VERSION='1.0.0'\n"
            '. "$SCRIPT_DIR/source-runtime.sh"\n_initialize_source_runtime\n'
            f"CONTEXTUAL_INSTALL={'true' if contextual else 'false'}\n"
            + function + "\nwrite_deploy_manifest\n"
        )
        argv = ["bash", "-c", program]
    else:
        source = (ROOT / "plugins/agent-worktrees/scripts/install.ps1").read_text()
        function = "function New-V3Manifest {" + source.split(
            "function New-V3Manifest {", 1
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
            "$ScriptDir=$env:TEST_SOURCE_SCRIPTS\n$SrcVersion='1.0.0'\n"
            ". (Join-Path $ScriptDir 'source-runtime.ps1')\nInitialize-SourceRuntime\n"
            f"$ContextualInstall=${'true' if contextual else 'false'}\n"
            + function + "\nWrite-V3Manifest\n"
        )
        program_path = binding_fixture["home"] / "publish-fixture.ps1"
        program_path.write_text(program, encoding="utf-8")
        argv = [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(program_path)]
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None

    def run() -> subprocess.CompletedProcess:
        return host.run(argv) if host else subprocess.run(
            argv, env=environment, cwd=binding_fixture["home"],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    completed = run()
    assert completed.returncode == 0, completed.stdout + completed.stderr
    resolved = _resolve(binding_fixture)
    assert resolved["status"] == "selected", resolved
    if contextual:
        manifest = json.loads((runtime / "deploy-manifest.json").read_text(encoding="utf-8"))
        assert manifest["schema_version"] == 3
        assert manifest["service"] == PLUGIN_ID
        assert "commandBinding" not in manifest
        assert "commandLifecycle" not in manifest
        assert not (binding_fixture["legacy"] / "deploy-manifest.json").exists()
        repeated = run()
        assert repeated.returncode == 0, repeated.stdout + repeated.stderr
        assert _resolve(binding_fixture)["status"] == "selected"
        return
    _write(binding_fixture["home"] / ".copilot/settings.json", {
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Other/Plugins"}}
        },
    })
    before = _snapshot(binding_fixture["home"])
    refused = run()
    assert refused.returncode != 0
    assert "Command ownership" in refused.stderr
    if host:
        host.close()
    after = _snapshot(binding_fixture["home"])
    assert after == before


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
    assert not _lifecycle_lock_path(binding_fixture["legacy"]).exists()
    selected = []
    for payload in (binding_fixture["payload"], other):
        binding_fixture["arguments"]["payload_root"] = payload
        selected.append(_resolve(binding_fixture)["status"])
    assert sorted(selected) == ["blocked", "selected"]
