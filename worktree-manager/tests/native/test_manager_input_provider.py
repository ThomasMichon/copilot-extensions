"""Installed Manager input-provider acceptance, prepared under the existing native group."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import venv

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "libs" / "versioned-runtime"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_manager_runtime_install import _environment, _prepare_command


def _checked(command, env, cwd):
    result = _prepare_command(command, env, cwd)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result


def _associated_input(directory: Path) -> dict:
    import build_python_artifacts as builder
    import promotion_python_artifacts as artifacts
    import versioned_runtime_source as sources
    from test_promotion_python_artifacts import _wheel_bytes

    snapshot = directory / "associated"
    payload = snapshot / "source" / "worktree-manager"
    package = payload / "src" / "worktree_manager"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__="1.0.0"\n')
    (payload / "pyproject.toml").write_text(
        '[project]\nname="copilot-extensions-worktree-manager"\nversion="1.0.0"\n'
        '[project.scripts]\nworktree-manager="worktree_manager:main"\n',
    )
    digest = sources.assembly_hash(payload)
    identity = sources.SourceIdentity(
        "worktree-manager", "1.0.0", "a" * 40, "b" * 40, digest,
        sources.runtime_version("1.0.0", "a" * 40, digest),
    )
    (payload / sources.SOURCE_FILE).write_text(json.dumps(identity.document()))
    wheels = snapshot / "wheels"
    wheels.mkdir()
    wheel = wheels / "copilot_extensions_worktree_manager-1.0.0-py3-none-any.whl"
    wheel.write_bytes(_wheel_bytes("copilot_extensions_worktree_manager", "1.0.0"))
    toolchain = builder.ToolchainLock(Path("."), {
        "setuptools": "80.9.0", "wheel": "0.45.1", "packaging": "25.0",
    }, provenance={
        "source_policy": "governed", "python": "/fixture-python",
        "validated_index_identity": "sha256:" + "f" * 64,
    })
    entry = {
        **builder.parse_wheel_filename(wheel), "filename": wheel.name, "role": "standalone",
        "source": "worktree-manager", "generator": toolchain.generator,
        "sha256": "sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }
    manifest = {
        "component": "worktree-manager", "version": "1.0.0", "wheels": [entry],
        "build_toolchain": toolchain.record, "payload_hash": "sha256:" + digest,
        "build_resources": ["libs/versioned-runtime", "libs/installer-engine"],
        **builder.overall_identity_tags([entry]),
    }
    manifest["artifact_id"] = "sha256:" + builder._hash_fields(
        manifest["payload_hash"], manifest["python_tag"], manifest["abi_tag"],
        manifest["platform_tag"], toolchain.lock_id, wheel.name, entry["sha256"],
    )
    artifacts.validate_manifest(manifest)
    record = {
        "schema": "copilot-extensions.install-source", "version": 1, "kind": "source",
        "source_commit": identity.source_commit, "source_tree": identity.source_tree,
        "projects": {"worktree-manager": {
            "payload": "source/worktree-manager", "source": identity.document(),
            "wheels": "wheels", "artifact": manifest,
        }},
    }
    (snapshot / "install-source.json").write_text(json.dumps(record))
    return {"snapshot": str(snapshot), "identity": identity.document()}


def prepare(directory: Path) -> dict:
    import materialize_main
    env = _environment(directory)
    env.update(
        UV_DEFAULT_INDEX="https://provider-fixture.invalid/simple",
        BUILD_PYTHON_ARTIFACTS_TRUSTED_INDEX_HOSTS="provider-fixture.invalid",
        UV_PYTHON_DOWNLOADS="never",
    )
    for key in ("UV_NO_INDEX", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_INDEX", "UV_CONFIG_FILE", "UV_OFFLINE"):
        env.pop(key, None)
    build = directory / "build"
    project = build / "worktree-manager"
    shutil.copytree(REPO / "worktree-manager", project, ignore=shutil.ignore_patterns(
        "__pycache__", "build", "*.egg-info", "tests",
    ))
    for name in ("versioned-runtime", "installer-engine"):
        shutil.copytree(REPO / "libs" / name, build / "libs" / name, ignore=shutil.ignore_patterns(
            "__pycache__", "build", "*.egg-info", "tests",
        ))
    report = materialize_main.materialize(build, canonical_root=REPO)
    assert not [line for line in report if line.startswith("SKIP")], report
    uv = Path(shutil.which("uv"))
    wheels = directory / "wheel-output"
    _checked([
        uv, "build", "--wheel", "--no-build-isolation", "--offline",
        "--python", sys.executable, "--out-dir", wheels, project,
    ], env, directory)
    wheel = next(wheels.glob("copilot_extensions_worktree_manager-*.whl"))
    installed = directory / "installed"
    venv.EnvBuilder(with_pip=False).create(installed)
    python = installed / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _checked([uv, "pip", "install", "--offline", "--no-deps", "--python", python, wheel], env, directory)
    inputs = _associated_input(directory)
    shutil.rmtree(build)
    output = directory / "complete"
    command = [
        python, "-I", "-B", "-c",
        "import json,pathlib,sys,time; "
        "from worktree_manager import runtime_source_prepare as provider; "
        "root=pathlib.Path(sys.prefix); "
        "assert pathlib.Path(provider.__file__).resolve().is_relative_to(root); "
        "data=json.loads(sys.argv[1]); "
        "result=provider.prepare_application_inputs("
        "pathlib.Path(data['snapshot']),pathlib.Path(sys.argv[2]),identity=data['identity'],"
        "python=pathlib.Path(sys.argv[3]),tooling_python=pathlib.Path(sys.argv[3]),"
        "uv=pathlib.Path(sys.argv[4]),deadline=time.monotonic()+180); "
        "print(json.dumps({'result':result,'module':provider.__file__,'prefix':sys.prefix}))",
        json.dumps(inputs), output, sys.executable, uv,
    ]
    result = _checked(command, env, directory)
    observed = json.loads(result.stdout)
    assert not build.exists()
    snapshot = Path(observed["result"]["snapshot"])
    assert (snapshot / "applications" / "worktree-manager" / "uv.lock").is_file()
    assert not (snapshot / "applications" / "worktree-manager" / ".venv").exists()
    before = (snapshot / "install-source.json").read_bytes()
    refusal = command.copy()
    wrong = {**inputs, "identity": {**inputs["identity"], "source_commit": "c" * 40}}
    refusal[-4:] = [json.dumps(wrong), directory / "wrong-source", sys.executable, uv]
    failed = _prepare_command(refusal, env, directory)
    assert failed.returncode and "differs from its admitted immutable source" in failed.stderr
    assert not (directory / "wrong-source").exists()
    archive = next((Path(inputs["snapshot"]) / "wheels").glob("*.whl"))
    archive.write_bytes(b"corrupt fixture archive")
    refusal[-4:] = [json.dumps(inputs), directory / "corrupt-input", sys.executable, uv]
    failed = _prepare_command(refusal, env, directory)
    assert failed.returncode and "Manager complete-input preparation failed" in failed.stderr
    assert not (directory / "corrupt-input").exists()
    assert (snapshot / "install-source.json").read_bytes() == before
    assert not (directory / "home" / ".worktree-manager").exists()
    return {
        **observed, "build_checkout_removed": True, "complete_snapshot": str(snapshot),
        "provider_wheel": str(wheel), "runtime_activated": False,
        "wrong_source_refused": True, "corrupt_wheel_refused": True,
        "completed_output_preserved": True,
    }


def test_installed_wheel_completes_native_profile_after_build_checkout_removed():
    data = json.loads(Path(os.environ["MANAGER_INPUT_PROVIDER_DATA"]).read_text())
    assert data["build_checkout_removed"] and not data["runtime_activated"]
    assert data["wrong_source_refused"] and data["corrupt_wheel_refused"]
    assert data["completed_output_preserved"]
    assert Path(data["module"]).resolve().is_relative_to(Path(data["prefix"]).resolve())
    assert (Path(data["complete_snapshot"]) / "applications/worktree-manager/uv.lock").is_file()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", type=Path, required=True)
    destination = parser.parse_args().prepare
    data = prepare(destination)
    (destination / "fixtures.json").write_text(json.dumps(data, indent=2))
