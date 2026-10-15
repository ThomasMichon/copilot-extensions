"""Related availability resolves physical metadata only in its actual context."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent_worktrees import config as cfg, doctor, related, related_cli, repos

pytestmark = pytest.mark.guard


@pytest.mark.parametrize("actual,key", [
    ("windows", "example-host"), ("wsl", "example-host-wsl"),
])
@pytest.mark.parametrize("hostname", ["Generated-Host", "gENERATED-hOST"])
@pytest.mark.parametrize("locus", ["local", "machine", "container"])
def test_unscoped_pair_hostname_uses_context_but_preserves_explicit_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd,
    actual: str, key: str, hostname: str, locus: str,
) -> None:
    entries = {
        "example-host": cfg.MachineEntry(
            key="example-host", display_name="Native", alias="native-alias",
            hostname="Generated-Host",
            ssh_environments=[cfg.SSHEnvironment("windows", "native-profile")],
        ),
        "example-host-wsl": cfg.MachineEntry(
            key="example-host-wsl", display_name="Guest", alias="guest-alias",
            hostname="Generated-Host",
            ssh_environments=[cfg.SSHEnvironment("wsl", "guest-profile")],
        ),
    }
    assert not any(entry.execution_platform for entry in entries.values())
    config = cfg.Config(
        srcroot=str(tmp_path), machine=key, platform=actual, repo_name="example",
        repos={"example": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path / "trees"))},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *args, **kwargs: entries)
    monkeypatch.setattr(cfg, "detect_platform", lambda: actual)
    monkeypatch.setattr(related_cli, "_related_config_source_anchors", lambda *a, **kw: [str(tmp_path)])
    monkeypatch.setattr(repos, "find_repo", lambda name: None)
    monkeypatch.setattr(repos, "resolve_account", lambda entry: None)
    monkeypatch.setattr(doctor, "_read_projects", lambda: {})
    target = related.Locus(
        preferred=f"machine:{hostname}" if locus == "machine" else locus,
        machines=[hostname] if locus == "local" else [],
        container={"repo": "org/target", "machines": [hostname]} if locus == "container" else {},
    )
    related.write_related(tmp_path, related.RelatedConfig(
        related={"target": related.RelatedEntry(name="target", locus=target)},
    ))
    matcher = related_cli._related_machine_matcher([str(tmp_path)], str(tmp_path))
    assert matcher(hostname, key)
    for candidate in ("example-host", "native-alias", "native-profile"):
        assert matcher(candidate, key) is (actual == "windows")
    for candidate in ("example-host-wsl", "guest-alias", "guest-profile"):
        assert matcher(candidate, key) is (actual == "wsl")
    assert related_cli.cmd_related_dispatch(["resolve", "target", "--repo", str(tmp_path), "--json"]) == 0
    assert json.loads(capfd.readouterr().out)["available_here"]
    related_cli._related_doctor(str(tmp_path), [], True)
    payload = json.loads(capfd.readouterr().out)
    assert payload["current_machine"] == key
    assert not any(item["kind"] == "unknown_machine" for item in payload["findings"])
    entries["profile-owner"] = cfg.MachineEntry(
        key="profile-owner", display_name="Foreign",
        ssh_environments=[cfg.SSHEnvironment(actual, hostname)],
    )
    assert not matcher(hostname, key)


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


@pytest.mark.parametrize("first", ["empty", "unrelated", "shadowed-key"])
@pytest.mark.parametrize("actual", ["windows", "wsl"])
def test_related_current_matcher_and_doctor_share_the_later_authoritative_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd, first: str, actual: str,
) -> None:
    product, control = tmp_path / "product", tmp_path / "control"
    product.mkdir()
    control.mkdir()
    key = "local-key-wsl" if actual == "wsl" else "local-key"
    initial = {} if first == "empty" else {
        key if first == "shadowed-key" else "other-key": {
            "execution_platform": actual, "physical_host": "foreign-host",
        },
    }
    (product / "machines.yaml").write_text(
        yaml.safe_dump({"machines": initial}), encoding="utf-8",
    )
    (control / "machines.yaml").write_text(yaml.safe_dump({"machines": {
        key: {
            "execution_platform": actual, "physical_host": "local-key",
            "hostname": "Generated-Host", "alias": "preferred-alias",
        },
    }}), encoding="utf-8")
    config = cfg.Config(
        srcroot=str(tmp_path), machine="Generated-Host", platform=actual, repo_name="product",
        repos={"product": cfg.RepoConfig(anchor=str(product), worktree_root=str(tmp_path / "trees"))},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(cfg, "detect_platform", lambda: actual)
    anchors = [str(product), str(control)]
    monkeypatch.setattr(related_cli, "_related_config_source_anchors", lambda *a, **kw: anchors)
    monkeypatch.setattr(repos, "find_repo", lambda name: None)
    monkeypatch.setattr(repos, "resolve_account", lambda entry: None)
    monkeypatch.setattr(doctor, "_read_projects", lambda: {})
    related.write_related(product, related.RelatedConfig(related={
        "target": related.RelatedEntry(
            name="target", locus=related.Locus(preferred="machine:PREFERRED-ALIAS"),
        ),
    }))
    assert related_cli._related_current_machine(anchors, str(product)) == key
    matcher = related_cli._related_machine_matcher(anchors, str(product))
    assert matcher("PREFERRED-ALIAS", key)
    assert matcher("generated-host", key)
    assert related_cli.cmd_related_dispatch(["resolve", "target", "--repo", str(product), "--json"]) == 0
    assert json.loads(capfd.readouterr().out)["available_here"]
    related_cli._related_doctor(str(product), [], True)
    assert not any(
        item["kind"] == "unknown_machine" for item in json.loads(capfd.readouterr().out)["findings"]
    )
