"""Completed source work renews quiet windows without flooding native receivers."""

from collections.abc import Callable
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import versioned_runtime_source as source


def test_source_cli_bounds_burst_output_and_reports_later_completed_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    now = [0.0]
    monkeypatch.setattr(source.time, "monotonic", lambda: now[0])

    def read(
        _payload: Path, _version: str, *, plugin: str | None,
        on_progress: Callable[[], None],
    ) -> None:
        for observed in (0.0, 0.75, 1.5):
            now[0] = observed
            for _ in range(1000):
                on_progress()

    monkeypatch.setattr(source, "read_source_identity", read)
    monkeypatch.delenv(source.SOURCE_PIN_ENV, raising=False)
    assert source.main(["--payload", str(tmp_path), "--package-version", "1.2.3"]) == 0
    result = capsys.readouterr()
    assert result.out == "1.2.3\n"
    assert result.err.splitlines() == ["source-runtime: completed source work"] * 3
