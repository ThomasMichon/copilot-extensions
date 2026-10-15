"""Related availability resolves physical metadata only in its actual context."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent_worktrees import config as cfg, doctor, related, related_cli, repos

pytestmark = pytest.mark.guard


@pytest.mark.parametrize("actual", ["windows", "wsl"])
@pytest.mark.parametrize("current", [
    "Generated-Host", "gENERATED-hOST", "Example Workstation", "eXAMPLE wORKSTATION",
])
@pytest.mark.parametrize("locus", ["local", "machine", "container"])
def test_related_resolve_and_doctor_use_contextual_physical_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd,
    actual: str, current: str, locus: str,
) -> None:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    (anchor / "machines.yaml").write_text(yaml.safe_dump({"machines": {
        "example-host": {
            "execution_platform": "windows", "physical_host": "example-host",
            "hostname": "Generated-Host", "display_name": "Example Workstation",
            "ssh": {"environments": [{"name": "windows", "alias": "native-profile"}]},
        },
        "example-host-wsl": {
            "execution_platform": "wsl", "physical_host": "example-host",
            "hostname": "Generated-Host", "display_name": "Example Workstation",
            "ssh": {"environments": [{"name": "wsl", "alias": "guest-profile"}]},
        },
    }}), encoding="utf-8")
    config = cfg.Config(
        srcroot=str(tmp_path), machine=current, platform=actual, repo_name="example",
        repos={"example": cfg.RepoConfig(anchor=str(anchor), worktree_root=str(tmp_path / "trees"))},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "detect_platform", lambda: actual)
    monkeypatch.setattr(related_cli, "_related_config_source_anchors", lambda *a, **k: [str(anchor)])
    monkeypatch.setattr(doctor, "_read_projects", lambda: {})
    monkeypatch.setattr(repos, "find_repo", lambda name: None)
    monkeypatch.setattr(repos, "resolve_account", lambda entry: None)
    metadata = current.swapcase()
    target = related.Locus(
        preferred=f"machine:{metadata}" if locus == "machine" else locus,
        machines=[metadata] if locus == "local" else [],
        container={"repo": "org/target", "machines": [metadata]} if locus == "container" else {},
    )
    related.write_related(anchor, related.RelatedConfig(
        related={"target": related.RelatedEntry(name="target", locus=target)},
    ))
    expected = "example-host-wsl" if actual == "wsl" else "example-host"
    assert related_cli._related_current_machine([str(anchor)], str(anchor)) == expected
    match = related_cli._related_machine_matcher([str(anchor)], str(anchor))
    assert match(metadata, expected)
    assert match("example-host", expected) is (actual == "windows")
    assert not match("native-profile" if actual == "wsl" else "guest-profile", expected)
    assert related_cli.cmd_related_dispatch(["resolve", "target", "--repo", str(anchor), "--json"]) == 0
    assert json.loads(capfd.readouterr().out)["available_here"]
    related_cli._related_doctor(str(anchor), [], True)
    payload = json.loads(capfd.readouterr().out)
    assert payload["current_machine"] == expected
    assert not any(finding["kind"] == "unknown_machine" for finding in payload["findings"])
