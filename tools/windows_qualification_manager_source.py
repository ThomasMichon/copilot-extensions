"""Protected adapter for the existing disposable two-application suite owner."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from windows_qualification_contract import SOURCE_PACKET, selection

TEXT_SUFFIXES = {".json", ".jsonl", ".xml", ".log"}


def validate_evidence(value: dict[str, Any], selected: dict[str, Any]) -> None:
    """Require the actual runner, frozen inputs and settled observer result."""
    selection(selected)
    if (
        selected.get("name") != SOURCE_PACKET
        or value.get("main_sha") != selected["main_sha"]
        or value.get("dev_sha") != selected["dev_sha"]
        or value.get("stages") != dict.fromkeys(("prepare", "exercise", "assertions", "settle"), 0)
        or value.get("errors") != []
        or value.get("observer") != {
            "schema": "worktree-manager.native-process-trace.v1",
            "stop_observed": True, "scan_errors": 0, "truncated": False, "window_armed": True,
        }
    ):
        raise ValueError("source application evidence is incomplete, foreign or unsettled")


def source_command(root: Path, workspace: Path, selected: dict[str, Any]) -> list[str]:
    selection(selected)
    if selected.get("name") != SOURCE_PACKET:
        raise ValueError("source suite requires its named packet")
    return [
        sys.executable, str(root / "tools/run-manager-suite-acceptance.py"),
        "--root", str(workspace), "--mode", "source",
        "--main-commit", selected["main_sha"], "--dev-commit", selected["dev_sha"],
    ]


def export_evidence(workspace: Path, output: Path) -> None:
    """Copy only bounded named diagnostics, never the private installation."""
    selected = [
        workspace / "source-runner.json", workspace / "source-process-trace.json",
        *(workspace / "source" / name for name in (
            "setup.json", "result.json", "preparation-error.json", "settlement.json",
            "commands.jsonl", "task-inventory.json", "driver-diagnostics.json",
        )),
    ]
    for name in ("source/driver-logs", "source/resident-logs", "source/frontdoor-traces", "junit"):
        directory = workspace / name
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError("source evidence directory is redirected")
        if directory.is_dir():
            selected.extend(directory.rglob("*"))
    total = 0
    for path in selected:
        if path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve()):
            raise ValueError("source evidence is redirected")
        if not path.exists() or path.is_dir():
            continue
        size = path.stat().st_size
        total += size
        if path.suffix not in TEXT_SUFFIXES or size > 16 * 1024 * 1024 or total > 64 * 1024 * 1024:
            raise ValueError("source evidence exceeds its text or size bound")
        relative = path.relative_to(workspace)
        target = output / ("junit" if relative.parts[0] == "junit" else "packet")
        target = target / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def require_hosted_owner() -> None:
    if not (
        os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("GITHUB_RUN_ID") and os.environ.get("RUNNER_TEMP")
    ):
        raise ValueError("source suite requires its disposable hosted Windows owner")


def qualify(
    root: Path, output: Path, selected: dict[str, Any], *,
    run: Callable[..., Any], redact: Callable[[str], str],
) -> dict[str, Any]:
    """Keep the original suite in its native VM owner with a finite outer wait."""
    require_hosted_owner()
    workspace = root / ".winqual-manager-source"
    if workspace.exists() or workspace.is_symlink():
        raise ValueError("source suite requires a fresh runner allocation")
    try:
        try:
            completed = run(
                source_command(root, workspace, selected), cwd=root, timeout=3500,
                max_stdout_bytes=8 * 1024 * 1024, max_stderr_bytes=2 * 1024 * 1024,
            )
        except subprocess.TimeoutExpired as error:
            for label, content in (("stdout", error.output), ("stderr", error.stderr)):
                text = content.decode("utf-8", "replace") if isinstance(content, bytes) else content or ""
                (output / f"phase-manager-source.{label}.log").write_text(redact(text), encoding="utf-8")
            raise
        for label in ("stdout", "stderr"):
            text = redact(getattr(completed, label))
            (output / f"phase-manager-source.{label}.log").write_text(text, encoding="utf-8")
        if completed.returncode:
            raise ValueError(f"source suite failed ({completed.returncode})")
    finally:
        if workspace.is_dir() and not workspace.is_symlink():
            export_evidence(workspace, output)
    report = json.loads((workspace / "source-runner.json").read_text("utf-8"))
    setup = json.loads((workspace / "source/setup.json").read_text("utf-8"))
    trace = json.loads((workspace / "source-process-trace.json").read_text("utf-8"))
    evidence = {
        "runner_sha": setup["runner_source"],
        "main_sha": setup["frozen"]["main"]["commit"],
        "dev_sha": setup["frozen"]["dev"]["commit"],
        "stages": report["stages"], "errors": report["errors"],
        "observer": {key: trace[key] for key in (
            "schema", "stop_observed", "scan_errors", "truncated", "window_armed",
        )},
    }
    validate_evidence(evidence, selected)
    return evidence
