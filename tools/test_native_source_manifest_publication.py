"""Native source publishers must not replace a manifest after SDK refusal."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _publication_body(plugin: str, suffix: str) -> str:
    installer = "init" if plugin in {"agent-machines", "agent-mcp", "agent-containers"} else "install"
    text = (ROOT / "plugins" / plugin / "scripts" / f"{installer}.{suffix}").read_text(
        encoding="utf-8",
    )
    if plugin in {"agent-machines", "agent-mcp"}:
        start = text.index("# -- 5.")
        return text[start:text.index("# -- 6.", start)]
    if plugin == "agent-containers":
        start = text.index("# -- 5. Write deploy manifest")
        return text[start:text.index("# -- Machine-local", start)]
    if plugin == "budget-guidance":
        start = text.index("$manifest = [ordered]@{")
        return text[start:text.index("\n$prevEAP =", start)]
    marker = "$sourcePublication =" if suffix == "ps1" else 'SOURCE_FIELDS=""'
    end = "\n$prevEAP =" if suffix == "ps1" else "\necho ''"
    start = text.index(marker)
    return text[start:text.index(end, start)]


@pytest.mark.skipif(os.name != "nt", reason="Windows native manifest producers")
@pytest.mark.parametrize("plugin", ["agent-containers", "budget-guidance"])
def test_native_wheel_manifest_publishes_completed_construction_hash(tmp_path, monkeypatch, plugin):
    monkeypatch.syspath_prepend(str(ROOT / "libs" / "versioned-runtime"))
    monkeypatch.syspath_prepend(str(ROOT / "libs" / "installer-engine"))
    import install_source

    shell = shutil.which("powershell.exe")
    assert shell
    payload, root = tmp_path / "payload", tmp_path / "runtime"
    payload.mkdir()
    (payload / "pyproject.toml").write_text(
        f'[project]\nname="{plugin}"\nversion="1.2.3"\n', encoding="utf-8",
    )
    row = {
        "name": plugin, "version": "1.2.3", "filename": plugin.replace("-", "_") + "-1.2.3-py3-none-any.whl",
        "sha256": "sha256:" + "a" * 64, "selected_extras": [],
    }
    binding = {"wheels": [row]}
    lock = "sha256:" + hashlib.sha256(json.dumps(
        binding, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    generation, fingerprint = "1.2.3.profile." + lock[7:23], "b" * 64
    slot = root / "versions" / generation
    python = slot / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    receipt = {
        "construction": "closed-wheels", "lock_id": lock, "binding": binding,
        "distributions": {plugin: {key: row[key] for key in ("version", "filename", "sha256")}},
    }
    (slot / ".runtime-input-origins.json").write_text(json.dumps({
        "schema": "installer-engine.frozen-input-origins", "version": 2,
        "wheel_inputs": receipt, "local_packages": [plugin],
    }))
    install_source.engine.runtime.mark_complete(root, generation, payload_hash=fingerprint)
    (root / "current-version").write_text(generation)

    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    harness = tmp_path / "manifest.ps1"
    harness.write_text(
        "$ErrorActionPreference='Stop'; $env:COMPUTERNAME='fixture'\n"
        "function Write-Ok { param($Message) }\n"
        "function Get-SourceKind { param($PluginPath) 'marketplace' }\n"
        "function Test-SourceRuntimeUnchanged {}\n"
        f"$InstallDir={quote(root)}; $PluginDir={quote(payload)}; $LinkDir={quote(slot)}\n"
        f"$ManifestPath={quote(root / 'deploy-manifest.json')}; $SrcVersion={quote(generation)}\n"
        "$kind='marketplace'; $ver='1.2.3'; $commit=$null; $branch=$null; $dirty=$false\n"
        f"$NativeWheelSelection=@{{payload_hash={quote(fingerprint)}}}\n"
        "$WheelProfile='selected.json'; $SourceSelected=$false; $SourceRuntimeIdentity=$null\n"
        f"$profileResult=[pscustomobject]@{{runtimeVersion={quote(generation)}; profile='base'; "
        f"runtimeSource=$null; payloadHash={quote(fingerprint)}}}\n"
        + _publication_body(plugin, "ps1"),
        encoding="utf-8",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["source"]["content_hash"] == fingerprint
    assert install_source.native_current(root, payload, plugin)


@pytest.mark.parametrize("plugin", ["agent-ssh", "agent-machines", "agent-mcp"])
@pytest.mark.parametrize("suffix", ["ps1", "sh"])
def test_sdk_refusal_preserves_native_source_manifest(
    tmp_path: Path, plugin: str, suffix: str,
) -> None:
    if suffix == "sh" and os.name == "nt":
        pytest.skip("native POSIX publication")
    shell = shutil.which("pwsh") if suffix == "ps1" else shutil.which("bash")
    if shell is None:
        pytest.skip("native shell unavailable")
    owner = tmp_path / "home/.copilot/installed-plugins/example" / plugin
    scripts = owner / "scripts"
    scripts.mkdir(parents=True)
    root = tmp_path / "runtime"
    root.mkdir()
    manifest = root / "deploy-manifest.json"
    previous = b'{"retained":"previous"}\n'
    manifest.write_bytes(previous)
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    capture = tmp_path / "sdk-call.json"
    (scripts / "command_binding.py").write_text(
        "import json,os,pathlib,sys\n"
        "pathlib.Path(os.environ['SOURCE_MANIFEST_CAPTURE']).write_text(json.dumps({\n"
        "    'argv':sys.argv[1:], 'manifest':json.load(sys.stdin),\n"
        "}),encoding='utf-8')\n"
        "print('injected ownership publication refusal',file=sys.stderr)\n"
        "raise SystemExit(17)\n",
        encoding="utf-8",
    )
    identity = {
        "schema": "copilot-extensions.runtime-source", "version": 1, "plugin": plugin,
        "package_version": "1.2.3", "source_commit": "a" * 40, "source_tree": "b" * 40,
        "assembly_hash": "c" * 64, "runtime_version": "1.2.3+source.fixture",
    }
    fields = {"runtime_source": identity, "runtime_version": identity["runtime_version"]}
    environment = dict(os.environ)
    environment.update(
        COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN=json.dumps(fields),
        COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT=str(tmp_path),
        SOURCE_MANIFEST_CAPTURE=str(capture),
    )
    harness = tmp_path / f"publication.{suffix}"
    if suffix == "ps1":
        def quote(value: Path | str) -> str:
            return "'" + str(value).replace("'", "''") + "'"

        harness.write_text(
            "$ErrorActionPreference='Stop'\n"
            "trap { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }\n"
            f". {quote(ROOT / 'libs/versioned-runtime/versioned_runtime_native.ps1')}\n"
            f". {quote(ROOT / 'libs/installer-engine/installer-engine.ps1')}\n"
            "function Write-Ok { param($Message) Write-Output \"SUCCESS: $Message\" }\n"
            "function Get-SourceKind { param($PluginPath) return 'marketplace' }\n"
            "function Get-GitInfo { param($Path) return @{commit='abc';branch='dev';dirty=$false} }\n"
            "function Get-PayloadHash { return 'fixture-hash' }\n"
            f"function Get-BootstrapPython {{ return {quote(sys.executable)} }}\n"
            f"$InstallDir={quote(root)}; $PluginDir={quote(prepared)}\n"
            f"$LinkDir={quote(root / 'slot')}; $sourcePath={quote(owner)}\n"
            f"$pythonCmd={quote(sys.executable)}; $SourceRuntimeIdentity={quote(json.dumps(fields))}\n"
            f"$script:SourceManifestPath={quote(owner)}\n"
            f"$sourceManifestPath={quote(owner)}\n"
            "$CellMode=$false; $PackageVersion='1.2.3'; $sourceManifestHash='fixture-hash'\n"
            "$script:SourceManifestGit=@{commit='abc';branch='dev';dirty=$false}\n"
            + _publication_body(plugin, suffix),
            encoding="utf-8",
        )
        command = [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)]
    else:
        harness.write_text(
            "set -euo pipefail\n"
            f". {shlex.quote(str(ROOT / 'libs/installer-engine/installer-engine.sh'))}\n"
            "_ok() { printf 'SUCCESS: %s\\n' \"$*\"; }\n"
            "_fail() { printf 'ERROR: %s\\n' \"$*\" >&2; }\n"
            "_source_kind() { printf marketplace; }\n"
            "_git_info() { printf 'abc dev false\\n'; }\n"
            f"_source_python() {{ printf '%s\\n' {shlex.quote(sys.executable)}; }}\n"
            f"INSTALL_DIR={shlex.quote(str(root))}; PLUGIN_DIR={shlex.quote(str(prepared))}\n"
            f"SOURCE_PATH={shlex.quote(str(owner))}; SOURCE_OWNER={shlex.quote(str(owner))}\n"
            f"SOURCE_MANIFEST_PATH={shlex.quote(str(owner))}\n"
            f"VENV_DIR={shlex.quote(str(root / 'slot'))}; VENV_PYTHON={shlex.quote(sys.executable)}\n"
            f"SOURCE_RUNTIME_IDENTITY={shlex.quote(json.dumps(fields))}\n"
            "CELL_MODE=0; PACKAGE_VERSION=1.2.3\n"
            "SOURCE_MANIFEST_HASH=fixture-hash; SOURCE_MANIFEST_GIT='abc dev false'\n"
            + _publication_body(plugin, suffix),
            encoding="utf-8",
        )
        command = [shell, str(harness)]
    result = subprocess.run(
        command, cwd=tmp_path, env=environment, capture_output=True, text=True,
        encoding="utf-8", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "injected ownership publication refusal" in result.stderr
    assert "SUCCESS:" not in result.stdout
    assert manifest.read_bytes() == previous
    assert list(root.iterdir()) == [manifest]
    recorded = json.loads(capture.read_text(encoding="utf-8"))
    arguments = recorded["argv"]
    assert arguments[arguments.index("--payload-root") + 1] == str(owner)
    assert arguments[arguments.index("--source-payload-root") + 1] == str(owner)
    assert recorded["manifest"]["source"]["path"] == owner.as_posix()
    assert recorded["manifest"]["runtime_source"] == identity
