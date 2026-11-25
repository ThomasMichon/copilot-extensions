"""Exercise the trusted inline verification of a generated cleanup PR's paths."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from _promotion_workflow_test_support import source_workflow

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "owner/project"
HEAD = "a" * 40


def run_script() -> str:
    steps = source_workflow(ROOT, "purge-consumed-changefiles.yml")["jobs"]["purge"]["steps"]
    return next(
        str(step["run"]) for step in steps if "cleanup_branch=" in step.get("run", "")
    )


def verifier() -> str:
    bodies = re.findall(r"<<'VERIFY'\s*\n(.*?)^VERIFY\s*$", run_script(), re.MULTILINE | re.DOTALL)
    assert len(bodies) == 1
    return str(bodies[0])


def removed(count: int, start: int = 0) -> list[dict[str, Any]]:
    return [
        {"filename": f".changefiles/{index:05d}.json", "status": "removed"}
        for index in range(start, start + count)
    ]


def paged(files: list[dict[str, Any]], size: int = 100) -> list[list[dict[str, Any]]]:
    return [files[index:index + size] for index in range(0, len(files), size)]


def pull(changed: object, head: str = HEAD, **change: object) -> dict[str, object]:
    pr: dict[str, object] = {
        "state": "open", "changed_files": changed, "head": {"sha": head},
        "base": {"ref": "dev", "repo": {"full_name": REPOSITORY}},
    }
    pr.update(change)
    return pr


def execute(
    monkeypatch: pytest.MonkeyPatch,
    pulls: list[dict[str, object]],
    pages: object,
    number: str = "7",
    error: BaseException | None = None,
) -> list[list[str]]:
    # Resolve the workflow before mocking: generated Main checkouts read it through Git.
    program = compile(verifier(), "<trusted-cleanup-verifier>", "exec")
    calls: list[list[str]] = []
    pulls = list(pulls)

    def run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(argv)
        if error:
            raise error
        path = argv[-1]
        if path == f"repos/{REPOSITORY}/pulls/{number}":
            return SimpleNamespace(stdout=json.dumps(pulls.pop(0)))
        assert argv[:4] == ["gh", "api", "--paginate", "--slurp"]
        assert path == f"repos/{REPOSITORY}/pulls/{number}/files?per_page=100"
        return SimpleNamespace(stdout=json.dumps(pages))

    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["-", number])
    exec(program, {"__name__": "__main__"})
    return calls


def test_more_than_300_removed_changefiles_verify_across_all_pages(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    files = removed(843)
    calls = execute(monkeypatch, [pull(843), pull(843)], paged(files))
    assert capsys.readouterr().out.strip() == HEAD
    assert len(calls) == 3


@pytest.mark.parametrize("files", [
    removed(842),
    removed(842) + removed(1),
    removed(843) + removed(1, 900),
])
def test_incomplete_or_duplicated_listing_fails_closed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    files: list[dict[str, Any]],
) -> None:
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [pull(843), pull(843)], paged(files))
    output = capsys.readouterr()
    assert "Cannot verify the changed paths" in output.err
    assert HEAD not in output.out


@pytest.mark.parametrize("after", [pull(843, "b" * 40), pull(844)])
def test_head_or_count_moving_during_listing_fails_closed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    after: dict[str, object],
) -> None:
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [pull(843), after], paged(removed(843)))
    assert "ValueError" in capsys.readouterr().err


@pytest.mark.parametrize("count", [0, 3000, 3500])
def test_counts_outside_the_listable_range_fail_before_listing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], count: int,
) -> None:
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [pull(count)], [])
    assert "Cannot verify the changed paths" in capsys.readouterr().err


@pytest.mark.parametrize("entry", [
    {"filename": "tools/promote_release.py", "status": "removed"},
    {"filename": ".changefiles/new.json", "status": "added"},
    {"filename": ".changefiles/edit.json", "status": "modified"},
    {"filename": ".changefiles/moved.json", "status": "removed",
     "previous_filename": ".github/workflows/ci.yml"},
])
def test_any_change_other_than_a_changefile_removal_refuses_merge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    entry: dict[str, Any],
) -> None:
    files = removed(400) + [entry]
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [pull(401), pull(401)], paged(files))
    output = capsys.readouterr()
    assert "refusing to admin-merge" in output.err
    assert entry["filename"] in output.err
    assert HEAD not in output.out


@pytest.mark.parametrize("change", [
    {"state": "closed"},
    {"base": {"ref": "main", "repo": {"full_name": REPOSITORY}}},
    {"base": {"ref": "dev", "repo": {"full_name": "fork/project"}}},
    {"head": {"sha": "short"}},
    {"changed_files": "843"},
])
def test_unexpected_pull_request_metadata_fails_closed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    change: dict[str, Any],
) -> None:
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [pull(843, **change), pull(843)], paged(removed(843)))
    assert "Cannot verify the changed paths" in capsys.readouterr().err


@pytest.mark.parametrize("pages", [{}, [None], [[None]], [[{"filename": 1, "status": "removed"}]]])
def test_malformed_listing_fails_closed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], pages: object,
) -> None:
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [pull(1), pull(1)], pages)
    assert "Cannot verify the changed paths" in capsys.readouterr().err


@pytest.mark.parametrize("error", [
    subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 406 private provider details"),
    subprocess.TimeoutExpired(["gh"], 120),
    OSError("private local details"),
])
def test_provider_errors_fail_without_leaking_raw_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: BaseException,
) -> None:
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [], [], error=error)
    output = capsys.readouterr()
    assert "refusing to admin-merge" in output.err
    assert "private" not in output.err + output.out


def test_invalid_pull_request_number_is_refused_without_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(SystemExit, match="1"):
        assert execute(monkeypatch, [], [], number="7; rm") == []


def test_workflow_binds_both_checks_and_merge_to_the_verified_listing() -> None:
    run = run_script()
    commands = [line for line in run.splitlines() if not line.lstrip().startswith("#")]
    assert not any("gh pr diff" in line for line in commands)
    assert "max_removals=1000" in run
    assert '[ "$removed" -lt "$max_removals" ] || break' in run
    definition = run.index("verify_cleanup_paths() {")
    first = run.index('verify_cleanup_paths "$pr_number" >/dev/null')
    final = run.index('final_head=$(verify_cleanup_paths "$pr_number")')
    watch = run.index('gh pr checks "$pr_number" --watch --required')
    merge = run.index('--match-head-commit "$final_head"')
    assert definition < run.index("git push") < first < watch < final < merge


REQUIRED_CHECKS = ("workflow-lockdown-guard", "source-attribution", "PR gate (required check)")


def required_check_wait(deadline_seconds: int) -> str:
    run = run_script()
    start = run.index("deadline=$(( $(date +%s) + 1200 ))")
    end = run.index("\ndone\n", run.index('for required_check in "workflow-')) + 6
    return run[start:end].replace("+ 1200 ))", f"+ {deadline_seconds} ))")


def run_required_check_wait(tmp_path: Path, deadline_seconds: int, stages: list[str]) -> Any:
    bash = shutil.which("bash")
    if bash is None or (os.name == "nt" and "system32" in bash.lower()):
        pytest.skip("POSIX bash is required to execute the workflow wait loop")
    calls = tmp_path / "calls"
    script = (
        'pr_number=7\n'
        'gh() { n=$(cat "$CALLS" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$CALLS";\n'
        '  i=$((n > ${#STAGES[@]} ? ${#STAGES[@]} : n)); printf "%b" "${STAGES[$((i - 1))]}"; }\n'
        "sleep() { :; }\n"
        + required_check_wait(deadline_seconds)
        + "\necho waited\n"
    )
    stage_values = " ".join("'" + stage.replace("'", "'\\''") + "'" for stage in stages)
    program = f"STAGES=({stage_values})\n{script}"
    result = subprocess.run(
        [bash, "-c", program], env={**os.environ, "CALLS": calls.as_posix()},
        capture_output=True, text=True, timeout=60, check=False,
    )
    return result, int(calls.read_text().strip() or 0)


def test_merge_waits_until_every_required_check_appears(tmp_path: Path) -> None:
    stages = [
        "workflow-lockdown-guard\\n",
        "workflow-lockdown-guard\\nsource-attribution\\n",
        "workflow-lockdown-guard\\nsource-attribution\\nguards + lint\\n",
        "workflow-lockdown-guard\\nsource-attribution\\nPR gate (required check)\\n",
    ]
    result, calls = run_required_check_wait(tmp_path, 1200, stages)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "waited"
    assert calls >= len(stages)


def test_a_required_check_that_never_appears_refuses_merge(tmp_path: Path) -> None:
    result, _ = run_required_check_wait(
        tmp_path, 0, ["workflow-lockdown-guard\\nsource-attribution\\nguards + lint\\n"],
    )
    assert result.returncode == 1
    assert "PR gate (required check) never appeared" in result.stderr
    assert "waited" not in result.stdout


def test_waited_check_names_match_their_producing_workflows() -> None:
    run = run_script()
    loop = 'for required_check in ' + " ".join(f'"{name}"' for name in REQUIRED_CHECKS) + "; do"
    assert loop in run
    assert run.index(loop) < run.index('gh pr checks "$pr_number" --watch --required')
    pr_gate = source_workflow(ROOT, "ci.yml")["jobs"]["pr-gate"]["name"]
    assert "github.event_name == 'pull_request' && 'PR gate (required check)'" in pr_gate
    guard = source_workflow(ROOT, "workflow-lockdown-guard.yml")["jobs"]
    assert {"workflow-lockdown-guard", "source-attribution"} <= set(guard)
    assert source_workflow(ROOT, "purge-consumed-changefiles.yml")["jobs"]["purge"][
        "timeout-minutes"
    ] == 30