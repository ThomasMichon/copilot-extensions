"""Worktree-row actions for supervised remote workers (venue-pivots-ux)."""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest
from worktree_manager.production_picker.picker_tui import engine_worker_actions as ewa
from worktree_manager.production_picker.picker_tui import pivots

_WID = "host-win-20260925-120000-ab12"


def _which(found):
    return lambda name: found.get(name)


# --- the new-window launcher ----------------------------------------------------

def test_new_window_prefers_tmux_inside_a_tmux_session():
    argv = ["/bin/agent-codespaces", "copilot", "cs one", "--effort", "fix"]
    got = ewa.venue_window_argv(
        argv, "cs one", env={"TMUX": "/tmp/tmux-1/default,1,0"},
        which=_which({"tmux": "/usr/bin/tmux", "wt": "wt"}), platform="linux")
    assert got == ["/usr/bin/tmux", "new-window", "-n", "cs one",
                   "/bin/agent-codespaces copilot 'cs one' --effort fix"]


def test_new_window_quotes_for_windows_inside_psmux():
    got = ewa.venue_window_argv(
        [r"C:\bin\agent-codespaces.cmd", "copilot", "cs1"], "cs1",
        env={"TMUX": "1"}, which=_which({"tmux": "tmux.exe"}), platform="win32")
    assert got == ["tmux.exe", "new-window", "-n", "cs1", r"C:\bin\agent-codespaces.cmd copilot cs1"]


def test_new_window_uses_a_windows_terminal_tab_outside_tmux():
    got = ewa.venue_window_argv(
        ["agent-codespaces.cmd", "copilot", "cs1"], "cs1", env={},
        which=_which({"wt": "wt.exe"}), platform="win32")
    assert got == ["wt.exe", "-w", "0", "new-tab", "--title", "cs1",
                   "agent-codespaces.cmd", "copilot", "cs1"]


def test_new_window_is_unavailable_without_tmux_or_windows_terminal():
    assert ewa.venue_window_argv(["x"], "t", env={}, which=_which({}), platform="linux") is None
    assert ewa.venue_window_argv(["x"], "t", env={"TMUX": "1"}, which=_which({}), platform="win32") is None


# --- engine behavior ------------------------------------------------------------

class _Engine(ewa.PickerScreenWorkerActionsMixin):
    def __init__(self, workers=(), pivot_names=(), rows=()):
        self._workers = list(workers)
        self.pivots = [{"kind": "worktrees"}] + [
            {"kind": "registered", "pivot": types.SimpleNamespace(name=n, id_field="id")}
            for n in pivot_names
        ]
        self._rows = list(rows)
        self.htab, self.btn_idx, self.top, self.sel = 0, 3, 9, ("L", 0)
        self.debug = ""
        self.menus = 0
        self.decisions = []

    def _worktree_supervised_workers(self, rec):
        return self._workers

    def _reg_pivot(self):
        return self.pivots[self.htab].get("pivot")

    def _task_rows(self):
        return self._rows

    def default_sel(self):
        return ("T", 0)

    def refresh(self):
        pass

    def _open_task_menu(self):
        self.menus += 1

    def _open_venue(self, ctx):
        self.decisions.append(ctx)
        return True, "fallback"


def test_worker_menu_verbs_label_each_worker_once():
    eng = _Engine(workers=[
        {"pivot": "agent-codespaces", "id": "cs1", "label": "cs-one"},
        {"pivot": "agent-codespaces", "id": "cs1", "label": "cs-one"},
        {"pivot": "agent-containers", "id": "box", "label": ""},
    ])
    labels, by_label = eng._worker_menu_verbs({"id": _WID})
    assert labels == ["Worker: cs-one", "Worker: box"]
    assert by_label["Worker: box"]["pivot"] == "agent-containers"


def test_open_worker_row_focuses_the_venue_row_and_opens_its_menu():
    eng = _Engine(pivot_names=["tasks", "agent-codespaces"],
                  rows=[{"id": "cs0"}, {"id": "cs1"}])
    eng._open_worker_row({"pivot": "agent-codespaces", "id": "cs1"})
    assert (eng.htab, eng.btn_idx, eng.top, eng.sel) == (2, 0, 0, ("T", 1))
    assert eng.menus == 1


def test_open_worker_row_lands_on_the_pivot_when_the_row_is_not_loaded():
    eng = _Engine(pivot_names=["agent-codespaces"], rows=[{"id": "other"}])
    eng._open_worker_row({"pivot": "agent-codespaces", "id": "cs1"})
    assert eng.htab == 1 and eng.sel == ("T", 0) and eng.menus == 0
    assert "not loaded" in eng.debug


def test_open_worker_row_reports_a_missing_pivot():
    eng = _Engine(pivot_names=["tasks"])
    eng._open_worker_row({"pivot": "agent-codespaces", "id": "cs1"})
    assert eng.htab == 0 and eng.menus == 0 and "not available" in eng.debug


def test_open_venue_window_carries_the_effort_owner(monkeypatch):
    launched = []
    monkeypatch.setattr(ewa.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(ewa, "venue_window_argv", lambda argv, title: ["term", title, *argv])
    monkeypatch.setattr(ewa.subprocess, "Popen", lambda argv, **kw: launched.append(argv))
    eng = _Engine()
    ok, msg = eng._open_venue_window({"provider": "agent-codespaces", "id": "cs1", "effort": "fix-relay"})
    assert ok and "new window" in msg
    assert launched == [["term", "cs1", "/bin/agent-codespaces", "copilot", "cs1", "--effort", "fix-relay"]]


def test_open_venue_window_falls_back_to_exit_and_attach(monkeypatch):
    monkeypatch.setattr(ewa.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(ewa, "venue_window_argv", lambda argv, title: None)
    eng = _Engine()
    assert eng._open_venue_window({"provider": "agent-containers", "id": "box"}) == (True, "fallback")
    assert eng.decisions and eng.decisions[0]["id"] == "box"


def test_open_venue_window_needs_a_provider_and_venue():
    ok, msg = _Engine()._open_venue_window({"id": "cs1"})
    assert not ok and "missing" in msg


def test_open_bridge_ui_reports_a_missing_binstub(monkeypatch):
    monkeypatch.setattr(ewa.shutil, "which", lambda name: None)
    ok, msg = _Engine()._open_bridge_ui({})
    assert not ok and "agent-bridge" in msg


def test_supervised_workers_carry_the_venue_row_id():
    from worktree_manager.production_picker.picker_tui import pivot_manifest

    spec = pivot_manifest.WorkerSpec(worktree_field="worktree_id", label_field="display")
    reg = types.SimpleNamespace(name="agent-codespaces", id_field="id", account_scoped=True, worker=spec)

    class _Rt:
        def get(self, scope):
            return ("ready", [{"id": "cs1", "display": "cs-one", "worktree_id": _WID}], "")

    [worker] = pivots.find_supervised_workers([{"pivot": reg}], {reg.name: _Rt()}, "host", _WID)
    assert worker["id"] == "cs1"


@pytest.mark.parametrize("plugin", ["agent-codespaces", "agent-containers"])
def test_venue_manifests_offer_inspect_and_watch(plugin):
    repo = Path(__file__).resolve().parents[3]
    path = repo / "plugins" / plugin / "pivots" / f"{plugin}.json"
    reg = pivots.parse_manifest(json.loads(path.read_text(encoding="utf-8")),
                                name=plugin, source_path=str(path))
    verbs = {a.internal for a in reg.actions if a.internal}
    assert {"open-venue-window", "open-bridge-ui"} <= verbs
