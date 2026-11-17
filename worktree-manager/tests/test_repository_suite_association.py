"""Explicit association operations do not install or retarget another policy."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterator, cast

import pytest

from worktree_manager import repository_suite as suite
from worktree_manager import source_config
from worktree_manager.config_write_lock import config_write_lock


def directory(path: Path) -> Path:
    path.mkdir()
    return path


def table(repository: Path) -> str:
    return "[repository_suite]\nversion = 1\nrepository_root = " + json.dumps(str(repository)) + "\n"


def test_missing_inspection_and_proposal_have_no_configuration_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "selected"
    repository = directory(tmp_path / "adopter")
    unrelated = directory(tmp_path / "unrelated")
    (unrelated / "config.toml").write_text("invalid TOML [", encoding="utf-8")
    monkeypatch.chdir(unrelated)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "ambient"))
    assert suite.read_association(root) is None
    result = suite.associate(root, repository)
    assert result.before is None
    assert result.proposed == repository
    assert not result.applied
    assert not root.exists()
    assert not (tmp_path / "ambient").exists()


def test_apply_preserves_other_config_and_repeat_is_an_observation(tmp_path: Path) -> None:
    root = directory(tmp_path / "manager")
    repository = directory(tmp_path / "adopter")
    path = root / "config.toml"
    original = (
        '# Retain the selected sources.\n[source]\nrepo = "https://self.example.invalid/suite.git"\n'
        'ref = "canary"\n[runtime_source]\nchannel = "main"\n'
        'repo = "https://services.example.invalid/suite.git"\nref = ""\nallow_dev = false\n'
        '[view]\nlabel = """keep this\nand this"""\nvalue = nan\n'
    )
    path.write_text(original, encoding="utf-8")
    result = suite.associate(root, repository, apply=True)
    assert result.applied
    assert suite.read_association(root) == repository
    assert path.read_text(encoding="utf-8").startswith(original)
    before = path.read_bytes()
    identity = path.stat()
    repeat = suite.associate(root, repository, apply=True)
    assert not repeat.applied
    assert path.read_bytes() == before
    assert (path.stat().st_ino, path.stat().st_mtime_ns) == (identity.st_ino, identity.st_mtime_ns)


def test_replacement_and_clear_require_the_expected_root(tmp_path: Path) -> None:
    root = tmp_path / "manager"
    first = directory(tmp_path / "first")
    second = directory(tmp_path / "second")
    suite.associate(root, first, apply=True)
    path = source_config.config_path(root)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="expected root"):
        suite.associate(root, second, apply=True)
    assert path.read_bytes() == before
    proposal = suite.associate(root, second, expect_current_root=first)
    assert not proposal.applied
    assert suite.read_association(root) == first
    assert suite.associate(root, second, expect_current_root=first, apply=True).applied
    with pytest.raises(ValueError, match="expected root"):
        suite.clear(root, expect_current_root=first, apply=True)
    assert suite.read_association(root) == second
    second.rmdir()
    assert suite.read_association(root) == second
    assert not suite.associate(root, second, apply=True).applied
    assert suite.clear(root, expect_current_root=second, apply=True).applied
    assert suite.read_association(root) is None
    assert not suite.clear(root, expect_current_root=second, apply=True).applied


@pytest.mark.parametrize("content", [
    "repository_suite = false\n",
    "[repository_suite]\n",
    '[repository_suite]\nversion = true\nrepository_root = "relative"\n',
    '[repository_suite]\nversion = 2\nrepository_root = "relative"\n',
    '[repository_suite]\nversion = 1\nrepository_root = "relative"\n',
    '[repository_suite]\nversion = 1\nrepository_root = "relative"\nextra = true\n',
    'private-value = ["unclosed"\n',
    pytest.param(b"\xff", id="invalid-utf8"),
    "#" * 65537,
    pytest.param("nested = " + "[" * 2000 + "0" + "]" * 2000, id="nested-input-budget"),
])
def test_invalid_config_is_not_absence_and_is_never_overwritten(
    tmp_path: Path, content: str | bytes,
) -> None:
    root = directory(tmp_path / "manager")
    repository = directory(tmp_path / "adopter")
    path = root / "config.toml"
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        suite.read_association(root)
    with pytest.raises(ValueError):
        suite.associate(root, repository, apply=True)
    assert path.read_bytes() == before
    assert not (root / ".config.toml.lock").exists()


@pytest.mark.parametrize("apply", [1, "yes", None])
def test_apply_requires_an_actual_boolean(tmp_path: Path, apply: object) -> None:
    root = tmp_path / "manager"
    repository = directory(tmp_path / "adopter")
    with pytest.raises(ValueError, match="boolean"):
        suite.associate(root, repository, apply=cast(bool, apply))
    assert not root.exists()


@pytest.mark.parametrize("drift", ["association", "target"])
def test_inputs_are_revalidated_under_the_shared_writer_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    root = tmp_path / "manager"
    first = directory(tmp_path / "first")
    second = directory(tmp_path / "second")
    other = directory(tmp_path / "other")
    suite.associate(root, first, apply=True)
    lock = config_write_lock

    @contextmanager
    def intervening(path: Path) -> Iterator[None]:
        with lock(path):
            if drift == "association":
                path.write_text(table(other), encoding="utf-8")
            else:
                second.rmdir()
            yield

    monkeypatch.setattr(suite, "config_write_lock", intervening)
    with pytest.raises(ValueError):
        suite.associate(root, second, expect_current_root=first, apply=True)
    assert suite.read_association(root) == (other if drift == "association" else first)
    assert not list(root.glob(".repository-suite-*.toml"))


def test_failed_publication_preserves_the_original_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "manager"
    first = directory(tmp_path / "first")
    second = directory(tmp_path / "second")
    suite.associate(root, first, apply=True)
    path = source_config.config_path(root)
    before = path.read_bytes()

    def fail(source: object, target: object) -> None:
        raise OSError("fixture publication failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="fixture publication"):
        suite.associate(root, second, expect_current_root=first, apply=True)
    assert path.read_bytes() == before
    assert not list(root.glob(".repository-suite-*.toml"))


@pytest.mark.skipif(os.name != "nt", reason="native Windows path equivalence")
def test_native_equivalent_root_does_not_retarget(tmp_path: Path) -> None:
    root = tmp_path / "manager"
    repository = directory(tmp_path / "adopter")
    suite.associate(root, repository, apply=True)
    alias = Path("\\\\?\\" + str(repository))
    assert not suite.associate(root, alias, apply=True).applied
    assert not suite.associate(root, Path(str(repository).swapcase()), apply=True).applied


def test_real_cold_module_is_explicit_and_read_only_until_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "selected"
    repository = directory(tmp_path / "adopter")
    replacement = directory(tmp_path / "replacement")
    policy = directory(repository / ".copilot-extensions") / "config.yaml"
    policy.write_text("invalid YAML [", encoding="utf-8")
    unrelated = directory(tmp_path / "unrelated")
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "ambient"))
    package_root = Path(suite.__file__).resolve().parents[1]
    code = (
        "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
        "runpy.run_module('worktree_manager.repository_suite',run_name='__main__')"
    )

    def invoke(*arguments: str) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-X", "utf8", "-c", code,
             str(package_root), *arguments],
            cwd=unrelated, capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        return cast(dict[str, object], json.loads(result.stdout))

    assert invoke("--describe")["read_only_default"] is True
    assert invoke("--root", str(root), "inspect")["repository_root"] is None
    assert not root.exists()
    assert not invoke(
        "--root", str(root), "associate", "--repository-root", str(repository),
    )["changed"]
    assert not root.exists()
    assert invoke(
        "--root", str(root), "associate", "--repository-root", str(repository), "--apply",
    )["changed"] is True
    assert invoke("--root", str(root), "inspect")["repository_root"] == str(repository)
    assert invoke(
        "--root", str(root), "associate", "--repository-root", str(replacement),
        "--expect-current-root", str(repository), "--apply",
    )["changed"] is True
    assert invoke("--root", str(root), "inspect")["repository_root"] == str(replacement)
    assert invoke(
        "--root", str(root), "clear", "--expect-current-root", str(replacement), "--apply",
    )["changed"] is True
    assert policy.read_text(encoding="utf-8") == "invalid YAML ["
    assert not (tmp_path / "ambient").exists()
