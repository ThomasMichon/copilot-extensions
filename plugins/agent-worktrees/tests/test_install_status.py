"""Install-status runtime resolution regressions."""

from __future__ import annotations

import subprocess

from agent_worktrees import installer


def test_install_status_probes_marker_selected_runtime(tmp_path, monkeypatch, capsys):
    active_python = tmp_path / "versions" / "1.5.3-dev596" / "bin" / "python"
    active_python.parent.mkdir(parents=True)
    active_python.touch()

    monkeypatch.setattr(installer.cfg, "venv_python", lambda: active_python)
    monkeypatch.setattr(installer.cfg, "project_name", lambda: "example")
    monkeypatch.setattr(installer.cfg, "project_dir", lambda: tmp_path / ".example")
    monkeypatch.setattr(
        installer.cfg, "default_config_path", lambda: tmp_path / ".example" / "config.yaml"
    )
    monkeypatch.setattr(installer.cfg, "tracking_dir", lambda: tmp_path / "worktrees")
    monkeypatch.setattr(installer, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "lib_dir", lambda: tmp_path / "lib")
    monkeypatch.setattr(installer, "bin_dir", lambda: tmp_path / "bin")
    monkeypatch.setattr(installer, "local_bin", lambda: tmp_path / "local-bin")
    monkeypatch.setattr(installer, "read_projects_registry", lambda: {"projects": {}})

    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="/active/agent_worktrees\n")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)

    installer.show_install_status()

    assert calls[0][0] == str(active_python)
    assert "Package importable: /active/agent_worktrees" in capsys.readouterr().out
