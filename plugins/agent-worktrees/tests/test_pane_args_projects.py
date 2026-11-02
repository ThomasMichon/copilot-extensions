import os

import pytest

from agent_worktrees import allocation_link_discovery, config, status_monitor_cli


def _handoffs(tracking_root):
    shell = tracking_root / "launch-attempts" / ("a" * 32) / ("b" * 32 + ".shell")
    root = shell / "pane-args"
    root.mkdir(parents=True)
    expired = root / "aw-pane-expired.json"
    expired.write_text("expired")
    os.utime(expired, (0, 0))
    pending = root / "aw-pane-pending.json"
    pending.write_text("pending")
    wrapper = shell / "pane-wrapper.ps1"
    wrapper.write_text("retained execution")
    return expired, pending, wrapper


@pytest.mark.parametrize("ambient", [None, "unrelated"])
def test_monitor_sweeps_all_registered_projects_without_ambient_context(tmp_path, monkeypatch, ambient):
    roots = {name: tmp_path / name / "worktrees" for name in ("one", "two")}
    handoffs = {name: _handoffs(root) for name, root in roots.items()}
    foreign = _handoffs(tmp_path / "unregistered" / "worktrees")
    runtime = tmp_path / "runtime"
    legacy = runtime / "pane-args" / "aw-pane-legacy.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("expired")
    os.utime(legacy, (0, 0))
    calls = []

    def tracking_dir(project=None):
        assert project in roots, "sweep queried ambient or unregistered project context"
        calls.append(project)
        return roots[project]

    monkeypatch.setattr(config, "_ACTIVE_PROJECT", ambient)
    monkeypatch.setattr(config, "tracking_dir", tracking_dir)
    monkeypatch.setattr(config, "install_dir", lambda: runtime)
    monkeypatch.setattr(allocation_link_discovery, "registered_projects", lambda: set(roots))
    status_monitor_cli.sweep_mux_pane_args()
    assert calls == ["one", "two"]
    assert config.active_project() == ambient
    assert not legacy.exists()
    for expired, pending, wrapper in handoffs.values():
        assert not expired.exists()
        assert pending.read_text() == "pending"
        assert wrapper.read_text() == "retained execution"
    assert all(path.exists() for path in foreign)


def test_monitor_keeps_sweeping_other_projects_after_one_resolution_error(tmp_path, monkeypatch, capsys):
    root = tmp_path / "two" / "worktrees"
    expired, pending, wrapper = _handoffs(root)

    def tracking_dir(project=None):
        assert project in {"one", "two"}
        if project == "one":
            raise RuntimeError("unavailable project state")
        return root

    monkeypatch.setattr(config, "tracking_dir", tracking_dir)
    monkeypatch.setattr(config, "install_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(allocation_link_discovery, "registered_projects", lambda: {"one", "two"})
    status_monitor_cli.sweep_mux_pane_args()
    assert not expired.exists()
    assert pending.exists() and wrapper.exists()
    assert "Could not resolve pane argument project one" in capsys.readouterr().out


def test_monitor_inventory_error_preserves_legacy_expiry_without_ambient_lookup(tmp_path, monkeypatch, capsys):
    root = tmp_path / "runtime"
    expired = root / "pane-args" / "aw-pane-expired.json"
    expired.parent.mkdir(parents=True)
    expired.write_text("expired")
    os.utime(expired, (0, 0))

    def unavailable():
        raise RuntimeError("unavailable project inventory")

    monkeypatch.setattr(config, "install_dir", lambda: root)
    monkeypatch.setattr(config, "tracking_dir", lambda *args: pytest.fail("ambient lookup"))
    monkeypatch.setattr(allocation_link_discovery, "registered_projects", unavailable)
    status_monitor_cli.sweep_mux_pane_args()
    assert not expired.exists()
    assert "Could not enumerate pane argument projects" in capsys.readouterr().out
