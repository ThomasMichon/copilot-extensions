"""Tests for topology.py -- machines.yaml parsing."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_bridge.topology import (
    MachineConfig,
    SshEnvironment,
    load_machines_yaml,
    parse_machines_yaml,
)


# -- Sample data matching the facility's actual machines.yaml format ----------

SAMPLE_MACHINES_YAML = {
    "machines": {
        "lambda-core": {
            "display_name": "Lambda-Core",
            "environment": "Windows 11 Pro",
            "role": "AI workloads, compilation",
            "ssh": {
                "environments": [
                    {"name": "windows", "alias": "lambda-core", "port": 2222, "user": "tmichon", "shell": "pwsh"},
                    {"name": "wsl", "alias": "lambda-core-wsl", "port": 22, "user": "tmichon", "shell": "bash"},
                ],
                "ip": "192.168.0.189",
                "ready": True,
            },
        },
        "wheatley": {
            "display_name": "Wheatley",
            "environment": "Debian 13",
            "role": "Media streaming",
            "ssh": {
                "environments": [
                    {"name": "linux", "alias": "wheatley", "port": 22, "user": "cjohnson", "shell": "bash"},
                ],
                "ip": "192.168.0.54",
                "ready": True,
            },
        },
        "tmichon-book2": {
            "display_name": "tmichon-book2",
            "environment": "Windows 11",
            "role": "Field terminal",
            "field_terminal": True,
            "ssh": {
                "environments": [
                    {"name": "windows", "alias": "tmichon-book2", "port": 2222, "user": "tmichon", "shell": "pwsh"},
                ],
                "ready": False,
            },
        },
    }
}


class TestParseMachinesYaml:

    def test_parse_all_machines(self):
        machines = parse_machines_yaml(SAMPLE_MACHINES_YAML)
        assert len(machines) == 3
        assert "lambda-core" in machines
        assert "wheatley" in machines
        assert "tmichon-book2" in machines

    def test_machine_metadata(self):
        machines = parse_machines_yaml(SAMPLE_MACHINES_YAML)
        lc = machines["lambda-core"]
        assert lc.display_name == "Lambda-Core"
        assert lc.environment == "Windows 11 Pro"
        assert lc.role == "AI workloads, compilation"
        assert lc.ssh_ready is True
        assert lc.ssh_ip == "192.168.0.189"
        assert lc.field_terminal is False

    def test_field_terminal_flag(self):
        machines = parse_machines_yaml(SAMPLE_MACHINES_YAML)
        assert machines["tmichon-book2"].field_terminal is True

    def test_ssh_environments(self):
        machines = parse_machines_yaml(SAMPLE_MACHINES_YAML)
        lc = machines["lambda-core"]
        assert len(lc.ssh_environments) == 2
        win = lc.ssh_environments[0]
        assert win.name == "windows"
        assert win.alias == "lambda-core"
        assert win.port == 2222
        assert win.user == "tmichon"
        assert win.shell == "pwsh"

    def test_ssh_ready_false(self):
        machines = parse_machines_yaml(SAMPLE_MACHINES_YAML)
        assert machines["tmichon-book2"].ssh_ready is False

    def test_empty_yaml(self):
        machines = parse_machines_yaml({})
        assert machines == {}

    def test_empty_machines_key(self):
        machines = parse_machines_yaml({"machines": {}})
        assert machines == {}


class TestMachineConfigSshEnv:

    def setup_method(self):
        self.machines = parse_machines_yaml(SAMPLE_MACHINES_YAML)

    def test_get_ssh_env_by_name(self):
        env = self.machines["lambda-core"].get_ssh_env("wsl")
        assert env is not None
        assert env.alias == "lambda-core-wsl"
        assert env.shell == "bash"

    def test_get_ssh_env_default_prefers_wsl(self):
        env = self.machines["lambda-core"].get_ssh_env()
        assert env is not None
        assert env.name == "wsl"

    def test_get_ssh_env_default_prefers_linux(self):
        env = self.machines["wheatley"].get_ssh_env()
        assert env is not None
        assert env.name == "linux"

    def test_get_ssh_env_nonexistent(self):
        env = self.machines["lambda-core"].get_ssh_env("freebsd")
        assert env is None

    def test_get_spawnable_ssh_env_skips_pwsh(self):
        env = self.machines["lambda-core"].get_spawnable_ssh_env()
        assert env is not None
        assert env.shell == "bash"
        assert env.name == "wsl"

    def test_get_spawnable_ssh_env_rejects_pwsh_explicit(self):
        env = self.machines["lambda-core"].get_spawnable_ssh_env("windows")
        assert env is None

    def test_get_spawnable_ssh_env_linux(self):
        env = self.machines["wheatley"].get_spawnable_ssh_env()
        assert env is not None
        assert env.alias == "wheatley"

    def test_get_spawnable_ssh_env_no_posix(self):
        env = self.machines["tmichon-book2"].get_spawnable_ssh_env()
        assert env is None


class TestLoadMachinesYaml:

    def test_load_valid_file(self, tmp_path: Path):
        import yaml
        yaml_path = tmp_path / "machines.yaml"
        yaml_path.write_text(yaml.dump(SAMPLE_MACHINES_YAML))
        machines = load_machines_yaml(yaml_path)
        assert len(machines) == 3

    def test_load_missing_file(self, tmp_path: Path):
        machines = load_machines_yaml(tmp_path / "nonexistent.yaml")
        assert machines == {}

    def test_load_invalid_yaml(self, tmp_path: Path):
        yaml_path = tmp_path / "machines.yaml"
        yaml_path.write_text(": : invalid yaml {{{")
        machines = load_machines_yaml(yaml_path)
        # Should return empty on parse error, not crash
        assert isinstance(machines, dict)
