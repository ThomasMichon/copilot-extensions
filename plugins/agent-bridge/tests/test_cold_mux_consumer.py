"""Cold inventory reads a fixture-owned pin, never a Bridge registration."""

import asyncio
from contextlib import nullcontext
import json
from pathlib import Path
import runpy
import socket
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from agent_bridge import __main__ as core, target_resolution_cli, worktree_native_resume
from test_worktree_metadata import metadata_boundary as metadata_boundary
from test_worktree_native_resume import boundary as boundary


@pytest.mark.parametrize(("actual", "reason"), [
    ("$7:100", "agent_route_unverified"), ("$7:101", "different-incarnation"),
    ("missing", "missing-rendezvous"), ("unreadable", "native-query-unavailable"),
])
def test_empty_registry_survivor(
    metadata_boundary: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    actual: str, reason: str, capsys: pytest.CaptureFixture[str], request: pytest.FixtureRequest,
) -> None:
    wire = runpy.run_path(str(Path(__file__).resolve().parents[2] / "agent-remote-driver"
                              / "tests" / "test_mux_observation.py"))
    name, pin = "wt-fixture", "$7:100"
    monkeypatch.setenv("PSMUX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COPILOT_DRIVER_PSMUX_NAMESPACE", "fixture")
    b = metadata_boundary
    loop = asyncio.new_event_loop()
    request.addfinalizer(loop.close)
    effects: list[str] = []

    def deny(*args: object, **kwargs: object) -> None:
        effects.append("control")
        pytest.fail("unexpected control operation")

    for action in ("start_session", "resume_session", "import_native_session"):
        monkeypatch.setattr(b.manager, action, deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    assert not b.manager.list_sessions() and not b.db.list_live_sessions(include_dead=True)

    def listing(refresh: bool) -> dict[str, Any]:
        assert not refresh
        with monkeypatch.context() as guarded:
            guarded.setattr(subprocess, "Popen", deny)
            return loop.run_until_complete(worktree_native_resume.list_targets(b.request))

    monkeypatch.setattr(core, "_get_client", lambda *, ensure: SimpleNamespace(list_targets=listing)
                        if ensure is False else pytest.fail("service ensure"))
    baseline = listing(False)
    assert pin not in json.dumps(baseline)
    prefix = [sys.executable, "-I", "-B", "-X", "utf8", "-m", "agent_remote_driver.mux_observation"]
    popen = subprocess.Popen
    modes: list[str] = []

    def selected(argv: list[str], *args: Any, **kwargs: Any) -> Any:
        assert argv[:-1] == prefix
        assert argv[-1] == ("--describe-contract", "--observe")[len(modes)]
        modes.append(argv[-1])
        return popen(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", selected)
    context: Any = nullcontext([])
    if actual.startswith("$"):
        body = (wire["_PANE"] + wire["_IDENTITY"]).replace(pin.encode(), actual.encode())
        context = wire["native_endpoint"](tmp_path, b"OK\n" + body, namespace="fixture")
    elif actual == "unreadable":
        (tmp_path / "fixture__wt-fixture.port").write_bytes(b"invalid")
        (tmp_path / "fixture__wt-fixture.key").write_bytes(b"fixture")
    with context as calls:
        target_resolution_cli.command(SimpleNamespace(json=True, refresh=False, discover_mux=(name, pin)))
    result = json.loads(capsys.readouterr().out)
    assert modes == ["--describe-contract", "--observe"]
    assert calls[1::2] == (list(wire["mux"]._QUERIES) if actual.startswith("$") else [])
    assert result["targets"] == baseline["targets"]
    observation = result["sources"]["pinned_mux"]
    assert observation["route"] == "unresolved" and observation["reason"] == reason
    assert observation.get("incarnation") == (pin if actual == pin else None)
    assert not effects and not b.calls and not b.db.list_live_sessions(include_dead=True)


def test_legacy_declaration_does_not_trigger_unpinned_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PSMUX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COPILOT_DRIVER_PSMUX_NAMESPACE", "fixture")
    modes: list[str] = []

    def legacy(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        modes.append(argv[-1])
        assert argv[-1] == "--describe-contract"
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "contract": "copilot-driver.mux-observation", "version": 1,
            "operations": ["psmux-observe"],
        }).encode())

    monkeypatch.setattr(subprocess, "run", legacy)
    observation = target_resolution_cli.discover_mux("wt-fixture", "$7:100")
    assert observation == {"route": "unresolved", "availability": "unavailable", "reason": "pinned_read_unsupported"}
    assert modes == ["--describe-contract"]
