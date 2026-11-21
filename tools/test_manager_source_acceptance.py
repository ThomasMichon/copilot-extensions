"""The authentic-source selection retains the existing runner's lifetime bounds."""
from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
import runpy
import subprocess
import sys
from typing import Any

import pytest

RUNNER = Path(__file__).with_name("run-manager-suite-acceptance.py")


@pytest.mark.parametrize("outcome", ("settled", "unsettled", "copy-failure"))
def test_source_owner_copies_evidence_and_preserves_unsettled_private_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    runner = runpy.run_path(str(RUNNER))
    owner = runner["_qualify_source"]
    workspace = tmp_path / "ce-ms-control"
    workspace.mkdir()
    report_root = tmp_path / "reports"
    report_root.mkdir()

    def allocate(*, prefix: str, dir: Path) -> str:
        assert prefix == "ce-ms-" and dir == Path.home()
        return str(workspace)

    @contextmanager
    def observe(
        root: Path, mode: str, script: Callable[[Path, Path, Path, Path], str], *, home: Path,
    ) -> Iterator[None]:
        assert root == report_root and mode == "source"
        assert home == workspace / "source" / "h"
        yield

    def qualify(
        root: Path, mode: str, python: Path, *, main_commit: str, dev_commit: str, execution_root: Path,
    ) -> int:
        assert root == report_root and execution_root == workspace
        assert (main_commit, dev_commit) == ("a" * 40, "b" * 40)
        case = execution_root / "source"
        case.mkdir()
        (case / "setup.json").write_text('{"actual_source":"retained"}', encoding="utf-8")
        git = case / ".git" / "objects"
        git.mkdir(parents=True)
        readonly = git / "readonly-object"
        readonly.write_bytes(b"fixture Git object")
        os.chmod(readonly, stat.S_IREAD)
        (root / "source-runner.json").write_text(
            json.dumps({"stages": {"settle": int(outcome == "unsettled")}, "errors": []}), encoding="utf-8",
        )
        return int(outcome == "unsettled")

    monkeypatch.setattr(runner["tempfile"], "mkdtemp", allocate)
    monkeypatch.setitem(owner.__globals__, "_observe_processes", observe)
    monkeypatch.setitem(owner.__globals__, "qualify", qualify)
    if outcome == "copy-failure":
        def copy(*args: Any, **options: Any) -> None:
            raise OSError("controlled evidence copy failure")
        monkeypatch.setattr(runner["shutil"], "copy2", copy)
    with pytest.raises(OSError, match="controlled evidence") if outcome == "copy-failure" else nullcontext():
        code = owner(report_root, Path(sys.executable), "a" * 40, "b" * 40, lambda *args: "")
        assert code == int(outcome == "unsettled")
    report = json.loads((report_root / "source-runner.json").read_text(encoding="utf-8"))
    assert workspace.exists() is (outcome != "settled")
    if outcome == "settled":
        assert json.loads((report_root / "source" / "setup.json").read_text())["actual_source"] == "retained"
    else:
        assert report["retained_private_root"] == str(workspace)


def test_source_stages_use_the_private_execution_root_and_public_report_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = runpy.run_path(str(RUNNER))
    owner = runner["qualify"]
    workspace = tmp_path / "private"

    def run(argv: list[str], *, timeout: float) -> int:
        stage = next((name for name in ("prepare", "exercise", "settle") if "--" + name in argv), None)
        if stage:
            assert argv[argv.index("--" + stage) + 1] == str(workspace)
        else:
            assert runner["os"].environ["MANAGER_SUITE_ACCEPTANCE_DATA"] == str(workspace)
        return 0

    monkeypatch.setitem(owner.__globals__, "_run", run)
    assert owner(tmp_path, "source", Path(sys.executable), execution_root=workspace) == 0
    assert (tmp_path / "source-runner.json").is_file()
    assert not workspace.exists()


def test_fresh_interpreter_bootstraps_the_runner_without_installed_packages(tmp_path: Path) -> None:
    from agent_procutil import no_window_kwargs

    base = Path(getattr(sys, "_base_executable", sys.executable))
    result = subprocess.run(
        [base, "-E", "-S", str(RUNNER), "--help"], cwd=tmp_path,
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--main-commit" in result.stdout and "--dev-commit" in result.stdout


@pytest.mark.parametrize("failed", ("prepare", "exercise", "assertions", "settle", "exception", None))
def test_source_stages_keep_bounds_selection_and_unconditional_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str | None,
) -> None:
    runner = runpy.run_path(str(RUNNER))
    calls: list[str] = []

    def run(argv: list[str], *, timeout: float) -> int:
        stage = next((name for name in ("prepare", "exercise", "settle")
                      if "--" + name in argv), "assertions")
        calls.append(stage)
        assert timeout == {"prepare": 1800, "exercise": 900, "assertions": 300, "settle": 90}[stage]
        assert "test_manager_source_replacement.py" in " ".join(argv)
        assert ("--main-commit" in argv) is (stage == "prepare")
        assert ("--dev-commit" in argv) is (stage == "prepare")
        if stage == "prepare":
            assert argv[-4:] == ["--main-commit", "a" * 40, "--dev-commit", "b" * 40]
            if failed == "exception":
                raise subprocess.TimeoutExpired(argv, timeout)
        elif stage == "assertions":
            assert argv[argv.index("--junit-dir") + 1] == str(tmp_path / "junit")
            assert argv[-1] == "test_installed_manager_updates_frozen_source"
        return int(stage == failed)

    qualify = runner["qualify"]
    monkeypatch.setitem(qualify.__globals__, "_run", run)
    monkeypatch.setenv("MANAGER_SUITE_ACCEPTANCE_DATA", "previous-owner")
    assert qualify(
        tmp_path, "source", Path(sys.executable), main_commit="a" * 40, dev_commit="b" * 40,
    ) == int(failed is not None)
    expected: dict[str | None, list[str]] = {
        "prepare": ["prepare", "settle"], "exception": ["prepare", "settle"],
        "exercise": ["prepare", "exercise", "settle"],
    }
    assert calls == expected.get(failed, ["prepare", "exercise", "assertions", "settle"])
    assert runner["os"].environ["MANAGER_SUITE_ACCEPTANCE_DATA"] == "previous-owner"
    report = json.loads((tmp_path / "source-runner.json").read_text(encoding="utf-8"))
    assert report["stages"]["settle"] == int(failed == "settle")


@pytest.mark.parametrize("commits", ([], ["--main-commit", "a" * 40], ["--dev-commit", "b" * 40]))
def test_source_runner_requires_both_frozen_inputs_before_native_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, commits: list[str],
) -> None:
    runner: dict[str, Any] = runpy.run_path(str(RUNNER))
    root = tmp_path / "must-not-exist"
    monkeypatch.setattr(sys, "argv", [
        str(RUNNER), "--root", str(root), "--mode", "source", *commits,
    ])
    with pytest.raises(SystemExit) as result:
        runner["main"]()
    assert result.value.code == 2 and not root.exists()
