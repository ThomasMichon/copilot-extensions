from __future__ import annotations

import importlib.util
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
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
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
    # A plugin with no bootstrap-check.sh at all (e.g. agent-index's no-op
    # stub lives elsewhere, but the opt-in criterion itself is "has the
    # hook"; a plugin missing it entirely must never be touched).
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


def test_no_reconcile_stub_excluded_from_call_site_check(fake_repo, capsys):
    _mk_adopter(fake_repo, "agent-index", wired=False)
    sync_bootstrap_killswitch.main([])
    capsys.readouterr()
    rc = sync_bootstrap_killswitch.main(["--check"])
    out = capsys.readouterr()
    assert rc == 0
    assert "All 0 wired adopters call their guard." in out.out


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
