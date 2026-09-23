"""Tests for tools/preview_release.py."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import accumulate_bumps as acc
import changefile
import preview_release


def _plugin(root: Path, name: str, version: str) -> Path:
    d = root / "plugins" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(
        json.dumps({"name": name, "version": version}, indent=2) + "\n", encoding="utf-8"
    )
    (d / "src").mkdir()
    (d / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    return d


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    monkeypatch.setattr(preview_release, "REPO", root)
    monkeypatch.setattr(preview_release, "PLUGINS_DIR", root / "plugins")
    monkeypatch.setattr(acc, "PLUGINS_DIR", root / "plugins")
    monkeypatch.setattr(changefile, "CHANGEFILES_DIR", root / ".changefiles")
    return root


def test_build_without_pending_changefile_keeps_current_version(isolated: Path):
    _plugin(isolated, "agent-worktrees", "1.5.5-dev253")
    workdir = isolated / "work"
    dest = preview_release.build("agent-worktrees", workdir)

    assert (dest / "src" / "main.py").exists()
    manifest = json.loads((dest / "PREVIEW.json").read_text())
    assert manifest["plugin"] == "agent-worktrees"
    assert manifest["current_version"] == "1.5.5-dev253"
    assert manifest["hypothetical_version"] == "1.5.5-dev253"
    assert manifest["has_pending_changefiles"] is False


def test_build_with_pending_changefile_reports_hypothetical_version(isolated: Path):
    _plugin(isolated, "agent-worktrees", "1.5.5-dev253")
    changefile.write_changefile([{"plugin": "agent-worktrees", "type": "patch"}], "fix")

    dest = preview_release.build("agent-worktrees", isolated / "work")
    manifest = json.loads((dest / "PREVIEW.json").read_text())
    assert manifest["hypothetical_version"] == "1.5.6-dev1"
    assert manifest["has_pending_changefiles"] is True
    # build() must never apply the bump for real -- only report it.
    pj = json.loads((isolated / "plugins/agent-worktrees/plugin.json").read_text())
    assert pj["version"] == "1.5.5-dev253"
    assert changefile.read_changefiles() != []  # changefile is untouched, not consumed


def test_build_unknown_plugin_raises(isolated: Path):
    with pytest.raises(FileNotFoundError):
        preview_release.build("does-not-exist", isolated / "work")


def test_build_rebuilds_cleanly_when_called_twice(isolated: Path):
    _plugin(isolated, "agent-worktrees", "1.0.0")
    workdir = isolated / "work"
    preview_release.build("agent-worktrees", workdir)
    dest = preview_release.build("agent-worktrees", workdir)  # idempotent re-run
    assert (dest / "src" / "main.py").read_text() == "x = 1\n"


def test_main_smoke(isolated: Path, capsys):
    _plugin(isolated, "agent-worktrees", "1.0.0")
    code = preview_release.main(["agent-worktrees", "--workdir", str(isolated / "work")])
    assert code == 0
    assert "Preview built at" in capsys.readouterr().out


def test_main_missing_plugin_reports_error(isolated: Path, capsys):
    code = preview_release.main(["ghost", "--workdir", str(isolated / "work")])
    assert code == 1
    assert "no such plugin" in capsys.readouterr().err


class _FakeSyncVendoredLibs:
    """Stands in for the real (hyphenated, importlib-loaded) module so the
    materialize-into-preview wiring can be tested without touching any real
    canonical libs/<lib> on disk."""

    def __init__(self, canonical_root: Path):
        self.LIBS_DIR = canonical_root

    def _materialize_blocked(self, lib, canonical, first_copy):
        return None

    def _copy_src(self, src_lib: Path, dst_lib: Path) -> None:
        dst = dst_lib / "src"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src_lib / "src", dst)

    def _sync_version(self, src_lib: Path, dst_lib: Path) -> None:
        pass


def test_materialize_into_preview_writes_only_into_dest_never_real_repo(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
):
    plugin_dir = _plugin(isolated, "agent-bridge", "1.0.0")
    real_copy = plugin_dir / "libs" / "shared-lib" / "src"
    real_copy.mkdir(parents=True)
    (real_copy / "__init__.py").write_text("stale = True\n", encoding="utf-8")

    canonical_root = isolated / "canonical-libs"
    canonical_lib = canonical_root / "shared-lib"
    (canonical_lib / "src").mkdir(parents=True)
    (canonical_lib / "src" / "__init__.py").write_text("fresh = True\n", encoding="utf-8")

    monkeypatch.setattr(
        preview_release, "_load_sync_vendored_libs",
        lambda: _FakeSyncVendoredLibs(canonical_root),
    )

    dest = preview_release.build("agent-bridge", isolated / "work")

    # The preview copy picked up the "fresh" canonical content...
    assert (dest / "libs" / "shared-lib" / "src" / "__init__.py").read_text() == "fresh = True\n"
    # ...but the real plugin directory in this checkout was never touched.
    assert (real_copy / "__init__.py").read_text() == "stale = True\n"
