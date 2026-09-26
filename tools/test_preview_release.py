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
        self.POINTER_NAME = "VENDOR_POINTER.json"
        self.blocked_reason: str | None = None

    def _materialize_blocked(self, lib, canonical, first_copy):
        return self.blocked_reason

    def _is_pointer_copy(self, path: Path) -> bool:
        return (path / self.POINTER_NAME).is_file()

    def _copy_src(self, src_lib: Path, dst_lib: Path) -> None:
        dst = dst_lib / "src"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src_lib / "src", dst)

    def _copy_tests(self, src_lib: Path, dst_lib: Path) -> None:
        src, dst = src_lib / "tests", dst_lib / "tests"
        if dst.exists():
            shutil.rmtree(dst)
        if src.is_dir():
            shutil.copytree(src, dst)

    def _sync_version(self, src_lib: Path, dst_lib: Path) -> None:
        pass

    def _find_symlink(self, tree: Path) -> str | None:
        if tree.is_symlink():
            return "."
        if not tree.is_dir():
            return None
        for p in sorted(tree.rglob("*")):
            if p.is_symlink():
                return str(p.relative_to(tree))
        return None

    def _find_symlinked_ancestor(self, path: Path, root: Path) -> Path | None:
        root_r = root.resolve()
        current = path
        while True:
            if current.is_symlink():
                return current
            if current.resolve() == root_r or current.parent == current:
                return None
            current = current.parent


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


def test_materialize_into_preview_bypasses_drift_check_for_a_pointer_copy(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
):
    # A DRY vendor-pointer copy (bare or src-passthrough) is never the
    # verified-agreeing "truth" _materialize_blocked() compares against --
    # previously this unconditionally ran that check and refused a real,
    # already-adopted src-passthrough copy (agent-worktrees/libs/
    # lazy-cli-dispatch) whenever canonical's declared version wasn't
    # already strictly ahead of the stub's own declared version (confirmed
    # against the real repo before this fix: "same version but content
    # differs -- bump one side").
    plugin_dir = _plugin(isolated, "agent-worktrees", "1.0.0")
    pointer_copy = plugin_dir / "libs" / "shared-lib"
    (pointer_copy / "src").mkdir(parents=True)
    (pointer_copy / "src" / "__init__.py").write_text("stub = True\n", encoding="utf-8")
    (pointer_copy / "VENDOR_POINTER.json").write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": "libs/shared-lib", "kind": "src-passthrough"}) + "\n",
        encoding="utf-8",
    )

    canonical_root = isolated / "canonical-libs"
    canonical_lib = canonical_root / "shared-lib"
    (canonical_lib / "src").mkdir(parents=True)
    (canonical_lib / "src" / "__init__.py").write_text("fresh = True\n", encoding="utf-8")

    fake = _FakeSyncVendoredLibs(canonical_root)
    fake.blocked_reason = "shared-lib: same version but content differs -- bump one side"
    monkeypatch.setattr(preview_release, "_load_sync_vendored_libs", lambda: fake)

    dest = preview_release.build("agent-worktrees", isolated / "work")

    lib_dest = dest / "libs" / "shared-lib"
    assert (lib_dest / "src" / "__init__.py").read_text() == "fresh = True\n"
    # The now-superseded pointer marker must not survive materialization --
    # the preview must be a fully real, no-pointer-remaining copy.
    assert not (lib_dest / "VENDOR_POINTER.json").exists()


def test_materialize_into_preview_refreshes_a_pointer_copys_stale_tests(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
):
    # A pointer copy vendors tests/ from canonical too; the preview build
    # must refresh it the same way it refreshes src/, matching
    # materialize_main.py's own promotion-time expansion.
    plugin_dir = _plugin(isolated, "agent-worktrees", "1.0.0")
    pointer_copy = plugin_dir / "libs" / "shared-lib"
    (pointer_copy / "src").mkdir(parents=True)
    (pointer_copy / "src" / "__init__.py").write_text("stub = True\n", encoding="utf-8")
    (pointer_copy / "tests").mkdir(parents=True)
    (pointer_copy / "tests" / "test_thing.py").write_text(
        "def test_it():\n    assert False  # stale\n", encoding="utf-8"
    )
    (pointer_copy / "VENDOR_POINTER.json").write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": "libs/shared-lib", "kind": "src-passthrough"}) + "\n",
        encoding="utf-8",
    )

    canonical_root = isolated / "canonical-libs"
    canonical_lib = canonical_root / "shared-lib"
    (canonical_lib / "src").mkdir(parents=True)
    (canonical_lib / "src" / "__init__.py").write_text("fresh = True\n", encoding="utf-8")
    (canonical_lib / "tests").mkdir(parents=True)
    (canonical_lib / "tests" / "test_thing.py").write_text(
        "def test_it():\n    pass\n", encoding="utf-8"
    )

    fake = _FakeSyncVendoredLibs(canonical_root)
    monkeypatch.setattr(preview_release, "_load_sync_vendored_libs", lambda: fake)

    dest = preview_release.build("agent-worktrees", isolated / "work")

    refreshed = dest / "libs" / "shared-lib" / "tests" / "test_thing.py"
    assert refreshed.read_text() == "def test_it():\n    pass\n"


def test_materialize_into_preview_still_respects_drift_check_for_a_real_copy(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
):
    # A real (non-pointer) copy must still be protected by the drift check
    # -- only a pointer copy bypasses it.
    plugin_dir = _plugin(isolated, "agent-bridge", "1.0.0")
    real_copy = plugin_dir / "libs" / "shared-lib" / "src"
    real_copy.mkdir(parents=True)
    (real_copy / "__init__.py").write_text("newer-real-content = True\n", encoding="utf-8")

    canonical_root = isolated / "canonical-libs"
    canonical_lib = canonical_root / "shared-lib"
    (canonical_lib / "src").mkdir(parents=True)
    (canonical_lib / "src" / "__init__.py").write_text("stale = True\n", encoding="utf-8")

    fake = _FakeSyncVendoredLibs(canonical_root)
    fake.blocked_reason = "shared-lib: copies are newer than canonical -- run --restore-canonical first"
    monkeypatch.setattr(preview_release, "_load_sync_vendored_libs", lambda: fake)

    dest = preview_release.build("agent-bridge", isolated / "work")

    # Blocked -- the real copy's own (newer) content must be left untouched.
    assert (dest / "libs" / "shared-lib" / "src" / "__init__.py").read_text() == (
        "newer-real-content = True\n"
    )


def test_materialize_into_preview_refuses_a_symlinked_canonical_lib_root(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
):
    # canonical.is_dir() follows a `libs/<lib>` symlink to an external
    # tree, and a scan starting at canonical/src would only ever see that
    # external target's own contents -- the canonical lib ROOT itself must
    # be rejected as a symlink, matching the safeguards already added to
    # --pointerize and --materialize.
    plugin_dir = _plugin(isolated, "agent-worktrees", "1.0.0")
    pointer_copy = plugin_dir / "libs" / "shared-lib"
    (pointer_copy / "src").mkdir(parents=True)
    (pointer_copy / "src" / "__init__.py").write_text("original stub\n", encoding="utf-8")
    (pointer_copy / "VENDOR_POINTER.json").write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": "libs/shared-lib", "kind": "src-passthrough"}) + "\n",
        encoding="utf-8",
    )

    external = isolated / "outside-repo-lib-preview"
    (external / "src").mkdir(parents=True)
    (external / "src" / "__init__.py").write_text("smuggled = True\n", encoding="utf-8")

    canonical_root = isolated / "canonical-libs"
    canonical_root.mkdir(parents=True)
    (canonical_root / "shared-lib").symlink_to(external, target_is_directory=True)

    fake = _FakeSyncVendoredLibs(canonical_root)
    monkeypatch.setattr(preview_release, "_load_sync_vendored_libs", lambda: fake)

    dest = preview_release.build("agent-worktrees", isolated / "work")

    # Nothing was expanded -- the copy's own stub content is untouched.
    assert (dest / "libs" / "shared-lib" / "src" / "__init__.py").read_text() == (
        "original stub\n"
    )
    assert (dest / "libs" / "shared-lib" / "VENDOR_POINTER.json").exists()


def test_materialize_into_preview_refuses_a_symlinked_destination_ancestor(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
):
    # Round-14 review finding: iterdir()/is_dir() follow a symlinked
    # libs/ or libs/<lib> DESTINATION just as readily as canonical.is_dir()
    # follows a symlinked SOURCE (fixed earlier this round) -- _copy_src(),
    # _copy_tests(), version syncing, and pointer removal could all mutate
    # whatever a symlinked destination points at. Exercises
    # _materialize_into_preview() directly (its only real caller, build(),
    # always populates dest fresh via its own dereferencing copytree, so
    # this destination-side attack shape isn't reachable through build()
    # alone -- but the function itself must still refuse it defensively).
    dest = isolated / "direct-dest"
    victim = isolated / "victim-dir"
    (victim / "src").mkdir(parents=True)
    (victim / "src" / "__init__.py").write_text("do not touch\n", encoding="utf-8")
    (victim / "VENDOR_POINTER.json").write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": "libs/shared-lib", "kind": "src-passthrough"}) + "\n",
        encoding="utf-8",
    )

    (dest / "libs").mkdir(parents=True)
    (dest / "libs" / "shared-lib").symlink_to(victim, target_is_directory=True)

    canonical_root = isolated / "canonical-libs"
    canonical_lib = canonical_root / "shared-lib"
    (canonical_lib / "src").mkdir(parents=True)
    (canonical_lib / "src" / "__init__.py").write_text("smuggled = True\n", encoding="utf-8")

    fake = _FakeSyncVendoredLibs(canonical_root)
    monkeypatch.setattr(preview_release, "_load_sync_vendored_libs", lambda: fake)

    log = preview_release._materialize_into_preview(dest)

    assert any("SKIP" in line and "is a symlink" in line for line in log)
    assert (victim / "src" / "__init__.py").read_text() == "do not touch\n"
    assert (victim / "VENDOR_POINTER.json").exists()
    assert (dest / "libs" / "shared-lib").is_symlink()


def test_materialize_into_preview_refuses_a_dangling_destination_symlink(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
):
    # Round-17 review finding: the candidate-discovery loop only admitted
    # entries where p.is_dir() is true -- a DANGLING (or non-directory-
    # target) symlink at dest/libs/<lib> has is_dir()==False (it follows
    # the link to a target that isn't there), so it was silently SKIPPED
    # by iteration itself, never even reaching the ancestor-symlink check,
    # and would survive untouched in the preview tree despite the
    # surrounding code's claim that preview copies contain only real
    # directories.
    dest = isolated / "direct-dest"
    (dest / "libs").mkdir(parents=True)
    (dest / "libs" / "shared-lib").symlink_to(
        isolated / "does-not-exist", target_is_directory=True
    )

    canonical_root = isolated / "canonical-libs"
    canonical_lib = canonical_root / "shared-lib"
    (canonical_lib / "src").mkdir(parents=True)
    (canonical_lib / "src" / "__init__.py").write_text("smuggled = True\n", encoding="utf-8")

    fake = _FakeSyncVendoredLibs(canonical_root)
    monkeypatch.setattr(preview_release, "_load_sync_vendored_libs", lambda: fake)

    log = preview_release._materialize_into_preview(dest)

    assert any("SKIP" in line and "is a symlink" in line for line in log)
    assert (dest / "libs" / "shared-lib").is_symlink()


def test_materialize_into_preview_refuses_and_preserves_the_pointer_for_a_symlinked_pyproject_toml(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
):
    # Round-17 review finding: _sync_version()'s own guard only silently
    # returns without writing, which isn't enough on its own -- without a
    # caller-side preflight, this loop would continue on to _copy_src()
    # (mutating src/) and unlink the pointer marker as though everything
    # succeeded, leaving a pointer-free preview with an unsafe linked
    # pyproject.toml surviving untouched. Exercises
    # _materialize_into_preview() directly: build()'s own initial
    # (dereferencing) copytree would neutralize a symlinked SOURCE
    # pyproject.toml before this function ever saw it, so this
    # destination-side attack shape isn't reachable through build() alone
    # -- but the function itself must still refuse it defensively.
    dest = isolated / "direct-dest"
    copy_dir = dest / "libs" / "shared-lib"
    (copy_dir / "src").mkdir(parents=True)
    (copy_dir / "src" / "__init__.py").write_text("original stub\n", encoding="utf-8")
    (copy_dir / "VENDOR_POINTER.json").write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": "libs/shared-lib", "kind": "src-passthrough"}) + "\n",
        encoding="utf-8",
    )
    victim = isolated / "outside-victim-preview-pyproject.toml"
    victim.write_text('[project]\nname = "victim"\nversion = "1.2.3"\n', encoding="utf-8")
    (copy_dir / "pyproject.toml").symlink_to(victim)

    canonical_root = isolated / "canonical-libs"
    canonical_lib = canonical_root / "shared-lib"
    (canonical_lib / "src").mkdir(parents=True)
    (canonical_lib / "src" / "__init__.py").write_text("fresh = True\n", encoding="utf-8")

    fake = _FakeSyncVendoredLibs(canonical_root)
    monkeypatch.setattr(preview_release, "_load_sync_vendored_libs", lambda: fake)

    log = preview_release._materialize_into_preview(dest)

    assert any("SKIP" in line and "is a symlink" in line for line in log)
    assert (copy_dir / "src" / "__init__.py").read_text() == "original stub\n"
    assert victim.read_text() == '[project]\nname = "victim"\nversion = "1.2.3"\n'
    assert (copy_dir / "VENDOR_POINTER.json").exists()


def test_materialize_into_preview_refuses_a_stray_symlink_anywhere_in_the_copy(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
):
    # The src/tests/pyproject.toml checks validate the pieces this
    # function itself knows about, but a pointer copy directory can carry
    # other, unrelated entries too (e.g. a stray docs/link) -- without a
    # final blanket scan, _copy_src() runs and the marker is unlinked,
    # leaving that symlink untouched in the preview output. Mirrors
    # materialize_main.py's and cmd_materialize()'s own final blanket scan.
    dest = isolated / "direct-dest"
    copy_dir = dest / "libs" / "shared-lib"
    (copy_dir / "src").mkdir(parents=True)
    (copy_dir / "src" / "__init__.py").write_text("original stub\n", encoding="utf-8")
    (copy_dir / "VENDOR_POINTER.json").write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": "libs/shared-lib", "kind": "src-passthrough"}) + "\n",
        encoding="utf-8",
    )
    outside = isolated / "outside-stray-target-preview"
    outside.mkdir()
    copy_docs = copy_dir / "docs"
    copy_docs.mkdir()
    (copy_docs / "link").symlink_to(outside, target_is_directory=True)

    canonical_root = isolated / "canonical-libs"
    canonical_lib = canonical_root / "shared-lib"
    (canonical_lib / "src").mkdir(parents=True)
    (canonical_lib / "src" / "__init__.py").write_text("fresh = True\n", encoding="utf-8")

    fake = _FakeSyncVendoredLibs(canonical_root)
    monkeypatch.setattr(preview_release, "_load_sync_vendored_libs", lambda: fake)

    log = preview_release._materialize_into_preview(dest)

    assert any("SKIP" in line and "is a symlink" in line for line in log)
    assert (copy_dir / "src" / "__init__.py").read_text() == "original stub\n"
    assert (copy_dir / "VENDOR_POINTER.json").exists()
    assert (copy_docs / "link").is_symlink()


def test_materialize_into_preview_refuses_a_canonical_lib_with_no_src_directory(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
):
    # _find_symlink() returns None for a MISSING src/ too, not just "no
    # symlink found inside it" -- an incomplete canonical lib must be
    # refused here, before _copy_src() removes the preview copy's
    # existing source and copies nothing, leaving a source-less preview
    # with the pointer marker still removed as if expansion had succeeded.
    plugin_dir = _plugin(isolated, "agent-worktrees", "1.0.0")
    pointer_copy = plugin_dir / "libs" / "shared-lib"
    (pointer_copy / "src").mkdir(parents=True)
    (pointer_copy / "src" / "__init__.py").write_text("original stub\n", encoding="utf-8")
    (pointer_copy / "VENDOR_POINTER.json").write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": "libs/shared-lib", "kind": "src-passthrough"}) + "\n",
        encoding="utf-8",
    )

    canonical_root = isolated / "canonical-libs"
    (canonical_root / "shared-lib").mkdir(parents=True)  # no src/ subdirectory

    fake = _FakeSyncVendoredLibs(canonical_root)
    monkeypatch.setattr(preview_release, "_load_sync_vendored_libs", lambda: fake)

    dest = preview_release.build("agent-worktrees", isolated / "work")

    assert (dest / "libs" / "shared-lib" / "src" / "__init__.py").read_text() == (
        "original stub\n"
    )
    assert (dest / "libs" / "shared-lib" / "VENDOR_POINTER.json").exists()


def test_materialize_into_preview_preflights_tests_before_mutating_src(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
):
    # _copy_src() must not run before a canonical tests/ symlink is
    # rejected -- otherwise a rejected tests/ refresh (found only after
    # src/ was already replaced) would leave the preview in a mixed state:
    # fresh src/, stale tests/, pointer marker still present.
    plugin_dir = _plugin(isolated, "agent-worktrees", "1.0.0")
    pointer_copy = plugin_dir / "libs" / "shared-lib"
    (pointer_copy / "src").mkdir(parents=True)
    (pointer_copy / "src" / "__init__.py").write_text("stale stub\n", encoding="utf-8")
    (pointer_copy / "tests").mkdir(parents=True)
    (pointer_copy / "tests" / "test_thing.py").write_text(
        "def test_it():\n    pass\n", encoding="utf-8"
    )
    (pointer_copy / "VENDOR_POINTER.json").write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": "libs/shared-lib", "kind": "src-passthrough"}) + "\n",
        encoding="utf-8",
    )

    canonical_root = isolated / "canonical-libs"
    canonical_lib = canonical_root / "shared-lib"
    (canonical_lib / "src").mkdir(parents=True)
    (canonical_lib / "src" / "__init__.py").write_text("fresh = True\n", encoding="utf-8")
    secret = isolated / "outside-secret"
    secret.mkdir()
    (canonical_lib / "tests").symlink_to(secret, target_is_directory=True)

    fake = _FakeSyncVendoredLibs(canonical_root)
    monkeypatch.setattr(preview_release, "_load_sync_vendored_libs", lambda: fake)

    dest = preview_release.build("agent-worktrees", isolated / "work")

    # src/ must be untouched -- the rejection happened before any mutation.
    assert (dest / "libs" / "shared-lib" / "src" / "__init__.py").read_text() == "stale stub\n"
    assert (dest / "libs" / "shared-lib" / "tests" / "test_thing.py").read_text() == (
        "def test_it():\n    pass\n"
    )
    assert (dest / "libs" / "shared-lib" / "VENDOR_POINTER.json").exists()


class _FakeMaterializeMain:
    """Stands in for the real (importlib-loaded) module so the
    file-pointer-materialize-into-preview wiring can be tested without
    touching any real canonical doc on disk."""

    def __init__(self, canonical_root: Path):
        self._canonical_root = canonical_root
        self.calls: list[Path] = []

    def materialize_file_pointers(self, dest: Path, *, canonical_root: Path) -> list[str]:
        self.calls.append(dest)
        assert canonical_root == self._canonical_root
        pointer = dest / "docs" / "thing.md"
        canonical = canonical_root / "docs/patterns/thing.md"
        if not pointer.exists():
            return []
        pointer.write_text(canonical.read_text(), encoding="utf-8")
        return [f"OK   {pointer} (file pointer) <- docs/patterns/thing.md"]


def test_materialize_file_pointers_into_preview_writes_only_into_dest(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
):
    plugin_dir = _plugin(isolated, "agent-bridge", "1.0.0")
    pointer = plugin_dir / "docs" / "thing.md"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(
        "<!-- VENDOR_POINTER: source=docs/patterns/thing.md kind=file -->\nstub\n",
        encoding="utf-8",
    )
    (isolated / "docs/patterns").mkdir(parents=True)
    (isolated / "docs/patterns/thing.md").write_text("canonical content\n", encoding="utf-8")

    fake = _FakeMaterializeMain(isolated)
    monkeypatch.setattr(preview_release, "_load_materialize_main", lambda: fake)

    dest = preview_release.build("agent-bridge", isolated / "work")

    assert (dest / "docs" / "thing.md").read_text() == "canonical content\n"
    # The real plugin directory's stub was never touched.
    assert pointer.read_text().startswith("<!-- VENDOR_POINTER:")
    manifest = json.loads((dest / "PREVIEW.json").read_text())
    assert any("(file pointer)" in line for line in manifest["vendored_file_pointers_materialize_log"])

