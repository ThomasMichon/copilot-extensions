"""Every handoff actor needs actual-platform and receiving-namespace proof."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import claim_handoffs, config as cfg, tracking

pytestmark = pytest.mark.guard


@pytest.mark.parametrize("actual,foreign", [("windows", "wsl"), ("linux", "windows")])
@pytest.mark.parametrize("violation", [None, "platform", "namespace"])
def test_same_name_native_handoff_requires_receiving_record_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, actual: str, foreign: str,
    violation: str | None,
) -> None:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    (anchor / "machines.yaml").write_text("machines: {}\n", encoding="utf-8")
    namespace = tmp_path / "trees"
    directory = tmp_path / "project" / "worktrees"
    directory.mkdir(parents=True)
    config = cfg.Config(
        srcroot=str(tmp_path), machine="example-host", platform=actual, repo_name="project",
        repos={"project": cfg.RepoConfig(anchor=str(anchor), worktree_root=str(namespace))},
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: actual)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: directory.parent)
    monkeypatch.setattr(claim_handoffs, "registry_path", lambda: tmp_path / "handoffs.yaml")
    for name in ("source", "consumer"):
        tracking.create_new_record(
            name, f"worktree/{name}", str(namespace / name), "project",
            "example-host", actual, directory,
        )
    source = tracking.load_record(directory / "source.yaml")
    source.resources = [tracking.ResourceClaim(kind="pr", ref="org/project#42")]
    if violation == "platform":
        source.platform = foreign
    elif violation == "namespace":
        source.worktree_path = str(tmp_path / "foreign" / "source")
    tracking.save_record(source, source.yaml_path)
    before = {path: path.read_bytes() for path in directory.glob("*.yaml")}

    def offer() -> claim_handoffs.ClaimBundle:
        return claim_handoffs.offer(
            "example-host/project/source", "example-host/project/consumer",
            ["org/project#42"], machine="example-host",
        )[0]

    if violation:
        with pytest.raises(claim_handoffs.ClaimHandoffError, match="platform or namespace"):
            offer()
        assert before == {path: path.read_bytes() for path in directory.glob("*.yaml")}
        assert not claim_handoffs.registry_path().exists()
    else:
        bundle = offer()
        assert claim_handoffs.accept(
            bundle.bundle_id, actor=bundle.consumer, machine="example-host",
        ).state == "accepted"
        assert tracking.load_record(source.yaml_path).resources == []
        assert tracking.load_record(directory / "consumer.yaml").resources[0].ref == "org/project#42"
