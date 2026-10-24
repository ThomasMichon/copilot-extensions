"""Pull Requests has a fixed installer recipe, not Vault's service options."""

import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys

import pytest

from agent_procutil import no_window_kwargs
from update_driver import ParticipantError, drain, status
from update_driver import first_use as provision

from test_first_use import primitive
from test_first_use_native import native_command
from test_first_use_vault import cold_environment, native_genesis

pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="fixed native recipes are qualified only on Windows and Linux",
)


@pytest.mark.parametrize("installer_action", ["provision", "stamp"])
def test_pull_requests_fixed_recipe_retains_source_root_and_result(
    owning_pull_requests, area, monkeypatch, installer_action,
):
    payload, installation, project = owning_pull_requests
    root = area / "queue"
    request = provision.enqueue(
        payload, installation, project, root=root, installer_action=installer_action,
    )
    repeated = provision.enqueue(
        payload, installation, project, root=root, installer_action=installer_action,
    )
    assert request.id == repeated.id and not installation.exists()
    admission = json.loads(request.spec.source)
    assert admission["plugin"] == "agent-pull-requests"
    assert admission["runtime_root"] == str(installation)
    assert admission["project"] == str(project)
    assert request.runner.context_keys == tuple(sorted(provision.PULL_REQUESTS_CONTEXT_KEYS))
    for name in ("FORCE", "ACTION", "INSTALL_DIR", "NO_SELFPROVISION"):
        monkeypatch.setenv("AGENT_PULL_REQUESTS_" + name, "foreign-worker-value")
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 7, result.log_path.read_text()
    assert (installation / "executed").read_text() == installer_action
    frozen = Path((installation / "source-path").read_text().strip()).parent
    assert frozen.name == payload.name
    assert frozen.parent.parent == installation / ".install-stage"
    assert frozen.parent.name.startswith(request.id + "-")
    assert int(frozen.parent.name.rpartition("-")[2]) > 0
    assert (frozen / "plugin.json").read_bytes() == (payload / "plugin.json").read_bytes()
    assert (installation / "client-only").read_text().strip() == "0"


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("reason", [
    "client-only", "no-self", "pinned", "managed", "malformed", "noninstalled", "custom-root",
])
def test_pull_requests_refuses_before_queue_or_plugin_writes(
    owning_pull_requests, area, monkeypatch, native, reason,
):
    payload, installation, project = owning_pull_requests
    root = area / "cold"
    if reason == "noninstalled":
        payload = area / "market" / "plugins" / "agent-pull-requests"
    elif reason == "custom-root":
        installation = installation.with_name("other-pull-requests")
    elif reason == "no-self":
        monkeypatch.setenv("AGENT_PULL_REQUESTS_NO_SELFPROVISION", "1")
    elif reason == "pinned":
        monkeypatch.setenv("AGENT_PULL_REQUESTS_PINNED_CONTEXT", "{}")
    elif reason in {"managed", "malformed"}:
        module = primitive(payload)
        arguments = dict(
            payload_root=payload, plugin_id="agent-pull-requests",
            legacy_root=installation, project_root=project,
        )
        resolution = module.resolve_installation_mode(**arguments)
        installation.mkdir()
        policy = {
            "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": module._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    before = {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()}
    source = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    if native:
        result = subprocess.run(
            native_command(root, payload, installation, project, client_only=reason == "client-only"),
            env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=60, **no_window_kwargs(),
        )
        assert result.returncode != 0, result.stdout + result.stderr
    else:
        with pytest.raises(provision.ProvisionError):
            provision.enqueue(
                payload, installation, project, root=root, client_only=reason == "client-only",
            )
    assert not root.exists()
    assert {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()} == before
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == source


def test_pull_requests_empty_no_self_control_retains_presence_semantics(
    owning_pull_requests, area, monkeypatch,
):
    payload, installation, project = owning_pull_requests
    monkeypatch.setenv("AGENT_PULL_REQUESTS_NO_SELFPROVISION", "")
    with pytest.raises(provision.ProvisionError, match="self-provisioning is disabled"):
        provision.enqueue(payload, installation, project, root=area / "queue")
    assert not (area / "queue").exists() and not installation.exists()


@pytest.mark.parametrize("installer_action", ["provision", "stamp"])
@pytest.mark.parametrize("owning_fixture", ["owning_pull_requests", "selected_pull_requests_snapshot"])
def test_pull_requests_native_admission_selects_its_own_source_before_acquisition(
    request, area, installer_action, owning_fixture,
):
    payload, installation, project = request.getfixturevalue(owning_fixture)
    root = area / "cold"
    existed = installation.exists()
    before = {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()}
    command = native_command(root, payload, installation, project)
    command += (
        ["-Mode", "owner", "-ExpectedSource", "sha256:" + "0" * 64,
         "-InstallerAction", installer_action] if os.name == "nt" else
        ["--run-owner", "sha256:" + "0" * 64, "--installer-action", installer_action]
    )
    result = subprocess.run(
        command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    assert result.returncode != 0
    assert "Owning source changed after native first-use admission" in result.stderr, result.stderr
    assert not root.exists() and installation.exists() is existed
    assert {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()} == before


def test_pull_requests_substituted_source_remains_interrupted(
    owning_pull_requests, area,
):
    payload, installation, project = owning_pull_requests
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    (payload / "scripts" / "install.ps1").write_text("exit 99\n", encoding="utf-8")
    with pytest.raises(ParticipantError):
        drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "interrupted" and result.exit_code is None
    assert not installation.exists()


@pytest.fixture
def selected_pull_requests_snapshot(owning_pull_requests):
    payload, installation, project = owning_pull_requests
    captured = primitive(payload).resolve_context(
        payload_root=payload, plugin_id="agent-pull-requests", project_root=project,
    )
    snapshot = installation / "snapshots" / "1.0.0-retained"
    shutil.copytree(payload, snapshot)
    (snapshot / "snapshot-provenance.json").write_text(json.dumps({
        "schema": "copilot-extensions.legacy-snapshot-source", "version": 2,
        "snapshotId": snapshot.name, "payloadVersion": "1.0.0", "resolution": captured,
    }), encoding="utf-8")
    (installation / "payload-dir").write_text(str(snapshot), encoding="utf-8")
    (installation / "stamped-version").write_text("1.0.0", encoding="utf-8")
    payload.rename(payload.with_name("unavailable-installed-payload"))
    return snapshot, installation, project


@pytest.mark.parametrize("installer_action", ["provision", "stamp"])
def test_pull_requests_selected_snapshot_keeps_exact_owning_source(
    selected_pull_requests_snapshot, area, installer_action,
):
    payload, installation, project = selected_pull_requests_snapshot
    root = area / "queue"
    admission = provision.inspect(payload, installation, project, installer_action=installer_action)
    request = provision.enqueue(
        payload, installation, project, root=root, installer_action=installer_action,
    )
    assert json.loads(request.spec.source) == admission
    assert admission["payload"] == str(payload)
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 7, result.log_path.read_text()
    frozen = Path((installation / "source-path").read_text().strip()).parent
    assert frozen.name == "agent-pull-requests"
    assert frozen.parent.parent == installation / ".install-stage"
    assert (frozen / "snapshot-provenance.json").read_bytes() == (
        payload / "snapshot-provenance.json"
    ).read_bytes()


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("reason", [
    "unselected", "stamped", "identity", "fingerprint", "missing", "ordinary-copy",
])
def test_pull_requests_snapshot_refusal_precedes_acquisition_and_queue(
    selected_pull_requests_snapshot, area, native, reason,
):
    payload, installation, project = selected_pull_requests_snapshot
    receipt = payload / "snapshot-provenance.json"
    document = json.loads(receipt.read_text())
    if reason == "unselected":
        (installation / "payload-dir").write_text(str(payload.with_name("other")), encoding="utf-8")
    elif reason == "stamped":
        (installation / "stamped-version").write_text("9.9.9", encoding="utf-8")
    elif reason == "identity":
        document["snapshotId"] = "other"
        receipt.write_text(json.dumps(document), encoding="utf-8")
    elif reason == "fingerprint":
        document["resolution"]["sourceFingerprint"] = "sha256:" + "0" * 64
        receipt.write_text(json.dumps(document), encoding="utf-8")
    elif reason == "missing":
        receipt.unlink()
    elif reason == "ordinary-copy":
        copied = area / "ordinary-copy"
        shutil.copytree(payload, copied)
        payload = copied
    root = area / "cold"
    before = {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()}
    if native:
        result = subprocess.run(
            native_command(root, payload, installation, project),
            env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=60, **no_window_kwargs(),
        )
        assert result.returncode != 0, result.stdout + result.stderr
    else:
        with pytest.raises(provision.ProvisionError):
            provision.enqueue(payload, installation, project, root=root)
    assert not root.exists()
    assert {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()} == before


def test_pull_requests_snapshot_reselection_does_not_substitute_queued_input(
    selected_pull_requests_snapshot, area,
):
    payload, installation, project = selected_pull_requests_snapshot
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    (installation / "payload-dir").write_text(str(payload.with_name("newer")), encoding="utf-8")
    with pytest.raises(ParticipantError):
        drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "interrupted" and result.exit_code is None
    assert not (installation / "executed").exists()
    assert not (installation / ".install-stage").exists()


@pytest.fixture
def materialized_pull_requests(owning_pull_requests, area):
    payload, installation, project = owning_pull_requests
    repository = Path(__file__).resolve().parents[3]
    candidate = area / "materialized" / "plugins" / "agent-pull-requests"
    shutil.copytree(
        repository / "plugins" / "agent-pull-requests", candidate,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".pytest_cache", "tests", "build", "dist", "*.egg-info",
        ),
    )
    previous = sys.path[:]
    try:
        release = runpy.run_path(str(repository / "tools" / "materialize_main.py"))
        result = release["materialize"](candidate.parents[1], canonical_root=repository)
    finally:
        sys.path[:] = previous
    assert not any(line.startswith("SKIP ") for line in result), result
    shutil.rmtree(payload)
    shutil.copytree(candidate, payload)
    assert (payload / "scripts" / "installer_engine.py").is_file()
    return payload, installation, project


def select_version_two_snapshot(snapshot, installation):
    selected = snapshot.with_name(snapshot.name + "-retained")
    shutil.copytree(snapshot, selected)
    receipt = selected / "snapshot-provenance.json"
    document = json.loads(receipt.read_text(encoding="utf-8"))
    document.update(
        version=2, snapshotId=selected.name,
        payloadVersion=(installation / "stamped-version").read_text(encoding="utf-8").strip(),
    )
    receipt.write_text(json.dumps(document), encoding="utf-8")
    (installation / "payload-dir").write_text(str(selected), encoding="utf-8")
    return selected


@pytest.mark.parametrize("receipt_version", [1, 2])
def test_actual_pull_requests_materialized_admission_and_owned_stamp(
    materialized_pull_requests, area, receipt_version,
):
    payload, installation, project = materialized_pull_requests
    cache = payload / "__pycache__"
    cache.mkdir()
    (cache / "ignored.pyc").write_bytes(b"not a canonical input")
    (payload / "empty-directory").mkdir()
    before = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    admission = provision.inspect(payload, installation, project, installer_action="stamp")
    assert admission["plugin"] == "agent-pull-requests"
    assert admission["mode"] == "legacy" and not installation.exists()
    root = area / "queue"
    request = provision.enqueue(
        payload, installation, project, root=root, installer_action="stamp",
    )
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 0, result.log_path.read_text()
    pointer = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    assert pointer.is_relative_to(installation / "snapshots")
    assert (pointer / "scripts" / "installer_engine.py").is_file()
    assert (installation.parent / ".local" / "bin" / (
        "agent-pull-requests.ps1" if os.name == "nt" else "agent-pull-requests"
    )).is_file()
    assert not (installation / "current-version").exists()
    assert not list((installation / "versions").glob("*/pyvenv.cfg"))
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == before
    payload.rename(payload.with_name("unavailable-installed-payload"))
    if receipt_version == 2:
        pointer = select_version_two_snapshot(pointer, installation)
    source_receipt = (pointer / "snapshot-provenance.json").read_bytes()
    retained = provision.enqueue(
        pointer, installation, project, root=root, installer_action="stamp",
    )
    drain(root=root)
    result = status(retained.id, root=root)
    assert result.state == "completed" and result.exit_code == 0, result.log_path.read_text()
    assert json.loads(result.spec.source)["payload"] == str(pointer)
    if receipt_version == 2:
        assert (pointer / "snapshot-provenance.json").read_bytes() == source_receipt
    selected = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    assert json.loads((selected / "snapshot-provenance.json").read_text())["version"] == 1
    assert provision.inspect(selected, installation, project)["source_fingerprint"] == (
        admission["source_fingerprint"]
    )
    assert not (installation / "current-version").exists()


@native_genesis
def test_actual_pull_requests_cold_provision_preserves_selection_and_source_independence(
    materialized_pull_requests, area, native_window_observation,
):
    payload, installation, project = materialized_pull_requests
    library = Path(__file__).resolve().parents[2]
    from update_driver.foundation_inputs import GENERATED, PACKAGES

    inputs = area / "foundation-source"
    for name in PACKAGES:
        shutil.copytree(
            library / name, inputs / name,
            ignore=lambda _path, names: {
                name for name in names if name in GENERATED
                or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))
            },
        )
    source = inputs / "update-driver"
    root = area / "cold"
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    env["AGENT_PULL_REQUESTS_HOME"] = str(installation)
    tools = Path(env["PATH"])

    def diagnostic():
        return "\n".join(
            f"{path.relative_to(root)}:\n{path.read_text(errors='replace')[-16000:]}"
            for path in root.rglob("*.log")
        )

    def run_request(installer_action, owning_source=payload):
        command = native_command(root, owning_source, installation, project, source=source)
        command += (
            ["-Wait", "-InstallerAction", installer_action] if os.name == "nt"
            else ["--wait", "--installer-action", installer_action]
        )
        return subprocess.run(
            command, env=env, cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", timeout=900, **no_window_kwargs(),
        )

    stamp = run_request("stamp")
    assert stamp.returncode == 0, stamp.stdout + stamp.stderr + diagnostic()
    assert not (installation / "current-version").exists()
    snapshot = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    snapshot = select_version_two_snapshot(snapshot, installation)
    removed = payload.rename(payload.with_name("unavailable-installed-payload"))
    installed = run_request("provision", snapshot)
    assert installed.returncode == 0, installed.stdout + installed.stderr + diagnostic()
    removed.rename(payload)
    requests = status(root=root)
    assert len(requests) == 2 and all(
        item.state == "completed" and item.exit_code == 0 for item in requests
    ), diagnostic()
    assert {json.loads(item.spec.source)["plugin"] for item in requests} == {"agent-pull-requests"}
    assert {json.loads(item.spec.source)["payload"] for item in requests} == {str(payload), str(snapshot)}
    assert {item.spec.argv for item in requests} == {(), ("--installer-action", "stamp")}
    version = (installation / "current-version").read_text().strip()
    slot = installation / "versions" / version
    completion = slot / ".install-complete.json"
    complete = completion.read_bytes()
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    project_file = payload / "pyproject.toml"
    project_file.write_text(re.sub(
        r'(?m)^version\s*=\s*"[^"]+"', 'version = "99.0.0"',
        project_file.read_text(),
    ), encoding="utf-8")
    manifest_path = payload / "plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = "99.0.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (payload / "src" / "agent_pull_requests" / "__main__.py").write_text(
        'raise RuntimeError("fixture failed installed Pull Requests entrypoint")\n', encoding="utf-8",
    )
    failed = run_request("provision")
    assert failed.returncode != 0, failed.stdout + failed.stderr + diagnostic()
    requests = status(root=root)
    assert len(requests) == 3 and requests[-1].state == "completed"
    assert requests[-1].exit_code != 0, diagnostic()
    assert "fixture failed installed Pull Requests entrypoint" in requests[-1].log_path.read_text(
        errors="replace",
    ), diagnostic()
    assert (installation / "current-version").read_text().strip() == version
    assert completion.read_bytes() == complete
    assert not (installation / "versions" / "99.0.0" / ".install-complete.json").exists()
    payload.rename(payload.with_name("removed-pull-requests"))
    inputs.rename(area / "removed-foundation-source")
    command = installation.parent / ".local" / "bin" / (
        "agent-pull-requests.ps1" if os.name == "nt" else "agent-pull-requests"
    )
    argv = (
        [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
         "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(command), "--help"]
        if os.name == "nt" else [str(tools / "bash"), str(command), "--help"]
    )
    dispatch = subprocess.run(
        argv, env={**env, "AGENT_PULL_REQUESTS_NO_SELFPROVISION": "1"}, cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=30, **no_window_kwargs(),
    )
    assert dispatch.returncode == 0 and "usage:" in dispatch.stdout.lower(), (
        dispatch.stdout, dispatch.stderr, diagnostic(),
    )
    origin = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import agent_pull_requests,json; print(json.dumps(agent_pull_requests.__file__))"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert origin.returncode == 0, origin.stdout + origin.stderr
    assert Path(json.loads(origin.stdout)).is_relative_to(slot)
    assert len(status(root=root)) == 3 and completion.read_bytes() == complete
