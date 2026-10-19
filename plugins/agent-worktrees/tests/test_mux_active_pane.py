"""Native pane lookup and seeded handoff on a private tmux server."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_worktrees import launch_seed_exec, launch_seed_state, sessions, tracking

pytestmark = pytest.mark.guard


@pytest.fixture
def native_panes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    tmux = shutil.which("tmux")
    if os.name == "nt":
        pytest.skip("requires native POSIX tmux")
    if tmux is None:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail("native tmux pane guards require tmux in POSIX CI")
        pytest.skip("requires native POSIX tmux")

    monkeypatch.chdir(tmp_path)
    socket = "mux.sock"
    server = subprocess.Popen(
        [tmux, "-D", "-S", socket, "-f", os.devnull],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not Path(socket).exists():
            if server.poll() is not None:
                pytest.fail(f"tmux server exited: {server.communicate()[1]}")
            if time.monotonic() >= deadline:
                pytest.fail("private tmux socket did not become ready")
            time.sleep(0.01)

        monkeypatch.setenv("TMUX", f"{socket},{server.pid},0")
        monkeypatch.delenv("PSMUX_PANE", raising=False)
        panes = {}
        for name in ("wt-example", "custom-session"):
            result = subprocess.run(
                [tmux, "-S", socket, "new-session", "-d", "-P", "-F", "#{pane_id}",
                 "-s", name, "sleep 60"],
                capture_output=True, text=True, check=True, timeout=5,
            )
            panes[name] = result.stdout.strip()
            assert panes[name].startswith("%")
        yield panes
    finally:
        if server.poll() is None:
            subprocess.run(
                [tmux, "-S", socket, "kill-server"],
                capture_output=True, text=True, check=True, timeout=5,
            )
        server.wait(timeout=5)


def test_native_active_pane_lookups(native_panes: dict[str, str]) -> None:
    assert sessions.mux_active_pane("example", mux="tmux") == native_panes["wt-example"]
    assert sessions.mux_active_pane_named("custom-session", mux="tmux") == native_panes["custom-session"]
    assert sessions.mux_active_pane("absent", mux="tmux") is None
    assert sessions.mux_active_pane_named("absent", mux="tmux") is None


@pytest.mark.parametrize("kind", ["new", "resume"])
def test_native_owned_pane_hands_off_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_panes: dict[str, str], kind: str,
    tmp_tracking_dir: Path, monkeypatch_config: None,
) -> None:
    record = tracking.create_new_record(
        "example", "worktree/example", str(tmp_path / "checkout"), "demo", "test",
        "linux", tmp_tracking_dir,
    )
    seed = launch_seed_state.stage(record.yaml_path, kind=kind, text="first line\n'quoted' second line")
    receipt = tmp_path / "backend-argv.json"
    backend = tmp_path / "backend.py"
    backend.write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("TMUX_PANE", native_panes["custom-session"])
    assert launch_seed_exec.launch(
        record.yaml_path, [sys.executable, str(backend), str(receipt)],
        invoke=True, seed_id=seed.seed_id,
    ) == 3
    assert launch_seed_state.peek(record.yaml_path) == seed
    assert not receipt.exists()

    monkeypatch.setenv("TMUX_PANE", native_panes["wt-example"])
    assert launch_seed_exec.launch(
        record.yaml_path, [sys.executable, str(backend), str(receipt)],
        invoke=True, seed_id=seed.seed_id,
    ) == 0
    assert json.loads(receipt.read_text(encoding="utf-8")) == ["--interactive", seed.text]
    assert launch_seed_state.peek(record.yaml_path) is None


@pytest.mark.parametrize(("mux", "target"), [("tmux", "=wt-example:"), ("psmux", "wt-example")])
def test_active_pane_targets_preserve_mux_syntax(
    monkeypatch: pytest.MonkeyPatch, mux: str, target: str,
) -> None:
    calls = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="%7\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert sessions.mux_active_pane("example", mux=mux) == "%7"
    assert sessions.mux_active_pane_named("wt-example", mux=mux) == "%7"
    assert calls == [[mux, "display-message", "-p", "-t", target, "#{pane_id}"]] * 2
