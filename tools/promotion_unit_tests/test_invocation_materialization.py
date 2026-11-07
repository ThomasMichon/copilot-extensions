"""Actor-free controls for the pinned Invocation candidate-copy owner."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import shutil

import pytest

import materialize_payload_invocation as owner

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


@pytest.mark.parametrize(("relative", "expected"), [
    ("tools/materialize_payload_invocation.py", {"libs/payload-invocation/generate.py"}),
    ("libs/payload-invocation/generate.py", {"libs/payload-invocation/runtime_admission.py"}),
], ids=["candidate-producer", "canonical-packager"])
def test_pinned_invocation_loader_closure_is_statically_admitted(relative, expected):
    from release_helper_loaders import dynamic_loads

    assert dynamic_loads(ast.parse((ROOT / relative).read_bytes()), relative) == expected
