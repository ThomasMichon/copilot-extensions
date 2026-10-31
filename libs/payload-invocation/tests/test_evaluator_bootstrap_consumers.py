"""Native PATH evaluator consumers, SDK contracts and finite cleanup."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import test_command_binding
from test_adopter_publication import _adopter
from test_command_binding import ROOT, _snapshot, _write
from test_native_publication import _completed_slot

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows dispatcher")
PLUGINS = ("agent-codespaces", "agent-containers")
base_binding_fixture = test_command_binding.binding_fixture


@pytest.fixture(params=PLUGINS)
def binding_fixture(base_binding_fixture, request):
    base_binding_fixture["consumer"] = request.param
    return base_binding_fixture


@pytest.fixture(params=("powershell", "pwsh"))
def host(request):
    executable = shutil.which(request.param)
    if not executable:
        pytest.skip(f"{request.param} is unavailable")
    return executable


def _fixture(fixture, *, route="selected", namespaced=False):
    plugin = fixture["consumer"]
    prefix = plugin.upper().replace("-", "_")
    environment = _adopter(fixture, plugin)
    root = fixture["legacy"]
    scripts = fixture["payload"] / "scripts"
    context = fixture["home"] / f".copilot-extensions/marketplaces/example/plugins/{plugin}/install.json"
    status = {
        "status": "ready", "reason": "namespaced-active" if namespaced else "legacy",
        "actualMode": "namespaced" if namespaced else "legacy",
        "desiredMode": "namespaced" if namespaced else "legacy",
        "runtimeRoot": str(root), "context": str(context), "installGeneration": 7,
        "legacy": {"root": str(root)},
    }
    environment.update({
        prefix + "_HOME": str(root), prefix + "_PAYLOAD_ROOT": str(fixture["payload"]),
    })
    environment.update(
        TEST_ROUTE=route, TEST_BOOTSTRAP_PYTHON=sys.executable,
        TEST_STATUS=json.dumps(status),
        TEST_VALIDATION=json.dumps({"generation": 7, "pluginRoot": str(root)}),
        TEST_EXIT="0", TEST_VALIDATION_EXIT="0",
        TEST_READ_LOG=str(fixture["home"] / "reads.jsonl"),
        TEST_DISPOSED=str(fixture["home"] / "disposed"),
        TEST_EXCLUDED=str(fixture["home"] / "excluded.json"),
    )
    if namespaced:
        environment["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    (scripts / "bootstrap-python.ps1").write_text(
        "function Resolve-EvaluatorBootstrapPython {\n"
        " param([string[]]$ExcludedRoots = @())\n"
        " if ($env:TEST_ROUTE -eq 'error') { throw 'fixture selector failure' }\n"
        " if ($env:TEST_ROUTE -eq 'unavailable') {\n"
        "  return [pscustomobject]@{Status='unavailable';Reason='fixture-absent';Python=$null;Guard=$null}\n"
        " }\n"
        " [IO.File]::WriteAllText($env:TEST_EXCLUDED, "
        "(ConvertTo-Json -InputObject @($ExcludedRoots) -Compress))\n"
        " $guard = [pscustomobject]@{}\n"
        " $guard | Add-Member ScriptMethod Dispose {\n"
        "  [IO.File]::WriteAllText($env:TEST_DISPOSED, 'disposed')\n"
        " }\n"
        " $status = if ($env:TEST_ROUTE -eq 'protocol') { 'invalid' } else { 'selected' }\n"
        " [pscustomobject]@{Status=$status;Reason='fixture-ABI-only';"
        "Python=$env:TEST_BOOTSTRAP_PYTHON;Guard=$guard}\n"
        "}\n", encoding="utf-8",
    )
    (scripts / "installation-context/installation_context.py").write_text(
        "import json, os, sys\n"
        "action = sys.argv[1]\n"
        "with open(os.environ['TEST_READ_LOG'], 'a', encoding='utf-8') as output:\n"
        " output.write(json.dumps({'route':'portable', 'args':sys.argv[1:], "
        "'isolated':sys.flags.isolated, 'no_site':sys.flags.no_site, "
        "'no_bytecode':sys.flags.dont_write_bytecode, 'utf8':sys.flags.utf8_mode})+'\\n')\n"
        "print('fixture evaluator diagnostic', file=sys.stderr)\n"
        "print(os.environ['TEST_STATUS' if action == 'status' else 'TEST_VALIDATION'])\n"
        "sys.exit(int(os.environ['TEST_EXIT' if action == 'status' else 'TEST_VALIDATION_EXIT']))\n",
        encoding="utf-8",
    )
    (scripts / "installation-context/installation-context.ps1").write_text(
        "[IO.File]::AppendAllText($env:TEST_READ_LOG, "
        "'{\"route\":\"native\"}' + [Environment]::NewLine)\n"
        "if ($args[0] -eq 'status') { $env:TEST_STATUS; exit ([int]$env:TEST_EXIT) }\n"
        "$env:TEST_VALIDATION; exit ([int]$env:TEST_VALIDATION_EXIT)\n",
        encoding="utf-8",
    )
    python = _completed_slot(fixture)
    site = python.parent.parent / "Lib/site-packages"
    (site / (plugin.replace("-", "_") + ".py")).write_text(
        "import json, site, sys\n"
        "assert not sys.flags.no_site\n"
        "assert any(path in sys.path for path in site.getsitepackages())\n"
        "print(json.dumps(sys.argv[1:], ensure_ascii=False))\n", encoding="utf-8",
    )
    environment["TEST_RUNTIME_PYTHON"] = str(python)
    (scripts / "resolve-runtime.ps1").write_text(
        "# Trace contract: normal/read-only/ready-v1\n"
        "if ($env:TEST_ROUTE -eq 'selected' -and -not [IO.File]::Exists($env:TEST_DISPOSED)) {\n"
        " throw 'bootstrap guard was not disposed before ordinary runtime resolution'\n"
        "}\n"
        "$script:AgentRtPy=$env:TEST_RUNTIME_PYTHON\n", encoding="utf-8",
    )
    return environment


def _run(fixture, environment, host, *arguments):
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File",
         str(fixture["payload"] / "scripts/runtime-gate.ps1"), *arguments],
        env=environment, cwd=fixture["home"], capture_output=True, text=True,
        encoding="utf-8", timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    log = Path(environment["TEST_READ_LOG"])
    reads = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, reads


def _refusal_snapshot(home):
    snapshot = _snapshot(home)
    # Empty Desktop and Core hosts each rewrite their own CLR startup profile.
    for directory in ("Windows/PowerShell", "PowerShell"):
        snapshot.pop(
            f"AppData/Local/Microsoft/{directory}/StartupProfileData-NonInteractive",
            None,
        )
    return snapshot


@pytest.mark.parametrize(("change", "profile_directory"), (
    pytest.param("product-state", "Windows/PowerShell", id="product-state"),
    pytest.param("unexpected-write", "Windows/PowerShell", id="unexpected-write"),
    pytest.param("product-state", "PowerShell", id="core-product-state"),
    pytest.param("unexpected-write", "PowerShell", id="core-unexpected-write"),
))
def test_refusal_snapshot_preserves_every_other_home_entry(tmp_path, change, profile_directory):
    profile = tmp_path / f"AppData/Local/Microsoft/{profile_directory}/StartupProfileData-NonInteractive"
    profile.parent.mkdir(parents=True)
    profile.write_bytes(b"first runtime profile")
    product = tmp_path / ".copilot-extensions/installation-mode.json"
    product.parent.mkdir()
    product.write_bytes(b"original product state")
    before = _refusal_snapshot(tmp_path)
    profile.write_bytes(b"second runtime profile")
    assert _refusal_snapshot(tmp_path) == before
    if change == "product-state":
        product.write_bytes(b"changed product state")
    else:
        profile.with_name("unexpected-write").write_bytes(b"new")
    assert _refusal_snapshot(tmp_path) != before


@pytest.mark.parametrize("namespaced", (False, True))
def test_selected_evaluator_is_fresh_isolated_and_releases_before_runtime(
    binding_fixture, host, namespaced,
):
    binding_fixture["payload"] = (
        binding_fixture["payload"].parent / "publisher's \u00e9\u6d4b\u8bd5" / "agent-worktrees"
    )
    binding_fixture["payload"].parent.mkdir()
    environment = _fixture(binding_fixture, namespaced=namespaced)
    ambient = binding_fixture["home"] / "ambient"
    ambient.mkdir()
    for directory in (
        ambient,
        binding_fixture["payload"] / "scripts/installation-context",
    ):
        (directory / "json.py").write_text("raise RuntimeError('ambient module executed')\n")
        (directory / "sitecustomize.py").write_text("raise RuntimeError('ambient startup executed')\n")
    environment["PYTHONPATH"] = str(ambient)
    environment["PYTHONHOME"] = str(binding_fixture["home"] / "not-a-python")
    forwarded = ["ssh", "--remote-cmd", 'echo "spaces & pipes | ; !" \u00e9\u6d4b\u8bd5']
    result, reads = _run(binding_fixture, environment, host, *forwarded)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == forwarded
    assert "fixture evaluator diagnostic" in result.stderr
    assert [read["args"][0] for read in reads] == (
        ["status", "validate"] if namespaced else ["status"]
    )
    assert all(read["route"] == "portable" for read in reads)
    assert all(
        read[key] == 1 for read in reads
        for key in ("isolated", "no_site", "no_bytecode", "utf8")
    )
    expected = [
        "status", "--copilot-home", str(binding_fixture["home"] / ".copilot"),
        "--payload-root", str(binding_fixture["payload"]),
        "--plugin-id", binding_fixture["consumer"], "--legacy-root", str(binding_fixture["legacy"]),
    ]
    durable = binding_fixture["home"] / ".copilot-extensions"
    if namespaced:
        context = environment["COPILOT_EXTENSIONS_CONTEXT"]
        expected += ["--context", context, "--durable-home", str(durable)]
        assert reads[1]["args"] == [
            "validate", "--copilot-home", str(binding_fixture["home"] / ".copilot"),
            "--context", context, "--durable-home", str(durable),
            "--expected-plugin-id", binding_fixture["consumer"],
            "--expected-payload-root", str(binding_fixture["payload"]),
        ]
    assert reads[0]["args"] == expected
    assert json.loads(Path(environment["TEST_EXCLUDED"]).read_text()) == []
    assert Path(environment["TEST_DISPOSED"]).read_text() == "disposed"


@pytest.mark.parametrize("route", ("unavailable", "missing", "missing-sdk"))
def test_missing_prerequisites_refuse_without_any_evaluator(binding_fixture, host, route):
    environment = _fixture(binding_fixture, route=route)
    if route == "missing":
        (binding_fixture["payload"] / "scripts/bootstrap-python.ps1").unlink()
    elif route == "missing-sdk":
        (binding_fixture["payload"] / "scripts/installation-context/installation_context.py").unlink()
    result, reads = _run(binding_fixture, environment, host)
    assert result.returncode == 126, result.stdout + result.stderr
    assert result.stdout == ""
    assert reads == []
    assert (
        "Python 3.10+ must be available as python on PATH" if route == "unavailable"
        else "payload Python evaluator is unavailable"
    ) in result.stderr
    assert not Path(environment["TEST_DISPOSED"]).exists()
    if route != "unavailable":
        assert not Path(environment["TEST_EXCLUDED"]).exists()


def test_native_path_semantics_still_require_the_python_prerequisite(binding_fixture, host):
    environment = _fixture(binding_fixture, route="native-path")
    environment[binding_fixture["consumer"].upper().replace("-", "_") + "_HOME"] = (
        r"%USERPROFILE%\." + binding_fixture["consumer"]
    )
    result, reads = _run(binding_fixture, environment, host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert reads == [{"route": "native"}]
    assert json.loads(Path(environment["TEST_EXCLUDED"]).read_text()) == []
    assert Path(environment["TEST_DISPOSED"]).read_text() == "disposed"
    environment["TEST_ROUTE"] = "unavailable"
    result, subsequent_reads = _run(binding_fixture, environment, host)
    assert result.returncode == 126, result.stdout + result.stderr
    assert subsequent_reads == reads
    assert "Python 3.10+ must be available as python on PATH" in result.stderr


def test_path_lookup_does_not_normalize_or_filter_relative_sdk_arguments(binding_fixture, host):
    environment = _fixture(binding_fixture)
    environment[binding_fixture["consumer"].upper().replace("-", "_") + "_HOME"] = r".\relative runtime"
    script = binding_fixture["payload"] / "scripts/runtime-gate.ps1"
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command",
         f"[IO.Directory]::SetCurrentDirectory({quote(binding_fixture['home'].parent)}); "
         f"Set-Location -LiteralPath {quote(binding_fixture['home'])}; "
         f"& {quote(script)}; exit $LASTEXITCODE"],
        env=environment, cwd=binding_fixture["home"], capture_output=True,
        text=True, encoding="utf-8", timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(Path(environment["TEST_EXCLUDED"]).read_text()) == []
    read = json.loads(Path(environment["TEST_READ_LOG"]).read_text())
    assert read["args"][-1] == r".\relative runtime"


@pytest.mark.parametrize("namespaced", (False, True))
def test_absent_userprofile_preserves_powershell_home_fallback(binding_fixture, host, namespaced):
    environment = _fixture(binding_fixture, namespaced=namespaced)
    environment.pop("USERPROFILE", None)
    environment["TEST_FALLBACK_HOME"] = str(binding_fixture["home"])
    script = binding_fixture["payload"] / "scripts/runtime-gate.ps1"
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command",
         "Set-Variable -Name HOME -Value $env:TEST_FALLBACK_HOME -Force; "
         f"& {quote(script)} version; exit $LASTEXITCODE"],
        env=environment, cwd=binding_fixture["home"], capture_output=True,
        text=True, encoding="utf-8", timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == ["version"]
    assert "fixture evaluator diagnostic" in result.stderr
    assert json.loads(Path(environment["TEST_EXCLUDED"]).read_text()) == []
    reads = [json.loads(line) for line in Path(environment["TEST_READ_LOG"]).read_text().splitlines()]
    assert [read["args"][0] for read in reads] == (
        ["status", "validate"] if namespaced else ["status"]
    )
    for read in reads:
        assert read["route"] == "portable"
        home_index = read["args"].index("--copilot-home") + 1
        assert read["args"][home_index] == str(binding_fixture["home"] / ".copilot")


@pytest.mark.parametrize("forwarded", (
    [], [""], ["version"],
    ["prune", "--max", 5, 'echo "quoted & pipes | ; !" \u00e9\u6d4b\u8bd5', "C:\\space path\\"],
))
def test_native_argument_modes_preserve_empty_numeric_and_quoted_arguments(binding_fixture, host, forwarded):
    environment = _fixture(binding_fixture)
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    values = "@(" + ", ".join(str(value) if isinstance(value, int) else quote(value) for value in forwarded) + ")"
    script = binding_fixture["payload"] / "scripts/runtime-gate.ps1"
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command",
         f"$forwarded={values}; & {quote(script)} @forwarded; exit $LASTEXITCODE"],
        env=environment, cwd=binding_fixture["home"], capture_output=True,
        text=True, encoding="utf-8", timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == [str(value) for value in forwarded]


def test_containers_keeps_the_validated_runtime_root_check(base_binding_fixture, host):
    fixture = {**base_binding_fixture, "consumer": "agent-containers"}
    environment = _fixture(fixture, namespaced=True)
    environment["TEST_VALIDATION"] = json.dumps({
        "generation": 7, "pluginRoot": str(fixture["home"] / "foreign-runtime"),
    })
    result, reads = _run(fixture, environment, host)
    assert result.returncode == 126, result.stdout + result.stderr
    assert "generation does not match governance" in result.stderr
    assert [read["args"][0] for read in reads] == ["status", "validate"]
    assert Path(environment["TEST_DISPOSED"]).exists()


@pytest.mark.parametrize("failure", (
    "selector", "protocol", "launch", "status-exit", "status-malformed", "status-refusal",
    "validate-exit", "validate-malformed", "generation",
))
def test_post_selection_failures_are_terminal_and_cleanup_is_owned(
    binding_fixture, host, failure,
):
    environment = _fixture(binding_fixture, namespaced=failure.startswith("validate") or failure == "generation")
    if failure == "selector":
        environment["TEST_ROUTE"] = "error"
    elif failure == "protocol":
        environment["TEST_ROUTE"] = "protocol"
    elif failure == "launch":
        environment["TEST_BOOTSTRAP_PYTHON"] = str(binding_fixture["home"] / "absent.exe")
    elif failure == "status-exit":
        environment["TEST_EXIT"] = "17"
    elif failure == "status-malformed":
        environment["TEST_STATUS"] = "{"
    elif failure == "status-refusal":
        environment["TEST_STATUS"] = json.dumps({
            "status": "provenance-blocked", "reason": "fixture-refused",
        })
    elif failure == "validate-exit":
        environment["TEST_VALIDATION_EXIT"] = "19"
    elif failure == "validate-malformed":
        environment["TEST_VALIDATION"] = "{"
    else:
        environment["TEST_VALIDATION"] = json.dumps({"generation": 8})
    result, reads = _run(binding_fixture, environment, host)
    assert result.returncode == (
        1 if failure in {"selector", "protocol", "launch"} else 126
    ), result.stderr
    diagnostic = {
        "selector": "fixture selector failure",
        "protocol": "invalid selection status",
        "launch": "absent.exe",
        "status-exit": "installation context could not be resolved",
        "status-malformed": "returned malformed status",
        "status-refusal": "reason=fixture-refused",
        "validate-exit": "installation context validation failed",
        "validate-malformed": "installation context validation was malformed",
        "generation": "generation does not match governance",
    }[failure]
    assert diagnostic in result.stderr
    assert len(reads) == (
        0 if failure in {"selector", "protocol", "launch"} else
        2 if failure.startswith("validate") or failure == "generation" else 1
    )
    assert all(read["route"] == "portable" for read in reads)
    assert result.stdout.strip() == ""
    assert Path(environment["TEST_DISPOSED"]).exists() == (failure != "selector")
    assert not (binding_fixture["legacy"] / ".provision.lock").exists()
    assert not (binding_fixture["legacy"] / ".provision-status").exists()


@pytest.mark.parametrize("plugin", PLUGINS)
def test_actual_sdk_is_payload_local(plugin) -> None:
    gate = (ROOT / "plugins" / plugin / "scripts/runtime-gate.ps1").read_text()
    assert "'installation-context\\installation_context.py'" in gate
    assert "& $bootstrap.Python -I -S -B -X utf8 $portableModeRunner" in gate


@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize("state", (
    "legacy", "namespaced", "deactivation", "foreign-source", "stale-context", "invalid-policy",
    "legacy-unequal-home", "namespaced-unequal-home",
))
def test_actual_sdk_routes_preserve_complete_status_and_refusals(binding_fixture, host, state):
    environment = _fixture(binding_fixture)
    if state.endswith("-unequal-home"):
        environment["HOME"] = str(binding_fixture["home"] / "different-home")
        state = state.removesuffix("-unequal-home")
    plugin = binding_fixture["consumer"]
    scripts = binding_fixture["payload"] / "scripts"
    for name in ("installation_context.py", "installation-context.ps1"):
        shutil.copyfile(
            ROOT / "plugins" / plugin / "scripts/installation-context" / name,
            scripts / "installation-context" / name,
        )
    environment["TEST_RESOLUTION"] = str(binding_fixture["home"] / "resolution.json")
    resolver = scripts / "resolve-runtime.ps1"
    resolver.write_text(
        resolver.read_text()
        + "[IO.File]::WriteAllText($env:TEST_RESOLUTION, "
        "(ConvertTo-Json -InputObject $resolution -Depth 100 -Compress))\n",
        encoding="utf-8",
    )
    primitive = binding_fixture["primitive"]
    if state not in {"legacy", "invalid-policy"}:
        arguments = binding_fixture["arguments"]
        cell = primitive.stamp_context(
            payload_version="1.0.0", payload_origin="installed",
            expected_namespace_generation=0, expected_install_generation=0,
            **{key: arguments[key] for key in ("payload_root", "plugin_id", "environment")},
        )
        primitive.compare_and_swap_activation(
            context=cell["installReceipt"], expected_marketplace_id=cell["marketplaceId"],
            expected_plugin_id=plugin,
            expected_namespace_generation=cell["namespaceGeneration"],
            expected_install_generation=cell["generation"],
            expected_activation_generation=0, activation_mode="namespaced",
            activation_state="active", legacy_disposition="absent",
            legacy_probe={"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
            legacy_root=binding_fixture["legacy"], environment=binding_fixture["environment"],
        )
        _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": state != "deactivation", "marketplaces": {}},
        })
        environment["COPILOT_EXTENSIONS_CONTEXT"] = cell["installReceipt"]
        if state == "foreign-source":
            receipt = Path(json.loads(Path(cell["installReceipt"]).read_text())["namespaceReceipt"])
            value = json.loads(receipt.read_text())
            assert value["source"]["fingerprint"].startswith("sha256:")
            value["source"]["fingerprint"] = "sha256:" + "0" * 64
            _write(receipt, value)
        elif state == "stale-context":
            receipt = Path(cell["installReceipt"])
            value = json.loads(receipt.read_text())
            value["generation"] += 1
            _write(receipt, value)
    elif state == "invalid-policy":
        _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": "true"},
        })
    runtime_before = _snapshot(binding_fixture["legacy"])
    result, _ = _run(binding_fixture, environment, host, "version")
    assert _snapshot(binding_fixture["legacy"]) == runtime_before
    resolution_path = Path(environment["TEST_RESOLUTION"])
    if state in {"legacy", "namespaced", "deactivation"}:
        assert result.returncode == 0, result.stdout + result.stderr
        resolution = json.loads(resolution_path.read_text())
        assert resolution["schema"] == "copilot-extensions.installation-resolution"
        assert resolution["actualMode"] == ("legacy" if state == "legacy" else "namespaced")
        assert resolution["status"] == ("deactivation-required" if state == "deactivation" else "ready")
    else:
        assert result.returncode == 126, result.stdout + result.stderr
        assert not resolution_path.exists()
    before_refusal = _refusal_snapshot(binding_fixture["home"])
    environment["TEST_ROUTE"] = "unavailable"
    result, reads = _run(binding_fixture, environment, host, "version")
    assert result.returncode == 126, result.stdout + result.stderr
    assert result.stdout == ""
    assert reads == []
    assert "Python 3.10+ must be available as python on PATH" in result.stderr
    assert _refusal_snapshot(binding_fixture["home"]) == before_refusal


@pytest.mark.portfolio_tier("T3")
def test_real_path_python_runs_actual_sdk_without_attestation_or_guard(binding_fixture, host):
    environment = _fixture(binding_fixture)
    plugin = binding_fixture["consumer"]
    scripts = binding_fixture["payload"] / "scripts"
    for name in ("installation_context.py", "installation-context.ps1"):
        shutil.copyfile(
            ROOT / "plugins" / plugin / "scripts/installation-context" / name,
            scripts / "installation-context" / name,
        )
    helper = scripts / "bootstrap-python.ps1"
    shutil.copyfile(
        ROOT / "libs/installer-engine/bootstrap-python.ps1", helper,
    )
    environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + environment["PATH"]
    (scripts / "resolve-runtime.ps1").write_text(
        "# Trace contract: normal/read-only/ready-v1\n"
        "if ($bootstrap.Status -cne 'selected' -or "
        "$bootstrap.Reason -cne 'path-python') { throw 'PATH Python was not selected' }\n"
        "if ($null -ne $bootstrap.Guard) { throw 'PATH discovery must not pin the interpreter' }\n"
        "if ($bootstrap.Python -ine $env:TEST_BOOTSTRAP_PYTHON) { throw 'PATH order was not preserved' }\n"
        "$script:AgentRtPy=$env:TEST_RUNTIME_PYTHON\n", encoding="utf-8",
    )
    result, _ = _run(binding_fixture, environment, host, "version")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == ["version"]
    assert result.stderr == ""
