"""Owning parser and placement construction, including passive failure paths."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import pytest
from native_shell_support import run_headless

from agent_worktrees import worktree_data_cli as cli
from agent_worktrees import config as cfg, project_state
from agent_worktrees import __main__ as main

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("filesystem"),
]


def parse(*argv: str):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    cli.add_parsers(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(list(argv))


def test_capability_discovery_and_invalid_selection_never_read_project_state(
    monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(
        cli.cfg, "project_name", lambda: pytest.fail("capability query resolved project data"),
    )
    args = parse("worktree-data-read", "--capabilities", "--json")
    assert args.func(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True and result["capabilities"] == [cli.CAPABILITY]
    invalid = parse(
        "worktree-data-read", "--capabilities", "--store-id", "a" * 32, "--json",
    )
    assert invalid.func(invalid) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "invalid-request" and result["ok"] is False


@pytest.mark.parametrize("argv", [
    ["worktree-data-read", "--worktree", "exact", "--contract-version", "2"],
    ["worktree-data-read", "--worktree", "exact", "--store", "a" * 32],
    ["worktree-data-read", "--worktree", "exact", "--capabilities"],
    ["worktree-data-allocate", "--worktree", "exact"],
    [
        "worktree-data-allocate", "--worktree", "exact", "--creation-nonce", "nonce",
        "--domain", "credentials", "--lane", "main", "--store-id", "a" * 32,
    ],
])
def test_invalid_or_incomplete_contract_is_rejected_by_actual_parser(argv) -> None:
    with pytest.raises(SystemExit) as failed:
        parse(*argv)
    assert failed.value.code == 2


def test_absent_project_is_explicit_not_an_empty_registration(capsys) -> None:
    argv = ["worktree-data-read", "--worktree", "exact", "--json"]
    assert cli.project_unavailable(argv, "No selected project\x1b\ud800") == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["ok"] is False and result["error"]["code"] == "project-unavailable"
    assert "data" not in result and "\x1b" not in output.out
    assert "\\ud800" in result["error"]["message"]
    with pytest.raises(SystemExit) as failed:
        cli.project_unavailable([*argv, "--not-a-field"], "missing project")
    assert failed.value.code == 2


@pytest.mark.parametrize("command", [
    "worktree-data-read", "worktree-observations", "agenda-read",
])
def test_root_missing_project_preserves_each_owner_error_envelope(
    monkeypatch, capsys, command: str,
) -> None:
    monkeypatch.setattr(main.front_door_cli, "resolve_invocation_project", lambda *args: False)
    monkeypatch.setattr(
        main, "_ensure_cluster_loaded", lambda: pytest.fail("missing project loaded cluster"),
    )
    argv = [command, "--worktree", "exact", "--json"]
    if command == "agenda-read":
        argv.extend(["--session", "head"])
    assert main.main(argv) == 1
    value = json.loads(capsys.readouterr().out)
    assert value["error"]["code"] == "project-unavailable"
    if command == "worktree-data-read":
        assert value["schema"] == cli.SCHEMA and "data" not in value
    elif command == "worktree-observations":
        assert value["type"] == "error" and value["contract_version"] == 1
    else:
        assert value["ok"] is False and value["contract_version"] == 1
        assert "type" not in value


CHILD = """
import os,sys
from pathlib import Path
os.environ.pop("COPILOT_EXTENSIONS_CONTEXT",None)
from agent_worktrees import config as cfg, project_state, __main__ as main
cfg._home=lambda:Path(sys.argv[1])
project_state._normalized_repository_remote=lambda repository:f"network:example.invalid/{repository}"
def unexpected_cluster():
    raise AssertionError("durable-placement CLI loaded deferred command cluster")
main._ensure_cluster_loaded=unexpected_cluster
sys.exit(main.main(["--project",sys.argv[2],*sys.argv[3:]]))
"""


def test_real_root_cli_allocates_once_and_fresh_reader_survives_retirement(
    tmp_path: Path, monkeypatch,
) -> None:
    project = "sample-project"
    worktree = "machine-20300101-123456-abcd"
    nonce, store = "0123456789abcdef", "a" * 32
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(cfg, "_home", lambda: tmp_path)
    monkeypatch.setattr(
        project_state, "_normalized_repository_remote",
        lambda repository: f"network:example.invalid/{repository}",
    )
    root = cfg.project_dir(project)
    (root / "worktrees").mkdir(parents=True)
    record = root / "worktrees" / f"{worktree}.yaml"
    record.write_text(json.dumps({
        "worktree_id": worktree, "creation_nonce": nonce, "repo": project,
        "branch": "worktree/sample", "worktree_path": str(tmp_path / "absent-checkout"),
        "machine": "sample-machine", "platform": "linux",
    }), encoding="utf-8")

    def invoke(command: str, *argv: str, code: int = 0):
        result = run_headless(
            [sys.executable, "-c", CHILD, str(tmp_path), project, command,
             "--worktree", worktree, "--json", *argv],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert result.returncode == code, (result.stdout, result.stderr)
        value = json.loads(result.stdout)
        assert value["schema"] == cli.SCHEMA and value["version"] == cli.VERSION
        return value

    def bytes_by_path():
        return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    original = bytes_by_path()
    absent = invoke("worktree-data-read", "--store-id", store)
    assert absent["availability"] == "not-registered" and absent["data"] is None
    assert bytes_by_path() == original
    allocation = ("--creation-nonce", nonce, "--lane", "main", "--store-id", store)
    created = invoke("worktree-data-allocate", *allocation)
    placement = created["data"]
    home = Path(placement["state_home"])
    assert home.is_dir() and home.is_relative_to(root / "worktree-state")
    assert list(home.iterdir()) == []
    assert placement["state_home_allocated"] is True
    assert placement["store_content_validated"] is False
    assert placement["live_authority"] is False
    (home / "owner-content.bin").write_bytes(b"domain-owned content is not reinitialized")
    registered = bytes_by_path()
    assert invoke("worktree-data-allocate", *allocation) == created
    assert bytes_by_path() == registered
    read = invoke("worktree-data-read", "--store-id", store)
    assert read["data"]["placements"] == [placement]
    assert read["data"]["canonical_record"] == "matching"
    assert bytes_by_path() == registered
    wrong = invoke("worktree-data-read", "--creation-nonce", "fedcba9876543210", code=1)
    assert wrong["error"]["code"] == "identity-conflict" and "data" not in wrong
    assert bytes_by_path() == registered
    record.rename(tmp_path / "retained-canonical-record.yaml")
    retired = bytes_by_path()
    retained = invoke("worktree-data-read", "--store-id", store)
    assert retained["data"]["canonical_record"] == "missing"
    assert retained["data"]["live_authority"] is False
    assert retained["data"]["placements"] == [placement]
    assert bytes_by_path() == retired
    descriptor = root / "worktree-state" / worktree / "descriptor.json"
    descriptor.write_bytes(b"{")
    broken = invoke("worktree-data-read", "--store-id", store, code=1)
    assert broken["ok"] is False and "availability" not in broken
    assert broken["error"]["code"] in {"invalid-descriptor", "partial-registration"}
