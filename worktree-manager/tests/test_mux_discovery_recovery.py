"""Recovery must pass both engine discovery and Manager's native admission."""

from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from worktree_manager import mux_attached_clients, mux_daemon, mux_discovery
from worktree_manager.mux_mapping_registry import MuxMappingRegistry


@pytest.mark.parametrize("mux", ["tmux", "psmux"])
def test_engine_discovery_recovers_home_started_manager_mapping(monkeypatch, tmp_path, mux):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "plugins" / "agent-worktrees" / "src"))
    from agent_worktrees import mux_link
    from agent_worktrees import status_monitor_discovery as engine

    home = tmp_path / "home"
    worktree = tmp_path / "worktree"
    home.mkdir()
    worktree.mkdir()
    current = "$2:200"
    pane_directory = [str(worktree)]
    attachments = [1]

    def native(argv, **kwargs):
        if argv[-1] == "#{pane_current_path}":
            output = f"{home}\n{pane_directory[0]}\n"
        elif "pane_current_path" in argv[-1]:
            output = (
                f"wt-one\t{attachments[0]}\t{current}\t{home}\n"
                f"wt-one\t{attachments[0]}\t{current}\t{pane_directory[0]}\n"
            )
        else:
            output = f"wt-one\t1\t{current}\t{home}\n"
        return subprocess.CompletedProcess(argv, 0, stdout=output)

    monkeypatch.setattr(engine.subprocess, "run", native)
    monkeypatch.setattr(mux_discovery, "run_mux_child", native)
    monkeypatch.setattr(engine, "_tracked_worktrees", lambda: [
        ("project", SimpleNamespace(worktree_id="one", worktree_path=str(worktree))),
    ])
    monkeypatch.setattr(engine, "_last_session", None)
    monkeypatch.setattr(engine, "_retry_after", {})
    monkeypatch.setattr(engine, "_register_standalone",
                        lambda *args: pytest.fail("Manager ownership must not fall back"))
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    registry.register(dict(
        project="project", worktree_id="one", worktree_path=str(worktree),
        mux_session="wt-one", mux_bin=mux, session_incarnation="$1:100",
        attached_clients=0, mapping_revision=9, live=False,
    ))
    compute = mux_daemon.build_compute(registry)
    monkeypatch.setattr(engine, "discover_via_manager",
                        lambda payload: compute(mux_discovery.KIND, payload))
    cache = mux_link.ManagedMuxCache()

    assert engine.discover_connected_worktrees(
        {"wt-one": (1, current)}, mux, cache, registered_paths={},
    ) == {}
    entry = registry.get("project", "one")
    assert entry["live"] and entry["session_incarnation"] == current
    assert entry["mapping_revision"] == 10
    assert cache.get("project", "one")["session_incarnation"] == current
    writes = []
    monkeypatch.setattr(mux_daemon, "apply_status_options",
                        lambda entry, values: writes.append(values) or True)
    monkeypatch.setattr(mux_daemon.mux_daemon_live, "publish_live_observation",
                        lambda *args, **kwargs: {"applied": True})
    (worktree / "src").mkdir()
    pane_directory[0] = str(worktree / "src")
    result = compute(mux_daemon.KIND, dict(
        project="project", worktree_id="one", mux_session="wt-one",
        session_incarnation=current, mapping_revision=10,
        rendered_at="2026-01-01T00:00:00Z",
        values={"@aw_ctx": "CTX", "@aw_seg": "SEG"},
    ))
    assert result["applied"]
    assert writes[0]["@aw_ctx"] == "CTX" and writes[0]["@aw_seg"] == "SEG"
    attachments[0] = 0
    refreshed = mux_attached_clients.refresh_attached_clients(registry, entry)
    assert refreshed["attached_clients"] == 0
    assert registry.get("project", "one")["attached_clients"] == 0


@pytest.mark.parametrize("second_identity", ["2\t$1:100", "1\t$2:200"])
def test_conflicting_pane_session_identity_does_not_admit(
    monkeypatch, tmp_path, second_identity,
):
    output = (
        f"wt-one\t1\t$1:100\t{tmp_path}\n"
        f"wt-one\t{second_identity}\t{tmp_path}\n"
    )
    monkeypatch.setattr(mux_discovery, "run_mux_child", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, stdout=output))
    assert mux_discovery.confirm_native_session(dict(
        mux_bin="mux", mux_session="wt-one", session_incarnation="$1:100",
        worktree_path=str(tmp_path),
    ), connected_only=True) == {"applied": False, "reason": "session-unconfirmed"}


@pytest.mark.parametrize("dead_session", ["wt-one", "wt-other"])
def test_empty_dead_pane_path_does_not_block_attributed_session(
    monkeypatch, tmp_path, dead_session,
):
    output = f"wt-one\t1\t$1:100\t{tmp_path}\n{dead_session}\t1\t$1:100\t\n"
    monkeypatch.setattr(mux_discovery, "run_mux_child", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, stdout=output))
    assert mux_discovery.confirm_native_session(dict(
        mux_bin="mux", mux_session="wt-one", session_incarnation="$1:100",
        worktree_path=str(tmp_path),
    ), connected_only=True) == {"applied": True, "attached_clients": 1}


def test_empty_pane_path_never_counts_as_checkout_evidence(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mux_discovery, "run_mux_child", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, stdout="wt-one\t1\t$1:100\t\n"))
    assert mux_discovery.confirm_native_session(dict(
        mux_bin="mux", mux_session="wt-one", session_incarnation="$1:100",
        worktree_path=str(tmp_path),
    ), connected_only=True) == {"applied": False, "reason": "unattributed-path"}
