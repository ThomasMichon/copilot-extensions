"""Native discovery retains revision, tombstone and incarnation fences."""

import os
import subprocess

import pytest

from worktree_manager import mux_discovery
from worktree_manager import mux_daemon
from worktree_manager.mux_mapping_registry import MuxMappingRegistry


def _payload(**overrides):
    result = dict(project="project", worktree_id="one", worktree_path="workspace",
                  mux_session="wt-one", mux_bin="mux", session_incarnation="$1:100",
                  attached_clients=1)
    result.update(overrides)
    return result


def _probe(monkeypatch, stdout="wt-one\t1\t$1:100\tworkspace\n", code=0, stderr=""):
    monkeypatch.setattr(mux_discovery, "run_mux_child",
                        lambda *a, **kw: subprocess.CompletedProcess(a, code, stdout=stdout, stderr=stderr))


@pytest.mark.parametrize("binary", ["tmux", "/usr/bin/tmux", r"C:\Tools\tmux.exe"])
@pytest.mark.parametrize("diagnostic", [
    "no server running on /run/user/1000/tmux/default",
    "error connecting to /run/user/1000/tmux/default (No such file or directory)",
])
def test_qualified_tmux_terminal_absence_matches_canonical_contract(monkeypatch, binary, diagnostic):
    _probe(monkeypatch, "", 1, diagnostic + "\n")
    result = mux_discovery.confirm_native_session(_payload(mux_bin=binary), connected_only=False)
    assert result == {"applied": False, "reason": "session-absent"}


@pytest.mark.parametrize("incarnation", [
    ":", "$1:bad", "1:100", "foreign:100", "$:100", "$1:", "$1:100:200",
])
def test_malformed_roster_identity_is_unknown_not_presence_or_absence(monkeypatch, incarnation):
    _probe(monkeypatch, f"wt-other\t1\t{incarnation}\tworkspace\n")
    result = mux_discovery.confirm_native_session(_payload(), connected_only=False)
    assert result == {"applied": False, "reason": "session-unconfirmed"}


@pytest.mark.parametrize("incarnation", [":", "$1:bad", "foreign:100"])
def test_malformed_requested_identity_never_admits(monkeypatch, tmp_path, caplog, incarnation):
    _probe(monkeypatch, f"wt-one\t1\t{incarnation}\tworkspace\n")
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    assert not mux_discovery.discover_mapping(
        registry, _payload(session_incarnation=incarnation),
    )["applied"]
    assert registry.snapshot() == {}
    assert any("malformed" in record.message for record in caplog.records)


@pytest.mark.parametrize("incarnation", ["$0:0", "$0:1700000000", "$123:1700000001"])
def test_actual_native_decimal_identities_remain_confirmable(monkeypatch, incarnation):
    _probe(monkeypatch, f"wt-one\t1\t{incarnation}\tworkspace\n")
    assert mux_discovery.confirm_native_session(
        _payload(session_incarnation=incarnation), connected_only=False,
    ) == {"applied": True, "attached_clients": 1}


def test_connected_mapping_is_admitted_and_survives_daemon_restart(monkeypatch, tmp_path):
    _probe(monkeypatch)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    first = mux_discovery.discover_mapping(registry, _payload())
    assert first["applied"]
    assert first["entry"]["mapping_revision"] == 1
    second = mux_discovery.discover_mapping(MuxMappingRegistry(registry._path), _payload())
    assert second["applied"]
    assert second["entry"]["mapping_revision"] == 1


@pytest.mark.parametrize("alias_kind", ["dotdot", "case", "different"])
def test_discovery_recognizes_logical_worktree_path(monkeypatch, tmp_path, alias_kind):
    if alias_kind == "case" and os.name != "nt":
        pytest.skip("Case aliases require Windows path semantics")
    (tmp_path / "sub").mkdir()
    path = {
        "dotdot": str(tmp_path / "sub" / ".."),
        "case": str(tmp_path).swapcase(),
        "different": str(tmp_path / "sub"),
    }[alias_kind]
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    initial = _payload(worktree_path=str(tmp_path))
    _probe(monkeypatch, f"wt-one\t1\t$1:100\t{tmp_path}\n")
    assert mux_discovery.discover_mapping(registry, initial)["applied"]
    before = registry.get("project", "one")
    _probe(monkeypatch, f"wt-one\t2\t$1:100\t{path}\n")
    result = mux_discovery.discover_mapping(registry, _payload(worktree_path=path))
    if alias_kind == "different":
        assert result == {"applied": False, "reason": "incumbent-mapping"}
        assert registry.get("project", "one") == before
    else:
        assert result["applied"]
        assert result["entry"] == dict(before, attached_clients=2)


@pytest.mark.parametrize("stdout,code", [
    ("wt-one\t1\t$2:200\tworkspace\n", 0),
    ("wt-one\t0\t$1:100\tworkspace\n", 0), ("", 1), ("bad", 0),
    ("wt-one\t1\t$1:100\tforeign\n", 0),
])
def test_failed_unknown_or_stale_native_probe_does_not_register(monkeypatch, tmp_path, stdout, code):
    _probe(monkeypatch, stdout, code)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    assert not mux_discovery.discover_mapping(registry, _payload())["applied"]
    assert registry.snapshot() == {}


@pytest.mark.parametrize("live", [True, False])
def test_discovery_cannot_replace_incumbent_or_tombstone(monkeypatch, tmp_path, live):
    _probe(monkeypatch)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    registry.register(dict(_payload(session_incarnation="launcher-token"),
                           live=live, mapping_revision=9))
    before = registry.snapshot()
    assert not mux_discovery.discover_mapping(registry, _payload())["applied"]
    assert registry.snapshot() == before


def test_foreign_name_cannot_be_admitted(monkeypatch, tmp_path):
    _probe(monkeypatch, "shell\t1\t$1:100\tworkspace\n")
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    assert not mux_discovery.discover_mapping(
        registry, _payload(mux_session="shell"),
    )["applied"]


def test_status_compute_discovers_then_presents_via_existing_owner(monkeypatch, tmp_path):
    _probe(monkeypatch)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    compute = mux_daemon.build_compute(registry)
    admitted = compute(mux_discovery.KIND, _payload())
    assert admitted["applied"]
    assert admitted["entry"]["attached_clients"] == 1
    writes = []
    monkeypatch.setattr(mux_daemon, "_mux_session_alive", lambda *a: True)
    monkeypatch.setattr(mux_daemon, "apply_status_options",
                        lambda entry, values: writes.append((entry, values)) or True)
    monkeypatch.setattr(mux_daemon.mux_daemon_live, "publish_live_observation",
                        lambda *a, **kw: {"applied": True})
    status = dict(project="project", worktree_id="one", values={"@aw_seg": "status"},
                  rendered_at="2026-10-22T12:00:00Z", mapping_revision=1,
                  mux_session="wt-one", session_incarnation="$1:100")
    assert compute(mux_daemon.KIND, status)["applied"]
    assert writes[0][1] == {"@aw_seg": "status"}
    # A same-name replacement is not the observed incarnation.
    _probe(monkeypatch, "wt-one\t1\t$2:200\tworkspace\n")
    assert compute(mux_daemon.KIND, status)["reason"] == "stale-incarnation"
    assert len(writes) == 1
    assert registry.get("project", "one")["live"]


@pytest.mark.parametrize("fence", [
    {"mapping_revision": 0}, {"mux_session": "other"},
    {"session_incarnation": "old"},
])
def test_status_compute_refuses_stale_sender_fences(monkeypatch, tmp_path, fence):
    _probe(monkeypatch)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    assert mux_discovery.discover_mapping(registry, _payload())["applied"]
    monkeypatch.setattr(mux_daemon, "apply_status_options",
                        lambda *a: pytest.fail("stale mapping must not paint"))
    compute = mux_daemon.build_compute(registry)
    status = dict(project="project", worktree_id="one", values={"@aw_seg": "stale"},
                  rendered_at="2026-10-22T12:00:00Z", **fence)
    assert compute(mux_daemon.KIND, status)["reason"] == "stale-mapping"


def test_enumeration_failure_preserves_prior_attached_count(monkeypatch, tmp_path):
    _probe(monkeypatch)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    assert mux_discovery.discover_mapping(registry, _payload())["applied"]
    before = registry.snapshot()
    _probe(monkeypatch, "", 1)
    assert not mux_discovery.discover_mapping(registry, _payload())["applied"]
    assert registry.snapshot() == before


def test_discovery_rechecks_incumbent_after_native_probe(monkeypatch, tmp_path):
    registry = MuxMappingRegistry(tmp_path / "mapping.json")

    def replaced_during_probe(*a, **kw):
        registry.register(dict(_payload(session_incarnation="new-owner"),
                               mapping_revision=7))
        return subprocess.CompletedProcess(a, 0, stdout="wt-one\t1\t$1:100\tworkspace\n")

    monkeypatch.setattr(mux_discovery, "run_mux_child", replaced_during_probe)
    assert mux_discovery.discover_mapping(registry, _payload())["reason"] == "mapping-changed"
    assert registry.get("project", "one")["mapping_revision"] == 7


def test_discovery_respects_draining_and_tracks_handler_lifetime(monkeypatch, tmp_path):
    from types import SimpleNamespace

    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    assert mux_discovery.handle_discovery(
        registry, _payload(), SimpleNamespace(accepting_requests=lambda: False),
    )["reason"] == "draining"
    events = []
    tracker = SimpleNamespace(enter=lambda: events.append("enter"),
                              exit=lambda: events.append("exit"))
    _probe(monkeypatch)
    assert mux_discovery.handle_discovery(registry, _payload(), None, tracker)["applied"]
    assert events == ["enter", "exit"]


@pytest.mark.parametrize("live", [True, False])
def test_fresh_native_incarnation_recovers_old_mapping(monkeypatch, tmp_path, live):
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    registry.register(dict(_payload(), mapping_revision=9, live=live,
                           last_status_rendered_at="2026-10-22T12:00:00Z"))
    _probe(monkeypatch, "wt-one\t1\t$2:200\tworkspace\n")
    result = mux_discovery.discover_mapping(registry, _payload(session_incarnation="$2:200"))
    assert result["applied"]
    assert result["entry"]["mapping_revision"] == 10
    assert result["entry"]["session_incarnation"] == "$2:200"
    assert result["entry"]["last_status_rendered_at"] is None
    assert registry.remove("project", "one", session_incarnation="$1:100")["reason"] == "session_mismatch"
    assert registry.get("project", "one")["live"]


def test_same_incarnation_tombstone_is_not_resurrected(monkeypatch, tmp_path):
    _probe(monkeypatch)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    registry.register(dict(_payload(), mapping_revision=9, live=False))
    before = registry.snapshot()
    assert not mux_discovery.discover_mapping(registry, _payload())["applied"]
    assert registry.snapshot() == before


@pytest.mark.parametrize("live", [False, True])
def test_launcher_binary_alias_can_recover_a_fresh_native_incarnation(monkeypatch, tmp_path, live):
    import shutil

    binary = tmp_path / "psmux.exe"
    binary.touch()
    monkeypatch.setattr(shutil, "which", lambda name: str(binary) if name == "psmux" else None)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    registry.register(dict(_payload(mux_bin="psmux"), mapping_revision=9, live=live))
    _probe(monkeypatch, "wt-one\t1\t$2:200\tworkspace\n")
    admitted = mux_discovery.discover_mapping(
        registry, _payload(mux_bin=str(binary), session_incarnation="$2:200"),
    )
    assert admitted["applied"]
    assert admitted["entry"]["mapping_revision"] == 10
    assert admitted["entry"]["mux_bin"] == str(binary)


@pytest.mark.parametrize("resolved", [None, "other-install"])
def test_matching_executable_basename_is_not_identity(monkeypatch, tmp_path, resolved):
    import shutil

    binary = tmp_path / "psmux.exe"
    other = tmp_path / "other-install" / "psmux.exe"
    monkeypatch.setattr(shutil, "which",
                        lambda name: str(other) if resolved and name == "psmux" else None)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    registry.register(dict(_payload(mux_bin="psmux"), mapping_revision=9, live=False))
    before = registry.snapshot()
    _probe(monkeypatch, "wt-one\t1\t$2:200\tworkspace\n")
    refused = mux_discovery.discover_mapping(
        registry, _payload(mux_bin=str(binary), session_incarnation="$2:200"),
    )
    assert refused["reason"] == "incumbent-mapping"
    assert registry.snapshot() == before


def test_fresh_admission_cannot_replace_concurrent_registration(monkeypatch, tmp_path):
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    registry.register(dict(_payload(), mapping_revision=9, live=False))

    def replaced_during_probe(*args, **kwargs):
        registry.register(dict(_payload(session_incarnation="$3:300"),
                               mapping_revision=12))
        return subprocess.CompletedProcess(args, 0, stdout="wt-one\t1\t$2:200\tworkspace\n")

    monkeypatch.setattr(mux_discovery, "run_mux_child", replaced_during_probe)
    assert not mux_discovery.discover_mapping(
        registry, _payload(session_incarnation="$2:200"),
    )["applied"]
    assert registry.get("project", "one")["mapping_revision"] == 12
    assert registry.get("project", "one")["session_incarnation"] == "$3:300"


@pytest.mark.parametrize("contents", ["[invalid-json", '{"unexpected": "registry"}'])
def test_unknown_registry_state_is_not_an_absent_mapping(monkeypatch, tmp_path, contents):
    _probe(monkeypatch)
    path = tmp_path / "mapping.json"
    path.write_text(contents, encoding="utf-8")
    registry = MuxMappingRegistry(path)
    assert not mux_discovery.discover_mapping(registry, _payload())["applied"]
    assert path.read_text(encoding="utf-8") == contents


def _admitted_status_fixture(monkeypatch, tmp_path, *, mux_bin="mux"):
    _probe(monkeypatch)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    assert mux_discovery.discover_mapping(registry, _payload(mux_bin=mux_bin))["applied"]
    observations = []
    monkeypatch.setattr(mux_daemon.mux_daemon_live, "publish_live_observation",
                        lambda entry, **kw: observations.append(dict(entry)) or {"applied": True})
    status = dict(project="project", worktree_id="one", values={"@aw_ctx": "CTX", "@aw_seg": "SEG"},
                  rendered_at="2026-10-22T12:00:00Z", mapping_revision=1,
                  mux_session="wt-one", session_incarnation="$1:100")
    return registry, mux_daemon.build_compute(registry), status, observations


@pytest.mark.parametrize("listing", ["", "wt-other\t1\t$2:200\telsewhere\n"])
def test_confirmed_native_disappearance_retires_and_stops_republication(monkeypatch, tmp_path, listing):
    registry, compute, status, observations = _admitted_status_fixture(monkeypatch, tmp_path)
    _probe(monkeypatch, listing)
    assert not compute(mux_daemon.KIND, status)["applied"]
    assert not registry.get("project", "one")["live"]
    assert not registry.has_any_live()
    assert len(observations) == 1 and not observations[0]["live"]
    assert mux_daemon._republish_live_mappings(registry, ensure_monitor=False)
    assert len(observations) == 1


@pytest.mark.parametrize("listing,code", [
    ("", 1), ("bad", 0), ("other\tbad\t$2:200\tpath\n", 0),
    ("other\t1\t\tpath\n", 0), ("wt-one\t1\t$2:200\tworkspace\n", 0), (None, 0),
])
def test_unknown_probe_or_same_name_replacement_does_not_retire(monkeypatch, tmp_path, listing, code):
    registry, compute, status, observations = _admitted_status_fixture(monkeypatch, tmp_path)
    before = registry.snapshot()
    _probe(monkeypatch, listing, code)
    assert not compute(mux_daemon.KIND, status)["applied"]
    assert registry.snapshot() == before
    assert observations == []


@pytest.mark.parametrize("error", [OSError("fixture"), subprocess.TimeoutExpired("mux", 2)])
def test_failed_native_probe_does_not_retire_discovered_mapping(monkeypatch, tmp_path, error):
    registry, compute, status, observations = _admitted_status_fixture(monkeypatch, tmp_path)
    before = registry.snapshot()

    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(mux_discovery, "run_mux_child", unavailable)
    assert not compute(mux_daemon.KIND, status)["applied"]
    assert registry.snapshot() == before
    assert observations == []


@pytest.mark.parametrize("revision,incarnation", [(2, "$1:100"), (1, "$2:200")])
def test_disappearance_retirement_preserves_concurrent_owner(
    monkeypatch, tmp_path, revision, incarnation,
):
    registry, compute, status, observations = _admitted_status_fixture(monkeypatch, tmp_path)

    def replacement_during_probe(*args, **kwargs):
        registry.register(dict(_payload(session_incarnation=incarnation),
                               mapping_revision=revision, native_session_incarnation=incarnation))
        return subprocess.CompletedProcess(args, 0, stdout="")

    monkeypatch.setattr(mux_discovery, "run_mux_child", replacement_during_probe)
    assert not compute(mux_daemon.KIND, status)["applied"]
    current = registry.get("project", "one")
    assert current["live"]
    assert (current["mapping_revision"], current["session_incarnation"]) == (revision, incarnation)
    assert observations == []


def test_confirmed_native_presence_does_not_use_unknown_legacy_liveness(monkeypatch, tmp_path):
    registry, compute, status, _ = _admitted_status_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(mux_daemon, "_mux_session_alive", lambda *a: False)
    monkeypatch.setattr(mux_daemon, "apply_status_options", lambda *a: True)
    assert compute(mux_daemon.KIND, status)["applied"]
    assert registry.get("project", "one")["live"]


@pytest.mark.parametrize("mux_bin", ["tmux", "psmux"])
def test_discovered_manager_status_initializes_session_formats_through_existing_writer(
    monkeypatch, tmp_path, mux_bin,
):
    registry, compute, status, _ = _admitted_status_fixture(monkeypatch, tmp_path, mux_bin=mux_bin)
    options = {}
    calls = []

    def apply(argv, **kwargs):
        calls.append(argv)
        assert argv[0] == mux_bin
        if argv[1] == "set-option":
            assert argv[2:4] == ["-t", "wt-one"]
            assert "-g" not in argv
            options[argv[4]] = argv[5]
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(mux_daemon, "run_mux_child", apply)
    assert compute(mux_daemon.KIND, status)["applied"]
    assert options["status-left"] == "#{@aw_ctx} "
    assert options["status-right"] == "#{@aw_seg} %H:%M "
    assert options["status"] == "on"
    assert options["status-left-length"] == "100"
    assert options["status-right-length"] == "150"
    assert options["@aw_ctx"] == "CTX" and options["@aw_seg"] == "SEG"
    assert registry.get("project", "one")["last_status_rendered_at"] == status["rendered_at"]
    calls.clear()
    assert compute(mux_daemon.KIND, dict(status, rendered_at="2026-10-22T12:00:01Z"))["applied"]
    assert not any(argv[1] == "set-option" and argv[4].startswith("status") for argv in calls)


@pytest.mark.parametrize("mux_bin", ["tmux", "psmux"])
def test_failed_manager_format_initialization_remains_retryable(monkeypatch, tmp_path, mux_bin):
    registry, compute, status, _ = _admitted_status_fixture(monkeypatch, tmp_path, mux_bin=mux_bin)
    writes = []
    failing = [True]

    def apply(argv, **kwargs):
        assert argv[0] == mux_bin
        if argv[1] == "set-option":
            writes.append(argv[4])
        return subprocess.CompletedProcess(
            argv, 1 if argv[1] == "set-option" and argv[4] == "status-right" and failing[0] else 0,
        )

    monkeypatch.setattr(mux_daemon, "run_mux_child", apply)
    assert compute(mux_daemon.KIND, status)["reason"] == "apply-failed"
    assert registry.get("project", "one")["last_status_rendered_at"] is None
    assert "status-left" in writes and "@aw_seg" in writes
    failing[0] = False
    writes.clear()
    assert compute(mux_daemon.KIND, status)["applied"]
    assert "status-left" in writes and "status-right" in writes
