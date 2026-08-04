"""Tests for the adoption/onboarding flow (agent-index setup + config helpers).

Adoption designates one indexer, writes the shared designation into the repo
config, and writes this machine's concrete role into the machine-local config
(effort agent-index-engine-daemon, Phase 6; vision §adoption-designates-one-indexer).
"""

from __future__ import annotations

import argparse

import pytest

from agent_index import config
from agent_index.__main__ import cmd_setup


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_INDEX_ROLE", raising=False)
    monkeypatch.delenv("AGENT_INDEX_CONFIG", raising=False)
    monkeypatch.delenv("AGENT_INDEX_REPO", raising=False)
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_MACHINE", "boxA")
    # Adoption tests exercise *designation*, not host capability -- present a
    # capable host so a host designation is never blocked by the real test box.
    from agent_index import capability

    monkeypatch.setattr(
        capability, "detect", lambda: {"cores": 16, "ram_gb": 64.0, "cuda": False}
    )
    return tmp_path


def _args(**kw):
    base = dict(indexer=None, single=False, ssh=None, endpoint=None, repo=None,
                yes=True, json=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_machine_id_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_MACHINE", "Custom.Name")
    assert config.machine_id() == "Custom.Name"


def test_single_machine_makes_host(_iso, capsys):
    repo = _iso / "repo"
    repo.mkdir()
    rc = cmd_setup(_args(single=True, repo=str(repo)))
    assert rc == 0
    # machine-local role is host
    assert config.resolve_role() == "host"
    # repo designation names this machine
    ind = config.read_indexer(repo)
    assert ind == {"machine": "boxA"}


def test_remote_indexer_makes_client(_iso):
    repo = _iso / "repo"
    repo.mkdir()
    cmd_setup(_args(indexer="boxB", ssh="boxB-wsl", repo=str(repo)))
    assert config.resolve_role() == "client"
    ind = config.read_indexer(repo)
    assert ind["machine"] == "boxB"
    assert ind["ssh"] == "boxB-wsl"


def test_this_machine_named_as_indexer_is_host(_iso):
    repo = _iso / "repo"
    repo.mkdir()
    cmd_setup(_args(indexer="boxA", repo=str(repo)))
    assert config.resolve_role() == "host"


def test_no_repo_writes_machine_role_only(_iso, monkeypatch):
    # No --repo, no AGENT_INDEX_REPO, and cwd is not a git repo -> role only.
    monkeypatch.setattr(config, "repo_root", lambda explicit=None: None)
    rc = cmd_setup(_args(single=True))
    assert rc == 0
    assert config.resolve_role() == "host"


def test_designation_and_role_roundtrip(_iso):
    repo = _iso / "repo"
    repo.mkdir()
    config.write_indexer_designation(repo, "boxB", ssh="boxB-wsl", endpoint="http://h:8420")
    ind = config.read_indexer(repo)
    assert ind == {"machine": "boxB", "ssh": "boxB-wsl", "endpoint": "http://h:8420"}
    config.write_machine_role("client")
    assert config.resolve_role() == "client"


def test_write_machine_role_rejects_bad_role(_iso):
    with pytest.raises(ValueError):
        config.write_machine_role("banana")


def test_setup_merges_existing_repo_config(_iso):
    repo = _iso / "repo"
    repo.mkdir()
    p = config.repo_config_path(repo)
    p.parent.mkdir(parents=True)
    p.write_text("sources:\n  - git\nindexer:\n  machine: old\n", encoding="utf-8")
    cmd_setup(_args(indexer="boxB", repo=str(repo)))
    data = config._load_yaml(p)
    # existing unrelated key preserved, indexer replaced
    assert data["sources"] == ["git"]
    assert data["indexer"]["machine"] == "boxB"
