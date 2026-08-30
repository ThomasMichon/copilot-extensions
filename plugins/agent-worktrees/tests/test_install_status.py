"""Install-status runtime resolution regressions."""

from __future__ import annotations

import platform
import subprocess

from agent_worktrees import config, installer


def _configure_status(tmp_path, monkeypatch):
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
    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    return active_python


def test_install_status_probes_marker_selected_runtime(tmp_path, monkeypatch, capsys):
    active_python = _configure_status(tmp_path, monkeypatch)
    monkeypatch.setenv("PYTHONPATH", "/stale/legacy/lib")

    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        environments.append(kwargs["env"])
        return subprocess.CompletedProcess(argv, 0, stdout="/active/agent_worktrees\n")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)

    installer.show_install_status()

    assert calls[0][0] == str(active_python)
    assert "PYTHONPATH" not in environments[0]
    assert "Package importable: /active/agent_worktrees" in capsys.readouterr().out


def test_install_status_rejects_stale_legacy_package(tmp_path, monkeypatch, capsys):
    _configure_status(tmp_path, monkeypatch)
    (tmp_path / "lib" / "agent_worktrees").mkdir(parents=True)
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, stderr="import failed"),
    )

    installer.show_install_status()

    output = capsys.readouterr().out
    assert "Stale legacy package present" in output
    assert "Package missing: active runtime cannot import agent_worktrees" in output
    assert "Package deployed" not in output


def test_venv_python_prefers_last_known_good_over_newest_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "install_dir", lambda: tmp_path)
    subdir, name = (
        ("Scripts", "python.exe") if platform.system() == "Windows" else ("bin", "python")
    )

    def make_slot(version):
        python = tmp_path / "versions" / version / subdir / name
        python.parent.mkdir(parents=True)
        python.touch()
        return python

    lkg_python = make_slot("1.5.3-dev595")
    make_slot("1.5.3-dev597")
    (tmp_path / "current-version").write_text("1.5.3-dev596", encoding="utf-8")
    (tmp_path / "last-known-good").write_text("1.5.3-dev595", encoding="utf-8")

    assert config.venv_python() == lkg_python
