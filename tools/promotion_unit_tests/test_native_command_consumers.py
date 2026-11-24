"""Actor-free qualification of explicit native-only evidence materialization."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any

import materialize_main as root_owner
import materialize_payload_invocation as owner
import pytest

ROOT = Path(__file__).resolve().parents[2]
NAMES = {
    "command-errors.sh", "command-errors.ps1", "command-causes.tsv",
    "private-paths.sh", "private-paths.ps1",
}


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*") if path.is_file() and not path.is_symlink()
    }


def _candidate(tmp_path: Path) -> tuple[Path, Path, Path]:
    from test_materialize_main import _copy_invocation_source_inputs

    source, candidate = tmp_path / "s", tmp_path / "c"
    _copy_invocation_source_inputs(ROOT, source)
    plugin = source / "plugins/native-only"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(json.dumps({"name": "native-only", "version": "1"}))
    (plugin / "native-command-errors.json").write_text(json.dumps({
        "version": 1, "consumer": "native-only", "outputDir": "support",
    }))
    (source / "libs/payload-invocation/native_consumers.py").write_text(
        'raise RuntimeError("consumer source code must not execute")\n',
    )
    shutil.copytree(source, candidate)
    return source, candidate, candidate / "plugins/native-only"


def _selection(plugin: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((plugin / "support/index.json").read_bytes())
    return value


def test_native_only_candidate_publishes_complete_source_bound_bundle(tmp_path: Path) -> None:
    source, candidate, plugin = _candidate(tmp_path)
    before = _snapshot(source)
    log = owner.materialize(candidate, canonical_root=source)
    assert len(log) == 6 and not any(line.startswith("SKIP") for line in log), log
    index = _selection(plugin)
    assert index["consumer"] == "native-only"
    assert index["schema"] == "copilot-extensions.native-command-errors"
    assert set(index["files"]) == NAMES
    for name, digest in index["files"].items():
        content = (plugin / "support" / index["bundle"] / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == digest
    assert not (plugin / "payload-invocation.json").exists()
    assert not (plugin / "bin").exists()
    assert not list(plugin.rglob("*.pyz"))
    assert before == _snapshot(source)


def test_root_and_single_consumer_materialization_reach_native_only_owner(tmp_path: Path) -> None:
    source, candidate, plugin = _candidate(tmp_path)
    log = root_owner.materialize(candidate, canonical_root=source)
    assert not any(line.startswith("SKIP Invocation") for line in log), log
    assert (plugin / "support/index.json").is_file()
    copied = tmp_path / "individual"
    shutil.copytree(source / "plugins/native-only", copied)
    log = root_owner.materialize_payload_invocation_into(
        source_consumer_dir=source / "plugins/native-only",
        dest_consumer_dir=copied, canonical_root=source,
    )
    assert len(log) == 6 and not any(line.startswith("SKIP") for line in log), log
    assert _selection(copied) == _selection(plugin)


@pytest.mark.parametrize(("field", "value"), [
    ("outputDir", "../outside"),
    ("outputDir", "C:/outside"),
    ("outputDir", "support/../outside"),
    ("consumer", "another-plugin"),
    ("version", True),
    ("version", 2),
], ids=["escape", "drive", "lexical", "foreign", "boolean", "future"])
def test_native_declaration_refusals_precede_all_writes(
    tmp_path: Path, field: str, value: object,
) -> None:
    source, candidate, _plugin = _candidate(tmp_path)
    for root in (source, candidate):
        manifest = root / "plugins/native-only/native-command-errors.json"
        data = json.loads(manifest.read_bytes())
        data[field] = value
        manifest.write_text(json.dumps(data))
    before = _snapshot(candidate)
    log = owner.materialize(candidate, canonical_root=source)
    assert len(log) == 1 and log[0].startswith("SKIP Invocation materialization:"), log
    assert before == _snapshot(candidate)


def test_native_declaration_cannot_replace_the_canonical_consumer(tmp_path: Path) -> None:
    source, candidate, plugin = _candidate(tmp_path)
    declaration = plugin / "native-command-errors.json"
    data = json.loads(declaration.read_bytes())
    data["outputDir"] = "elsewhere"
    declaration.write_text(json.dumps(data))
    before = _snapshot(candidate)
    log = owner.materialize(candidate, canonical_root=source)
    assert len(log) == 1 and "differs from canonical source" in log[0], log
    assert before == _snapshot(candidate)


def test_static_consumer_causes_preserve_safe_inner_classifications(tmp_path: Path) -> None:
    source, candidate, plugin = _candidate(tmp_path)
    for root in (source, candidate):
        manifest = root / "plugins/native-only/native-command-errors.json"
        data = json.loads(manifest.read_bytes())
        data["causes"] = [{
            "code": "consumer-request-refused", "message": "The selected consumer request was refused",
        }]
        manifest.write_text(json.dumps(data))
    assert len(owner.materialize(candidate, canonical_root=source)) == 6
    index = _selection(plugin)
    catalog = (plugin / "support" / index["bundle"] / "command-causes.tsv").read_text()
    assert "consumer-request-refused\tThe selected consumer request was refused\n" in catalog
    assert "python-unavailable\t" in catalog


@pytest.mark.parametrize("cause", [
    {"code": "uv-unavailable", "message": "A redefinition"},
    {"code": "consumer-error", "message": 'Quoted "runtime" text'},
    {"code": "consumer-error", "message": "Text\nwith a control"},
], ids=["redefinition", "json-quote", "control"])
def test_unsafe_consumer_cause_data_cannot_publish_outputs(tmp_path: Path, cause: object) -> None:
    source, candidate, _plugin = _candidate(tmp_path)
    for root in (source, candidate):
        manifest = root / "plugins/native-only/native-command-errors.json"
        data = json.loads(manifest.read_bytes())
        data["causes"] = [cause]
        manifest.write_text(json.dumps(data))
    before = _snapshot(candidate)
    log = owner.materialize(candidate, canonical_root=source)
    assert len(log) == 1 and log[0].startswith("SKIP Invocation materialization:"), log
    assert before == _snapshot(candidate)


def test_incomplete_native_provider_cannot_publish_any_candidate_output(tmp_path: Path) -> None:
    source, candidate, _plugin = _candidate(tmp_path)
    (source / "libs/agent-procutil/src/agent_procutil/private_paths.sh").unlink()
    before = _snapshot(candidate)
    log = owner.materialize(candidate, canonical_root=source)
    assert len(log) == 1 and log[0].startswith("SKIP Invocation materialization:"), log
    assert before == _snapshot(candidate)


def test_native_and_invocation_output_ancestor_owners_cannot_overlap(tmp_path: Path) -> None:
    from promotion_unit_tests.test_invocation_materialization import (
        _candidate as invocation,
    )

    source, candidate, _plugin = invocation(tmp_path)
    for root in (source, candidate):
        plugin = root / "plugins/sample"
        (plugin / "plugin.json").write_text(json.dumps({"name": "sample"}))
        (plugin / "native-command-errors.json").write_text(json.dumps({
            "version": 1, "consumer": "sample", "outputDir": "bin/sample.ps1",
        }))
    before = _snapshot(candidate)
    log = owner.materialize(candidate, canonical_root=source)
    assert len(log) == 1 and "competing ancestor ownership" in log[0], log
    assert before == _snapshot(candidate)


def test_partial_bundle_write_never_selects_an_incomplete_native_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, candidate, plugin = _candidate(tmp_path)
    assert len(owner.materialize(candidate, canonical_root=source)) == 6
    selected = (plugin / "support/index.json").read_bytes()
    old = _selection(plugin)
    native = source / "libs/process-telemetry/src/process_telemetry/native/command-errors.sh"
    native.write_bytes(native.read_bytes() + b"\n# synthetic next generation\n")
    original = importlib.util.module_from_spec

    def partial(spec: Any) -> Any:
        module = original(spec)
        if spec.name == "_promotion_payload_invocation":
            loader = spec.loader.exec_module

            def execute(target: Any) -> None:
                loader(target)
                write = target.write_generated

                def write_partial(path: Path, content: str | bytes) -> None:
                    write(path, content[:-1] if path.name == "command-errors.sh" else content)

                target.write_generated = write_partial

            monkeypatch.setattr(spec.loader, "exec_module", execute)
        return module

    with monkeypatch.context() as patch:
        patch.setattr(importlib.util, "module_from_spec", partial)
        log = owner.materialize(candidate, canonical_root=source)
    assert len(log) == 1 and "generated content is stale" in log[0], log
    assert (plugin / "support/index.json").read_bytes() == selected
    assert len(owner.materialize(candidate, canonical_root=source)) == 6
    assert _selection(plugin)["bundle"] != old["bundle"]
    assert (plugin / "support" / old["bundle"] / "command-errors.sh").is_file()
