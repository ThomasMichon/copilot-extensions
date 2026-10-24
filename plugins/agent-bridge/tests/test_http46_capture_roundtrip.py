"""Opt-in actual capture qualification; never run writers in the working checkout."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from collections.abc import Callable

import pytest
import agent_bridge_contract_git as evidence
import agent_bridge_contract_registry as codec
from agent_procutil.owned_process import run_caller_owned

ROOT = Path(__file__).resolve().parents[3]
MARKER = ".http46-capture-qualification.json"
pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
]


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=evidence.clean_git_environment(),
        timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    ).stdout.strip()


def checkpoint(root: Path, message: str) -> str:
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Contract qualification",
        "-c",
        "user.email=qualification@example.invalid",
        "commit",
        "-qm",
        message + "\n\nCo-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
    )
    return git(root, "rev-parse", "HEAD")


def files(root: Path) -> dict[str, bytes]:
    contract = root / "plugins/agent-bridge/contract"
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in contract.rglob("*")
        if path.is_file()
    }


def capture(root: Path, python: Path, source: str, *, write: bool) -> None:
    result = run_caller_owned(
        [
            str(python),
            str(root / "plugins/agent-bridge/contract/capture_http46.py"),
            "--source-commit",
            source,
            *(["--write"] if write else []),
        ],
        timeout=180,
        max_stdout_bytes=4 * 1024 * 1024,
        max_stderr_bytes=1024 * 1024,
    )
    output = result.stdout or ""
    assert result.returncode == 0, output + (result.stderr or "")
    receipts = re.findall(r"HTTP46_REPLAY_BOUND (\{[^\n]+\})", output)
    assert len(receipts) == 1, output
    receipt = json.loads(receipts[0])
    assert receipt["protocol"] == 46 and receipt["checked_modules"] > 0
    assert receipt["exitstatus"] == 0
    assert re.search(r"\b[1-9][0-9]* passed\b", output), output
    assert not re.search(r"\b[1-9][0-9]* (?:skipped|failed|error)", output), output
    assert "HTTP-46 evidence:" in output


@pytest.mark.timeout(420)
def test_http46_capture_actual_inline_and_chunked_roundtrip(
    tmp_path: Path,
    record_property: Callable[[str, object], None],
) -> None:
    marker_path = ROOT / MARKER
    if not marker_path.is_file():
        pytest.skip("use qualify_http46_capture.py with an actual retained HTTP-46 source")
    assert sys.platform == "linux"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    root = tmp_path / "capture"
    shutil.copytree(
        ROOT,
        root,
        ignore=shutil.ignore_patterns(".git", ".test-venvs", "__pycache__", "*.pyc"),
    )
    git(root, "init", "-q", "-b", "qualification")
    source = checkpoint(root, "Own a disposable historical capture fixture")
    for relative, sha in marker["runtime_sha256"].items():
        assert evidence.sha256_bytes((root / relative).read_bytes()) == sha
    # The nested replay owns a distinct environment/lease, but uses this same
    # retained source cohort. Rebind its editable paths, never production state.
    venv_relative = Path(".test-venvs") / sys.platform / "agent-bridge"
    shutil.copytree(ROOT / venv_relative, root / venv_relative, symlinks=True)
    for path in (root / venv_relative).rglob("*.pth"):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(str(ROOT), str(root)), encoding="utf-8")
    for path in (root / venv_relative).rglob("__editable__*.py"):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(str(ROOT), str(root)), encoding="utf-8")
    # Run the capture with the copied interpreter so nested editable imports
    # resolve to the copied cohort, not the outer runner's leased environment.
    inner_python = root / venv_relative / "bin/python"
    before = codec.load(root)
    assert before.index["schema_version"] == 1
    histories = {
        row["id"]: copy.deepcopy(row["provenance"]) for row in before.materialized["contracts"]
    }
    http = next(
        row for row in before.materialized["contracts"] if row["id"] == "agent-bridge.http-wire"
    )
    old_fixtures = {
        row["path"]: (root / row["path"]).read_bytes()
        for contract in before.materialized["contracts"]
        for row in contract["fixtures"]
        if contract["id"] != http["id"] or row["generation"] < 46
    }
    floors = {
        key: value
        for key, value in http["capability_versions"].items()
        if key != "startup_progress"
    }
    receipts = []
    for representation in ("inline", "chunked"):
        if representation == "chunked":
            snapshot = codec.load(root)
            original_limit = codec.MAX_ARTIFACT_BYTES
            try:
                codec.MAX_ARTIFACT_BYTES = len(codec.encode(snapshot.materialized)) - 1
                planned = codec.plan(root, snapshot, snapshot.materialized)
            finally:
                codec.MAX_ARTIFACT_BYTES = original_limit
            for relative, content in planned.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            assert codec.load(root).materialized == snapshot.materialized
            source = checkpoint(root, "Retain exact history in immutable provenance chunks")
        snapshot = codec.load(root)
        assert snapshot.index["schema_version"] == (1 if representation == "inline" else 2)
        retained = {key: value for key, value in snapshot.inputs.items() if key != codec.REGISTRY}
        assert representation == "inline" or retained
        capture(root, inner_python, source, write=True)
        written = files(root)
        capture(root, inner_python, source, write=False)
        assert files(root) == written
        after = codec.load(root)
        for row in after.materialized["contracts"]:
            assert row["provenance"][: len(histories[row["id"]])] == histories[row["id"]]
        for relative, content in {**old_fixtures, **retained}.items():
            assert (root / relative).read_bytes() == content
        for old_contract, current_contract in zip(
            snapshot.materialized["contracts"],
            after.materialized["contracts"],
            strict=True,
        ):
            if old_contract["id"] != http["id"]:
                assert current_contract == old_contract
        current_chunks = {
            row["id"]: row.get("provenance_chunks", []) for row in after.index["contracts"]
        }
        for old_contract in snapshot.index["contracts"]:
            chunks = old_contract.get("provenance_chunks", [])
            assert current_chunks[old_contract["id"]][: len(chunks)] == chunks
        current_http = next(
            row for row in after.materialized["contracts"] if row["id"] == http["id"]
        )
        assert floors == {
            key: value
            for key, value in current_http["capability_versions"].items()
            if key != "startup_progress"
        }
        assert all(len(raw) <= (1 << 20) for raw in after.inputs.values())
        assert any(row["commit"] == source for row in current_http["provenance"])
        receipts.append(
            {
                "representation": representation,
                "write": True,
                "no_write": True,
                "source": source,
                "retained_chunks": len(retained),
            }
        )
    record_property(
        "http46_capture_qualified",
        {
            "historical_source": marker["historical_source"],
            "cases": receipts,
            "composed_fixture_not_original_capture": True,
        },
    )
