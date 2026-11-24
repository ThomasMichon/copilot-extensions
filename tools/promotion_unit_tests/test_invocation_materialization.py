"""Actor-free controls for the pinned Invocation candidate-copy owner."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

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
    for library, package, names in (
        ("process-telemetry", "process_telemetry",
         ("command_errors.py", "command_entry.py", "diagnostic_logs.py", "append.py")),
        ("agent-procutil", "agent_procutil",
         ("diagnostics.py", "python_cache_permissions.py", "_daemon_instance_owner.py")),
        ("single-instance-lease", "single_instance_lease", ("operation.py", "lease.py")),
    ):
        for name in names:
            relative = Path("libs") / library / "src" / package / name
            (source / relative).parent.mkdir(parents=True, exist_ok=True)
            (source / relative).write_bytes((ROOT / relative).read_bytes())
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


def _legacy_entry_templates(root: Path) -> None:
    directory = root / "libs/payload-invocation/templates"
    for name in ("posix-shim.tmpl", "powershell-shim.tmpl"):
        path = directory / name
        text = path.read_text().replace("# payload-command-evidence: 1\n", "")
        text = text.replace(
            '"$_payload_root/scripts/command-errors.pyz" run "$_command" "$_plugin" "$_module" --',
            '-m "$_module"',
        ).replace(
            r"(Join-Path $_payloadRoot 'scripts\command-errors.pyz') run $_command $_plugin $_module --",
            "-m $_module",
        )
        assert "command-errors.pyz" not in text
        path.write_text(text, encoding="utf-8")


def test_command_evidence_inputs_have_exact_unit_source_admission() -> None:
    spec = importlib.util.spec_from_file_location(
        "_unit_evidence_inputs", ROOT / "libs/payload-invocation/command_evidence.py",
    )
    assert spec and spec.loader
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    sources = json.loads((ROOT / "tools/promotion-unit-manifest.json").read_bytes())["sources"]
    for package, library, names in producer.INPUTS:
        for name in names:
            relative = (Path("libs") / library / "src" / package / name).as_posix()
            assert relative in sources, relative
            assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == sources[relative]


@pytest.mark.parametrize("toolkit", [False, True])
def test_current_preparer_preserves_declared_legacy_sources_without_evidence_inputs(
    tmp_path: Path, toolkit: bool,
) -> None:
    if toolkit:
        from test_materialize_main import _invocation_candidate

        source, candidate = _invocation_candidate(tmp_path)
        plugin = candidate / "plugins/agent-worktrees"
    else:
        source, candidate, plugin = _candidate(tmp_path)
    _legacy_entry_templates(source)
    for name in ("command_errors.py", "command_entry.py"):
        (source / "libs/process-telemetry/src/process_telemetry" / name).unlink()
    (source / "libs/payload-invocation/command_evidence.py").unlink(missing_ok=True)
    log = owner.materialize(candidate, canonical_root=source)
    assert log and all(line.startswith("MATERIALIZE Invocation ") for line in log), log
    assert not (plugin / "scripts/command-errors.pyz").exists()
    for path in (plugin / "bin").rglob("*"):
        if path.is_file():
            assert b"command-errors.pyz" not in path.read_bytes()


@pytest.mark.parametrize("missing", ["marker", "input"])
def test_declared_evidence_refuses_incomplete_current_source_before_writes(
    tmp_path: Path, missing: str,
) -> None:
    source, candidate, _plugin = _candidate(tmp_path)
    if missing == "marker":
        path = source / "libs/payload-invocation/templates/posix-shim.tmpl"
        path.write_text(path.read_text().replace("# payload-command-evidence: 1\n", ""))
    else:
        (source / "libs/process-telemetry/src/process_telemetry/command_entry.py").unlink()
    before = {path.relative_to(candidate): path.read_bytes()
              for path in candidate.rglob("*") if path.is_file()}
    log = owner.materialize(candidate, canonical_root=source)
    assert len(log) == 1 and log[0].startswith("SKIP Invocation materialization:"), log
    assert before == {path.relative_to(candidate): path.read_bytes()
                      for path in candidate.rglob("*") if path.is_file()}


def test_uv_launcher_is_materialized_with_prepared_exact_environment(tmp_path: Path) -> None:
    source, candidate, plugin = _candidate(tmp_path)
    for root in (source, candidate):
        manifest = root / "plugins/sample/payload-invocation.json"
        data = json.loads(manifest.read_text())
        data.update(bootstrapPython="3.13.12", pythonLauncher="uv")
        manifest.write_text(json.dumps(data))
    log = owner.materialize(candidate, canonical_root=source)
    assert log and all(line.startswith("MATERIALIZE Invocation ") for line in log), log
    shell = (plugin / "bin/sample").read_text()
    powershell = (plugin / "bin/sample.ps1").read_text()
    arguments = "run --no-project --no-config --no-env-file --no-python-downloads --managed-python"
    assert f'"$_uv" {arguments} --python "$AGENT_RT_PY" python' in shell
    assert f'$_uv {arguments} --python $_py python' in powershell
    for text in (shell, powershell):
        assert "UV_WORKING_DIR" in text and "UV_WORKING_DIRECTORY" in text
        assert "runtime is not prepared; run the owning scripts/install." in text
    assert (plugin / "scripts/bootstrap-python-version.txt").read_bytes() == b"cpython@3.13.12\n"


@pytest.mark.parametrize(("manifest_name", "payload_directory"), [
    ("launch-wrapper-assets.json", "scripts/libs/payload-invocation"),
    ("packaged-assets.json", "scripts/runtime-input-provider/libs/payload-invocation"),
    ("packaged-assets.json", "scripts/runtime-source-tools/libs/payload-invocation"),
], ids=["launch-wrapper", "input-provider", "source-tools"])
def test_packaged_invocation_renders_without_checkout(
    tmp_path: Path, manifest_name: str, payload_directory: str,
) -> None:
    record = json.loads((ROOT / "worktree-manager" / manifest_name).read_bytes())
    groups = record.get("groups", [record])
    group = next(item for item in groups if item["payloadDir"] == payload_directory)
    packaged = tmp_path / "packaged" / payload_directory
    packaged.mkdir(parents=True)
    for name in group["files"]:
        (packaged / name).write_bytes((ROOT / group["canonicalDir"] / name).read_bytes())
    spec = importlib.util.spec_from_file_location("_packaged_invocation", packaged / "generate.py")
    assert spec and spec.loader
    generator = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(generator)
        source, candidate, plugin = _candidate(tmp_path / "fixture")
        manifest = plugin / "payload-invocation.json"
        for launcher in ("direct", "uv"):
            for root in (source, candidate):
                declaration = root / "plugins/sample/payload-invocation.json"
                data = json.loads(declaration.read_bytes())
                data.update(pythonLauncher=launcher, bootstrapPython="3.13.12")
                declaration.write_text(json.dumps(data), encoding="utf-8")
            log = owner.materialize(candidate, canonical_root=source)
            assert log and all(line.startswith("MATERIALIZE Invocation ") for line in log), log
            for path, output in generator.expected_files(manifest, canonical_root=source).items():
                assert path.read_bytes() == (
                    output.encode("utf-8") if isinstance(output, str) else output
                )
    finally:
        sys.dont_write_bytecode = previous
    assert not list(packaged.rglob("__pycache__"))


@pytest.mark.parametrize("selection", [
    "null", "boolean", "unknown", "missing-declaration", "dispatcher",
])
def test_uv_launcher_refuses_incomplete_contract_before_candidate_writes(
    tmp_path: Path, selection: str,
) -> None:
    source, candidate, plugin = _candidate(tmp_path)
    for root in (source, candidate):
        manifest = root / "plugins/sample/payload-invocation.json"
        data = json.loads(manifest.read_text())
        data.update(bootstrapPython="3.13.12", pythonLauncher="uv")
        if selection in ("null", "boolean", "unknown"):
            data["pythonLauncher"] = {"null": None, "boolean": True, "unknown": "unsupported"}[selection]
        elif selection == "missing-declaration":
            del data["bootstrapPython"]
        else:
            data["payloadDispatcher"] = {"posix": "scripts/install.sh", "windows": "scripts/install.ps1"}
        manifest.write_text(json.dumps(data))
    before = {path.relative_to(candidate): path.read_bytes()
              for path in candidate.rglob("*") if path.is_file()}
    log = owner.materialize(candidate, canonical_root=source)
    assert len(log) == 1 and log[0].startswith("SKIP Invocation materialization:"), log
    assert before == {path.relative_to(candidate): path.read_bytes()
                      for path in candidate.rglob("*") if path.is_file()}
    assert not (plugin / "bin").exists()


def test_pinned_invocation_owner_refuses_corrupted_actual_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, candidate, plugin = _candidate(tmp_path)
    target = plugin / "bin/sample.ps1"
    original = Path.write_text

    def corrupt(path: Path, text: str, *args: Any, **kwargs: Any) -> int:
        if path == target:
            text += "\n# corrupt actual candidate output\n"
        return original(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", corrupt)
    log = owner.materialize(candidate, canonical_root=source)
    assert any(line.startswith("SKIP Invocation materialization:") for line in log), log
    assert b"corrupt actual candidate output" in target.read_bytes()
    assert not any(line.startswith("MATERIALIZE Invocation ") for line in log), log


def test_pinned_invocation_owner_preserves_hardlinked_candidate_output(tmp_path: Path) -> None:
    source, candidate, plugin = _candidate(tmp_path)
    original = tmp_path / "unrelated-command"
    original.write_bytes(b"preserve unrelated command\n")
    output = plugin / "bin" / "sample.ps1"
    output.parent.mkdir()
    os.link(original, output)
    log = owner.materialize(candidate, canonical_root=source)
    assert len(log) == 1 and log[0].startswith("SKIP Invocation materialization:") and "custody" in log[0], log
    assert output.read_bytes() == original.read_bytes() == b"preserve unrelated command\n"


@pytest.mark.parametrize("blocked", ["output-directory", "parent-file"])
def test_source_phase_refuses_unmaterializable_output_types(tmp_path: Path, blocked: str) -> None:
    source, _candidate_root, _plugin = _candidate(tmp_path)
    spec = importlib.util.spec_from_file_location(
        "_unit_invocation_source_check", ROOT / "libs/payload-invocation/generate.py",
    )
    assert spec and spec.loader
    generator = importlib.util.module_from_spec(spec)
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
    ("libs/payload-invocation/generate.py", {
        "libs/payload-invocation/runtime_admission.py", "libs/payload-invocation/template_rendering.py",
    }),
    ("libs/payload-invocation/runtime_admission.py", {
        "libs/payload-invocation/bootstrap_python.py", "libs/payload-invocation/command_evidence.py",
    }),
], ids=["candidate-producer", "canonical-packager", "bootstrap-provider"])
def test_pinned_invocation_loader_closure_is_statically_admitted(relative: str, expected: set[str]) -> None:
    from release_helper_loaders import dynamic_loads

    assert dynamic_loads(ast.parse((ROOT / relative).read_bytes()), relative) == expected


@pytest.mark.parametrize("phase", ["source", "candidate"])
def test_planned_output_ancestor_conflicts_refuse_before_writes(tmp_path: Path, phase: str) -> None:
    source, candidate, plugin = _candidate(tmp_path)
    for root in (source, candidate):
        manifest = root / "plugins/sample/payload-invocation.json"
        data = json.loads(manifest.read_text())
        data["outputDir"] = "scripts/emit-command-catalog.sh"
        manifest.write_text(json.dumps(data), encoding="utf-8")
    before = {path.relative_to(tmp_path): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}
    if phase == "source":
        spec = importlib.util.spec_from_file_location(
            "_unit_invocation_conflict_check", ROOT / "libs/payload-invocation/generate.py",
        )
        assert spec and spec.loader
        generator = importlib.util.module_from_spec(spec)
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
def test_source_preparation_materializes_declared_invocation_outputs(tmp_path: Path, stale: bool) -> None:
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
def test_single_invocation_consumer_uses_canonical_manifest_and_pinned_producer(
    tmp_path: Path, initial: str,
) -> None:
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


@pytest.mark.parametrize("selection", ["absent", "matching", "different", "malformed", "withdrawn"])
def test_bootstrap_request_decisions_are_qualified(tmp_path: Path, selection: str) -> None:
    source, candidate, plugin = _candidate(tmp_path)
    for root in (source, candidate):
        manifest = root / "plugins/sample/payload-invocation.json"
        data = json.loads(manifest.read_text())
        if selection in {"matching", "different", "malformed"}:
            data["bootstrapPython"] = "3.11" if selection == "malformed" else "3.11.11"
        manifest.write_text(json.dumps(data))
        requirement = ">=3.12" if selection == "different" else ">=3.11"
        (manifest.parent / "pyproject.toml").write_text(
            '[project]\nname="sample"\nversion="1"\nrequires-python=' + json.dumps(requirement),
        )
    declaration = plugin / "scripts/bootstrap-python-version.txt"
    if selection == "withdrawn":
        declaration.write_text("cpython@3.11.11\n")
    before = {path.relative_to(candidate): path.read_bytes()
              for path in candidate.rglob("*") if path.is_file()}
    log = owner.materialize(candidate, canonical_root=source)
    if selection in {"malformed", "withdrawn"}:
        reason = "exact stable CPython" if selection == "malformed" else "obsolete bootstrap Python request"
        assert len(log) == 1 and log[0].startswith("SKIP") and reason in log[0]
        assert before == {path.relative_to(candidate): path.read_bytes()
                          for path in candidate.rglob("*") if path.is_file()}
    else:
        assert log and all(line.startswith("MATERIALIZE Invocation ") for line in log), log
        if selection in {"matching", "different"}:
            assert declaration.read_bytes() == b"cpython@3.11.11\n"
        else:
            assert not declaration.exists()


@pytest.mark.parametrize(("field", "enabled"), [
    pytest.param("catalogGate", False, id="gate-legacy"),
    pytest.param("catalogGate", True, id="gate-selected"),
    pytest.param("payloadDispatcher", False, id="dispatcher-legacy"),
    pytest.param("payloadDispatcher", True, id="dispatcher-selected"),
])
def test_bootstrap_output_does_not_claim_declared_input_ownership(
    tmp_path: Path, field: str, enabled: bool,
) -> None:
    source, candidate, plugin = _candidate(tmp_path)
    relative = "scripts/bootstrap-python-version.txt"
    for root in (source, candidate):
        manifest = root / "plugins/sample/payload-invocation.json"
        data = json.loads(manifest.read_text())
        data[field] = {"posix": relative, "windows": relative} if field == "payloadDispatcher" else relative
        if enabled:
            data["bootstrapPython"] = "3.11.11"
        manifest.write_text(json.dumps(data))
        (manifest.parent / relative).write_text("# declared input\n")
        (manifest.parent / "pyproject.toml").write_text(
            '[project]\nname="sample"\nversion="1"\nrequires-python=">=3.11"\n',
        )
    before = {path.relative_to(candidate): path.read_bytes()
              for path in candidate.rglob("*") if path.is_file()}
    log = owner.materialize(candidate, canonical_root=source)
    if enabled:
        assert len(log) == 1 and "conflicts with a declared input" in log[0]
        assert before == {path.relative_to(candidate): path.read_bytes()
                          for path in candidate.rglob("*") if path.is_file()}
    else:
        assert log and all(line.startswith("MATERIALIZE Invocation ") for line in log), log
    assert (plugin / relative).read_text() == "# declared input\n"
