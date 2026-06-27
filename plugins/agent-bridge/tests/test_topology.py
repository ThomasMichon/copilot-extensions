"""Tests for topology.py -- machines.yaml parsing."""

from __future__ import annotations

from pathlib import Path


from agent_bridge.topology import (
    load_machines_yaml,
    parse_machines_yaml,
)


# -- Sample data matching the machines.yaml format ----------

SAMPLE_MACHINES_YAML = {
    "machines": {
        "workstation": {
            "display_name": "Workstation",
            "environment": "Windows 11 Pro",
            "role": "Dev workloads, compilation",
            "ssh": {
                "environments": [
                    {"name": "windows", "alias": "workstation", "port": 2222, "user": "dev", "shell": "pwsh"},
                    {"name": "wsl", "alias": "workstation-wsl", "port": 22, "user": "dev", "shell": "bash"},
                ],
                "ip": "10.0.0.20",
                "ready": True,
            },
        },
        "server-a": {
            "display_name": "Server A",
            "environment": "Debian 13",
            "role": "Services",
            "ssh": {
                "environments": [
                    {"name": "linux", "alias": "server-a", "port": 22, "user": "deploy", "shell": "bash"},
                ],
                "ip": "10.0.0.10",
                "ready": True,
            },
        },
        "laptop": {
            "display_name": "Laptop",
            "environment": "Windows 11",
            "role": "Field terminal",
            "field_terminal": True,
            "ssh": {
                "environments": [
                    {"name": "windows", "alias": "laptop", "port": 2222, "user": "dev", "shell": "pwsh"},
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
        assert "workstation" in machines
        assert "server-a" in machines
        assert "laptop" in machines

    def test_machine_metadata(self):
        machines = parse_machines_yaml(SAMPLE_MACHINES_YAML)
        ws = machines["workstation"]
        assert ws.display_name == "Workstation"
        assert ws.environment == "Windows 11 Pro"
        assert ws.role == "Dev workloads, compilation"
        assert ws.ssh_ready is True
        assert ws.ssh_ip == "10.0.0.20"
        assert ws.field_terminal is False

    def test_field_terminal_flag(self):
        machines = parse_machines_yaml(SAMPLE_MACHINES_YAML)
        assert machines["laptop"].field_terminal is True

    def test_ssh_environments(self):
        machines = parse_machines_yaml(SAMPLE_MACHINES_YAML)
        ws = machines["workstation"]
        assert len(ws.ssh_environments) == 2
        win = ws.ssh_environments[0]
        assert win.name == "windows"
        assert win.alias == "workstation"
        assert win.port == 2222
        assert win.user == "dev"
        assert win.shell == "pwsh"

    def test_ssh_ready_false(self):
        machines = parse_machines_yaml(SAMPLE_MACHINES_YAML)
        assert machines["laptop"].ssh_ready is False

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
        env = self.machines["workstation"].get_ssh_env("wsl")
        assert env is not None
        assert env.alias == "workstation-wsl"
        assert env.shell == "bash"

    def test_get_ssh_env_default_prefers_wsl(self):
        env = self.machines["workstation"].get_ssh_env()
        assert env is not None
        assert env.name == "wsl"

    def test_get_ssh_env_default_prefers_linux(self):
        env = self.machines["server-a"].get_ssh_env()
        assert env is not None
        assert env.name == "linux"

    def test_get_ssh_env_nonexistent(self):
        env = self.machines["workstation"].get_ssh_env("freebsd")
        assert env is None

    def test_get_spawnable_ssh_env_skips_pwsh(self):
        env = self.machines["workstation"].get_spawnable_ssh_env()
        assert env is not None
        assert env.shell == "bash"
        assert env.name == "wsl"

    def test_get_spawnable_ssh_env_rejects_pwsh_explicit(self):
        env = self.machines["workstation"].get_spawnable_ssh_env("windows")
        assert env is None

    def test_get_spawnable_ssh_env_linux(self):
        env = self.machines["server-a"].get_spawnable_ssh_env()
        assert env is not None
        assert env.alias == "server-a"

    def test_get_spawnable_ssh_env_no_posix(self):
        env = self.machines["laptop"].get_spawnable_ssh_env()
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
