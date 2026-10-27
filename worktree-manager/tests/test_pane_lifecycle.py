import os

import pytest

from worktree_manager import mux_mapping_registry as mappings, pane_lifecycle


@pytest.fixture(autouse=True)
def non_serving_observation(monkeypatch):
    from worktree_manager import mux_daemon_live

    def publish(_entry, *, ensure_monitor, request_deadline_s):
        assert ensure_monitor is False
        assert request_deadline_s == 2.0
        return {"applied": False, "reason": "monitor-unavailable"}

    monkeypatch.setattr(mux_daemon_live, "publish_live_observation", publish)


def entry(pane="%1", **overrides):
    return {
        "project": "example", "worktree_id": "worktree",
        "mux_session": "wt-worktree", "mux_bin": "tmux",
        "session_incarnation": "$1:123",
        "panes": [{"pane_id": pane, "role": "cli", "live": True}],
        "mapping_revision": 1, **overrides,
    }


@pytest.mark.parametrize("alias_kind", ["dotdot", "case", "different"])
def test_start_uses_logical_path_identity_without_losing_existing_exit(tmp_path, alias_kind):
    if alias_kind == "case" and os.name != "nt":
        pytest.skip("Case aliases require Windows path semantics")
    (tmp_path / "sub").mkdir()
    alias = {
        "dotdot": str(tmp_path / "sub" / ".."),
        "case": str(tmp_path).swapcase(),
        "different": str(tmp_path / "sub"),
    }[alias_kind]
    registry = mappings.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register_next(entry(
        worktree_path=str(tmp_path), native_session_incarnation="$1:123",
        panes=[{"pane_id": pane, "role": "cli", "live": True} for pane in ("%1", "%2")],
    ))
    registry.remove("example", "worktree", session_incarnation="$1:123", pane_id="%2", exit_code=7)
    before = registry.get("example", "worktree")
    result = pane_lifecycle.admit(
        "start", root=tmp_path, expected_installation=None,
        project="example", worktree_id="worktree", mux_session="wt-worktree",
        session_incarnation="$1:123", pane_id="%3", worktree_path=alias, mux_bin="tmux",
    )["result"]
    current = registry.get("example", "worktree")
    if alias_kind == "different":
        assert result == {"applied": False, "reason": "incumbent-mapping"}
        assert current == before
    else:
        assert result["applied"]
        assert current["panes"] == [
            *before["panes"], {"pane_id": "%3", "role": "cli", "live": True},
        ]
        assert current["observed_end"] == before["observed_end"]
        assert current["native_session_incarnation"] == "$1:123"


def test_delayed_exit_does_not_retire_a_successor_in_the_same_mux(tmp_path):
    registry = mappings.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register_next(entry())
    registry.register_next(entry("%2"))
    before = registry.get("example", "worktree")
    result = registry.remove(
        "example", "worktree", mux_session="wt-worktree",
        session_incarnation="$1:123", pane_id="%1",
    )
    assert result["applied"] is False
    assert result["reason"] == "pane_mismatch"
    assert registry.get("example", "worktree") == before
    assert registry.remove(
        "example", "worktree", mux_session="wt-worktree",
        session_incarnation="$1:123", pane_id="%2",
    ) == {"applied": True}
    assert registry.get("example", "worktree")["live"] is False


@pytest.mark.parametrize("panes", [
    [],
    [{"pane_id": "%1", "role": "head", "live": True}],
    [{"pane_id": "%1", "role": "cli", "live": False}],
])
def test_unconfirmed_cli_panes_are_not_retired(tmp_path, panes):
    registry = mappings.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register_next(entry(panes=panes))
    before = registry.get("example", "worktree")
    assert registry.remove(
        "example", "worktree", session_incarnation="$1:123", pane_id="%1",
    )["reason"] == "pane_mismatch"
    assert registry.get("example", "worktree") == before


def test_native_session_identity_still_fences_reused_pane_ids(tmp_path):
    registry = mappings.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register_next(entry(session_incarnation="$2:456"))
    assert registry.remove(
        "example", "worktree", session_incarnation="$1:123", pane_id="%1",
    )["reason"] == "session_mismatch"
    assert registry.get("example", "worktree")["live"] is True


@pytest.mark.parametrize("current_live", [False, True])
@pytest.mark.parametrize("native", ["incoming", "incumbent", "unknown"])
def test_delayed_start_cannot_replace_another_incarnations_exit_without_native_proof(
    tmp_path, monkeypatch, current_live, native,
):
    import subprocess
    from worktree_manager import mux_discovery

    path = tmp_path / "mux-mapping.json"
    registry = mappings.MuxMappingRegistry(path)
    registry.register_next(entry("%2", session_incarnation="$2:456", worktree_path=str(tmp_path)))
    if not current_live:
        registry.remove("example", "worktree", session_incarnation="$2:456", pane_id="%2", exit_code=7)
    before = path.read_bytes()
    probes = []

    def inventory(command, **kwargs):
        probes.append(command)
        if native == "unknown":
            raise TimeoutError("native pane unconfirmed")
        pane, incarnation = ("%1", "$1:123") if native == "incoming" else ("%2", "$2:456")
        return subprocess.CompletedProcess(
            command, 0, stdout=f"{pane}\t0\twt-worktree\t{incarnation}\t{tmp_path}\n",
        )

    monkeypatch.setattr(mux_discovery, "run_mux_child", inventory)
    result = pane_lifecycle.admit(
        "start", root=tmp_path, expected_installation=None,
        project="example", worktree_id="worktree", mux_session="wt-worktree",
        session_incarnation="$1:123", pane_id="%1", worktree_path=str(tmp_path), mux_bin="tmux",
    )
    admitted = not current_live and native == "incoming"
    assert result["result"]["applied"] is admitted
    if admitted:
        current = registry.get("example", "worktree")
        assert current["live"] is True
        assert current["session_incarnation"] == "$1:123"
        assert "observed_end" not in current
    else:
        assert path.read_bytes() == before
    assert bool(probes) is (not current_live)


def test_start_rechecks_native_proof_after_tombstone_changes_during_probe(tmp_path, monkeypatch):
    import subprocess
    from worktree_manager import mux_discovery

    registry = mappings.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register_next(entry("%2", session_incarnation="$2:456", worktree_path=str(tmp_path)))
    registry.remove("example", "worktree", session_incarnation="$2:456", pane_id="%2", exit_code=7)
    probes = []

    def inventory(command, **kwargs):
        probes.append(command)
        if len(probes) == 1:
            successor = entry("%3", session_incarnation="$3:789", worktree_path=str(tmp_path))
            successor.pop("mapping_revision")
            assert registry.register_next(successor)["applied"]
            assert registry.remove(
                "example", "worktree", session_incarnation="$3:789", pane_id="%3", exit_code=0,
            )["applied"]
            pane, incarnation = "%1", "$1:123"
        else:
            pane, incarnation = "%3", "$3:789"
        return subprocess.CompletedProcess(
            command, 0, stdout=f"{pane}\t0\twt-worktree\t{incarnation}\t{tmp_path}\n",
        )

    monkeypatch.setattr(mux_discovery, "run_mux_child", inventory)
    result = pane_lifecycle.admit(
        "start", root=tmp_path, expected_installation=None,
        project="example", worktree_id="worktree", mux_session="wt-worktree",
        session_incarnation="$1:123", pane_id="%1", worktree_path=str(tmp_path), mux_bin="tmux",
    )
    assert result["result"] == {"applied": False, "reason": "stale-incarnation"}
    assert len(probes) == 2
    current = registry.get("example", "worktree")
    assert current["live"] is False
    assert current["observed_end"]["pane_id"] == "%3"
    assert current["observed_end"]["exit_code"] == 0


def test_pane_removal_requires_stored_native_session_evidence(tmp_path):
    registry = mappings.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register_next(entry(session_incarnation=""))
    assert registry.remove(
        "example", "worktree", mux_session="wt-worktree",
        session_incarnation="$1:123", pane_id="%1",
    )["reason"] == "session_mismatch"
    assert registry.get("example", "worktree")["live"] is True


def test_pane_removal_does_not_claim_delivery_against_a_corrupt_registry(tmp_path):
    path = tmp_path / "mux-mapping.json"
    path.write_bytes(b"invalid")
    registry = mappings.MuxMappingRegistry(path)
    assert registry.remove(
        "example", "worktree", session_incarnation="$1:123", pane_id="%1",
    ) == {"applied": False, "reason": "registry-unavailable"}
    assert path.read_bytes() == b"invalid"


@pytest.mark.parametrize("pane,incarnation", [("", "$1:123"), ("%x", "$1:123"), ("%1", "")])
def test_pane_owned_removal_never_falls_back_to_an_unfenced_remove(tmp_path, pane, incarnation):
    with pytest.raises(ValueError, match="native pane and session identities"):
        mappings.remove_mapping(
            "example", "worktree", pane_id=pane, session_incarnation=incarnation, root=tmp_path,
        )
    assert not (tmp_path / "mux-mapping.json").exists()


def test_remove_helper_forwards_the_pane_fence(tmp_path):
    mappings.register_next_mapping(entry("%2"), root=tmp_path)
    assert mappings.remove_mapping(
        "example", "worktree", pane_id="%1", session_incarnation="$1:123", root=tmp_path,
    )["reason"] == "pane_mismatch"


def test_boundary_admission_is_synchronous_and_does_not_boot_services(tmp_path, monkeypatch):
    from worktree_manager import mux_daemon

    monkeypatch.setattr(
        mux_daemon, "ensure_daemon_running",
        lambda *_args, **_kwargs: pytest.fail("pane boundary must not start a service"),
    )
    arguments = {
        "root": tmp_path, "project": "example", "worktree_id": "worktree",
        "mux_session": "wt-worktree", "session_incarnation": "$1:123", "pane_id": "%1",
    }
    started = pane_lifecycle.admit(
        "start", expected_installation=None, worktree_path=str(tmp_path / "worktree"),
        mux_bin="tmux", **arguments,
    )
    assert started["result"]["applied"] is True
    assert mappings.get_mapping("example", "worktree", root=tmp_path)["live"] is True
    ended = pane_lifecycle.admit(
        "end", expected_installation=started["installation"], exit_code=7, **arguments,
    )
    assert ended["result"]["applied"] is True
    persisted = mappings.get_mapping("example", "worktree", root=tmp_path)
    assert persisted["live"] is False
    assert persisted["observed_end"]["pane_id"] == "%1"
    assert persisted["observed_end"]["session_incarnation"] == "$1:123"
    assert persisted["observed_end"]["exit_code"] == 7


def test_cancel_retires_only_its_unstarted_pane_without_inventing_a_child_exit(tmp_path):
    arguments = dict(
        root=tmp_path, project="example", worktree_id="worktree",
        mux_session="wt-worktree", session_incarnation="$1:123",
    )
    for pane in ("%1", "%2"):
        assert pane_lifecycle.admit(
            "start", expected_installation=None, pane_id=pane,
            worktree_path=str(tmp_path), mux_bin="tmux", **arguments,
        )["result"]["applied"]
    assert pane_lifecycle.admit(
        "cancel", expected_installation=pane_lifecycle.installation_identity(tmp_path),
        pane_id="%2", **arguments,
    )["result"]["applied"]
    current = mappings.get_mapping("example", "worktree", root=tmp_path)
    assert current["live"] is True
    assert current["panes"] == [
        {"pane_id": "%1", "role": "cli", "live": True},
        {"pane_id": "%2", "role": "cli", "live": False},
    ]
    assert "observed_end" not in current


@pytest.mark.parametrize("metadata", [{"exit_code": 2}, {"runtime": 0}, {"launch_id": "unstarted"}])
def test_cancel_rejects_observed_child_exit_metadata(tmp_path, metadata):
    with pytest.raises(ValueError, match="cannot claim a child exit"):
        pane_lifecycle.admit(
            "cancel", root=tmp_path, expected_installation=pane_lifecycle.installation_identity(tmp_path),
            project="example", worktree_id="worktree", mux_session="wt-worktree",
            session_incarnation="$1:123", pane_id="%1", **metadata,
        )
    assert not (tmp_path / "mux-mapping.json").exists()


@pytest.mark.parametrize("old_pane,old_session", [("%1", "$1:123"), ("%1", "$2:456")])
def test_delayed_exit_cannot_overwrite_successor_tombstone(tmp_path, old_pane, old_session):
    path = tmp_path / "mux-mapping.json"
    registry = mappings.MuxMappingRegistry(path)
    registry.register_next(entry("%2", session_incarnation="$2:456"))
    assert registry.remove(
        "example", "worktree", session_incarnation="$2:456", pane_id="%2", exit_code=0,
    )["applied"] is True
    before = path.read_bytes()
    assert registry.remove(
        "example", "worktree", session_incarnation=old_session, pane_id=old_pane, exit_code=7,
    )["applied"] is False
    assert path.read_bytes() == before


def test_duplicate_exit_is_read_only_and_cannot_change_exit_code(tmp_path):
    path = tmp_path / "mux-mapping.json"
    registry = mappings.MuxMappingRegistry(path)
    registry.register_next(entry())
    arguments = dict(session_incarnation="$1:123", pane_id="%1", exit_code=7)
    assert registry.remove("example", "worktree", **arguments)["applied"] is True
    before = path.read_bytes()
    assert registry.remove("example", "worktree", **arguments) == {
        "applied": True, "reason": "already-observed",
    }
    assert registry.remove(
        "example", "worktree", **dict(arguments, exit_code=0),
    )["applied"] is False
    assert path.read_bytes() == before


def test_pane_exit_cannot_revive_an_explicitly_retired_whole_mapping(tmp_path):
    registry = mappings.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register_next(entry(panes=[*entry("%1")["panes"], *entry("%2")["panes"]]))
    assert registry.remove("example", "worktree")["applied"]
    assert registry.remove(
        "example", "worktree", session_incarnation="$1:123", pane_id="%1", exit_code=7,
    )["applied"]
    current = registry.get("example", "worktree")
    assert current["live"] is False
    assert current["observed_end"]["exit_code"] == 7
    assert current["panes"][1] == {"pane_id": "%2", "role": "cli", "live": True}


@pytest.mark.parametrize("exiting_pane,survivor", [("%1", "%2"), ("%2", "%1")])
def test_start_and_exit_preserve_other_live_cli_panes(tmp_path, exiting_pane, survivor):
    arguments = {
        "root": tmp_path, "project": "example", "worktree_id": "worktree",
        "mux_session": "wt-worktree", "session_incarnation": "$1:123",
    }
    starts = [
        pane_lifecycle.admit(
            "start", expected_installation=None, worktree_path=str(tmp_path), mux_bin="tmux",
            pane_id=pane, **arguments,
        )
        for pane in ("%1", "%2")
    ]
    assert all(start["result"]["applied"] for start in starts)
    current = mappings.get_mapping("example", "worktree", root=tmp_path)
    assert {pane["pane_id"] for pane in current["panes"] if pane["live"]} == {"%1", "%2"}
    result = pane_lifecycle.admit(
        "end", expected_installation=starts[0]["installation"], exit_code=2,
        pane_id=exiting_pane, **arguments,
    )
    assert result["result"]["applied"] is True
    current = mappings.get_mapping("example", "worktree", root=tmp_path)
    assert current["live"] is True
    assert {pane["pane_id"] for pane in current["panes"] if pane["live"]} == {survivor}
    assert pane_lifecycle.admit(
        "end", expected_installation=starts[0]["installation"], exit_code=0,
        pane_id=survivor, **arguments,
    )["result"]["applied"] is True
    before = (tmp_path / "mux-mapping.json").read_bytes()
    assert pane_lifecycle.admit(
        "end", expected_installation=starts[0]["installation"], exit_code=2,
        pane_id=exiting_pane, **arguments,
    )["result"]["applied"] is False
    assert (tmp_path / "mux-mapping.json").read_bytes() == before


@pytest.mark.parametrize("raw", [b"invalid", b"{}", b'[{"project":"example"}]'])
def test_start_refuses_corrupt_registry_without_rewriting_it(tmp_path, raw):
    path = tmp_path / "mux-mapping.json"
    path.write_bytes(raw)
    result = pane_lifecycle.admit(
        "start", root=tmp_path, expected_installation=None,
        project="example", worktree_id="worktree", mux_session="wt-worktree",
        session_incarnation="$1:123", pane_id="%1", worktree_path=str(tmp_path), mux_bin="tmux",
    )
    assert result["result"] == {"applied": False, "reason": "registry-unavailable"}
    assert path.read_bytes() == raw


def test_registry_loss_is_not_confirmed_exit_delivery(tmp_path):
    result = pane_lifecycle.admit(
        "end", root=tmp_path, expected_installation=pane_lifecycle.installation_identity(tmp_path),
        project="example", worktree_id="worktree", mux_session="wt-worktree",
        session_incarnation="$1:123", pane_id="%1", exit_code=0,
    )
    assert result["result"] == {"applied": False, "reason": "unconfirmed-absence"}
    assert not (tmp_path / "mux-mapping.json").exists()
    assert mappings.remove_mapping("example", "worktree", root=tmp_path) == {
        "applied": True, "reason": "absent",
    }


def test_admitted_exit_publishes_tombstone_without_starting_a_monitor(tmp_path, monkeypatch):
    from worktree_manager import mux_daemon_live

    published = []
    def publish(current, **options):
        assert options == {"ensure_monitor": False, "request_deadline_s": 2.0}
        published.append(current)
        return {"applied": True}
    monkeypatch.setattr(mux_daemon_live, "publish_live_observation", publish)
    mappings.register_next_mapping(entry(), root=tmp_path)
    result = pane_lifecycle.admit(
        "end", root=tmp_path, expected_installation=pane_lifecycle.installation_identity(tmp_path),
        project="example", worktree_id="worktree", mux_session="wt-worktree",
        session_incarnation="$1:123", pane_id="%1", exit_code=7,
    )
    assert result["result"]["observation"] == {"applied": True}
    assert len(published) == 1
    assert published[0]["live"] is False
    assert published[0]["observed_end"]["exit_code"] == 7


@pytest.mark.parametrize("creation", [False, True])
def test_reattach_or_creation_keeps_concurrent_cli_panes(tmp_path, monkeypatch, creation):
    import subprocess
    from worktree_manager import mux_daemon, mux_daemon_cli, mux_discovery

    monkeypatch.setattr(mappings, "default_root", lambda: tmp_path)
    monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda: True)
    monkeypatch.setattr(mux_daemon, "publish_live_observation", lambda *_a, **_k: None)
    def native(command, **kwargs):
        suffix = f"\t{tmp_path}" if "pane_current_path" in command[-1] else ""
        return subprocess.CompletedProcess(
            [], 0, stdout="".join(f"{pane}\t0\twt-worktree\t$1:123{suffix}\n" for pane in ("%1", "%2")),
        )
    monkeypatch.setattr(mux_daemon_cli, "run_mux_child", native)
    monkeypatch.setattr(mux_discovery, "run_mux_child", native)
    payload = entry(worktree_path=str(tmp_path))
    mappings.register_next_mapping(
        dict(payload, panes=[*entry("%1")["panes"], *entry("%2")["panes"]]), root=tmp_path,
    )
    assert mux_daemon_cli._register_reconnect(
        dict(payload, panes=entry("%2")["panes"]), creation=creation,
    )["applied"] is True
    current = mappings.get_mapping("example", "worktree", root=tmp_path)
    assert {pane["pane_id"] for pane in current["panes"] if pane["live"]} == {"%1", "%2"}


def test_exit_reconnect_duplicate_retains_original_exit_evidence(tmp_path, monkeypatch):
    import subprocess
    from worktree_manager import mux_daemon, mux_daemon_cli

    monkeypatch.setattr(mappings, "default_root", lambda: tmp_path)
    monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda: True)
    monkeypatch.setattr(mux_daemon, "publish_live_observation", lambda *_a, **_k: None)
    monkeypatch.setattr(
        mux_daemon_cli, "run_mux_child",
        lambda *_a, **_k: subprocess.CompletedProcess(
            [], 0, stdout="%2\t0\twt-worktree\t$1:123\n",
        ),
    )
    payload = entry(worktree_path=str(tmp_path))
    payload.pop("mapping_revision")
    mappings.register_next_mapping(
        dict(payload, panes=[*entry("%1")["panes"], *entry("%2")["panes"]]), root=tmp_path,
    )
    exit_args = dict(
        root=tmp_path, session_incarnation="$1:123", pane_id="%1", exit_code=7,
    )
    mappings.remove_mapping("example", "worktree", **exit_args)
    before = mappings.get_mapping("example", "worktree", root=tmp_path)["observed_end"]
    assert mux_daemon_cli._register_reconnect(payload)["applied"] is True
    current = mappings.get_mapping("example", "worktree", root=tmp_path)
    assert current["live"] is True
    assert current["observed_end"] == before
    assert mappings.remove_mapping("example", "worktree", **exit_args) == {
        "applied": True, "reason": "already-observed",
    }


def test_exit_activity_uses_current_installed_owner_and_keeps_correlation(monkeypatch, tmp_path):
    import subprocess
    from worktree_manager import engine_client

    calls = []
    command = [str(tmp_path / "current-engine"), "-I", "-m", "agent_worktrees"]
    monkeypatch.setattr(engine_client, "installed_engine_command", lambda: command)
    monkeypatch.setattr(
        engine_client, "engine_base_command",
        lambda: pytest.fail("exit must not use a captured override or PATH"),
    )
    def run(arguments, **options):
        calls.append((arguments, options))
        return subprocess.CompletedProcess(arguments, 0)
    monkeypatch.setattr(pane_lifecycle.subprocess, "run", run)
    assert pane_lifecycle._exit_activity("example", "worktree", 7, 42, "original-launch") == {
        "attempted": True, "command_exit_code": 0,
    }
    assert calls[0][0] == [
        *command, "--project", "example", "activity-log", "pane_exited",
        "--source", "launcher", "--worktree-id", "worktree",
        "--field", "exit_code=7", "--field", "runtime=42", "--launch-id", "original-launch",
    ]
    assert calls[0][1]["timeout"] == 1.0
    assert calls[0][1]["stdin"] == subprocess.DEVNULL


def test_activity_failure_never_changes_durable_admission_or_replays_duplicate(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        pane_lifecycle, "_exit_activity",
        lambda *args: calls.append(args) or {"applied": False, "reason": "activity-unconfirmed"},
    )
    mappings.register_next_mapping(entry(), root=tmp_path)
    arguments = dict(
        root=tmp_path, expected_installation=pane_lifecycle.installation_identity(tmp_path),
        project="example", worktree_id="worktree", mux_session="wt-worktree",
        session_incarnation="$1:123", pane_id="%1", exit_code=7, runtime=42,
        launch_id="original-launch",
    )
    result = pane_lifecycle.admit("end", **arguments)
    assert result["result"]["applied"] is True
    assert result["result"]["activity"]["applied"] is False
    assert calls == [("example", "worktree", 7, 42, "original-launch")]
    assert pane_lifecycle.admit("end", **arguments)["result"]["reason"] == "already-observed"
    assert len(calls) == 1


def test_changed_installation_refuses_before_mutating_state(tmp_path):
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    with pytest.raises(ValueError, match="installation changed"):
        pane_lifecycle.admit(
            "end", root=replacement,
            expected_installation=pane_lifecycle.installation_identity(original),
            project="example", worktree_id="worktree", mux_session="wt-worktree",
            session_incarnation="$1:123", pane_id="%1", exit_code=0,
        )
    assert not replacement.exists()


def test_changed_configured_source_does_not_inherit_an_old_installations_exit(tmp_path):
    identity = pane_lifecycle.installation_identity(tmp_path)
    (tmp_path / "config.toml").write_text(
        '[source]\nrepo = "https://example.invalid/another/repository.git"\n', encoding="utf-8",
    )
    with pytest.raises(ValueError, match="installation changed"):
        pane_lifecycle.admit(
            "end", root=tmp_path, expected_installation=identity,
            project="example", worktree_id="worktree", mux_session="wt-worktree",
            session_incarnation="$1:123", pane_id="%1", exit_code=0,
        )
    assert not (tmp_path / "mux-mapping.json").exists()


def test_captured_or_incomplete_runtime_is_not_a_current_handler(tmp_path, monkeypatch):
    slot = tmp_path / "versions" / "A"
    slot.mkdir(parents=True)
    monkeypatch.setattr(pane_lifecycle.sys, "prefix", str(slot))
    (tmp_path / "current-version").write_text("B\n", encoding="utf-8")
    with pytest.raises(ValueError, match="current completed runtime"):
        pane_lifecycle.require_current_runtime(tmp_path)
    (tmp_path / "current-version").write_text("A\n", encoding="utf-8")
    with pytest.raises(ValueError, match="current completed runtime"):
        pane_lifecycle.require_current_runtime(tmp_path)
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    (slot / ".runtime-bootstrap").mkdir()
    (slot / ".runtime-bootstrap" / "runtime_launcher.py").write_text("", encoding="utf-8")
    pane_lifecycle.require_current_runtime(tmp_path)


def test_unknown_boundary_contract_is_rejected_before_any_runtime_or_state_access(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "argv", [
        "pane-lifecycle", "end", "--contract-version", "2",
    ])
    monkeypatch.setattr(
        pane_lifecycle, "require_current_runtime",
        lambda *_: pytest.fail("unknown protocol reached runtime admission"),
    )
    with pytest.raises(SystemExit) as refused:
        pane_lifecycle.main()
    assert refused.value.code == 2


def test_reattach_preserves_cli_identity_when_a_mux_alias_becomes_an_exact_path(tmp_path, monkeypatch):
    import subprocess
    from worktree_manager import mux_daemon, mux_daemon_cli, mux_discovery

    executable = tmp_path / "native-mux"
    executable.touch()
    monkeypatch.setattr(mappings.shutil, "which", lambda name: str(executable) if name == "tmux" else None)
    monkeypatch.setattr(mappings, "default_root", lambda: tmp_path)
    monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda: True)
    monkeypatch.setattr(mux_daemon, "publish_live_observation", lambda *_a, **_k: None)
    monkeypatch.setattr(
        mux_discovery, "confirm_creation_cli",
        lambda *_: pytest.fail("equivalent executable is not a new incarnation"),
    )
    monkeypatch.setattr(
        mux_daemon_cli, "run_mux_child",
        lambda *_a, **_k: subprocess.CompletedProcess(
            [], 0, stdout="%1\t0\twt-worktree\t$1:123\n",
        ),
    )
    payload = entry(worktree_path=str(tmp_path))
    mappings.register_next_mapping(payload, root=tmp_path)
    result = mux_daemon_cli._register_reconnect(
        dict(payload, mux_bin=str(executable)), creation=False,
    )
    assert result["applied"] is True
    current = mappings.get_mapping("example", "worktree", root=tmp_path)
    assert current["mux_bin"] == str(executable)
    assert current["panes"] == [{"pane_id": "%1", "role": "cli", "live": True}]
