from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "tools" / "sync-bootstrap-killswitch.py"

_SPEC = importlib.util.spec_from_file_location("sync_bootstrap_killswitch", MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
sync_bootstrap_killswitch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sync_bootstrap_killswitch)


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    canonical_dir = tmp_path / "libs" / "bootstrap-killswitch"
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "bootstrap-killswitch-guard.sh").write_text("canonical sh\n", encoding="utf-8")
    (canonical_dir / "bootstrap-killswitch-guard.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (canonical_dir / "pretool_killswitch.py").write_text("canonical pretool\n", encoding="utf-8")
    for entry, marker in (
        ("plugins/agent-worktrees/scripts/hook_client.py", "switch.is_active()"),
        ("plugins/copilot-extensions-harness/scripts/copilot-mention-guard.py", "switch.is_active()"),
        ("plugins/agent-machines/src/agent_machines/__main__.py", "pretool_killswitch.register_parser(sub)"),
    ):
        target = tmp_path / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(marker, encoding="utf-8")
    plugins_dir = tmp_path / "plugins"
    monkeypatch.setattr(sync_bootstrap_killswitch, "REPO", tmp_path)
    monkeypatch.setattr(sync_bootstrap_killswitch, "CANONICAL_DIR", canonical_dir)
    monkeypatch.setattr(sync_bootstrap_killswitch, "PLUGINS_DIR", plugins_dir)
    return tmp_path


def _mk_adopter(repo: Path, name: str, *, wired: bool = True) -> Path:
    scripts = repo / "plugins" / name / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    if wired:
        sh_body = '#!/usr/bin/env bash\nbash "$_bks_guard" check\nexit 0\n'
        ps1_body = '& $_bksGuard check\nexit 0\n'
    else:
        sh_body = "#!/usr/bin/env bash\nexit 0\n"
        ps1_body = "exit 0\n"
    (scripts / "bootstrap-check.sh").write_text(sh_body, encoding="utf-8")
    (scripts / "bootstrap-check.ps1").write_text(ps1_body, encoding="utf-8")
    return scripts


def _mk_non_adopter(repo: Path, name: str) -> Path:
    # A plugin missing the hook entirely must never be touched.
    plugin_dir = repo / "plugins" / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    return plugin_dir


def test_adopter_detection_requires_bootstrap_check(fake_repo):
    _mk_adopter(fake_repo, "has-hook")
    _mk_non_adopter(fake_repo, "no-hook")
    plugins = sync_bootstrap_killswitch._adopter_plugins()
    names = sorted(p.name for p in plugins)
    assert names == ["has-hook"]


def test_sync_writes_both_guard_files_for_each_adopter(fake_repo):
    scripts = _mk_adopter(fake_repo, "agent-example")
    rc = sync_bootstrap_killswitch.main([])
    assert rc == 0
    assert (scripts / "bootstrap-killswitch-guard.sh").read_text() == "canonical sh\n"
    assert (scripts / "bootstrap-killswitch-guard.ps1").read_text() == "canonical ps1\n"


@pytest.mark.skipif(os.name == "nt", reason="Windows does not expose POSIX executable mode bits")
def test_sh_guard_is_made_executable(fake_repo):
    scripts = _mk_adopter(fake_repo, "agent-example")
    sync_bootstrap_killswitch.main([])
    mode = (scripts / "bootstrap-killswitch-guard.sh").stat().st_mode
    assert mode & 0o111


def test_check_passes_when_already_in_sync(fake_repo, capsys):
    _mk_adopter(fake_repo, "agent-example")
    assert sync_bootstrap_killswitch.main([]) == 0
    capsys.readouterr()
    rc = sync_bootstrap_killswitch.main(["--check"])
    out = capsys.readouterr()
    assert rc == 0
    assert "in sync" in out.out


def test_check_fails_when_drifted(fake_repo, capsys):
    scripts = _mk_adopter(fake_repo, "agent-example")
    sync_bootstrap_killswitch.main([])
    (scripts / "bootstrap-killswitch-guard.sh").write_text("drifted!\n", encoding="utf-8")

    rc = sync_bootstrap_killswitch.main(["--check"])
    out = capsys.readouterr()
    assert rc == 1
    assert "drifted" in out.err


def test_check_fails_when_missing(fake_repo, capsys):
    _mk_adopter(fake_repo, "agent-example")

    rc = sync_bootstrap_killswitch.main(["--check"])
    out = capsys.readouterr()
    assert rc == 1
    assert "missing" in out.err


def test_check_fails_when_call_site_not_wired(fake_repo, capsys):
    _mk_adopter(fake_repo, "agent-example", wired=False)
    sync_bootstrap_killswitch.main([])
    capsys.readouterr()
    rc = sync_bootstrap_killswitch.main(["--check"])
    out = capsys.readouterr()
    assert rc == 1
    assert "does not call its vendored guard" in out.err
    assert "agent-example" in out.err


def test_check_passes_when_call_site_wired(fake_repo, capsys):
    _mk_adopter(fake_repo, "agent-example", wired=True)
    sync_bootstrap_killswitch.main([])
    capsys.readouterr()
    rc = sync_bootstrap_killswitch.main(["--check"])
    out = capsys.readouterr()
    assert rc == 0
    assert "All 1 wired adopters call their guard." in out.out


def test_index_metadata_stamp_requires_guard_call_site(fake_repo, capsys):
    _mk_adopter(fake_repo, "agent-index", wired=False)
    sync_bootstrap_killswitch.main([])
    capsys.readouterr()
    rc = sync_bootstrap_killswitch.main(["--check"])
    out = capsys.readouterr()
    assert rc == 1
    assert "agent-index/scripts/bootstrap-check.sh" in out.err
    assert "agent-index/scripts/bootstrap-check.ps1" in out.err
    assert "does not call its vendored guard" in out.err


def test_write_mode_warns_but_does_not_fail_on_call_site_gap(fake_repo, capsys):
    _mk_adopter(fake_repo, "agent-example", wired=False)
    rc = sync_bootstrap_killswitch.main([])
    out = capsys.readouterr()
    assert rc == 0
    assert "does not call its vendored guard" in out.err


def test_main_fails_when_canonical_missing(tmp_path, monkeypatch):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    monkeypatch.setattr(sync_bootstrap_killswitch, "REPO", tmp_path)
    monkeypatch.setattr(sync_bootstrap_killswitch, "CANONICAL_DIR", tmp_path / "libs" / "bootstrap-killswitch")
    monkeypatch.setattr(sync_bootstrap_killswitch, "PLUGINS_DIR", plugins_dir)
    assert sync_bootstrap_killswitch.main([]) == 1


@pytest.mark.parametrize("target", [
    "plugins/agent-worktrees/scripts/pretool_killswitch.py",
    "plugins/copilot-extensions-harness/scripts/pretool_killswitch.py",
    "plugins/agent-machines/src/agent_machines/pretool_killswitch.py",
])
def test_pretool_helper_copies_are_checked_for_drift(fake_repo, capsys, target):
    _mk_adopter(fake_repo, "agent-example")
    assert sync_bootstrap_killswitch.main([]) == 0
    path = fake_repo / target
    assert path.read_text() == "canonical pretool\n"
    path.write_text("drift", encoding="utf-8")
    assert sync_bootstrap_killswitch.main(["--check"]) == 1
    assert target in capsys.readouterr().err


@pytest.mark.parametrize("entry", [
    "plugins/agent-worktrees/scripts/hook_client.py",
    "plugins/copilot-extensions-harness/scripts/copilot-mention-guard.py",
    "plugins/agent-machines/src/agent_machines/__main__.py",
])
def test_pretool_control_must_be_wired_not_merely_copied(fake_repo, capsys, entry):
    _mk_adopter(fake_repo, "agent-example")
    assert sync_bootstrap_killswitch.main([]) == 0
    (fake_repo / entry).write_text("no call site", encoding="utf-8")
    assert sync_bootstrap_killswitch.main(["--check"]) == 1
    assert entry in capsys.readouterr().err
