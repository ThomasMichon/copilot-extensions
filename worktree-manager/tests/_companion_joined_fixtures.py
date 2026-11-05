"""Actual private Worktrees and managed Agenda owners for joined UI scenarios."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import importlib
import json
from pathlib import Path
import sys
from typing import Any

import pytest

from worktree_manager import mux_companion as companion
from test_agenda_end_to_end import ROOT, SOURCE_PATHS
from test_companion_bridge_codec import OWNER
from _worktree_timeline_fixtures import resident_join


@contextmanager
def managed_join(
    root: Path, monkeypatch: pytest.MonkeyPatch, *, clock_origin: float = 0,
) -> Iterator[dict[str, Any]]:
    """Share the existing three-owner setup without replacing its production boundaries."""
    root.mkdir()
    with resident_join(root, monkeypatch, with_agenda=True, clock_origin=clock_origin) as peer:
        config = importlib.import_module("agent_worktrees.config")
        project_state = importlib.import_module("agent_worktrees.project_state")
        placement = importlib.import_module("agent_worktrees.worktree_data_store")
        store = importlib.import_module("agenda_store")
        publisher = importlib.import_module("agenda_publish")
        monkeypatch.setattr(config, "_home", lambda: root / "profile")
        monkeypatch.setattr(project_state, "_normalized_repository_remote", lambda project: f"network:example.invalid/{project}")
        checkout = peer["checkout"]
        settings = checkout / ".github/copilot/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "enabledPlugins": {"efforts@source-fixture": True},
            "extraKnownMarketplaces": {"source-fixture": {
                "source": {"source": "directory", "path": str(ROOT), "manifestName": "copilot-extensions"},
            }},
        }))
        bootstrap = root / "bootstrap/sitecustomize.py"
        bootstrap.write_text(bootstrap.read_text() + (
            f"\nconfig._home=lambda:Path({str(root / 'profile')!r})\n"
            "from agent_worktrees import project_state\n"
            "project_state._normalized_repository_remote=lambda project:f'network:example.invalid/{project}'\n"
        ))
        interpreter = root / "selected-python"
        prefix = (
            f"import sys;sys.path[:0]={[str(root / 'bootstrap'), *map(str, SOURCE_PATHS)]!r};"
            "import sitecustomize\n"
        )
        interpreter.write_text(
            f"#!{sys.executable} -I\nimport os,sys\n"
            f"prefix={prefix!r}\n"
            "if '-c' in sys.argv:\n"
            "    index=sys.argv.index('-c')\n"
            "    code=prefix+sys.argv[index+1]; args=sys.argv[index+2:]\n"
            "else:\n"
            "    assert sys.argv[1:3]==['-m','agent_worktrees']\n"
            "    code=prefix+'from agent_worktrees.__main__ import main;main(sys.argv[1:])'; args=sys.argv[3:]\n"
            "os.execv(sys.executable,[sys.executable,'-I','-B','-c',code,*args])\n",
        )
        interpreter.chmod(0o700)
        monkeypatch.setattr(companion.ec, "engine_base_command", lambda: [str(interpreter), "-m", "agent_worktrees"])
        allocated = placement.allocate_worktree_data(
            "example", "selected", creation_nonce="f" * 32,
            domain="agendas", lane="main", store_id="c" * 32,
        )
        home = Path(allocated.state_home)
        initialization = json.loads((ROOT / "plugins/efforts/skills/driving-effort-agendas/assets/init.context-qualified.example.json").read_text())
        initialization["change"]["worktree"] = "selected"
        store.mutate(home, initialization, session="head-a", initial_store_id="c" * 32)

        def data() -> companion._CompanionData:
            return companion._CompanionData(
                worktree={**peer["row"], "repo": "example", "path": str(checkout)},
                sessions=[{"id": "head-a", "is_head": True}],
                bundle={"facts": {"claims": {"value": {"owner_ref": OWNER}, "confirmed": True}}},
                attention_error=None,
            )

        monkeypatch.setattr(companion, "_load_current_worktree", lambda *args, **kwargs: data())
        yield {
            **peer, "agenda_home": home, "agenda_store": store, "agenda_publisher": publisher,
            "provider_python": str(interpreter),
        }
