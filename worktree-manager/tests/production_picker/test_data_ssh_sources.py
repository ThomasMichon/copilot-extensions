"""Unit tests for ``data_ssh._build_sources`` machine/env resolution.

Focus: the local machine never needs an SSH profile of its own (the picker runs
there), and a listed env with no SSH profile is never connected to -- it renders
as a disabled tab instead.
"""
from __future__ import annotations

import json as _json
import os
import shlex
import stat
import types

import pytest

from agent_worktrees import config as cfg
from worktree_manager.production_picker.picker_tui import (
    data_ssh,
    derive,
    provider_sources,
    source_identity,
)


def _install_roster(monkeypatch, entries, *, machine, local_id):
    """Point ``_build_sources`` at a fabricated roster + local identity."""
    fake_config = types.SimpleNamespace(
        default_repo=types.SimpleNamespace(anchor="/repo"),
        machine=machine,
    )
    monkeypatch.setattr(data_ssh.cfg, "load_config", lambda: fake_config)
    monkeypatch.setattr(
        data_ssh.cfg, "load_machines_yaml", lambda _anchor: entries)
    monkeypatch.setattr(data_ssh, "_local_identity", lambda: local_id)
    monkeypatch.setattr(data_ssh, "_project", lambda: "proj")
    monkeypatch.setattr(data_ssh.provider_sources, "load", lambda _project: [])


def _entry(key, display, envs, *, ssh_ready=True, copilot=True, alias="", hostname=""):
    return cfg.MachineEntry(
        key=key,
        display_name=display,
        environment="",
        alias=alias,
        hostname=hostname,
        ssh_environments=envs,
        ssh_ready=ssh_ready,
        copilot=copilot,
    )


def _by_key(sources):
    return {(s.machine, s.env): s for s in sources}


def _assignment(effort="example-effort", acquired_at=1_700_000_000.0):
    return {
        "kind": "lease",
        "effort": effort,
        "acquired_at": acquired_at,
    }


def _provider_descriptor(**overrides):
    descriptor = {
        "kind": "provider-exec",
        "project": "proj",
        "target_id": "container:one",
        "instance_id": "instance-1",
        "label": "Restricted target",
        "alias": "restricted-target",
        "shell": "bash",
        "resolve": ["/bin/provider", "resolve", "one"],
        "connect": ["/bin/provider", "connect", "one"],
        "venue": {
            "provider": "agent-containers",
            "target_id": "container:one",
            "instance_id": "instance-1",
            "transport": "provider-exec",
            "ready": True,
            "posture_verified": True,
            "assignment": _assignment(),
        },
        "capabilities": {"list": True},
    }
    descriptor.update(overrides)
    return descriptor


def test_machine_source_has_canonical_identity_and_legacy_key():
    source = data_ssh.Source("Host-A", "WSL", None)

    assert source.source_kind == "machine-ssh"
    assert source.source_id == "machine-ssh:host-a:wsl"
    assert source.source_label == "Host-A / WSL"
    assert source.cache_key == source.source_id
    assert source.key == ("Host-A", "WSL")


def test_machine_source_identity_escapes_delimiters():
    first = data_ssh.Source("alpha:beta", "gamma", None)
    second = data_ssh.Source("alpha", "beta:gamma", None)

    assert first.source_id == "machine-ssh:alpha%3Abeta:gamma"
    assert second.source_id == "machine-ssh:alpha:beta%3Agamma"
    assert first.source_id != second.source_id


def test_provider_source_identity_is_stable_and_namespaced():
    assert source_identity.provider_exec_id(
        "Example Provider", "container:target-1"
    ) == "provider-exec:example%20provider:container%3Atarget-1"


def test_machine_source_rejects_noncanonical_explicit_identity():
    with pytest.raises(ValueError, match="machine source id must equal"):
        data_ssh.Source(
            "Host-A", "WSL", None, source_id="machine-ssh:other-host:linux"
        )


def test_non_machine_source_requires_matching_explicit_identity():
    with pytest.raises(ValueError, match="require an explicit source id"):
        data_ssh.Source("Virtual Target", "Provider", None, source_kind="provider-exec")

    with pytest.raises(ValueError, match="provider-exec: namespace"):
        data_ssh.Source(
            "Virtual Target",
            "Provider",
            None,
            source_kind="provider-exec",
            source_id="machine-ssh:virtual-target:provider",
        )

    with pytest.raises(ValueError, match="include a namespace value"):
        data_ssh.Source(
            "Virtual Target",
            "Provider",
            None,
            source_kind="provider-exec",
            source_id="provider-exec:",
        )


def test_normalized_row_carries_source_identity():
    row = derive.norm({"id": "example-1234"}, "Host-A", "WSL")
    other = derive.norm(
        {"id": "provider-5678"},
        "Virtual Target",
        "Provider",
        source_kind="provider-exec",
        source_id="provider-exec:example:target-1",
        source_label="Virtual target",
    )

    assert row["machine"] == "Host-A"
    assert row["env"] == "WSL"
    assert row["source_kind"] == "machine-ssh"
    assert row["source_id"] == "machine-ssh:host-a:wsl"
    assert row["source"] == {
        "kind": "machine-ssh",
        "id": "machine-ssh:host-a:wsl",
        "label": "Host-A / WSL",
    }
    assert derive.for_source([row, other], row["source_id"]) == derive.for_machine(
        [row, other], "Host-A", "WSL"
    )


def test_provider_row_carries_lineage_posture_and_no_machine_identity():
    row = derive.norm(
        {"id": "provider-5678"},
        "",
        "",
        source_kind="provider-exec",
        source_id="provider-exec:example:target-1",
        source_label="Restricted target",
        source_metadata={
            "provider": "example",
            "target_id": "target-1",
            "instance_id": "instance-2",
            "venue": {"ready": True, "posture_verified": True},
        },
        source_capabilities={"messages": True, "resume": False},
    )

    assert row["machine"] == ""
    assert row["env"] == ""
    assert row["machine_env"] == "Restricted target"
    assert row["source"]["instance_id"] == "instance-2"
    assert row["source"]["venue"]["posture_verified"] is True
    assert row["source_capabilities"] == {"messages": True, "resume": False}


def test_provider_registry_filters_project_and_isolates_invalid_files(tmp_path, caplog):
    registry_path = tmp_path / "agent-containers.json"
    registry_path.write_text(
        _json.dumps({
            "schema_version": 1,
            "provider": "agent-containers",
            "sources": [
                {
                    "kind": "provider-exec",
                    "project": "proj",
                    "target_id": "container:one",
                    "instance_id": "instance-1",
                    "label": "Restricted target",
                    "alias": "restricted-target",
                    "shell": "bash",
                    "resolve": ["/bin/agent-containers", "namespace-resolve", "one"],
                    "connect": ["/bin/agent-containers", "ssh-stdio", "one"],
                    "venue": {
                        "provider": "agent-containers",
                        "target_id": "container:one",
                        "instance_id": "instance-1",
                        "transport": "provider-exec",
                        "ready": True,
                        "posture_verified": True,
                        "assignment": _assignment(),
                    },
                    "capabilities": {"list": True, "resume": False},
                },
                {
                    "kind": "provider-exec",
                    "project": "other",
                    "target_id": "container:two",
                    "instance_id": "instance-2",
                    "label": "Other target",
                    "alias": "other-target",
                    "shell": "bash",
                    "resolve": ["/bin/agent-containers", "namespace-resolve", "two"],
                    "connect": ["/bin/agent-containers", "ssh-stdio", "two"],
                    "venue": {
                        "provider": "agent-containers",
                        "target_id": "container:two",
                        "instance_id": "instance-2",
                        "transport": "provider-exec",
                        "ready": True,
                        "posture_verified": True,
                        "assignment": _assignment("other-effort"),
                    },
                    "capabilities": {"list": True},
                },
                {
                    "kind": "provider-exec",
                    "project": "proj",
                    "target_id": "container:broken",
                },
            ],
        }),
        encoding="utf-8",
    )
    registry_path.chmod(0o600)
    broken_path = tmp_path / "broken.json"
    broken_path.write_text("{", encoding="utf-8")
    broken_path.chmod(0o600)

    sources = provider_sources.load("PROJ", tmp_path)

    assert [source.source_id for source in sources] == [
        "provider-exec:agent-containers:container%3Aone"
    ]
    assert sources[0].alias == "restricted-target"
    assert "ignoring invalid Picker source registry" in caplog.text
    assert "ignoring invalid Picker source" in caplog.text


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"shell": []}, "shell must be bash or pwsh"),
        (
            {"label": "bad\nlabel"},
            "label must be at most 80 characters without control characters",
        ),
        (
            {"capabilities": {"list": True, "open": True}},
            "unsupported capabilities: open",
        ),
    ],
)
def test_provider_registry_rejects_invalid_read_boundary(
    tmp_path,
    caplog,
    override,
    message,
):
    registry_path = tmp_path / "provider.json"
    registry_path.write_text(
        _json.dumps({
            "schema_version": 1,
            "provider": "agent-containers",
            "sources": [_provider_descriptor(**override)],
        }),
        encoding="utf-8",
    )
    registry_path.chmod(0o600)

    assert provider_sources.load("proj", tmp_path) == []
    assert message in caplog.text


def test_provider_registry_rejects_every_ambiguous_duplicate(tmp_path, caplog):
    payload = {
        "schema_version": 1,
        "provider": "agent-containers",
        "sources": [_provider_descriptor()],
    }
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(_json.dumps(payload), encoding="utf-8")
    second_path.write_text(_json.dumps(payload), encoding="utf-8")
    first_path.chmod(0o600)
    second_path.chmod(0o600)

    assert provider_sources.load("proj", tmp_path) == []
    assert "rejecting ambiguous duplicate Picker source id" in caplog.text


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX ownership")
def test_provider_registry_rejects_group_writable_directory(tmp_path, caplog):
    tmp_path.chmod(tmp_path.stat().st_mode | stat.S_IWGRP)

    assert provider_sources.load("proj", tmp_path) == []
    assert "source registry must not be group/world-writable" in caplog.text


def test_build_sources_appends_provider_without_synthetic_machine(monkeypatch):
    _install_roster(
        monkeypatch,
        {},
        machine="local",
        local_id=("local", "linux"),
    )
    registered = provider_sources.ProviderSource(
        kind="provider-exec",
        source_id="provider-exec:agent-containers:container%3Aone",
        label="Restricted target",
        project="proj",
        provider="agent-containers",
        target_id="container:one",
        instance_id="instance-1",
        alias="restricted-target",
        shell="bash",
        venue={
            "ready": True,
            "posture_verified": True,
            "assignment": _assignment(),
        },
        capabilities={"list": True, "messages": True, "resume": False},
        resolve_argv=("agent-containers", "namespace-resolve", "one"),
        connect_argv=("/bin/agent-containers", "ssh-stdio", "one"),
    )
    monkeypatch.setattr(
        data_ssh.provider_sources,
        "load",
        lambda project: [registered] if project == "proj" else [],
    )

    sources = data_ssh._build_sources()
    provider = next(source for source in sources if source.source_kind == "provider-exec")
    tab = next(tab for tab in data_ssh.source_tabs() if tab["source_kind"] == "provider-exec")

    assert provider.machine == ""
    assert provider.env == ""
    assert provider.alias == "restricted-target"
    assert any(part.startswith("ProxyCommand=") for part in provider.argv)
    assert provider.argv[-2] == "restricted-target"
    assert tab["label"] == "Restricted target"
    assert tab["source_id"] == provider.source_id


def test_provider_resolve_refreshes_instance_and_rejects_unverified_posture():
    source = data_ssh.Source(
        "",
        "",
        ["ssh"],
        source_kind="provider-exec",
        source_id="provider-exec:agent-containers:container%3Aone",
        provider="agent-containers",
        target_id="container:one",
        instance_id="old-instance",
        venue={"assignment": _assignment()},
        resolve_argv=["provider", "resolve", "one"],
        connect_argv=["/bin/provider", "connect", "one"],
    )

    def runner(_argv, _timeout):
        return types.SimpleNamespace(
            returncode=0,
            stdout=_json.dumps({
                "venue": {
                    "provider": "agent-containers",
                    "target_id": "container:one",
                    "instance_id": "new-instance",
                    "ready": True,
                    "posture_verified": True,
                    "assignment": _assignment(),
                }
            }),
            stderr="",
        )

    assert data_ssh._resolve_provider_source(source, runner) is True
    assert source.instance_id == "new-instance"

    def unverified(_argv, _timeout):
        payload = {
            "venue": {
                **source.venue,
                "instance_id": "new-instance",
                "posture_verified": False,
            }
        }
        return types.SimpleNamespace(
            returncode=0,
            stdout=_json.dumps(payload),
            stderr="",
        )

    with pytest.raises(RuntimeError, match="trust posture"):
        data_ssh._resolve_provider_source(source, unverified)

    def reassigned(_argv, _timeout):
        payload = {
            "venue": {
                **source.venue,
                "assignment": _assignment("replacement-effort"),
            }
        }
        return types.SimpleNamespace(
            returncode=0,
            stdout=_json.dumps(payload),
            stderr="",
        )

    with pytest.raises(RuntimeError, match="lease assignment changed"):
        data_ssh._resolve_provider_source(source, reassigned)


def test_loader_invalidates_rows_before_loading_replaced_instance(monkeypatch):
    source = data_ssh.Source(
        "",
        "",
        ["ssh"],
        source_kind="provider-exec",
        source_id="provider-exec:agent-containers:container%3Aone",
        provider="agent-containers",
        target_id="container:one",
        instance_id="old-instance",
        venue={"assignment": _assignment()},
        resolve_argv=["/bin/provider", "resolve", "one"],
    )
    loader = data_ssh.LiveLoader([source])
    loader._records[source.source_id] = ["stale-row"]
    monkeypatch.setattr(data_ssh, "_resolve_provider_source", lambda *_args: True)
    observed = []
    monkeypatch.setattr(
        data_ssh,
        "_fetch",
        lambda *_args, **_kwargs: observed.append(
            list(loader._records[source.source_id])
        ) or [],
    )

    loader._load_one(source)

    assert observed == [[]]


def test_provider_repoll_replaces_instance_rows_and_returns_ready(monkeypatch):
    source = data_ssh.Source(
        "",
        "",
        ["ssh"],
        source_kind="provider-exec",
        source_id="provider-exec:agent-containers:container%3Aone",
        provider="agent-containers",
        target_id="container:one",
        instance_id="old-instance",
        venue={"assignment": _assignment()},
        resolve_argv=["/bin/provider", "resolve", "one"],
    )
    loader = data_ssh.LiveLoader([source])
    loader._records[source.source_id] = ["stale-row"]
    loader._state[source.source_id] = "ready"
    loader._refreshing.add(source.source_id)
    monkeypatch.setattr(data_ssh, "_resolve_provider_source", lambda *_args: True)
    monkeypatch.setattr(data_ssh, "_fetch", lambda *_args, **_kwargs: ["fresh-row"])

    loader._refresh_one(source, 0)

    assert loader.records_for_source(source.source_id) == ["fresh-row"]
    assert loader.state_for_source(source.source_id) == "ready"


def test_provider_repoll_clears_rows_when_live_resolve_fails(monkeypatch):
    source = data_ssh.Source(
        "",
        "",
        ["ssh"],
        source_kind="provider-exec",
        source_id="provider-exec:agent-containers:container%3Aone",
        provider="agent-containers",
        target_id="container:one",
        instance_id="old-instance",
        venue={"assignment": _assignment()},
        resolve_argv=["/bin/provider", "resolve", "one"],
    )
    loader = data_ssh.LiveLoader([source])
    loader._records[source.source_id] = ["stale-row"]
    loader._state[source.source_id] = "ready"
    loader._refreshing.add(source.source_id)

    def fail(*_args):
        raise RuntimeError("provider target is not ready")

    monkeypatch.setattr(data_ssh, "_resolve_provider_source", fail)

    loader._refresh_one(source, 0)

    assert loader.records_for_source(source.source_id) == []
    assert loader.state_for_source(source.source_id) == "failed"


def test_provider_repoll_keeps_same_instance_rows_on_list_failure(monkeypatch):
    source = data_ssh.Source(
        "",
        "",
        ["ssh"],
        source_kind="provider-exec",
        source_id="provider-exec:agent-containers:container%3Aone",
        provider="agent-containers",
        target_id="container:one",
        instance_id="instance-1",
        venue={"assignment": _assignment()},
        resolve_argv=["/bin/provider", "resolve", "one"],
    )
    loader = data_ssh.LiveLoader([source])
    loader._records[source.source_id] = ["last-good"]
    loader._state[source.source_id] = "ready"
    loader._refreshing.add(source.source_id)
    monkeypatch.setattr(data_ssh, "_resolve_provider_source", lambda *_args: False)

    def fail(*_args, **_kwargs):
        raise RuntimeError("temporary SSH failure")

    monkeypatch.setattr(data_ssh, "_fetch", fail)

    loader._refresh_one(source, 0)

    assert loader.records_for_source(source.source_id) == ["last-good"]
    assert loader.state_for_source(source.source_id) == "ready"


def test_provider_routes_reads_but_suppresses_mutations(monkeypatch):
    _install_roster(
        monkeypatch,
        {},
        machine="local",
        local_id=("local", "linux"),
    )
    registered = provider_sources.ProviderSource(
        kind="provider-exec",
        source_id="provider-exec:agent-containers:container%3Aone",
        label="Restricted target",
        project="proj",
        provider="agent-containers",
        target_id="container:one",
        instance_id="instance-1",
        alias="restricted-target",
        shell="bash",
        venue={
            "ready": True,
            "posture_verified": True,
            "assignment": _assignment(),
        },
        capabilities={
            "messages": True,
            "sessions": True,
            "refresh": True,
            "resume": False,
            "cleanup": True,
        },
        resolve_argv=("agent-containers", "namespace-resolve", "one"),
        connect_argv=("/bin/agent-containers", "ssh-stdio", "one"),
    )
    monkeypatch.setattr(data_ssh.provider_sources, "load", lambda _project: [registered])
    monkeypatch.setattr(data_ssh, "_resolve_provider_source", lambda *_args: False)
    source_id = registered.source_id
    fingerprint = data_ssh._provider_transport_fingerprint(registered)

    messages = data_ssh.recent_messages_argv(
        "",
        "",
        "wt-1",
        source_id=source_id,
        expected_instance_id="instance-1",
        expected_assignment=_assignment(),
        expected_transport_fingerprint=fingerprint,
        limit=4,
    )
    sessions = data_ssh.list_sessions_argv(
        "",
        "",
        "wt-1",
        source_id=source_id,
        expected_instance_id="instance-1",
        expected_assignment=_assignment(),
        expected_transport_fingerprint=fingerprint,
    )

    assert messages and messages[-2] == "restricted-target"
    proxy = next(part for part in messages if part.startswith("ProxyCommand="))
    assert "--expected-target-id container:one" in proxy
    assert "--expected-instance-id instance-1" in proxy
    assert "example-effort" in proxy
    assert "ControlMaster=no" in messages
    assert "ControlPath=none" in messages
    assert "recent-messages --worktree wt-1 --limit 4 --json" in messages[-1]
    assert sessions and "list-sessions --worktree wt-1 --json" in sessions[-1]
    assert data_ssh.profiles_argv("", "", action="apply", set_json="[]") is None
    assert data_ssh.remote_op_argv(
        "", "", "cleanup", "wt-1", source_id=source_id
    ) is None

    hostile_id = "wt-1; touch /tmp/not-executed"
    hostile = data_ssh.recent_messages_argv(
        "",
        "",
        hostile_id,
        source_id=source_id,
        expected_instance_id="instance-1",
        expected_assignment=_assignment(),
        expected_transport_fingerprint=fingerprint,
    )
    outer = shlex.split(hostile[-1])
    assert outer[:2] == ["bash", "-lc"]
    assert shlex.split(outer[2])[3] == hostile_id
    assert data_ssh._remote_arg("pwsh", "wt'; Write-Error pwned") == (
        "'wt''; Write-Error pwned'"
    )


def test_provider_read_route_requires_matching_live_instance(monkeypatch):
    _install_roster(
        monkeypatch,
        {},
        machine="local",
        local_id=("local", "linux"),
    )
    registered = provider_sources.ProviderSource(
        kind="provider-exec",
        source_id="provider-exec:agent-containers:container%3Aone",
        label="Restricted target",
        project="proj",
        provider="agent-containers",
        target_id="container:one",
        instance_id="instance-1",
        alias="restricted-target",
        shell="bash",
        venue={
            "ready": True,
            "posture_verified": True,
            "assignment": _assignment(),
        },
        capabilities={"list": True, "messages": True, "sessions": True},
        resolve_argv=("/bin/provider", "resolve", "one"),
        connect_argv=("/bin/provider", "connect", "one"),
    )
    monkeypatch.setattr(data_ssh.provider_sources, "load", lambda _project: [registered])
    fingerprint = data_ssh._provider_transport_fingerprint(registered)

    def replace(source, _runner):
        source.instance_id = "instance-2"
        return True

    monkeypatch.setattr(data_ssh, "_resolve_provider_source", replace)

    assert data_ssh.recent_messages_argv(
        "",
        "",
        "wt-1",
        source_id=registered.source_id,
        expected_instance_id="instance-1",
        expected_assignment=_assignment(),
        expected_transport_fingerprint=fingerprint,
    ) is None
    assert data_ssh.list_sessions_argv(
        "",
        "",
        "wt-1",
        source_id=registered.source_id,
        expected_instance_id="instance-1",
        expected_assignment=_assignment(),
        expected_transport_fingerprint=fingerprint,
    ) is None


def test_provider_read_route_requires_displayed_lease_assignment(monkeypatch):
    _install_roster(
        monkeypatch,
        {},
        machine="local",
        local_id=("local", "linux"),
    )
    registered = provider_sources.ProviderSource(
        kind="provider-exec",
        source_id="provider-exec:agent-containers:container%3Aone",
        label="Restricted target",
        project="proj",
        provider="agent-containers",
        target_id="container:one",
        instance_id="instance-1",
        alias="restricted-target",
        shell="bash",
        venue={
            "ready": True,
            "posture_verified": True,
            "assignment": _assignment(),
        },
        capabilities={"list": True, "messages": True, "sessions": True},
        resolve_argv=("/bin/provider", "resolve", "one"),
        connect_argv=("/bin/provider", "connect", "one"),
    )
    monkeypatch.setattr(data_ssh.provider_sources, "load", lambda _project: [registered])
    fingerprint = data_ssh._provider_transport_fingerprint(registered)

    def reassign(source, _runner):
        source.venue["assignment"] = _assignment("replacement-effort")
        return False

    monkeypatch.setattr(data_ssh, "_resolve_provider_source", reassign)

    assert data_ssh.recent_messages_argv(
        "",
        "",
        "wt-1",
        source_id=registered.source_id,
        expected_instance_id="instance-1",
        expected_assignment=_assignment(),
        expected_transport_fingerprint=fingerprint,
    ) is None
    assert data_ssh.list_sessions_argv(
        "",
        "",
        "wt-1",
        source_id=registered.source_id,
        expected_instance_id="instance-1",
        expected_assignment=_assignment(),
        expected_transport_fingerprint=fingerprint,
    ) is None


def test_provider_proxy_command_escapes_openssh_percent_tokens():
    source = data_ssh.Source(
        "",
        "",
        None,
        source_kind="provider-exec",
        source_id="provider-exec:agent-containers:container%3Aone",
        provider="agent-containers",
        target_id="container:one",
        instance_id="instance-1",
        alias="restricted-target",
        shell="bash",
        venue={"assignment": _assignment("100%home")},
        connect_argv=["/bin/provider", "connect", "one"],
    )

    argv = data_ssh._provider_remote_argv(
        source,
        "proj list --json",
        expected_instance_id="instance-1",
        expected_assignment=_assignment("100%home"),
    )

    proxy = next(part for part in argv if part.startswith("ProxyCommand="))
    assert "100%%home" in proxy


def test_provider_read_route_rejects_rewritten_transport(monkeypatch):
    _install_roster(
        monkeypatch,
        {},
        machine="local",
        local_id=("local", "linux"),
    )
    displayed = provider_sources.ProviderSource(
        kind="provider-exec",
        source_id="provider-exec:agent-containers:container%3Aone",
        label="Restricted target",
        project="proj",
        provider="agent-containers",
        target_id="container:one",
        instance_id="instance-1",
        alias="restricted-target",
        shell="bash",
        venue={
            "ready": True,
            "posture_verified": True,
            "assignment": _assignment(),
        },
        capabilities={"list": True, "messages": True},
        resolve_argv=("/bin/provider", "resolve", "one"),
        connect_argv=("/bin/provider", "connect", "one"),
    )
    rewritten = provider_sources.ProviderSource(
        **{
            **displayed.__dict__,
            "connect_argv": ("/bin/other-provider", "connect", "one"),
        }
    )
    monkeypatch.setattr(data_ssh.provider_sources, "load", lambda _project: [rewritten])
    monkeypatch.setattr(
        data_ssh,
        "_resolve_provider_source",
        lambda *_args: pytest.fail("rewritten transport must fail before resolve"),
    )

    assert data_ssh.recent_messages_argv(
        "",
        "",
        "wt-1",
        source_id=displayed.source_id,
        expected_instance_id=displayed.instance_id,
        expected_assignment=_assignment(),
        expected_transport_fingerprint=data_ssh._provider_transport_fingerprint(
            displayed
        ),
    ) is None


def test_loader_cache_isolated_by_canonical_source_id():
    machine = data_ssh.Source("Host-A", "WSL", None)
    provider = data_ssh.Source(
        "Host-A",
        "WSL",
        None,
        source_kind="provider-exec",
        source_id="provider-exec:example:target-1",
        source_label="Virtual target",
    )

    loader = data_ssh.LiveLoader([machine, provider])
    with loader._lock:
        loader._records[machine.source_id] = ["machine-row"]
        loader._records[provider.source_id] = ["provider-row"]
        loader._state[machine.source_id] = "ready"
        loader._state[provider.source_id] = "failed"

    assert set(loader._state) == {machine.source_id, provider.source_id}
    assert loader.state("Host-A", "WSL") == "ready"
    assert loader.state_for_source(provider.source_id) == "failed"
    assert loader.records_for_source(machine.source_id) == ["machine-row"]
    assert loader.records_for_source(provider.source_id) == ["provider-row"]


def test_loader_ignores_duplicate_canonical_source_ids(caplog):
    first = data_ssh.Source("Host-A", "WSL", None)
    duplicate = data_ssh.Source("host-a", "wsl", None)

    loader = data_ssh.LiveLoader([first, duplicate])

    assert loader._all_sources == [first]
    assert "ignoring duplicate Picker source id" in caplog.text


def test_local_fetch_passes_source_metadata_to_normalizer(monkeypatch):
    captured = {}

    def fake_load(machine, env, **kwargs):
        captured.update(machine=machine, env=env, **kwargs)
        return []

    monkeypatch.setattr(data_ssh.data_local, "load", fake_load)
    source = data_ssh.Source(
        "Virtual Target",
        "Provider",
        None,
        local=True,
        source_kind="provider-exec",
        source_id="provider-exec:example:target-1",
        source_label="Virtual target",
    )

    assert data_ssh._fetch(source) == []
    assert captured == {
        "machine": "Virtual Target",
        "env": "Provider",
        "classify": True,
        "source_kind": "provider-exec",
        "source_id": "provider-exec:example:target-1",
        "source_label": "Virtual target",
    }


def test_local_machine_matched_by_hostname_field(monkeypatch):
    """A re-keyed machine (friendly key + explicit ``hostname:`` COMPUTERNAME) is
    recognized as local via its hostname field -- so it becomes the in-process
    local tab and NO duplicate raw-COMPUTERNAME source leaks in."""
    entries = {
        "host-augloop1": _entry(
            "host-augloop1", "augloop1",
            [cfg.SSHEnvironment(name="windows", alias="host-augloop1",
                                shell="pwsh")],
            ssh_ready=True, alias="augloop1", hostname="cpc-tmich-oixui"),
    }
    _install_roster(
        monkeypatch, entries, machine="",                 # config.machine unset
        local_id=("cpc-tmich-oixui", "windows"))          # raw COMPUTERNAME

    sources = data_ssh._build_sources()
    assert len(sources) == 1
    local = sources[0]
    assert local.machine == "host-augloop1"   # roster key, not the raw COMPUTERNAME
    assert local.env == "Win"
    assert local.local is True
    assert local.argv is None             # in-process, not an SSH-to-self source
    # No raw-hostname fallback tab leaked in.
    assert not any(s.machine.lower() == "cpc-tmich-oixui" for s in sources)


def test_local_machine_needs_no_ssh_profile(monkeypatch):
    """A current machine with NO ssh environments still gets a local tab."""
    entries = {
        "anomalous-potato": _entry("anomalous-potato", "Anomalous-Potato", [], ssh_ready=False),
    }
    _install_roster(
        monkeypatch, entries, machine="anomalous-potato",
        local_id=("anomalous-potato", "windows"))

    sources = data_ssh._build_sources()
    assert len(sources) == 1
    local = sources[0]
    assert local.local is True
    assert local.ready is True
    assert local.machine == "anomalous-potato"
    assert local.env == "Win"  # derived from the running platform
    assert local.argv is None
    assert local.alias == ""


def test_local_env_is_local_even_when_machine_not_ssh_ready(monkeypatch):
    """The current machine's native env is local; its other env is a disabled
    tab because the machine is not ssh_ready."""
    envs = [
        cfg.SSHEnvironment(name="windows", alias="anomalous-potato", shell="pwsh"),
        cfg.SSHEnvironment(name="wsl", alias="anomalous-potato-wsl", shell="bash"),
    ]
    entries = {"anomalous-potato": _entry("anomalous-potato", "Anomalous-Potato", envs,
                                     ssh_ready=False)}
    _install_roster(
        monkeypatch, entries, machine="anomalous-potato",
        local_id=("anomalous-potato", "windows"))

    by = _by_key(data_ssh._build_sources())
    assert by[("anomalous-potato", "Win")].local is True
    assert by[("anomalous-potato", "Win")].ready is True
    # WSL of the current machine is not local and the machine is not ready:
    # disabled tab, never contacted.
    wsl = by[("anomalous-potato", "WSL")]
    assert wsl.local is False
    assert wsl.ready is False
    assert wsl.argv is None


def test_env_without_alias_is_disabled_not_connected(monkeypatch):
    """A remote env with no SSH profile (empty alias) becomes a disabled tab
    even when the machine is ssh_ready -- it is never connected to."""
    envs = [cfg.SSHEnvironment(name="linux", alias="", shell="bash")]
    entries = {
        "anomalous-potato": _entry(
            "anomalous-potato", "Anomalous-Potato",
            [cfg.SSHEnvironment(name="windows", alias="anomalous-potato",
                                shell="pwsh")]),
        "ghost": _entry("ghost", "Ghost", envs, ssh_ready=True),
    }
    _install_roster(
        monkeypatch, entries, machine="anomalous-potato",
        local_id=("anomalous-potato", "windows"))

    by = _by_key(data_ssh._build_sources())
    ghost = by[("ghost", "Linux")]
    assert ghost.ready is False
    assert ghost.argv is None
    assert ghost.local is False


def test_ready_remote_env_with_alias_is_connected(monkeypatch):
    """A remote ssh_ready env with a real alias gets an SSH argv (reachable)."""
    entries = {
        "anomalous-potato": _entry(
            "anomalous-potato", "Anomalous-Potato",
            [cfg.SSHEnvironment(name="windows", alias="anomalous-potato",
                                shell="pwsh")]),
        "mantis-counter": _entry(
            "mantis-counter", "Mantis-Counter",
            [cfg.SSHEnvironment(name="linux", alias="mantis-counter", shell="bash")],
            ssh_ready=True),
    }
    _install_roster(
        monkeypatch, entries, machine="anomalous-potato",
        local_id=("anomalous-potato", "windows"))

    by = _by_key(data_ssh._build_sources())
    mantis_counter = by[("mantis-counter", "Linux")]
    assert mantis_counter.ready is True
    assert mantis_counter.local is False
    assert mantis_counter.alias == "mantis-counter"
    assert mantis_counter.argv and mantis_counter.argv[0] == "ssh"
    assert "mantis-counter" in mantis_counter.argv


def _remote_roster(monkeypatch):
    """A local machine + one ready remote (Mantis-Counter/Linux) for op-argv tests."""
    entries = {
        "anomalous-potato": _entry(
            "anomalous-potato", "Anomalous-Potato",
            [cfg.SSHEnvironment(name="windows", alias="anomalous-potato",
                                shell="pwsh")]),
        "mantis-counter": _entry(
            "mantis-counter", "Mantis-Counter",
            [cfg.SSHEnvironment(name="linux", alias="mantis-counter", shell="bash")],
            ssh_ready=True),
    }
    _install_roster(
        monkeypatch, entries, machine="anomalous-potato",
        local_id=("anomalous-potato", "windows"))


def test_remote_op_argv_restart_uses_positional_id_and_json(monkeypatch):
    """The remote 'restart' op runs ``<proj> restart <id> --json`` (the CLI
    verb is ``restart`` even though the picker labels it 'Stop'); the id is
    positional, not ``--worktree-id``."""
    _remote_roster(monkeypatch)
    argv = data_ssh.remote_op_argv("mantis-counter", "Linux", "restart", "wt-xyz")
    assert argv is not None and argv[0] == "ssh"
    inner = argv[-1]
    assert "proj restart wt-xyz --json" in inner
    assert "--worktree-id" not in inner


def test_remote_op_argv_restart_local_returns_none(monkeypatch):
    """A local target yields no SSH argv (the caller runs it in-process)."""
    _remote_roster(monkeypatch)
    assert data_ssh.remote_op_argv(
        "anomalous-potato", "Win", "restart", "wt-xyz") is None


def test_remote_op_argv_finalize_uses_positional_id_and_json(monkeypatch):
    """The remote 'finalize' op runs ``<proj> finalize <id> --json`` -- the id
    is positional (the ``finalize`` CLI has no ``--worktree-id`` flag)."""
    _remote_roster(monkeypatch)
    argv = data_ssh.remote_op_argv("mantis-counter", "Linux", "finalize", "wt-xyz")
    assert argv is not None and argv[0] == "ssh"
    inner = argv[-1]
    assert "proj finalize wt-xyz --json" in inner
    assert "--worktree-id" not in inner


def test_recent_messages_argv_remote_builds_worktree_scoped_cli(monkeypatch):
    """The remote recent-messages fetch runs
    ``<proj> recent-messages --worktree <id> --limit N --json``."""
    _remote_roster(monkeypatch)
    argv = data_ssh.recent_messages_argv("mantis-counter", "Linux", "wt-xyz", limit=5)
    assert argv is not None and argv[0] == "ssh"
    inner = argv[-1]
    assert "proj recent-messages --worktree wt-xyz --limit 5 --json" in inner


def test_recent_messages_argv_local_returns_none(monkeypatch):
    """A local target yields no SSH argv (the caller loads it in-process)."""
    _remote_roster(monkeypatch)
    assert data_ssh.recent_messages_argv("anomalous-potato", "Win", "wt-xyz") is None


def test_list_sessions_argv_remote_builds_worktree_scoped_cli(monkeypatch):
    """The remote session-list fetch runs
    ``<proj> list-sessions --worktree <id> --json`` (the enriched registry with
    id + title + is_head, for the picker's diagnostic session list)."""
    _remote_roster(monkeypatch)
    argv = data_ssh.list_sessions_argv("mantis-counter", "Linux", "wt-xyz")
    assert argv is not None and argv[0] == "ssh"
    inner = argv[-1]
    assert "proj list-sessions --worktree wt-xyz --json" in inner


def test_list_sessions_argv_local_returns_none(monkeypatch):
    """A local target yields no SSH argv (the caller loads it in-process)."""
    _remote_roster(monkeypatch)
    assert data_ssh.list_sessions_argv("anomalous-potato", "Win", "wt-xyz") is None


def test_ssh_not_ready_remote_env_is_disabled(monkeypatch):
    """A ssh.ready:false machine's remote env stays a disabled tab."""
    entries = {
        "anomalous-potato": _entry(
            "anomalous-potato", "Anomalous-Potato",
            [cfg.SSHEnvironment(name="windows", alias="anomalous-potato",
                                shell="pwsh")]),
        "book2": _entry(
            "book2", "host-book2",
            [cfg.SSHEnvironment(name="windows", alias="book2", shell="pwsh")],
            ssh_ready=False),
    }
    _install_roster(
        monkeypatch, entries, machine="anomalous-potato",
        local_id=("anomalous-potato", "windows"))

    by = _by_key(data_ssh._build_sources())
    book2 = by[("book2", "Win")]
    assert book2.ready is False
    assert book2.argv is None
    assert book2.alias == "book2"


def test_copilot_false_machine_is_skipped(monkeypatch):
    entries = {
        "anomalous-potato": _entry(
            "anomalous-potato", "Anomalous-Potato",
            [cfg.SSHEnvironment(name="windows", alias="anomalous-potato",
                                shell="pwsh")]),
        "nas": _entry(
            "nas", "NAS",
            [cfg.SSHEnvironment(name="linux", alias="nas", shell="bash")],
            copilot=False),
    }
    _install_roster(
        monkeypatch, entries, machine="anomalous-potato",
        local_id=("anomalous-potato", "windows"))

    by = _by_key(data_ssh._build_sources())
    assert ("nas", "Linux") not in by


def test_absent_local_machine_still_gets_local_source(monkeypatch):
    """Defensive fail-safe: when this machine is entirely absent from
    machines.yaml (a stale or freshly-provisioned anchor whose self-entry
    hasn't landed yet), ``_build_sources`` still yields exactly one local
    source, so the picker always has a 'this host' tab and never crashes."""
    entries = {
        "mantis-counter": _entry(
            "mantis-counter", "Mantis-Counter",
            [cfg.SSHEnvironment(name="linux", alias="mantis-counter", shell="bash")],
            ssh_ready=True),
    }
    _install_roster(
        monkeypatch, entries, machine="ghosthost",
        local_id=("ghosthost", "windows"))
    # Pin the hostname-based fallback identity so the assertion is deterministic.
    monkeypatch.setattr(data_ssh.data_local, "LOCAL", ("ghosthost", "Win"))

    sources = data_ssh._build_sources()
    local_sources = [s for s in sources if s.local]
    assert len(local_sources) == 1
    local = local_sources[0]
    assert (local.machine, local.env) == ("ghosthost", "Win")
    assert local.argv is None
    assert local.ready is True
    # The remote roster entry is unaffected.
    assert ("mantis-counter", "Linux") in _by_key(sources)


# ── #1421 continuous background poll: LiveLoader.repoll_silent ────────────────

def _ready_loader(monkeypatch, records):
    """A LiveLoader with one ready source seeded with ``records``."""
    src = data_ssh.Source("M", "Win", ["ssh", "m", "list"], ready=True)
    loader = data_ssh.LiveLoader([src])
    with loader._lock:
        loader._state[src.cache_key] = "ready"
        loader._records[src.cache_key] = list(records)
    return loader, src


def _wait(pred, timeout=2.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not pred():
        time.sleep(0.01)
    return pred()


def test_repoll_silent_swaps_records_without_loading_flip(monkeypatch):
    loader, src = _ready_loader(monkeypatch, ["old"])
    calls = {"n": 0}

    def fake_fetch(source, runner=None):
        calls["n"] += 1
        return ["new"]

    monkeypatch.setattr(data_ssh, "_fetch", fake_fetch)
    assert loader.repoll_silent() == 1
    assert _wait(lambda: loader.records() == ["new"])
    assert loader.state("M", "Win") == "ready"      # never flipped to loading
    assert calls["n"] == 1
    assert _wait(lambda: not loader._refreshing)     # guard cleared


def test_repoll_silent_keeps_last_good_on_failure(monkeypatch):
    loader, src = _ready_loader(monkeypatch, ["old"])

    def boom(source, runner=None):
        raise RuntimeError("ssh down")

    monkeypatch.setattr(data_ssh, "_fetch", boom)
    assert loader.repoll_silent() == 1
    assert _wait(lambda: not loader._refreshing)
    assert loader.records() == ["old"]              # last-good preserved
    assert loader.state("M", "Win") == "ready"


def test_repoll_silent_skips_non_ready_and_cancelled(monkeypatch):
    loader, src = _ready_loader(monkeypatch, ["old"])
    called = {"n": 0}
    monkeypatch.setattr(
        data_ssh, "_fetch",
        lambda source, runner=None: called.__setitem__("n", called["n"] + 1) or ["x"])
    # Not ready -> skipped.
    with loader._lock:
        loader._state[src.cache_key] = "loading"
    assert loader.repoll_silent() == 0
    # Cancelled -> whole pass is a no-op.
    with loader._lock:
        loader._state[src.cache_key] = "ready"
    loader._cancelled.set()
    assert loader.repoll_silent() == 0
    assert called["n"] == 0


# ── #2102 remote-tab PR reconcile: argv flag + LiveLoader.reconcile_remote_prs ─

def test_argv_for_reconcile_includes_flag():
    argv = data_ssh._argv_for("bash", "mantis-counter", "proj",
                              classify=True, reconcile=True)
    inner = argv[-1]
    assert "--reconcile-prs" in inner
    assert "list --json" in inner


def test_argv_for_without_reconcile_omits_flag():
    argv = data_ssh._argv_for("bash", "mantis-counter", "proj", classify=True)
    assert "--reconcile-prs" not in argv[-1]


def test_argv_for_pwsh_uses_encoded_command():
    """Windows remotes must use -EncodedCommand, not -Command '<cmd>'.

    Under a dtssh Windows sshd (default shell cmd.exe), a single-quoted
    ``-Command`` is echoed as a string literal instead of executed, so the
    picker gets non-JSON back and the machine shows as failed. -EncodedCommand
    is immune to the remote shell's quote handling.
    """
    import base64

    argv = data_ssh._argv_for("pwsh", "host-dev6", "dotfiles", classify=True)
    assert argv[0] == "ssh"
    # Hardening options sit between "ssh" and the alias; the alias is now the
    # second-to-last element and the remote command stays last (dotfiles#1702).
    assert argv[-2] == "host-dev6"
    remote = argv[-1]
    assert remote.startswith("pwsh -NoProfile -WindowStyle Hidden -EncodedCommand ")
    assert "-Command '" not in remote
    enc = remote.split("-EncodedCommand ", 1)[1]
    decoded = base64.b64decode(enc).decode("utf-16-le")
    # Windows/pwsh targets also pull the machine's other-platform (WSL) worktrees.
    assert decoded == (
        "dotfiles list --json --classify --mux-details --include-other-platforms"
    )


def test_wrap_remote_pwsh_uses_encoded_command():
    import base64

    argv = data_ssh._wrap_remote("pwsh", "host-cloud1", "dotfiles cleanup --json")
    remote = argv[-1]
    assert remote.startswith("pwsh -NoProfile -WindowStyle Hidden -EncodedCommand ")
    enc = remote.split("-EncodedCommand ", 1)[1]
    assert base64.b64decode(enc).decode("utf-16-le") == "dotfiles cleanup --json"
    # bash path stays a plain -lc invocation
    b = data_ssh._wrap_remote("bash", "mantis-counter", "dotfiles cleanup --json")
    assert b[-1] == "bash -lc 'dotfiles cleanup --json'"


def test_remote_argv_carries_ssh_hardening():
    """Every remote argv self-limits so an unreachable peer can't leak an
    immortal orphaned ssh child (dotfiles#1702).

    The hardening options sit between ``ssh`` and the alias -- so ``argv[0]`` is
    still ``ssh``, the alias is second-to-last, and the remote command stays last
    -- and are present for both the list-fetch (``_argv_for``) and the generic
    (``_wrap_remote``) builders, on both pwsh and bash targets.
    """
    def _opts(argv):
        # the "-o KEY=VAL" pairs between ssh and the alias
        pairs = {}
        i = 1
        while i < len(argv) - 2 and argv[i] == "-o":
            k, _, v = argv[i + 1].partition("=")
            pairs[k] = v
            i += 2
        return pairs

    for argv in (
        data_ssh._argv_for("pwsh", "host-dev6", "dotfiles", classify=True),
        data_ssh._argv_for("bash", "mantis-counter", "proj", classify=True),
        data_ssh._wrap_remote("pwsh", "host-cloud1", "dotfiles cleanup --json"),
        data_ssh._wrap_remote("bash", "mantis-counter", "dotfiles cleanup --json"),
    ):
        assert argv[0] == "ssh"
        opts = _opts(argv)
        assert opts.get("BatchMode") == "yes"
        assert "ConnectTimeout" in opts and int(opts["ConnectTimeout"]) > 0
        assert "ServerAliveInterval" in opts and "ServerAliveCountMax" in opts


def test_classify_fallback_is_encoding_aware(monkeypatch):
    """The classify-unsupported retry must strip --classify from a pwsh
    -EncodedCommand argv too (the flag lives inside the base64 blob, so a plain
    str.replace would be a no-op and the retry would resend the same command)."""
    import base64

    argv = data_ssh._argv_for("pwsh", "host-cloud1", "dotfiles", classify=True)

    # Direct: decode -> drop --classify -> re-encode, still EncodedCommand.
    stripped = data_ssh._drop_classify_arg(argv)
    remote = stripped[-1]
    assert remote.startswith("pwsh -NoProfile -WindowStyle Hidden -EncodedCommand ")
    dec = base64.b64decode(
        remote.split("-EncodedCommand ", 1)[1]).decode("utf-16-le")
    assert "--classify" not in dec
    assert dec == "dotfiles list --json --mux-details --include-other-platforms"

    # End-to-end via _fetch: the first call errors as classify-unsupported; the
    # retry (decoded, no --classify) succeeds and use_classify is persisted off.
    calls = []

    class _Proc:
        def __init__(self, rc, stdout="", stderr=""):
            self.returncode = rc
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(a, timeout):
        calls.append(list(a))
        d = base64.b64decode(
            a[-1].split("-EncodedCommand ", 1)[1]).decode("utf-16-le")
        if "--classify" in d:
            return _Proc(2, stderr="error: unrecognized arguments: --classify")
        return _Proc(0, stdout='{"worktrees": []}')

    monkeypatch.setattr(data_ssh, "_run", fake_run)
    src = data_ssh.Source("Cloud1", "Win", argv, ready=True)
    assert data_ssh._fetch(src) == []
    assert len(calls) == 2
    d1 = base64.b64decode(
        calls[1][-1].split("-EncodedCommand ", 1)[1]).decode("utf-16-le")
    assert "--classify" not in d1
    assert src.use_classify is False


def test_reconcile_remote_prs_runs_reconcile_argv_and_swaps(monkeypatch):
    loader, src = _ready_loader(monkeypatch, ["old"])
    seen = {"argv": None, "n": 0}

    def fake_fetch(source, runner=None, *, argv=None):
        seen["n"] += 1
        seen["argv"] = argv
        return ["reconciled"]

    monkeypatch.setattr(data_ssh, "_fetch", fake_fetch)
    assert loader.reconcile_remote_prs() == 1
    assert _wait(lambda: loader.records() == ["reconciled"])
    assert loader.state("M", "Win") == "ready"              # never flips to loading
    assert seen["argv"] is not None
    assert "--reconcile-prs" in seen["argv"][-1]            # ran the reconcile argv
    assert _wait(lambda: not loader._refreshing)            # guard cleared
    # One-shot per source: a second pass is a no-op.
    assert loader.reconcile_remote_prs() == 0
    assert seen["n"] == 1


def test_reconcile_remote_prs_skips_local(monkeypatch):
    local = data_ssh.Source("M", "Win", None, local=True, ready=True)
    loader = data_ssh.LiveLoader([local])
    with loader._lock:
        loader._state[local.cache_key] = "ready"
    called = {"n": 0}
    monkeypatch.setattr(
        data_ssh, "_fetch",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or ["x"])
    assert loader.reconcile_remote_prs() == 0
    assert called["n"] == 0


def test_reconcile_remote_prs_keeps_last_good_on_failure(monkeypatch):
    loader, src = _ready_loader(monkeypatch, ["old"])

    def boom(source, runner=None, *, argv=None):
        raise RuntimeError("ssh down")

    monkeypatch.setattr(data_ssh, "_fetch", boom)
    assert loader.reconcile_remote_prs() == 1
    assert _wait(lambda: not loader._refreshing)
    assert loader.records() == ["old"]                     # last-good preserved
    assert loader.state("M", "Win") == "ready"


def test_reconcile_remote_prs_noop_when_cancelled(monkeypatch):
    loader, src = _ready_loader(monkeypatch, ["old"])
    monkeypatch.setattr(
        data_ssh, "_fetch",
        lambda *a, **k: ["x"])
    loader._cancelled.set()
    assert loader.reconcile_remote_prs() == 0


# -- display_name -> registry key resolution (registered-pivot {machine}) ------


def test_machine_key_map_maps_display_names_to_registry_keys(monkeypatch):
    entries = {
        "anomalous-potato": _entry("anomalous-potato", "Anomalous-Potato", []),
        "emancipation-cube": _entry("emancipation-cube", "Emancipation-Cube", []),
    }
    _install_roster(
        monkeypatch, entries, machine="anomalous-potato",
        local_id=("anomalous-potato", "windows"))

    assert data_ssh.machine_key_map() == {
        "Anomalous-Potato": "anomalous-potato",
        "Emancipation-Cube": "emancipation-cube",
    }


def test_machine_key_translates_display_to_key(monkeypatch):
    entries = {"anomalous-potato": _entry("anomalous-potato", "Anomalous-Potato", [])}
    _install_roster(
        monkeypatch, entries, machine="anomalous-potato",
        local_id=("anomalous-potato", "windows"))

    # A tab's display name resolves to the canonical (lowercase) identity that
    # agent-dispatch and the SSH alias expect.
    assert data_ssh.machine_key("Anomalous-Potato") == "anomalous-potato"


def test_machine_key_falls_back_to_display_when_unknown(monkeypatch):
    entries = {"anomalous-potato": _entry("anomalous-potato", "Anomalous-Potato", [])}
    _install_roster(
        monkeypatch, entries, machine="anomalous-potato",
        local_id=("anomalous-potato", "windows"))

    # An unknown display name (roster gap) degrades to itself, not None.
    assert data_ssh.machine_key("Unlisted") == "Unlisted"
    assert data_ssh.machine_key(None) is None


def test_machine_key_map_empty_on_unreadable_roster(monkeypatch):
    def _boom(_anchor):
        raise FileNotFoundError("no machines.yaml")

    monkeypatch.setattr(data_ssh.cfg, "load_config", lambda: types.SimpleNamespace(
        default_repo=types.SimpleNamespace(anchor="/repo"), machine="m"))
    monkeypatch.setattr(data_ssh.cfg, "load_machines_yaml", _boom)
    assert data_ssh.machine_key_map() == {}
    assert data_ssh.machine_key("Anything") == "Anything"


# ── SSH resolution diagnostics: picker-ssh.log enumeration ───────────────────

def test_remote_cmd_str_decodes_encoded_command():
    """The Windows remote form is logged decoded, not as opaque base64."""
    argv = data_ssh._argv_for("pwsh", "host-cloud1", "dotfiles", classify=True)
    rendered = data_ssh._remote_cmd_str(argv)
    assert "(decoded) dotfiles list --json" in rendered
    assert "--include-other-platforms" in rendered
    # bash form is passed through verbatim (nothing to decode).
    bash = data_ssh._argv_for("bash", "host", "dotfiles", classify=True)
    assert data_ssh._remote_cmd_str(bash).endswith(
        "bash -lc 'dotfiles list --json --classify --mux-details'")


def test_fetch_raises_remote_fetch_error_with_detail(monkeypatch):
    """A nonzero remote exit surfaces returncode + stderr + argv for logging."""
    class _Proc:
        def __init__(self, rc, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = rc, stdout, stderr

    argv = ["ssh", "host", "dotfiles list --json"]
    monkeypatch.setattr(
        data_ssh, "_run",
        lambda a, timeout: _Proc(255, stderr="ssh: Could not resolve hostname"))
    src = data_ssh.Source("Box", "Win", argv, ready=True, alias="host")
    with pytest.raises(data_ssh._RemoteFetchError) as ei:
        data_ssh._fetch(src)
    exc = ei.value
    assert exc.returncode == 255
    assert "Could not resolve hostname" in exc.stderr
    assert exc.argv == argv


def test_fetch_raises_remote_fetch_error_on_unparseable_output(monkeypatch):
    class _Proc:
        def __init__(self, rc, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = rc, stdout, stderr

    monkeypatch.setattr(
        data_ssh, "_run", lambda a, timeout: _Proc(0, stdout="not json at all"))
    src = data_ssh.Source("Box", "Win", ["ssh", "h", "x"], ready=True)
    with pytest.raises(data_ssh._RemoteFetchError) as ei:
        data_ssh._fetch(src)
    assert "unparseable output" in str(ei.value)


def test_log_load_header_enumerates_roster_with_skip_reasons(monkeypatch, tmp_path):
    logf = tmp_path / "picker-ssh.log"
    monkeypatch.setattr(data_ssh, "_ssh_log_path", lambda: logf)
    sources = [
        data_ssh.Source("book2", "Win", None, local=True, ready=True),
        data_ssh.Source("dev6", "Win", ["ssh", "d", "x"], ready=True,
                        alias="host-dev6"),
        data_ssh.Source("cloud1", "Win", None, ready=False, alias="",
                        shell="pwsh"),
        data_ssh.Source("augloop1", "Win", None, ready=False,
                        alias="host-augloop1", shell="pwsh"),
    ]
    loader = data_ssh.LiveLoader(sources)
    loader._log_load_header()
    text = logf.read_text(encoding="utf-8")
    assert "1 remote to resolve, 1 local, 2 skipped" in text
    assert "LOCAL   book2/Win" in text
    assert "RESOLVE dev6/Win alias=host-dev6" in text
    assert "SKIP    cloud1/Win (no SSH alias/profile)" in text
    assert "SKIP    augloop1/Win (machine not ssh.ready in machines.yaml)" in text


def test_load_one_failure_logs_reason(monkeypatch, tmp_path):
    logf = tmp_path / "picker-ssh.log"
    monkeypatch.setattr(data_ssh, "_ssh_log_path", lambda: logf)

    def boom(source, runner=None, classify=True, argv=None, timeout=None):
        raise data_ssh._RemoteFetchError(
            "exit 1", returncode=1, stderr="boom-detail",
            argv=["ssh", "dev6", "cmd"])

    monkeypatch.setattr(data_ssh, "_fetch", boom)
    src = data_ssh.Source("dev6", "Win", ["ssh", "dev6", "cmd"], ready=True,
                          alias="host-dev6")
    loader = data_ssh.LiveLoader([src])
    loader._load_remote_two_phase(src)
    assert loader.state("dev6", "Win") == "failed"
    text = logf.read_text(encoding="utf-8")
    assert "dev6/Win [load]" in text
    assert "exit=1 alias=host-dev6" in text
    assert "stderr| boom-detail" in text


# ── remote two-phase load: fast enumeration then follow-up classification ─────

def _classify_src():
    """A ready remote whose argv carries --classify (so phase-1 strips it)."""
    argv = data_ssh._argv_for("pwsh", "host-dev6", "dotfiles", classify=True)
    return data_ssh.Source("dev6", "Win", argv, ready=True, alias="host-dev6")


def test_remote_two_phase_paints_fast_then_classifies(monkeypatch, tmp_path):
    monkeypatch.setattr(data_ssh, "_ssh_log_path", lambda: tmp_path / "l.log")
    src = _classify_src()
    calls = []

    def fake_fetch(source, runner=None, classify=True, argv=None, timeout=None):
        # Phase 1 passes an explicit fast argv override; phase 2 uses source.argv.
        if argv is not None:
            calls.append(("fast", timeout))
            return ["f1"]
        calls.append(("classify", timeout))
        return ["c1", "c2"]

    monkeypatch.setattr(data_ssh, "_fetch", fake_fetch)
    loader = data_ssh.LiveLoader([src])
    loader._load_remote_two_phase(src)

    assert loader.state("dev6", "Win") == "ready"
    assert loader.records() == ["c1", "c2"]          # classified rows swapped in
    assert [c[0] for c in calls] == ["fast", "classify"]
    # phase-1 uses the interactive timeout; phase-2 the longer classify budget.
    assert calls[0][1] is None
    assert calls[1][1] == max(src.timeout, data_ssh._CLASSIFY_TIMEOUT)


def test_remote_two_phase_keeps_fast_rows_when_classify_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(data_ssh, "_ssh_log_path", lambda: tmp_path / "l.log")
    src = _classify_src()

    def fake_fetch(source, runner=None, classify=True, argv=None, timeout=None):
        if argv is not None:
            return ["f1"]
        raise data_ssh._RemoteFetchError("exit 1", returncode=1)

    monkeypatch.setattr(data_ssh, "_fetch", fake_fetch)
    loader = data_ssh.LiveLoader([src])
    loader._load_remote_two_phase(src)

    # Enumeration survives a classify timeout: tab stays resolved on fast rows.
    assert loader.state("dev6", "Win") == "ready"
    assert loader.records() == ["f1"]
    text = (tmp_path / "l.log").read_text(encoding="utf-8")
    assert "[classify]" in text


def test_remote_two_phase_fast_failure_fails_machine(monkeypatch, tmp_path):
    monkeypatch.setattr(data_ssh, "_ssh_log_path", lambda: tmp_path / "l.log")
    src = _classify_src()

    def fake_fetch(source, runner=None, classify=True, argv=None, timeout=None):
        raise data_ssh._RemoteFetchError(
            "ssh: Could not resolve hostname", returncode=255)

    monkeypatch.setattr(data_ssh, "_fetch", fake_fetch)
    loader = data_ssh.LiveLoader([src])
    loader._load_remote_two_phase(src)

    assert loader.state("dev6", "Win") == "failed"
    assert "[fast]" in (tmp_path / "l.log").read_text(encoding="utf-8")


def test_remote_single_pass_when_classify_unsupported(monkeypatch, tmp_path):
    monkeypatch.setattr(data_ssh, "_ssh_log_path", lambda: tmp_path / "l.log")
    # A remote whose argv already lacks --classify (older agent-worktrees):
    # nothing to strip, so it loads in exactly one pass.
    argv = data_ssh._argv_for("pwsh", "host-old", "dotfiles", classify=False)
    src = data_ssh.Source("old", "Win", argv, ready=True, alias="host-old")
    calls = []

    def fake_fetch(source, runner=None, classify=True, argv=None, timeout=None):
        calls.append(argv)
        return ["x"]

    monkeypatch.setattr(data_ssh, "_fetch", fake_fetch)
    loader = data_ssh.LiveLoader([src])
    loader._load_remote_two_phase(src)

    assert loader.state("old", "Win") == "ready"
    assert loader.records() == ["x"]
    assert len(calls) == 1                            # no second phase


# ── remote NDJSON streaming load (Phase A) ────────────────────────────────────


class _FakeStreamProc:
    """Stand-in for a streaming Popen: yields pre-baked NDJSON lines on stdout."""

    def __init__(self, lines, err="", rc=0):
        self.stdout = iter(list(lines))
        self._err = err
        self.returncode = rc

    def communicate(self, timeout=None):
        return ("", self._err)

    def poll(self):
        return self.returncode

    def kill(self):
        pass


def _nd(obj):
    return _json.dumps(obj) + "\n"


def _stream_src():
    argv = data_ssh._argv_for("pwsh", "host-dev6", "dotfiles", classify=True)
    return data_ssh.Source("dev6", "Win", argv, ready=True, alias="host-dev6")


def test_stream_paints_fast_then_classified(monkeypatch, tmp_path):
    monkeypatch.setattr(data_ssh, "_ssh_log_path", lambda: tmp_path / "l.log")
    # Keep the record identity trivial so we can assert the fast->classified swap.
    monkeypatch.setattr(
        data_ssh.derive, "norm", lambda wt, m, e, **source: dict(wt)
    )
    lines = [
        _nd({"type": "begin", "count": 2}),
        _nd({"type": "worktree", "phase": "fast", "wt": {"id": "A"}}),
        _nd({"type": "worktree", "phase": "fast", "wt": {"id": "B"}}),
        _nd({"type": "worktree", "phase": "classified",
             "wt": {"id": "A", "state": "clean"}}),
        _nd({"type": "worktree", "phase": "classified",
             "wt": {"id": "B", "state": "unused"}}),
        _nd({"type": "done", "count": 2}),
    ]
    src = _stream_src()
    loader = data_ssh.LiveLoader([src])
    monkeypatch.setattr(loader, "_spawn_stream", lambda argv: _FakeStreamProc(lines))

    handled = loader._load_remote_stream(src, loader._gen[src.cache_key])
    assert handled is True
    assert loader.state("dev6", "Win") == "ready"
    recs = loader.records()
    assert [r["id"] for r in recs] == ["A", "B"]         # first-seen order kept
    assert recs[0]["state"] == "clean"                   # classified swapped in
    assert recs[1]["state"] == "unused"
    assert "streamed 2 worktree(s)" in (tmp_path / "l.log").read_text("utf-8")


def test_stream_empty_remote_resolves_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(data_ssh, "_ssh_log_path", lambda: tmp_path / "l.log")
    lines = [_nd({"type": "begin", "count": 0}), _nd({"type": "done", "count": 0})]
    src = _stream_src()
    loader = data_ssh.LiveLoader([src])
    monkeypatch.setattr(loader, "_spawn_stream", lambda argv: _FakeStreamProc(lines))

    handled = loader._load_remote_stream(src, loader._gen[src.cache_key])
    assert handled is True
    assert loader.state("dev6", "Win") == "ready"        # empty is success
    assert loader.records() == []


def test_stream_unsupported_returns_false_for_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(data_ssh, "_ssh_log_path", lambda: tmp_path / "l.log")
    proc = _FakeStreamProc(
        [], err="error: unrecognized arguments: --stream", rc=2)
    src = _stream_src()
    loader = data_ssh.LiveLoader([src])
    monkeypatch.setattr(loader, "_spawn_stream", lambda argv: proc)

    handled = loader._load_remote_stream(src, loader._gen[src.cache_key])
    assert handled is False                              # caller falls back
    assert loader.state("dev6", "Win") != "ready"


def test_stream_hard_failure_marks_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(data_ssh, "_ssh_log_path", lambda: tmp_path / "l.log")
    proc = _FakeStreamProc(
        [], err="ssh: Could not resolve hostname", rc=255)
    src = _stream_src()
    loader = data_ssh.LiveLoader([src])
    monkeypatch.setattr(loader, "_spawn_stream", lambda argv: proc)

    handled = loader._load_remote_stream(src, loader._gen[src.cache_key])
    assert handled is True                               # owned, not fallback
    assert loader.state("dev6", "Win") == "failed"
    assert "[stream]" in (tmp_path / "l.log").read_text("utf-8")


def test_load_one_prefers_stream_then_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(data_ssh, "_ssh_log_path", lambda: tmp_path / "l.log")
    monkeypatch.setenv("AGENT_WORKTREES_PICKER_STREAM", "1")
    src = _stream_src()
    loader = data_ssh.LiveLoader([src])
    calls = []
    # Streaming declines (old remote) -> _load_one must fall back to two-phase.
    monkeypatch.setattr(loader, "_load_remote_stream",
                        lambda s, gen: calls.append("stream") or False)
    monkeypatch.setattr(loader, "_load_remote_two_phase",
                        lambda s: calls.append("two_phase"))
    loader._load_one(src)
    assert calls == ["stream", "two_phase"]


def test_load_one_skips_stream_when_disabled(monkeypatch):
    # Default (gate off): _load_one goes straight to two-phase, never streams.
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_STREAM", raising=False)
    src = _stream_src()
    loader = data_ssh.LiveLoader([src])
    calls = []
    monkeypatch.setattr(loader, "_load_remote_stream",
                        lambda s, gen: calls.append("stream") or True)
    monkeypatch.setattr(loader, "_load_remote_two_phase",
                        lambda s: calls.append("two_phase"))
    loader._load_one(src)
    assert calls == ["two_phase"]


def test_stream_enabled_reads_env(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_STREAM", raising=False)
    assert data_ssh._stream_enabled() is False
    for v in ("1", "true", "YES", "on"):
        monkeypatch.setenv("AGENT_WORKTREES_PICKER_STREAM", v)
        assert data_ssh._stream_enabled() is True
    monkeypatch.setenv("AGENT_WORKTREES_PICKER_STREAM", "0")
    assert data_ssh._stream_enabled() is False


def test_stream_argv_adds_stream_flag():
    src = _stream_src()
    argv = data_ssh._stream_argv(src)
    rendered = data_ssh._remote_cmd_str(argv)
    assert "list --json" in rendered and "--stream" in rendered


def test_records_serves_last_good_during_reload():
    """records() keeps a source's last-good rows while it is reloading, so a
    refresh never blanks the list (dotfiles#948 follow-up)."""
    loader = data_ssh.LiveLoader(sources=[])
    key = ("host", "Win")
    loader._records[key] = [{"id4": "aaaa"}, {"id4": "bbbb"}]
    # ready -> served
    loader._state[key] = "ready"
    assert len(loader.records()) == 2
    # loading (a reload in flight) with last-good rows -> STILL served (no blank)
    loader._state[key] = "loading"
    assert len(loader.records()) == 2
    # failed with last-good rows -> still served (transient failure keeps rows)
    loader._state[key] = "failed"
    assert len(loader.records()) == 2


def test_records_hides_source_that_never_resolved():
    """A source still on its initial connect (loading, no records) contributes
    nothing -- the spinner shows, not stale/empty rows."""
    loader = data_ssh.LiveLoader(sources=[])
    key = ("host", "Win")
    loader._records[key] = []
    loader._state[key] = "loading"
    assert loader.records() == []
