"""Tests for repo provisioning hooks (config + command builder)."""

from __future__ import annotations

import base64
import re
from pathlib import Path

from agent_codespaces.config import (
    ProvisionConfig,
    ProvisionFile,
    _parse_provision,
    _parse_repo_config,
)
from agent_codespaces.provision import build_provision_command


class TestParseProvision:
    def test_parses_files_and_commands(self, tmp_path: Path) -> None:
        raw = {
            "files": [
                {"src": "hooks/a.sh", "dest": "~/.bashrc.d/a.sh"},
                {"src": "hooks/b.sh", "dest": "~/b", "mode": "0755"},
            ],
            "on_connect": ["echo hi"],
        }
        prov = _parse_provision(raw, tmp_path)
        assert len(prov.files) == 2
        assert prov.files[0].dest == "~/.bashrc.d/a.sh"
        assert prov.files[0].mode == "0644"
        assert prov.files[1].mode == "0755"
        assert prov.files[0].repo_dir == tmp_path
        assert prov.on_connect == ["echo hi"]

    def test_skips_invalid_entries(self, tmp_path: Path) -> None:
        prov = _parse_provision({"files": [{"src": "x"}]}, tmp_path)
        assert prov.files == []

    def test_repo_config_parses_provision(self, tmp_path: Path) -> None:
        rc = _parse_repo_config(
            {"machine_type": "m", "provision": {"files": [
                {"src": "h.sh", "dest": "~/h.sh"}]}},
            tmp_path,
        )
        assert rc.provision is not None
        assert rc.provision.files[0].dest == "~/h.sh"


class TestProvisionForRepo:
    def test_unions_global_and_repo(self, tmp_path: Path) -> None:
        from agent_codespaces.config import CodespacesConfig, RepoConfig

        cfg = CodespacesConfig()
        cfg.provision = ProvisionConfig(
            files=[ProvisionFile(src="g.sh", dest="~/g.sh", repo_dir=tmp_path)],
        )
        cfg.repos["o/r"] = RepoConfig(provision=ProvisionConfig(
            files=[ProvisionFile(src="r.sh", dest="~/r.sh", repo_dir=tmp_path)],
        ))
        combined = cfg.provision_for_repo("o/r")
        dests = [f.dest for f in combined.files]
        assert dests == ["~/g.sh", "~/r.sh"]

    def test_unknown_repo_returns_global_only(self, tmp_path: Path) -> None:
        from agent_codespaces.config import CodespacesConfig

        cfg = CodespacesConfig()
        cfg.provision = ProvisionConfig(
            files=[ProvisionFile(src="g.sh", dest="~/g.sh", repo_dir=tmp_path)],
        )
        combined = cfg.provision_for_repo("nope/nope")
        assert [f.dest for f in combined.files] == ["~/g.sh"]


class TestBuildProvisionCommand:
    def test_none_when_empty(self) -> None:
        assert build_provision_command(ProvisionConfig()) is None

    def test_deploys_file_with_payload(self, tmp_path: Path) -> None:
        script = tmp_path / "hook.sh"
        script.write_text("export FOO=bar\n")
        prov = ProvisionConfig(files=[
            ProvisionFile(src="hook.sh", dest="~/.bashrc.d/hook.sh",
                          mode="0644", repo_dir=tmp_path),
        ])
        cmd = build_provision_command(prov)
        assert cmd is not None
        assert "$HOME/.bashrc.d/hook.sh" in cmd
        assert "base64 -d" in cmd
        blob = re.search(r"printf %s (\S+) \| base64 -d", cmd).group(1)
        assert base64.b64decode(blob).decode() == "export FOO=bar\n"

    def test_missing_src_skipped(self, tmp_path: Path) -> None:
        prov = ProvisionConfig(files=[
            ProvisionFile(src="nope.sh", dest="~/x", repo_dir=tmp_path),
        ])
        assert build_provision_command(prov) is None

    def test_on_connect_only(self) -> None:
        prov = ProvisionConfig(on_connect=["echo hello"])
        cmd = build_provision_command(prov)
        assert cmd is not None
        assert "echo hello" in cmd

    def test_on_create_excluded_by_default(self) -> None:
        prov = ProvisionConfig(on_create=["bash install.sh"])
        assert build_provision_command(prov) is None

    def test_on_create_included_when_requested(self) -> None:
        prov = ProvisionConfig(
            on_connect=["echo connect"], on_create=["bash install.sh"],
        )
        cmd = build_provision_command(prov, include_on_create=True)
        assert cmd is not None
        assert "echo connect" in cmd
        assert "bash install.sh" in cmd
        # on_create runs after on_connect
        assert cmd.index("echo connect") < cmd.index("bash install.sh")
