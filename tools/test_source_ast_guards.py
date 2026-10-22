"""Native subprocess contracts for the fixed full-source guard runner."""

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

SPEC = importlib.util.spec_from_file_location(
    "source_ast_guards",
    Path(__file__).with_name("run-source-ast-guards.py"),
)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)

GUARDS = (
    "check-no-sibling-tracking-writes.py",
    "check-headless-launch.py",
    "check-asyncio-timeout-handlers.py",
)


@pytest.mark.parametrize("status", [0, 7, -9])
def test_exact_scanner_commands_budget_and_output_are_preserved(monkeypatch, status):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, status, "stdout\nstderr\n")

    monkeypatch.setattr(RUNNER.subprocess, "run", run)
    assert RUNNER.GUARDS == GUARDS
    for guard in GUARDS:
        assert RUNNER._run_guard(guard) == (status, "stdout\nstderr\n")
    assert [argv for argv, _ in calls] == [
        [sys.executable, str(RUNNER.ROOT / "tools" / guard)] for guard in GUARDS
    ]
    for _, options in calls:
        assert options == {
            "cwd": RUNNER.ROOT,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "timeout": 300,
            "check": False,
            **(
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if sys.platform == "win32"
                else {}
            ),
        }


@pytest.mark.parametrize(
    "output", [b"partial diagnostic\n", "partial diagnostic\n", None]
)
def test_timeout_preserves_partial_diagnostics_and_fails(monkeypatch, output):
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 300, output=output)

    monkeypatch.setattr(RUNNER.subprocess, "run", run)
    status, text = RUNNER._run_guard(GUARDS[0])
    assert status == 124
    assert "300-second deadline" in text
    assert ("partial diagnostic" in text) == (output is not None)


def test_missing_scanner_interpreter_fails_explicitly(monkeypatch):
    def run(argv, **kwargs):
        raise FileNotFoundError("fixture interpreter missing")

    monkeypatch.setattr(RUNNER.subprocess, "run", run)
    status, text = RUNNER._run_guard(GUARDS[0])
    assert status == 125
    assert "Could not launch guard" in text
    assert "fixture interpreter missing" in text


def test_real_timeout_reaps_the_child_and_preserves_output(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / GUARDS[0]).write_text(
        "import sys, time\n"
        "print('partial stdout', flush=True)\n"
        "print('partial stderr', file=sys.stderr, flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    real_run = subprocess.run
    real_popen = subprocess.Popen
    children = []

    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        return process

    def run(argv, **kwargs):
        assert kwargs["timeout"] == 300
        return real_run(argv, **{**kwargs, "timeout": 1})

    monkeypatch.setattr(RUNNER, "ROOT", tmp_path)
    monkeypatch.setattr(RUNNER.subprocess, "Popen", popen)
    monkeypatch.setattr(RUNNER.subprocess, "run", run)
    status, text = RUNNER._run_guard(GUARDS[0])
    assert status == 124
    assert "partial stdout" in text and "partial stderr" in text
    assert "300-second deadline" in text
    assert len(children) == 1 and children[0].poll() is not None


@pytest.mark.parametrize("failed_index", [None, 0, 1, 2])
def test_real_processes_overlap_join_and_keep_all_diagnostics(
    tmp_path,
    monkeypatch,
    capsys,
    failed_index,
):
    tools = tmp_path / "tools"
    tools.mkdir()
    markers = [str(tmp_path / f"started-{index}") for index in range(3)]
    finished = [tmp_path / f"finished-{index}" for index in range(3)]
    for index, guard in enumerate(GUARDS):
        script = (
            "import sys, time\n"
            "from pathlib import Path\n"
            f"markers = {markers!r}\n"
            f"Path(markers[{index}]).write_text('started')\n"
            "deadline = time.monotonic() + 5\n"
            "while not all(Path(marker).is_file() for marker in markers):\n"
            "    if time.monotonic() >= deadline:\n"
            "        raise SystemExit('scanners did not overlap')\n"
            "    time.sleep(0.01)\n"
            f"print('stdout-{index}', flush=True)\n"
            f"print('stderr-{index}', file=sys.stderr, flush=True)\n"
            f"Path({str(finished[index])!r}).write_text('finished')\n"
            f"raise SystemExit({23 if failed_index == index else 0})\n"
        )
        (tools / guard).write_text(script, encoding="utf-8")
    monkeypatch.setattr(RUNNER, "ROOT", tmp_path)
    assert RUNNER.main() == int(failed_index is not None)
    output = capsys.readouterr().out
    assert all(marker.read_text() == "finished" for marker in finished)
    assert [line for line in output.splitlines() if line.startswith("::group::")] == [
        f"::group::{guard}" for guard in GUARDS
    ]
    for index in range(3):
        assert f"stdout-{index}" in output and f"stderr-{index}" in output
    assert output.count("::error::") == int(failed_index is not None)
    if failed_index is not None:
        assert f"{GUARDS[failed_index]} exited with status 23" in output
