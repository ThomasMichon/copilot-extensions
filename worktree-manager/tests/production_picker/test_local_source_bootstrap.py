"""Exercise both engine bootstrap producers through real Manager readers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import config as engine_cfg
from worktree_manager.production_picker import context, project_config as cfg
from worktree_manager.production_picker.picker_tui import data_local, data_ssh, roster

from test_local_source_admission import LABELS, _machines, _setup

pytestmark = [
    pytest.mark.portfolio_tier("T1"),
    pytest.mark.effect("filesystem"),
    pytest.mark.contract("worktree_manager.local_source_admission"),
]


@pytest.mark.parametrize("platform", LABELS)
@pytest.mark.parametrize("kind", ["absent", "empty", "unrelated", "unscoped", "matching", "sibling"])
@pytest.mark.parametrize("binding_tier", ["detected", "project", "global", "dropin"])
@pytest.mark.parametrize("entry", ["resident", "cli"])
def test_engine_binding_provenance_reaches_actual_manager_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture,
    platform: str, kind: str, binding_tier: str, entry: str,
) -> None:
    source = Path(engine_cfg.__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(str(source / "libs" / "repository-policy" / "src"))
    from agent_worktrees import context_cli, picker_bootstrap_runtime

    root = _setup(monkeypatch, tmp_path, platform, _machines(kind, platform))
    state = tmp_path / "project"
    configured = None if binding_tier == "detected" else "preferred-host"
    raw = {
        "repo_name": "example",
        "repos": {"example": {"anchor": str(root)}},
    }
    if binding_tier == "project":
        raw["machine"] = configured
    (state / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    global_path = tmp_path / "global.yaml"
    global_path.write_text(
        yaml.safe_dump({"machine": configured} if binding_tier == "global" else {}),
        encoding="utf-8",
    )
    if binding_tier == "dropin":
        dropins = state / "config.d"
        dropins.mkdir()
        (dropins / "binding.yaml").write_text(
            yaml.safe_dump({"machine": configured}), encoding="utf-8",
        )
    monkeypatch.setattr(engine_cfg, "project_dir", lambda project=None: state)
    monkeypatch.setattr(engine_cfg, "install_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(engine_cfg, "global_config_path", lambda: global_path)
    monkeypatch.setattr(engine_cfg, "detect_platform", lambda: platform)
    monkeypatch.setattr(engine_cfg, "_ACTIVE_PROJECT", "example")
    monkeypatch.chdir(root)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.yaml")}
    try:
        if entry == "resident":
            manifest = SimpleNamespace(resident_picker_command=("contained-manager",))
            monkeypatch.setattr(
                picker_bootstrap_runtime.providers, "_discover_control_plane_provider_manifests",
                lambda **kwargs: {"manager": manifest},
            )
            payload = picker_bootstrap_runtime.build({
                "contract_version": 1, "project": "example", "cwd": str(root),
            })["bootstrap"]
        else:
            monkeypatch.setattr(context_cli, "_resolve_active_project", lambda project: (project, root))
            monkeypatch.setattr(context_cli, "_cwd_is_inside_project", lambda anchor: True)
            monkeypatch.setattr(
                context_cli.worktree_identity, "tracked_invocation_record", lambda *args, **kwargs: None,
            )
            assert context_cli.cmd_picker_bootstrap(
                argparse.Namespace(json=True, project_config=True),
            ) == 0
            payload = json.loads(capfd.readouterr().out)
        assert payload["project_config"]["configured_machine"] == configured
        context.bind_project_bootstrap(payload)
        cfg.bind_bootstrap("example", payload["project_config"])
        loaded = cfg.load_config()
        assert loaded.machine_binding_known
        assert loaded.configured_machine == configured
        sources = data_ssh._build_sources()
        local = [source for source in sources if source.local]
        assert len(local) == 1
        source = local[0]
        assert source.argv is None and source.ready
        assert roster.local_host() == source.key
        assert data_local.is_local(*source.key)
        for candidate in sources:
            assert data_local.is_local(*candidate.key) is candidate.local
        assert {path: path.read_bytes() for path in before} == before
    finally:
        cfg.clear_caches(reset_binding=True)
