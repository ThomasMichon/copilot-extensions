from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import runtime_deployment as deployment

REPO = Path(__file__).resolve().parents[3]


def test_runtime_drift_description_preserves_build_and_legacy_evidence():
    from agent_worktrees.runtime_identity import drift_description

    assert drift_description({
        "service": "example", "running": "1.0.0", "payload": "2.0.0",
    }) == "      - example: running 1.0.0 but 2.0.0 installed -> example service restart"
    assert drift_description({
        "service": "example", "running_generation": "original",
        "installed_generation": "candidate", "running_slot": "dev",
        "installed_slot": "1.0.0",
    }) == (
        "      - example: running build original (dev) but candidate (1.0.0) installed -> "
        "example service restart"
    )


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "plugins" / "agent-worktrees" / "scripts"))
    monkeypatch.syspath_prepend(str(REPO / "libs" / "installer-engine"))
    spec = importlib.util.spec_from_file_location(
        "generation_installer_engine", REPO / "libs" / "installer-engine" / "installer_engine.py",
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", ["1.0.0-dev7", "1.0.0.dev7", "1.0.0", "1.0.0+local"])
def test_repair_identity_cannot_outrank_later_package_release(engine, version):
    separator = "." if "+" in version else "+"
    repair = f"{version}{separator}repair.{'a' * 32}"
    assert engine.runtime.package_version(repair) == version
    assert engine.runtime._version_key(version) < engine.runtime._version_key(repair)
    assert engine.runtime._version_key(repair) < engine.runtime._version_key("1.0.1-dev1")
    assert engine.runtime.package_version("1.0.0+repair.not-a-generation") == (
        "1.0.0+repair.not-a-generation"
    )


@pytest.mark.parametrize("selected", [True, False])
@pytest.mark.parametrize("healthy", [True, False])
def test_ordinary_generation_discovery_uses_frozen_content_and_retains_repair(
    engine, tmp_path, monkeypatch, selected, healthy,
):
    root, plugin = tmp_path / "runtime", tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "pyproject.toml").write_text(
        '[project]\nname="example"\nversion="1.0.0"\n', encoding="utf-8",
    )
    (plugin / "source.py").write_text("VALUE=1\n", encoding="utf-8")
    frozen = engine.freeze_inputs(plugin, tmp_path / "inputs")
    original = root / "versions" / "1.0.0"
    original.mkdir(parents=True)
    evidence = original / ".install-complete.json"
    evidence.write_bytes(b"broken published evidence")
    repair = f"1.0.0+repair.{'a' * 32}"
    (root / "versions" / repair).mkdir()
    engine.runtime.mark_complete(root, repair, payload_hash=frozen.payload_hash)
    if selected:
        engine.runtime.activate(root, repair, link_free=True)
    marker = engine.runtime.marker_path(root, repair).read_bytes()
    checks = []

    def verify(_inputs, slot, _python, _module, _descriptor, _timeout, **kwargs):
        assert slot.name == repair and kwargs["require_frozen_origin"] is False
        checks.append(slot.name)
        with pytest.raises(engine.runtime.RetentionTimeout):
            with engine.runtime._kernel_lease(
                root, repair, kind="retention", shared=False, timeout=0,
            ):
                pytest.fail("repair health ran without retained custody")
        if not healthy:
            raise engine.HealthRepairRequired("repair failed installed health")

    monkeypatch.setattr(engine, "_verify_installed", verify)
    monkeypatch.setattr(
        engine, "_construct",
        lambda *_args, **_kwargs: pytest.fail("ordinary reuse rebuilt a published runtime"),
    )
    arguments = dict(
        plugin=plugin, uv=tmp_path / "uv", base_python=Path(sys.executable),
        health_module="example", reuse_generations=True,
    )
    if healthy:
        with engine.numbered_candidate(root, "1.0.0", **arguments) as candidate:
            assert candidate.slot.name == repair and candidate.reused
    else:
        with pytest.raises(engine.HealthRepairRequired, match="installed health"):
            with engine.numbered_candidate(root, "1.0.0", **arguments):
                pytest.fail("failed repair was reused")
    assert checks == [repair]
    assert evidence.read_bytes() == b"broken published evidence"
    assert engine.runtime.marker_path(root, repair).read_bytes() == marker
    (plugin / "source.py").write_text("VALUE=2\n", encoding="utf-8")
    with pytest.raises(engine.HealthRepairRequired, match="distinct-generation"):
        with engine.numbered_candidate(root, "1.0.0", **arguments):
            pytest.fail("different frozen content selected the repair")
    assert checks == [repair]


@pytest.mark.parametrize("resolver_kind", ["canonical", "worktrees"])
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe", "bash"])
@pytest.mark.parametrize("development,preferred", [
    ("1.0.0-dev9", "1.0.0-dev10"),
    ("1.0.0.dev99", "1.0.0"),
])
def test_native_first_run_prefers_later_release_over_older_repair(
    tmp_path, resolver_kind, shell, development, preferred,
):
    directory = (
        REPO / "libs" / "versioned-runtime" if resolver_kind == "canonical"
        else REPO / "plugins" / "agent-worktrees" / "scripts"
    )
    root = tmp_path / "runtime"
    repair = f"{development}+repair.{'f' * 32}"
    for version in (repair, preferred):
        slot = root / "versions" / version
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir(parents=True)
        python.write_bytes(b"interpreter")
        python.chmod(0o755)
        (slot / ".install-complete.json").write_text(json.dumps({
            "version": version, "completed_at": "2026-10-13T00:00:00Z", "pid": 1,
        }), encoding="utf-8")
    env = {**os.environ, "AGENT_RT_ROOT": str(root)}
    if shell == "bash":
        git = shutil.which("git")
        executable = (
            Path(git).resolve().parent.parent / "bin" / "bash.exe"
            if os.name == "nt" and git else shutil.which("bash")
        )
        if executable is None or not Path(executable).is_file():
            pytest.skip("bash is unavailable")
        command = (
            'export AGENT_RT_ROOT="$(cygpath -u "$AGENT_RT_ROOT")"; '
            if os.name == "nt" else ""
        )
        variable = "AGENT_RT_PY" if resolver_kind == "canonical" else "AW_PY"
        command += f'. "$1"; printf "%s\\n" "${variable}"'
        argv = [
            str(executable), "-c", command, "--", (directory / "resolve-runtime.sh").as_posix(),
        ]
    else:
        executable = shutil.which(shell)
        if executable is None:
            pytest.skip(f"{shell} is unavailable")
        script = (directory / "resolve-runtime.ps1").as_posix().replace("'", "''")
        argv = [
            executable, "-NoProfile", "-Command",
            f". '{script}'; [Console]::WriteLine($AgentRtPy)",
        ]
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert preferred in result.stdout and repair not in result.stdout


def _identity():
    source = {
        "kind": "local", "path": "original/source", "repo": "copilot-extensions",
        "plugin": "agent-worktrees", "version": "1.0.0-dev7",
        "commit": "original", "branch": "original-branch", "dirty": False,
        "payload_fingerprint": "frozen-fingerprint",
    }
    return {
        "package_version": "1.0.0.dev7",
        "build_info": {
            "version": "1.0.0-dev7", "runtime_generation": "original-build",
            "deployment_source": json.dumps(source),
        },
    }, source


def test_manifest_preserves_installed_source_and_distinct_runtime_identity(tmp_path):
    identity, source = _identity()
    slot = tmp_path / "versions" / f"1.0.0-dev7+repair.{'a' * 32}"
    manifest = json.loads(deployment.manifest_bytes(identity, slot, "frozen"))
    assert manifest["source"] == source
    assert manifest["runtime_generation"] == "original-build"
    assert manifest["runtime_slot"] == slot.name
    assert manifest["payload_hash"] == "frozen"
    assert manifest["venv"] == slot.as_posix()


@pytest.mark.parametrize("repaired", [False, True])
def test_manifest_retains_source_identity_separately_from_package_version(tmp_path, repaired):
    identity, source = _identity()
    version = "1.0.0-dev7+source." + "a" * 40 + "." + "b" * 16
    document = {
        "plugin": "agent-worktrees", "package_version": "1.0.0-dev7",
        "runtime_version": version, "source_commit": "a" * 40,
        "source_tree": "c" * 40, "assembly_hash": "b" * 64,
        "schema": "copilot-extensions.runtime-source", "version": 1,
    }
    source.update(runtime_version=version, runtime_source=document)
    identity["build_info"]["deployment_source"] = json.dumps(source)
    slot = tmp_path / "versions" / (version + (".repair." + "d" * 32 if repaired else ""))
    manifest = json.loads(deployment.manifest_bytes(identity, slot, "frozen"))
    assert manifest["runtime_version"] == slot.name
    assert manifest["runtime_source"] == document
    assert manifest["source"]["version"] == "1.0.0-dev7"


def test_manifest_storage_failure_preserves_previous_evidence(tmp_path, monkeypatch):
    previous = b"previous exact deployment bytes\r\n"
    deployment.publish(tmp_path, previous)
    monkeypatch.setattr(
        deployment.os, "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("storage failure")),
    )
    with pytest.raises(OSError, match="storage failure"):
        deployment.publish(tmp_path, b"candidate")
    assert deployment.read_manifest(tmp_path) == previous
    assert not list(tmp_path.glob("deploy-manifest.json.tmp-*"))


def test_manifest_rollback_restores_exact_bytes_and_refuses_other_writer(tmp_path):
    previous = b"previous\r\n"
    deployment.publish(tmp_path, b"candidate")
    deployment.restore(tmp_path, previous, b"candidate")
    assert deployment.read_manifest(tmp_path) == previous
    deployment.publish(tmp_path, b"another writer")
    with pytest.raises(RuntimeError, match="changed before rollback"):
        deployment.restore(tmp_path, previous, b"candidate")
    assert deployment.read_manifest(tmp_path) == b"another writer"


def test_same_dev_prefix_still_detects_a_replaced_process_generation(tmp_path, monkeypatch):
    from agent_worktrees import runtime_identity
    from agent_worktrees import status_updater_cli as updater

    slot = tmp_path / "versions" / "dev"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    (tmp_path / "current-version").write_text("dev\n", encoding="utf-8")
    (bootstrap / "runtime-generation").write_text("a" * 32 + "\n", encoding="utf-8")
    monkeypatch.setattr(runtime_identity, "PROCESS_GENERATION", "a" * 32)
    monkeypatch.setattr(updater.sys, "prefix", str(slot))
    assert updater._runtime_superseded(install_root=tmp_path) is False
    (bootstrap / "runtime-generation").write_text("b" * 32 + "\n", encoding="utf-8")
    assert updater._runtime_superseded(install_root=tmp_path) is True
    assert updater._runtime_superseded(
        prefix=str(slot), install_root=tmp_path, generation="b" * 32,
    ) is False
    assert updater._runtime_superseded(
        prefix=str(slot), install_root=tmp_path, generation="a" * 32,
    ) is True


def test_legacy_slot_has_no_invented_generation_and_malformed_evidence_is_logged(tmp_path, caplog):
    from agent_worktrees import runtime_identity

    assert runtime_identity.generation_superseded(tmp_path, "a" * 32) is False
    bootstrap = tmp_path / ".runtime-bootstrap"
    bootstrap.mkdir()
    (bootstrap / "runtime-generation").write_text("not-a-generation\n", encoding="utf-8")
    assert runtime_identity.slot_generation(tmp_path) is None
    assert "generation evidence is malformed" in caplog.text


@pytest.mark.parametrize("lock_current,health_current", [
    (False, False), (True, False), (True, True),
])
def test_equal_prefix_cannot_hide_an_old_monitor_generation(
    engine, tmp_path, monkeypatch, lock_current, health_current,
):
    from types import SimpleNamespace

    import runtime_install as driver

    from agent_worktrees import locks
    from agent_worktrees import status_monitor_cutover as cutover

    slot = tmp_path / "versions" / "dev"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    generation = "a" * 32
    (bootstrap / "runtime-generation").write_text(generation + "\n", encoding="utf-8")
    record = {
        "prefix": str(slot), "pid": 123,
        "runtime_generation": generation if lock_current else "b" * 32,
    }
    monkeypatch.setattr(locks, "read_lock", lambda _path: record)
    monkeypatch.setattr(locks, "lock_is_live", lambda _record: True)
    monkeypatch.setattr(
        cutover, "live_endpoint", lambda: SimpleNamespace(pid=123, base_url="unused"),
    )
    monkeypatch.setattr(cutover, "ControlClient", lambda _url: SimpleNamespace(
        health=lambda: {
            "status": "ready", "published": True, "pid": 123,
            "runtime_generation": generation if health_current else "b" * 32,
        },
    ))
    assert driver._candidate_serving(tmp_path, slot) is (lock_current and health_current)


@pytest.mark.parametrize("monitor_committed", [False, True])
def test_manifest_interruption_stays_under_custody_until_restore_or_commit_forward(
    engine, tmp_path, monkeypatch, monitor_committed,
):
    from contextlib import nullcontext
    from types import SimpleNamespace

    import runtime_install as driver

    from agent_worktrees import config as cfg
    from agent_worktrees import status_monitor_cutover as cutover

    for version in ("1.0.0", "2.0.0"):
        (tmp_path / "versions" / version).mkdir(parents=True)
        engine.runtime.mark_complete(tmp_path, version, payload_hash="frozen")
    engine.runtime.activate(tmp_path, "1.0.0", link_free=True)
    previous_manifest = b"exact previous deployment\r\n"
    deployment.publish(tmp_path, previous_manifest)
    released = []
    governance = SimpleNamespace(
        root=tmp_path, payload=tmp_path, signature=lambda: {},
        held=lambda *, timeout: nullcontext(),
    )
    monkeypatch.setattr(driver, "Governance", lambda *_args: governance)
    monkeypatch.setattr(driver, "_load_installed", lambda _slot: None)
    monkeypatch.setattr(driver, "_installed_identity", lambda _slot: {
        "package_version": "2.0.0", "build_info": {
            "version": "2.0.0", "runtime_generation": "candidate-build",
        },
    })
    monkeypatch.setattr(
        cfg, "install_dir", lambda: tmp_path if monitor_committed else tmp_path / "other",
    )
    monkeypatch.setattr(driver, "_monitor_identity", lambda _root: None)
    monkeypatch.setattr(driver, "_candidate_serving", lambda *_args: False)
    monkeypatch.setattr(
        cutover, "_acquire_cutover_lock",
        lambda _root: SimpleNamespace(release=lambda: released.append("cutover")),
    )
    monkeypatch.setattr(cutover, "activate_after_update", lambda **_kwargs: {"action": "cutover"})
    monkeypatch.setattr(driver, "_settle_monitor", lambda *_args: True)
    publish = deployment.publish
    calls = []

    def interrupted_publication(root, content):
        assert released == []
        for version in ("1.0.0", "2.0.0"):
            with pytest.raises(engine.runtime.RetentionTimeout):
                with engine.runtime._kernel_lease(
                    root, version, kind="retention", shared=False, timeout=0,
                ):
                    pytest.fail("manifest outcome released runtime custody")
        calls.append(content)
        publish(root, content)
        if len(calls) == 1:
            raise OSError("publication acknowledgement lost")

    monkeypatch.setattr(deployment, "publish", interrupted_publication)
    with pytest.raises(OSError, match="acknowledgement lost"):
        driver.settle_candidate(
            tmp_path, "2.0.0", payload=tmp_path, payload_hash="frozen",
            expected_governance={}, previous="1.0.0",
        )
    assert len(calls) == 2 and released == ["cutover"]
    if monitor_committed:
        assert engine.runtime.current_version(tmp_path) == "2.0.0"
        assert deployment.read_manifest(tmp_path) == calls[0] == calls[1]
    else:
        assert engine.runtime.current_version(tmp_path) == "1.0.0"
        assert deployment.read_manifest(tmp_path) == previous_manifest == calls[1]
