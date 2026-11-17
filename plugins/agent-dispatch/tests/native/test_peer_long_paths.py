"""Real Windows peer execution from a selected runtime beyond MAX_PATH."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_dispatch import peer_launch, procutil
from ..test_procutil import (
    FIRST_MARKETPLACE_ID, _make_live_peer, _make_namespaced_context, _update_receipt,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows executable paths")


def _supplementary_home(home: Path, relative_python: Path) -> Path | None:
    units = len(str(home / relative_python).encode("utf-16-le", errors="surrogatepass")) // 2
    selected = home / ("\U0001f600" * max(1, (260 - units) // 2))
    return selected if len(str(selected / relative_python)) < 260 else None


@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("supplementary", [False, True], ids=["ascii", "supplementary"])
@pytest.mark.parametrize("deep_named_tmp", [False, True], ids=["default-temp", "deep-temp"])
def test_selected_long_path_peer_preserves_arguments_and_stdio(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch,
    pinned: bool, supplementary: bool, deep_named_tmp: bool,
) -> None:
    if deep_named_tmp:
        tmp_path /= "deep-caller-" + "x" * 120
    version = "1.0.0+" + "x" * 150
    if supplementary:
        version = "1.0.0"
        relative_python = (
            Path("marketplaces") / FIRST_MARKETPLACE_ID / "plugins"
            / "agent-worktrees" / "versions" / version / ".venv" / "Scripts" / "python.exe"
        )
        selected = _supplementary_home(tmp_path, relative_python)
        if selected is None:
            selected = _supplementary_home(tmp_path_factory.mktemp("peer"), relative_python)
        if selected is None:
            pytest.skip("fixture-owned temporary base cannot fit a sub-260-code-point interpreter")
        tmp_path = selected
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own = _make_namespaced_context(cell)
    python = _make_live_peer(cell, "agent-worktrees", version, nested=True)
    assert len(str(python).encode("utf-16-le")) // 2 >= 260
    if supplementary:
        assert len(str(python)) < 260
    slot = python.parent.parent.parent
    if pinned:
        bootstrap = slot / ".runtime-bootstrap"
        bootstrap.mkdir()
        scripts = Path(__file__).resolve().parents[3] / "agent-worktrees" / "scripts"
        for source in (scripts / "runtime_launcher.py", *scripts.glob("versioned_runtime*.py")):
            shutil.copyfile(source, bootstrap / source.name)
        (bootstrap / "runtime-generation").write_text("1" * 32, encoding="ascii")
        _update_receipt(slot / ".install-complete.json", lambda data: data.update(payload_hash="a" * 64))
    arguments = ("agent-dispatch", own.parent, str(own), "agent-worktrees")
    prefix = (
        peer_launch.pinned_launch_prefix(*arguments)[0] if pinned
        else peer_launch.launch_prefix(*arguments)
    )
    result = subprocess.run(
        [*prefix, "probe", 'quoted " argument', "snowman \u2603"],
        capture_output=True, encoding="utf-8", timeout=20, **procutil.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert Path(output["python"]).samefile(python)
    assert output["argv"] == ["probe", 'quoted " argument', "snowman \u2603"]
    streams = subprocess.run(
        [*prefix, "streams"], input="long-path stdin\n", capture_output=True,
        encoding="utf-8", timeout=20, **procutil.no_window_kwargs(),
    )
    assert (streams.returncode, streams.stdout, streams.stderr) == (
        7, "long-path stdin\n", "peer-stderr\n",
    )
