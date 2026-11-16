"""Use the real candidate writer for static bootstrap declaration delivery."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

import materialize_payload_invocation as invocation
from test_materialize_main import _invocation_candidate


@pytest.mark.parametrize("stale", [False, True])
def test_bootstrap_declaration_materializes_without_changing_canonical_source(
    tmp_path: Path, stale: bool,
) -> None:
    canonical, candidate = _invocation_candidate(tmp_path)
    for root in (canonical, candidate):
        plugin = root / "plugins/agent-worktrees"
        manifest = plugin / "payload-invocation.json"
        manifest.write_text(json.dumps({
            **json.loads(manifest.read_text()), "bootstrapPython": "3.11.11",
        }))
        (plugin / "pyproject.toml").write_text(
            '[project]\nname="agent-worktrees"\nversion="1"\nrequires-python=">=3.11"\n',
        )
    declaration = candidate / "plugins/agent-worktrees/scripts/bootstrap-python-version.txt"
    if stale:
        declaration.write_text("obsolete derived copy\n")
    before = {p.relative_to(canonical): p.read_bytes() for p in canonical.rglob("*") if p.is_file()}
    log = invocation.materialize(candidate, canonical_root=canonical)
    assert log and not any(line.startswith("SKIP") for line in log), log
    assert declaration.read_bytes() == b"cpython@3.11.11\n"
    assert before == {p.relative_to(canonical): p.read_bytes() for p in canonical.rglob("*") if p.is_file()}


def test_withdrawn_bootstrap_request_refuses_candidate_before_writes(tmp_path: Path) -> None:
    canonical, candidate = _invocation_candidate(tmp_path)
    declaration = candidate / "plugins/agent-worktrees/scripts/bootstrap-python-version.txt"
    declaration.write_text("cpython@3.11.11\n")
    before = {p.relative_to(candidate): p.read_bytes() for p in candidate.rglob("*") if p.is_file()}
    log = invocation.materialize(candidate, canonical_root=canonical)
    assert len(log) == 1 and "SKIP" in log[0] and "obsolete bootstrap Python request" in log[0]
    assert before == {p.relative_to(candidate): p.read_bytes() for p in candidate.rglob("*") if p.is_file()}


def test_isolated_producer_and_materializer_need_only_stdlib(tmp_path: Path) -> None:
    canonical, candidate = _invocation_candidate(tmp_path)
    for root in (canonical, candidate):
        manifest = root / "plugins/agent-worktrees/payload-invocation.json"
        manifest.write_text(json.dumps({
            **json.loads(manifest.read_text()), "bootstrapPython": "3.11.11",
        }))
        (manifest.parent / "pyproject.toml").write_text(
            '[project]\nname="agent-worktrees"\nversion="1"\nrequires-python=">=3.12"\n',
        )
    before = {p.relative_to(canonical): p.read_bytes() for p in canonical.rglob("*") if p.is_file()}
    script = """
import importlib.util
import json
from pathlib import Path
import sys

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

repo, canonical, candidate = map(Path, sys.argv[1:])
assert sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
for name in ("packaging", "tomli"):
    assert importlib.util.find_spec(name) is None, name
generator = load("isolated_generator", repo / "libs/payload-invocation/generate.py")
manifest = candidate / "plugins/agent-worktrees/payload-invocation.json"
outputs = generator.expected_files(manifest, canonical_root=canonical)
assert outputs[manifest.parent / "scripts/bootstrap-python-version.txt"] == "cpython@3.11.11\\n"
materializer = load("isolated_materializer", repo / "tools/materialize_payload_invocation.py")
log = materializer.materialize(candidate, canonical_root=canonical)
assert log and not any(line.startswith("SKIP") for line in log), log
assert (manifest.parent / "scripts/bootstrap-python-version.txt").read_bytes() == b"cpython@3.11.11\\n"
print(json.dumps({"isolated": True, "materialized": len(log)}))
"""
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script,
         str(Path(__file__).resolve().parents[1]), str(canonical), str(candidate)],
        capture_output=True, text=True, timeout=60,
        creationflags=creation_flags,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["materialized"] > 0
    assert before == {p.relative_to(canonical): p.read_bytes() for p in canonical.rglob("*") if p.is_file()}
