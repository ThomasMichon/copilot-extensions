"""Native PR impact checks with an advancing base, outside the pure unit packet."""

import os
import subprocess

import pytest

from promotion_unit_contract import ci_requires_code_checks, ci_requires_source_ast_guards, ci_requires_units


@pytest.fixture
def divergent_repository(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
               GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid")

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], env=env, stderr=subprocess.PIPE,
            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    git("init", "-q", "-b", "main")
    source = tmp_path / "source.py"
    source.write_text("value = 1\n")
    git("add", ".")
    git("-c", "commit.gpgsign=false", "commit", "-qm", "initial")
    git("checkout", "-qb", "proposal")
    (tmp_path / "README.md").write_text("# Documentation\n")
    git("add", ".")
    git("-c", "commit.gpgsign=false", "commit", "-qm", "documentation")
    git("checkout", "-q", "main")
    source.write_text("value = 2\n")
    git("add", ".")
    git("-c", "commit.gpgsign=false", "commit", "-qm", "upstream code")
    base = git("rev-parse", "HEAD").decode().strip()
    git("checkout", "-q", "proposal")
    return tmp_path, git, base


@pytest.mark.parametrize("change,selected", [("markdown", False), ("mixed", True), ("rename", True)])
def test_pr_impact_excludes_unrelated_upstream_code_but_keeps_actual_source_changes(
    divergent_repository, change, selected,
):
    root, git, base = divergent_repository
    if change == "mixed":
        (root / "source.py").write_text("value = 3\n")
    elif change == "rename":
        git("mv", "source.py", "source.md")
    if change != "markdown":
        git("add", ".")
        git("-c", "commit.gpgsign=false", "commit", "-qm", change)
    head = git("rev-parse", "HEAD").decode().strip()

    def read(argv):
        assert argv[0] == "git"
        return git(*argv[1:])

    for selector in (ci_requires_units, ci_requires_code_checks, ci_requires_source_ast_guards):
        assert selector("pull_request", base, head, read) is selected
    assert ci_requires_code_checks("push", base, head, read)
