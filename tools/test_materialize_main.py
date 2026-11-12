"""Tests for tools/materialize_main.py -- the whole-repo pointer materializer."""
from __future__ import annotations

import json
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import threading
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer_engine_ref as ier
import launch_wrapper_assets_ref as lwar
import materialize_main as mm
import materialize_payload_invocation as mpiv
import uv_editable_ref as uer


def _invocation_candidate(tmp_path: Path) -> tuple[Path, Path]:
    repo = Path(__file__).resolve().parents[1]
    canonical, candidate = tmp_path / "canonical", tmp_path / "candidate"
    shutil.copytree(
        repo / "libs/payload-invocation", canonical / "libs/payload-invocation",
        ignore=shutil.ignore_patterns("tests", "__pycache__"),
    )
    tools = canonical / "tools"
    tools.mkdir()
    entries = (
        "runtime_profile_policy", "runtime_source_build", "runtime_source_reuse",
        "runtime_marketplace_source", "artifact_release_discovery", "runtime_profile_delivery",
    )
    for name in entries:
        (tools / (name + ".py")).write_text(
            "import support\nimport promotion_unit_contract\n" if name == entries[0] else "VALUE = 1\n",
            newline="\n",
        )
    (tools / "promotion_unit_contract.py").write_text("VALUE = 'version-stamped canonical'\n", newline="\n")
    (canonical / "worktree-manager/src").mkdir(parents=True)
    support = canonical / "libs/support"
    support.mkdir()
    (support / "support.py").write_text("VALUE = 1\n", newline="\n")
    (support / "caller.pyz").write_bytes(b"PK\x00\xffcanonical binary\n")
    for name in (
        "libs/agent-procutil/setup.py", "libs/versioned-runtime/standalone_build.py",
        "plugins/agent-containers/setup.py",
    ):
        path = canonical / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("pass\n", newline="\n")
    plugin = canonical / "plugins/agent-worktrees"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(repo / "plugins/agent-worktrees/payload-invocation.json", plugin / "payload-invocation.json")
    for name in ("install.sh", "install.ps1", "resolve-runtime.sh", "resolve-runtime.ps1"):
        (scripts / name).write_text("# owned fixture\n")
    for name in ("invoke-payload-runtime.sh", "invoke-payload-runtime.ps1"):
        shutil.copyfile(repo / "plugins/agent-worktrees/scripts" / name, scripts / name)
    primitive = scripts / "installation-context/installation_context.py"
    primitive.parent.mkdir()
    primitive.write_text("pass\n", newline="\n")
    (plugin / "pyproject.toml").write_text('[project]\nname = "agent-worktrees"\nversion = "1"\n')
    roster = canonical / ".github/plugin/marketplace.json"
    roster.parent.mkdir(parents=True)
    roster.write_text(json.dumps({"plugins": [{"name": "agent-worktrees", "source": "plugins/agent-worktrees"}]}))
    telemetry = canonical / "libs/update-telemetry/src/update_telemetry"
    telemetry.mkdir(parents=True)
    for name in ("human.py", "redact.py", "capabilities.py"):
        shutil.copyfile(repo / "libs/update-telemetry/src/update_telemetry" / name, telemetry / name)
    shutil.copytree(plugin, candidate / "plugins/agent-worktrees")
    return canonical, candidate


@pytest.mark.parametrize("stale", [False, True])
def test_invocation_promotion_rebuilds_canonical_helpers_and_toolkit(tmp_path: Path, stale: bool) -> None:
    canonical, candidate = _invocation_candidate(tmp_path)
    plugin = candidate / "plugins/agent-worktrees"
    if stale:
        for name in ("scripts/command_binding.py", "scripts/_runtime_admission/tools/promotion_unit_contract.py"):
            target = plugin / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"obsolete derived copy\n")
        (plugin / "scripts/_runtime_admission/toolkit.json").write_bytes(b"obsolete inventory\n")
    unrelated = plugin / "unrelated.txt"
    unrelated.write_bytes(b"preserve")
    before = {p.relative_to(canonical): p.read_bytes() for p in canonical.rglob("*") if p.is_file()}
    log = mpiv.materialize(candidate, canonical_root=canonical)
    assert log and not any(line.startswith("SKIP") for line in log), log
    assert (plugin / "scripts/command_binding.py").read_bytes() == (
        canonical / "libs/payload-invocation/command_binding.py"
    ).read_bytes()
    assert (plugin / "scripts/_runtime_admission/tools/promotion_unit_contract.py").read_bytes() == (
        canonical / "tools/promotion_unit_contract.py"
    ).read_bytes()
    kit = plugin / "scripts/_runtime_admission"
    assert (kit / "libs/support/caller.pyz").read_bytes() == b"PK\x00\xffcanonical binary\n"
    assert "tools/promotion_unit_contract.py" in json.loads((kit / "toolkit.json").read_text())["files"]
    assert unrelated.read_bytes() == b"preserve"
    assert before == {p.relative_to(canonical): p.read_bytes() for p in canonical.rglob("*") if p.is_file()}
    if os.name != "nt":
        assert os.access(plugin / "scripts/emit-command-catalog.sh", os.X_OK)


def test_invocation_promotion_refuses_foreign_manifest_before_writes(tmp_path: Path) -> None:
    canonical, candidate = _invocation_candidate(tmp_path)
    manifest = candidate / "plugins/agent-worktrees/payload-invocation.json"
    manifest.write_bytes(manifest.read_bytes() + b"\n")
    assert mpiv.materialize(candidate, canonical_root=canonical)[0].startswith("SKIP")
    assert not (manifest.parent / "scripts/command_binding.py").exists()


def _invocation_preparation_source(tmp_path: Path) -> Path:
    canonical, _candidate = _invocation_candidate(tmp_path)
    consumer = canonical / "plugins/sample"
    shutil.copytree(canonical / "plugins/agent-worktrees/scripts", consumer / "scripts")
    (consumer / "pyproject.toml").write_text('[project]\nname = "sample"\nversion = "1"\n')
    (consumer / "payload-invocation.json").write_text(json.dumps({
        "schema": "copilot-extensions.payload-invocation", "version": 1,
        "command": "sample", "module": "sample.cli", "purpose": "Sample command",
        "runtimeRoot": ".sample", "noSelfProvisionEnv": "SAMPLE_NO_PROVISION",
    }))
    return canonical


@pytest.mark.parametrize("bundle", [
    "scripts/runtime-source-tools", "scripts/runtime-input-provider/tools",
])
def test_packaged_manager_preparer_imports_in_isolated_python(tmp_path: Path, bundle: str) -> None:
    repo = Path(__file__).resolve().parents[1]
    source, payload = _invocation_preparation_source(tmp_path), tmp_path / "payload"
    consumer = source / "worktree-manager"
    consumer.mkdir(parents=True, exist_ok=True)
    manifest = repo / "worktree-manager/packaged-assets.json"
    shutil.copyfile(manifest, consumer / manifest.name)
    shutil.copyfile(repo / "worktree-manager/launch-wrapper-assets.json",
                    consumer / "launch-wrapper-assets.json")
    declaration = json.loads(manifest.read_text())
    for group in declaration.get("groups", [declaration]):
        for name in group["files"]:
            target = source / group["canonicalDir"] / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo / group["canonicalDir"] / name, target)
    shutil.copytree(
        repo / "worktree-manager/scripts", payload / "scripts",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    log = mm.materialize_launch_wrapper_assets_into(
        source_consumer_dir=consumer, dest_consumer_dir=payload, canonical_root=source,
    )
    assert log and not any(line.startswith("SKIP") for line in log), log
    shutil.copyfile(repo / "worktree-manager/setup.py", payload / "setup.py")
    builder = payload.parent / "libs/versioned-runtime/standalone_build.py"
    builder.parent.mkdir(parents=True)
    shutil.copyfile(repo / "libs/versioned-runtime/standalone_build.py", builder)
    for name in ("generate.py", "runtime_admission.py"):
        (source / "libs/payload-invocation" / name).write_text(
            'raise RuntimeError("candidate tooling must never execute")\n',
        )
    before = {path.relative_to(source): path.read_bytes()
              for path in source.rglob("*") if path.is_file()}
    script = """
import pathlib, sys
payload, source, output = map(pathlib.Path, sys.argv[1:4])
sys.path.insert(0, str(payload / sys.argv[4]))
import prepare_runtime_source
prepare_runtime_source._materialize(source, "sample", output)
assert b"sample.cli" in (output / "bin/sample.ps1").read_bytes()
sys.path.insert(0, str(payload / "scripts"))
import bootstrap_inputs, runpy
from types import ModuleType
assets = bootstrap_inputs._preparation_assets(source / "worktree-manager")
backend = ModuleType("setuptools")
observed = {}
backend.setup = lambda **kwargs: observed.update(kwargs)
sys.modules["setuptools"] = backend
run = runpy.run_path
def selected_helper(path, *args, **kwargs):
    assert pathlib.Path(path) == payload.parent / "libs/versioned-runtime/standalone_build.py"
    helpers = run(path, *args, **kwargs)
    def commands(project, package, canonical, *, companion_resources):
        assert pathlib.Path(project) == payload and package == "worktree_manager"
        assert pathlib.Path(canonical) == pathlib.Path(path).parent
        return {"resources": companion_resources}
    helpers["commands"] = commands
    return helpers
runpy.run_path = selected_helper
run(str(payload / "setup.py"))
resources = observed["cmdclass"]["resources"]
for directory in ("scripts/libs/payload-invocation",
                  "scripts/runtime-input-provider/libs/payload-invocation"):
    for name in ("generate.py", "runtime_admission.py"):
        assert assets[pathlib.Path(directory) / name] == (
            source / "libs/payload-invocation" / name
        )
        assert "_runtime_assets/" + directory + "/" + name in resources
print("copied-manager-generation-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(payload), str(source),
         str(tmp_path / "prepared"), bundle],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "copied-manager-generation-ok"
    assert before == {path.relative_to(source): path.read_bytes()
                      for path in source.rglob("*") if path.is_file()}


def test_copied_invocation_toolkit_prepares_source_with_pinned_generators(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    canonical = _invocation_preparation_source(tmp_path)
    spec = importlib.util.spec_from_file_location(
        "_copied_preparer_packager", repo / "libs/payload-invocation/runtime_admission.py",
    )
    assert spec and spec.loader
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    toolkit = tmp_path / "payload/scripts/_runtime_admission"
    for relative, content in packager.expected_files(repo).items():
        path = toolkit / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    for name in ("generate.py", "runtime_admission.py"):
        (canonical / "libs/payload-invocation" / name).write_text(
            'raise RuntimeError("candidate tooling must never execute")\n',
        )
    before = {path.relative_to(canonical): path.read_bytes()
              for path in canonical.rglob("*") if path.is_file()}
    script = """
import pathlib, shutil, sys
toolkit, source, output = map(pathlib.Path, sys.argv[1:])
sys.path.insert(0, str(toolkit.parent))
import _runtime_admission
_runtime_admission.activate()
import prepare_runtime_source
assert pathlib.Path(prepare_runtime_source.__file__).is_relative_to(toolkit)
prepare_runtime_source._materialize(source, "sample", output)
assert b"sample.cli" in (output / "bin/sample.ps1").read_bytes()
assert (output / "scripts/emit-command-catalog.sh").is_file()
import materialize_payload_invocation
consumer = source / "plugins/agent-worktrees"
copied = output.parent / "copied/agent-worktrees"
shutil.copytree(consumer, copied)
log = materialize_payload_invocation.materialize(copied, canonical_root=source, source_consumer=consumer)
assert log and not any(line.startswith("SKIP") for line in log), log
assert (copied / "scripts/_runtime_admission/libs/payload-invocation/runtime_admission.py").is_file()
print("copied-toolkit-preparation-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(toolkit), str(canonical), str(tmp_path / "prepared")],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "copied-toolkit-preparation-ok"
    assert before == {path.relative_to(canonical): path.read_bytes()
                      for path in canonical.rglob("*") if path.is_file()}


def test_invocation_promotion_uses_the_version_stamped_candidate_root(tmp_path: Path) -> None:
    canonical, _candidate = _invocation_candidate(tmp_path)
    before = {p.relative_to(canonical): p.read_bytes() for p in canonical.rglob("*") if p.is_file()}
    log = mpiv.materialize(canonical, canonical_root=canonical)
    assert log and not any(line.startswith("SKIP") for line in log), log
    assert all((canonical / name).read_bytes() == raw for name, raw in before.items())
    assert (canonical / "plugins/agent-worktrees/scripts/_runtime_admission/tools/promotion_unit_contract.py").read_bytes() == (
        canonical / "tools/promotion_unit_contract.py"
    ).read_bytes()


def test_invocation_promotion_refuses_escaping_output(tmp_path: Path) -> None:
    canonical, candidate = _invocation_candidate(tmp_path)
    for root in (canonical, candidate):
        manifest = root / "plugins/agent-worktrees/payload-invocation.json"
        data = json.loads(manifest.read_text())
        data["outputDir"] = "../../foreign"
        manifest.write_text(json.dumps(data))
    assert mpiv.materialize(candidate, canonical_root=canonical)[0].startswith("SKIP")
    assert not (candidate / "foreign").exists()


def test_invocation_promotion_refuses_linked_destination(tmp_path: Path, native_file_symlink) -> None:
    canonical, candidate = _invocation_candidate(tmp_path)
    foreign = tmp_path / "foreign.py"
    foreign.write_bytes(b"foreign")
    target = candidate / "plugins/agent-worktrees/scripts/command_binding.py"
    native_file_symlink(target, foreign)
    assert mpiv.materialize(candidate, canonical_root=canonical)[0].startswith("SKIP")
    assert foreign.read_bytes() == b"foreign"


def test_materialize_calls_invocation_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(mm.mie, "validate_human_output_sources", lambda _root: frozenset())

    def materialize(dest: Path, *, canonical_root: Path) -> list[str]:
        calls.append((dest, canonical_root))
        return ["MATERIALIZE Invocation fixture"]

    monkeypatch.setattr(mm.mpiv, "materialize", materialize)
    assert "MATERIALIZE Invocation fixture" in mm.materialize(tmp_path, canonical_root=tmp_path)
    assert calls == [(tmp_path, tmp_path)]


def _invocation_generator(canonical: Path):
    tooling_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "_invocation_phase_fixture", tooling_root / "libs/payload-invocation/generate.py",
    )
    assert spec and spec.loader
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    return generator


@pytest.mark.parametrize("stale", [False, True])
def test_invocation_source_check_accepts_stale_or_absent_outputs(tmp_path: Path, stale: bool) -> None:
    canonical, candidate = _invocation_candidate(tmp_path)
    manifest = candidate / "plugins/agent-worktrees/payload-invocation.json"
    if stale:
        assert not any(line.startswith("SKIP") for line in mpiv.materialize(candidate, canonical_root=canonical))
    helper = canonical / "libs/payload-invocation/command_binding.py"
    helper.write_bytes(helper.read_bytes() + b"\n# canonical source update\n")
    (canonical / "tools/promotion_unit_contract.py").write_text("VALUE = 'new canonical contract'\n")
    generator = _invocation_generator(canonical)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert generator.source_check(manifest, canonical_root=canonical) == []
    assert generator.process_manifest(manifest, check=True, canonical_root=canonical)
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert not any(line.startswith("SKIP") for line in mpiv.materialize(candidate, canonical_root=canonical))
    assert generator.process_manifest(manifest, check=True, canonical_root=canonical) == []
    assert all(
        generator.check_generated(path, value, exact_bytes=True) == []
        for path, value in generator.expected_files(manifest, canonical_root=canonical).items()
    )


@pytest.mark.parametrize("kind", ["text", "binary"])
def test_invocation_materialized_equality_refuses_byte_drift(tmp_path: Path, kind: str) -> None:
    canonical, candidate = _invocation_candidate(tmp_path)
    assert not any(line.startswith("SKIP") for line in mpiv.materialize(candidate, canonical_root=canonical))
    generator = _invocation_generator(canonical)
    manifest = candidate / "plugins/agent-worktrees/payload-invocation.json"
    name = "tools/promotion_unit_contract.py" if kind == "text" else "libs/support/caller.pyz"
    target = manifest.parent / "scripts/_runtime_admission" / name
    expected = generator.expected_files(manifest, canonical_root=canonical)[target]
    raw = target.read_bytes()
    target.write_bytes(raw.replace(b"\n", b"\r\n") if kind == "text" else raw + b"\x00")
    assert generator.check_generated(target, expected, exact_bytes=True)
    assert generator.source_check(manifest, canonical_root=canonical) == []


def test_invocation_source_check_refuses_invalid_python(tmp_path: Path) -> None:
    canonical, candidate = _invocation_candidate(tmp_path)
    (canonical / "libs/payload-invocation/command_binding.py").write_text("def broken(\n")
    generator = _invocation_generator(canonical)
    assert any("invalid canonical generated Python" in problem for problem in generator.source_check(
        candidate / "plugins/agent-worktrees/payload-invocation.json", canonical_root=canonical,
    ))


def test_invocation_source_check_refuses_linked_destination(tmp_path: Path, native_file_symlink) -> None:
    canonical, candidate = _invocation_candidate(tmp_path)
    foreign = tmp_path / "foreign.py"
    foreign.write_bytes(b"foreign")
    target = candidate / "plugins/agent-worktrees/scripts/command_binding.py"
    native_file_symlink(target, foreign)
    with pytest.raises(ValueError, match="unsafe custody"):
        _invocation_generator(canonical).source_check(
            target.parent.parent / "payload-invocation.json", canonical_root=canonical,
        )
    assert foreign.read_bytes() == b"foreign"


def test_invocation_source_cli_keeps_check_and_source_modes_distinct(tmp_path: Path) -> None:
    canonical, candidate = _invocation_candidate(tmp_path)
    generator = _invocation_generator(canonical)
    manifest = str(candidate / "plugins/agent-worktrees/payload-invocation.json")
    assert generator.main([manifest, "--source-check"], canonical_root=canonical) == 0
    assert generator.main([manifest, "--check"], canonical_root=canonical) == 1
    with pytest.raises(SystemExit) as failure:
        generator.main([manifest, "--check", "--source-check"])
    assert failure.value.code == 2


def test_invocation_materializer_refuses_corrupt_writer_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical, candidate = _invocation_candidate(tmp_path)
    write_text = Path.write_text

    def corrupt(path: Path, content: str, *args, **kwargs):
        result = write_text(path, content, *args, **kwargs)
        if path.name == "command_binding.py":
            path.write_bytes(b"corrupt writer result\n")
        return result

    monkeypatch.setattr(Path, "write_text", corrupt)
    log = mpiv.materialize(candidate, canonical_root=canonical)
    assert log[0].startswith("SKIP") and "generated content is stale" in log[0]


def test_invocation_materializer_never_executes_candidate_tooling(tmp_path: Path) -> None:
    canonical, candidate = _invocation_candidate(tmp_path)
    marker = tmp_path / "candidate-tool-executed"
    for name in ("generate.py", "runtime_admission.py"):
        path = canonical / "libs/payload-invocation" / name
        path.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
            "raise AssertionError('candidate Python tooling executed')\n",
        )
    log = mpiv.materialize(candidate, canonical_root=canonical)
    assert log and not any(line.startswith("SKIP") for line in log), log
    assert not marker.exists()
    assert (candidate / "plugins/agent-worktrees/scripts/_runtime_admission/tools/promotion_unit_contract.py").read_bytes() == (
        canonical / "tools/promotion_unit_contract.py"
    ).read_bytes()


def test_actual_manager_manifest_materializes_without_a_bridge_test_pointer(tmp_path):
    root = Path(__file__).resolve().parents[1]
    manager = tmp_path / "worktree-manager"
    manager.mkdir()
    shutil.copyfile(root / "worktree-manager" / "pyproject.toml", manager / "pyproject.toml")
    shutil.copytree(root / "worktree-manager" / "vendor", manager / "vendor")
    log = mm.materialize(tmp_path, canonical_root=root)
    unresolved = [line for line in log if line.startswith("SKIP")]
    assert not unresolved, unresolved
    metadata = tomllib.loads((manager / "pyproject.toml").read_text())
    assert "agent-bridge" not in metadata["tool"]["uv"]["sources"]


@pytest.mark.parametrize(
    "reference",
    [
        '{ path = "libs/missing" }',
        '{ path = "libs/first", editable = true }',
        '{ path = "libs/first", editable = "false" }',
    ],
)
def test_materialize_refuses_incomplete_already_local_sources(tmp_path, reference):
    consumer = tmp_path / "plugins" / "example"
    first = consumer / "libs" / "first"
    first.mkdir(parents=True)
    (first / "pyproject.toml").write_text('[project]\nname = "first"\nversion = "1"\n')
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "1"\n'
        f"[tool.uv.sources]\nfirst = {reference}\n",
    )
    log = mm.materialize(tmp_path, canonical_root=tmp_path)
    assert any(line.startswith("SKIP") and "local source" in line for line in log), log


@pytest.mark.parametrize("escape", [False, True])
def test_materialize_refuses_incomplete_nested_local_sources(tmp_path, escape):
    consumer = tmp_path / "plugins" / "example"
    first = consumer / "libs" / "first"
    first.mkdir(parents=True)
    target = tmp_path / "libs" / "outside"
    target.mkdir(parents=True)
    (target / "pyproject.toml").write_text('[project]\nname = "outside"\nversion = "1"\n')
    reference = "../../../../libs/outside" if escape else "../missing"
    (first / "pyproject.toml").write_text(
        '[project]\nname = "first"\nversion = "1"\n'
        f'[tool.uv.sources]\nsecond = {{ path = "{reference}" }}\n',
    )
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "1"\n'
        '[tool.uv.sources]\nfirst = { path = "libs/first" }\n',
    )
    log = mm.materialize(tmp_path, canonical_root=tmp_path)
    assert any(line.startswith("SKIP") and "local source" in line for line in log), log


def test_materialize_accepts_complete_local_sibling_closure(tmp_path):
    consumer = tmp_path / "plugins" / "example"
    for name, dependency in (("first", "second"), ("second", "first")):
        library = consumer / "libs" / name
        library.mkdir(parents=True)
        (library / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "1"\n'
            f'[tool.uv.sources]\n{dependency} = {{ path = "../{dependency}" }}\n',
        )
    (consumer / "vendor").mkdir()
    (consumer / "vendor" / "fixture-1-py3-none-any.whl").write_bytes(b"fixture")
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "1"\n'
        '[tool.uv.sources]\nfirst = { path = "libs/first" }\n'
        'fixture = { path = "vendor/fixture-1-py3-none-any.whl" }\n'
        'external = { index = "approved" }\n',
    )
    log = mm.materialize(tmp_path, canonical_root=tmp_path)
    assert not any(line.startswith("SKIP") for line in log), log


def test_materialize_requires_existing_standalone_manifest_but_allows_skill_plugins(tmp_path):
    (tmp_path / "worktree-manager").mkdir()
    (tmp_path / "plugins" / "skill-only").mkdir(parents=True)
    log = mm.materialize(tmp_path, canonical_root=tmp_path)
    assert len(log) == 1 and "worktree-manager" in log[0] and "missing" in log[0], log


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "requirements",
    [
        '[project]\ndependencies = ["missing @ file:///unavailable/package"]\n',
        '[project.optional-dependencies]\nextra = ["missing @ ../unavailable"]\n',
        '[build-system]\nrequires = ["missing @ file:///unavailable/package"]\n',
    ],
)
def test_materialize_refuses_direct_local_requirements(tmp_path, nested, requirements):
    consumer = tmp_path / "plugins" / "example"
    package = consumer / "libs" / "nested" if nested else consumer
    package.mkdir(parents=True)
    (package / "pyproject.toml").write_text(requirements)
    if nested:
        (consumer / "pyproject.toml").write_text(
            '[tool.uv.sources]\nnested = { path = "libs/nested" }\n',
        )
    log = mm.materialize(tmp_path, canonical_root=tmp_path)
    assert any("direct local requirement" in line for line in log), log


@pytest.mark.parametrize("kind", ["source", "inside-traversal", "outside-traversal"])
def test_materialize_refuses_linked_source_content(tmp_path, native_file_symlink, kind):
    consumer = tmp_path / "plugins" / "example"
    package = consumer / "libs" / "dependency"
    package.mkdir(parents=True)
    (package / "pyproject.toml").write_text('[project]\nname = "dependency"\nversion = "1"\n')
    original = tmp_path / "original-source"
    original.mkdir()
    (original / "module.py").write_text("value = 1\n")
    if kind != "source":
        (consumer / "anchor").mkdir()
        link = original / "link" if kind == "outside-traversal" else consumer / "link"
        native_file_symlink(link, consumer / "anchor", target_is_directory=True)
        prefix = "../../original-source/" if kind == "outside-traversal" else ""
        reference = prefix + "link/../libs/dependency"
    else:
        native_file_symlink(package / "src", original, target_is_directory=True)
        reference = "libs/dependency"
    (consumer / "pyproject.toml").write_text(
        f'[tool.uv.sources]\ndependency = {{ path = "{reference}" }}\n',
    )
    log = mm.materialize(tmp_path, canonical_root=tmp_path)
    assert any(
        line.startswith("SKIP") and (
            "linked" in line
            or kind == "outside-traversal" and "not relative to its own payload" in line
        )
        for line in log
    ), log
    assert (original / "module.py").read_text() == "value = 1\n"
    assert not (package / "src").exists() or kind == "source"
    assert (package / "pyproject.toml").read_text() == (
        '[project]\nname = "dependency"\nversion = "1"\n'
    )


def test_materialized_closure_allows_an_aliased_parent(tmp_path, native_file_symlink):
    root = tmp_path / "real" / "repository"
    consumer = root / "plugins" / "example"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text('[project]\nname = "example"\nversion = "1"\n')
    alias = tmp_path / "alias"
    native_file_symlink(alias, root.parent, target_is_directory=True)
    aliased_repo = alias / "repository"
    assert uer.materialized_local_source_problems(aliased_repo / "plugins" / "example") == []
    _canonical_lib(root, "shared", version="1.0.0", content="value = 1\n")
    source_consumer = root / "plugins" / "source"
    source_consumer.mkdir()
    (source_consumer / "pyproject.toml").write_text(
        '[tool.uv.sources]\nshared = {path = "../../libs/shared", editable = true}\n',
    )
    assert uer.uv_editable_problems(
        "source", aliased_repo / "plugins" / "source", repo=aliased_repo,
    ) == []


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("editable", [False, True])
def test_materialize_declared_flat_python_modules(tmp_path, nested, editable):
    root = tmp_path / "repo"
    flat = root / "libs" / "flat-context"
    flat.mkdir(parents=True)
    (flat / "pyproject.toml").write_text(
        '[project]\nname = "agent-flat-context"\nversion = "1.0.0"\n'
        '[tool.setuptools]\npy-modules = ["flat_context", "_flat_helper"]\n',
        encoding="utf-8",
    )
    (flat / "flat_context.py").write_text("from _flat_helper import value\n", encoding="utf-8")
    (flat / "_flat_helper.py").write_text("value = 1\n", encoding="utf-8")
    if nested:
        _canonical_lib_with_dependency(
            root, "owner", dep_lib="flat-context", dep_raw_path="../flat-context",
        )
        consumer = _uv_editable_consumer(
            root, "plugins/agent-example", "agent-owner", "../../libs/owner",
        )
    else:
        consumer = _uv_editable_consumer(
            root, "plugins/agent-example", "agent-flat-context", "../../libs/flat-context",
        )
    reference = root / "libs" / "owner" if nested else consumer
    if not editable:
        manifest = reference / "pyproject.toml"
        manifest.write_text(manifest.read_text().replace("editable = true", "editable = false"))
    dependency_name = "flat-context" if nested else "agent-flat-context"
    assert uer.is_retained_flat_ref(reference, dependency_name, flat) is (not editable)
    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert log and all(line.startswith("OK") for line in log), log
    copied = consumer / "libs" / "flat-context"
    assert (copied / "flat_context.py").read_bytes() == (flat / "flat_context.py").read_bytes()
    assert (copied / "_flat_helper.py").is_file()
    assert not (copied / "src").exists()
    if nested:
        assert "editable" not in (consumer / "libs" / "owner" / "pyproject.toml").read_text()


@pytest.mark.parametrize("editable", ['"false"', "1", None])
def test_retained_flat_reference_requires_explicit_boolean_false(tmp_path, editable):
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "pyproject.toml").write_text('[tool.setuptools]\npy-modules = ["flat"]\n')
    (flat / "flat.py").write_text("value = 1\n")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    setting = f", editable = {editable}" if editable is not None else ""
    (consumer / "pyproject.toml").write_text(
        '[tool.uv.sources]\nagent-flat = { path = "../flat"' + setting + " }\n",
    )
    assert not uer.is_retained_flat_ref(consumer, "agent-flat", flat)


@pytest.mark.parametrize("modules", [[], ["missing"], ["../outside"], ["a.b"], [True], ["x", "x"]])
def test_materialize_refuses_undeclared_or_invalid_flat_sources(tmp_path, modules):
    root = tmp_path / "repo"
    flat = root / "libs" / "flat-context"
    flat.mkdir(parents=True)
    (flat / "x.py").write_text("value = 1\n")
    (flat / "pyproject.toml").write_text(
        '[project]\nname = "flat"\nversion = "1"\n'
        '[tool.setuptools]\npy-modules = ' + json.dumps(modules).lower() + "\n",
        encoding="utf-8",
    )
    assert not uer.has_canonical_python_sources(flat)
    consumer = _uv_editable_consumer(
        root, "plugins/agent-example", "agent-flat-context", "../../libs/flat-context",
    )
    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any(line.startswith("SKIP") for line in log)
    assert not (consumer / "libs" / "flat-context").exists()


def test_packaged_projection_assets_expand_into_own_payload(tmp_path):
    root = tmp_path / "repo"
    consumer = root / "plugins" / "agent-worktrees"
    consumer.mkdir(parents=True)
    source = _canonical_file(
        root, "plugins/customizing-copilot/scripts/render.py", content="print('owned engine')\n",
    )
    (consumer / "packaged-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 1,
        "canonicalDir": "plugins/customizing-copilot/scripts",
        "payloadDir": "scripts/instruction-projections", "files": ["render.py"],
    }), encoding="utf-8")
    dest = tmp_path / "release" / "plugins" / "agent-worktrees"
    dest.mkdir(parents=True)
    report = mm.materialize_launch_wrapper_assets_into(
        source_consumer_dir=consumer, dest_consumer_dir=dest,
        canonical_root=root, dest_root=tmp_path / "release",
    )
    assert len(report) == 1 and report[0].startswith("OK")
    assert (dest / "scripts" / "instruction-projections" / "render.py").read_bytes() == source.read_bytes()
    assert not (dest.parent / "customizing-copilot").exists()


def test_packaged_assets_include_standalone_manager_without_plugins(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    consumer = root / "worktree-manager"
    consumer.mkdir(parents=True)
    source = _canonical_file(root, "tools/prepare.py", content="print('trusted')\n")
    (consumer / lwar.PACKAGED_MANIFEST_NAME).write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 1,
        "canonicalDir": "tools", "payloadDir": "scripts/runtime-source-tools",
        "files": ["prepare.py"],
    }), encoding="utf-8")
    dest = tmp_path / "release"
    (dest / "worktree-manager").mkdir(parents=True)
    report = mm.materialize_launch_wrapper_assets(dest, canonical_root=root)
    assert len(report) == 1 and report[0].startswith("OK")
    assert (
        dest / "worktree-manager" / "scripts" / "runtime-source-tools" / "prepare.py"
    ).read_bytes() == source.read_bytes()


@pytest.mark.parametrize("fault", [None, "missing", "overlap", "escape"])
def test_packaged_groups_project_multiple_roots_only_after_complete_preflight(tmp_path, fault):
    root = tmp_path / "source"
    consumer = root / "worktree-manager"
    consumer.mkdir(parents=True)
    _canonical_file(root, "tools/prepare.py", content="producer")
    if fault != "missing":
        _canonical_file(root, "libs/owned/helper.py", content="primitive")
    groups = [
        {"canonicalDir": "tools", "payloadDir": "scripts/provider/tools", "files": ["prepare.py"]},
        {"canonicalDir": "libs/owned", "payloadDir": "scripts/provider/libs/owned", "files": ["helper.py"]},
    ]
    if fault == "overlap":
        groups.append(dict(groups[0]))
    if fault == "escape":
        groups[1]["payloadDir"] = "../outside"
    (consumer / lwar.PACKAGED_MANIFEST_NAME).write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 2, "groups": groups,
    }))
    destination = tmp_path / "destination"
    destination.mkdir()
    report = mm.materialize_launch_wrapper_assets_into(
        source_consumer_dir=consumer, dest_consumer_dir=destination, canonical_root=root,
    )
    if fault:
        assert report and all(line.startswith("SKIP ") for line in report)
        assert not list(destination.iterdir())
    else:
        assert len(report) == 2 and all(line.startswith("OK ") for line in report)
        assert (destination / "scripts/provider/tools/prepare.py").read_text() == "producer"
        assert (destination / "scripts/provider/libs/owned/helper.py").read_text() == "primitive"
        with pytest.raises(ValueError, match="group-aware"):
            lwar.load_manifest(consumer, manifest_name=lwar.PACKAGED_MANIFEST_NAME)


@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_packaged_assets_retain_exact_bytes_only_when_requested(
    tmp_path: Path, reuse: bool, changed: bool,
) -> None:
    source = tmp_path / "tools"
    source.mkdir()
    (source / "prepare.py").write_text("canonical\n", encoding="utf-8")
    consumer = tmp_path / "worktree-manager"
    (consumer / "scripts").mkdir(parents=True)
    retained = consumer / "scripts" / "prepare.py"
    retained.write_text("changed\n" if changed else "canonical\n", encoding="utf-8")
    original_bytes = retained.read_bytes()
    original_stat = retained.stat()
    (consumer / lwar.PACKAGED_MANIFEST_NAME).write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 1,
        "canonicalDir": "tools", "payloadDir": "scripts", "files": ["prepare.py"],
    }), encoding="utf-8")
    report = mm.materialize_launch_wrapper_assets_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer,
        canonical_root=tmp_path, reuse_existing=reuse,
    )
    assert bool(report) is (not reuse or changed)
    assert all(line.startswith("SKIP ") for line in report)
    assert retained.read_bytes() == original_bytes
    retained_stat = retained.lstat()
    for field in (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns",
    ):
        assert getattr(retained_stat, field) == getattr(original_stat, field), field
    for field in ("st_birthtime_ns", "st_file_attributes", "st_reparse_tag"):
        if hasattr(original_stat, field):
            assert getattr(retained_stat, field) == getattr(original_stat, field), field


@pytest.mark.parametrize("directory", ["../outside", "..\\outside", "C:\\outside"])
def test_packaged_assets_refuse_escaping_payload_directory(tmp_path, directory):
    consumer = tmp_path / "plugins" / "agent-worktrees"
    consumer.mkdir(parents=True)
    (consumer / "packaged-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 1,
        "canonicalDir": "canonical", "payloadDir": directory, "files": ["render.py"],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="payloadDir"):
        lwar.load_manifest(consumer, manifest_name=lwar.PACKAGED_MANIFEST_NAME)


def test_packaged_assets_preserve_nested_plugin_layout_without_copying_other_plugins(tmp_path):
    root = tmp_path / "source"
    consumer = root / "plugins" / "bridge"
    consumer.mkdir(parents=True)
    source = _canonical_file(root, "plugins/driver/extensions/driver/extension.mjs", content="export {};\n")
    (consumer / lwar.PACKAGED_MANIFEST_NAME).write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 1,
        "canonicalDir": "plugins", "payloadDir": "support",
        "files": ["driver/extensions/driver/extension.mjs"],
    }), encoding="utf-8")
    dest = tmp_path / "release" / "bridge"
    dest.mkdir(parents=True)
    report = mm.materialize_launch_wrapper_assets_into(
        source_consumer_dir=consumer, dest_consumer_dir=dest,
        canonical_root=root,
    )
    assert len(report) == 1 and report[0].startswith("OK")
    assert (dest / "support/driver/extensions/driver/extension.mjs").read_bytes() == source.read_bytes()
    assert not (dest / "support/bridge").exists()


@pytest.mark.parametrize("name", ["../outside", "driver/../outside", "/outside", "C:/outside", "driver\\file", "driver//file"])
def test_packaged_assets_refuse_unsafe_nested_file_names(tmp_path, name):
    (tmp_path / lwar.PACKAGED_MANIFEST_NAME).write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 1,
        "canonicalDir": "canonical", "files": [name],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="relative file"):
        lwar.load_manifest(tmp_path, manifest_name=lwar.PACKAGED_MANIFEST_NAME)


@pytest.mark.parametrize("linked_source", [True, False])
def test_packaged_assets_refuse_linked_nested_directories(tmp_path, linked_source):
    root = tmp_path / "source"
    consumer = root / "plugins" / "bridge"
    consumer.mkdir(parents=True)
    _canonical_file(root, "plugins/driver/extensions/entry.mjs", content="export {};\n")
    (consumer / lwar.PACKAGED_MANIFEST_NAME).write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 1,
        "canonicalDir": "plugins", "payloadDir": "support",
        "files": ["driver/extensions/entry.mjs"],
    }), encoding="utf-8")
    dest = tmp_path / "release"
    (dest / "support/driver").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = root / "plugins/driver" if linked_source else dest / "support/driver"
    if linked_source:
        shutil.rmtree(parent / "extensions")
    try:
        (parent / "extensions").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    report = mm.materialize_launch_wrapper_assets_into(
        source_consumer_dir=consumer, dest_consumer_dir=dest,
        canonical_root=root,
    )
    assert len(report) == 1 and "symlink" in report[0] and report[0].startswith("SKIP")
    assert not list(outside.iterdir())


def _canonical_lib(root: Path, lib: str, *, version: str, content: str) -> Path:
    pkg = lib.replace("-", "_")
    d = root / "libs" / lib
    (d / "src" / pkg).mkdir(parents=True, exist_ok=True)
    (d / "src" / pkg / "__init__.py").write_text(content, encoding="utf-8")
    (d / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{version}"\n', encoding="utf-8"
    )
    return d


def _retired_pointer(root: Path, plugin: str, lib: str, *, source: str | None = None) -> Path:
    d = root / "plugins" / plugin / "libs" / lib
    d.mkdir(parents=True, exist_ok=True)
    (d / "VENDOR_POINTER.json").write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.vendor-pointer",
                "version": 1,
                "source": source or f"libs/{lib}",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return d


def _file_pointer(root: Path, plugin: str, rel: str, *, source: str) -> Path:
    """Create a vendored *file* pointer stub at ``plugins/<plugin>/<rel>``."""
    d = root / "plugins" / plugin
    path = d / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"<!-- VENDOR_POINTER: source={source} kind=file -->\n\n"
        "This file is a vendored pointer; see the canonical source above.\n",
        encoding="utf-8",
    )
    return path


def _canonical_file(root: Path, rel: str, *, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_materialize_expands_file_pointer_from_canonical(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_file(root, "docs/patterns/thing.md", content="# Thing\n\nreal content\n")
    pointer = _file_pointer(root, "agent-bridge", "docs/thing.md",
                             source="docs/patterns/thing.md")

    log = mm.materialize(root, canonical_root=root)

    assert any("(file pointer)" in line and line.startswith("OK") for line in log)
    assert pointer.read_text() == "# Thing\n\nreal content\n"


def test_materialize_skips_file_pointer_with_missing_canonical(tmp_path: Path):
    root = tmp_path / "repo"
    pointer = _file_pointer(root, "agent-bridge", "docs/ghost.md",
                             source="docs/patterns/ghost.md")

    log = mm.materialize(root, canonical_root=root)
    assert any("SKIP" in line and "(file pointer)" in line and "not found" in line
               for line in log)
    # Left untouched -- still the stub, since nothing was expanded.
    assert pointer.read_text().startswith("<!-- VENDOR_POINTER:")


def test_materialize_refuses_a_symlinked_file_pointer(tmp_path: Path, native_file_symlink):
    # find_file_pointers()'s own is_file() check follows a symlink, and
    # pointer_path.write_bytes() would follow it again -- a symlinked
    # pointer path (or an ancestor between it and dest) would let
    # materialization overwrite the symlink's TARGET outside the
    # snapshot, the same class of gap already fixed for the
    # directory-pointer path.
    root = tmp_path / "repo"
    _canonical_file(root, "docs/patterns/thing.md", content="# Thing\n\nreal content\n")
    victim = root.parent / "outside-victim-pointer.md"
    victim.write_text(
        "<!-- VENDOR_POINTER: source=docs/patterns/thing.md kind=file -->\n\n"
        "This file is a vendored pointer; see the canonical source above.\n",
        encoding="utf-8",
    )
    pointer_path = root / "plugins" / "agent-bridge" / "docs" / "thing.md"
    pointer_path.parent.mkdir(parents=True)
    native_file_symlink(pointer_path, victim)

    log = mm.materialize(root, canonical_root=root)
    assert any("SKIP" in line and "(file pointer)" in line and "is a symlink" in line
               for line in log)
    # Nothing was overwritten through the symlink.
    assert victim.read_text().startswith("<!-- VENDOR_POINTER:")


def test_materialize_refuses_absolute_source_path(tmp_path: Path):
    root = tmp_path / "repo"
    secret = tmp_path / "outside-repo-secret.txt"
    secret.write_text("do not leak\n", encoding="utf-8")
    pointer = _file_pointer(root, "agent-bridge", "docs/evil.md", source=str(secret))

    log = mm.materialize(root, canonical_root=root)
    assert any("SKIP" in line and "escapes the canonical root" in line for line in log)
    assert pointer.read_text().startswith("<!-- VENDOR_POINTER:")


def test_materialize_refuses_traversal_source_path(tmp_path: Path):
    root = tmp_path / "repo"
    secret = tmp_path / "outside-repo-secret.txt"
    secret.write_text("do not leak\n", encoding="utf-8")
    pointer = _file_pointer(root, "agent-bridge", "docs/evil.md",
                             source="../outside-repo-secret.txt")

    log = mm.materialize(root, canonical_root=root)
    assert any("SKIP" in line and "escapes the canonical root" in line for line in log)
    assert pointer.read_text().startswith("<!-- VENDOR_POINTER:")


def test_resolve_within_allows_legitimate_nested_source(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_file(root, "docs/patterns/deep/thing.md", content="ok\n")
    resolved = mm._resolve_within(root, "docs/patterns/deep/thing.md")
    assert resolved == (root / "docs/patterns/deep/thing.md").resolve()


def test_materialize_expands_multiple_file_pointers_for_same_doc(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_file(root, "docs/patterns/shared.md", content="shared doc\n")
    pointers = [
        _file_pointer(root, plugin, "docs/shared.md", source="docs/patterns/shared.md")
        for plugin in ("agent-bridge", "agent-worktrees", "agent-dispatch")
    ]

    log = mm.materialize(root, canonical_root=root)
    assert sum(1 for line in log if line.startswith("OK") and "(file pointer)" in line) == 3
    for pointer in pointers:
        assert pointer.read_text() == "shared doc\n"


def test_find_file_pointers_ignores_non_pointer_files(tmp_path: Path):
    root = tmp_path / "repo"
    _file_pointer(root, "agent-bridge", "docs/real-pointer.md",
                   source="docs/patterns/thing.md")
    (root / "plugins/agent-bridge/docs").mkdir(parents=True, exist_ok=True)
    (root / "plugins/agent-bridge/docs/plain.md").write_text("# Not a pointer\n",
                                                              encoding="utf-8")

    found = mm.find_file_pointers(root)
    assert [p.name for p in found] == ["real-pointer.md"]


@pytest.mark.parametrize("relative", ["outside.md", "repo/../outside.md"])
def test_file_pointer_candidates_cannot_write_outside_extracted_root(tmp_path, relative):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.md"
    contents = "<!-- VENDOR_POINTER: source=canonical.md kind=file -->\n"
    outside.write_text(contents)
    (root / "canonical.md").write_text("canonical\n")
    with pytest.raises(ValueError, match="outside the extracted root"):
        mm.materialize_file_pointers(
            root, canonical_root=root, candidates=[tmp_path / relative],
        )
    assert outside.read_text() == contents


def test_extracted_pointer_candidate_still_refuses_a_symlink(tmp_path, native_file_symlink):
    root = tmp_path / "repo"
    root.mkdir()
    victim = tmp_path / "victim"
    contents = "<!-- VENDOR_POINTER: source=canonical.md kind=file -->\n"
    victim.write_text(contents)
    pointer = root / "pointer"
    native_file_symlink(pointer, victim)
    (root / "canonical.md").write_text("canonical\n")
    log = mm.materialize_file_pointers(root, canonical_root=root, candidates=[pointer])
    assert any(line.startswith("SKIP") and "is a symlink" in line for line in log)
    assert victim.read_text() == contents


@pytest.mark.parametrize("parallel,extra_files", [(False, 0), (False, 61), (True, 61)])
@pytest.mark.parametrize("preselected", [False, True])
def test_file_pointer_discovery_preserves_full_scan_and_caller_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parallel: bool, extra_files: int,
    preselected: bool,
) -> None:
    pointers = [
        _file_pointer(tmp_path, "example", name, source="docs/source.md")
        for name in ("last.unknown", "nested/first")
    ]
    (tmp_path / "not-a-pointer").write_bytes(b"\xff\x00")
    for index in range(extra_files):
        (tmp_path / f"plain-{index}").write_text("not a pointer\n", encoding="utf-8")
    candidates = list(tmp_path.rglob("*")) if preselected else None
    if preselected:
        _file_pointer(tmp_path, "example", "unselected", source="docs/source.md")
    else:
        monkeypatch.setattr(Path, "is_file", lambda _path: pytest.fail(
            "a full scan must use its directory-entry metadata, not re-stat each file",
        ))
    reader = mm._file_pointer_source
    caller = threading.get_ident()
    barrier = threading.Barrier(2)
    observed = []
    def context_allows_workers() -> bool:
        assert extra_files, "a small scan must not query native context or create workers"
        return parallel

    monkeypatch.setattr(mm, "_parallel_pointer_reads", context_allows_workers)
    if not parallel or preselected:
        monkeypatch.setattr(mm, "_pointer_prefix_candidates", lambda _root: pytest.fail(
            "a restricted caller or preselected scan must not launch a native prefilter",
        ))

    def read(path: Path) -> str | None:
        if path in pointers:
            if parallel:
                assert threading.get_ident() != caller
                barrier.wait(timeout=5)
            else:
                assert threading.get_ident() == caller
            observed.append(path)
        return reader(path)

    monkeypatch.setattr(mm, "_file_pointer_source", read)
    assert mm.find_file_pointers(tmp_path, candidates=candidates) == sorted(pointers)
    assert set(observed) == set(pointers)


@pytest.mark.parametrize("failure", ["missing", "timeout", "error", "foreign"])
def test_pointer_prefilter_failure_keeps_the_complete_direct_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    pointer = _file_pointer(tmp_path, "example", "pointer", source="docs/source.md")
    for index in range(70):
        (tmp_path / f"ordinary-{index}").write_text("ordinary\n", encoding="utf-8")
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        if failure == "missing":
            raise FileNotFoundError("optional native scanner unavailable")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        return subprocess.CompletedProcess(
            args[0], 2 if failure == "error" else 0,
            str(tmp_path.parent / "foreign").encode("utf-8") + b"\0", b"",
        )

    monkeypatch.setattr(mm, "_parallel_pointer_reads", lambda: True)
    monkeypatch.setattr(mm.subprocess, "run", run)
    assert mm.find_file_pointers(tmp_path) == [pointer]
    assert len(calls) == 1
    assert "--no-index" in calls[0][0][0] and "--no-textconv" in calls[0][0][0]


def test_native_pointer_prefilter_keeps_unicode_binary_and_later_line_candidates(tmp_path: Path):
    if shutil.which("git") is None:
        pytest.skip("optional native prefilter requires Git")
    contents = {
        "pointer.unusual": "<!--\u00a0VENDOR_POINTER: source=canonical kind=file -->\n".encode(),
        "later": b"ordinary\n<!-- VENDOR_POINTER: source=canonical kind=file -->\n",
        "binary": b"<\xff\x00",
        "ordinary": b"ordinary\n",
    }
    for name, content in contents.items():
        (tmp_path / name).write_bytes(content)
    assert mm._pointer_prefix_candidates(tmp_path) == {
        tmp_path / name for name in ("pointer.unusual", "later", "binary")
    }
    assert mm._file_pointer_source(tmp_path / "pointer.unusual") == "canonical"


@pytest.mark.parametrize("layout", ["metadata", "root-reparse"])
def test_nonordinary_pointer_trees_keep_the_direct_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str,
) -> None:
    pointer = _file_pointer(tmp_path, "example", "pointer", source="docs/source.md")
    expected = [pointer]
    for index in range(70):
        (tmp_path / f"ordinary-{index}").write_text("ordinary\n", encoding="utf-8")
    monkeypatch.setattr(mm, "os", SimpleNamespace(name="nt", scandir=os.scandir))
    if layout == "metadata":
        metadata = tmp_path / ".GiT"
        metadata.mkdir()
        nested = metadata / "pointer"
        nested.write_text("<!-- VENDOR_POINTER: source=canonical kind=file -->\n")
        expected.append(nested)
    else:
        original = Path.lstat
        mode = tmp_path.lstat().st_mode
        monkeypatch.setattr(Path, "lstat", lambda path, *args, **kwargs: (
            SimpleNamespace(st_mode=mode, st_file_attributes=0x400)
            if path == tmp_path else original(path, *args, **kwargs)
        ))
    monkeypatch.setattr(mm, "_parallel_pointer_reads", lambda: True)
    monkeypatch.setattr(mm, "_pointer_prefix_candidates", lambda _root: pytest.fail(
        "nonordinary trees require the original direct reader",
    ))
    assert mm.find_file_pointers(tmp_path) == sorted(expected)


@pytest.mark.parametrize("opened,error", [(False, 1008), (False, 5), (True, 0)])
def test_parallel_pointer_reads_require_the_windows_process_identity(
    monkeypatch: pytest.MonkeyPatch, opened: bool, error: int,
) -> None:
    import ctypes

    closed = []

    def current_thread() -> int:
        return 17

    def open_token(thread: int, access: int, as_self: bool, token: object) -> bool:
        assert thread == 17 and access == 8 and as_self
        return opened

    def close_token(token: object) -> bool:
        closed.append(token)
        return True

    kernel = SimpleNamespace(GetCurrentThread=current_thread, CloseHandle=close_token)
    security = SimpleNamespace(OpenThreadToken=open_token)
    monkeypatch.setattr(mm.sys, "platform", "win32")
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda name, **_: kernel if name == "kernel32" else security,
        raising=False,
    )
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    assert mm._parallel_pointer_reads() is (not opened and error == 1008)
    assert len(closed) == int(opened)


def test_build_materializes_file_pointers_end_to_end(tmp_path: Path):
    source = tmp_path / "source"
    _canonical_file(source, "docs/patterns/thing.md", content="canonical\n")
    _file_pointer(source, "agent-bridge", "docs/thing.md", source="docs/patterns/thing.md")

    dest = tmp_path / "dest"
    log = mm.build(dest, source_root=source)

    assert any("(file pointer)" in line and line.startswith("OK") for line in log)
    assert (dest / "plugins/agent-bridge/docs/thing.md").read_text() == "canonical\n"
    # The source checkout itself must never be mutated by build().
    assert (source / "plugins/agent-bridge/docs/thing.md").read_text().startswith(
        "<!-- VENDOR_POINTER:"
    )


def test_main_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    source = tmp_path / "source"
    _canonical_lib(source, "zdd", version="0.1.0-dev1", content="x\n")
    _uv_editable_consumer(source, "plugins/agent-example", "agent-zdd", "../../libs/zdd")
    monkeypatch.setattr(mm, "REPO", source)

    dest = tmp_path / "dest"
    code = mm.main(["--dest", str(dest)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Materialized 1 artifact(s)" in out


def test_main_returns_nonzero_when_a_retired_pointer_marker_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    source = tmp_path / "source"
    _retired_pointer(source, "agent-bridge", "zdd")
    monkeypatch.setattr(mm, "REPO", source)

    dest = tmp_path / "dest"
    code = mm.main(["--dest", str(dest)])
    assert code == 1
    assert "retired directory-pointer kind still present" in capsys.readouterr().out


def test_build_overwrites_a_stale_existing_dest(tmp_path: Path):
    source = tmp_path / "source"
    _canonical_lib(source, "zdd", version="0.1.0-dev1", content="fresh\n")
    (source / "marker.txt").write_text("v2\n", encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "stale-leftover.txt").write_text("old build\n", encoding="utf-8")

    mm.build(dest, source_root=source)
    assert not (dest / "stale-leftover.txt").exists()
    assert (dest / "marker.txt").read_text() == "v2\n"


def test_build_preserves_a_symlink_instead_of_dereferencing_its_content(
    tmp_path: Path, native_file_symlink,
):
    source_root = tmp_path / "repo"
    secret = tmp_path / "outside-repo-secret"
    secret.mkdir()
    (secret / "leaked.txt").write_text("do not leak\n", encoding="utf-8")
    (source_root / "some-dir").mkdir(parents=True)
    native_file_symlink(source_root / "some-dir" / "a-link", secret, target_is_directory=True)
    (source_root / "README.md").write_text("hello\n", encoding="utf-8")
    dest = tmp_path / "dest"

    mm.build(dest, source_root=source_root)

    copied_link = dest / "some-dir" / "a-link"
    assert copied_link.is_symlink(), "a-link must be preserved as a symlink, not dereferenced"
    assert os.path.samefile(os.readlink(copied_link), secret)


def test_materialize_refuses_a_retired_directory_pointer_marker(tmp_path: Path):
    root = tmp_path / "repo"
    pointer_dir = _retired_pointer(root, "agent-bridge", "zdd")

    log = mm.materialize(root, canonical_root=root)

    assert any("retired directory-pointer kind still present" in line for line in log)
    assert (pointer_dir / "VENDOR_POINTER.json").exists()


def _uv_editable_consumer(
    root: Path, consumer_rel: str, dist_name: str, raw_path: str,
) -> Path:
    """Write a consumer ``pyproject.toml`` carrying a `uv`-editable
    canonical-reference entry (``path`` escapes the consumer's own root,
    ``editable = true``)."""
    d = root / consumer_rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        f'dependencies = ["{dist_name}"]\n'
        "\n"
        "[tool.uv.sources]\n"
        f'{dist_name} = {{ path = "{raw_path}", editable = true }}\n',
        encoding="utf-8",
    )
    return d


def _rewrite_to_local_path(pyproject: Path, *, name: str, raw_path: str, lib: str) -> None:
    text = pyproject.read_text(encoding="utf-8")
    pattern = re.compile(
        r'^([ \t]*(?:"' + re.escape(name) + r'"|\'' + re.escape(name) + r"'|" +
        re.escape(name) + r')\s*=\s*)\{\s*(?:'
        r'path\s*=\s*(?:"' + re.escape(raw_path) + r'"|\'' + re.escape(raw_path) + r"')"
        r'\s*,\s*editable\s*=\s*(?:true|false)'
        r'|editable\s*=\s*(?:true|false)\s*,\s*path\s*=\s*(?:"' + re.escape(raw_path) + r'"|\'' +
        re.escape(raw_path) + r"'))\s*\}[ \t]*(?:#.*)?$",
        re.MULTILINE,
    )
    text, count = pattern.subn(
        lambda m: f'{m.group(1)}{{ path = "libs/{lib}" }}',
        text,
        count=1,
    )
    assert count == 1, f"{pyproject}: missing top-level entry for {name}"
    pyproject.write_text(text, encoding="utf-8")


def _rewrite_nested_path(pyproject: Path, *, name: str, raw_path: str) -> None:
    text = pyproject.read_text(encoding="utf-8")
    pattern = re.compile(
        r'^([ \t]*(?:"' + re.escape(name) + r'"|\'' + re.escape(name) + r"'|" +
        re.escape(name) + r')\s*=\s*)\{\s*(?:'
        r'path\s*=\s*(?:"' + re.escape(raw_path) + r'"|\'' + re.escape(raw_path) + r"')"
        r'\s*,\s*editable\s*=\s*(?:true|false)'
        r'|editable\s*=\s*(?:true|false)\s*,\s*path\s*=\s*(?:"' + re.escape(raw_path) + r'"|\'' +
        re.escape(raw_path) + r"'))\s*\}[ \t]*(?:#.*)?$",
        re.MULTILINE,
    )
    text, count = pattern.subn(
        lambda m: f'{m.group(1)}{{ path = "{raw_path}" }}',
        text,
        count=1,
    )
    assert count == 1, f"{pyproject}: missing nested entry for {name}"
    pyproject.write_text(text, encoding="utf-8")


def _assert_reference_kind(consumer: Path, name: str, canonical: Path, editable: bool) -> None:
    if editable:
        return
    sources = uer.tomllib.loads((consumer / "pyproject.toml").read_text())["tool"]["uv"]["sources"]
    assert sources[name].get("editable") is False
    assert not (canonical / "src").exists()
    data = uer.tomllib.loads((canonical / "pyproject.toml").read_text())
    modules = data["tool"]["setuptools"]["py-modules"]
    assert modules and len(modules) == len(set(modules))
    assert all(
        isinstance(module, str) and module.isidentifier()
        and (canonical / (module + ".py")).is_file()
        and not (canonical / (module + ".py")).is_symlink()
        for module in modules
    )


def _build_expected_vendored_tree(dest_lib: Path, *, canonical_root: Path) -> None:
    """Independently reconstruct the vendored tree from current canonical libs.

    This intentionally does NOT call the production nested materializer: it
    copies the canonical lib, rewrites nested source entries itself, and recurses
    over nested canonical sibling deps so the expected manifests are an
    independent oracle.
    """

    refs = uer.find_uv_editable_refs(dest_lib)
    for name, raw_path, lib, editable in refs:
        _assert_reference_kind(dest_lib, name, canonical_root / "libs" / lib, editable)
        nested_dest = dest_lib.parent.parent / "libs" / lib
        if not nested_dest.exists():
            shutil.copytree(canonical_root / "libs" / lib, nested_dest, ignore=mm._ignore)
            _build_expected_vendored_tree(nested_dest, canonical_root=canonical_root)
        _rewrite_nested_path(dest_lib / "pyproject.toml", name=name, raw_path=raw_path)


def test_find_uv_editable_refs_only_returns_escaping_entries_regardless_of_editable(tmp_path: Path):
    root = tmp_path / "repo"
    consumer = _uv_editable_consumer(
        root, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd"
    )
    # An in-tree (non-escaping) entry must never be reported.
    (consumer / "pyproject.toml").write_text(
        (consumer / "pyproject.toml").read_text()
        + 'agent-other = { path = "libs/other" }\n',
        encoding="utf-8",
    )
    refs = uer.find_uv_editable_refs(consumer)
    assert refs == [("agent-zdd", "../../libs/zdd", "zdd", True)]


def test_find_uv_editable_refs_includes_a_non_editable_escaping_entry(tmp_path: Path):
    # A caller must SEE this entry (never silently skip it) so promotion
    # can refuse it explicitly rather than shipping the external path
    # unchanged -- see materialize_uv_editable_ref_into's own handling.
    root = tmp_path / "repo"
    consumer = root / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd" }\n',
        encoding="utf-8",
    )
    refs = uer.find_uv_editable_refs(consumer)
    assert refs == [("agent-zdd", "../../libs/zdd", "zdd", False)]


def test_materialize_uv_editable_ref_into_copies_full_tree_and_rewrites_entry(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev5", content="real = True\n")
    (root / "libs/zdd/README.md").write_text("# zdd\n", encoding="utf-8")
    (root / "libs/zdd/tests").mkdir(parents=True)
    (root / "libs/zdd/tests/test_zdd.py").write_text("def test_it(): pass\n", encoding="utf-8")
    consumer = _uv_editable_consumer(root, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any(line.startswith("OK") for line in log), log
    dest_lib = consumer / "libs/zdd"
    assert (dest_lib / "src/zdd/__init__.py").read_text() == "real = True\n"
    assert (dest_lib / "README.md").read_text() == "# zdd\n"
    assert (dest_lib / "tests/test_zdd.py").exists()
    assert '"0.1.0-dev5"' in (dest_lib / "pyproject.toml").read_text()

    pp_text = (consumer / "pyproject.toml").read_text()
    assert 'agent-zdd = { path = "libs/zdd" }' in pp_text
    assert "editable" not in pp_text


def test_materialize_uv_editable_ref_into_skips_missing_canonical(tmp_path: Path):
    root = tmp_path / "repo"
    consumer = _uv_editable_consumer(root, "plugins/agent-bridge", "agent-ghost", "../../libs/ghost")
    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("does not exist" in line for line in log)
    assert not (consumer / "libs/ghost").exists()


def test_materialize_uv_editable_ref_into_refuses_a_non_editable_entry(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    consumer = root / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd" }\n',
        encoding="utf-8",
    )
    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("missing editable = true" in line for line in log)
    assert not (consumer / "libs/zdd").exists()
    # The pyproject.toml is never touched either -- refused before any write.
    assert "editable" not in (consumer / "pyproject.toml").read_text()


def test_materialize_uv_editable_ref_into_refuses_escaping_canonical_root(tmp_path: Path):
    root = tmp_path / "repo"
    outside = tmp_path / "outside" / "zdd"
    (outside / "src" / "zdd").mkdir(parents=True)
    (outside / "src" / "zdd" / "__init__.py").write_text("evil\n", encoding="utf-8")
    consumer = _uv_editable_consumer(
        root, "plugins/agent-bridge", "agent-zdd", "../../../outside/zdd"
    )
    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("is not canonical_root/libs/zdd" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_uv_editable_ref_into_refuses_a_path_outside_the_libs_tree(tmp_path: Path):
    # The reviewer's exact concern: an escaping entry that DOES resolve
    # inside canonical_root, but not under canonical_root/libs/<lib> --
    # e.g. another plugin's own tree entirely. A containment check scoped
    # only to "somewhere inside canonical_root" would accept this and copy
    # the OTHER plugin's tree into this consumer's libs/<lib>/, rewriting
    # it as a shared-lib dependency it never was.
    root = tmp_path / "repo"
    (root / "plugins/other-plugin/src/other_plugin").mkdir(parents=True)
    (root / "plugins/other-plugin/src/other_plugin/__init__.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    consumer = _uv_editable_consumer(
        root, "plugins/agent-bridge", "agent-zdd", "../other-plugin"
    )
    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("is not canonical_root/libs/other-plugin" in line for line in log)
    assert not (consumer / "libs/other-plugin").exists()


@pytest.mark.parametrize("eligible", [False, True])
def test_plugin_copy_workers_preserve_tree_logs_and_standalone_dependency_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, eligible: bool,
):
    baseline = tmp_path / "baseline"
    _canonical_lib(baseline, "shared-lib", version="1.0.0", content="shared\n")
    for name in ("agent-dispatch", "other"):
        plugin = _uv_editable_consumer(
            baseline, f"plugins/{name}", "shared-lib", "../../libs/shared-lib",
        )
        (plugin / "src").mkdir()
        (plugin / "src" / "module.py").write_text("value = 1\n", encoding="utf-8")
    _uv_editable_consumer(
        baseline, "services/dispatch", "agent-dispatch", "../../plugins/agent-dispatch",
    )
    candidate = tmp_path / "candidate"
    shutil.copytree(baseline, candidate)
    monkeypatch.setattr(mm, "_parallel_pointer_reads", lambda: False)
    expected_log = mm.materialize_uv_editable_refs(baseline, canonical_root=baseline)
    expected = {
        path.relative_to(baseline): path.read_bytes()
        for path in baseline.rglob("*") if path.is_file()
    }
    original = mm.materialize_uv_editable_ref_into
    finished = set()
    threads = []

    def materialize(**kwargs):
        destination = kwargs["dest_consumer_dir"]
        if destination.parent.name == "plugins":
            threads.append(threading.current_thread().name)
            result = original(**kwargs)
            finished.add(destination.name)
            return result
        assert finished == {"agent-dispatch", "other"}
        return original(**kwargs)

    monkeypatch.setattr(mm, "materialize_uv_editable_ref_into", materialize)
    monkeypatch.setattr(mm, "_parallel_pointer_reads", lambda: eligible)
    observed_log = mm.materialize_uv_editable_refs(candidate, canonical_root=candidate)
    assert {
        path.relative_to(candidate): path.read_bytes()
        for path in candidate.rglob("*") if path.is_file()
    } == expected
    assert [line.replace(str(candidate), "ROOT") for line in observed_log] == [
        line.replace(str(baseline), "ROOT") for line in expected_log
    ]
    assert threads and all(
        name.startswith("ThreadPoolExecutor") if eligible else name == threading.current_thread().name
        for name in threads
    )


def test_materialize_uv_editable_refs_whole_repo(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="x\n")
    _uv_editable_consumer(root, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")
    _uv_editable_consumer(root, "worktree-manager", "agent-zdd", "../libs/zdd")

    log = mm.materialize_uv_editable_refs(root, canonical_root=root)

    assert (root / "plugins/agent-bridge/libs/zdd/src/zdd/__init__.py").exists()
    assert (root / "worktree-manager/libs/zdd/src/zdd/__init__.py").exists()
    assert sum(1 for line in log if line.startswith("OK")) == 2


def test_materialize_whole_repo_also_expands_uv_editable_refs(tmp_path: Path):
    source = tmp_path / "source"
    _canonical_lib(source, "zdd", version="0.1.0-dev1", content="x\n")
    _uv_editable_consumer(source, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")

    dest_dir = tmp_path / "dest"
    log = mm.build(dest_dir, source_root=source)

    assert any(line.startswith("OK") for line in log)
    assert (dest_dir / "plugins/agent-bridge/libs/zdd/src/zdd/__init__.py").exists()
    pp_text = (dest_dir / "plugins/agent-bridge/pyproject.toml").read_text()
    assert 'agent-zdd = { path = "libs/zdd" }' in pp_text


def test_materialize_uv_editable_ref_into_preflights_rewrite_before_copying(tmp_path: Path):
    # If the pyproject.toml entry doesn't exactly match the expected form
    # (e.g. hand-edited between authoring and promotion), the rewrite must
    # be refused BEFORE canonical's tree is ever copied in -- never leaving
    # a half-materialized libs/<lib>/ with an un-rewritten pyproject.toml.
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    consumer = root / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        # Deliberately a DIFFERENT raw_path than what find_uv_editable_refs
        # will report reading it back (can't normally happen through the
        # ordinary API, but simulates a rewrite target that vanished
        # between discovery and expansion).
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    # Sabotage the entry's exact text AFTER computing raw_path via a direct
    # call, to exercise the preflight independent of find_uv_editable_refs.
    canonical = root / "libs/zdd"
    dest_lib_dir = consumer / "libs/zdd"
    log_line = mm._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=consumer / "pyproject.toml",
        name="agent-zdd", raw_path="../../libs/nonexistent-raw-path-text", lib="zdd",
    )
    assert "could not find" in log_line
    assert not dest_lib_dir.exists()


def test_materialize_uv_editable_ref_into_refuses_a_malformed_canonical_lib(tmp_path: Path):
    # A canonical directory that exists but has no src/ (or no
    # pyproject.toml) must be refused BEFORE anything is copied -- matching
    # the existing directory/file pointer materializer's own fail-closed
    # behavior for an incomplete canonical lib.
    root = tmp_path / "repo"
    (root / "libs/zdd").mkdir(parents=True)  # exists, but no src/ at all
    consumer = _uv_editable_consumer(root, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("src not found" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_uv_editable_ref_into_refuses_a_canonical_lib_missing_pyproject(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    (root / "libs/zdd/src/zdd").mkdir(parents=True)
    (root / "libs/zdd/src/zdd/__init__.py").write_text("x = 1\n", encoding="utf-8")
    # No pyproject.toml under canonical.
    consumer = _uv_editable_consumer(root, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("pyproject.toml missing" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_uv_editable_ref_into_refuses_a_symlinked_source_pyproject(
    tmp_path: Path, native_file_symlink,
):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    real = tmp_path / "outside-pyproject.toml"
    real.write_text(
        '[tool.uv.sources]\nagent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    consumer = root / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    native_file_symlink(consumer / "pyproject.toml", real)

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("is a symlink" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_uv_editable_ref_into_catches_a_symlinked_canonical_before_resolving(
    tmp_path: Path, native_file_symlink,
):
    # The symlink-ancestor check must run on the UNRESOLVED, clean
    # canonical_root/libs/<lib> path BEFORE any .resolve() call -- resolving
    # first would silently follow (and erase) the symlink, so a later
    # ancestor check against the already-resolved path could never detect
    # it.
    root = tmp_path / "repo"
    real_lib = tmp_path / "outside-lib"
    (real_lib / "src/zdd").mkdir(parents=True)
    (real_lib / "src/zdd/__init__.py").write_text("evil\n", encoding="utf-8")
    (real_lib / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    (root / "libs").mkdir(parents=True)
    native_file_symlink(root / "libs/zdd", real_lib, target_is_directory=True)
    consumer = _uv_editable_consumer(root, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("is a symlink" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_uv_editable_ref_into_refuses_an_unsafe_lib_name(tmp_path: Path):
    root = tmp_path / "repo"
    consumer = _uv_editable_consumer(
        root, "plugins/agent-bridge", "agent-x", "../../libs/.."
    )
    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("not a safe lib name" in line for line in log)


def test_real_repo_uv_editable_materialization_matches_current_canonical_trees(
    tmp_path: Path,
):
    """Regression coverage for the real converted consumers.

    A historical pre-conversion tree is NOT the faithful baseline once a
    conversion PR also changed canonical README/tests content in the same commit.
    The non-circular comparison is "promotion output now" vs. "what a byte-
    vendored copy from the SAME current canonical lib would look like now" --
    i.e. the materialized tree must match today's canonical ``libs/<lib>`` tree
    byte-for-byte for every real `uv`-editable consumer reference.
    """

    repo = mm.REPO.resolve()
    discovered: list[tuple[str, Path, list[tuple[str, str, str, bool]]]] = []
    for consumer, consumer_dir in uer.iter_consumer_dirs():
        refs = uer.find_uv_editable_refs(consumer_dir)
        if refs:
            discovered.append((consumer, consumer_dir, refs))

    assert discovered, "expected at least one real uv-editable consumer"
    checked: list[str] = []
    actual_root = tmp_path / "actual"
    tracked = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(repo), "ls-files", "-z"],
        check=True, capture_output=True, text=True, encoding="utf-8",
        env={name: value for name, value in os.environ.items() if not name.startswith("GIT_")},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    included = set()
    for name in tracked.stdout.split("\0"):
        if name:
            path = repo / name
            included.update([path, *path.parents])

    def _consumer_snapshot_dir(root: Path, consumer: str) -> Path:
        return root / consumer if consumer in mm._EXTRA_CONSUMER_DIRS else root / "plugins" / consumer

    def _ignore_snapshot(directory: str, names: list[str]) -> set[str]:
        return mm._ignore(directory, names) | {
            name for name in names if Path(directory) / name not in included
        }

    for consumer, consumer_dir, refs in discovered:
        actual_consumer_dir = _consumer_snapshot_dir(actual_root, consumer)
        expected_consumer_dir = _consumer_snapshot_dir(tmp_path / "expected", consumer)
        actual_consumer_dir.mkdir(parents=True, exist_ok=True)
        expected_consumer_dir.mkdir(parents=True, exist_ok=True)
        pyproject_text = (consumer_dir / "pyproject.toml").read_text(encoding="utf-8")
        (actual_consumer_dir / "pyproject.toml").write_text(
            pyproject_text,
            encoding="utf-8",
        )
        (expected_consumer_dir / "pyproject.toml").write_text(
            pyproject_text,
            encoding="utf-8",
        )
        for directory in ("libs", "vendor"):
            retained = consumer_dir / directory
            if retained.is_dir():
                shutil.copytree(
                    retained, actual_consumer_dir / directory, ignore=_ignore_snapshot,
                )
        for ext in ("sh", "ps1"):
            installer = consumer_dir / "scripts" / f"install.{ext}"
            if not installer.is_file():
                installer = consumer_dir / "scripts" / f"init.{ext}"
            if installer.is_file():
                scripts = actual_consumer_dir / "scripts"
                scripts.mkdir(exist_ok=True)
                shutil.copy2(installer, scripts / installer.name)
        for name, raw_path, lib, editable in refs:
            _assert_reference_kind(consumer_dir, name, repo / "libs" / lib, editable)
            _rewrite_to_local_path(
                expected_consumer_dir / "pyproject.toml", name=name, raw_path=raw_path, lib=lib
            )

    log = mm.materialize(actual_root, canonical_root=repo)
    skips = [line for line in log if line.startswith("SKIP")]
    assert not skips, "; ".join(skips)
    for consumer, _consumer_dir, refs in discovered:
        actual_consumer_dir = _consumer_snapshot_dir(actual_root, consumer)
        expected_consumer_dir = _consumer_snapshot_dir(tmp_path / "expected", consumer)
        assert (actual_consumer_dir / "pyproject.toml").read_text(encoding="utf-8") == (
            expected_consumer_dir / "pyproject.toml"
        ).read_text(encoding="utf-8")
        for name, _raw_path, lib, _editable in refs:
            expected_lib = expected_consumer_dir / "libs" / lib
            if not expected_lib.exists():
                source = repo / "libs" / lib
                if consumer == "services/dispatch" and name == lib == "agent-dispatch":
                    source = repo / "plugins" / lib
                shutil.copytree(source, expected_lib, ignore=mm._ignore)
                _build_expected_vendored_tree(expected_lib, canonical_root=repo)

            materialized = actual_consumer_dir / "libs" / lib
            assert materialized.is_dir(), f"{consumer}: libs/{lib} was not materialized"
            assert uer.lib_tree_matches(expected_lib, materialized), (
                f"{consumer}: materialized libs/{lib} differs from the current vendored "
                f"reconstruction built from canonical libs/{lib}"
            )
            checked.append(f"{consumer}:{lib}")
    assert checked, "expected at least one materialized consumer/lib pair"


def test_materialize_uv_editable_ref_into_refuses_a_malformed_manifest(tmp_path: Path):
    root = tmp_path / "repo"
    consumer = root / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text("this is not [ valid toml", encoding="utf-8")

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("could not read/parse" in line for line in log)


def test_materialize_one_uv_editable_ref_accepts_editable_before_path_key_order(tmp_path: Path):
    # find_uv_editable_refs() parses real TOML and accepts either key
    # order -- the rewrite must too, or a valid entry authored with
    # editable-then-path would pass --check yet make promotion emit an
    # unresolvable SKIP.
    canonical = tmp_path / "libs/zdd"
    (canonical / "src/zdd").mkdir(parents=True)
    (canonical / "src/zdd/__init__.py").write_text("real\n", encoding="utf-8")
    (canonical / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    consumer = tmp_path / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    pyproject = consumer / "pyproject.toml"
    pyproject.write_text(
        "[tool.uv.sources]\n"
        'agent-zdd = { editable = true, path = "../../libs/zdd" }\n',
        encoding="utf-8",
    )
    dest_lib_dir = consumer / "libs/zdd"

    result = mm._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=pyproject,
        name="agent-zdd", raw_path="../../libs/zdd", lib="zdd",
    )
    assert result.startswith("OK"), result
    assert (dest_lib_dir / "src/zdd/__init__.py").read_text() == "real\n"
    assert 'agent-zdd = { path = "libs/zdd" }' in pyproject.read_text()


def test_materialize_one_uv_editable_ref_scopes_rewrite_to_uv_sources_table(tmp_path: Path):
    canonical = tmp_path / "libs/zdd"
    (canonical / "src/zdd").mkdir(parents=True)
    (canonical / "src/zdd/__init__.py").write_text("real\n", encoding="utf-8")
    (canonical / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    consumer = tmp_path / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    pyproject = consumer / "pyproject.toml"
    pyproject.write_text(
        "[tool.other]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n'
        "\n"
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    dest_lib_dir = consumer / "libs/zdd"

    result = mm._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=pyproject,
        name="agent-zdd", raw_path="../../libs/zdd", lib="zdd",
    )
    assert result.startswith("OK"), result
    text = pyproject.read_text()
    # The unrelated table's identical-looking entry survives untouched.
    assert text.count('agent-zdd = { path = "../../libs/zdd", editable = true }') == 1
    assert 'agent-zdd = { path = "libs/zdd" }' in text


def test_materialize_uv_editable_ref_into_refuses_a_symlinked_destination_manifest(
    tmp_path: Path, native_file_symlink,
):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    victim = tmp_path / "outside-victim-pyproject.toml"
    victim.write_text("victim content\n", encoding="utf-8")
    consumer = root / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    real_pp = tmp_path / "real-pyproject.toml"
    real_pp.write_text(
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    native_file_symlink(consumer / "pyproject.toml", victim)

    # find_uv_editable_refs() reads via the symlink (so the ref is
    # discovered), but the destination-manifest symlink itself must still
    # be refused before ever writing through it.
    victim.write_text(
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("is a symlink" in line for line in log)
    assert victim.read_text() == (
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n'
    )
    assert not (consumer / "libs/zdd").exists()


def test_materialize_one_uv_editable_ref_copies_files_outside_the_four_known_subpaths(
    tmp_path: Path,
):
    # lib_tree_matches() (the dev-time conversion's drift gate) compares
    # canonical's COMPLETE tree -- the copy performed here must match that
    # promise, or an extra file (a root-level LICENSE, package data, or
    # anything else) would silently disappear from the promoted snapshot.
    canonical = tmp_path / "libs/zdd"
    (canonical / "src/zdd").mkdir(parents=True)
    (canonical / "src/zdd/__init__.py").write_text("real\n", encoding="utf-8")
    (canonical / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (canonical / "LICENSE").write_text("MIT\n", encoding="utf-8")
    consumer = tmp_path / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    pyproject = consumer / "pyproject.toml"
    pyproject.write_text(
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    dest_lib_dir = consumer / "libs/zdd"

    result = mm._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=pyproject,
        name="agent-zdd", raw_path="../../libs/zdd", lib="zdd",
    )
    assert result.startswith("OK"), result
    assert (dest_lib_dir / "LICENSE").read_text() == "MIT\n"


def test_materialize_one_uv_editable_ref_refuses_a_symlink_anywhere_in_canonical(
    tmp_path: Path, native_file_symlink,
):
    canonical = tmp_path / "libs/zdd"
    (canonical / "src/zdd").mkdir(parents=True)
    (canonical / "src/zdd/__init__.py").write_text("real\n", encoding="utf-8")
    (canonical / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    victim = tmp_path / "victim.txt"
    victim.write_text("victim\n", encoding="utf-8")
    native_file_symlink(canonical / "sneaky-link", victim)
    consumer = tmp_path / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    pyproject = consumer / "pyproject.toml"
    pyproject.write_text(
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    dest_lib_dir = consumer / "libs/zdd"

    result = mm._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=pyproject,
        name="agent-zdd", raw_path="../../libs/zdd", lib="zdd",
    )
    assert "is a symlink" in result
    assert not dest_lib_dir.exists()


def test_materialize_uv_editable_ref_into_rewrites_all_aliases_of_the_same_lib(tmp_path: Path):
    # Two different [tool.uv.sources] distribution names pointing at the
    # SAME canonical lib -- the second must be rewritten too, not treated
    # as an "already exists" overwrite conflict (which would leave its
    # escaping reference un-rewritten in the promoted snapshot).
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    consumer = root / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n'
        'agent-zdd-alias = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert sum(1 for line in log if line.startswith("OK")) == 2
    text = (consumer / "pyproject.toml").read_text()
    assert 'agent-zdd = { path = "libs/zdd" }' in text
    assert 'agent-zdd-alias = { path = "libs/zdd" }' in text
    # Only copied once -- the second entry reused the existing dest_lib_dir.
    assert (consumer / "libs/zdd/src/zdd/__init__.py").read_text() == "real\n"


def test_materialize_uv_editable_ref_into_refuses_a_symlinked_dest_consumer_ancestor(
    tmp_path: Path, native_file_symlink,
):
    # dest_root bounds the destination-side ancestor check all the way to
    # the real snapshot root -- checking only up to dest_consumer_dir
    # itself would miss a symlinked dest/plugins (or dest_consumer_dir
    # itself), which materialize_uv_editable_refs()'s own symlink-
    # following is_dir() discovery could otherwise be redirected through.
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    dest = tmp_path / "dest"
    outside = tmp_path / "outside-plugins"
    consumer = outside / "agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    dest.mkdir(parents=True)
    dest_plugins = dest / "plugins"
    native_file_symlink(dest_plugins, outside, target_is_directory=True)
    dest_consumer_dir = dest_plugins / "agent-bridge"

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=dest_consumer_dir,
        canonical_root=root, dest_root=dest,
    )
    assert any("is a symlink" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_uv_editable_refs_passes_dest_root_for_the_ancestor_check(
    tmp_path: Path, native_file_symlink,
):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    dest = tmp_path / "dest"
    outside = tmp_path / "outside-plugins"
    consumer = outside / "agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    dest.mkdir(parents=True)
    native_file_symlink(dest / "plugins", outside, target_is_directory=True)

    log = mm.materialize_uv_editable_refs(dest, canonical_root=root)
    assert any("is a symlink" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_uv_editable_ref_into_refuses_a_symlinked_canonical_root(
    tmp_path: Path, native_file_symlink,
):
    # canonical_root.resolve() must never run before its own is_symlink()
    # is checked -- resolving first discards that fact, so every later
    # _find_symlinked_ancestor() walk (which terminates AT the resolved
    # root) would never inspect canonical_root itself, letting its
    # external target be treated as trusted.
    real_root = tmp_path / "real-repo"
    _canonical_lib(real_root, "zdd", version="0.1.0-dev1", content="real\n")
    root = tmp_path / "repo-symlink"
    native_file_symlink(root, real_root, target_is_directory=True)
    consumer = _uv_editable_consumer(root, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("is a symlink" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_one_uv_editable_ref_accepts_a_quoted_key_and_trailing_comment(
    tmp_path: Path,
):
    canonical = tmp_path / "libs/zdd"
    (canonical / "src/zdd").mkdir(parents=True)
    (canonical / "src/zdd/__init__.py").write_text("real\n", encoding="utf-8")
    (canonical / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    consumer = tmp_path / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    pyproject = consumer / "pyproject.toml"
    pyproject.write_text(
        "[tool.uv.sources]\n"
        '"agent-zdd" = { path = "../../libs/zdd", editable = true }  # a comment\n',
        encoding="utf-8",
    )
    dest_lib_dir = consumer / "libs/zdd"

    result = mm._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=pyproject,
        name="agent-zdd", raw_path="../../libs/zdd", lib="zdd",
    )
    assert result.startswith("OK"), result
    # The original quoted key form is preserved verbatim (group(1) captures
    # the matched key text as-is); only the value is rewritten.
    assert '"agent-zdd" = { path = "libs/zdd" }' in pyproject.read_text()


def test_materialize_one_uv_editable_ref_accepts_a_single_quoted_path(tmp_path: Path):
    canonical = tmp_path / "libs/zdd"
    (canonical / "src/zdd").mkdir(parents=True)
    (canonical / "src/zdd/__init__.py").write_text("real\n", encoding="utf-8")
    (canonical / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    consumer = tmp_path / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    pyproject = consumer / "pyproject.toml"
    pyproject.write_text(
        "[tool.uv.sources]\n"
        "agent-zdd = { path = '../../libs/zdd', editable = true }\n",
        encoding="utf-8",
    )
    dest_lib_dir = consumer / "libs/zdd"

    result = mm._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=pyproject,
        name="agent-zdd", raw_path="../../libs/zdd", lib="zdd",
    )
    assert result.startswith("OK"), result
    assert 'agent-zdd = { path = "libs/zdd" }' in pyproject.read_text()


# ── nested `uv`-editable dependency between two canonical libs (PR #4372) ─
#
# A canonical lib (e.g. ssh-manager) can itself depend on another canonical
# lib (e.g. agent-procutil) via its OWN escaping `uv`-editable entry. When a
# consumer materializes ssh-manager, the outer rewrite only fixes the
# CONSUMER's own top-level entry -- the just-copied ssh-manager's own
# nested entry must be fixed up too, or a real release ends up requiring
# the same path both editable and non-editable at once.


def _canonical_lib_with_dependency(
    root: Path, lib: str, *, dep_lib: str, dep_raw_path: str, version: str = "0.1.0-dev1",
) -> Path:
    """A canonical lib whose own ``pyproject.toml`` declares an escaping
    `uv`-editable dependency on another canonical lib."""
    d = _canonical_lib(root, lib, version=version, content="real = True\n")
    (d / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{version}"\n'
        f'dependencies = ["{dep_lib}"]\n'
        "\n[tool.uv.sources]\n"
        f'{dep_lib} = {{ path = "{dep_raw_path}", editable = true }}\n',
        encoding="utf-8",
    )
    return d


@pytest.mark.parametrize("cycle", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_materialize_complete_nested_closure_without_editable_edges(tmp_path: Path, cycle, reverse):
    root = tmp_path / "repo"
    _canonical_lib_with_dependency(root, "first", dep_lib="middle", dep_raw_path="../middle")
    _canonical_lib_with_dependency(root, "second", dep_lib="middle", dep_raw_path="../middle")
    _canonical_lib_with_dependency(root, "middle", dep_lib="last", dep_raw_path="../last")
    if cycle:
        _canonical_lib_with_dependency(root, "last", dep_lib="first", dep_raw_path="../first")
    else:
        _canonical_lib(root, "last", version="0.1.0-dev1", content="value = 1\n")
    consumer = root / "plugins" / "consumer"
    consumer.mkdir(parents=True)
    names = ["first", "second", "last"]
    if reverse:
        names.reverse()
    (consumer / "pyproject.toml").write_text(
        '[project]\nname="consumer"\nversion="1.0.0"\n[tool.uv.sources]\n'
        + "".join(f'{name} = {{ path="../../libs/{name}", editable=true }}\n' for name in names),
        encoding="utf-8",
    )
    before = {name: (root / "libs" / name / "pyproject.toml").read_bytes()
              for name in ("first", "second", "middle", "last")}
    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert not any(line.startswith("SKIP") for line in log), log
    assert {path.name for path in (consumer / "libs").iterdir()} == set(before)
    for manifest in consumer.rglob("pyproject.toml"):
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        for entry in data.get("tool", {}).get("uv", {}).get("sources", {}).values():
            assert "editable" not in entry
            target = (manifest.parent / entry["path"]).resolve()
            assert target.is_relative_to(consumer) and (target / "pyproject.toml").is_file()
    assert {name: (root / "libs" / name / "pyproject.toml").read_bytes() for name in before} == before


def test_materialize_uv_editable_refs_fixes_up_a_nested_canonical_dependency(tmp_path: Path):
    root = tmp_path / "repo"
    # agent-procutil: a plain canonical lib, no dependencies of its own.
    _canonical_lib(root, "agent-procutil", version="0.2.0-dev1", content="real = True\n")
    # ssh-manager: depends on agent-procutil via its own escaping entry.
    _canonical_lib_with_dependency(
        root, "ssh-manager", dep_lib="agent-procutil", dep_raw_path="../agent-procutil",
    )
    # Consumer directly depends on BOTH -- ssh-manager listed first so its
    # materialization (and the nested fixup it triggers) runs before the
    # consumer's own direct agent-procutil entry is processed.
    consumer = root / "plugins/agent-ssh"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        'dependencies = ["ssh-manager", "agent-procutil"]\n'
        "\n[tool.uv.sources]\n"
        'ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n'
        'agent-procutil = { path = "../../libs/agent-procutil", editable = true }\n',
        encoding="utf-8",
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert all(not line.startswith("SKIP") for line in log), log

    consumer_text = (consumer / "pyproject.toml").read_text()
    assert 'ssh-manager = { path = "libs/ssh-manager" }' in consumer_text
    assert 'agent-procutil = { path = "libs/agent-procutil" }' in consumer_text
    assert "editable" not in consumer_text

    nested_ssh_manager_pp = (consumer / "libs/ssh-manager/pyproject.toml").read_text()
    assert 'agent-procutil = { path = "../agent-procutil" }' in nested_ssh_manager_pp
    assert "editable" not in nested_ssh_manager_pp

    # The nested dependency materialized to the SAME sibling location the
    # consumer's own direct entry would also use.
    assert (consumer / "libs/agent-procutil/src/agent_procutil/__init__.py").read_text() == (
        "real = True\n"
    )


def test_materialize_uv_editable_refs_nested_dependency_already_materialized(tmp_path: Path):
    """Same scenario, but the consumer's own direct entry for the nested
    dependency is listed FIRST -- the top-level entry materializes
    agent-procutil before ssh-manager's own nested fixup runs, so the
    nested step must rewrite the entry WITHOUT re-copying (or failing on)
    an already-materialized sibling."""
    root = tmp_path / "repo"
    _canonical_lib(root, "agent-procutil", version="0.2.0-dev1", content="real = True\n")
    _canonical_lib_with_dependency(
        root, "ssh-manager", dep_lib="agent-procutil", dep_raw_path="../agent-procutil",
    )
    consumer = root / "plugins/agent-ssh"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        'dependencies = ["agent-procutil", "ssh-manager"]\n'
        "\n[tool.uv.sources]\n"
        'agent-procutil = { path = "../../libs/agent-procutil", editable = true }\n'
        'ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n',
        encoding="utf-8",
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert all(not line.startswith("SKIP") for line in log), log
    nested_ssh_manager_pp = (consumer / "libs/ssh-manager/pyproject.toml").read_text()
    assert 'agent-procutil = { path = "../agent-procutil" }' in nested_ssh_manager_pp
    assert "editable" not in nested_ssh_manager_pp


def test_materialize_nested_uv_editable_refs_refuses_a_missing_editable_true(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "agent-procutil", version="0.2.0-dev1", content="real = True\n")
    # ssh-manager's own nested entry is missing editable = true.
    d = _canonical_lib(root, "ssh-manager", version="0.1.0-dev1", content="real = True\n")
    (d / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0-dev1"\n'
        'dependencies = ["agent-procutil"]\n'
        "\n[tool.uv.sources]\n"
        'agent-procutil = { path = "../agent-procutil" }\n',
        encoding="utf-8",
    )
    consumer = root / "plugins/agent-ssh"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        'dependencies = ["ssh-manager"]\n'
        "\n[tool.uv.sources]\n"
        'ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n',
        encoding="utf-8",
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any(
        "missing editable = true" in line for line in log
    ), log


def test_materialize_nested_uv_editable_refs_refuses_a_path_traversal_raw_path(
    tmp_path: Path,
):
    """Review finding (PR #4372): a nested entry whose ``raw_path`` does
    NOT resolve to the expected sibling ``<consumer>/libs/<nested_lib>``
    (e.g. ``../../plugins/other``, escaping into an unrelated project)
    must be refused outright -- accepting it would copy canonical's
    `<nested_lib>` content into that OTHER location while the manifest
    still points at the wrong path, silently shipping the wrong project."""
    root = tmp_path / "repo"
    _canonical_lib(root, "other-project", version="0.1.0-dev1", content="unrelated\n")
    _canonical_lib_with_dependency(
        root, "ssh-manager", dep_lib="other-project",
        dep_raw_path="../../plugins/other",  # escapes the expected sibling shape
    )
    consumer = root / "plugins/agent-ssh"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        'dependencies = ["ssh-manager"]\n'
        "\n[tool.uv.sources]\n"
        'ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n',
        encoding="utf-8",
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("which is not" in line for line in log), log
    assert not (root / "plugins" / "other").exists()


def test_materialize_nested_uv_editable_refs_refuses_a_symlinked_canonical(
    tmp_path: Path, native_file_symlink,
):
    """Review finding (PR #4372): the nested materializer must check the
    UNRESOLVED canonical path for a symlinked ancestor BEFORE ever calling
    ``.resolve()`` on it -- resolving first would silently follow (and
    erase) the symlink, letting `copytree` import files from outside the
    trusted canonical tree during release materialization."""
    root = tmp_path / "repo"
    outside = root.parent / "outside-target"
    (outside / "agent-procutil").mkdir(parents=True)
    (outside / "agent-procutil" / "src").mkdir()
    (outside / "agent-procutil" / "src" / "smuggled.py").write_text(
        "smuggled = True\n", encoding="utf-8"
    )
    (root / "libs").mkdir(parents=True)
    native_file_symlink(root / "libs" / "agent-procutil",
        outside / "agent-procutil", target_is_directory=True
    )
    _canonical_lib_with_dependency(
        root, "ssh-manager", dep_lib="agent-procutil", dep_raw_path="../agent-procutil",
    )
    consumer = root / "plugins/agent-ssh"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        'dependencies = ["ssh-manager"]\n'
        "\n[tool.uv.sources]\n"
        'ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n',
        encoding="utf-8",
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("is a symlink" in line for line in log), log
    assert not (consumer / "libs" / "agent-procutil").exists()


def test_materialize_nested_uv_editable_refs_refuses_a_non_directory_sibling(tmp_path: Path):
    """Review finding (PR #4372): a pre-existing REGULAR FILE at the
    expected sibling path must never be silently accepted as
    "already materialized" -- rewriting the manifest over it would ship a
    dependency the promoted package can't actually import."""
    root = tmp_path / "repo"
    _canonical_lib(root, "agent-procutil", version="0.2.0-dev1", content="real = True\n")
    _canonical_lib_with_dependency(
        root, "ssh-manager", dep_lib="agent-procutil", dep_raw_path="../agent-procutil",
    )
    consumer = root / "plugins/agent-ssh"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        'dependencies = ["ssh-manager"]\n'
        "\n[tool.uv.sources]\n"
        'ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n',
        encoding="utf-8",
    )
    # A stray regular file already sits where agent-procutil should land.
    (consumer / "libs").mkdir(parents=True)
    (consumer / "libs" / "agent-procutil").write_text("not a directory\n", encoding="utf-8")

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("is not a directory" in line for line in log), log
    ssh_manager_pp = (consumer / "libs/ssh-manager/pyproject.toml").read_text()
    assert "editable = true" in ssh_manager_pp


def test_materialize_nested_uv_editable_refs_refuses_stale_mismatched_content(tmp_path: Path):
    """Review finding (PR #4372): a pre-existing directory at the expected
    sibling path whose content does NOT match canonical (stale from an
    earlier release) must never be silently accepted as "already
    materialized" -- only a byte-identical directory may be treated as
    equivalent to a fresh copy."""
    root = tmp_path / "repo"
    _canonical_lib(root, "agent-procutil", version="0.2.0-dev1", content="real = True\n")
    _canonical_lib_with_dependency(
        root, "ssh-manager", dep_lib="agent-procutil", dep_raw_path="../agent-procutil",
    )
    consumer = root / "plugins/agent-ssh"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        'dependencies = ["ssh-manager"]\n'
        "\n[tool.uv.sources]\n"
        'ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n',
        encoding="utf-8",
    )
    # A stale prior copy of agent-procutil already sits at the sibling
    # location, with content that no longer matches canonical.
    stale_dir = consumer / "libs" / "agent-procutil" / "src" / "agent_procutil"
    stale_dir.mkdir(parents=True)
    (stale_dir / "__init__.py").write_text("stale = True\n", encoding="utf-8")
    (consumer / "libs/agent-procutil/pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0-dev-stale"\n', encoding="utf-8"
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("does not match canonical" in line for line in log), log
    # The stale content is untouched, not silently overwritten.
    assert (stale_dir / "__init__.py").read_text() == "stale = True\n"


@pytest.mark.parametrize("plugin", ["agent-pull-requests", "agent-dispatch", "agent-index"])
def test_materialize_installer_engine_ref_into_copies_and_rewrites(
    tmp_path: Path, plugin: str, monkeypatch: pytest.MonkeyPatch,
):
    assert plugin in ier.ADOPTERS
    monkeypatch.setattr(ier, "PYTHON_ADOPTERS", ())
    root = tmp_path / "repo"
    engine = root / "libs" / "installer-engine"
    engine.mkdir(parents=True)
    (engine / "installer-engine.sh").write_text("# canonical sh\n", encoding="utf-8")
    (engine / "installer-engine.ps1").write_text("# canonical ps1\n", encoding="utf-8")
    consumer = root / "plugins" / plugin
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "install.sh").write_text(
        '#!/usr/bin/env bash\n. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (consumer / "scripts" / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n",
        encoding="utf-8",
    )

    log = mm.materialize_installer_engine_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert not any(line.startswith("SKIP") for line in log), log
    assert sum(1 for line in log if line.startswith("OK")) == 4
    assert (consumer / "scripts" / "installer-engine.sh").read_text() == "# canonical sh\n"
    assert (consumer / "scripts" / "installer-engine.ps1").read_text() == "# canonical ps1\n"
    assert (consumer / "scripts" / "install.sh").read_text(encoding="utf-8").splitlines() == [
        "#!/usr/bin/env bash",
        '. "$SCRIPT_DIR/installer-engine.sh"',
    ]
    assert (consumer / "scripts" / "install.ps1").read_text(encoding="utf-8").splitlines() == [
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1')"
    ]


def test_materialize_installer_engine_ref_into_reports_missing_canonical(tmp_path: Path):
    root = tmp_path / "repo"
    consumer = root / "plugins" / "agent-pull-requests"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "install.sh").write_text(
        '#!/usr/bin/env bash\n. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )

    log = mm.materialize_installer_engine_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("canonical source missing" in line for line in log)
    assert not (consumer / "scripts" / "installer-engine.sh").exists()


def test_materialize_installer_engine_ref_into_refuses_an_escaping_reference(tmp_path: Path):
    root = tmp_path / "repo"
    engine = root / "libs" / "installer-engine"
    engine.mkdir(parents=True)
    (engine / "installer-engine.sh").write_text("# canonical sh\n", encoding="utf-8")
    consumer = root / "plugins" / "agent-pull-requests"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "install.sh").write_text(
        '#!/usr/bin/env bash\n. "$SCRIPT_DIR/../../../outside/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (root / "outside").mkdir()
    (root / "outside" / "installer-engine.sh").write_text("# nope\n", encoding="utf-8")

    log = mm.materialize_installer_engine_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("is not libs/installer-engine/installer-engine.sh" in line for line in log)
    assert not (consumer / "scripts" / "installer-engine.sh").exists()


def test_materialize_installer_engine_ref_into_refuses_a_symlinked_canonical_ancestor(
    tmp_path: Path, native_file_symlink,
):
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    outside.mkdir()
    real_engine = outside / "installer-engine"
    real_engine.mkdir()
    (real_engine / "installer-engine.sh").write_text("# canonical sh\n", encoding="utf-8")
    (real_engine / "installer-engine.ps1").write_text("# canonical ps1\n", encoding="utf-8")
    (root / "libs").mkdir(parents=True)
    native_file_symlink(root / "libs" / "installer-engine", real_engine, target_is_directory=True)
    consumer = root / "plugins" / "agent-pull-requests"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "install.sh").write_text(
        '#!/usr/bin/env bash\n. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )

    log = mm.materialize_installer_engine_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("is a symlink -- refusing" in line for line in log)
    assert not (consumer / "scripts" / "installer-engine.sh").exists()


def test_materialize_installer_engine_ref_into_refuses_duplicate_source_lines(tmp_path: Path):
    root = tmp_path / "repo"
    engine = root / "libs" / "installer-engine"
    engine.mkdir(parents=True)
    (engine / "installer-engine.sh").write_text("# canonical sh\n", encoding="utf-8")
    consumer = root / "plugins" / "agent-pull-requests"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "install.sh").write_text(
        '#!/usr/bin/env bash\n'
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n'
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )

    log = mm.materialize_installer_engine_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("expected exactly one" in line for line in log)
    assert not (consumer / "scripts" / "installer-engine.sh").exists()


def test_rewrite_to_local_refuses_duplicate_source_lines(tmp_path: Path):
    script = tmp_path / "install.sh"
    script.write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n'
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )

    assert ier.rewrite_to_local(script, "sh") is False
    assert script.read_text(encoding="utf-8").count("../../../libs/installer-engine") == 2


def test_rewrite_to_local_preserves_following_comment_lines(tmp_path: Path):
    sh_script = tmp_path / "install.sh"
    sh_script.write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n'
        "# keep me\n",
        encoding="utf-8",
    )
    ps1_script = tmp_path / "install.ps1"
    ps1_script.write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n"
        "# keep me\n",
        encoding="utf-8",
    )

    assert ier.rewrite_to_local(sh_script, "sh") is True
    assert ier.rewrite_to_local(ps1_script, "ps1") is True
    assert sh_script.read_text(encoding="utf-8").splitlines() == [
        '. "$SCRIPT_DIR/installer-engine.sh"',
        "# keep me",
    ]
    assert ps1_script.read_text(encoding="utf-8").splitlines() == [
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1')",
        "# keep me",
    ]


def test_rewrite_to_local_preserves_trailing_same_line_comments(tmp_path: Path):
    sh_script = tmp_path / "install.sh"
    sh_script.write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh" # keep me\n',
        encoding="utf-8",
    )
    ps1_script = tmp_path / "install.ps1"
    ps1_script.write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1') # keep me\n",
        encoding="utf-8",
    )

    assert ier.rewrite_to_local(sh_script, "sh") is True
    assert ier.rewrite_to_local(ps1_script, "ps1") is True
    assert sh_script.read_text(encoding="utf-8").splitlines() == [
        '. "$SCRIPT_DIR/installer-engine.sh" # keep me'
    ]
    assert ps1_script.read_text(encoding="utf-8").splitlines() == [
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1') # keep me"
    ]


def test_materialize_installer_engine_ref_into_skips_malformed_local_reference_for_registered_adopter(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    engine = root / "libs" / "installer-engine"
    engine.mkdir(parents=True)
    (engine / "installer-engine.sh").write_text("# canonical sh\n", encoding="utf-8")
    consumer = root / "plugins" / "agent-pull-requests"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "install.sh").write_text(
        '#!/usr/bin/env bash\n. "$SCRIPT_DIR/missing/installer-engine.sh"\n',
        encoding="utf-8",
    )

    log = mm.materialize_installer_engine_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("neither the exact local form" in line for line in log)
    assert not (consumer / "scripts" / "installer-engine.sh").exists()


def test_materialize_installer_engine_ref_into_preserves_following_comment_lines(tmp_path: Path):
    root = tmp_path / "repo"
    engine = root / "libs" / "installer-engine"
    engine.mkdir(parents=True)
    (engine / "installer-engine.sh").write_text("# canonical sh\n", encoding="utf-8")
    (engine / "installer-engine.ps1").write_text("# canonical ps1\n", encoding="utf-8")
    consumer = root / "plugins" / "agent-pull-requests"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "install.sh").write_text(
        '#!/usr/bin/env bash\n'
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n'
        "# keep me\n",
        encoding="utf-8",
    )
    (consumer / "scripts" / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n"
        "# keep me\n",
        encoding="utf-8",
    )

    log = mm.materialize_installer_engine_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert sum(1 for line in log if line.startswith("OK")) == 2
    assert (consumer / "scripts" / "install.sh").read_text(encoding="utf-8").splitlines() == [
        "#!/usr/bin/env bash",
        '. "$SCRIPT_DIR/installer-engine.sh"',
        "# keep me",
    ]
    assert (consumer / "scripts" / "install.ps1").read_text(encoding="utf-8").splitlines() == [
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1')",
        "# keep me",
    ]


def test_materialize_launch_wrapper_assets_into_copies_manifested_files(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    source_dir = root / "worktree-manager" / "bin"
    source_dir.mkdir(parents=True)
    for name, content in {
        "launch-session.sh": "#!/usr/bin/env bash\n",
        "pane-wrapper.sh": "#!/usr/bin/env bash\n",
        "session-options.sh": "session opts\n",
    }.items():
        (source_dir / name).write_text(content, encoding="utf-8")
    plugin = root / "plugins" / "agent-worktrees"
    plugin.mkdir(parents=True)
    (plugin / lwar.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.launch-wrapper-assets",
                "version": 1,
                "canonicalDir": "worktree-manager/bin",
                "files": ["launch-session.sh", "pane-wrapper.sh", "session-options.sh"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    dest_plugin = root / "preview" / "plugins" / "agent-worktrees"
    dest_plugin.mkdir(parents=True)

    log = mm.materialize_launch_wrapper_assets_into(
        source_consumer_dir=plugin,
        dest_consumer_dir=dest_plugin,
        canonical_root=root,
        dest_root=root / "preview",
    )

    assert sum(1 for line in log if line.startswith("OK")) == 3
    assert (dest_plugin / "bin" / "launch-session.sh").read_text() == "#!/usr/bin/env bash\n"
    assert (dest_plugin / "bin" / "pane-wrapper.sh").read_text() == "#!/usr/bin/env bash\n"
    assert (dest_plugin / "bin" / "session-options.sh").read_text() == "session opts\n"


def test_materialize_launch_wrapper_assets_into_reports_missing_canonical_file(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    (root / "worktree-manager" / "bin").mkdir(parents=True)
    plugin = root / "plugins" / "agent-worktrees"
    plugin.mkdir(parents=True)
    (plugin / lwar.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.launch-wrapper-assets",
                "version": 1,
                "canonicalDir": "worktree-manager/bin",
                "files": ["launch-session.sh"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    dest_plugin = root / "preview" / "plugins" / "agent-worktrees"
    dest_plugin.mkdir(parents=True)

    log = mm.materialize_launch_wrapper_assets_into(
        source_consumer_dir=plugin,
        dest_consumer_dir=dest_plugin,
        canonical_root=root,
        dest_root=root / "preview",
    )

    assert any("canonical source missing" in line for line in log)
    assert not (dest_plugin / "bin" / "launch-session.sh").exists()


def test_materialize_launch_wrapper_assets_into_refuses_escaping_source_dir(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "launch-session.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    plugin = root / "plugins" / "agent-worktrees"
    plugin.mkdir(parents=True)
    (plugin / lwar.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.launch-wrapper-assets",
                "version": 1,
                "canonicalDir": "../outside",
                "files": ["launch-session.sh"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    dest_plugin = root / "preview" / "plugins" / "agent-worktrees"
    dest_plugin.mkdir(parents=True)

    log = mm.materialize_launch_wrapper_assets_into(
        source_consumer_dir=plugin,
        dest_consumer_dir=dest_plugin,
        canonical_root=root,
        dest_root=root / "preview",
    )

    assert any("canonicalDir must be a non-empty string" in line for line in log)
    assert not (dest_plugin / "bin" / "launch-session.sh").exists()


@pytest.mark.parametrize(("same_root", "nested", "stale"), [
    (True, False, True),
    (False, False, False),
    (True, True, False),
    (False, True, True),
], ids=["same-direct-stale", "separate-direct-absent", "same-transitive-absent", "separate-transitive-stale"])
def test_materialize_refreshes_owned_driver_copies_before_they_are_consumed(
    tmp_path: Path, same_root: bool, nested: bool, stale: bool,
) -> None:
    from test_sync_installer_engine import _promotion_installer_source

    source = tmp_path / "source"
    _promotion_installer_source(source, stale=False, consumer_names={"agent-vault"})
    contract = mm.mie.ier
    names = set((*contract.PYTHON_FILES, *contract.SUPPORT_FILES, *contract.STANDALONE_NATIVE_FILES))
    driver = source / "libs/update-driver"
    (driver / "scripts").mkdir(parents=True)
    (driver / "src/update_driver").mkdir(parents=True)
    (driver / "src/update_driver/__init__.py").write_bytes(b"VALUE = 1\n")
    (driver / "pyproject.toml").write_text(
        '[project]\nname = "update-driver"\nversion = "1.0"\n', encoding="utf-8",
    )
    if stale:
        for name in names:
            (driver / "scripts" / name).write_bytes(b"# reviewed old library copy\n")
    payload = source / "plugins/agent-vault"
    dependency = "outer" if nested else "update-driver"
    declarations = f'{dependency} = {{path = "../../libs/{dependency}", editable = true}}\n'
    if nested:
        outer = source / "libs/outer"
        (outer / "src/outer").mkdir(parents=True)
        (outer / "src/outer/__init__.py").write_bytes(b"VALUE = 2\n")
        (outer / "pyproject.toml").write_text(
            '[project]\nname = "outer"\nversion = "1.0"\n'
            '[tool.uv.sources]\nupdate-driver = {path = "../update-driver", editable = true}\n',
            encoding="utf-8",
        )
        declarations += 'driver-alias = {path = "../../libs/update-driver", editable = true}\n'
    (payload / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0"\n[tool.uv.sources]\n' + declarations,
        encoding="utf-8",
    )
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    candidate = source if same_root else tmp_path / "candidate"
    if not same_root:
        shutil.copytree(source, candidate)
    log = mm.materialize(candidate, canonical_root=source)
    assert not any(line.startswith("SKIP") for line in log), log
    copied = candidate / "plugins/agent-vault/libs/update-driver"
    for name in names:
        assert (copied / "scripts" / name).read_bytes() == before[Path("libs/installer-engine") / name]
        assert (candidate / "libs/installer-engine" / name).read_bytes() == before[Path("libs/installer-engine") / name]
    assert mm.uer.materialized_local_source_problems(candidate / "plugins/agent-vault") == []
    if nested:
        assert any("(alias)" in line for line in log), log
    if not same_root:
        assert before == {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
