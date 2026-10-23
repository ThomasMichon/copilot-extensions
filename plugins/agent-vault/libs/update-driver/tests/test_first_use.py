"""Fixed recipe admission with real canonical source/publication helpers."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from update_driver import ParticipantError, RequestSpec, TrustedRunner, drain, enqueue, status
from update_driver import first_use as provision
from agent_procutil import no_window_kwargs

pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="fixed native first-use execution is qualified only on Windows and Linux",
)


def primitive(payload):
    helper = payload / "scripts" / "installation-context" / "installation_context.py"
    spec = importlib.util.spec_from_file_location("_provision_test_context", helper)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fixed_request_is_durable_coalesced_and_not_a_recipe(owning_payload, area):
    root = area / "queue"
    payload, installation, project = owning_payload
    one = provision.enqueue(payload, installation, project, root=root)
    two = provision.enqueue(payload, installation, project, root=root)
    assert one.id == two.id
    assert one.state == "pending" and status(one.id, root=root) == one
    assert one.spec.backend == "owning-plugin-provision" and one.spec.argv == ()
    admission = json.loads(one.spec.source)
    assert admission["source"]["kind"] == "directory"
    assert len(admission["input_hash"]) == 64
    assert one.runner.context_keys == tuple(sorted(provision.CONTEXT_KEYS))
    assert "_execute" in one.runner.argv and "provision" not in one.runner.argv
    assert not installation.exists()


def test_explicit_client_only_recipe_is_distinct_and_reaches_native_installer(owning_payload, area):
    payload, installation, project = owning_payload
    root = area / "queue"
    ordinary = provision.enqueue(payload, installation, project, root=root)
    client = provision.enqueue(payload, installation, project, root=root, client_only=True)
    assert client.id != ordinary.id and client.spec.argv == ("--client-only",)
    drain(root=root)
    assert all(status(item.id, root=root).exit_code == 7 for item in (ordinary, client))
    assert (installation / "client-only").read_text().splitlines() == ["0", "1"]


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_nonboolean_client_only_refuses_before_writes(owning_payload, area, value):
    payload, installation, project = owning_payload
    with pytest.raises(provision.ProvisionError, match="must be a boolean"):
        provision.enqueue(payload, installation, project, root=area / "queue", client_only=value)
    assert not (area / "queue").exists() and not installation.exists()


def test_source_inspection_never_writes_payload_or_installation(owning_payload):
    payload, installation, project = owning_payload
    before = {str(path.relative_to(payload)): path.read_bytes()
              for path in payload.rglob("*") if path.is_file()}
    provision.inspect(payload, installation, project)
    assert {str(path.relative_to(payload)): path.read_bytes()
            for path in payload.rglob("*") if path.is_file()} == before
    assert not installation.exists()


@pytest.mark.parametrize("change", ["managed", "malformed", "no-self", "pinned", "manifest"])
def test_refusal_precedes_queue_and_installation_writes(owning_payload, area, monkeypatch, change):
    payload, installation, project = owning_payload
    if change in {"managed", "malformed"}:
        module = primitive(payload)
        arguments = dict(
            payload_root=payload, plugin_id="agent-vault", legacy_root=installation,
            project_root=project, environment=provision._environment(payload),
        )
        resolution = module.resolve_installation_mode(**arguments)
        owner = module._deployment_owner(resolution, arguments)
        installation.mkdir()
        value = {
            "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": owner, "managed": True,
        } if change == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(value), encoding="utf-8")
    elif change == "no-self":
        monkeypatch.setenv("AGENT_VAULT_NO_SELFPROVISION", "1")
    elif change == "pinned":
        monkeypatch.setenv("AGENT_VAULT_PINNED_CONTEXT", "")
    else:
        path = payload / "payload-invocation.json"
        manifest = json.loads(path.read_text())
        manifest["installer"] = "other"
        path.write_text(json.dumps(manifest), encoding="utf-8")
    before = {str(p.relative_to(installation)): p.read_bytes()
              for p in installation.rglob("*") if p.is_file()}
    with pytest.raises(provision.ProvisionError):
        provision.enqueue(payload, installation, project, root=area / "queue")
    assert not (area / "queue").exists()
    assert {str(p.relative_to(installation)): p.read_bytes()
            for p in installation.rglob("*") if p.is_file()} == before


def test_actual_owned_native_provision_keeps_exit_status_and_does_not_leak_ticket(
    owning_payload, area, monkeypatch,
):
    payload, installation, project = owning_payload
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    # Context absent on acceptance must not leak from the shared worker's caller.
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(area / "foreign.json"))
    monkeypatch.setenv("AGENT_VAULT_NO_SELFPROVISION", "1")
    monkeypatch.setenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "unrelated-ticket")
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 7, result.log_path.read_text()
    assert (installation / "executed").read_text() == "provision"
    frozen = Path((installation / "source-path").read_text().strip()).parent
    assert frozen == root / "provision-inputs" / request.id
    assert (frozen / "plugin.json").read_bytes() == (payload / "plugin.json").read_bytes()


def test_source_substitution_after_acceptance_never_runs_installer(owning_payload, area):
    payload, installation, project = owning_payload
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    with (payload / "scripts" / "install.ps1").open("a") as stream:
        stream.write("# changed after acceptance\n")
    with pytest.raises(ParticipantError):
        drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "interrupted" and result.exit_code is None
    assert not installation.exists()
    assert "changed after acceptance" in result.log_path.read_text()


def test_direct_execution_without_custody_cannot_provision(owning_payload):
    payload, installation, project = owning_payload
    admission = provision.inspect(payload, installation, project)
    with pytest.raises(provision.ProvisionError, match="live installation custody"):
        provision._execute(admission, root=project / "unowned-queue")
    assert not installation.exists()


def test_native_source_expectation_mismatch_does_not_create_queue(owning_payload, area):
    payload, installation, project = owning_payload
    with pytest.raises(provision.ProvisionError, match="differs from native"):
        provision.enqueue(
            payload, installation, project, root=area / "queue",
            expected_source="sha256:" + "0" * 64,
        )
    assert not (area / "queue").exists() and not installation.exists()


def test_publication_policy_changed_after_acceptance_never_runs_installer(owning_payload, area):
    payload, installation, project = owning_payload
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    installation.mkdir()
    (installation / "runtime-deployment.json").write_text('{"version":999}', encoding="utf-8")
    with pytest.raises(ParticipantError):
        drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "interrupted" and result.exit_code is None
    assert not (root / "provision-inputs").exists()
    assert sorted(path.name for path in installation.iterdir()) == ["runtime-deployment.json"]


def test_actual_submit_wait_worker_uses_the_addressed_execution_root(owning_payload, area):
    payload, installation, project = owning_payload
    root = area / "queue"
    argv, imports = provision.core.runtime_command("update_driver.first_use", [
        "submit", "--payload", str(payload), "--runtime-root", str(installation),
        "--project", str(project), "--root", str(root), "--wait",
    ])
    result = subprocess.run(
        argv, env={**os.environ, **imports}, cwd=project, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", timeout=90, **no_window_kwargs(),
    )
    assert result.returncode == 7, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    request = status(receipt["request_id"], root=root)
    assert request.state == "completed" and request.exit_code == 7
    assert (installation / "executed").read_text() == "provision"
    assert not (installation.parent / ".copilot-extensions" / "update-driver").exists()


def test_downgrade_permission_belongs_to_each_request_not_the_worker(
    owning_payload, area, monkeypatch,
):
    payload, installation, project = owning_payload
    root = area / "queue"
    ordinary = provision.enqueue(payload, installation, project, root=root)
    monkeypatch.setenv("AGENT_VAULT_ALLOW_DOWNGRADE", "1")
    permitted = provision.enqueue(payload, installation, project, root=root)
    assert ordinary.id != permitted.id
    assert "AGENT_VAULT_ALLOW_DOWNGRADE" not in dict(ordinary.spec.context)
    assert dict(permitted.spec.context)["AGENT_VAULT_ALLOW_DOWNGRADE"] == "1"
    # The shared worker carries permission, but the ordinary request must not.
    drain(root=root)
    assert (installation / "downgrade-history").read_text().splitlines() == ["[]", "[1]"]
    assert all(status(item.id, root=root).exit_code == 7 for item in (ordinary, permitted))


@pytest.mark.parametrize("witness", ["", "malformed", "{}"])
def test_nested_evidence_never_accepts_first_use(owning_payload, area, monkeypatch, witness):
    payload, installation, project = owning_payload
    root = area / "unopened"
    monkeypatch.setenv("UPDATE_DRIVER_WITNESS", witness)
    with pytest.raises(provision.ProvisionError, match="nested first-use submission"):
        provision.enqueue(payload, installation, project, root=root)
    assert not root.exists() and not installation.exists()


@pytest.mark.parametrize("same_root", [False, True])
def test_live_owner_cannot_recursively_submit_first_use(owning_payload, area, monkeypatch, same_root):
    payload, installation, project = owning_payload
    root = area / "owner"
    destination = root if same_root else area / "foreign"
    request = enqueue(
        RequestSpec("nested-admission-control"), root=root,
        runner=TrustedRunner((sys.executable, "-c", "pass"), str(project)),
    )

    def executor(transaction):
        with monkeypatch.context() as context:
            context.setenv("UPDATE_DRIVER_WITNESS", transaction.participant_env()["UPDATE_DRIVER_WITNESS"])
            with pytest.raises(provision.ProvisionError, match="nested first-use submission"):
                provision.enqueue(payload, installation, project, root=destination)
        assert len(status(root=root)) == 1
        if not same_root:
            assert not destination.exists()
        transaction.assert_owned()
        return 0

    drain(root=root, executor=executor)
    assert status(request.id, root=root).state == "completed"
    assert not installation.exists()


def test_owned_native_installer_discovers_executing_python_without_caller_path(
    owning_payload, area, monkeypatch,
):
    payload, installation, project = owning_payload
    if os.name == "nt":
        path = payload / "scripts" / "install.ps1"
        probe = (
            "$python = Get-Command python -CommandType Application -ErrorAction Stop\n"
            "& $python.Source -I -c 'import sys; print(sys.executable)' | "
            "Set-Content (Join-Path $InstallDir 'python-path')\n"
            "if ($LASTEXITCODE -ne 0) { exit 93 }\nexit 7"
        )
        monkeypatch.setenv("PATH", str(Path(os.environ["SystemRoot"]) / "System32"))
    else:
        tools = area / "native-tools"
        tools.mkdir()
        for name in ("bash", "dirname", "mkdir"):
            (tools / name).symlink_to(shutil.which(name))
        path = payload / "scripts" / "install.sh"
        probe = 'python -I -c "import sys; print(sys.executable)" >"$3/python-path"\nexit 7'
        monkeypatch.setenv("PATH", str(tools))
    assert shutil.which("python") is None
    path.write_text(path.read_text().replace("exit 7", probe), encoding="utf-8")
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    drain(root=root)
    result = status(request.id, root=root)
    assert result.exit_code == 7, result.log_path.read_text()
    assert Path((installation / "python-path").read_text().strip()).samefile(
        sys.executable,
    )


def test_scoped_nested_admission_refuses_without_ambient_witness(
    owning_payload, area, monkeypatch,
):
    payload, installation, project = owning_payload
    root = area / "owner"
    request = enqueue(
        RequestSpec("scoped-nested-admission-control"), root=root,
        runner=TrustedRunner((sys.executable, "-c", "pass"), str(project)),
    )

    def executor(transaction):
        with monkeypatch.context() as context:
            context.setenv("UPDATE_DRIVER_WITNESS", transaction.participant_env()["UPDATE_DRIVER_WITNESS"])
            custody = provision.borrowed_custody(root=root)
            with custody, custody.execution_scope():
                assert "UPDATE_DRIVER_WITNESS" not in os.environ
                with pytest.raises(provision.ProvisionError, match="nested first-use submission"):
                    provision.enqueue(payload, installation, project, root=root)
        assert len(status(root=root)) == 1
        transaction.assert_owned()
        return 0

    drain(root=root, executor=executor)
    assert status(request.id, root=root).state == "completed"
    assert not installation.exists()
