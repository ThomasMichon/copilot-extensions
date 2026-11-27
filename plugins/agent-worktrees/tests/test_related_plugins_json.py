"""``related list --json`` publishes each grafted entry's plugin selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import config, related, related_cli as cli, repos
from agent_worktrees.related import RelatedConfig, RelatedEntry


def test_related_list_json_carries_layered_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    """A plugin-contributed layer's plugins survive an empty base ``related: {}``."""
    contributed = tmp_path / "contributed"
    base = tmp_path / "base"
    contributed.mkdir()
    base.mkdir()
    selected = {"source": "dev-locally@synthetic", "enable": True}
    related.write_related(contributed, RelatedConfig(related={
        "product": RelatedEntry(name="product", plugins=[selected]),
        "bare": RelatedEntry(name="bare"),
    }))
    (base / ".agent-worktrees").mkdir()
    (base / ".agent-worktrees" / "related.yaml").write_text("related: {}\n", encoding="utf-8")

    monkeypatch.setattr(config, "active_project", lambda: "consumer")
    monkeypatch.setattr(
        cli, "_related_config_source_anchors", lambda _anchor: [str(contributed), str(base)],
    )
    monkeypatch.setattr(repos, "read_registry", lambda: repos.ReposRegistry(repos={}))
    assert cli.cmd_related_dispatch(["list", "--json", "--repo", str(base)]) == 0
    rows = {row["name"]: row for row in json.loads(capfd.readouterr().out)["related"]}
    assert rows["product"]["plugins"] == [selected]
    assert rows["bare"]["plugins"] == []
