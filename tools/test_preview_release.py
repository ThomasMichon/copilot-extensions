"""Tests for tools/preview_release.py."""
from __future__ import annotations

import json
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


def test_build_refuses_a_retired_directory_pointer(isolated: Path):
    plugin = _plugin(isolated, "agent-worktrees", "1.0.0")
    pointer_dir = plugin / "libs" / "shared-lib"
    pointer_dir.mkdir(parents=True)
    (pointer_dir / "VENDOR_POINTER.json").write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.vendor-pointer",
                "version": 1,
                "source": "libs/shared-lib",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="retired directory-pointer kind still present"):
        preview_release.build("agent-worktrees", isolated / "work")


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

    def materialize_uv_editable_ref_into(
        self, *, source_consumer_dir: Path, dest_consumer_dir: Path, canonical_root: Path
    ) -> list[str]:
        # No uv-editable references exist in this test's fixture plugin --
        # a real (non-fake) materialize_main would simply find none and
        # return an empty log, which this stand-in mirrors.
        assert canonical_root == self._canonical_root
        return []

    def materialize_installer_engine_ref_into(
        self, *, source_consumer_dir: Path, dest_consumer_dir: Path, canonical_root: Path
    ) -> list[str]:
        assert canonical_root == self._canonical_root
        return []

    def materialize_launch_wrapper_assets_into(
        self, *, source_consumer_dir: Path, dest_consumer_dir: Path, canonical_root: Path
    ) -> list[str]:
        assert canonical_root == self._canonical_root
        return []


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



def test_build_materializes_a_uv_editable_reference_into_the_preview(isolated: Path):
    # Real (not mocked) end-to-end: a plugin whose pyproject.toml carries a
    # `uv`-editable canonical-reference entry gets canonical's complete lib
    # tree copied into the PREVIEW's own libs/<lib>/, and the preview's own
    # pyproject.toml rewritten to the local non-editable form -- the real
    # plugin's own pyproject.toml (and the real repo) must never be touched.
    plugin_dir = _plugin(isolated, "agent-bridge", "1.0.0")
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-bridge"\nversion = "1.0.0"\n'
        'dependencies = ["agent-zdd"]\n'
        "\n"
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    (isolated / "libs/zdd/src/zdd").mkdir(parents=True)
    (isolated / "libs/zdd/src/zdd/__init__.py").write_text("real = True\n", encoding="utf-8")
    (isolated / "libs/zdd/pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0-dev5"\n', encoding="utf-8"
    )

    dest = preview_release.build("agent-bridge", isolated / "work")

    assert (dest / "libs/zdd/src/zdd/__init__.py").read_text() == "real = True\n"
    dest_pp = (dest / "pyproject.toml").read_text()
    assert 'agent-zdd = { path = "libs/zdd" }' in dest_pp
    assert "editable" not in dest_pp
    # The real plugin's own pyproject.toml is never mutated.
    real_pp = (plugin_dir / "pyproject.toml").read_text()
    assert "editable = true" in real_pp
    assert not (plugin_dir / "libs/zdd").exists()

    manifest = json.loads((dest / "PREVIEW.json").read_text())
    assert any(
        line.startswith("OK") for line in manifest["vendored_uv_editable_refs_materialize_log"]
    )


def test_build_materializes_a_canonical_installer_engine_reference_into_the_preview(
    isolated: Path,
):
    plugin_dir = _plugin(isolated, "agent-pull-requests", "1.0.0")
    scripts = plugin_dir / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "install.sh").write_text(
        '#!/usr/bin/env bash\n. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n",
        encoding="utf-8",
    )
    engine = isolated / "libs" / "installer-engine"
    engine.mkdir(parents=True)
    (engine / "installer-engine.sh").write_text("# canonical sh\n", encoding="utf-8")
    (engine / "installer-engine.ps1").write_text("# canonical ps1\n", encoding="utf-8")

    dest = preview_release.build("agent-pull-requests", isolated / "work")

    assert (dest / "scripts" / "installer-engine.sh").read_text() == "# canonical sh\n"
    assert (dest / "scripts" / "installer-engine.ps1").read_text() == "# canonical ps1\n"
    assert (dest / "scripts" / "install.sh").read_text(encoding="utf-8").splitlines() == [
        "#!/usr/bin/env bash",
        '. "$SCRIPT_DIR/installer-engine.sh"',
    ]
    assert (dest / "scripts" / "install.ps1").read_text(encoding="utf-8").splitlines() == [
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1')"
    ]
    manifest = json.loads((dest / "PREVIEW.json").read_text())
    assert any(
        line.startswith("OK")
        for line in manifest["vendored_installer_engine_materialize_log"]
    )


def test_build_preview_reports_missing_canonical_installer_engine(isolated: Path):
    plugin_dir = _plugin(isolated, "agent-pull-requests", "1.0.0")
    scripts = plugin_dir / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "install.sh").write_text(
        '#!/usr/bin/env bash\n. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )

    dest = preview_release.build("agent-pull-requests", isolated / "work")

    assert not (dest / "scripts" / "installer-engine.sh").exists()
    manifest = json.loads((dest / "PREVIEW.json").read_text())
    assert any(
        "canonical source missing" in line
        for line in manifest["vendored_installer_engine_materialize_log"]
    )


def test_build_preview_reports_escaping_installer_engine_reference(isolated: Path):
    plugin_dir = _plugin(isolated, "agent-pull-requests", "1.0.0")
    scripts = plugin_dir / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "install.sh").write_text(
        '#!/usr/bin/env bash\n. "$SCRIPT_DIR/../../../outside/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (isolated / "outside").mkdir(parents=True)
    (isolated / "outside" / "installer-engine.sh").write_text("# nope\n", encoding="utf-8")
    engine = isolated / "libs" / "installer-engine"
    engine.mkdir(parents=True)
    (engine / "installer-engine.sh").write_text("# canonical sh\n", encoding="utf-8")

    dest = preview_release.build("agent-pull-requests", isolated / "work")

    assert not (dest / "scripts" / "installer-engine.sh").exists()
    manifest = json.loads((dest / "PREVIEW.json").read_text())
    assert any(
        "is not libs/installer-engine/installer-engine.sh" in line
        for line in manifest["vendored_installer_engine_materialize_log"]
    )


def test_build_materializes_packaged_launch_wrapper_assets_into_the_preview(
    isolated: Path,
):
    plugin_dir = _plugin(isolated, "agent-worktrees", "1.0.0")
    (plugin_dir / "launch-wrapper-assets.json").write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.launch-wrapper-assets",
                "version": 1,
                "canonicalDir": "worktree-manager/bin",
                "files": [
                    "launch-session.sh",
                    "pane-wrapper.sh",
                    "session-options.sh",
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    source_dir = isolated / "worktree-manager" / "bin"
    source_dir.mkdir(parents=True)
    for name, content in {
        "launch-session.sh": "#!/usr/bin/env bash\n",
        "pane-wrapper.sh": "#!/usr/bin/env bash\n",
        "session-options.sh": "session opts\n",
    }.items():
        (source_dir / name).write_text(content, encoding="utf-8")

    dest = preview_release.build("agent-worktrees", isolated / "work")

    assert (dest / "bin" / "launch-session.sh").read_text() == "#!/usr/bin/env bash\n"
    assert (dest / "bin" / "pane-wrapper.sh").read_text() == "#!/usr/bin/env bash\n"
    assert (dest / "bin" / "session-options.sh").read_text() == "session opts\n"
    manifest = json.loads((dest / "PREVIEW.json").read_text())
    assert any(
        line.startswith("OK")
        for line in manifest["vendored_launch_wrapper_assets_materialize_log"]
    )
