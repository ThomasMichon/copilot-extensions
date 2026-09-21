"""Regression tests for the module-size guard (hard cap + shrink-only baseline).

Drives the real ``tools/check-module-size.py`` as a subprocess inside a
throwaway git repo, the same pattern ``test_check_version_bump.py`` and
``test_check_no_internal_identifiers.py`` already use.

Run:  python -m pytest tools/test_check_module_size.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-module-size.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write_lines(repo: Path, rel: str, line_count: int) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "\n".join(f"x{i} = {i}" for i in range(line_count)) + "\n", encoding="utf-8"
    )


def _write_baseline(repo: Path, data: dict[str, int]) -> None:
    p = repo / "tools" / "module-size-baseline.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / SCRIPT.name), *extra],
        cwd=repo,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / SCRIPT.name).write_bytes(SCRIPT.read_bytes())
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    return tmp_path


def _commit_all(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "snapshot")


def test_a_small_file_passes(repo: Path):
    _write_lines(repo, "src/small.py", 50)
    _commit_all(repo)

    result = _run(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[OK]" in result.stdout


def test_an_uncapped_new_file_over_the_cap_fails(repo: Path):
    _write_lines(repo, "src/huge.py", 1001)
    _commit_all(repo)

    result = _run(repo)

    assert result.returncode == 1
    assert "src/huge.py" in result.stdout
    assert "1000-line cap" in result.stdout


def test_a_baselined_file_within_its_ceiling_passes(repo: Path):
    _write_lines(repo, "src/legacy.py", 5000)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    result = _run(repo)

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_baselined_file_that_grows_past_its_ceiling_fails(repo: Path):
    _write_lines(repo, "src/legacy.py", 5001)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    result = _run(repo)

    assert result.returncode == 1
    assert "src/legacy.py" in result.stdout
    assert "grandfathered ceiling" in result.stdout


def test_a_baselined_file_that_shrinks_still_passes(repo: Path):
    _write_lines(repo, "src/legacy.py", 4000)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    result = _run(repo)

    assert result.returncode == 0, result.stdout + result.stderr


def test_test_files_are_exempt_from_the_cap(repo: Path):
    _write_lines(repo, "tests/test_something.py", 5000)
    _write_lines(repo, "src/pkg/tests/test_other.py", 5000)
    _write_lines(repo, "src/pkg/conftest.py", 5000)
    _commit_all(repo)

    result = _run(repo)

    assert result.returncode == 0, result.stdout + result.stderr


def test_refresh_baseline_lowers_a_shrunk_entry(repo: Path):
    _write_lines(repo, "src/legacy.py", 4000)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    result = _run(repo, "--refresh-baseline")
    assert result.returncode == 0, result.stdout + result.stderr

    baseline = json.loads((repo / "tools" / "module-size-baseline.json").read_text())
    assert baseline == {"src/legacy.py": 4000}


def test_refresh_baseline_graduates_a_file_that_dropped_to_or_below_the_cap(
    repo: Path,
):
    _write_lines(repo, "src/legacy.py", 900)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    result = _run(repo, "--refresh-baseline")
    assert result.returncode == 0, result.stdout + result.stderr

    baseline = json.loads((repo / "tools" / "module-size-baseline.json").read_text())
    assert baseline == {}


def test_refresh_baseline_never_raises_a_ceiling_for_a_grown_file(repo: Path):
    _write_lines(repo, "src/legacy.py", 5001)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    result = _run(repo, "--refresh-baseline")
    assert result.returncode == 0, result.stdout + result.stderr

    baseline = json.loads((repo / "tools" / "module-size-baseline.json").read_text())
    # Unchanged: growth past an existing ceiling is a `check()` failure to fix,
    # never something --refresh-baseline silently absorbs.
    assert baseline == {"src/legacy.py": 5000}


def test_refresh_baseline_discovers_a_new_offender(repo: Path):
    _write_lines(repo, "src/new_huge.py", 1200)
    _write_baseline(repo, {})
    _commit_all(repo)

    result = _run(repo, "--refresh-baseline")
    assert result.returncode == 0, result.stdout + result.stderr

    baseline = json.loads((repo / "tools" / "module-size-baseline.json").read_text())
    assert baseline == {"src/new_huge.py": 1200}


def test_allow_widen_requires_refresh_baseline(repo: Path):
    _write_lines(repo, "src/small.py", 50)
    _commit_all(repo)

    result = _run(repo, "--allow-widen")

    assert result.returncode == 2
    assert "--allow-widen requires --refresh-baseline" in result.stderr


def test_refresh_baseline_allow_widen_raises_a_grown_ceiling(repo: Path):
    _write_lines(repo, "src/legacy.py", 5001)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    result = _run(repo, "--refresh-baseline", "--allow-widen")
    assert result.returncode == 0, result.stdout + result.stderr

    baseline = json.loads((repo / "tools" / "module-size-baseline.json").read_text())
    assert baseline == {"src/legacy.py": 5001}


def test_refresh_baseline_allow_widen_still_lowers_a_shrunk_entry(repo: Path):
    _write_lines(repo, "src/legacy.py", 4000)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    result = _run(repo, "--refresh-baseline", "--allow-widen")
    assert result.returncode == 0, result.stdout + result.stderr

    baseline = json.loads((repo / "tools" / "module-size-baseline.json").read_text())
    assert baseline == {"src/legacy.py": 4000}


def test_refresh_baseline_allow_widen_never_auto_baselines_a_new_offender(repo: Path):
    # A brand-new file crossing the hard cap is a genuinely new violation --
    # --allow-widen only ratchets EXISTING baseline entries. Silently
    # grandfathering a never-baselined file here would let ordinary growth
    # past the cap sneak in through the automation's own back door.
    _write_lines(repo, "src/new_huge.py", 1200)
    _write_baseline(repo, {})
    _commit_all(repo)

    result = _run(repo, "--refresh-baseline", "--allow-widen")
    assert result.returncode == 0, result.stdout + result.stderr

    baseline = json.loads((repo / "tools" / "module-size-baseline.json").read_text())
    assert baseline == {}

    # The un-widened file still fails the ordinary check.
    check_result = _run(repo)
    assert check_result.returncode == 1
    assert "src/new_huge.py" in check_result.stdout


def test_changed_since_exempts_a_file_the_pr_never_touched(repo: Path):
    # Baseline a file at its current (over-cap) size -- the common ancestor
    # both "main" and the PR branch below diverge from.
    _write_lines(repo, "src/shared.py", 5000)
    _write_baseline(repo, {"src/shared.py": 5000})
    _commit_all(repo)
    _git(repo, "branch", "-M", "main")

    # The PR branch: diverges here, adds its own file, never touches shared.py.
    _git(repo, "checkout", "-q", "-b", "pr-branch")
    _write_lines(repo, "src/mine.py", 10)
    _commit_all(repo)

    # Meanwhile, an unrelated already-merged PR grows shared.py past its
    # ceiling directly on "main" -- the PR branch above never sees this commit
    # until it rebases.
    _git(repo, "checkout", "-q", "main")
    _write_lines(repo, "src/shared.py", 5001)
    _commit_all(repo)

    # The PR rebases onto the now-grown "main" (the facility's normal
    # keep-up-to-date flow) -- its own working tree now DOES contain
    # shared.py's grown state, even though its own commits never touched it.
    _git(repo, "checkout", "-q", "pr-branch")
    _git(repo, "rebase", "main")

    # Enforce against the rebased PR HEAD, scoped since "main" -- the
    # triple-dot diff still resolves to files THIS PR's own commit(s) touch
    # (mine.py), never shared.py, regardless of what the working tree now
    # contains.
    result = _run(repo, "--changed-since", "main")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "src/shared.py" not in result.stdout


def test_changed_since_still_enforces_a_file_the_pr_itself_touches(repo: Path):
    _write_lines(repo, "src/mine.py", 999)
    _commit_all(repo)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    _write_lines(repo, "src/mine.py", 1001)  # this PR's own growth, over the cap
    _commit_all(repo)

    result = _run(repo, "--changed-since", base_sha)

    assert result.returncode == 1
    assert "src/mine.py" in result.stdout


def test_changed_since_is_incompatible_with_refresh_baseline(repo: Path):
    _write_lines(repo, "src/small.py", 50)
    _commit_all(repo)

    result = _run(repo, "--changed-since", "HEAD", "--refresh-baseline")

    assert result.returncode == 2
    assert "--changed-since is incompatible with --refresh-baseline" in result.stderr


def test_changed_since_still_checks_a_baseline_entry_this_diff_edits(
    repo: Path,
):
    # A baseline-only edit touches no *.py file at all, so a naive *.py-only
    # diff would scope enforcement to an empty set and silently pass a now
    # inconsistent (too-low) ceiling. Reproduce: lower an existing entry
    # below the file's actual (unchanged) size in a diff that touches only
    # the baseline JSON.
    _write_lines(repo, "src/legacy.py", 5000)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    _write_baseline(repo, {"src/legacy.py": 100})  # bogus/mistaken lowering
    _commit_all(repo)

    result = _run(repo, "--changed-since", base_sha)

    # Still checked -- the diff's own baseline edit is exactly knowable, so
    # it's added to the scope even though this diff's *.py file list is
    # empty. Not a fully unscoped sweep, though (see the next test).
    assert result.returncode == 1, result.stdout + result.stderr
    assert "src/legacy.py" in result.stdout
    assert "[INFO]" in result.stdout


def test_changed_since_with_baseline_touch_does_not_blame_unrelated_drift(
    repo: Path,
):
    """The exact PR #3205 CI failure this guards against: a componentization
    PR that legitimately lowers ITS OWN file's ceiling must not also be
    blamed for a completely unrelated, already-baselined file elsewhere that
    drifted past its own ceiling via some other, unrelated merged PR -- that
    is organic trunk drift for a scheduled sweep/decomposer to handle (Phase
    2), not something a fair-attribution PR-time gate should block on."""
    _write_lines(repo, "src/mine.py", 2000)
    _write_lines(repo, "src/unrelated.py", 3000)
    _write_baseline(repo, {"src/mine.py": 2000, "src/unrelated.py": 3000})
    _commit_all(repo)
    fork_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    default_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "switch", "-c", "pr-branch")

    # This PR's own branch shrinks + re-baselines its own file...
    _write_lines(repo, "src/mine.py", 1500)
    _write_baseline(repo, {"src/mine.py": 1500, "src/unrelated.py": 3000})
    _commit_all(repo)

    # ...meanwhile, back on "main", some unrelated, already-merged PR grows a
    # different file past its already-recorded ceiling (never touching the
    # baseline entry, exactly the buggy-but-already-landed trunk state Phase
    # 2's watchdog/decomposer -- not this PR-time gate -- is responsible for).
    _git(repo, "switch", default_branch)
    _write_lines(repo, "src/unrelated.py", 3001)
    _commit_all(repo)
    main_tip = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    # Rebase the PR branch onto the now-advanced "main" tip, exactly like a
    # real PR branch does before its CI re-runs.
    _git(repo, "switch", "pr-branch")
    _git(repo, "rebase", default_branch)

    result = _run(repo, "--changed-since", main_tip)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "src/unrelated.py" not in result.stdout
    assert "[INFO]" in result.stdout
    assert fork_sha  # sanity: fixture actually forked from a real commit


def test_changed_since_with_baseline_touch_still_catches_this_diffs_own_growth(
    repo: Path,
):
    """The baseline-touch scoping widening must not become a loophole: a
    file this diff's own *.py commits touch is still enforced even when the
    same diff also happens to edit the baseline for an unrelated entry."""
    _write_lines(repo, "src/mine.py", 999)
    _write_lines(repo, "src/other.py", 500)
    _write_baseline(repo, {"src/other.py": 500})
    _commit_all(repo)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    _write_lines(repo, "src/mine.py", 1001)  # this diff's own growth, over cap
    _write_baseline(repo, {"src/other.py": 400})  # unrelated baseline edit
    _commit_all(repo)

    result = _run(repo, "--changed-since", base_sha)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "src/mine.py" in result.stdout

