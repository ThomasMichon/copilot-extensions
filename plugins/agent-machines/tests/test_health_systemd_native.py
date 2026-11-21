"""User-manager adapter controls; synthetic manager responses are not qualification."""

import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent_machines import health_jobs, health_slots, health_systemd_native as native
from agent_procutil import owned_process

OWNER = "urn:copilot-extensions:health-job:" + "c" * 64
NAME = "ce-health-" + "c" * 32


def definition() -> health_jobs.LinuxDefinition:
    return health_jobs.installed_linux_definition(
        slot="/example/versions/1.0", python="/example/versions/1.0/bin/python",
        identity="1" * 32 + ":" + "2" * 64, store="/example/store", policy_token="a" * 64,
        owner=OWNER, account_uid=1000, enabled=False,
    )


def test_native_command_distinguishes_absent_unit_from_unavailable_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair = native.NativePair(NAME, OWNER)
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    calls: list[tuple[list[str], dict[str, Any]]] = []
    answer = SimpleNamespace(returncode=4, stdout="LoadState=not-found\n")

    def run(argv: list[str], **kwargs: Any) -> Any:
        calls.append((argv, kwargs))
        return answer

    monkeypatch.setattr(owned_process, "run_caller_owned", run)
    assert pair._command("show", NAME + ".service", missing=True) == answer.stdout
    assert calls[0][0][:3] == ["/usr/bin/systemctl", "--user", "--no-pager"]
    assert calls[0][1]["env"]["LC_ALL"] == "C" and calls[0][1]["env"]["TZ"] == "UTC"
    answer.stdout = "Failed to connect to bus\n"
    with pytest.raises(ValueError, match="unconfirmed"):
        pair._command("show", NAME + ".service", missing=True)
    assert all("sudo" not in argv and "linger" not in argv for argv, _options in calls)


def test_confirmed_client_cleanup_clears_the_fence_independently_of_manager_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair = native.NativePair(NAME, OWNER)
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    events: list[str] = []
    pair._native_begin = lambda: events.append("begin")
    pair._native_settled = lambda: events.append("settled")
    answer = SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(owned_process, "run_caller_owned", lambda *_args, **_kwargs: answer)
    pair._command("show", NAME + ".service")
    assert not events
    pair._command("daemon-reload")
    assert events == ["begin", "settled"]
    events.clear()
    answer.returncode = 1
    with pytest.raises(ValueError, match="unconfirmed"):
        pair._command("start", NAME + ".timer")
    assert events == ["begin", "settled"]
    events.clear()

    def timeout(*_args: Any, **kwargs: Any) -> Any:
        kwargs["on_settled"]()
        raise subprocess.TimeoutExpired("systemctl", 15)

    monkeypatch.setattr(owned_process, "run_caller_owned", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        pair._command("stop", NAME + ".timer")
    assert events == ["begin", "settled"]


@pytest.mark.parametrize(("mode", "uid", "links"), [
    (stat.S_IFREG | 0o622, 1000, 1),
    (stat.S_IFREG | 0o600, 1001, 1),
    (stat.S_IFREG | 0o600, 1000, 2),
    (stat.S_IFLNK | 0o600, 1000, 1),
])
def test_native_file_identity_refuses_foreign_writable_or_indirect_files(
    mode: int, uid: int, links: int,
) -> None:
    info = SimpleNamespace(
        st_mode=mode, st_uid=uid, st_nlink=links, st_ino=10, st_dev=1, st_size=2, st_mtime_ns=3,
    )
    with pytest.raises(ValueError, match="foreign"):
        native._stamp(cast(os.stat_result, info), 1000)


def snapshot_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[native.NativePair, Path, dict[str, dict[str, str]]]:
    scope = tmp_path / "units"
    scope.mkdir()
    pair = native.NativePair(NAME, OWNER)
    wanted = definition()
    monkeypatch.setattr(pair, "_context", lambda: (1000, scope))
    monkeypatch.setattr(pair, "_command", lambda *_args, **_kwargs: str(scope))
    properties: dict[str, dict[str, str]] = {}
    for unit in pair.units:
        properties[unit] = {
            "LoadState": "loaded", "ActiveState": "inactive", "FragmentPath": str(scope / unit),
            "DropInPaths": "", "Description": OWNER, "NeedDaemonReload": "no", "Names": unit,
            "UnitFileState": "static" if unit.endswith(".service") else "disabled",
            "MainPID": "0", "ControlPID": "0",
        }
    monkeypatch.setattr(pair, "_properties", lambda unit: properties[unit].copy())
    monkeypatch.setattr(native, "read_file", lambda path, _uid: (wanted["artifacts"][path.name], (1, 2, 3, 4)))
    return pair, scope, properties


@pytest.mark.parametrize("missing", [".config", "systemd", "user", "timers.target.wants"])
def test_first_preview_treats_absent_user_scope_as_absent_without_creating_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    pair = native.NativePair(NAME, OWNER)
    scope = tmp_path / ".config" / "systemd" / "user"
    parts = [tmp_path / ".config", tmp_path / ".config" / "systemd", scope, scope / "timers.target.wants"]
    for directory in parts:
        if directory.name == missing:
            break
        directory.mkdir()
    monkeypatch.setattr(pair, "_context", lambda: (1000, scope))
    monkeypatch.setattr(pair, "_command", lambda *_args, **_kwargs: str(scope))
    monkeypatch.setattr(pair, "_properties", lambda _unit: {
        "LoadState": "not-found", "ActiveState": "inactive", "FragmentPath": "",
        "DropInPaths": "", "UnitFileState": "", "MainPID": "0", "ControlPID": "0",
    })
    monkeypatch.setattr(sys, "platform", "linux")
    before = sorted(tmp_path.rglob("*"))
    observed = pair.snapshot()
    assert not observed["configured"] and not observed["enabled"] and not observed["loaded"]
    assert observed["definition"] is None and observed["link_identity"] is None
    assert sorted(tmp_path.rglob("*")) == before


def test_missing_scope_does_not_hide_non_directory_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / ".config"
    config.write_text("not a directory")
    scope = config / "systemd" / "user"
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ValueError, match="plain-path"):
        native.read_file(scope / (NAME + ".service"), 1000)
    with pytest.raises(ValueError, match="plain-path"):
        native._link(scope, NAME + ".timer", 1000)


@pytest.mark.parametrize("job", ["", "0"])
def test_native_readback_keeps_timestamps_and_execution_unknown_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job: str,
) -> None:
    pair, _, properties = snapshot_case(tmp_path, monkeypatch)
    for value in properties.values():
        value["Job"] = job
    observed = pair.snapshot()
    assert observed["loaded"] and observed["configured"] and not observed["enabled"]
    assert observed["last_run"] is None and observed["next_run"] is None and observed["last_result"] is None
    properties[pair.units[0]].update(
        ActiveState="activating", MainPID="1234", InvocationID="invocation",
        ExecMainStartTimestamp="Wed 2026-11-18 17:00:15 UTC", ExecMainStatus="0",
    )
    properties[pair.units[1]]["NextElapseUSecRealtime"] = "Wed 2026-11-18 18:00:15 UTC"
    observed = pair.snapshot()
    assert observed["instances"] == ["invocation"]
    assert observed["last_run"] == "2026-11-18T17:00:15+00:00"
    assert observed["next_run"] == "2026-11-18T18:00:15+00:00"


@pytest.mark.parametrize("override", [
    {"DropInPaths": "/foreign/override.conf"}, {"FragmentPath": "/foreign/unit"},
    {"SourcePath": "/foreign/source"}, {"Names": NAME + ".service alias.service"},
    {"UnitFileState": "masked"},
])
def test_native_readback_refuses_foreign_loaded_or_overridden_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: dict[str, str],
) -> None:
    pair, _, properties = snapshot_case(tmp_path, monkeypatch)
    properties[pair.units[0]].update(override)
    with pytest.raises(ValueError, match="foreign"):
        pair.snapshot()


@pytest.mark.parametrize("job", ["1234", "1234 /org/freedesktop/systemd1/job/1234", "unknown"])
def test_native_readback_refuses_queued_manager_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job: str,
) -> None:
    pair, _, properties = snapshot_case(tmp_path, monkeypatch)
    properties[pair.units[1]]["Job"] = job
    with pytest.raises(ValueError, match="operation-unconfirmed"):
        pair.snapshot()


@pytest.mark.parametrize("directory", [
    NAME + ".service.d", "service.d", "ce-.service.d", "ce-health-.service.d",
])
def test_even_unloaded_drop_ins_refuse_before_native_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: str,
) -> None:
    pair, scope, _ = snapshot_case(tmp_path, monkeypatch)
    (scope / directory).mkdir()
    with pytest.raises(ValueError, match="overridden"):
        pair.snapshot()


def test_native_pair_publication_requires_disabled_verified_loaded_pair_before_enable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair = native.NativePair(NAME, OWNER)
    wanted = definition()
    observed: dict[str, Any] = {
        "configured": False, "definition": None, "enabled": False, "loaded": False,
        "timer_enabled": False, "timer_active": False, "instances": [],
        "service_loaded": False, "timer_loaded": False,
    }
    calls: list[str] = []

    def snapshot() -> dict[str, Any]:
        value = copy.deepcopy(observed)
        value["digest"] = hashlib.sha256(json.dumps(observed, sort_keys=True).encode()).hexdigest()
        return value

    def publish(_scope: Path, artifacts: dict[str, str], expected: str) -> None:
        assert expected == snapshot()["digest"] and not observed["enabled"]
        calls.append("publish-disabled")
        observed.update(configured=True, definition={**wanted, "artifacts": artifacts})

    def command(*argv: str, **_options: Any) -> str:
        calls.append(argv[0])
        if argv[0] == "daemon-reload":
            observed.update(loaded=True, service_loaded=True, timer_loaded=True)
        if argv[0] == "start":
            observed.update(enabled=True, timer_enabled=True, timer_active=True)
            observed["definition"]["enabled"] = True
        return ""

    monkeypatch.setattr(pair, "_context", lambda: (1000, tmp_path))
    monkeypatch.setattr(pair, "snapshot", snapshot)
    monkeypatch.setattr(pair, "_publish", publish)
    monkeypatch.setattr(pair, "_command", command)
    result = pair.call("create", expected=snapshot()["digest"], definition=wanted)
    assert result["loaded"] and not result["enabled"] and calls == ["publish-disabled", "daemon-reload"]
    assert pair.call("enable", expected=result["digest"])["enabled"]
    assert calls == ["publish-disabled", "daemon-reload", "enable", "start"]
    with pytest.raises(ValueError, match="changed"):
        pair.call("remove", expected=result["digest"])


def test_second_file_failure_preserves_first_file_for_journaled_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair = native.NativePair(NAME, OWNER)
    monkeypatch.setattr(pair, "_context", lambda: (1000, tmp_path))

    def read(path: Path, _uid: int) -> Any:
        if not path.exists():
            return None, None
        info = path.stat()
        return path.read_text(), (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

    monkeypatch.setattr(native, "read_file", read)
    monkeypatch.setattr(pair, "snapshot", lambda: {"digest": "before"})
    replace = os.replace
    count = 0

    def interrupted(source: Path, target: Path) -> None:
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("second publication interrupted")
        replace(source, target)

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        pair._publish(tmp_path, definition()["artifacts"], "before")
    assert (tmp_path / pair.units[0]).exists() and not (tmp_path / pair.units[1]).exists()
    assert list(tmp_path.glob("*.tmp.*")) == []


def test_native_settlement_requires_empty_owned_cgroup_not_just_missing_main_pid(tmp_path: Path) -> None:
    (tmp_path / "cgroup.controllers").write_text("cpu memory")
    group = tmp_path / "owned"
    group.mkdir()
    events = group / "cgroup.events"
    events.write_text("populated 1\nfrozen 0\n")
    assert not native._cgroup_empty("/owned", tmp_path)
    events.write_text("populated 0\nfrozen 0\n")
    assert native._cgroup_empty("/owned", tmp_path)
    events.unlink()
    assert not native._cgroup_empty("/owned", tmp_path)
    group.rmdir()
    assert native._cgroup_empty("/owned", tmp_path)
    assert not native._cgroup_empty("/../foreign", tmp_path)


def test_partial_pair_disable_stops_timer_before_action_and_retains_unknown_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair = native.NativePair(NAME, OWNER)
    partial = definition()
    del partial["artifacts"][pair.units[1]]
    partial["enabled"] = True
    observed: dict[str, Any] = {
        "configured": True, "definition": partial, "enabled": True,
        "timer_enabled": True, "timer_active": True, "timer_loaded": True,
        "service_loaded": True, "instances": ["owned invocation"], "link_identity": (1, 2, "target"),
    }
    calls: list[str] = []
    link: list[tuple[int, int, str] | None] = [(1, 2, "target")]

    def snapshot() -> dict[str, Any]:
        value = copy.deepcopy(observed)
        value["digest"] = hashlib.sha256(json.dumps(observed, sort_keys=True).encode()).hexdigest()
        return value

    def unlink(path: Path, **_kwargs: Any) -> None:
        assert path == tmp_path / "timers.target.wants" / pair.units[1]
        calls.append("unlink")
        link[0] = None

    def command(*argv: str, **_options: Any) -> str:
        if argv[0] == "stop" and argv[1] == pair.units[1]:
            calls.append("stop-timer")
            observed["timer_active"] = False
        elif argv[0] == "daemon-reload":
            calls.append("reload")
            observed["timer_enabled"] = False
            observed["enabled"] = False
            observed["definition"]["enabled"] = False
        elif argv[0] == "stop":
            calls.append("stop-action")
            # A missing MainPID is insufficient if the cgroup is still populated.
        return ""

    monkeypatch.setattr(pair, "_context", lambda: (1000, tmp_path))
    monkeypatch.setattr(pair, "snapshot", snapshot)
    monkeypatch.setattr(native, "_link", lambda *_args: link[0])
    monkeypatch.setattr(pair, "_command", command)
    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(health_slots, "_sync_directory", lambda _path: None)
    with pytest.raises(ValueError, match="settlement-unconfirmed"):
        pair.call("disable", expected=snapshot()["digest"])
    assert calls == ["stop-timer", "unlink", "reload", "stop-action"]


@pytest.mark.parametrize("change", ["contents", "identity"])
def test_removal_preserves_unit_edited_or_replaced_during_action_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    pair = native.NativePair(NAME, OWNER)
    observed: dict[str, Any] = {
        "configured": True, "definition": definition(), "enabled": False,
        "timer_enabled": False, "timer_active": False, "timer_loaded": True,
        "service_loaded": True, "instances": ["owned invocation"], "link_identity": None,
        "file_identities": {unit: (1, 2, 3, 4) for unit in pair.units},
    }

    def snapshot() -> dict[str, Any]:
        value = copy.deepcopy(observed)
        value["digest"] = hashlib.sha256(json.dumps(observed, sort_keys=True).encode()).hexdigest()
        return value

    def command(*argv: str, **_options: Any) -> str:
        if argv == ("stop", pair.units[0]):
            observed["instances"] = []
            if change == "contents":
                observed["definition"]["artifacts"][pair.units[0]] += "# edited during stop\n"
            else:
                observed["file_identities"][pair.units[0]] = (1, 99, 3, 4)
        return ""

    def no_unlink(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("changed unit must not be deleted")

    monkeypatch.setattr(pair, "_context", lambda: (1000, tmp_path))
    monkeypatch.setattr(pair, "snapshot", snapshot)
    monkeypatch.setattr(pair, "_command", command)
    monkeypatch.setattr(native, "_link", lambda *_args: None)
    monkeypatch.setattr(Path, "unlink", no_unlink)
    with pytest.raises(ValueError, match="pair-changed"):
        pair.call("remove", expected=snapshot()["digest"])
