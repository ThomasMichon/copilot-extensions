"""Preferred identities stay local through source construction and row loading."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from machine_transport import (
    execution_identity,
    legacy_alias_forms,
    normalize_machine_entries,
    parse_machines_yaml,
)
from worktree_manager import engine_client
from worktree_manager.production_picker.picker_tui import data_local, data_ssh, derive, roster


def _execution_context(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    monkeypatch.setattr(execution_identity.sys, "platform", "win32" if platform == "windows" else "linux")
    monkeypatch.setattr(
        os, "uname", lambda: SimpleNamespace(release="6.1-microsoft-standard-WSL2"),
        raising=False,
    )
    is_file = Path.is_file
    read_text = Path.read_text
    markers = {Path("/.dockerenv"), Path("/run/.containerenv")}
    monkeypatch.setattr(
        Path, "is_file", lambda path: False if path in markers else is_file(path),
    )
    monkeypatch.setattr(
        Path, "read_text",
        lambda path, *a, **kw: "" if path == Path("/proc/1/cgroup") else read_text(path, *a, **kw),
    )
    assert execution_identity.detect_platform() == platform


CASES = [
    (platform, alias)
    for platform in ("windows", "wsl")
    for base in ("Example-Host", "Legacy-Alias")
    for form in dict.fromkeys((base, *legacy_alias_forms(base, platform)))
    for alias in (form, form.swapcase())
]


@pytest.mark.parametrize("platform,configured", CASES)
@pytest.mark.parametrize("hostname_metadata", [False, True])
def test_preferred_local_source_loads_rows_without_self_ssh(
    monkeypatch: pytest.MonkeyPatch, platform: str, configured: str,
    hostname_metadata: bool,
) -> None:
    _execution_context(monkeypatch, platform)
    monkeypatch.setattr(data_ssh.socket, "gethostname", lambda: "Generated-Host.example.test")
    monkeypatch.setattr(data_local, "LOCAL", data_local._local_identity())
    entries = normalize_machine_entries(parse_machines_yaml({"machines": {
        "Example-Host": {
            "hostname": "Generated-Host" if hostname_metadata else "", "alias": "Legacy-Alias",
            "display_name": "Example Workstation", "ssh": {"ready": True, "environments": [
                {"name": "windows", "alias": "native-profile"},
                {"name": "wsl", "alias": "guest-profile"},
            ]},
        },
        "Other-Host": {"ssh": {"ready": True, "environments": [
            {"name": "windows", "alias": "other-native"},
            {"name": "wsl", "alias": "other-guest"},
        ]}},
    }}))
    config = SimpleNamespace(machine=configured, default_repo=SimpleNamespace(anchor="/repo"))
    monkeypatch.setattr(data_ssh.cfg, "load_config", lambda: config)
    monkeypatch.setattr(data_ssh.cfg, "load_machines_yaml", lambda anchor: entries)
    monkeypatch.setattr(data_ssh.provider_sources, "load", lambda project: [])
    monkeypatch.setattr(data_ssh, "_project", lambda: "project")
    monkeypatch.setattr(data_local.context, "project", lambda: "project")
    monkeypatch.setattr(engine_client, "_ENGINE_CMD_OVERRIDE", ["fixture-engine"])
    sources = data_ssh._build_sources()
    local, = [source for source in sources if source.local]
    expected_key = "Example-Host-wsl" if platform == "wsl" else "Example-Host"
    expected_env = "WSL" if platform == "wsl" else "Win"
    assert local.key == (expected_key, expected_env)
    assert local.argv is None and local.ready
    assert roster.local_entry(entries, configured, "Generated-Host", platform).key == expected_key
    assert data_local.is_local(configured, expected_env)
    assert data_local.is_local(*local.key)
    local_labels = ["Legacy-Alias", "Example Workstation", "Generated-Host"]
    for base in local_labels:
        for alias in (base, *legacy_alias_forms(base, platform)):
            assert data_local.is_local(alias.swapcase(), expected_env)
    assert all(not source.local and source.argv[0] == "ssh" for source in sources if source is not local)
    assert {source.alias for source in sources if source is not local} == {
        "other-native", "other-guest",
        "native-profile" if platform == "wsl" else "guest-profile",
    }
    for source in sources:
        if source is not local:
            assert not data_local.is_local(*source.key)

    calls: list[list[str]] = []
    stored_machine = configured if hostname_metadata else "Generated-Host"

    def runner(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert argv[:3] == ["fixture-engine", "--project", "project"]
        assert "list" in argv and "--current" in argv
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "ok": True, "contract_version": 1,
            "worktrees": [{"id": "one", "machine": stored_machine, "platform": platform}],
            "projection": {
                "schema_version": 1, "quality": "current", "generation": "a" * 32,
                "revision": 1, "observed_at": 123, "roster_count": 1, "selected_count": 1,
            },
        }), "")

    row, = data_ssh._fetch(local, runner=runner)
    assert len(calls) == 1
    assert (row["machine"], row["env"], row["source_id"]) == (
        *local.key, local.source_id,
    )
    assert row["raw"]["machine"] == stored_machine
    buckets = derive.for_source([row], local.source_id)
    assert buckets == derive.for_machine([row], *local.key)
    assert [item for bucket in buckets for item in bucket] == [row]
    tab, = [tab for tab in data_ssh.source_tabs(sources) if tab["source_id"] == local.source_id]
    assert tab["machine"] == expected_key and tab["physical_machine"] == "Example-Host"


@pytest.mark.parametrize("platform", ["windows", "wsl"])
def test_preferred_local_source_preserves_explicit_execution_authority(
    monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    _execution_context(monkeypatch, platform)
    label = "Win" if platform == "windows" else "WSL"
    monkeypatch.setattr(data_local, "LOCAL", ("Generated-Host", label))
    entries = normalize_machine_entries(parse_machines_yaml({"machines": {
        "Example-Host": {
            "hostname": "Generated-Host", "alias": "Legacy-Alias",
            "ssh": {"environments": [
                {"name": "windows", "alias": "native-profile"},
                {"name": "wsl", "alias": "guest-profile"},
            ]},
        },
        "Foreign-Space": {
            "hostname": "Foreign-Host", "execution_platform": platform,
        },
    }}))
    entries = {key: replace(entry, legacy_key="") for key, entry in entries.items()}
    local_key = "Example-Host-wsl" if platform == "wsl" else "Example-Host"
    config = SimpleNamespace(machine=local_key, default_repo=SimpleNamespace(anchor="/repo"))
    monkeypatch.setattr(data_local.cfg, "load_config", lambda: config)
    monkeypatch.setattr(data_local.cfg, "load_machines_yaml", lambda anchor: entries)
    assert data_local.is_local(local_key.swapcase(), label)
    assert data_local.is_local("Generated-Host", label)
    assert not data_local.is_local("Foreign-Space", label)
    assert not data_local.is_local("guest-profile" if platform == "windows" else "native-profile", label)
    assert not data_local.is_local(
        "Example-Host (WSL)" if platform == "windows" else "Example-Host (Windows)", label,
    )
    # Explicit native keys/aliases cannot borrow guest ownership via the host.
    if platform == "wsl":
        assert not data_local.is_local("Example-Host", label)
        assert not data_local.is_local("Legacy-Alias", label)
