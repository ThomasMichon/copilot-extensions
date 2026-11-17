"""Associated declaration reads use the existing reader without acquisition."""
from __future__ import annotations

import json
from importlib.util import find_spec
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import cast

import pytest

from worktree_manager import repository_suite as association
from worktree_manager import repository_suite_declaration as declaration
from worktree_manager import source_config


def policy_file(repository: Path, text: str) -> Path:
    path = repository / ".copilot-extensions" / "config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


def policy(result: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], result["repository_policy"])


def test_unassociated_read_ignores_ambient_repository_and_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign = tmp_path / "foreign"
    policy_file(foreign, "current_version: invalid [\n")
    monkeypatch.chdir(foreign)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(foreign))
    selected = tmp_path / "selected"
    observed = policy(declaration.read_declaration(selected))
    assert observed["repository_root"] is None
    assert observed["reference"] == "main"
    assert observed["fallback_reason"] == "repository-unavailable"
    assert not selected.exists()


def test_addressed_association_reads_current_intent_and_preserves_every_file(tmp_path: Path) -> None:
    root = tmp_path / "manager"
    root.mkdir()
    config = root / source_config.CONFIG_NAME
    config.write_text(
        '[source]\nrepo = "https://self.example.invalid/suite.git"\nref = "canary"\n'
        '[runtime_source]\nchannel = "main"\nrepo = "https://other.example.invalid/suite.git"\n',
        encoding="utf-8",
    )
    repository = tmp_path / "adopter"
    path = policy_file(
        repository,
        "current_version: refs/tags/v1\nrepository: https://example.invalid/suite.git\n"
        "last_known_good: [refs/tags/v0]\nunrelated: retained\n",
    )
    association.associate(root, repository, apply=True)
    original_config, original_policy = config.read_bytes(), path.read_bytes()
    observed = policy(declaration.read_declaration(root))
    assert observed["repository_root"] == str(repository)
    assert observed["repository"] == "https://example.invalid/suite.git"
    assert observed["requested_reference"] == "refs/tags/v1"
    assert observed["reference"] == "refs/tags/v1"
    assert observed["fallback_reason"] is None
    assert config.read_bytes() == original_config
    assert path.read_bytes() == original_policy
    path.write_text("current_version: refs/tags/older\n", encoding="utf-8")
    assert policy(declaration.read_declaration(root))["reference"] == "refs/tags/older"
    assert config.read_bytes() == original_config


def test_unavailable_associated_repository_retains_identity_and_explicit_fallback(tmp_path: Path) -> None:
    repository = tmp_path / "adopter"
    repository.mkdir()
    root = tmp_path / "manager"
    association.associate(root, repository, apply=True)
    repository.rmdir()
    observed = policy(declaration.read_declaration(root))
    assert observed["repository_root"] == str(repository)
    assert observed["fallback_reason"] == "repository-unavailable"
    assert observed["requested_reference"] is None
    assert observed["reference"] == "main"
    assert association.read_association(root) == repository


@pytest.mark.parametrize("text", [
    "current_version: [unclosed\n",
    "current_version: first\ncurrent_version: second\n",
    "repository: file:///not-admitted\n",
    "last_known_good: not-a-list\n",
])
def test_invalid_declaration_is_not_an_absent_selection(tmp_path: Path, text: str) -> None:
    repository = tmp_path / "adopter"
    path = policy_file(repository, text)
    root = tmp_path / "manager"
    association.associate(root, repository, apply=True)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        declaration.read_declaration(root)
    assert path.read_bytes() == original
    assert association.read_association(root) == repository


def test_cold_module_negotiation_real_parser_and_missing_parser_refusal(tmp_path: Path) -> None:
    root = tmp_path / "manager"
    repository = tmp_path / "adopter"
    policy_file(repository, "current_version: refs/tags/v1\n")
    association.associate(root, repository, apply=True)
    package_root = tmp_path / "source"
    shutil.copytree(
        Path(declaration.__file__).resolve().parent, package_root / "worktree_manager",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    parser = find_spec("yaml")
    assert parser is not None and parser.origin is not None
    parser_root = Path(parser.origin).resolve().parent.parent
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    code = (
        "import runpy,sys; roots=sys.argv.pop(1).split(';');sys.path[:0]=roots;"
        "runpy.run_module('worktree_manager.repository_suite_declaration',run_name='__main__')"
    )

    def invoke(*arguments: str, include_parser: bool = False) -> subprocess.CompletedProcess[str]:
        roots = [str(package_root)] + ([str(parser_root)] if include_parser else [])
        return subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-X", "utf8", "-c", code,
             ";".join(roots), *arguments],
            cwd=foreign, capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )

    described = invoke("--describe")
    assert described.returncode == 0, described.stdout + described.stderr
    capability = json.loads(described.stdout)
    assert capability["schema"] == declaration.SCHEMA + ".capabilities"
    assert capability["association_versions"] == [1]
    assert capability["read_only"] is True
    assert capability["resolves_source"] is False
    unavailable = invoke("--root", str(root), "inspect")
    assert unavailable.returncode == 1, unavailable.stdout + unavailable.stderr
    assert "YAML parser is unavailable" in json.loads(unavailable.stdout)["error"]
    observed = invoke("--root", str(root), "inspect", include_parser=True)
    assert observed.returncode == 0, observed.stdout + observed.stderr
    assert policy(json.loads(observed.stdout))["reference"] == "refs/tags/v1"
    assert association.read_association(root) == repository


def test_credential_bearing_source_is_not_emitted_by_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    repository = tmp_path / "adopter"
    marker = "synthetic-password-marker"
    policy_file(repository, "repository: https://reader:" + marker + "@example.invalid/suite.git\n")
    root = tmp_path / "manager"
    association.associate(root, repository, apply=True)
    assert declaration.main(["--root", str(root), "inspect"]) == 1
    output = capsys.readouterr()
    assert marker not in output.out + output.err
    assert "credential-free remote Git source" in json.loads(output.out)["error"]
