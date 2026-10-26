"""Real selected-runtime bootstrap controls; no installer runs outside the fixture."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

LIBRARY = Path(__file__).resolve().parents[1]
REPO = LIBRARY.parents[1]
sys.path.insert(0, str(LIBRARY))
import versioned_runtime_source as source

PLUGINS = ("agent-bridge", "agent-dispatch", "agent-mcp")
VERSION = "1.2.3"
START = "# --- selected completed runtime readiness (reference: agent-bridge) ---"
END = "# --- end selected completed runtime readiness ---"
NATIVE = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="native POSIX shell required; Windows/WSL are not qualification",
)


def _fixture(tmp_path, plugin):
    home = tmp_path / "home"
    payload = (
        home / ".copilot/installed-plugins/fixture/agent-bridge"
        if plugin == "agent-bridge" else tmp_path / "payload"
    )
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for path in LIBRARY.iterdir():
        if path.suffix in (".py", ".sh"):
            shutil.copyfile(path, scripts / path.name)
    shutil.copyfile(
        REPO / "plugins" / plugin / "scripts" / "bootstrap-check.sh",
        scripts / "bootstrap-check.sh",
    )
    (payload / "plugin.json").write_text(
        json.dumps({"name": plugin, "version": VERSION}), encoding="utf-8",
    )
    (payload / "pyproject.toml").write_text(f'version = "{VERSION}"\n', encoding="utf-8")
    (scripts / "install.sh").write_text(
        '#!/usr/bin/env bash\ncase "${1:-}" in\n'
        'stamp) printf "stamp\\n" >> "$HOME/children" ;;\n'
        '*) printf "reconcile\\n" >> "$HOME/children" ;;\nesac\n',
        encoding="utf-8",
    )
    if plugin == "agent-bridge":
        query = scripts / "installation-context/json-query.awk"
        query.parent.mkdir()
        shutil.copyfile(REPO / "libs/installation-context/json-query.awk", query)
        driver = payload / "libs/update-driver/scripts/first-use.sh"
        driver.parent.mkdir(parents=True)
        driver.write_text(
            "# Installation driver lifecycle contract: bridge-install-v1\n"
            '[[ ! -e "$3/runtime-deployment.json" ]] || exit 3\n'
            'action=reconcile; [[ "$6" != stamp ]] || action=stamp\n'
            'printf "%s\\n" "$action" >>"$HOME/children"\n'
            'printf "launched:123 log:%s/first-use-00000000-0000-0000-0000-000000000000.log\\n" "$1"\n',
            encoding="utf-8",
        )
    root = home / f".{plugin}"
    root.mkdir(parents=True)
    _manifest(root)
    return payload, home, root


def _manifest(root, **fields):
    data = {"source": {"version": VERSION}, **fields}
    (root / "deploy-manifest.json").write_text(json.dumps(data), encoding="utf-8")


def _source_payload(payload):
    digest = source.assembly_hash(payload)
    identity = source.SourceIdentity(
        json.loads((payload / "plugin.json").read_text())["name"],
        VERSION, "a" * 40, "b" * 40, digest,
        source.runtime_version(VERSION, "a" * 40, digest),
    )
    (payload / "runtime-source.json").write_text(
        json.dumps(identity.document()), encoding="utf-8",
    )
    return identity


def _slot(root, version, *, complete=True):
    slot = root / "versions" / version
    (slot / "bin").mkdir(parents=True)
    (slot / "bin" / "python").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (slot / "bin" / "python").chmod(0o755)
    if complete:
        (slot / ".install-complete.json").write_text(
            json.dumps({"version": version, "completed_at": "2026-01-01", "pid": 0}),
            encoding="utf-8",
        )
    return slot


def _environment(home):
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_RT_", "COPILOT_EXTENSIONS_", "COPILOT_PLUGIN_"))
    }
    env["HOME"] = str(home)
    return env


def _hook(payload, home):
    started = time.monotonic()
    result = subprocess.run(
        ["bash", str(payload / "scripts" / "bootstrap-check.sh")],
        env=_environment(home), cwd=home, capture_output=True, text=True, timeout=30,
    )
    assert result.stdout == "{}", result
    return result, time.monotonic() - started


def _children(home):
    path = home / "children"
    return path.read_text().splitlines() if path.exists() else []


def _wait_for_child(home):
    for _ in range(50):
        if _children(home):
            return
        time.sleep(0.1)
    pytest.fail("expected fixture installer child did not run")


@pytest.mark.parametrize("plugin", PLUGINS)
def test_readiness_reference_is_synchronized(plugin):
    reference = (REPO / "plugins" / PLUGINS[0] / "scripts" / "bootstrap-check.sh").read_text()
    actual = (REPO / "plugins" / plugin / "scripts" / "bootstrap-check.sh").read_text()
    block = actual.split(START)[1].split(END)[0]
    assert block == reference.split(START)[1].split(END)[0]
    assert "AGENT_RT_RESOLVE_TRACE_MODE=read-only" in block
    assert '. "$ScriptDir/resolve-runtime.sh"' in block
    assert "AGENT_RT_PY" in block


@pytest.mark.parametrize("case", [
    "ordinary", "source", "changed-source", "changed-bytes", "wrong-generation",
    "missing-source-receipt",
])
def test_actual_identity_probe_on_host(tmp_path, case):
    payload, _, root = _fixture(tmp_path, "agent-bridge")
    expected = VERSION
    if case != "ordinary":
        identity = _source_payload(payload)
        fields = source.manifest_fields(identity)
        expected = identity.runtime_version
        if case == "changed-source":
            fields["runtime_source"] = {**identity.document(), "source_tree": "c" * 40}
        if case == "wrong-generation":
            fields["runtime_version"] = VERSION
        _manifest(root, **fields)
        if case == "missing-source-receipt":
            (payload / "runtime-source.json").unlink()
        if case == "changed-bytes":
            (payload / "changed.txt").write_text("changed payload", encoding="utf-8")
    hook = (payload / "scripts" / "bootstrap-check.sh").read_text()
    code = hook.split("<<'EXPECTED_SLOT'\n", 1)[1].split("\nEXPECTED_SLOT", 1)[0]
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-", str(payload / "scripts"), str(payload),
         str(root / "deploy-manifest.json"), VERSION, "agent-bridge"],
        input=code, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if case in ("ordinary", "source"):
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected
    else:
        assert result.returncode != 0
        assert result.stdout == ""


@NATIVE
@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("case", [
    "complete", "current-with-fallback", "source", "repair", "legacy",
    "wrong-selected", "wrong-selected-generation", "fallback",
    "missing-selected", "incomplete", "corrupt", "wrong-completion-version",
    "missing-interpreter", "changed-version", "changed-source", "changed-bytes",
    "wrong-generation", "legacy-broken-selection",
])
def test_actual_hook_selected_readiness(tmp_path, plugin, case):
    payload, home, root = _fixture(tmp_path, plugin)
    selected = VERSION
    if case in ("source", "changed-source", "changed-bytes", "wrong-generation"):
        identity = _source_payload(payload)
        selected = identity.runtime_version
        fields = source.manifest_fields(identity)
        if case == "changed-source":
            fields["runtime_source"] = {**identity.document(), "source_tree": "c" * 40}
        if case == "wrong-generation":
            fields["runtime_version"] = VERSION
        _manifest(root, **fields)
        if case == "changed-bytes":
            (payload / "changed.txt").write_text("changed payload", encoding="utf-8")
    if case == "repair":
        selected = VERSION + "+repair." + "d" * 32
        _manifest(root, runtime_version=selected)
    slot = _slot(root, selected, complete=case not in ("incomplete", "fallback"))
    (root / "current-version").write_text(selected, encoding="utf-8")
    if case in ("wrong-selected", "legacy-broken-selection"):
        _slot(root, "1.2.2")
        (root / "current-version").write_text("1.2.2", encoding="utf-8")
    if case == "wrong-selected-generation":
        other = VERSION + "+repair." + "e" * 32
        _slot(root, other)
        (root / "current-version").write_text(other, encoding="utf-8")
    if case == "current-with-fallback":
        _slot(root, "1.2.2")
        _slot(root, "1.2.4")
        (root / "last-known-good").write_text("1.2.2", encoding="utf-8")
    if case == "missing-selected":
        (root / "current-version").unlink()
    if case == "fallback":
        _slot(root, "1.2.2")
        (root / "last-known-good").write_text("1.2.2", encoding="utf-8")
    if case == "corrupt":
        (slot / ".install-complete.json").write_text("{", encoding="utf-8")
    if case == "wrong-completion-version":
        (slot / ".install-complete.json").write_text(
            '{"version":"1.2.2","completed_at":"now","pid":0}', encoding="utf-8",
        )
    if case == "missing-interpreter":
        (slot / "bin" / "python").unlink()
    if case == "changed-version":
        _manifest(root, source={"version": "1.2.2"})
    if case in ("legacy", "legacy-broken-selection"):
        (root / "venv").mkdir()
        if case == "legacy":
            (root / "current-version").unlink()
    result, elapsed = _hook(payload, home)
    assert result.returncode == 0, result.stderr
    ready = case in ("complete", "current-with-fallback", "source", "repair", "legacy")
    if ready:
        assert _children(home) == []
        assert not (root / "reconcile.lock").exists()
        assert not (root / "reconcile-status.json").exists()
    else:
        _wait_for_child(home)
        assert _children(home) == ["reconcile"]
    if case not in ("legacy", "legacy-broken-selection"):
        assert not (root / "venv").exists()
    assert not (root / ".venv").exists()
    assert not (root / "logs").exists(), "readiness must not persist resolver traces"
    print(f"{plugin}/{case}: installer_children={len(_children(home))}, elapsed={elapsed:.3f}s")


@NATIVE
@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("case", ["missing", "refused", "refused-first", "context"])
def test_existing_bootstrap_admission_and_output(tmp_path, plugin, case):
    payload, home, root = _fixture(tmp_path, plugin)
    if case in ("missing", "refused-first"):
        (root / "deploy-manifest.json").unlink()
    if case in ("refused", "refused-first"):
        (root / "runtime-deployment.json").write_text("{}", encoding="utf-8")
    if case == "context" and plugin == "agent-bridge":
        pytest.skip("bridge context admission belongs to its outer hook adapter")
    if case == "context":
        env = _environment(home)
        env["COPILOT_EXTENSIONS_CONTEXT"] = "selected-cell"
        result = subprocess.run(
            ["bash", str(payload / "scripts" / "bootstrap-check.sh")],
            env=env, cwd=home, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0 and result.stdout == "{}"
    else:
        result, _ = _hook(payload, home)
        assert result.returncode == (3 if case.startswith("refused") else 0)
    if case == "missing":
        assert _children(home) == ["stamp"]
    else:
        assert _children(home) == []


@NATIVE
def test_marker_only_before_after_installer_count(tmp_path, capsys):
    payload, home, root = _fixture(tmp_path, "agent-bridge")
    _slot(root, VERSION)
    (root / "current-version").write_text(VERSION, encoding="utf-8")
    hook = payload / "scripts" / "bootstrap-check.sh"
    candidate = hook.read_text()
    before, remainder = candidate.split(START, 1)
    _, after = remainder.split(END, 1)
    hook.write_text(
        before
        + 'if { [ -e "$InstallDir/.venv" ] || [ -e "$InstallDir/venv" ]; } && '
        + '[ "$deployed" = "$current" ]; then exit 0; fi\n'
        + after,
        encoding="utf-8",
    )
    original, before_elapsed = _hook(payload, home)
    assert original.returncode == 0, original.stderr
    _wait_for_child(home)
    status = root / "reconcile-status.json"
    assert not status.exists(), "the fixture driver does not own legacy status publication"
    assert _children(home) == ["reconcile"]
    (home / "children").unlink()
    hook.write_text(candidate, encoding="utf-8")
    result, after_elapsed = _hook(payload, home)
    assert result.returncode == 0, result.stderr
    assert _children(home) == []
    assert not status.exists()
    with capsys.disabled():
        print(
            f"marker-only baseline: installer_children=1, elapsed={before_elapsed:.3f}s; "
            f"candidate: installer_children=0, elapsed={after_elapsed:.3f}s"
        )


def test_sync_tool_is_idempotent_and_detects_readiness_drift(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("bootstrap_sync", REPO / "tools" / "check-bootstrap-sync.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    plugins = tmp_path / "plugins"
    for name in PLUGINS:
        destination = plugins / name / "scripts"
        destination.mkdir(parents=True)
        shutil.copyfile(REPO / "plugins" / name / "scripts" / "bootstrap-check.sh", destination / "bootstrap-check.sh")
    monkeypatch.setattr(tool, "PLUGINS_DIR", plugins)
    target = plugins / "agent-mcp" / "scripts" / "bootstrap-check.sh"
    target.write_text(target.read_text().replace("provisioned=0", "provisioned=1", 1))
    assert tool._readiness(target) != tool._readiness(plugins / "agent-bridge" / "scripts" / "bootstrap-check.sh")
    tool.sync_readiness()
    expected = target.read_bytes()
    tool.sync_readiness()
    assert target.read_bytes() == expected
