"""Selected-cell invocation, refusal and response validation."""

import base64
import json
import subprocess
from types import SimpleNamespace as NS

import pytest

from agent_worktrees import pr_sweep, pr_sweep_transport as transport


def observation(**changes):
    return {
        "schema": pr_sweep.SCHEMA, "version": 1, "repo": "example/widgets",
        "cell": "worker-a/windows", "checked_utc": "2026-01-01T00:00:00Z",
        "roots": [], "claims": [], "errors": [], **changes,
    }


def target():
    return {"cell": "worker-a/windows", "alias": "worker-a-ssh", "shell": "powershell.exe"}


def test_windows_command_uses_exact_catalog_path_and_terminal_exit():
    text = transport.remote_command(
        "powershell.exe", r"C:\catalog owner\it's\agent-worktrees.ps1",
        "project-a", "example/widgets",
    )
    decoded = base64.b64decode(text.split()[-1]).decode("utf-16-le")
    assert r"& 'C:\catalog owner\it''s\agent-worktrees.ps1'" in decoded
    assert "$env:AGENT_WORKTREES_NO_SELFPROVISION='1'" in decoded
    assert "'-p' 'project-a' 'claims' 'find' 'pr' '--repo' 'example/widgets'" in decoded
    assert "'--state' 'all' '--sweep-observation' '--json'" in decoded
    assert decoded.endswith("exit $LASTEXITCODE")
    assert "'pr-sweep'" not in decoded and "'--help'" not in decoded


def test_posix_command_quotes_each_argument_without_path_lookup():
    text = transport.remote_command("bash", "/catalog owner's/agent-worktrees", "project-a", "example/widgets")
    assert "AGENT_WORKTREES_NO_SELFPROVISION=1" in text
    assert "--sweep-observation" in text and "catalog owner" in text
    assert "command -v" not in text


def test_owned_process_receives_existing_route_and_no_credential_arguments(monkeypatch):
    called = []

    def run(argv, **kwargs):
        called.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(observation()), "")

    monkeypatch.setattr(transport, "run_owned", run)
    result = transport.observe_remote(
        target(), r"C:\catalog\agent-worktrees.ps1", project="project-a",
        repo="example/widgets", timeout=17,
    )
    assert result["status"] == "ok"
    assert called[0][1]["timeout"] == 17
    assert "StrictHostKeyChecking=yes" in called[0][0]
    assert "BatchMode=yes" in called[0][0]
    assert called[0][0][-2] == "worker-a-ssh"


@pytest.mark.parametrize("payload", [
    {}, observation(version=True), observation(repo=7), observation(claims={}),
    observation(checked_utc="invalid"), observation(checked_utc="2026-01-01"),
    observation(claims=[{}]), observation(cell="worker-b/linux"),
    observation(claims=[{
        "project": "host", "worktree_id": "root", "repo": "example/widgets",
        "evidence": "unsettled-transitive-claim", "checked_utc": "2026-01-01T00:00:00Z",
        "path": ["root"], "number": 1,
    }]),
])
def test_invalid_or_foreign_response_is_explicit_failure(monkeypatch, payload):
    monkeypatch.setattr(transport, "run_owned", lambda argv, **_k:
                        subprocess.CompletedProcess(argv, 0, json.dumps(payload), ""))
    result = transport.observe_remote(
        target(), r"C:\catalog\agent-worktrees.ps1",
        project="project-a", repo="example/widgets", timeout=12,
    )
    assert result["status"] == "failed" and result["error"]
    assert "observation" not in result


def test_timeout_is_failure_not_empty_inventory(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ssh", 12)

    monkeypatch.setattr(transport, "run_owned", timeout)
    result = transport.observe_remote(
        target(), r"C:\catalog\agent-worktrees.ps1", project="project-a",
        repo="example/widgets", timeout=12,
    )
    assert result["status"] == "failed"
    assert "observation" not in result


def test_old_command_has_explicit_unsupported_coverage(monkeypatch):
    monkeypatch.setattr(transport, "run_owned", lambda argv, **_k:
                        subprocess.CompletedProcess(argv, 2, "", "unrecognized arguments: --sweep-observation"))
    result = transport.observe_remote(
        target(), r"C:\catalog\agent-worktrees.ps1", project="project-a",
        repo="example/widgets", timeout=12,
    )
    assert result["status"] == "unsupported"
    assert "observation" not in result


def test_invalid_process_encoding_is_a_cell_failure_and_next_cell_still_reads(monkeypatch):
    attempts = []

    def read(argv, **kwargs):
        attempts.append(argv)
        if len(attempts) == 1:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid response")
        return subprocess.CompletedProcess(argv, 0, json.dumps(observation()), "")

    monkeypatch.setattr(transport, "run_owned", read)
    results = [
        transport.observe_remote(
            target(), r"C:\catalog\agent-worktrees.ps1", project="project-a",
            repo="example/widgets", timeout=12,
        )
        for _ in range(2)
    ]
    assert results[0]["status"] == "failed" and results[0]["error"]
    assert "observation" not in results[0]
    assert results[1]["status"] == "ok" and len(attempts) == 2


def test_exact_cell_selection_preserves_native_and_wsl_as_distinct():
    cells = [{"cell": "worker-a/windows"}, {"cell": "worker-a/wsl"}]
    result = transport.select_targets(cells, [], ["worker-a/wsl"])
    assert [cell["selected"] for cell in result] == [True, False]


@pytest.mark.parametrize("cell,command", [
    ("worker-a/windows", "/commands/agent-worktrees"),
    ("worker-a/wsl", r"C:\commands\agent-worktrees.ps1"),
])
def test_command_paths_must_be_absolute_in_the_selected_environment(cell, command):
    with pytest.raises(ValueError, match="absolute"):
        transport.validate_command_map({cell: {"command": command, "project": "host"}}, [{"cell": cell}])


def test_unsupported_shell_is_reported_without_execution(monkeypatch):
    monkeypatch.setattr(transport, "run_owned", lambda *_a, **_k: pytest.fail("unsupported shell executed"))
    result = transport.observe_remote(
        {**target(), "shell": "cmd.exe"}, r"C:\catalog\agent-worktrees.ps1",
        project="project-a", repo="example/widgets", timeout=12,
    )
    assert result["status"] == "unsupported" and result["error"]


def test_native_topology_includes_unavailable_cells_without_probing(monkeypatch):
    conf = NS(machine="worker-a", platform="windows", default_repo=NS(anchor="/example/host"))
    entries = {
        "worker-a": transport.cfg.MachineEntry(key="worker-a", display_name="A", copilot=True, ssh_ready=True, alias="", ssh_environments=[
            transport.cfg.SSHEnvironment(name="windows", alias="worker-a-win", shell="pwsh"),
            transport.cfg.SSHEnvironment(name="wsl", alias="worker-a-wsl", shell="bash"),
        ]),
        "worker-b": transport.cfg.MachineEntry(key="worker-b", display_name="B", copilot=False, ssh_ready=False),
    }
    monkeypatch.setattr(transport.cfg, "load_machines_yaml", lambda anchor, **_k: entries)
    monkeypatch.setattr(transport.machine_identity, "is_local_machine", lambda name, cfg: name == "worker-a")
    result = {cell["cell"]: cell for cell in transport.targets(conf, True)}
    assert set(result) == {"worker-a/windows", "worker-a/wsl", "worker-b/"}
    assert result["worker-a/windows"]["local"]
    assert not result["worker-a/wsl"]["local"]
    assert not result["worker-b/"]["available"]


@pytest.mark.parametrize("platform,shell", [("linux", "bash"), ("windows", "pwsh"), ("", "bash")])
def test_alias_only_topology_retains_execution_platform(monkeypatch, platform, shell):
    conf = NS(machine="worker-a", platform="windows", default_repo=NS(anchor="/example/host"))
    monkeypatch.setattr(transport.cfg, "load_machines_yaml", lambda *_a, **_k: {
        "worker-b": transport.cfg.MachineEntry(key="worker-b", display_name="B", copilot=True,
                                             ssh_ready=True, alias="worker-b-ssh", execution_platform=platform),
    })
    monkeypatch.setattr(transport.machine_identity, "is_local_machine", lambda *_a: False)
    remote = transport.targets(conf, True)[1]
    assert remote["cell"] == "worker-b/" + platform and remote["shell"] == shell
    assert remote["available"] == bool(platform)


def test_actual_topology_loader_keeps_unrouted_environment(tmp_path, monkeypatch):
    (tmp_path / "machines.yaml").write_text(
        "machines:\n  worker-b:\n    ssh:\n      ready: true\n      environments:\n"
        "      - {name: windows, alias: worker-b-win}\n"
        "      - {name: wsl}\n",
    )
    conf = NS(machine="worker-a", platform="windows", default_repo=NS(anchor=str(tmp_path)))
    monkeypatch.setattr(transport.machine_identity, "is_local_machine", lambda *_a: False)
    cells = {row["cell"]: row for row in transport.targets(conf, True)}
    assert "worker-b-wsl/wsl" in cells and not cells["worker-b-wsl/wsl"]["available"]
    assert transport.select_targets(list(cells.values()), ["worker-b-wsl/wsl"], [])[-1]["selected"]
    ordinary = transport.cfg.load_machines_yaml(tmp_path)
    assert ordinary["worker-b-wsl"].ssh_environments == []


@pytest.mark.parametrize("reported,accepted", [
    ("worker-b-ssh/linux", True), ("worker-b-ssh/windows", False), ("foreign/linux", False),
])
def test_topology_alias_keeps_wrong_machine_and_environment_checks(monkeypatch, reported, accepted):
    selected = {
        "cell": "worker-b/linux", "alias": "worker-b-ssh", "shell": "bash",
        "machine_names": ["worker-b", "worker-b-ssh"],
    }
    monkeypatch.setattr(transport, "run_owned", lambda argv, **_k:
                        subprocess.CompletedProcess(argv, 0, json.dumps(observation(cell=reported)), ""))
    result = transport.observe_remote(
        selected, "/catalog/agent-worktrees", project="remote-project", repo="example/widgets", timeout=12,
    )
    assert (result["status"] == "ok") is accepted


@pytest.mark.parametrize("ambiguous", [False, True])
def test_legacy_response_hostname_requires_unambiguous_topology(monkeypatch, ambiguous):
    conf = NS(machine="worker-a", platform="windows", default_repo=NS(anchor="/example/host"))
    entries = {
        "worker-b": transport.cfg.MachineEntry(
            key="worker-b", display_name="B", hostname="os-host-b", copilot=True, ssh_ready=True,
            ssh_environments=[transport.cfg.SSHEnvironment(name="linux", alias="worker-b-ssh", shell="bash")],
        ),
    }
    if ambiguous:
        entries["worker-c"] = transport.cfg.MachineEntry(
            key="worker-c", display_name="C", hostname="os-host-b", copilot=True, ssh_ready=True,
            ssh_environments=[transport.cfg.SSHEnvironment(name="linux", alias="worker-c-ssh", shell="bash")],
        )
    monkeypatch.setattr(transport.cfg, "load_machines_yaml", lambda *_a, **_k: entries)
    monkeypatch.setattr(transport.machine_identity, "is_local_machine", lambda *_a: False)
    selected = next(row for row in transport.targets(conf, True) if row["cell"] == "worker-b/linux")
    monkeypatch.setattr(transport, "run_owned", lambda argv, **_k: subprocess.CompletedProcess(
        argv, 0, json.dumps(observation(cell="os-host-b/linux")), "",
    ))
    result = transport.observe_remote(
        selected, "/catalog/agent-worktrees", project="remote-project", repo="example/widgets", timeout=12,
    )
    assert (result["status"] == "ok") is not ambiguous
