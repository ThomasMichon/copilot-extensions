"""Runtime-only intent survives the existing native execution-driver boundary."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import importlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from update_driver import core

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
pytestmark = pytest.mark.guard


@pytest.fixture
def driver(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "_runtime_only_driver", SCRIPTS / "installer_driver.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _arguments(tmp_path: Path, **values: object) -> SimpleNamespace:
    return SimpleNamespace(**{
        "plugin": "agent-bridge", "action": "update", "options": ["NoService"],
        "payload": SCRIPTS.parent, "root": tmp_path / "runtime",
        "shell": tmp_path / "pwsh.exe", "wheel_profile": tmp_path / "profile",
        "wheel_directory": tmp_path / "wheels", "wheel_python": Path(sys.executable),
        "wheel_uv": tmp_path / "uv.exe", **values,
    })


@pytest.mark.parametrize(
    "values",
    [
        {"action": "provision"}, {"action": "uninstall"},
        {"wheel_profile": None}, {"wheel_directory": None},
        {"wheel_python": None}, {"wheel_uv": None},
        {"options": ["NoService", "RequireGracefulCutover"]},
        {"options": ["NoService", "NonInteractive"]},
        {"options": ["NoService", "Purge"]},
        {"options": ["NoService", "DryRun"]},
        {"options": ["NoService", "Repair"], "install_source": Path("source")},
    ],
)
def test_runtime_only_is_admitted_only_for_complete_closed_wheel_construction(
    driver: ModuleType, tmp_path: Path, values: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="NoService requires"):
        driver.native_arguments(_arguments(tmp_path, **values))


def test_runtime_only_reaches_journal_and_participant_unchanged(
    driver: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = importlib.import_module("install_source")

    @contextmanager
    def inputs(*_arguments: object) -> Iterator[object]:
        yield object()

    monkeypatch.setattr(source, "wheel_inputs", inputs)
    monkeypatch.setattr(source, "wheel_selection", lambda *_args: {"selected": True})
    monkeypatch.setattr(driver, "source_digest", lambda _payload: "a" * 64)
    request = Mock(return_value=SimpleNamespace(id="request"))
    owner = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=request, drain=Mock(),
        wait=Mock(return_value=SimpleNamespace(state="completed", exit_code=0)),
    )
    args = _arguments(tmp_path, options=["NoService", "ZeroDowntime"])
    assert driver.foreground(args, owner) == 0
    spec = request.call_args.args[0]
    runner = request.call_args.kwargs["runner"]
    assert "-NoService" in spec.argv
    assert runner.argv[runner.argv.index("--option") + 1] == "NoService"
    assert dict(spec.context)[driver.WHEEL_SELECTION_ENV] == '{"selected":true}'
    assert runner.argv[runner.argv.index("--wheel-profile") + 1] == str(args.wheel_profile)
