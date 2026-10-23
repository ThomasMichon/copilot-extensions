"""Fresh installed producer -> existing role consumer, without source or hosting."""

import copy
import hashlib
import json
import os
import shutil
import sys
import zipfile

import pytest

import test_client_distribution as distribution

pytestmark = pytest.mark.client_distribution_full
PROJECT = distribution.PROJECT
run = distribution.run

PRODUCE = """
import hashlib, importlib.util, json, socket, sys
from pathlib import Path
def denied(*args, **kwargs):
    raise AssertionError("assembly must not use network")
socket.socket = denied
socket.create_connection = denied
target = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(target))
import fleet_manager.release_assembly as assembly
from fleet_manager.planning import decode
assert Path(assembly.__file__).resolve().is_relative_to(target)
assert importlib.util.find_spec("cryptography") is None
assert importlib.util.find_spec("joserfc") is None
spec = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
raw = assembly.assemble_release_manifest(
    spec["components"], spec["sources"], release_id="installed-release", roles=spec["roles"])
print(json.dumps({"manifest": decode(raw),
    "producer_sha256": hashlib.sha256(Path(assembly.__file__).read_bytes()).hexdigest()}))
"""

CONSUME = (
    "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); "
    "runpy.run_module('fleet_manager.artifacts',run_name='__main__')"
)


def test_release_assembly_installed_first_touch_consumes_real_wheel_and_preserves_state(
    tmp_path, wheel,
):
    shared_artifact, shared_library = wheel
    original = shared_artifact.read_bytes()
    project, library = distribution.project_at(tmp_path / "private-build")
    output = tmp_path / "private-build" / "artifacts"
    built = distribution.build(project, output)
    assert built.returncode == 0, built.stderr
    artifact = next(output.glob("*.whl"))
    uv = shutil.which("uv")
    assert uv, "installed assembly qualification requires the runner's uv"
    target = tmp_path / "fresh-runtime"
    state = tmp_path / "client-state"
    state.mkdir()
    sentinel = state / "settings.json"
    sentinel.write_bytes(b'{"custom":"retained"}\n')
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["UV_OFFLINE"] = "1"
    installed = run([
        uv, "pip", "install", "--python", sys.executable, "--target", str(target),
        "--no-index", "--no-deps", "--link-mode", "copy", str(artifact),
    ], env=environment)
    assert installed.returncode == 0, installed.stderr
    with zipfile.ZipFile(artifact) as archive:
        expected_producer = hashlib.sha256(
            archive.read("fleet_manager/release_assembly.py")
        ).hexdigest()
    source = artifact.parent.parent / "fleet-manager"
    assert source.exists() and source.resolve() != PROJECT.resolve()
    assert library.resolve().is_relative_to(artifact.parent.parent.resolve())
    shutil.rmtree(source)
    shutil.rmtree(library)
    assert not source.exists() and not library.exists()
    assert shared_library.is_dir()
    assert shared_artifact.read_bytes() == original

    case = tmp_path / "assembly"
    case.mkdir()
    sdk = case / artifact.name
    shutil.copy2(artifact, sdk)
    ssh = case / "ssh-fixture.artifact"
    ssh.write_bytes(b"synthetic native SSH artifact; not an executable\n")
    bastion = case / "bastion-fixture.artifact"
    bastion.write_bytes(b"synthetic optional Bastion artifact; not a service\n")
    contract = {"kind": "client", "api": {"min": 1, "max": 1}, "data_schemas": [1]}
    components = {name: copy.deepcopy(contract) for name in ("cli", "gateway", "ssh", "bastion")}
    components["gateway"]["kind"] = "control"
    components["ssh"]["kind"] = "executor"
    components["bastion"]["kind"] = "control"
    sources = {
        "schema": "fleet-manager.artifact-sources", "version": 1,
        "components": {"cli": str(sdk), "gateway": str(sdk),
                       "ssh": str(ssh), "bastion": str(bastion)},
    }
    roles = {"client": ["cli"], "gateway": ["cli", "gateway", "ssh", "bastion"],
             "node": ["cli", "ssh"]}
    spec = case / "inputs.json"
    original_spec = {"components": components, "sources": sources, "roles": roles}
    spec.write_text(json.dumps(original_spec), encoding="utf-8")
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    produced = run(
        [sys.executable, "-I", "-S", "-B", "-c", PRODUCE, str(target), str(spec)],
        cwd=case, env=environment,
    )
    assert produced.returncode == 0, produced.stderr
    output = json.loads(produced.stdout)
    assert output["producer_sha256"] == expected_producer
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before
    manifest = case / "release.json"
    manifest.write_text(json.dumps(output["manifest"]), encoding="utf-8")
    source_file = case / "sources.json"
    source_file.write_text(json.dumps(sources), encoding="utf-8")

    def consume(role):
        return run([
            sys.executable, "-I", "-S", "-B", "-c", CONSUME, str(target),
            "--release", str(manifest), "--sources", str(source_file), "--role", role,
        ], cwd=case, env=environment)

    for role in roles:
        result = consume(role)
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["role"] == role and report["status"] == "integrity-match"
        assert not report["authenticity_verified"] and not report["activation_authorized"]
        assert not report["live_readiness_verified"] and not report["mutations_performed"]

    linked = tmp_path / "linked-artifacts"
    if os.name == "nt":
        result = run([
            os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", str(linked), str(case),
        ])
        assert result.returncode == 0, result.stderr
    else:
        linked.symlink_to(case, target_is_directory=True)
    try:
        changed = copy.deepcopy(original_spec)
        changed["sources"]["components"]["cli"] = str(linked / sdk.name)
        spec.write_text(json.dumps(changed), encoding="utf-8")
        refused = run(
            [sys.executable, "-I", "-S", "-B", "-c", PRODUCE, str(target), str(spec)],
            cwd=case, env=environment,
        )
        assert refused.returncode != 0 and not refused.stdout
        assert "link or reparse" in refused.stderr
    finally:
        if os.name == "nt":
            os.rmdir(linked)
        else:
            linked.unlink()
    sdk.write_bytes(b"changed after assembly")
    refused = consume("gateway")
    assert refused.returncode == 1 and not refused.stdout
    assert "digest mismatch" in refused.stderr
    removed = run([
        uv, "pip", "uninstall", "--python", sys.executable,
        "--target", str(target), "fleet-manager",
    ], env=environment)
    assert removed.returncode == 0, removed.stderr
    assert not (target / "fleet_manager").exists()
    assert sentinel.read_bytes() == b'{"custom":"retained"}\n'
    unavailable = consume("gateway")
    assert unavailable.returncode != 0
    print("FLEET_RELEASE_ASSEMBLY_PROOF " + json.dumps({
        "python": sys.version.split()[0], "platform": os.name,
        "wheel_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "wheel_bytes": artifact.stat().st_size,
        "producer_sha256": expected_producer,
        "roles_consumed": sorted(roles), "source_and_library_removed": True,
        "linked_input_refused": True, "changed_artifact_refused": True,
        "user_state_preserved": True, "package_removal_verified": True,
        "authentication_or_activation_proven": False,
    }, sort_keys=True))
