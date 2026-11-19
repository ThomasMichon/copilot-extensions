"""Actual placement process, complete export, rescue CLI and independent reads."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import project_state, worktree_data_store

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


def test_actual_allocator_cutover_preserves_history_and_has_one_writable_locator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    project, worktree = "sample-project", "machine-20300101-123456-abcd"
    nonce, identity = "0123456789abcdef", "a" * 32
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(cfg, "_home", lambda: tmp_path)
    root = cfg.project_dir(project)
    (root / "worktrees").mkdir(parents=True)
    (root / "worktrees" / f"{worktree}.yaml").write_text(json.dumps({
        "worktree_id": worktree, "creation_nonce": nonce, "repo": project,
        "branch": "worktree/sample", "worktree_path": str(tmp_path / "old-checkout"),
        "machine": "sample-machine", "platform": sys.platform,
    }), encoding="utf-8")
    skill = Path(__file__).resolve().parents[2] / "efforts" / "skills" / "driving-effort-agendas"
    monkeypatch.syspath_prepend(str(skill / "scripts"))
    rescue = importlib.import_module("agenda_rescue")
    prefix = (
        "import os\nfrom pathlib import Path\n"
        "os.environ.pop('COPILOT_EXTENSIONS_CONTEXT',None)\n"
        "from agent_worktrees import config as cfg, project_state, __main__ as main\n"
        f"cfg._home=lambda:Path({str(tmp_path)!r})\n"
        "project_state._normalized_repository_remote=lambda repo:f'network:example.invalid/{repo}'\n"
        "def no_cluster():\n    raise AssertionError('placement loaded deferred cluster')\n"
        "main._ensure_cluster_loaded=no_cluster\n"
    )
    monkeypatch.setattr(rescue.managed, "PROVIDER", prefix + rescue.managed.PROVIDER)
    monkeypatch.setattr(
        project_state, "_normalized_repository_remote",
        lambda repository: f"network:example.invalid/{repository}",
    )
    source = tmp_path / "old-checkout" / ".effort-agenda" / "main"
    source.mkdir(parents=True)
    request = json.loads((skill / "assets" / "init.context-qualified.example.json").read_text())
    request["change"]["worktree"] = worktree
    accepted, _ = rescue.store.mutate(source, request, initial_store_id=identity)
    transition = accepted["transition"]
    rescue.store.mutate(source, {
        "schema": rescue.store.SCHEMA, "version": 2, "mode": "context-qualified",
        "expected_revision": 1, "operation_id": "consumer-wait",
        "change": {
            "operation": "wait", "frame_id": transition["frame_id"],
            "item_id": transition["item_id"], "reason_kind": "external",
            "reason_ref": "fixture evidence", "next_action": "Inspect fixture evidence",
            "evidence": [{"criterion": "Evidence retained", "kind": "measured",
                          "ref": "fixture", "inputs": "consumer revision"}],
        },
    })
    saved = tmp_path / "complete-export"
    saved.mkdir(mode=0o700)
    manifest = rescue.export.export_store(
        source, saved, worktree=worktree, store_id=identity, expected_revision=2,
    )
    argv = [
        "relocate", "--source-home", str(source), "--export-home", str(saved),
        "--provider-python", sys.executable, "--project", project, "--worktree", worktree,
        "--creation-nonce", nonce, "--lane", "rescued", "--store-id", identity,
        "--expected-revision", "2", "--operation-id", "consumer-rescue",
    ]
    assert rescue.main(argv) == 0
    reply = json.loads(capsys.readouterr().out)
    home = Path(reply["state_home"])
    assert home.is_relative_to(root.resolve()) and reply["activated"] is True
    registered = worktree_data_store.read_worktree_data(
        project, worktree, store_id=identity, creation_nonce=nonce, placement_generation=1,
    )
    assert registered is not None
    assert registered.placements[0].state_home == str(home)
    assert reply["placement"] == registered.placements[0].to_dict()
    options: dict[str, Any] = {"capture_output": True, "text": True, "timeout": 20}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    for selected, code in ((source, 2), (home, 0)):
        read = subprocess.run(
            [sys.executable, "-B", str(skill / "scripts" / "agenda_cli.py"),
             "read", "--state-home", str(selected), "--worktree", worktree],
            **options,
        )
        assert read.returncode == code, (read.stdout, read.stderr)
        if code == 0:
            current = json.loads(read.stdout)
            assert current["store_id"] == identity and current["revision"] == 2
        else:
            assert "unsupported" in read.stderr
    assert rescue.export.verify_export(
        saved, worktree=worktree, store_id=identity, expected_revision=2,
    ) == manifest
    assert rescue.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["activated"] is False
    assert source.is_dir() and saved.is_dir()
