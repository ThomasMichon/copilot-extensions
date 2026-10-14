"""Regression tests for the module-size guard (hard cap + shrink-only baseline).

Drives the real ``tools/check-module-size.py`` as a subprocess inside a
throwaway git repo, the same pattern ``test_check_version_bump.py`` and
``test_check_no_internal_identifiers.py`` already use.

Run:  python -m pytest tools/test_check_module_size.py
"""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-module-size.py"


def test_repository_probe_timeout_is_a_controlled_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--repository", str(tmp_path)])

    def hung_git(command, **kwargs):
        assert command == ["git", "rev-parse", "--show-toplevel"]
        assert kwargs["timeout"] == 15
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hung_git)
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert error.value.code == 1
    stderr = capsys.readouterr().err
    assert "[FAIL] module size: Git timed out after 15 seconds" in stderr
    assert "Traceback" not in stderr


def test_module_size_stays_in_ci_not_publication_hook():
    hook = (SCRIPT.parent / "hooks" / "pre-push").read_text(encoding="utf-8")
    calls = [
        line for line in hook.splitlines()
        if line.startswith('"${PY[@]}"') and "check-module-size.py" in line
    ]
    assert calls == []
    ci = (SCRIPT.parent.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "check-module-size.py --changed-since ${{ github.event.pull_request.base.sha }}" in ci


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


def test_trusted_tooling_can_refresh_a_poisoned_target_checkout(repo: Path):
    _write_lines(repo, "src/large.py", 1200)
    _write_baseline(repo, {"src/large.py": 1100})
    (repo / "tools" / SCRIPT.name).write_text(
        "raise RuntimeError('candidate tooling executed')\n", encoding="utf-8",
    )
    (repo / "json.py").write_text(
        "raise RuntimeError('candidate import executed')\n", encoding="utf-8",
    )
    _commit_all(repo)
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--repository", str(repo),
         "--refresh-baseline", "--widen-only"],
        cwd=repo, capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((repo / "tools/module-size-baseline.json").read_text()) == {
        "src/large.py": 1200,
    }


def test_trusted_target_refresh_still_rejects_new_offenders(repo: Path):
    _write_lines(repo, "src/new.py", 1200)
    _write_baseline(repo, {})
    _commit_all(repo)
    for extra in [
        ("--refresh-baseline", "--allow-widen"),
        ("--refresh-baseline", "--widen-only"),
        (),
    ]:
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--repository", str(repo), *extra],
            cwd=repo.parent, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == (0 if extra else 1), result.stdout + result.stderr
    assert json.loads((repo / "tools/module-size-baseline.json").read_text()) == {}


def test_widen_only_tolerates_a_stale_high_ceiling_after_dev_shrinks(repo: Path):
    _write_lines(repo, "src/legacy.py", 1200)
    _write_baseline(repo, {"src/legacy.py": 1100})
    _commit_all(repo)
    _git(repo, "branch", "dev")
    _git(repo, "checkout", "-qb", "automation/module-size-baseline-widen-dev")
    assert _run(repo, "--refresh-baseline", "--widen-only").returncode == 0
    _commit_all(repo)
    _git(repo, "checkout", "-q", "dev")
    _write_lines(repo, "src/legacy.py", 1100)
    _commit_all(repo)
    _git(repo, "merge", "-q", "--no-edit", "automation/module-size-baseline-widen-dev")
    baseline = repo / "tools/module-size-baseline.json"
    assert _run(repo).returncode == 0
    assert _run(repo, "--refresh-baseline", "--widen-only").returncode == 0
    assert json.loads(baseline.read_text()) == {"src/legacy.py": 1200}
    assert _run(repo, "--refresh-baseline", "--allow-widen").returncode == 0
    assert json.loads(baseline.read_text()) == {"src/legacy.py": 1100}


def test_widen_only_never_lowers_or_removes_existing_entries(repo: Path):
    for path, count in [
        ("src/grown.py", 1300), ("src/shrunk.py", 1100), ("src/graduated.py", 900),
    ]:
        _write_lines(repo, path, count)
    _write_baseline(repo, {
        "src/grown.py": 1200, "src/shrunk.py": 1200, "src/graduated.py": 1200,
    })
    _commit_all(repo)
    result = _run(repo, "--refresh-baseline", "--widen-only")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((repo / "tools/module-size-baseline.json").read_text()) == {
        "src/grown.py": 1300, "src/shrunk.py": 1200, "src/graduated.py": 1200,
    }


def test_widen_only_cannot_introduce_a_low_ceiling_after_dev_grows(repo: Path):
    _write_lines(repo, "src/legacy.py", 1100)
    _write_lines(repo, "src/other.py", 1300)
    _write_baseline(repo, {"src/legacy.py": 1500, "src/other.py": 1200})
    _commit_all(repo)
    _git(repo, "branch", "dev")
    _git(repo, "checkout", "-qb", "automation/module-size-baseline-widen-dev")
    assert _run(repo, "--refresh-baseline", "--widen-only").returncode == 0
    _commit_all(repo)
    _git(repo, "checkout", "-q", "dev")
    _write_lines(repo, "src/legacy.py", 1400)
    _commit_all(repo)
    _git(repo, "merge", "-q", "--no-edit", "automation/module-size-baseline-widen-dev")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((repo / "tools/module-size-baseline.json").read_text()) == {
        "src/legacy.py": 1500, "src/other.py": 1300,
    }


@pytest.mark.parametrize("args", [
    ("--widen-only",),
    ("--refresh-baseline", "--widen-only", "--allow-widen"),
])
def test_widen_only_requires_an_unambiguous_refresh_mode(repo: Path, args):
    result = _run(repo, *args)
    assert result.returncode == 2
    assert "--widen-only" in result.stderr


def test_nonfile_target_baseline_is_a_controlled_cli_failure(repo: Path):
    (repo / "tools/module-size-baseline.json").mkdir()
    _commit_all(repo)
    result = _run(repo, "--repository", str(repo))
    assert result.returncode == 2
    assert "baseline must be a regular path" in result.stderr
    assert "Traceback" not in result.stderr


def test_invalid_baseline_is_a_controlled_cli_failure(repo: Path):
    (repo / "tools/module-size-baseline.json").write_text("{invalid\n", encoding="utf-8")
    _commit_all(repo)
    result = _run(repo)
    assert result.returncode == 1
    assert "[FAIL] module size:" in result.stderr
    assert "Traceback" not in result.stderr


def test_missing_target_checkout_is_a_usage_error(repo: Path):
    result = _run(repo, "--repository", str(repo / "missing"))
    assert result.returncode == 2
    assert "--repository must name a checkout directory" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("inside_checkout", [False, True])
def test_target_must_be_a_git_checkout_root(repo: Path, inside_checkout):
    target = (repo if inside_checkout else repo.parent) / "not-a-checkout-root"
    target.mkdir()
    result = _run(repo, "--repository", str(target))
    assert result.returncode == 2
    assert "--repository must name a Git checkout root" in result.stderr
    assert "Traceback" not in result.stderr


def test_git_failure_is_a_controlled_cli_failure(repo: Path):
    _write_lines(repo, "src/small.py", 50)
    _commit_all(repo)
    result = _run(repo, "--changed-since", "missing-ref")
    assert result.returncode == 1
    assert "[FAIL] module size:" in result.stderr
    assert "missing-ref" in result.stderr
    assert "Traceback" not in result.stderr


def test_unicode_target_root_uses_git_utf8_output(repo: Path):
    _write_lines(repo, "src/small.py", 50)
    _commit_all(repo)
    target = repo.parent / "target-\u00e9"
    _git(repo, "clone", "-q", str(repo), str(target))
    driver = (
        "import locale,runpy,sys; locale.getencoding=lambda:'cp1252'; "
        "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-I", "-c", driver,
         str(SCRIPT), "--repository", str(target)],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
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


def test_changed_since_default_head_misattributes_base_drift_via_merge_ref(
    repo: Path,
):
    """Reproduces the real bug: a `pull_request` trigger with no pinned
    checkout `ref:` checks out the ephemeral `refs/pull/<n>/merge` commit
    (PR head merged into the base branch's CURRENT tip), not the PR's own
    head. Diffing that merge commit against an increasingly stale
    `base.sha` folds every commit the base branch gained since into what
    looks like the PR's own diff -- misattributing another, unrelated PR's
    growth to this one. `--head` must be pinned to the PR's real head sha
    (as CI now does) to avoid this; the default (bare `HEAD`) reproduces it.
    """
    _write_lines(repo, "src/shared.py", 5000)
    _write_baseline(repo, {"src/shared.py": 5000})
    _commit_all(repo)
    _git(repo, "branch", "-M", "dev")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    # The PR branch: diverges at base_sha, adds its own file, never touches
    # shared.py.
    _git(repo, "checkout", "-q", "-b", "pr-branch")
    _write_lines(repo, "src/mine.py", 10)
    _commit_all(repo)
    pr_head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    # Meanwhile, `dev` advances (an unrelated already-merged PR grows
    # shared.py past its ceiling) -- base_sha, captured at PR-open time, is
    # now stale relative to dev's current tip.
    _git(repo, "checkout", "-q", "dev")
    _write_lines(repo, "src/shared.py", 5001)
    _commit_all(repo)

    # actions/checkout with no ref: override produces exactly this: a merge
    # commit of the PR branch into dev's CURRENT tip, without ever touching
    # the PR branch itself.
    _git(repo, "checkout", "-q", "-b", "merge-ref", "dev")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge pr-branch into dev", "pr-branch")

    # Default --head ("HEAD" == the merge-ref commit) reproduces the bug:
    # dev's own unrelated growth of shared.py gets misattributed to this PR.
    buggy = _run(repo, "--changed-since", base_sha)
    assert buggy.returncode == 1, buggy.stdout + buggy.stderr
    assert "src/shared.py" in buggy.stdout

    # Pinning --head to the PR's real head sha (as CI now does) fixes it:
    # the diff is scoped to exactly this PR's own commits again.
    fixed = _run(repo, "--changed-since", base_sha, "--head", pr_head_sha)
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert "src/shared.py" not in fixed.stdout


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


def test_changed_since_still_checks_a_baseline_entry_this_diff_removes(
    repo: Path,
):
    # Symmetric with the lowering case above: REMOVING a baseline entry
    # entirely (not just editing its value) must also be re-checked, even
    # though the removed key no longer appears in the "after" baseline dict
    # at all -- _changed_baseline_keys must surface it via the "before-only"
    # side of the diff, not just the "after" side.
    _write_lines(repo, "src/legacy.py", 5000)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    _write_baseline(repo, {})  # bogus/mistaken removal; file is still 5000 lines
    _commit_all(repo)

    result = _run(repo, "--changed-since", base_sha)

    # A removed entry falls back to the hard 1,000-line cap (no baseline
    # entry == no grandfathering), so the still-5,000-line file must fail.
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
