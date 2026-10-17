from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

LIBRARY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRARY))
import versioned_runtime_source as identity

VERSION = "0.4.1-dev3"
COMMIT = "a" * 40
TREE = "b" * 40
ASSEMBLY = "c" * 64


def _bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else None
    return str(candidate) if candidate is not None and candidate.is_file() else None


def _posix(path: Path) -> str:
    value = path.as_posix()
    if os.name == "nt" and len(value) > 1 and value[1] == ":":
        return "/" + value[0].lower() + value[2:]
    return value


def _function(path: Path, name: str, *, powershell: bool = False) -> str:
    prefix = f"function {name} " if powershell else f"{name}() "
    text = path.read_text()
    if not powershell:
        opening = re.search(rf"^{re.escape(prefix)}\{{", text, flags=re.MULTILINE)
        assert opening is not None, f"Missing production function {name}"
        following = re.search(
            r"^[A-Za-z_][A-Za-z0-9_]*\(\) \{", text[opening.end():], flags=re.MULTILINE,
        )
        end = opening.end() + following.start() if following else len(text)
        closing = text.rfind("\n}\n", opening.start(), end)
        assert closing >= 0, f"Missing production function boundary {name}"
        return text[opening.start():closing + 2]
    match = re.search(
        rf"^{re.escape(prefix)}\{{.*?^\}}", text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"Missing production function {name}"
    return match.group(0)


@pytest.fixture
def payload(tmp_path: Path) -> Path:
    root = tmp_path / "payload"
    root.mkdir()
    (root / "plugin.json").write_text(
        json.dumps({"name": "agent-bridge", "version": VERSION}), encoding="utf-8",
    )
    digest = identity.assembly_hash(root)
    source = identity.SourceIdentity(
        "agent-bridge", VERSION, COMMIT, TREE, digest,
        identity.runtime_version(VERSION, COMMIT, digest),
    )
    (root / identity.SOURCE_FILE).write_text(
        json.dumps(source.document()), encoding="utf-8",
    )
    return root


def test_source_identity_preserves_package_and_full_source_identity(payload: Path) -> None:
    source = identity.read_source_identity(payload, VERSION)
    assert source is not None
    assert source.package_version == VERSION
    assert COMMIT in source.runtime_version
    assert source.runtime_version.startswith(VERSION + "+source.")
    assert identity.manifest_fields(source)["runtime_source"] == source.document()


@pytest.mark.parametrize(
    "version", ["0.4.1-dev3", "0.4.1.dev3", "0.4.1-dev3+base", "0.4.1.dev3+base"],
)
def test_source_identity_accepts_original_and_normalized_dev_versions(version: str) -> None:
    qualified = identity.runtime_version(version, COMMIT, ASSEMBLY)
    assert qualified.startswith(version + ("." if "+" in version else "+") + "source.")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", True), ("source_commit", "short"), ("source_tree", "short"),
        ("assembly_hash", "short"), ("runtime_version", "dev"),
        ("package_version", "9.0.0"), ("plugin", "agent-other"),
    ],
)
def test_source_identity_refuses_mismatches(payload: Path, field: str, value: object) -> None:
    path = payload / identity.SOURCE_FILE
    document = json.loads(path.read_text())
    document[field] = value
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(identity.SourceIdentityError):
        identity.read_source_identity(payload, VERSION)


def test_source_identity_refuses_duplicate_fields(payload: Path) -> None:
    path = payload / identity.SOURCE_FILE
    path.write_text('{"schema":"one","schema":"two"}', encoding="utf-8")
    with pytest.raises(identity.SourceIdentityError, match="Duplicate"):
        identity.read_source_identity(payload, VERSION)


def test_source_identity_refuses_changed_payload_bytes(payload: Path) -> None:
    (payload / "changed.py").write_text("VALUE = 99\n", encoding="utf-8")
    with pytest.raises(identity.SourceIdentityError, match="payload bytes"):
        identity.read_source_identity(payload, VERSION)


def test_cell_provenance_is_separate_from_assembled_source_bytes(payload: Path) -> None:
    source = identity.read_source_identity(payload, VERSION)
    (payload / "snapshot-provenance.json").write_text(
        '{"schema":"copilot-extensions.snapshot-provenance","version":1}', encoding="utf-8",
    )
    assert identity.read_source_identity(payload, VERSION) == source
    (payload / "nested").mkdir()
    (payload / "nested" / "snapshot-provenance.json").write_text("runtime input", encoding="utf-8")
    with pytest.raises(identity.SourceIdentityError, match="payload bytes"):
        identity.read_source_identity(payload, VERSION)


def _source_slots(root: Path):
    runtime = identity._runtime()
    first = identity.runtime_version(VERSION, "f" * 40, "a" * 64)
    second = identity.runtime_version(VERSION + "+base", "a" * 40, "b" * 64)
    for version in ("0.1.0", first, second):
        python = root / "versions" / version / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        python.parent.mkdir(parents=True)
        python.write_bytes(b"fixture interpreter")
        python.chmod(0o700)
        runtime.mark_complete(root, version)
    return runtime, first, second


@pytest.mark.parametrize("selector", [None, "current-version", "last-known-good"])
def test_source_recovery_uses_explicit_identity_never_hash_order(
    tmp_path: Path, selector: str | None, capsys,
) -> None:
    runtime, first, _ = _source_slots(tmp_path)
    if selector:
        (tmp_path / selector).write_text(first, encoding="utf-8")
    python = runtime.resolve_python(tmp_path)
    if selector:
        assert python is not None and python.parent.parent == tmp_path / "versions" / first
    else:
        assert python is None
        assert "refusing unordered source fallback" in capsys.readouterr().err


@pytest.mark.parametrize("host", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("selector", [None, "current-version", "last-known-good"])
@pytest.mark.parametrize("owner", ["canonical", "agent-worktrees"])
def test_native_source_recovery_matches_explicit_python_selection(
    tmp_path: Path, host: str, selector: str | None, owner: str,
) -> None:
    executable = _bash() if host == "bash" else (
        shutil.which(host) if host != "powershell" or os.name == "nt" else None
    )
    if not executable:
        pytest.skip(f"{host} unavailable")
    _, first, _ = _source_slots(tmp_path)
    if selector:
        (tmp_path / selector).write_text(first, encoding="utf-8")
    scripts = LIBRARY if owner == "canonical" else (
        LIBRARY.parents[1] / "plugins" / owner / "scripts"
    )
    if host == "bash":
        command = (
            f"AGENT_RT_ROOT='{_posix(tmp_path)}'; "
            f". '{_posix(scripts / 'resolve-runtime.sh')}'; printf '%s' \"$AGENT_RT_PY\""
        )
        argv = [executable, "-c", command]
    else:
        root = str(tmp_path).replace("'", "''")
        resolver = str(scripts / "resolve-runtime.ps1").replace("'", "''")
        command = f"$env:AGENT_RT_ROOT='{root}'; . '{resolver}'; [Console]::Out.Write($AgentRtPy)"
        argv = [executable, "-NoProfile", "-Command", command]
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    if selector:
        selected = result.stdout.replace("\\", "/")
        suffix = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        assert f"/versions/{first}/{suffix}" in selected
    else:
        assert result.stdout == ""
        assert "refusing unordered source fallback" in result.stderr


def test_source_identity_cannot_select_another_installer_owner(payload: Path) -> None:
    manifest = payload / "plugin.json"
    manifest.write_text(
        json.dumps({"name": "agent-other", "version": VERSION}), encoding="utf-8",
    )
    digest = identity.assembly_hash(payload)
    source = identity.SourceIdentity(
        "agent-other", VERSION, COMMIT, TREE, digest,
        identity.runtime_version(VERSION, COMMIT, digest),
    )
    (payload / identity.SOURCE_FILE).write_text(json.dumps(source.document()), encoding="utf-8")
    assert identity.read_source_identity(payload, VERSION) == source
    with pytest.raises(identity.SourceIdentityError, match="plugin and package"):
        identity.read_source_identity(payload, VERSION, plugin="agent-bridge")


def test_assembly_hash_excludes_build_outputs_not_runtime_packages(payload: Path) -> None:
    (payload / "pyproject.toml").write_text(
        '[project]\nname="agent-bridge"\nversion="0.4.1-dev3"\n', encoding="utf-8",
    )
    before = identity.assembly_hash(payload)
    (payload / "build").mkdir()
    (payload / "build" / "derived.py").write_text("generated", encoding="utf-8")
    assert identity.assembly_hash(payload) == before
    package = payload / "src" / "example" / "build"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert identity.assembly_hash(payload) != before


@pytest.mark.parametrize("directory", [".pytest_cache", ".ruff_cache", ".mypy_cache", "__pycache__"])
def test_generated_analysis_caches_preserve_source_identity(payload: Path, directory: str) -> None:
    source = identity.read_source_identity(payload, VERSION)
    cache = payload / "src" / directory
    cache.mkdir(parents=True)
    (cache / "analysis.json").write_text('{"generated": true}', encoding="utf-8")
    assert identity.read_source_identity(payload, VERSION) == source


def test_absent_source_identity_keeps_existing_version(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert identity.main(["--payload", str(tmp_path), "--package-version", VERSION]) == 0
    assert capsys.readouterr().out.strip() == VERSION


def test_expected_selection_refuses_disappeared_receipt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert identity.main([
        "--payload", str(tmp_path), "--package-version", VERSION,
        "--expected-runtime-version", identity.runtime_version(VERSION, COMMIT, ASSEMBLY),
    ]) == 1
    assert "changed after runtime selection" in capsys.readouterr().err


def test_bash_source_initializer_uses_the_production_function(payload: Path, tmp_path: Path) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("native Bash is unavailable")
    script = LIBRARY.parents[1] / "plugins" / "agent-bridge" / "scripts" / "install.sh"
    command = "\n".join([
        "set -e",
        f"PLUGIN_DIR='{_posix(payload)}'",
        f"SCRIPT_DIR='{_posix(LIBRARY)}'",
        f"INSTALL_DIR='{_posix(tmp_path / 'runtime')}'",
        f"PACKAGE_VERSION='{VERSION}'",
        "SRC_VERSION=\"$PACKAGE_VERSION\"",
        f"_bootstrap_python() {{ printf '%s' '{_posix(Path(sys.executable))}'; }}",
        "_fail() { printf '%s\\n' \"$*\" >&2; }",
        _function(script, "_initialize_source_runtime"),
        "_initialize_source_runtime",
        "printf '%s\\n%s\\n' \"$SRC_VERSION\" \"$VENV_DIR\"",
    ])
    result = subprocess.run(
        [bash, "-c", command], capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    expected = identity.runtime_version(VERSION, COMMIT, identity.assembly_hash(payload))
    assert result.stdout.splitlines() == [
        expected, f"{_posix(tmp_path / 'runtime')}/versions/{expected}",
    ]


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_powershell_source_initializer_uses_the_production_function(
    payload: Path, tmp_path: Path, shell: str,
) -> None:
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is unavailable")
    owner = LIBRARY.parents[1] / "plugins" / "agent-bridge" / "scripts" / "install.ps1"
    fixture = tmp_path / "source-initializer.ps1"
    fixture.write_text("\n".join([
        "param([string]$Python, [string]$Payload, [string]$Root, [string]$Scripts)",
        "$ErrorActionPreference = 'Stop'",
        "$PluginDir = $Payload; $InstallDir = $Root; $ScriptDir = $Scripts",
        ". (Join-Path $ScriptDir 'versioned_runtime_native.ps1')",
        f"$PackageVersion = '{VERSION}'; $SrcVersion = $PackageVersion",
        "function Get-BootstrapPython { return $Python }",
        _function(owner, "Get-SourceRuntimeIdentity", powershell=True),
        _function(owner, "Initialize-SourceRuntime", powershell=True),
        "Initialize-SourceRuntime",
        "@{ version = $SrcVersion; python = $VenvPython } | ConvertTo-Json -Compress",
    ]), encoding="utf-8")
    result = subprocess.run(
        [executable, "-NoProfile", "-File", str(fixture), sys.executable,
         str(payload), str(tmp_path / "runtime"), str(LIBRARY)],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    expected = identity.runtime_version(VERSION, COMMIT, identity.assembly_hash(payload))
    assert data["version"] == expected
    assert expected in data["python"]


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_powershell_source_refusal_keeps_helper_diagnostic(
    payload: Path, tmp_path: Path, shell: str,
) -> None:
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is unavailable")
    (payload / "changed.py").write_text("VALUE = 2\n", encoding="utf-8")
    owner = LIBRARY.parents[1] / "plugins" / "agent-bridge" / "scripts" / "install.ps1"
    fixture = tmp_path / "source-diagnostic.ps1"
    fixture.write_text("\n".join([
        "param([string]$Python, [string]$Payload, [string]$Scripts)",
        "$ErrorActionPreference = 'Stop'",
        f"$PackageVersion = '{VERSION}'; $PluginDir = $Payload; $ScriptDir = $Scripts",
        ". (Join-Path $ScriptDir 'versioned_runtime_native.ps1')",
        "function Get-BootstrapPython { return $Python }",
        _function(owner, "Get-SourceRuntimeIdentity", powershell=True),
        _function(owner, "Initialize-SourceRuntime", powershell=True),
        "try { Initialize-SourceRuntime; exit 2 } catch {",
        "  if ($ErrorActionPreference -ne 'Stop') { exit 3 }",
        "  Write-Output $_.Exception.Message; exit 1",
        "}",
    ]), encoding="utf-8")
    result = subprocess.run(
        [executable, "-NoProfile", "-File", str(fixture), sys.executable,
         str(payload), str(LIBRARY)],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "exit 1" in result.stdout
    assert "Materialized payload bytes do not match the source identity" in result.stdout


def test_bash_manifest_keeps_package_version_and_binds_source_generation(
    payload: Path, tmp_path: Path,
) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("native Bash is unavailable")
    repository = LIBRARY.parents[1]
    owner = repository / "plugins" / "agent-bridge" / "scripts" / "install.sh"
    engine = repository / "libs" / "installer-engine" / "installer-engine.sh"
    root = tmp_path / "runtime"
    root.mkdir()
    command = "\n".join([
        "set -e",
        f"PLUGIN_DIR='{_posix(payload)}'; SCRIPT_DIR='{_posix(LIBRARY)}'",
        f"INSTALL_DIR='{_posix(root)}'; PACKAGE_VERSION='{VERSION}'; SRC_VERSION=\"$PACKAGE_VERSION\"",
        f"_bootstrap_python() {{ printf '%s' '{_posix(Path(sys.executable))}'; }}",
        "_fail() { printf '%s\\n' \"$*\" >&2; }",
        "_ok() { :; }",
        "_git_info() { printf 'unknown unknown false\\n'; }",
        "_payload_hash() { printf fingerprint; }",
        f"source '{_posix(engine)}'",
        _function(owner, "_initialize_source_runtime"),
        _function(owner, "_write_deploy_manifest"),
        "_initialize_source_runtime",
        "_write_deploy_manifest",
    ])
    result = subprocess.run(
        [bash, "-c", command], capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
    assert data["source"]["version"] == VERSION
    assert data["runtime_version"] == identity.runtime_version(
        VERSION, COMMIT, identity.assembly_hash(payload),
    )
    assert data["runtime_source"]["source_commit"] == COMMIT


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_powershell_manifest_keeps_package_version_and_binds_source_generation(
    payload: Path, tmp_path: Path, shell: str,
) -> None:
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is unavailable")
    repository = LIBRARY.parents[1]
    owner = repository / "plugins" / "agent-bridge" / "scripts" / "install.ps1"
    engine = repository / "libs" / "installer-engine" / "installer-engine.ps1"
    root = tmp_path / "runtime"
    root.mkdir()
    fixture = tmp_path / "source-manifest.ps1"
    fixture.write_text("\n".join([
        "param([string]$Python, [string]$Payload, [string]$Root, [string]$Scripts)",
        "$ErrorActionPreference = 'Stop'",
        "$PluginDir = $Payload; $InstallDir = $Root; $ScriptDir = $Scripts",
        ". (Join-Path $ScriptDir 'versioned_runtime_native.ps1')",
        f"$PackageVersion = '{VERSION}'; $SrcVersion = $PackageVersion",
        "function Get-BootstrapPython { return $Python }",
        "function Get-SourceKind { return 'local' }",
        "function Get-GitInfo { return @{ commit = $null; branch = $null; dirty = $false } }",
        "function Write-Ok { }",
        _function(engine, "Write-DeployManifest", powershell=True),
        "Set-Item Function:\\Write-DeployManifestShared ${function:Write-DeployManifest}",
        _function(owner, "Get-SourceRuntimeIdentity", powershell=True),
        _function(owner, "Initialize-SourceRuntime", powershell=True),
        _function(owner, "Write-DeployManifest", powershell=True),
        "Initialize-SourceRuntime",
        "Write-DeployManifest -PayloadHash 'fingerprint'",
    ]), encoding="utf-8")
    result = subprocess.run(
        [executable, "-NoProfile", "-File", str(fixture), sys.executable,
         str(payload), str(root), str(LIBRARY)],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert data["source"]["version"] == VERSION
    assert data["runtime_version"] == identity.runtime_version(
        VERSION, COMMIT, identity.assembly_hash(payload),
    )
    assert data["runtime_source"]["source_commit"] == COMMIT


def test_clean_room_source_scenario_applies_the_supplied_uv_index(tmp_path: Path) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("native Bash is unavailable")
    library = tmp_path / "clean-room-lib.sh"
    library.write_text(
        "cr_init() { :; }\n"
        "phase() { printf '%s\\n' \"$UV_INDEX_URL\" \"$UV_DEFAULT_INDEX\" "
        "\"$UV_EXTRA_INDEX_URL\"; exit 0; }\n",
        encoding="utf-8",
    )
    scenario = (
        LIBRARY.parents[1] / "tools" / "clean-room" / "scenarios"
        / "merged-dev-runtime" / "scenario.sh"
    )
    index = "https://packages.example.com/simple"
    environment = {
        **os.environ, "CR_LIB": _posix(library), "CR_UV_INDEX": index,
        "UV_INDEX_URL": "https://unselected.example.com/simple",
        "UV_DEFAULT_INDEX": "https://unselected.example.com/simple",
        "UV_EXTRA_INDEX_URL": "https://unselected.example.com/simple",
    }
    result = subprocess.run(
        [bash, _posix(scenario)], env=environment, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [index, index, index]
