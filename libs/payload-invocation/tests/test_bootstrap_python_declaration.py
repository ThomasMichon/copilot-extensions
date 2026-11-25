"""Static helper declarations do not select application interpreters."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

_spec = importlib.util.spec_from_file_location(
    "_bootstrap_declaration_generator", Path(__file__).resolve().parents[1] / "generate.py",
)
assert _spec and _spec.loader
generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generator)


def _declare(tmp_path: Path, version: object, requirement: str = ">=3.11,<3.13") -> Path:
    scripts = tmp_path / "plugin/scripts"
    scripts.mkdir(parents=True)
    for name in ("install.sh", "install.ps1", "resolve-runtime.sh", "resolve-runtime.ps1"):
        (scripts / name).write_text("# fixture\n", encoding="utf-8")
    manifest = scripts.parent / "payload-invocation.json"
    data = {
        "schema": "copilot-extensions.payload-invocation", "version": 1,
        "command": "agent-example", "module": "agent_example", "runtimeRoot": ".agent-example",
        "noSelfProvisionEnv": "AGENT_EXAMPLE_NO_SELFPROVISION", "purpose": "Example runtime",
        "bootstrapPython": version,
    }
    manifest.write_text(json.dumps(data), encoding="utf-8")
    (manifest.parent / "pyproject.toml").write_text(
        '[project]\nname = "agent-example"\nversion = "1"\n'
        f"requires-python = {json.dumps(requirement)}\n",
        encoding="utf-8",
    )
    return manifest


def test_static_bootstrap_declaration_does_not_change_launchers(tmp_path: Path) -> None:
    manifest = _declare(tmp_path, "3.11.11")
    outputs = generator.expected_files(manifest)
    declaration = manifest.parent / "scripts/bootstrap-python-version.txt"
    assert outputs.pop(declaration) == "cpython@3.11.11\n"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    del data["bootstrapPython"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert generator.expected_files(manifest) == outputs


@pytest.mark.parametrize("launcher", [None, True, "unknown", "uv"])
def test_incomplete_managed_bootstrap_refuses_before_writes(tmp_path: Path, launcher: object) -> None:
    manifest = _declare(tmp_path, "3.11.18")
    data = json.loads(manifest.read_text())
    data.pop("bootstrapPython")
    data["bootstrapLauncher"] = launcher
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="bootstrapLauncher|bootstrap declaration"):
        generator.process_manifest(manifest, check=False)
    assert not (manifest.parent / "bin").exists()


@pytest.mark.parametrize("keep_declaration", [False, True])
def test_withdrawn_managed_launcher_refuses_before_writes(
    tmp_path: Path, keep_declaration: bool,
) -> None:
    manifest = _declare(tmp_path, "3.11.18")
    data = json.loads(manifest.read_text())
    data["bootstrapLauncher"] = "uv"
    manifest.write_text(json.dumps(data))
    assert generator.process_manifest(manifest, check=False) == []
    del data["bootstrapLauncher"]
    if not keep_declaration:
        del data["bootstrapPython"]
        (manifest.parent / "scripts/bootstrap-python-version.txt").unlink()
    manifest.write_text(json.dumps(data))
    before = {p: p.read_bytes() for p in manifest.parent.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="obsolete bootstrap launcher"):
        generator.process_manifest(manifest, check=False)
    assert before == {p: p.read_bytes() for p in manifest.parent.rglob("*") if p.is_file()}


@pytest.mark.parametrize("check", [False, True])
def test_withdrawn_request_is_rejected_by_generation_and_check(tmp_path: Path, check: bool) -> None:
    manifest = _declare(tmp_path, "3.11.11")
    assert generator.process_manifest(manifest, check=False) == []
    data = json.loads(manifest.read_text())
    del data["bootstrapPython"]
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="obsolete bootstrap Python request"):
        generator.process_manifest(manifest, check=check)
    (manifest.parent / "scripts/bootstrap-python-version.txt").unlink()
    assert generator.process_manifest(manifest, check=check) == []


@pytest.mark.parametrize("version", [
    None, True, "3.11", "pypy@3.11.11", "3.13.1t", "3.11.11\n", "../python", "--system",
])
def test_invalid_bootstrap_requests_refuse_before_writes(tmp_path: Path, version: object) -> None:
    manifest = _declare(tmp_path, version)
    with pytest.raises(ValueError, match="exact stable CPython"):
        generator.process_manifest(manifest, check=False)
    assert not (manifest.parent / "bin").exists()
    assert not (manifest.parent / "scripts/bootstrap-python-version.txt").exists()


@pytest.mark.parametrize("requirement", ["", "not-a-specifier", ">=3.12", ">=3.11,!=3.11.11"])
def test_helper_declaration_does_not_interpret_application_constraints(tmp_path: Path, requirement: str) -> None:
    manifest = _declare(tmp_path, "3.11.11", requirement)
    outputs = generator.expected_files(manifest)
    assert outputs[manifest.parent / "scripts/bootstrap-python-version.txt"] == "cpython@3.11.11\n"
    assert not (manifest.parent / "bin").exists()


@pytest.mark.parametrize("metadata", [None, "[project]\nname='example'\n", "[project\n", "project='invalid'\n"])
def test_helper_declaration_precedes_application_metadata(tmp_path: Path, metadata: str | None) -> None:
    manifest = _declare(tmp_path, "3.11.11")
    project = manifest.parent / "pyproject.toml"
    if metadata is None:
        project.unlink()
    else:
        project.write_text(metadata, encoding="utf-8")
    outputs = generator.expected_files(manifest)
    assert outputs[manifest.parent / "scripts/bootstrap-python-version.txt"] == "cpython@3.11.11\n"
    assert not (manifest.parent / "bin").exists()


@pytest.mark.parametrize("operation", ["planning", "checking", "refusal"])
def test_planning_and_refusal_do_not_write_bytecode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    tooling = tmp_path / "tooling/libs/payload-invocation"
    tooling.mkdir(parents=True)
    for name in (
        "generate.py", "runtime_admission.py", "bootstrap_python.py",
        "template_rendering.py", "command_evidence.py",
    ):
        shutil.copyfile(Path(__file__).resolve().parents[1] / name, tooling / name)
    spec = importlib.util.spec_from_file_location("_private_bootstrap_generator", tooling / "generate.py")
    assert spec and spec.loader
    copied = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as setup:
        setup.setattr(sys, "dont_write_bytecode", True)
        spec.loader.exec_module(copied)
    manifest = _declare(tmp_path, "3.11" if operation == "refusal" else "3.11.11")
    with monkeypatch.context() as enabled:
        enabled.setattr(sys, "dont_write_bytecode", False)
        enabled.setattr(sys, "pycache_prefix", None)
        if operation == "refusal":
            with pytest.raises(ValueError, match="exact stable CPython"):
                copied.source_check(manifest, canonical_root=generator.REPO)
        elif operation == "checking":
            assert copied.process_manifest(manifest, check=True, canonical_root=generator.REPO)
        else:
            assert copied.source_check(manifest, canonical_root=generator.REPO) == []
        assert sys.dont_write_bytecode is False
    assert not list(tooling.rglob("__pycache__"))


@pytest.mark.parametrize("field", ["catalogGate", "payloadDispatcher"])
@pytest.mark.parametrize("enabled", [False, True])
def test_declared_inputs_are_preserved_or_refused_before_writes(
    tmp_path: Path, field: str, enabled: bool,
) -> None:
    manifest = _declare(tmp_path, "3.11.11")
    destination = manifest.parent / "scripts/bootstrap-python-version.txt"
    destination.write_text("# existing declared input\n")
    data = json.loads(manifest.read_text())
    if not enabled:
        del data["bootstrapPython"]
    relative = "scripts/bootstrap-python-version.txt"
    data[field] = {"posix": relative, "windows": relative} if field == "payloadDispatcher" else relative
    manifest.write_text(json.dumps(data))
    before = {p.relative_to(manifest.parent): p.read_bytes()
              for p in manifest.parent.rglob("*") if p.is_file()}
    if enabled:
        with pytest.raises(ValueError, match="conflicts with a declared input"):
            generator.process_manifest(manifest, check=False)
        assert before == {p.relative_to(manifest.parent): p.read_bytes()
                          for p in manifest.parent.rglob("*") if p.is_file()}
    else:
        assert generator.process_manifest(manifest, check=False) == []
    assert destination.read_text() == "# existing declared input\n"


def test_declared_input_alias_is_not_misidentified_as_a_withdrawn_scalar(tmp_path: Path) -> None:
    manifest = _declare(tmp_path, "3.11.11")
    source = manifest.parent / "scripts/input-gate.txt"
    source.write_text("# existing aliased input\n")
    destination = source.with_name("bootstrap-python-version.txt")
    destination.hardlink_to(source)
    data = json.loads(manifest.read_text())
    del data["bootstrapPython"]
    data["catalogGate"] = "scripts/input-gate.txt"
    manifest.write_text(json.dumps(data))
    assert generator.process_manifest(manifest, check=False) == []
    assert source.read_text() == destination.read_text() == "# existing aliased input\n"


def test_legacy_output_directory_is_not_misidentified_as_a_withdrawn_scalar(tmp_path: Path) -> None:
    manifest = _declare(tmp_path, "3.11.11")
    destination = manifest.parent / "scripts/bootstrap-python-version.txt"
    destination.mkdir()
    data = json.loads(manifest.read_text())
    del data["bootstrapPython"]
    data["outputDir"] = "scripts/bootstrap-python-version.txt"
    manifest.write_text(json.dumps(data))
    assert generator.process_manifest(manifest, check=False) == []
    assert (destination / "agent-example.ps1").is_file()


@pytest.mark.parametrize("value,launcher,writer", [
    ("true", "uv", True), (True, "direct", True), (True, "uv", False),
])
def test_incomplete_guidance_adoption_refuses_before_writes(
    tmp_path: Path, value: object, launcher: str, writer: bool,
) -> None:
    manifest = _declare(tmp_path, "3.11.18")
    data = json.loads(manifest.read_text())
    data.update(powershellGuidance=value, bootstrapLauncher=launcher)
    manifest.write_text(json.dumps(data))
    if writer:
        (manifest.parent / "scripts/write_session_guidance.py").write_text("print('{}')\n")
    before = {p: p.read_bytes() for p in manifest.parent.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="powershellGuidance"):
        generator.process_manifest(manifest, check=False)
    assert before == {p: p.read_bytes() for p in manifest.parent.rglob("*") if p.is_file()}


def test_guidance_generation_and_check_preserve_nonadopters(tmp_path: Path) -> None:
    manifest = _declare(tmp_path, "3.11.18")
    wrapper = manifest.parent / "scripts/write-session-guidance.ps1"
    wrapper.write_text("# custom guidance\n")
    data = json.loads(manifest.read_text())
    data["bootstrapLauncher"] = "uv"
    manifest.write_text(json.dumps(data))
    assert generator.process_manifest(manifest, check=False) == []
    assert wrapper.read_text() == "# custom guidance\n"
    data["powershellGuidance"] = True
    manifest.write_text(json.dumps(data))
    (manifest.parent / "scripts/write_session_guidance.py").write_text("print('{}')\n")
    assert generator.process_manifest(manifest, check=False) == []
    assert generator.process_manifest(manifest, check=True) == []
    wrapper.write_text("# drift\n")
    assert generator.process_manifest(manifest, check=True)
    assert wrapper.read_text() == "# drift\n"


@pytest.mark.parametrize("value,launcher,writer", [
    ("true", "uv", True), (True, "direct", True), (True, "uv", False),
])
def test_incomplete_posix_guidance_refuses_before_writes(
    tmp_path: Path, value: object, launcher: str, writer: bool,
) -> None:
    manifest = _declare(tmp_path, "3.11.18")
    data = json.loads(manifest.read_text())
    data.update(posixGuidance=value, bootstrapLauncher=launcher)
    manifest.write_text(json.dumps(data))
    if writer:
        (manifest.parent / "scripts/write_session_guidance.py").write_text("print('{}')\n")
    before = {p: p.read_bytes() for p in manifest.parent.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="posixGuidance"):
        generator.process_manifest(manifest, check=False)
    assert before == {p: p.read_bytes() for p in manifest.parent.rglob("*") if p.is_file()}


def test_posix_guidance_packages_existing_process_owners(tmp_path: Path) -> None:
    manifest = _declare(tmp_path, "3.11.18")
    data = json.loads(manifest.read_text())
    data.update(posixGuidance=True, bootstrapLauncher="uv")
    manifest.write_text(json.dumps(data))
    (manifest.parent / "scripts/write_session_guidance.py").write_text("print('{}')\n")
    assert generator.process_manifest(manifest, check=False) == []
    support = manifest.parent / "scripts/_bootstrap_python"
    (support / "native_owned.sh").unlink()
    assert generator.process_manifest(manifest, check=True)
    assert not (support / "native_owned.sh").exists()
    assert generator.process_manifest(manifest, check=False) == []
    assert generator.process_manifest(manifest, check=True) == []
