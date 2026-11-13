from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from worktree_manager import runtime_source_repository as policy

UPSTREAM = "https://example.invalid/suite.git"


def declaration(root: Path, text: str) -> Path:
    path = root / ".copilot-extensions" / "config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("layout", ["unresolved", "missing", "no-pin"])
def test_unresolved_selection_reports_main_foundation_without_writes(tmp_path, layout):
    root = tmp_path / "adopter"
    if layout == "no-pin":
        path = declaration(root, "background_reconcile:\n  enabled: true\n")
        before = path.read_bytes()
    value = policy.read(None if layout == "unresolved" else root, default_repository=UPSTREAM)
    assert value["repository"] == UPSTREAM
    assert value["reference"] == "main"
    assert value["fallback_reason"] in {"repository-unavailable", "selection-absent"}
    assert value["requested_reference"] is None
    if layout == "no-pin":
        assert path.read_bytes() == before
    else:
        assert not root.exists()


@pytest.mark.parametrize("reference", ["a" * 40, "main", "refs/heads/release", "v1.2.3", "main~2"])
def test_addressed_repository_keeps_requested_source_not_cwd(tmp_path, monkeypatch, reference):
    root, elsewhere = tmp_path / "adopter", tmp_path / "elsewhere"
    path = declaration(root, f'current_version: "{reference}"\nrepository: "{UPSTREAM}"\n')
    declaration(elsewhere, 'current_version: "b' + "b" * 39 + '"\n')
    monkeypatch.chdir(elsewhere)
    before = path.read_bytes()
    value = policy.read(root, default_repository="https://example.invalid/default.git")
    assert value["repository"] == UPSTREAM
    assert value["reference"] == value["requested_reference"] == reference
    assert value["repository_root"] == str(root)
    assert value["declaration"] == str(path)
    assert value["fallback_reason"] is None
    assert path.read_bytes() == before


def test_repository_override_alone_is_an_explicit_main_source(tmp_path):
    declaration(tmp_path, f'repository: "{UPSTREAM}"\n')
    value = policy.read(tmp_path, default_repository="https://example.invalid/default.git")
    assert value["repository"] == UPSTREAM and value["reference"] == "main"
    assert value["fallback_reason"] is None


@pytest.mark.parametrize("text", [
    "current_version: main\ncurrent_version: other\n",
    "current_version: [main]\n",
    "current_version: true\n",
    "current_version: ''\n",
    "repository: 42\n",
    "current_version: [\n",
    "- current_version: main\n",
    "last_known_good: not-a-list\ncurrent_version: main\n",
])
def test_invalid_declaration_is_not_main_fallback(tmp_path, text):
    path = declaration(tmp_path, text)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        policy.read(tmp_path, default_repository=UPSTREAM)
    assert path.read_bytes() == before


def test_reader_does_not_execute_adopter_code_or_change_qualified_history(tmp_path):
    history = ["a" * 40, "refs/tags/previous"]
    path = declaration(
        tmp_path, "current_version: main\nlast_known_good: " + json.dumps(history) + "\n",
    )
    (tmp_path / "setup.py").write_text("raise AssertionError('adopter code executed')", encoding="utf-8")
    before = path.read_bytes()
    assert policy.read(tmp_path, default_repository=UPSTREAM)["reference"] == "main"
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["oversized", "invalid-encoding", "directory"])
def test_unreadable_input_never_becomes_an_absent_pin(tmp_path, kind):
    path = declaration(tmp_path, "current_version: main\n")
    if kind == "oversized":
        path.write_bytes(b"#" * 65537)
    elif kind == "invalid-encoding":
        path.write_bytes(b"\xff")
    else:
        path.unlink()
        path.mkdir()
    with pytest.raises(ValueError):
        policy.read(tmp_path, default_repository=UPSTREAM)


def test_missing_cold_parser_is_not_an_absent_selection(tmp_path):
    declaration(tmp_path, "current_version: main\n")
    script = """
import runpy, sys
from pathlib import Path
read = runpy.run_path(sys.argv[1])["read"]
try:
    read(Path(sys.argv[2]), default_repository=sys.argv[3])
except ValueError as error:
    assert "parser is unavailable" in str(error), error
else:
    raise AssertionError("missing parser was mistaken for absent policy")
"""
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B", "-c",
         script, policy.__file__, str(tmp_path), UPSTREAM],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_replaced_configuration_is_not_read_as_the_addressed_declaration(tmp_path, monkeypatch):
    root = tmp_path / "adopter"
    path = declaration(root, "current_version: main\n")
    other = tmp_path / "other.yaml"
    other.write_text("current_version: other\n", encoding="utf-8")
    original = Path.open

    def swapped(candidate, *args, **kwargs):
        return original(other if candidate == path else candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", swapped)
    with pytest.raises(ValueError, match="changed during observation"):
        policy.read(root, default_repository=UPSTREAM)


def test_unreadable_repository_is_not_an_unavailable_repository(tmp_path, monkeypatch):
    root = tmp_path / "adopter"
    root.mkdir()
    original = Path.stat

    def denied(candidate, *args, **kwargs):
        if candidate == root:
            raise PermissionError("repository unavailable to this reader")
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(PermissionError):
        policy.read(root, default_repository=UPSTREAM)


@pytest.mark.parametrize("repository", [
    r"\\server\share\suite", "//server/share/suite", r"/\server\share\suite",
    r"\\?\UNC\server\share\suite", "file://server/share/suite",
    "file:////server/share/suite", "file:///C:/suite",
])
def test_repository_declaration_cannot_select_network_filesystem_sources(tmp_path, repository):
    declaration(tmp_path, "repository: " + json.dumps(repository) + "\n")
    with pytest.raises(ValueError, match="network filesystem|file:"):
        policy.read(tmp_path, default_repository=UPSTREAM)


@pytest.mark.parametrize("text", ["null\n", "~\n", "!!null ''\n", "---\n"])
def test_explicit_null_documents_are_not_absent_policy(tmp_path, text):
    declaration(tmp_path, text)
    with pytest.raises(ValueError, match="mapping"):
        policy.read(tmp_path, default_repository=UPSTREAM)


def test_yaml_merge_keys_refuse_before_alias_expansion(tmp_path):
    declaration(
        tmp_path, "base: &base {key: value}\n"
        "merged: {<<: *base}\ncurrent_version: main\n",
    )
    with pytest.raises(ValueError, match="merge"):
        policy.read(tmp_path, default_repository=UPSTREAM)


@pytest.mark.parametrize("text", ["", "# no selected source\n"])
def test_no_yaml_document_still_reports_absent_selection(tmp_path, text):
    declaration(tmp_path, text)
    assert policy.read(tmp_path, default_repository=UPSTREAM)["fallback_reason"] == "selection-absent"


@pytest.mark.parametrize("linked_part", ["ancestor", "directory", "file", "source"])
def test_linked_components_refuse_before_any_target_metadata_read(tmp_path, monkeypatch, linked_part):
    root = tmp_path / "adopter"
    source = tmp_path / "linked" / "suite"
    path = declaration(root, "current_version: main\nrepository: " + json.dumps(str(source)) + "\n")
    link = {
        "ancestor": tmp_path, "directory": path.parent, "file": path, "source": source.parent,
    }[linked_part]
    original = Path.stat

    def guarded(candidate, *, follow_symlinks=True):
        if candidate == link:
            if follow_symlinks:
                pytest.fail("linked target metadata was followed")
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        if candidate.is_relative_to(link):
            pytest.fail("linked ancestor was traversed before refusal")
        return original(candidate, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", guarded)
    with pytest.raises(ValueError, match="linked"):
        policy.read(root, default_repository=UPSTREAM)
