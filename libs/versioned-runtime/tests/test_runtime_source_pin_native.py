"""A transaction pin must not become a package-only native installation."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_runtime_source_identity import LIBRARY, _bash, _function, _posix, identity
from test_runtime_source_native import _quote, _run

REPOSITORY = LIBRARY.parents[1]
PLUGINS = (
    "agent-worktrees", "agent-bridge", "agent-codespaces", "agent-containers",
    "agent-dispatch", "agent-index", "agent-logger", "agent-machines", "agent-mcp",
    "agent-pull-requests", "agent-ssh", "agent-vault", "budget-guidance",
)


def _adapter(plugin, suffix):
    scripts = REPOSITORY / "plugins" / plugin / "scripts"
    source = scripts / f"source-runtime.{suffix}"
    if source.is_file():
        return source
    return scripts / f'{"init" if plugin in {"agent-containers", "agent-machines"} else "install"}.{suffix}'


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("receipt_mode", ["lost", "retained", "ordinary"])
def test_native_initialization_preserves_required_source_selection(
    tmp_path, plugin, host, receipt_mode,
):
    payload = tmp_path / "payload"
    payload.mkdir()
    version = "1.0.0"
    (payload / "plugin.json").write_text(json.dumps({"name": plugin, "version": version}))
    digest = identity.assembly_hash(payload)
    selected = identity.SourceIdentity(
        plugin, version, "a" * 40, "b" * 40, digest,
        identity.runtime_version(version, "a" * 40, digest),
    )
    receipt = payload / "runtime-source.json"
    receipt.write_text(json.dumps(selected.document()))
    assert identity.read_source_identity(payload, version, plugin=plugin) == selected
    pin = json.dumps(identity.manifest_fields(selected), sort_keys=True)
    if receipt_mode != "retained":
        receipt.unlink()
    environment = dict(os.environ)
    environment.pop(identity.SOURCE_PIN_ENV, None)
    if receipt_mode != "ordinary":
        environment[identity.SOURCE_PIN_ENV] = pin
    root = tmp_path / "must-not-create"
    if host == "bash":
        executable = _bash()
        if not executable:
            pytest.skip("bash unavailable")
        adapter = _adapter(plugin, "sh")
        text = adapter.read_text(encoding="utf-8")
        functions = (
            _function(adapter, "_source_runtime_identity")
            if "\n_source_runtime_identity() {" in "\n" + text else ""
        )
        if plugin == "agent-containers":
            functions = _function(adapter, "_source_runtime_version")
            initialize = text[text.index("\nif [[", text.index("_source_runtime_version()")) + 1:
                              text.index("\n_assert_source_runtime_identity()")]
        else:
            initialize = _function(adapter, "_initialize_source_runtime") + "\n_initialize_source_runtime"
        script = (
            "set -e\n"
            f"PLUGIN_DIR='{_posix(payload)}'\nSCRIPT_DIR='{_posix(LIBRARY)}'\n"
            f"INSTALL_DIR='{_posix(root)}'\nSRC_VERSION='{version}'\nPACKAGE_VERSION='{version}'\n"
            f"_bootstrap_python() {{ printf '%s\\n' '{_posix(Path(sys.executable))}'; }}\n"
            "_source_python() { _bootstrap_python; }\n"
            f"{functions}\n{initialize}\n"
            "printf 'NATIVE_INITIALIZATION_ACCEPTED:%s\\n' \"$SRC_VERSION\"\n"
        )
        result = subprocess.run(
            [executable, "-c", script], capture_output=True, text=True, env=environment, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    else:
        adapter = _adapter(plugin, "ps1")
        text = adapter.read_text(encoding="utf-8")
        if plugin == "agent-containers":
            functions = _function(adapter, "Get-SourceRuntimeVersion", powershell=True)
            initialize = text[text.index("$sourceMetadata ="):
                              text.index("\nfunction Assert-SourceRuntimeIdentity")]
        else:
            functions = _function(adapter, "Get-SourceRuntimeIdentity", powershell=True)
            initialize = _function(adapter, "Initialize-SourceRuntime", powershell=True) + "\nInitialize-SourceRuntime"
        script = (
            "trap { [Console]::Error.Write($_.Exception.Message); exit 1 }\n"
            f"$PluginDir = {_quote(payload)}\n$ScriptDir = {_quote(LIBRARY)}\n"
            f"$InstallDir = {_quote(root)}\n$SrcVersion = '{version}'\n$PackageVersion = '{version}'\n"
            f"function Get-BootstrapPython {{ return {_quote(sys.executable)} }}\n"
            "function Get-SourceRuntimePython { Get-BootstrapPython }\n"
            "$SourceSelected = $false\n"
            f"{functions.replace('$PSScriptRoot', '$ScriptDir')}\n{initialize}\n"
            "[Console]::Out.Write('NATIVE_INITIALIZATION_ACCEPTED:' + $SrcVersion)\n"
        )
        result = _run(host, script, tmp_path, env=environment)
    if receipt_mode == "lost":
        assert result.returncode != 0, result.stdout + result.stderr
        assert "update transaction selection" in result.stdout + result.stderr
        assert "NATIVE_INITIALIZATION_ACCEPTED" not in result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        expected = selected.runtime_version if receipt_mode == "retained" else version
        assert result.stdout.strip() == f"NATIVE_INITIALIZATION_ACCEPTED:{expected}"
    assert not root.exists()
