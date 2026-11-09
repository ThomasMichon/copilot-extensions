"""Actor-free controls for the pinned Invocation candidate-copy owner."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import shutil

import pytest

import materialize_payload_invocation as owner
import prepare_runtime_source as preparation

ROOT = Path(__file__).resolve().parents[2]


def _candidate(tmp_path: Path, *, stale: bool = False) -> tuple[Path, Path, Path]:
    source, candidate = tmp_path / "source", tmp_path / "candidate"
    templates = source / "libs/payload-invocation/templates"
    templates.mkdir(parents=True)
    for template in (ROOT / "libs/payload-invocation/templates").glob("*.tmpl"):
        (templates / template.name).write_bytes(template.read_bytes())
    plugin = source / "plugins/sample"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    for name in ("install.sh", "install.ps1", "resolve-runtime.sh", "resolve-runtime.ps1"):
        (scripts / name).write_bytes(b"# synthetic installer input\n")
    (plugin / "payload-invocation.json").write_text(json.dumps({
        "schema": "copilot-extensions.payload-invocation", "version": 1,
        "command": "sample", "module": "sample.cli", "purpose": "Sample command",
        "runtimeRoot": ".sample", "noSelfProvisionEnv": "SAMPLE_NO_PROVISION",
    }), encoding="utf-8")
    if stale:
        (plugin / "bin").mkdir()
        (plugin / "bin/sample.ps1").write_bytes(b"# stale generated shim\n")
    (source / "libs/payload-invocation/generate.py").write_text(
        'raise RuntimeError("candidate generator must never execute")\n', encoding="utf-8",
    )
    shutil.copytree(source, candidate)
    return source, candidate, candidate / "plugins/sample"


@pytest.mark.parametrize("stale", [False, True], ids=["absent", "stale"])
def test_pinned_invocation_owner_replaces_candidate_outputs_without_running_them(
    tmp_path: Path, stale: bool,
) -> None:
    source, candidate, plugin = _candidate(tmp_path, stale=stale)
    before = {path.relative_to(source): path.read_bytes()
              for path in source.rglob("*") if path.is_file()}
    log = owner.materialize(candidate, canonical_root=source)
    assert log and all(line.startswith("MATERIALIZE Invocation ") for line in log), log
    for name in ("sample", "sample.ps1", "sample.cmd"):
        content = (plugin / "bin" / name).read_bytes()
        assert b"sample" in content
        if name != "sample.cmd":
            assert b"sample.cli" in content
        assert b"stale generated shim" not in content
    assert (plugin / "scripts/emit-command-catalog.sh").is_file()
    assert (plugin / "scripts/emit-command-catalog.ps1").is_file()
    assert before == {path.relative_to(source): path.read_bytes()
                      for path in source.rglob("*") if path.is_file()}


def test_pinned_invocation_owner_refuses_corrupted_actual_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, candidate, plugin = _candidate(tmp_path)
    target = plugin / "bin/sample.ps1"
    original = Path.write_text

    def corrupt(path: Path, text: str, *args, **kwargs):
        if path == target:
            text += "\n# corrupt actual candidate output\n"
        return original(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", corrupt)
    log = owner.materialize(candidate, canonical_root=source)
    assert any(line.startswith("SKIP Invocation materialization:") for line in log), log
    assert b"corrupt actual candidate output" in target.read_bytes()
    assert not any(line.startswith("MATERIALIZE Invocation ") for line in log), log


@pytest.mark.parametrize("blocked", ["output-directory", "parent-file"])
def test_source_phase_refuses_unmaterializable_output_types(tmp_path, blocked):
    source, _candidate_root, _plugin = _candidate(tmp_path)
    spec = owner.importlib.util.spec_from_file_location(
        "_unit_invocation_source_check", ROOT / "libs/payload-invocation/generate.py",
    )
    assert spec and spec.loader
    generator = owner.importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    manifest = source / "plugins/sample/payload-invocation.json"
    output = manifest.parent / "bin/sample.ps1"
    if blocked == "output-directory":
        output.mkdir(parents=True)
    else:
        output.parent.write_bytes(b"not a directory")
    with pytest.raises(ValueError, match="generated output has unsafe custody"):
        generator.source_check(manifest, canonical_root=source)


@pytest.mark.parametrize(("relative", "expected"), [
    ("tools/materialize_payload_invocation.py", {"libs/payload-invocation/generate.py"}),
    ("libs/payload-invocation/generate.py", {"libs/payload-invocation/runtime_admission.py"}),
], ids=["candidate-producer", "canonical-packager"])
def test_pinned_invocation_loader_closure_is_statically_admitted(relative, expected):
    from release_helper_loaders import dynamic_loads

    assert dynamic_loads(ast.parse((ROOT / relative).read_bytes()), relative) == expected


@pytest.mark.parametrize("phase", ["source", "candidate"])
def test_planned_output_ancestor_conflicts_refuse_before_writes(tmp_path, phase):
    source, candidate, plugin = _candidate(tmp_path)
    for root in (source, candidate):
        manifest = root / "plugins/sample/payload-invocation.json"
        data = json.loads(manifest.read_text())
        data["outputDir"] = "scripts/emit-command-catalog.sh"
        manifest.write_text(json.dumps(data), encoding="utf-8")
    before = {path.relative_to(tmp_path): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}
    if phase == "source":
        spec = owner.importlib.util.spec_from_file_location(
            "_unit_invocation_conflict_check", ROOT / "libs/payload-invocation/generate.py",
        )
        assert spec and spec.loader
        generator = owner.importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        with pytest.raises(ValueError, match="generated output path conflict"):
            generator.source_check(source / "plugins/sample/payload-invocation.json", canonical_root=source)
    else:
        log = owner.materialize(candidate, canonical_root=source)
        assert len(log) == 1 and log[0].startswith("SKIP Invocation materialization:"), log
        assert "generated output path conflict" in log[0], log
    assert before == {path.relative_to(tmp_path): path.read_bytes()
                      for path in tmp_path.rglob("*") if path.is_file()}
    assert not (plugin / "scripts/emit-command-catalog.sh").exists()


@pytest.mark.parametrize("stale", [False, True], ids=["absent", "stale"])
def test_source_preparation_materializes_declared_invocation_outputs(tmp_path, stale):
    source, _candidate_root, _plugin = _candidate(tmp_path, stale=stale)
    (source / "plugins/sample/pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "1.0"\n', encoding="utf-8",
    )
    before = {path.relative_to(source): path.read_bytes()
              for path in source.rglob("*") if path.is_file()}
    destination = tmp_path / "prepared/sample"
    preparation._materialize(source, "sample", destination)
    for name in ("sample", "sample.ps1", "sample.cmd"):
        assert b"sample" in (destination / "bin" / name).read_bytes()
        assert b"stale generated shim" not in (destination / "bin" / name).read_bytes()
    assert (destination / "scripts/emit-command-catalog.sh").is_file()
    assert (destination / "scripts/emit-command-catalog.ps1").is_file()
    assert before == {path.relative_to(source): path.read_bytes()
                      for path in source.rglob("*") if path.is_file()}


@pytest.mark.parametrize("initial", ["absent", "stale", "foreign-manifest"])
def test_single_invocation_consumer_uses_canonical_manifest_and_pinned_producer(tmp_path, initial):
    source, _candidate_root, _plugin = _candidate(tmp_path, stale=initial == "stale")
    consumer = source / "plugins/sample"
    destination = tmp_path / "individual/sample"
    shutil.copytree(consumer, destination)
    if initial == "foreign-manifest":
        manifest = destination / "payload-invocation.json"
        data = json.loads(manifest.read_text())
        data["purpose"] = "Foreign replacement"
        manifest.write_text(json.dumps(data), encoding="utf-8")
    before = {path.relative_to(tmp_path): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}
    log = owner.materialize(destination, canonical_root=source, source_consumer=consumer)
    if initial == "foreign-manifest":
        assert log and all(line.startswith("SKIP Invocation materialization:") for line in log), log
        assert before == {path.relative_to(tmp_path): path.read_bytes()
                          for path in tmp_path.rglob("*") if path.is_file()}
    else:
        assert log and all(line.startswith("MATERIALIZE Invocation ") for line in log), log
        assert b"sample.cli" in (destination / "bin/sample.ps1").read_bytes()
        assert b"stale generated shim" not in (destination / "bin/sample.ps1").read_bytes()
    assert {path.relative_to(source): path.read_bytes()
            for path in source.rglob("*") if path.is_file()} == {
                path.relative_to("source"): content for path, content in before.items()
                if path.parts[0] == "source"
            }
