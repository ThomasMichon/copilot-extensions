"""Only Bridge's source-qualified selected snapshot may submit provision."""

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest
from packaging.version import Version

from agent_procutil import no_window_kwargs
from update_driver import drain, foundation_status, status
from update_driver import first_use as provision

from test_first_use import primitive
from test_first_use_bridge import (
    REPOSITORY, assert_bridge_registration, bridge_service_adapter, build_bridge_native_test_assets,
    materialized_bridge as materialized_bridge, native_recipe_runner as native_recipe_runner,
)
from test_first_use_native import native_command
from test_first_use_genesis import observe
from test_first_use_vault import cold_environment, native_genesis
from test_first_use_vault_bootstrap import receipt_completed_count


pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="native Bridge selected snapshots are qualified only on Windows and Linux",
)


def qualify_source(payload, *, commit="a" * 40, tree="b" * 40):
    source = runpy.run_path(str(payload / "scripts/versioned_runtime_source.py"))
    version = json.loads((payload / "plugin.json").read_text())["version"]
    digest = source["assembly_hash"](payload)
    identity = source["SourceIdentity"](
        "agent-bridge", version, commit, tree, digest,
        source["runtime_version"](version, commit, digest),
    )
    (payload / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
    return identity


@pytest.fixture
def selected_bridge_snapshot(owning_bridge):
    return select_snapshot(owning_bridge)


def select_snapshot(owning_bridge):
    payload, installation, project = owning_bridge
    captured = primitive(payload).resolve_context(
        payload_root=payload, plugin_id="agent-bridge", project_root=project,
    )
    identity = qualify_source(payload)
    source = runpy.run_path(str(payload / "scripts/versioned_runtime_source.py"))
    identity = source["read_source_identity"](payload, identity.package_version, plugin="agent-bridge")
    snapshot = source["publish_source_snapshot"](payload, installation, identity)
    (snapshot / "snapshot-provenance.json").write_text(json.dumps({
        "schema": "copilot-extensions.legacy-snapshot-source", "version": 2,
        "snapshotId": snapshot.name, "payloadVersion": identity.runtime_version,
        "resolution": captured,
    }), encoding="utf-8")
    (installation / "payload-dir").write_text(str(snapshot), encoding="utf-8")
    (installation / "stamped-version").write_text(identity.runtime_version, encoding="utf-8")
    payload.rename(payload.with_name("unavailable-installed-payload"))
    return snapshot, installation, project


def test_bridge_selected_snapshot_retains_source_context_and_native_installer(selected_bridge_snapshot, area):
    payload, installation, project = selected_bridge_snapshot
    root = area / "queue"
    admission = provision.inspect(payload, installation, project)
    request = provision.enqueue(payload, installation, project, root=root)
    assert provision.enqueue(payload, installation, project, root=root).id == request.id
    assert json.loads(request.spec.source) == admission
    assert admission["runtime_source"] == json.loads((payload / "runtime-source.json").read_text())
    assert request.runner.context_keys == tuple(sorted(provision.BRIDGE_CONTEXT_KEYS))
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 7, result.log_path.read_text()
    assert (installation / "executed").read_text() == "provision"
    assert Path((installation / "project-path").read_text()) == project
    frozen = Path((installation / "source-path").read_text().strip()).parent
    assert frozen.name == "agent-bridge" and frozen.parent.parent == installation / ".install-stage"
    for name in ("snapshot-provenance.json", "runtime-source.json"):
        assert (frozen / name).read_bytes() == (payload / name).read_bytes()
    assert not (installation / "active.json").exists()


@native_genesis
def test_actual_stamp_preserves_bridge_selected_provenance(
    materialized_bridge, area, monkeypatch,
):
    payload, installation, project = materialized_bridge
    if os.name == "nt":
        monkeypatch.setenv("PATH", str(Path(os.environ["SystemRoot"]) / "System32"))
    identity = qualify_source(payload)
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root, installer_action="stamp")
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 0, result.log_path.read_text()
    snapshot = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    assert snapshot == installation / "snapshots" / identity.runtime_version
    provenance = json.loads((snapshot / "snapshot-provenance.json").read_text(encoding="utf-8-sig"))
    assert provenance["snapshotId"] == snapshot.name
    assert provenance["payloadVersion"] == identity.runtime_version
    assert provenance["resolution"]["sourceFingerprint"] == json.loads(request.spec.source)["source_fingerprint"]
    payload.rename(payload.with_name("unavailable-installed-payload"))
    admission = provision.inspect(snapshot, installation, project)
    assert admission["runtime_source"] == identity.document()
    assert not (installation / "current-version").exists()
    assert not (installation / "start-agent-bridge.ps1").exists()


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("reason", [
    "unselected", "stamped", "identity", "fingerprint", "missing", "malformed",
    "ordinary-copy", "unavailable", "no-self", "pinned", "nested", "managed", "cell",
    "custom-root", "stamp", "install", "missing-source", "malformed-source",
    "foreign-source", "missing-helper", "missing-context", "declaration",
])
def test_bridge_selected_snapshot_refusal_precedes_acquisition_queue_and_writes(
    selected_bridge_snapshot, area, monkeypatch, native, reason,
):
    payload, installation, project = selected_bridge_snapshot
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
            "no-self": "AGENT_BRIDGE_NO_SELFPROVISION", "pinned": "AGENT_BRIDGE_PINNED_CONTEXT",
            "nested": "UPDATE_DRIVER_WITNESS", "cell": "AGENT_BRIDGE_INSTALLATION_ID",
        }[reason], "foreign")
    elif reason == "custom-root":
        installation = installation.with_name("other-bridge")
    elif reason in {"missing-helper", "missing-context"}:
        (payload / "scripts" / {
            "missing-helper": "installer_engine.py",
            "missing-context": "installation-context/installation_context.py",
        }[reason]).unlink()
    elif reason == "declaration":
        path = payload / "payload-invocation.json"
        manifest = json.loads(path.read_text())
        manifest["provisionMode"] = "snapshot"
        path.write_text(json.dumps(manifest), encoding="utf-8")
    elif reason in {"missing-source", "malformed-source", "foreign-source"}:
        path = payload / "runtime-source.json"
        if reason == "missing-source":
            path.unlink()
        elif reason == "malformed-source":
            path.write_text("{", encoding="utf-8")
        else:
            source = json.loads(path.read_text())
            source["plugin"] = "agent-logger"
            path.write_text(json.dumps(source), encoding="utf-8")
    elif reason == "managed":
        module = primitive(payload)
        arguments = dict(payload_root=payload, plugin_id="agent-bridge",
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
    "reselected", "substituted", "unavailable", "source-tree", "missing-source",
    "manifest-array", "identity-array", "invalid-installer", "empty-installer",
    "raising-installer", "dependency-loop",
])
def test_bridge_selected_snapshot_change_settles_without_installer_or_selection_mutation(
    selected_bridge_snapshot, area, change,
):
    payload, installation, project = selected_bridge_snapshot
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    if change == "reselected":
        (installation / "payload-dir").write_text(str(payload.with_name("other")), encoding="utf-8")
    elif change == "substituted":
        (payload / "scripts/install.ps1").write_text("exit 99\n", encoding="utf-8")
    elif change == "unavailable":
        payload.rename(payload.with_name("unavailable"))
    elif change == "source-tree":
        path = payload / "runtime-source.json"
        document = json.loads(path.read_text())
        document["source_tree"] = "c" * 40
        path.write_text(json.dumps(document), encoding="utf-8")
    elif change == "missing-source":
        (payload / "runtime-source.json").unlink()
    elif change in {"manifest-array", "identity-array"}:
        (payload / ("payload-invocation.json" if change == "manifest-array" else "plugin.json")).write_text(
            "[]", encoding="utf-8",
        )
    elif change == "invalid-installer":
        (payload / "scripts/installer_engine.py").write_text("def broken(\n", encoding="utf-8")
    elif change in {"empty-installer", "raising-installer"}:
        (payload / "scripts/installer_engine.py").write_text(
            "" if change == "empty-installer" else 'raise RuntimeError("fixture damaged engine")\n',
            encoding="utf-8",
        )
    elif change == "dependency-loop":
        dependency = payload / "libs"
        dependency.mkdir(exist_ok=True)
        dependency.rename(payload / "retained-libs")
        try:
            dependency.symlink_to(dependency.name, target_is_directory=True)
        except OSError as error:
            if os.name == "nt" and error.winerror == 1314:
                pytest.skip("native directory symlink privilege unavailable")
            raise
    before = {path: path.read_bytes() for path in installation.glob("*") if path.is_file()}
    drain(root=root)
    result = status(request.id, root=root)
    assert result.spec == request.spec and result.state == "completed" and result.exit_code == 1
    assert "Selected Bridge source refused before installer execution" in result.log_path.read_text()
    assert not (installation / "executed").exists()
    assert not (installation / ".install-stage").exists()
    assert {path: path.read_bytes() for path in installation.glob("*") if path.is_file()} == before


@pytest.mark.parametrize("change", ["after-freeze", "during-copy"])
def test_bridge_selected_snapshot_rechecks_selection_and_bytes_around_freeze(owning_bridge, area, change):
    payload, _installation, _project = owning_bridge
    engine = payload / "scripts/installer_engine.py"
    if change == "after-freeze":
        engine.write_text(engine.read_text() + "\n"
                          "_canonical_freeze_inputs = freeze_inputs\n"
                          "def freeze_inputs(*args, **kwargs):\n"
                          "    result = _canonical_freeze_inputs(*args, **kwargs)\n"
                          "    (Path(__file__).resolve().parents[3] / 'payload-dir').write_text(\n"
                          "        str(Path(__file__).resolve().parents[1].with_name('other')), encoding='utf-8')\n"
                          "    return result\n", encoding="utf-8")
    else:
        engine.write_text(engine.read_text() + "\n"
                          "_canonical_copyfile = shutil.copyfile\n"
                          "def _mutating_copyfile(source, target, **kwargs):\n"
                          "    result = _canonical_copyfile(source, target, **kwargs)\n"
                          "    if Path(source) == Path(__file__).with_name('install.sh'):\n"
                          "        Path(source).write_bytes(Path(source).read_bytes() + b'\\n# changed\\n')\n"
                          "    return result\n"
                          "shutil.copyfile = _mutating_copyfile\n", encoding="utf-8")
    payload, installation, project = select_snapshot(owning_bridge)
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 1, result.log_path.read_text()
    assert "Selected Bridge source refused before installer execution" in result.log_path.read_text()
    assert not (installation / "executed").exists()
    assert (installation / ".install-stage").is_dir()


@native_genesis
def test_actual_bridge_source_selected_snapshot_caller_loss_generations_and_failed_candidate(
    materialized_bridge, area, native_window_observation, native_recipe_runner,
):
    payload, installation, project = materialized_bridge
    checkout = area / "source-checkout"
    source_payload = checkout / "plugins/agent-bridge"
    ignored = shutil.ignore_patterns(
        "__pycache__", "*.pyc", ".pytest_cache", "tests", "build", "dist", "*.egg-info",
    )
    shutil.copytree(REPOSITORY / "plugins/agent-bridge", source_payload, ignore=ignored)
    shutil.copytree(REPOSITORY / "libs", checkout / "libs", ignore=ignored)
    shutil.copytree(REPOSITORY / "tools", checkout / "tools", ignore=ignored)
    shutil.copytree(REPOSITORY / "docs", checkout / "docs", ignore=ignored)
    build_bridge_native_test_assets(source_payload, checkout / "libs/agent-procutil")
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_CONFIG_NOSYSTEM"] = "1"

    def git(*arguments):
        result = subprocess.run(
            ["git", "-C", str(checkout), *arguments], env=environment,
            capture_output=True, text=True, timeout=60, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    git("init", "-q", "--initial-branch=dev")
    git("config", "user.name", "Example")
    git("config", "user.email", "user@example.com")
    git("config", "core.autocrlf", "false")
    git("config", "core.longpaths", "true")
    git("config", "core.hooksPath", os.devnull)

    def prepare():
        git("add", "--all", "--force")
        git("commit", "-qm", "Synthetic Bridge source qualification")
        commit = git("rev-parse", "HEAD")
        git("update-ref", "refs/remotes/origin/dev", commit)
        result = subprocess.run(
            [sys.executable, str(REPOSITORY / "tools/prepare_runtime_source.py"),
             "--source", str(checkout), "--commit", commit, "--plugin", "agent-bridge",
             "--output", str(area / "prepared")],
            env=environment, cwd=project, capture_output=True, text=True, timeout=120,
            **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        prepared = json.loads(result.stdout)
        shutil.rmtree(payload)
        shutil.copytree(Path(prepared["payload"]), payload)
        return prepared["identity"]

    identity = prepare()
    stamp_root, root = area / "stamp-queue", area / "selected-queue"
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    adapter = bridge_service_adapter(env)

    def diagnostic():
        return "\n".join(f"{path.name}:\n{path.read_text(errors='replace')[-16000:]}"
                         for target in (root, stamp_root) for path in target.rglob("*.log"))

    def command(source, action="provision"):
        return native_command(
            stamp_root if action == "stamp" else root, source, installation, project,
            source=source / "libs/update-driver",
        ) + (["-Wait", "-InstallerAction", action] if os.name == "nt"
             else ["--wait", "--installer-action", action])

    def stamp():
        result = native_recipe_runner.run(
            command(payload, "stamp"), env=env, cwd=project, root=stamp_root, diagnostic=diagnostic,
        )
        assert result.returncode == 0, result.stdout + diagnostic()
        snapshot = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
        assert json.loads((snapshot / "runtime-source.json").read_text()) == identity
        assert snapshot.name == identity["runtime_version"]
        assert (snapshot / "snapshot-provenance.json").is_file()
        return snapshot

    snapshot = stamp()
    admission = provision.inspect(snapshot, installation, project)
    unavailable = payload.rename(payload.with_name("unavailable-installed-payload"))
    checkout.rename(area / "unavailable-source-checkout")
    receipt = (snapshot / "snapshot-provenance.json").read_bytes()
    caller, _log = native_recipe_runner.start(
        command(snapshot), env=env, cwd=project, root=root, diagnostic=diagnostic, contain_caller=True,
    )
    native_recipe_runner.lose_caller(caller)

    def original_request_completed():
        operation = foundation_status(root=root)
        if operation is not None:
            assert operation.state in {"running", "completed"}, diagnostic()
        logs = list(root.glob("first-use-*.log"))
        assert len(logs) <= 1, diagnostic()
        return bool(logs) and receipt_completed_count(root, 1, logs[0])

    observe(original_request_completed, 900, diagnostic)
    accepted = status(root=root)
    assert len(accepted) == 1
    assert all(item.state == "completed" and item.exit_code == 0
               and json.loads(item.spec.source) == admission for item in accepted), diagnostic()
    concurrent = [
        native_recipe_runner.start(command(snapshot), env=env, cwd=project, root=root, diagnostic=diagnostic)
        for _ in range(2)
    ]
    for process, log in concurrent:
        assert process.wait(timeout=900) == 0, log.read_text(errors="replace") + diagnostic()
    assert 2 <= len(status(root=root)) <= 3
    assert all(item.state == "completed" and item.exit_code == 0
               and json.loads(item.spec.source) == admission for item in status(root=root)), diagnostic()
    assert (snapshot / "snapshot-provenance.json").read_bytes() == receipt
    assert_bridge_registration(installation, adapter)
    selected = (installation / "current-version").read_text().strip()
    assert selected == identity["runtime_version"]
    completion = installation / "versions" / selected / ".install-complete.json"
    complete = completion.read_bytes()
    again = native_recipe_runner.run(
        command(snapshot), env=env, cwd=project, root=root, diagnostic=diagnostic,
    )
    assert again.returncode == 0, again.stdout + diagnostic()
    assert completion.read_bytes() == complete

    unavailable.rename(payload)
    (area / "unavailable-source-checkout").rename(checkout)
    with (source_payload / "README.md").open("a", encoding="utf-8") as output:
        output.write("\nSynthetic second source generation.\n")
    previous_identity = identity
    identity = prepare()
    assert identity["package_version"] == previous_identity["package_version"]
    assert identity["runtime_version"] != previous_identity["runtime_version"]
    snapshot = stamp()
    unavailable = payload.rename(unavailable)
    checkout.rename(area / "unavailable-source-checkout")
    second = native_recipe_runner.run(
        command(snapshot), env=env, cwd=project, root=root, diagnostic=diagnostic,
    )
    assert second.returncode == 0, second.stdout + diagnostic()
    assert (installation / "current-version").read_text().strip() == identity["runtime_version"]
    assert completion.read_bytes() == complete
    selected = identity["runtime_version"]
    slot = installation / "versions" / selected
    completion = slot / ".install-complete.json"
    complete = completion.read_bytes()
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    consumer = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import agent_bridge,json;from importlib.metadata import version;"
         "print(json.dumps({'origin':agent_bridge.__file__,'version':version('agent-bridge')}))"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert consumer.returncode == 0, consumer.stdout + consumer.stderr
    actual = json.loads(consumer.stdout)
    assert Path(actual["origin"]).is_relative_to(slot)
    assert Version(actual["version"]) == Version(identity["package_version"])
    module = subprocess.run(
        [str(python), "-I", "-B", "-m", "agent_bridge", "--help"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert module.returncode == 0 and "usage:" in module.stdout.lower(), module.stdout + module.stderr
    stub = installation.parent / ".local/bin" / ("agent-bridge.ps1" if os.name == "nt" else "agent-bridge")
    argv = (
        [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
         "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(stub), "--help"]
        if os.name == "nt" else [str(Path(env["PATH"]) / "bash"), str(stub), "--help"]
    )
    dispatch = subprocess.run(
        argv, env={**env, "AGENT_BRIDGE_NO_SELFPROVISION": "1"}, cwd=project,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert dispatch.returncode == 0 and "usage:" in dispatch.stdout.lower(), dispatch.stdout + dispatch.stderr
    unavailable.rename(payload)
    (area / "unavailable-source-checkout").rename(checkout)
    (source_payload / "src/agent_bridge/__init__.py").write_text(
        'raise RuntimeError("fixture failed selected Bridge source")\n', encoding="utf-8",
    )
    identity = prepare()
    failed_snapshot = stamp()
    payload.rename(unavailable)
    checkout.rename(area / "unavailable-source-checkout")
    redirects = {name: (installation / name).read_bytes() for name in ("payload-dir", "stamped-version")}
    failed = native_recipe_runner.run(
        command(failed_snapshot), env=env, cwd=project, root=root, diagnostic=diagnostic,
    )
    assert failed.returncode != 0, failed.stdout + diagnostic()
    result = status(root=root)[-1]
    assert result.state == "completed" and result.exit_code == failed.returncode
    assert json.loads(result.spec.source)["runtime_source"] == identity
    assert (installation / "current-version").read_text().strip() == selected
    assert completion.read_bytes() == complete
    assert {name: (installation / name).read_bytes() for name in redirects} == redirects
    assert not (installation / "active.json").exists()
    assert not (installation / "agent-bridge.pid").exists()
