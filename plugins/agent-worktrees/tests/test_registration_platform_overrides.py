"""Configuration platform routing stays distinct from observed execution."""

from pathlib import Path

import pytest
import yaml

from agent_worktrees import config as cfg, installation_cli, repos
from test_registration_local_identity import registration as _registration

registration = _registration


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("command", ["register", "install"])
@pytest.mark.parametrize("tier", ["global", "project", "dropin"])
@pytest.mark.parametrize("label", ["previous-label", "registered"])
@pytest.mark.parametrize(("allowed", "force"), [(False, False), (True, False), (True, True)])
def test_retained_platform_override_selects_the_actual_reload_anchor(
    registration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    platform: str, command: str, tier: str, label: str, allowed: bool, force: bool,
) -> None:
    fixture = registration
    fixture.args.force = force
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    override = {"windows": "wsl", "wsl": "linux", "linux": "windows"}[platform]
    observed_checkout = tmp_path / "observed-checkout"
    configured_checkout = tmp_path / "configured-checkout"
    current_valid = not (label == "registered" and allowed and not force)
    for path, valid in ((fixture.repo, current_valid), (observed_checkout, not allowed),
                        (configured_checkout, allowed)):
        path.mkdir(exist_ok=True)
        (path / "machines.yaml").write_text(yaml.safe_dump({"machines": {"local-space": {
            "hostname": "raw-host", "physical_host": "friendly-host",
            "execution_platform": platform if valid else override,
        }}}))
    registry = repos.ReposRegistry(repos={label: repos.RepoEntry(
        name=label, paths={
            platform: str(observed_checkout), override: str(configured_checkout),
        },
    )})
    monkeypatch.setattr(repos, "read_registry", lambda **k: registry)
    project_path = cfg.project_dir("registered") / "config.yaml"
    project_path.parent.mkdir(parents=True)
    raw = {
        "repo_name": label, "machine": "local-space",
        "repos": {"registered": {"anchor": str(fixture.repo)}} if label != "registered" else {},
    }
    if tier == "project":
        raw["platform"] = override
    elif tier == "global":
        cfg.global_config_path().write_text(f"platform: {override}\n")
    else:
        dropin = project_path.parent / "config.d" / "local.yaml"
        dropin.parent.mkdir()
        dropin.write_text(f"platform: {override}\n")
    project_path.write_text(yaml.safe_dump(raw))
    before = {str(p.relative_to(fixture.state)): p.read_bytes()
              for p in fixture.state.rglob("*") if p.is_file()}
    assert getattr(installation_cli, f"cmd_{command}")(fixture.args) == (0 if allowed else 1)
    if allowed:
        config = cfg.load_config(
            project_path, include_control_plane_related_pr=False,
            include_knowledge_preferences=False,
        )
        assert config.platform == (platform if force and tier == "project" else override)
        assert Path(config.default_repo.anchor) == (fixture.repo if force else configured_checkout)
        assert config.machine == "local-space"
    else:
        assert fixture.effects == []
        assert {str(p.relative_to(fixture.state)): p.read_bytes()
                for p in fixture.state.rglob("*") if p.is_file()} == before
