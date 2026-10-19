"""Publication checks inspect the actual pushed tree and published tip."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = "private-fixture-token"


def native_bash() -> str:
    git = shutil.which("git")
    if os.name == "nt" and git:
        candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Native Bash is unavailable")
    return bash


class Repository:
    def __init__(self, root: Path, *, unrelated_dev: bool):
        self.root = root
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update({
            "HOME": str(root / "home"), "USERPROFILE": str(root / "home"),
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "COPILOT_EXTENSIONS_FORBIDDEN_IDS": PRIVATE,
            "COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI": "",
            "COPILOT_EXTENSIONS_DISABLE_LIVE_SWEEP": "1",
            "PATH": str(Path(sys.executable).parent) + os.pathsep + self.env.get("PATH", ""),
        })
        self.env.pop("BASH_ENV", None)
        self.env.pop("COPILOT_EXTENSIONS_GUARD_BASE", None)
        (root / "tools" / "hooks").mkdir(parents=True)
        for name in ("check-no-internal-identifiers.py", "check-large-files.py", "hooks/pre-push"):
            shutil.copyfile(ROOT / "tools" / name, root / "tools" / name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (root / "legacy.txt").write_text(PRIVATE, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "published baseline")
        self.base = self.git("rev-parse", "HEAD")
        empty = self.git("mktree", data=b"")
        unrelated = self.git("commit-tree", empty, "-m", "unrelated dev")
        self.git("update-ref", "refs/remotes/origin/main", self.base)
        self.git("update-ref", "refs/remotes/origin/dev", unrelated if unrelated_dev else self.base)

    def git(self, *arguments: str, data: bytes | None = None) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), *arguments], input=data, env=self.env,
            capture_output=True, check=True, timeout=20,
        )
        return result.stdout.decode("utf-8").strip()

    def file_commit(self, name: str, content: str) -> str:
        (self.root / name).write_text(content, encoding="utf-8")
        self.git("add", "--", name)
        self.git("commit", "-qm", "incoming change")
        return self.git("rev-parse", "HEAD")

    def tree_commit(self, name: str, content: str) -> str:
        blob = self.git("hash-object", "-w", "--stdin", data=content.encode("utf-8"))
        tree = self.git("mktree", "-z", data=f"100644 blob {blob}\t{name}\0".encode("utf-8"))
        return self.git("commit-tree", tree, "-p", self.base, "-m", "unselected incoming tree")

    def hook(self, *updates: tuple[str, str]):
        records = "".join(
            f"refs/heads/local-{index} {head} refs/heads/remote-{index} {old}\n"
            for index, (head, old) in enumerate(updates)
        )
        result = subprocess.run(
            [native_bash(), str(self.root / "tools" / "hooks" / "pre-push")],
            cwd=self.root, env=self.env, input=records.encode("utf-8"), capture_output=True,
            timeout=20,
        )
        return subprocess.CompletedProcess(
            result.args, result.returncode, result.stdout.decode("utf-8", "replace"),
            result.stderr.decode("utf-8", "replace"),
        )


def test_existing_main_ref_does_not_rescan_unrelated_legacy_tree(tmp_path):
    repo = Repository(tmp_path, unrelated_dev=True)
    head = repo.file_commit("change.txt", "public change")
    result = repo.hook((head, repo.base))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "within cap" in result.stdout
    assert "skipping" not in result.stdout.lower()


@pytest.mark.parametrize("name", ["private.txt", "unicode-\u00e9.txt", "tab\tfile", "line\nfile"])
def test_identifier_check_reads_unselected_pushed_tree_losslessly(tmp_path, name):
    repo = Repository(tmp_path, unrelated_dev=False)
    pushed = repo.tree_commit(name, PRIVATE)
    result = repo.hook((pushed, repo.base))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "forbidden identifier" in result.stdout


def test_every_ref_is_checked(tmp_path):
    repo = Repository(tmp_path, unrelated_dev=False)
    safe = repo.tree_commit("public.txt", "public")
    unsafe = repo.tree_commit("private.txt", PRIVATE)
    result = repo.hook((safe, repo.base), (unsafe, repo.base))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "forbidden identifier" in result.stdout


@pytest.mark.parametrize("size,accepted", [(1024 * 1024, True), (1024 * 1024 + 1, False)])
def test_actual_main_range_enforces_exact_blob_cap(tmp_path, size, accepted):
    repo = Repository(tmp_path, unrelated_dev=True)
    head = repo.file_commit("sized.txt", "x" * size)
    result = repo.hook((head, repo.base))
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr
    expected = "within cap" if accepted else "sized.txt"
    assert expected in result.stdout + result.stderr


@pytest.mark.parametrize("unrelated", [False, True])
def test_first_publication_uses_a_related_protected_base(tmp_path, unrelated):
    repo = Repository(tmp_path, unrelated_dev=unrelated)
    head = repo.file_commit("public.txt", "public")
    result = repo.hook((head, "0" * len(head)))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "within cap" in result.stdout


def test_deletion_does_not_rescan_the_current_checkout(tmp_path):
    repo = Repository(tmp_path, unrelated_dev=True)
    result = repo.hook(("0" * len(repo.base), repo.base))
    assert result.returncode == 0, result.stdout + result.stderr


def test_rewritten_main_ref_uses_release_base_instead_of_unrelated_old_tip(tmp_path):
    repo = Repository(tmp_path, unrelated_dev=True)
    head = repo.file_commit("public.txt", "public")
    result = repo.hook((head, repo.git("rev-parse", "origin/dev")))
    assert result.returncode == 0, result.stdout + result.stderr


def test_rebased_dev_ref_does_not_rescan_newly_published_default_changes(tmp_path):
    repo = Repository(tmp_path, unrelated_dev=False)
    old = repo.tree_commit("old-public.txt", "public")
    published = repo.file_commit("protected-private.txt", PRIVATE)
    repo.git("update-ref", "refs/remotes/origin/dev", published)
    head = repo.file_commit("new-public.txt", "public")
    result = repo.hook((head, old))
    assert result.returncode == 0, result.stdout + result.stderr


def test_unavailable_pushed_commit_fails_closed(tmp_path):
    repo = Repository(tmp_path, unrelated_dev=False)
    result = repo.hook(("f" * len(repo.base), repo.base))
    assert result.returncode != 0


def test_fallback_base_is_pinned_when_another_worktree_fetches(tmp_path):
    repo = Repository(tmp_path, unrelated_dev=True)
    head = repo.file_commit("sized.txt", "x" * (1024 * 1024 + 1))
    repo.env["COPILOT_EXTENSIONS_FORBIDDEN_IDS"] = "absent-fixture-token"
    guard = tmp_path / "tools" / "check-no-internal-identifiers.py"
    source = guard.read_text(encoding="utf-8")
    source = source.replace(
        "REPO = Path(__file__).resolve().parent.parent",
        "REPO = Path(__file__).resolve().parent.parent\n"
        "subprocess.run(['git', '-C', str(REPO), 'update-ref', "
        "'refs/remotes/origin/main', 'refs/remotes/origin/dev'], check=True)",
        1,
    )
    guard.write_text(source, encoding="utf-8")
    result = repo.hook((head, "0" * len(head)))
    assert result.returncode != 0, result.stdout + result.stderr
    assert "sized.txt" in result.stdout + result.stderr
