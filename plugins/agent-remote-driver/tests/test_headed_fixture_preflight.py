"""Source-only admission controls for the existing native headed consumer."""

from __future__ import annotations

from collections.abc import Mapping
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace
from typing import Protocol, cast

import pytest


class _CoordinatorFixture(Protocol):
    async def _native_coordinator_flow(
        self, tmp_path: Path, offline_cli: object, monkeypatch: pytest.MonkeyPatch,
        delivery: str,
    ) -> None: ...


@pytest.mark.skipif(os.name != "nt", reason="Existing Windows private mux composition")
@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["bridge-client", "startup-no-seed", "launch-error"])
async def test_native_bridge_coordinator_preflights_private_artifact_and_plain_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str,
) -> None:
    delivery = "bridge-client" if selection == "launch-error" else selection
    directory = Path(__file__).parent
    monkeypatch.syspath_prepend(str(directory))
    monkeypatch.syspath_prepend(str(directory / "native"))
    fixture = cast(_CoordinatorFixture, importlib.import_module("test_headed_extension"))
    mux_module = importlib.import_module("headed_mux_fixture")
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    environment = {"COPILOT_EXTENSIONS_TEST_CONTAINED": "1"}
    offline = SimpleNamespace(
        executable=sys.executable, home=home, workspace=workspace, env=environment,
        logs=tmp_path / "logs", requests=[], release_response=threading.Event(),
    )
    monkeypatch.setenv("COPILOT_DRIVER_HEADED_MUX_E2E", "1")
    monkeypatch.setenv("PSMUX_TEST_BIN", sys.executable)
    if not shutil.which("node"):
        pytest.skip("Node must already be present for the selected Bridge consumer")
    captured: list[tuple[str, ...]] = []

    class BeforeNativeLaunch(Exception):
        pass

    class PrivateMux:
        def __init__(self, binary: str, root: Path, env: Mapping[str, str]) -> None:
            assert binary == sys.executable
            assert root == home
            assert env["HOME"] == env["USERPROFILE"] == str(home)
            assert env["PSMUX_DATA_DIR"] == str(home / ".psmux")
            assert env["PSMUX_CONFIG_FILE"] == os.devnull
            assert env["PSMUX_NO_WARM"] == "1"
            assert env["PSMUX_SERVER_DEBUG"] == "1"
            self.target = "source-preflight-only"

        def query(self, action: str, *arguments: str) -> subprocess.CompletedProcess[str]:
            if action == "has-session":
                return subprocess.CompletedProcess([], 1, "", "")
            assert action == "new-session"
            assert (tmp_path / "bridge-consumer" / "remote-driver.mjs").is_file()
            command = arguments[arguments.index("--") + 1:]
            if delivery == "bridge-client":
                assert command[1:3] == ("-I", "-S")
                assert Path(command[3]).suffix == ".pyz" and Path(command[3]).is_file()
            else:
                from agent_remote_driver.headed import launcher as headed
                from agent_remote_driver.session_host import launcher

                assert command[1] == "-c"
                monkeypatch.setattr(launcher, "main", lambda: 0)
                monkeypatch.setattr(headed, "stage_extension", getattr(headed, "stage_extension"))
                with pytest.raises(SystemExit) as stopped:
                    exec(compile(command[2], "<startup-fixture>", "exec"), {})
                assert stopped.value.code == 0
            native = command[command.index("--") + 1:]
            assert native[0] == sys.executable
            assert "--screen-reader" not in native
            assert "--no-auto-update" in native and "--disable-builtin-mcps" in native
            assert "--session-id" in native
            assert ("-i" in native) == (delivery == "bridge-client")
            captured.append(command)
            if selection == "launch-error":
                private_logs = home / ".psmux"
                private_logs.mkdir()
                (private_logs / "startup.log").write_text(
                    "fixture coordinator startup failure", encoding="utf-8",
                )
                return subprocess.CompletedProcess([], 1, "", "private fixture pane exited")
            raise BeforeNativeLaunch

    monkeypatch.setattr(mux_module, "FixtureMux", PrivateMux)
    expected_error = AssertionError if selection == "launch-error" else BeforeNativeLaunch
    expected_message = "fixture coordinator startup failure" if selection == "launch-error" else None
    with pytest.raises(expected_error, match=expected_message):
        await fixture._native_coordinator_flow(tmp_path, offline, monkeypatch, delivery)
    assert len(captured) == 1
    assert offline.release_response.is_set()
