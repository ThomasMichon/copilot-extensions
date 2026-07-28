"""Regression guards for the Windows Terminal profile refresh resolution.

dotfiles#211: `register`'s WT-profile refresh silently no-op'd because the
installer script was resolved *only* from the deploy-manifest's `plugin_source`,
which is empty after a marketplace install. The resolver must fall back to the
installed-plugin dir and warn (not silently return) when nothing is found.

dotfiles#563: the mirror ran the full `install.ps1 update` (~60s+) under a 30s
subprocess timeout, so it was killed before the fragment was regenerated and the
failure was swallowed while `mirrored: true` was still reported. The refresh now
runs the narrow `refresh-profiles` action, passes the active project explicitly,
and returns an accurate success bool.
"""
from __future__ import annotations

import json

from agent_worktrees import __main__ as m


def _write_manifest(install_dir, plugin_source):
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "deploy-manifest.json").write_text(
        json.dumps({"plugin_source": plugin_source})
    )


def test_empty_plugin_source_falls_back_to_installed_plugin(tmp_path, monkeypatch):
    """Empty manifest `plugin_source` -> resolver uses the installed-plugin dir."""
    install_dir = tmp_path / ".agent-worktrees"
    _write_manifest(install_dir, "")  # marketplace-install leaves this empty

    plugin_dir = tmp_path / "installed-plugins" / "copilot-extensions" / "agent-worktrees"
    (plugin_dir / "scripts").mkdir(parents=True)
    installed_script = plugin_dir / "scripts" / "install.ps1"
    installed_script.write_text("# stub")

    monkeypatch.setattr(m.cfg, "install_dir", lambda: install_dir)
    monkeypatch.setattr(m, "discover_plugin_dir", lambda: (plugin_dir, "marketplace"))

    resolved = m._resolve_terminal_install_script()
    assert resolved == installed_script


def test_plugin_source_preferred_when_present(tmp_path, monkeypatch):
    """A valid `plugin_source` wins over the installed-plugin fallback."""
    source_dir = tmp_path / "src-plugin"
    (source_dir / "scripts").mkdir(parents=True)
    source_script = source_dir / "scripts" / "install.ps1"
    source_script.write_text("# source stub")

    install_dir = tmp_path / ".agent-worktrees"
    _write_manifest(install_dir, str(source_dir))

    plugin_dir = tmp_path / "installed-plugins" / "agent-worktrees"
    (plugin_dir / "scripts").mkdir(parents=True)
    (plugin_dir / "scripts" / "install.ps1").write_text("# installed stub")

    monkeypatch.setattr(m.cfg, "install_dir", lambda: install_dir)
    monkeypatch.setattr(m, "discover_plugin_dir", lambda: (plugin_dir, "marketplace"))

    resolved = m._resolve_terminal_install_script()
    assert resolved == source_script


def test_refresh_warns_when_no_script_found(tmp_path, monkeypatch):
    """Nothing resolvable -> warn, do NOT silently return (and do NOT spawn)."""
    install_dir = tmp_path / ".agent-worktrees"  # no manifest at all

    monkeypatch.setattr(m.cfg, "install_dir", lambda: install_dir)
    monkeypatch.setattr(m, "discover_plugin_dir", lambda: (None, ""))
    # Neutralize the module-root fallback so nothing resolves.
    monkeypatch.setattr(m, "_resolve_terminal_install_script", lambda: None)

    warnings: list[str] = []
    monkeypatch.setattr(m.output, "warn", lambda msg: warnings.append(msg))

    def _fail_run(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("subprocess.run must not run without a script")

    monkeypatch.setattr(m.subprocess, "run", _fail_run)

    assert m._refresh_terminal_profiles() is False
    assert warnings, "a missing installer must surface a warning, not a silent no-op"
    assert "Windows Terminal profiles" in warnings[0]


class _FakeProc:
    def __init__(self, returncode):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def _stub_script(tmp_path, monkeypatch):
    """Point the resolver at a fake install.ps1 so the refresh reaches subprocess."""
    script = tmp_path / "scripts" / "install.ps1"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# stub")
    monkeypatch.setattr(m, "_resolve_terminal_install_script", lambda: script)
    return script


def test_refresh_uses_narrow_action_and_passes_project(tmp_path, monkeypatch):
    """dotfiles#563: the mirror runs the fast `refresh-profiles` action (not the
    full `update`) and passes the active project explicitly; rc 0 -> True."""
    script = _stub_script(tmp_path, monkeypatch)
    monkeypatch.setattr(m.cfg, "project_name", lambda: "dotfiles")

    captured = {}

    def _run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["timeout"] = kwargs.get("timeout")
        return _FakeProc(0)

    monkeypatch.setattr(m.subprocess, "run", _run)
    monkeypatch.setattr(m.output, "ok", lambda *_a, **_k: None)

    assert m._refresh_terminal_profiles() is True
    cmd = captured["cmd"]
    assert str(script) in cmd
    assert "refresh-profiles" in cmd
    assert "update" not in cmd  # must NOT run the heavyweight update path
    assert cmd[cmd.index("-ProjectName") + 1] == "dotfiles"
    # Generous timeout, well above the ~60s the full update takes and the old
    # 30s cap that killed the mirror mid-pip.
    assert captured["timeout"] and captured["timeout"] >= 120


def test_refresh_returns_false_on_nonzero_exit(tmp_path, monkeypatch):
    """A non-zero installer exit -> warn + return False (no misreported success)."""
    _stub_script(tmp_path, monkeypatch)
    monkeypatch.setattr(m.cfg, "project_name", lambda: "dotfiles")
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: _FakeProc(1))

    warnings: list[str] = []
    monkeypatch.setattr(m.output, "warn", lambda msg: warnings.append(msg))

    assert m._refresh_terminal_profiles() is False
    assert warnings and "Windows Terminal profiles" in warnings[0]


def test_refresh_returns_false_on_timeout(tmp_path, monkeypatch):
    """A subprocess timeout is caught and surfaced as a failed mirror, not True."""
    _stub_script(tmp_path, monkeypatch)
    monkeypatch.setattr(m.cfg, "project_name", lambda: "dotfiles")

    def _timeout(*a, **k):
        raise m.subprocess.TimeoutExpired(cmd="pwsh", timeout=180)

    monkeypatch.setattr(m.subprocess, "run", _timeout)
    warnings: list[str] = []
    monkeypatch.setattr(m.output, "warn", lambda msg: warnings.append(msg))

    assert m._refresh_terminal_profiles() is False
    assert warnings and "Windows Terminal profiles" in warnings[0]
