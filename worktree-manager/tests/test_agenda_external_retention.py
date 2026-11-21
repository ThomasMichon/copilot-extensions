"""The actual Agendas reader follows retained owner placement without a checkout."""

from typing import Any

import importlib
import json
from pathlib import Path
import sys
from threading import Event

import pytest

from worktree_manager import companion_agenda_commands as commands
from worktree_manager import agenda_transport as transport
from worktree_manager import engine_client as ec
from test_agenda_end_to_end import SOURCE_PATHS, ROOT, SKILL

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


def test_fresh_consumer_reads_external_history_after_checkout_and_record_removal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for path in reversed(SOURCE_PATHS):
        monkeypatch.syspath_prepend(str(path))
    home, anchor, checkout = (tmp_path / name for name in ("profile", "anchor", "checkout"))
    for path in (home, anchor, checkout):
        path.mkdir()
    for name in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    cfg = importlib.import_module("agent_worktrees.config")
    state = importlib.import_module("agent_worktrees.project_state")
    owner = importlib.import_module("agent_worktrees.worktree_data_store")
    store = importlib.import_module("agenda_store")
    monkeypatch.setattr(cfg, "_home", lambda: home)
    monkeypatch.setattr(state, "_normalized_repository_remote", lambda project: f"network:example.invalid/{project}")
    records = cfg.project_dir("example") / "worktrees"
    records.mkdir(parents=True)
    record = records / "selected.yaml"
    record.write_text(json.dumps({
        "worktree_id": "selected", "creation_nonce": "retained-incarnation", "repo": "example",
        "branch": "worktree/selected", "worktree_path": str(checkout),
        "machine": "fixture", "platform": "linux",
    }), encoding="utf-8")
    store_id = "b" * 32
    placement = owner.allocate_worktree_data(
        "example", "selected", creation_nonce="retained-incarnation",
        domain="agendas", lane="main", store_id=store_id,
    )
    agenda_home = Path(placement.state_home)
    assert agenda_home.is_relative_to(records / "selected")
    request = json.loads((SKILL / "assets" / "init.context-qualified.example.json").read_text())
    request["change"]["worktree"] = "selected"
    store.mutate(agenda_home, request, initial_store_id=store_id)
    settings = anchor / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "enabledPlugins": {"efforts@source-fixture": True},
        "extraKnownMarketplaces": {"source-fixture": {
            "source": {"source": "directory", "path": str(ROOT), "manifestName": "copilot-extensions"},
        }},
    }), encoding="utf-8")
    prefix = (
        f"import sys\nfrom pathlib import Path\nsys.path[:0]={[str(path) for path in SOURCE_PATHS]!r}\n"
        "from agent_worktrees import config,project_state\n"
        f"config._home=lambda:Path({str(home)!r})\n"
        "project_state._normalized_repository_remote=lambda project:f'network:example.invalid/{project}'\n"
    )
    point = commands._point
    frames = transport.command_frames
    # This proves the consumer/storage contract, not cold-start latency on a
    # shared Windows host. Keep production frame deadlines unchanged.
    monkeypatch.setattr(transport, "command_frames", lambda *args, **kwargs: frames(
        *args, **{**kwargs, "startup_timeout": 60, "idle_timeout": 60},
    ))
    calls = []
    def invoke(argv: Any, cancelled: Any, cwd: str) -> Any:
        calls.append(argv)
        index = argv.index("-c") + 1
        return point([*argv[:index], prefix + argv[index], *argv[index + 1:]], cancelled, cwd)
    monkeypatch.setattr(commands, "_point", invoke)
    monkeypatch.setattr(ec, "engine_base_command", lambda: [sys.executable, "-m", "agent_worktrees"])
    checkout.rmdir()
    record.unlink()
    before = {path: path.read_bytes() for path in agenda_home.rglob("*") if path.is_file()}
    reader = commands.AgendaCommands(
        "example", "selected", store_id, cwd=str(anchor), project_root=str(anchor),
    )
    reader.discover(Event())
    result = reader.read(Event(), history=True)
    assert reader.placement is not None
    assert reader.placement["state_home"] == str(agenda_home)
    assert result["type"] == "history-page" and result["source"]["store_id"] == store_id
    assert len(result["entries"]) == 1 and result["entries"][0]["operation"] == "init"
    assert {path: path.read_bytes() for path in before} == before
    assert not checkout.exists() and not record.exists()
    assert all("worktree-data-allocate" not in argv for argv in calls)
