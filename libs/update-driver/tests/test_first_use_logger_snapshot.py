"""Only the exact selected legacy Logger snapshot may provision service-less."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from agent_procutil import no_window_kwargs
from packaging.version import Version
from update_driver import drain, status
from update_driver import first_use as provision

from test_first_use import primitive
from test_first_use_logger import (
    assert_no_periodic_worker, logger_registration_adapter,
    materialized_logger as materialized_logger, registration_effects,
)
from test_first_use_native import native_command
from test_first_use_vault import cold_environment, native_genesis


pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="native Logger selected snapshots are qualified only on Windows and Linux",
)


def test_logger_selected_snapshot_keeps_queued_source_and_lifecycle_owner(selected_logger_snapshot, area):
    payload, installation, project = selected_logger_snapshot
    root = area / "queue"
    admission = provision.inspect(payload, installation, project)
    request = provision.enqueue(payload, installation, project, root=root)
    assert provision.enqueue(payload, installation, project, root=root).id == request.id
    assert json.loads(request.spec.source) == admission
    assert admission["payload"] == str(payload)
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 7, result.log_path.read_text()
    frozen = Path((installation / "source-path").read_text().strip()).parent
    assert frozen.name == "agent-logger" and frozen.parent.parent == installation / ".install-stage"
    assert (frozen / "snapshot-provenance.json").read_bytes() == (payload / "snapshot-provenance.json").read_bytes()
    owner = json.loads((installation / "deploy-manifest.json").read_text())["commandLifecycle"]["owner"]
    assert owner["sourceFingerprint"] == admission["source_fingerprint"]
    assert not owner.get("unattributed") and str(frozen) not in json.dumps(owner)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("reason", [
    "unselected", "stamped", "identity", "fingerprint", "missing", "malformed",
    "ordinary-copy", "unavailable", "no-self", "pinned", "nested", "managed", "cell",
    "custom-root", "stamp", "install", "missing-helper", "missing-context", "missing-lifecycle", "declaration",
])
def test_logger_snapshot_refusal_precedes_acquisition_queue_and_writes(
    selected_logger_snapshot, area, monkeypatch, native, reason,
):
    payload, installation, project = selected_logger_snapshot
    receipt = payload / "snapshot-provenance.json"
    document = json.loads(receipt.read_text())
    if reason == "unselected":
        (installation / "payload-dir").write_text(str(payload.with_name("other")), encoding="utf-8")
    elif reason == "stamped":
        (installation / "stamped-version").write_text("9.9.9", encoding="utf-8")
    elif reason in {"identity", "fingerprint"}:
        if reason == "identity":
            document["snapshotId"] = "other"
        else:
            document["resolution"]["sourceFingerprint"] = "sha256:" + "0" * 64
        receipt.write_text(json.dumps(document), encoding="utf-8")
    elif reason == "missing":
        receipt.unlink()
    elif reason == "malformed":
        receipt.write_text("{", encoding="utf-8")
    elif reason == "ordinary-copy":
        payload = Path(shutil.copytree(payload, area / "ordinary-copy"))
    elif reason == "unavailable":
        payload.rename(payload.with_name("unavailable"))
    elif reason in {"no-self", "pinned", "nested", "cell"}:
        monkeypatch.setenv({
            "no-self": "AGENT_LOGGER_NO_SELFPROVISION", "pinned": "AGENT_LOGGER_PINNED_CONTEXT",
            "nested": "UPDATE_DRIVER_WITNESS", "cell": "AGENT_LOGGER_INSTALLATION_ID",
        }[reason], "foreign")
    elif reason == "custom-root":
        installation = installation.with_name("other-logger")
    elif reason in {"missing-helper", "missing-context", "missing-lifecycle"}:
        (payload / "scripts" / {
            "missing-helper": "installer_engine.py",
            "missing-context": "installation-context/installation_context.py",
            "missing-lifecycle": "command_lifecycle.py",
        }[reason]).unlink()
    elif reason == "declaration":
        path = payload / "payload-invocation.json"
        manifest = json.loads(path.read_text())
        manifest["commands"][0]["module"] = "another_module"
        path.write_text(json.dumps(manifest), encoding="utf-8")
    elif reason == "managed":
        module = primitive(payload)
        arguments = dict(payload_root=payload, plugin_id="agent-logger",
                         legacy_root=installation, project_root=project)
        resolution = module.resolve_installation_mode(**arguments)
        (installation / "runtime-deployment.json").write_text(json.dumps({
            "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": module._deployment_owner(resolution, arguments), "managed": True,
        }), encoding="utf-8")
    action = reason if reason in {"stamp", "install"} else "provision"
    root = area / "cold"
    before = {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()}
    source = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    if native:
        command = native_command(root, payload, installation, project)
        command += ["-InstallerAction", action] if os.name == "nt" else ["--installer-action", action]
        result = subprocess.run(command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=60, **no_window_kwargs())
        assert result.returncode != 0, result.stdout + result.stderr
    else:
        with pytest.raises(provision.ProvisionError):
            provision.enqueue(payload, installation, project, root=root, installer_action=action)
    assert not root.exists()
    assert {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()} == before
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == source


@pytest.mark.parametrize("change", [
    "reselected", "substituted", "unavailable", "after-freeze", "during-copy",
    "missing-import", "manifest-array", "identity-array",
    "invalid-installer", "invalid-fragment",
    "metadata-project", "metadata-tool", "metadata-uv", "metadata-sources", "dependency-project",
    "unseasoned-artifact",
    "symlink-loop", "dependency-loop",
])
def test_logger_snapshot_change_preserves_accepted_request(selected_logger_snapshot, area, change):
    payload, installation, project = selected_logger_snapshot
    root = area / "queue"
    if change == "after-freeze":
        engine = payload / "scripts/installer_engine.py"
        engine.write_text(engine.read_text() + '\n'
                          '_canonical_freeze_inputs = freeze_inputs\n'
                          'def freeze_inputs(*args, **kwargs):\n'
                          '    result = _canonical_freeze_inputs(*args, **kwargs)\n'
                          '    from pathlib import Path as _Path\n'
                          '    (_Path(__file__).resolve().parents[3] / "payload-dir").write_text(\n'
                          '        str(_Path(__file__).resolve().parents[1].with_name("other")), encoding="utf-8")\n'
                          '    return result\n', encoding="utf-8")
    elif change == "during-copy":
        engine = payload / "scripts/installer_engine.py"
        engine.write_text(engine.read_text() + '\n'
                          '_canonical_copyfile = shutil.copyfile\n'
                          'def _mutating_copyfile(source, target, **kwargs):\n'
                          '    result = _canonical_copyfile(source, target, **kwargs)\n'
                          '    if Path(source) == Path(__file__).with_name("install.sh"):\n'
                          '        Path(source).write_bytes(Path(source).read_bytes() + b"\\n# fixture source mutation\\n")\n'
                          '    return result\n'
                          'shutil.copyfile = _mutating_copyfile\n', encoding="utf-8")
    elif change in {"dependency-project", "dependency-loop"}:
        dependency_path = (
            "libs/retained/dependency" if change == "dependency-loop" else "libs/fixture-dependency"
        )
        dependency = payload / dependency_path
        dependency.mkdir(parents=True)
        (dependency / "pyproject.toml").write_text(
            '[project]\nname="fixture-dependency"\nversion="1.0.0"\n', encoding="utf-8",
        )
        metadata = payload / "pyproject.toml"
        metadata.write_text(metadata.read_text() + '\n[tool.uv.sources]\n'
                            f'fixture-dependency={{path="{dependency_path}"}}\n', encoding="utf-8")
    request = provision.enqueue(payload, installation, project, root=root)
    if change == "reselected":
        (installation / "payload-dir").write_text(str(payload.with_name("other")), encoding="utf-8")
    elif change == "substituted":
        (payload / "scripts/install.ps1").write_text("exit 99\n", encoding="utf-8")
    elif change == "unavailable":
        payload.rename(payload.with_name("unavailable"))
    elif change in {"symlink-loop", "dependency-loop"}:
        linked = payload if change == "symlink-loop" else payload / "libs/retained"
        linked.rename(linked.with_name("retained-before-loop"))
        try:
            linked.symlink_to(linked.name, target_is_directory=True)
        except OSError as error:
            if os.name == "nt" and error.winerror == 1314:
                pytest.skip("native directory symlink privilege unavailable")
            raise
    elif change == "missing-import":
        (payload / "scripts/versioned_runtime.py").unlink()
    elif change in {"manifest-array", "identity-array"}:
        (payload / ("payload-invocation.json" if change == "manifest-array" else "plugin.json")).write_text(
            "[]", encoding="utf-8",
        )
    elif change in {"invalid-installer", "invalid-fragment"}:
        script = payload / "scripts" / (
            "installer_engine.py" if change == "invalid-installer"
            else "installation-context/_installation_context_source.py"
        )
        assert script.is_file()
        script.write_text("def broken(\n", encoding="utf-8")
    elif change.startswith("metadata-") or change == "dependency-project":
        manifest = payload / "pyproject.toml"
        if change == "dependency-project":
            manifest = payload / "libs/fixture-dependency/pyproject.toml"
        manifest.write_text({
            "metadata-project": 'project="damaged"\n',
            "metadata-tool": 'tool="damaged"\n[project]\nname="agent-logger"\n',
            "metadata-uv": '[project]\nname="agent-logger"\n[tool]\nuv="damaged"\n',
            "metadata-sources": '[project]\nname="agent-logger"\n[tool.uv]\nsources="damaged"\n',
            "dependency-project": 'project="damaged"\n',
        }[change], encoding="utf-8")
    elif change == "unseasoned-artifact":
        (payload / "unseasoned.whl").write_bytes(b"unadmitted local artifact")
        manifest = payload / "pyproject.toml"
        manifest.write_text(manifest.read_text() + '\n[tool.uv.sources]\n'
                            'unseasoned={path="unseasoned.whl"}\n', encoding="utf-8")
    drain(root=root)
    result = status(request.id, root=root)
    assert result.spec == request.spec and result.state == "completed" and result.exit_code == 1
    assert "Selected Logger source refused before installer execution" in result.log_path.read_text()
    if change == "during-copy":
        assert "Runtime input content changed while freezing inputs" in result.log_path.read_text()
    if change.startswith("metadata-") or change == "dependency-project":
        assert "must be a table" in result.log_path.read_text()
    if change == "unseasoned-artifact":
        assert "local UV artifacts cannot prove release age" in result.log_path.read_text()
    assert not (installation / "executed").exists()
    assert (installation / ".install-stage").exists() == (change in {"after-freeze", "during-copy"})


@native_genesis
def test_actual_logger_selected_snapshot_concurrent_provision_reuse_and_consumers(
    materialized_logger, area, native_window_observation,
):
    payload, installation, project = materialized_logger
    root = area / "cold"
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    env["AGENT_LOGGER_HOME"] = str(installation)
    adapter = logger_registration_adapter(env, area)

    def diagnostic():
        return "\n".join(f"{path.relative_to(root)}:\n{path.read_text(errors='replace')[-16000:]}"
                         for path in root.rglob("*.log"))

    def command(source, action="provision"):
        result = native_command(root, source, installation, project, source=source / "libs/update-driver")
        return result + (
            ["-Wait", "-InstallerAction", action] if os.name == "nt" else ["--wait", "--installer-action", action]
        )

    def stamp():
        result = subprocess.run(command(payload, "stamp"), env=env, cwd=project, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=900, **no_window_kwargs())
        assert result.returncode == 0, result.stdout + result.stderr + diagnostic()
        return Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())

    snapshot = stamp()
    receipt = (snapshot / "snapshot-provenance.json").read_bytes()
    admission = provision.inspect(snapshot, installation, project)
    assert admission["source_fingerprint"] == json.loads(status(root=root)[0].spec.source)["source_fingerprint"]
    removed = payload.rename(payload.with_name("unavailable-installed-payload"))
    assert not (installation / "current-version").exists()
    callers = [subprocess.Popen(command(snapshot), env=env, cwd=project, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs())
               for _ in range(2)]
    try:
        for caller in callers:
            out, err = caller.communicate(timeout=900)
            assert caller.returncode == 0, out.decode() + err.decode() + diagnostic()
    finally:
        for caller in callers:
            if caller.poll() is None:
                caller.kill()
                caller.communicate(timeout=30)
    requests = status(root=root)
    provisions = [item for item in requests if item.spec.argv == ()]
    assert 1 <= len(provisions) <= 2 and all(
        item.state == "completed" and item.exit_code == 0
        and json.loads(item.spec.source) == admission for item in provisions
    ), diagnostic()
    assert (snapshot / "snapshot-provenance.json").read_bytes() == receipt
    fresh_manifest = json.loads((installation / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    fresh_owner = fresh_manifest["commandLifecycle"]["owner"]
    assert fresh_owner["sourceFingerprint"] == admission["source_fingerprint"]
    assert not fresh_owner.get("unattributed") and ".install-stage" not in json.dumps(fresh_owner)
    selected = (installation / "current-version").read_text().strip()
    slot = installation / "versions" / selected
    completion = slot / ".install-complete.json"
    complete = completion.read_bytes()
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    effects = registration_effects(adapter)
    assert all(item["operation"] == "stop" for item in effects)
    assert_no_periodic_worker(installation)
    repeated = subprocess.run(command(snapshot), env=env, cwd=project, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=900, **no_window_kwargs())
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr + diagnostic()
    assert status(root=root)[-1].state == "completed" and status(root=root)[-1].exit_code == 0
    assert json.loads(status(root=root)[-1].spec.source) == admission
    assert (installation / "current-version").read_text().strip() == selected
    assert completion.read_bytes() == complete
    manifest = json.loads((installation / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    owner = manifest["commandLifecycle"]["owner"]
    assert owner["sourceFingerprint"] == admission["source_fingerprint"]
    assert not owner.get("unattributed") and ".install-stage" not in json.dumps(owner)
    identity = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import agent_logger,json; from importlib.metadata import version; "
         "from agent_logger._build_info import __version__,COMMIT; "
         "print(json.dumps({'origin':agent_logger.__file__,'version':version('agent-logger'),"
         "'build_version':__version__,'commit':COMMIT}))"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert identity.returncode == 0, identity.stdout + identity.stderr
    actual = json.loads(identity.stdout)
    assert Path(actual["origin"]).is_relative_to(slot)
    assert actual["version"] == str(Version(selected)) == str(Version(actual["build_version"]))
    version = subprocess.run(
        [str(python), "-I", "-B", "-m", "agent_logger", "version"], env=env, cwd=project,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert version.returncode == 0 and version.stdout.startswith(
        "agent-logger " + actual["build_version"] + " (commit " + actual["commit"]
    ), version.stdout + version.stderr
    for name, module in provision.LOGGER_COMMANDS:
        consumer = subprocess.run(
            [str(python), "-I", "-B", "-X", "utf8", "-m", module, "--help"], env=env, cwd=project,
            capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        assert consumer.returncode == 0 and "usage:" in consumer.stdout.lower(), (
            name, consumer.stdout, consumer.stderr,
        )
        stub = installation.parent / ".local/bin" / (name + ".ps1" if os.name == "nt" else name)
        argv = (
            [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
             "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(stub), "--help"]
            if os.name == "nt" else [str(Path(env["PATH"]) / "bash"), str(stub), "--help"]
        )
        dispatch = subprocess.run(argv, env={**env, "AGENT_LOGGER_NO_SELFPROVISION": "1"},
                                  cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs())
        assert dispatch.returncode == 0 and "usage:" in dispatch.stdout.lower(), (
            name, dispatch.stdout, dispatch.stderr,
        )
    removed.rename(payload)
    project_file = payload / "pyproject.toml"
    project_file.write_text(re.sub(r'(?m)^version\s*=\s*"[^"]+"', 'version = "99.0.0"',
                                   project_file.read_text()), encoding="utf-8")
    plugin = json.loads((payload / "plugin.json").read_text())
    plugin["version"] = "99.0.0"
    (payload / "plugin.json").write_text(json.dumps(plugin), encoding="utf-8")
    (payload / "src/agent_logger/__init__.py").write_text(
        'raise RuntimeError("fixture failed selected Logger candidate")\n', encoding="utf-8",
    )
    candidate = stamp()
    payload.rename(removed)
    failed = subprocess.run(command(candidate), env=env, cwd=project, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=900, **no_window_kwargs())
    assert failed.returncode != 0, failed.stdout + failed.stderr + diagnostic()
    result = status(root=root)[-1]
    assert result.state == "completed" and result.exit_code != 0
    assert json.loads(result.spec.source)["payload"] == str(candidate)
    assert (installation / "current-version").read_text().strip() == selected
    assert completion.read_bytes() == complete
    assert all(item["operation"] == "stop" for item in registration_effects(adapter))
    assert_no_periodic_worker(installation)
    assert not (installation / "logs/session-sync.log").exists()
