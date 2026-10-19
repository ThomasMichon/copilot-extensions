"""Preserved-control preflight stays pinned, isolated and explicit about scope."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

import rehearse_promotion_control as control


@pytest.mark.parametrize("files", [
    [], "../outside", [None], ["../../outside.yml"], ["/tmp/outside.yml"],
    [".github/workflows/../outside.yml"],
    [".github/workflows/ci.yml", ".github/workflows/CI.yml"],
    [".github/workflows/nested/ci.yml"], [".github/workflows/ci.yml\n"],
])
def test_invalid_preservation_paths_refuse(files):
    with pytest.raises(ValueError):
        control.control_paths(json.dumps({"files": files}))


@pytest.mark.parametrize("name", ["../test_escape.py", "/tmp/test_escape.py", "tools/test_a.py::test_x", "-q"])
def test_invalid_test_selectors_refuse(name):
    with pytest.raises(ValueError):
        control.selected_tests("", [name])


def test_default_selection_is_complete_and_deduplicated():
    assert control.selected_tests(
        "python -m pytest tools/test_b.py tools/test_a.py tools/test_b.py", None,
    ) == ["tools/test_a.py", "tools/test_b.py"]
    with pytest.raises(ValueError):
        control.selected_tests("no declared tests", None)


def test_inherited_git_routing_cannot_redirect_fixture(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/unrelated")
    monkeypatch.setenv("GIT_WORK_TREE", "/unrelated")
    env = control.fixture_environment()
    assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / ".github/workflows").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / ".github/workflows/ci.yml").write_text(
        "# MAIN_NATIVE\njobs: {}\n# tools/test_probe.py\n\n\n", encoding="utf-8",
    )
    (root / ".github/workflows/validate-and-promote.yml").write_text(
        "jobs:\n  qualify-candidate:\n    steps: []\n", encoding="utf-8",
    )
    (root / control.MANIFEST).write_text(json.dumps({"files": [
        ".github/workflows/ci.yml", ".github/workflows/validate-and-promote.yml",
    ]}), encoding="utf-8")
    (root / control.STATE).write_text(
        json.dumps({"last_promotion": {"dev_head": "f" * 40}, "paused": True}),
        encoding="utf-8",
    )
    (root / "tools/test_probe.py").write_text(
        """import json
import subprocess
from pathlib import Path

def test_pinned_control_and_source_are_distinct():
    root = Path(__file__).resolve().parents[1]
    ci = (root / ".github/workflows/ci.yml").read_bytes()
    assert b"MAIN_NATIVE" in ci
    assert ci.endswith(b"\\n\\n\\n")
    state = json.loads((root / ".github/release-pipeline-state.json").read_text())
    source = state["last_promotion"]["dev_head"]
    recipe = subprocess.check_output(
        ["git", "show", source + ":.github/workflows/ci.yml"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert b"DEV_RECIPE" in recipe
    assert state["paused"] is True
""", encoding="utf-8",
    )
    (root / "tools/test_source_only.py").write_text(
        "def test_source_only_contract(): assert True\n", encoding="utf-8",
    )
    control.git(root, "init", "--quiet", "-b", "source")
    control.git(root, "add", ".")
    control.git(
        root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "--quiet", "-m", "control fixture",
    )
    control.git(root, "update-ref", "refs/remotes/origin/main", control.revision(root, "HEAD"))
    (root / ".github/workflows/ci.yml").write_text(
        "# DEV_RECIPE\n# tools/test_source_only.py\n", encoding="utf-8",
    )
    control.commit_fixture(root, "source fixture")
    return root


def test_real_rehearsal_preserves_bytes_lineage_source_and_cleanup(source, tmp_path, capsys):
    before = control.revision(source, "HEAD")
    main = control.revision(source, "origin/main")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert control.rehearse(source, "origin/main", scratch=scratch) == 0
    assert control.revision(source, "HEAD") == before
    assert control.revision(source, "origin/main") == main
    assert not control.git(source, "status", "--porcelain")
    assert not list(scratch.iterdir())
    record = json.loads(capsys.readouterr().out.splitlines()[0])
    assert record["evidence"].endswith("not release qualification")
    assert record["tests"] == ["tools/test_probe.py", "tools/test_source_only.py"]


def test_proposed_control_gets_a_distinct_fixture_commit(source, tmp_path, capsys):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    for name in ("ci.yml", "validate-and-promote.yml"):
        raw = control.blob(source, control.revision(source, "origin/main"), f".github/workflows/{name}")
        (overlay / name).write_bytes(raw + b"# prepared control\n\n\n")
    original = control.revision(source, "origin/main")
    assert control.rehearse(source, "origin/main", overlay=overlay) == 0
    record = json.loads(capsys.readouterr().out.splitlines()[0])
    assert record["control"] != original
    assert control.revision(source, "origin/main") == original


def test_dirty_source_and_missing_control_ref_refuse_before_execution(source):
    (source / "tools/test_probe.py").write_text("raise AssertionError('not a committed test')\n")
    with pytest.raises(ValueError, match="commit the source"):
        control.rehearse(source, "origin/main")
    with pytest.raises(subprocess.CalledProcessError):
        control.revision(source, "refs/heads/missing")
