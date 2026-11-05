"""Fixture observation failures cannot manufacture Companion application failures."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_companion_joined_tmux import source_entrypoint
from worktree_manager import companion_bridge_source, mux_companion


@pytest.mark.parametrize(
    "platform,code,retry",
    [("nt", 32, True), ("nt", 33, True), ("nt", 5, False), ("posix", 32, False)],
)
def test_observer_retries_only_native_sharing_without_advancing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    platform: str, code: int, retry: bool,
) -> None:
    monkeypatch.setattr(mux_companion, "_load_current_worktree", lambda *_args, **_kw: SimpleNamespace(
        worktree={}, sessions=[], bundle={},
    ))
    monkeypatch.setattr(mux_companion.ec, "engine_base_command", lambda: ["fixture"])
    monkeypatch.setattr(companion_bridge_source, "bridge_command", lambda _root: ["fixture"])
    source_entrypoint({"checkout": tmp_path / "checkout"}, monkeypatch)
    parsed = ast.parse((tmp_path / "native-app.py").read_text(encoding="utf-8"))
    mount = next(node for node in parsed.body if isinstance(node, ast.FunctionDef)
                 and node.name == "observed_mount")
    callbacks = []
    timeline = SimpleNamespace(
        size=(80, 20), window=SimpleNamespace(follow_live=True, events=[], sources={}),
    )
    app = SimpleNamespace(
        size=(80, 24), focused=None, query_one=lambda _kind: timeline,
        set_interval=lambda _seconds, callback: callbacks.append(callback),
    )
    namespace = {
        "Path": Path, "json": json, "os": SimpleNamespace(name=platform),
        "mount": lambda _app: None, "SubwayTimeline": object,
    }
    exec(compile(ast.Module(body=[mount], type_ignores=[]), "<fixture observer>", "exec"), namespace)
    namespace["observed_mount"](app)
    real_replace, calls = Path.replace, []

    def replace(path: Path, target: Path) -> Path:
        calls.append(path)
        if len(calls) == 1:
            error = PermissionError("synthetic native sharing refusal")
            error.winerror = code
            raise error
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", replace)
    target = tmp_path / "native-state.json"
    if not retry:
        with pytest.raises(PermissionError, match="native sharing refusal"):
            callbacks[0]()
        assert not target.exists()
        return
    callbacks[0]()
    assert not target.exists()
    callbacks[0]()
    assert json.loads(target.read_text(encoding="utf-8"))["app_cells"] == [80, 24]
    callbacks[0]()
    assert len(calls) == 2
