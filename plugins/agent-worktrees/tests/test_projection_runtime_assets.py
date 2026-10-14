"""The promoted standalone payload projects instructions without a sibling."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agent_worktrees import installer, local_cache_refresh, projection_runtime_assets as assets

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.guard


def materialized_payload(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import materialize_launch_wrapper_assets as materializer

    source = ROOT / "plugins" / "agent-worktrees"
    payload = tmp_path / "agent-worktrees"
    payload.mkdir()
    shutil.copyfile(source / "plugin.json", payload / "plugin.json")
    shutil.copyfile(source / "packaged-assets.json", payload / "packaged-assets.json")
    report = materializer.materialize_into(
        source_consumer_dir=source, dest_consumer_dir=payload,
        canonical_root=ROOT, dest_root=tmp_path,
        manifest_name="packaged-assets.json",
    )
    assert report and all(entry.startswith("OK") for entry in report), report
    return payload


def test_packaged_engine_is_complete_and_byte_identical(tmp_path, monkeypatch):
    payload = materialized_payload(tmp_path, monkeypatch)
    manifest = json.loads((payload / "packaged-assets.json").read_text(encoding="utf-8"))
    script = assets.projection_cli_script(payload)
    for name in manifest["files"]:
        assert (script.parent / name).read_bytes() == (ROOT / manifest["canonicalDir"] / name).read_bytes()
    assert not (tmp_path / "customizing-copilot").exists()


def test_missing_packaged_engine_does_not_discover_other_plugins(tmp_path, monkeypatch):
    payload = materialized_payload(tmp_path, monkeypatch)
    assets.projection_cli_script(payload).unlink()
    monkeypatch.setattr(
        "plugin_activation.resolve_active_plugins",
        lambda **kwargs: pytest.fail("packaged engine attempted sibling discovery"),
    )
    with pytest.raises(FileNotFoundError, match="incomplete"):
        assets.projection_cli_script(payload)


@pytest.mark.parametrize("field", ["payloadDir", "canonicalDir"])
def test_packaged_engine_rejects_path_escape(tmp_path, monkeypatch, field):
    payload = materialized_payload(tmp_path, monkeypatch)
    path = payload / "packaged-assets.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest[field] = "../../outside"
    if field == "canonicalDir":
        payload = tmp_path / "repo" / "plugins" / "agent-worktrees"
        (payload.parent.parent / "tools").mkdir(parents=True)
        (payload.parent.parent / "tools" / "materialize_main.py").write_text("", encoding="utf-8")
        payload.mkdir(parents=True)
        path = payload / "packaged-assets.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid projection"):
        assets.projection_cli_script(payload)


@pytest.mark.parametrize("safe_path", [False, True])
def test_standalone_payload_renders_with_no_customizing_plugin(tmp_path, monkeypatch, capsys, safe_path):
    payload = materialized_payload(tmp_path, monkeypatch)
    monkeypatch.setattr(installer, "_payload_root", lambda: payload)
    monkeypatch.setattr(local_cache_refresh, "_resolve_own_agent_worktrees_command", lambda: None)
    home = tmp_path / "home"
    copilot = home / ".copilot"
    plugin = copilot / "installed-plugins" / "example" / "guidance"
    (plugin / "instructions").mkdir(parents=True)
    (plugin / "instructions" / "body.instructions.md").write_text('---\napplyTo: "**"\n---\n\nUseful guidance.\n', encoding="utf-8")
    (plugin / "plugin.json").write_text('{"name":"guidance","version":"1.0.0"}', encoding="utf-8")
    (plugin / "instruction-projections.json").write_text(json.dumps({
        "schema": "copilot-extensions.instruction-projections", "version": 1,
        "projections": [{
            "id": "body", "template": "instructions/body.instructions.md",
            "destination": ".github/instructions/guidance/body.instructions.md",
            "customizationKind": "instructions", "applyTo": "**",
            "legacyMarkers": [],
        }],
    }), encoding="utf-8")
    repo = tmp_path / "repo"
    settings = repo / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"enabledPlugins":{"guidance@example":true}}', encoding="utf-8")
    (copilot / "config.json").write_text(json.dumps({"trustedFolders": [str(repo)]}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PYTHONPATH", str(repo))
    if safe_path:
        monkeypatch.setenv("PYTHONSAFEPATH", "1")
    else:
        monkeypatch.delenv("PYTHONSAFEPATH", raising=False)
    (repo / "instruction_projections.py").write_text(
        'raise AssertionError("renderer imported an untrusted checkout module")\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    result = local_cache_refresh.prepare_for_launch(repo)
    assert result.status == "ready", result.diagnostic
    assert result.changed == 1
    local = repo / ".github" / "instructions" / "guidance" / "body.local.instructions.md"
    assert "Useful guidance." in local.read_text(encoding="utf-8")
    assert not (copilot / "installed-plugins" / "example" / "customizing-copilot").exists()
    assert capsys.readouterr().out == ""
