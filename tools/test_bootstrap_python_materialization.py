"""Use the real candidate writer for static bootstrap declaration delivery."""
from __future__ import annotations

import json
from pathlib import Path

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
