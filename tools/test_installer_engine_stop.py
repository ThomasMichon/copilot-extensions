"""Actual daemon writers, original admission propagation, and pidfd STOP bounds."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "libs" / "installer-engine" / "installer_engine_stop.py"
SPEC = importlib.util.spec_from_file_location("native_stop_subject", SOURCE)
assert SPEC is not None and SPEC.loader is not None
STOP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = STOP
SPEC.loader.exec_module(STOP)
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
BASH = str(GIT_BASH) if GIT_BASH.is_file() else shutil.which("bash")


def _posix(path: Path) -> str:
    value = path.as_posix()
    return "/" + value[0].lower() + value[2:] if os.name == "nt" else value


def _function(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(name + "() {")
    return text[start:text.index("\n}\n", start) + 2]


def _shell(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    if not BASH:
        pytest.skip("Native Bash unavailable")
    script = tmp_path / "fixture.sh"
    script.write_text(body, encoding="utf-8", newline="\n")
    return subprocess.run(  # noqa: S603 - fixed native host and fixture-owned script
        [BASH, _posix(script)], cwd=tmp_path, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("plugin", ("agent-bridge", "agent-vault"))
def test_actual_unit_writer_is_accepted_by_daemon_admission(tmp_path: Path, plugin: str) -> None:
    root = tmp_path / "runtime"
    home = tmp_path / "home"
    scripts = REPO / "plugins" / plugin / "scripts"
    writer = "_install_systemd_unit" if plugin == "agent-bridge" else "_install_service"
    unit = home / ".config" / "systemd" / "user" / "fixture.service"
    body = "\n".join((
        "set -euo pipefail",
        '_skip() { :; }; _ok() { :; }; _fail() { echo "$1" >&2; }; _warn() { :; }',
        'systemctl() { if [[ "${2:-}" == show ]]; then printf "%s\\n" "$UNIT_DIR/$SYSTEMD_UNIT"; '
        'else [[ "${2:-}" != is-active ]]; fi; }',
        f"HOME={shlex.quote(_posix(home))}; INSTALL_DIR={shlex.quote(_posix(root))}",
        'VENV_DIR="$INSTALL_DIR/versions/1.0"; LINK_PYTHON="$VENV_DIR/bin/python"',
        'UNIT_DIR="$HOME/.config/systemd/user"; SYSTEMD_UNIT=fixture.service',
        'RUN_DIR="$INSTALL_DIR/run"; SOCKET_PATH="$RUN_DIR/vault.sock"; PIPE_PATH=fixture',
        'PID_FILE="$RUN_DIR/service.pid"; LOG_FILE="$INSTALL_DIR/logs/service.log"',
        "SERVICE_SUFFIX=''; INSTALLATION_ID=fixture; PORT_ENV_LINE=''; NO_SERVICE=0",
        _function(scripts / "install.sh", writer),
        writer,
    ))
    result = _shell(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr
    STOP.check_unit(unit, _posix(root), plugin)
    checked, inspected, control = _run_preflight(
        tmp_path, unit, root, scripts / "install.sh", plugin, _effective_properties(unit),
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert inspected.exists() and not control.exists()
    start = next(line[10:] for line in unit.read_text().splitlines() if line.startswith("ExecStart="))
    argv = shlex.split(start)
    assert STOP.admits_argv(argv, _posix(root), plugin)
    if plugin == "agent-bridge":
        assert argv[-2:] == [f"{_posix(root)}/versions/1.0/bin/agent-bridge", "start"]
        assert STOP.admits_argv(["/usr/bin/python3", *argv], _posix(root), plugin)
    else:
        assert argv[-3:] == ["--foreground", "--persistent", "--handoff-stdin"]
    changed = unit.read_text().replace(_posix(root) + "/", _posix(root) + "-other/")
    unit.write_text(changed)
    with pytest.raises(RuntimeError, match="does not name"):
        STOP.check_unit(unit, _posix(root), plugin)


def _written_unit(tmp_path: Path, kind: str) -> tuple[Path, Path, Path, str]:
    plugin = "agent-bridge" if kind == "bridge" else (
        "agent-vault" if kind == "vault" else "agent-dispatch"
    )
    source = REPO / "plugins" / plugin / "scripts" / "install.sh"
    root = tmp_path / "runtime"
    root.mkdir()
    home = tmp_path / "home"
    units = home / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    supervisor = kind == "supervisor"
    name = "fixture-supervisor.service" if supervisor else "fixture.service"
    writer = "_install_systemd_unit" if kind == "bridge" else (
        "_install_supervisor_unit" if supervisor else "_install_service"
    )
    invoke = (
        '_install_supervisor_unit fixture-supervisor.service "$INSTALL_DIR/supervisor.env" fixture'
        if supervisor else writer
    )
    result = _shell(tmp_path, "\n".join((
        "set -euo pipefail",
        '_skip() { :; }; _ok() { :; }; _fail() { echo "$1" >&2; }; _warn() { :; }',
        'systemctl() { [[ "${2:-}" != is-active ]]; }',
        "_is_wsl() { return 1; }; _supervisor_mode() { echo serve; }",
        "_supervisor_labels_configured() { return 1; }; _supervisor_action() { :; }",
        "_supervisor_audit() { :; }",
        f"INSTALL_DIR={shlex.quote(_posix(root))};UNIT_DIR={shlex.quote(_posix(units))}",
        f"HOME={shlex.quote(_posix(home))}",
        'VENV_DIR="$INSTALL_DIR/versions/1.0"; VENV_PYTHON="$VENV_DIR/bin/python"',
        'ENV_FILE="$INSTALL_DIR/service.env"',
        'LINK_PYTHON="$VENV_PYTHON"; SYSTEMD_UNIT=fixture.service; NO_SERVICE=0',
        'SUPERVISOR_LAUNCHER="$INSTALL_DIR/supervise-service.sh"; SUPERVISOR_PATH="$PATH"',
        'RUN_DIR="$INSTALL_DIR/run"; SOCKET_PATH="$RUN_DIR/vault.sock"; PIPE_PATH=fixture',
        'PID_FILE="$RUN_DIR/service.pid"; LOG_FILE="$INSTALL_DIR/logs/service.log"',
        "SERVICE_SUFFIX=''; INSTALLATION_ID=fixture; PORT_ENV_LINE=''",
        _function(source, writer),
        invoke,
    )))
    assert result.returncode == 0, result.stdout + result.stderr
    return units / name, root, source, plugin


def _effective_properties(unit: Path, **changes: str) -> str:
    lines = unit.read_text(encoding="utf-8").splitlines()
    start = next(line[10:] for line in lines if line.startswith("ExecStart="))
    directory = next(line[17:] for line in lines if line.startswith("WorkingDirectory="))
    values = {
        "LoadState": "loaded", "ActiveState": "inactive", "FragmentPath": _posix(unit),
        "DropInPaths": "", "WorkingDirectory": directory,
        "ExecStart": (
            f"{{ path={shlex.split(start)[0]} ; argv[]={start} ; ignore_errors=no ; "
            "start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }"
        ),
    }
    values.update(changes)
    return "".join(f"{key}={value}\n" for key, value in values.items())


def _run_preflight(tmp_path: Path, unit: Path, root: Path, source: Path, plugin: str,
                   properties: str, *, query_fails: bool = False,
                   ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    inspected = tmp_path / "process-inspected"
    control = tmp_path / "unexpected-native-control"
    captured = tmp_path / "effective-properties"
    captured.write_text(properties, encoding="utf-8", newline="\n")
    suffix = plugin.removeprefix("agent-")
    preflight = f"_{suffix}_uninstall_preflight"
    tool = "_bridge_uninstall_tool" if suffix == "bridge" else "_finite_stop_tool"
    census = "_bridge_uninstall_processes" if suffix == "bridge" else "_finite_owned_pids"
    state = (
        'if [[ " ${_dispatch_uninstall_loaded_units[*]:-} " == *" $EXPECTED_UNIT "* ]]; '
        'then echo admission=loaded; else echo admission=unloaded; fi'
        if suffix == "dispatch" else f'echo "admission=${{_{suffix}_uninstall_unit_state}}"'
    )
    result = _shell(tmp_path, "\n".join((
        "set -euo pipefail",
        f"INSTALL_DIR={shlex.quote(_posix(root))};UNIT_DIR={shlex.quote(_posix(unit.parent))}",
        f"HOME={shlex.quote(_posix(unit.parents[3]))};EXPECTED_UNIT={shlex.quote(unit.name)}",
        "SYSTEMD_UNIT=fixture.service;SUPERVISOR_UNIT=fixture-supervisor.service",
        f"PY={shlex.quote(_posix(Path(sys.executable)))};STOP_HELPER={shlex.quote(_posix(SOURCE))}",
        f"INSPECTED={shlex.quote(_posix(inspected))};CONTROL={shlex.quote(_posix(control))}",
        f"PROPERTIES={shlex.quote(_posix(captured))};QUERY_FAILS={int(query_fails)}",
        '_fail() { echo "$1" >&2; }',
        'systemctl() { if [[ "${2:-}" != show ]]; then : > "$CONTROL"; return 99; fi; '
        '[[ " $* " == *" --all "* && " $* " == *" --no-pager "* && '
        '" $* " == *" --property=LoadState,ActiveState,FragmentPath,DropInPaths,ExecStart,WorkingDirectory "* '
        '&& "${@: -1}" == "$EXPECTED_UNIT" ]] || return 97; '
        '[[ "$QUERY_FAILS" -eq 0 ]] || return 23; cat "$PROPERTIES"; }',
        f'{tool}() {{ "$PY" -I -B "$STOP_HELPER" "$@" --root "$INSTALL_DIR" --plugin {plugin}; }}',
        f'{census}() {{ : > "$INSPECTED"; }}',
        _function(source, preflight),
        preflight,
        state,
    )))
    return result, inspected, control


@pytest.mark.parametrize("kind", ("coordinator", "supervisor", "vault"))
@pytest.mark.parametrize("mutation", (
    "none", "foreign-executable-comment", "foreign-workdir", "wrong-module",
    "duplicate-executable", "commented-executable",
))
def test_real_uninstall_preflight_binds_generated_unit_not_comment(
    tmp_path: Path, kind: str, mutation: str,
) -> None:
    unit, root, source, plugin = _written_unit(tmp_path, kind)
    original = unit.read_text(encoding="utf-8")
    properties = _effective_properties(unit)
    start = next(line for line in original.splitlines() if line.startswith("ExecStart="))
    changed = original
    if mutation == "foreign-executable-comment":
        changed = original.replace(start, start.replace(_posix(root), _posix(root) + "-foreign"))
        changed = f"# historical owner: {_posix(root)}/\n" + changed
    elif mutation == "foreign-workdir":
        changed = original.replace(
            f"WorkingDirectory={_posix(root)}", f"WorkingDirectory={_posix(root)}-foreign",
        )
    elif mutation == "wrong-module":
        changed = (
            original.replace("supervise-service.sh", "unrelated-service.sh")
            if kind == "supervisor" else
            original.replace("-m " + plugin.replace("-", "_"), "-m unrelated_module")
        )
    elif mutation == "duplicate-executable":
        changed = original.replace(start, start + "\n" + start)
    elif mutation == "commented-executable":
        changed = original.replace(start, "#" + start)
    unit.write_text(changed, encoding="utf-8")
    before = unit.read_bytes(), unit.stat().st_mtime_ns
    result, inspected, control = _run_preflight(tmp_path, unit, root, source, plugin, properties)
    assert (result.returncode == 0) is (mutation == "none"), result.stdout + result.stderr
    assert inspected.exists() is (mutation == "none")
    assert not control.exists(), "Preflight must never stop, disable, restart, or remove a service"
    assert (unit.read_bytes(), unit.stat().st_mtime_ns) == before


def test_dispatch_supervisor_registration_cannot_name_another_unit(tmp_path: Path) -> None:
    unit, root, _, plugin = _written_unit(tmp_path, "supervisor")
    unit.write_text(unit.read_text().replace(
        "--systemd-unit fixture-supervisor.service", "--systemd-unit foreign.service",
    ))
    with pytest.raises(RuntimeError, match="does not name"):
        STOP.check_unit(unit, _posix(root), plugin)


@pytest.mark.parametrize("kind", ("bridge", "coordinator", "supervisor", "vault"))
@pytest.mark.parametrize("effective", (
    "active", "inactive", "unloaded", "foreign-executable", "foreign-workdir",
    "foreign-fragment", "drop-in", "multiple-commands", "different-path",
    "missing-property", "duplicate-property", "unloaded-active", "unloaded-stale",
    "query-failure", "secret-argument",
))
def test_real_preflight_checks_manager_effective_service_ownership(
    tmp_path: Path, kind: str, effective: str,
) -> None:
    unit, root, source, plugin = _written_unit(tmp_path, kind)
    before = unit.read_bytes(), unit.stat().st_mtime_ns
    values = dict(line.split("=", 1) for line in _effective_properties(unit).splitlines())
    if effective == "active":
        values["ActiveState"] = "active"
    elif effective.startswith("unloaded"):
        values.update(LoadState="not-found", ActiveState="inactive",
                      FragmentPath="", ExecStart="", WorkingDirectory="")
        if effective == "unloaded-active":
            values["ActiveState"] = "active"
        elif effective == "unloaded-stale":
            values["WorkingDirectory"] = _posix(root)
    elif effective == "foreign-executable":
        values["ExecStart"] = values["ExecStart"].replace(
            _posix(root) + "/", _posix(root) + "-foreign/",
        )
    elif effective == "foreign-workdir":
        values["WorkingDirectory"] = _posix(root) + "-foreign"
    elif effective == "foreign-fragment":
        values["FragmentPath"] = _posix(unit) + "-foreign"
    elif effective == "drop-in":
        values["DropInPaths"] = _posix(unit.parent / "fixture.service.d" / "override.conf")
    elif effective == "multiple-commands":
        values["ExecStart"] += " " + values["ExecStart"]
    elif effective == "different-path":
        values["ExecStart"] = values["ExecStart"].replace(
            "{ path=" + _posix(root), "{ path=" + _posix(root) + "-foreign", 1,
        )
    elif effective == "missing-property":
        del values["ExecStart"]
    elif effective == "secret-argument":
        values["ExecStart"] = values["ExecStart"].replace(
            " ; ignore_errors=", " --token fixture-secret ; ignore_errors=", 1,
        )
    properties = "".join(f"{key}={value}\n" for key, value in values.items())
    if effective == "duplicate-property":
        properties += f"WorkingDirectory={_posix(root)}\n"
    result, inspected, control = _run_preflight(
        tmp_path, unit, root, source, plugin, properties, query_fails=effective == "query-failure",
    )
    accepted = effective in {"active", "inactive", "unloaded"}
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr
    assert inspected.exists() is accepted
    assert not control.exists(), "Foreign or unknown loaded properties must never authorize native control"
    assert (unit.read_bytes(), unit.stat().st_mtime_ns) == before
    if accepted:
        expected = "unloaded" if effective == "unloaded" else "loaded"
        assert f"admission={expected}" in result.stdout
    assert "fixture-secret" not in result.stdout + result.stderr


@pytest.mark.parametrize("historical", ("venv", ".venv"))
@pytest.mark.parametrize("plugin", ("agent-bridge", "agent-dispatch", "agent-vault"))
def test_closed_historical_inspection_is_not_a_launch(tmp_path: Path, historical: str, plugin: str) -> None:
    root = "/fixture/runtime"
    tail = {
        "agent-bridge": ["agent_bridge", "start"],
        "agent-dispatch": ["agent_dispatch", "serve"],
        "agent-vault": ["agent_vault.service", "--foreground", "--persistent", "--handoff-stdin"],
    }[plugin]
    argv = [f"{root}/{historical}/bin/python", "-m", *tail]
    assert STOP.admits_argv(argv, root, plugin)
    assert not STOP.admits_argv(argv, root + "-other", plugin)
    assert not STOP.admits_argv([*argv[:2], "unrelated_module", *argv[3:]], root, plugin)


def _identity(root: Path, plugin: str, birth: int = 10) -> tuple[object, tuple[str, ...]]:
    tail = {
        "agent-bridge": ["agent_bridge", "start"],
        "agent-dispatch": ["agent_dispatch", "serve"],
        "agent-vault": ["agent_vault.service", "--foreground", "--persistent", "--handoff-stdin"],
    }[plugin]
    argv = (str(root / "versions" / "1.0" / "bin" / "python"), "-m", *tail)
    raw = b"\0".join(value.encode() for value in argv) + b"\0"
    return STOP.Identity(123, birth, hashlib.sha256(raw).hexdigest(), 1000), argv


@pytest.mark.parametrize("change", ("birth", "command", "uid"))
def test_replacement_before_first_fresh_read_never_receives_a_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    original, argv = _identity(tmp_path, "agent-dispatch")
    replacement = asdict(original)
    replacement[{"birth": "birth", "command": "command_digest", "uid": "uid"}[change]] = {
        "birth": 20, "command": "0" * 64, "uid": 2000,
    }[change]
    current = STOP.Identity(**replacement)
    monkeypatch.setattr(STOP.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(STOP, "_boot", lambda: "fixture-boot")
    monkeypatch.setattr(STOP.os, "pidfd_open", lambda *_: 456, raising=False)
    monkeypatch.setattr(STOP.os, "close", lambda *_: None)
    monkeypatch.setattr(STOP, "_observe", lambda *_: (current, 0, argv))
    signals = []
    monkeypatch.setattr(STOP.signal, "pidfd_send_signal", lambda *args: signals.append(args), raising=False)
    admission = {"version": 1, "root": str(tmp_path), "plugin": "agent-dispatch",
                 "boot": "fixture-boot", "identities": [asdict(original)]}
    with pytest.raises(RuntimeError, match="replaced"):
        STOP.stop(tmp_path, "agent-dispatch", admission)
    assert not signals


def test_unknown_child_refuses_stop_and_admission_contains_no_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, argv = _identity(tmp_path, "agent-dispatch")
    monkeypatch.setattr(STOP.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(STOP, "_boot", lambda: "fixture-boot")
    rows = {123: (identity, 0, argv)}
    monkeypatch.setattr(STOP, "_inventory", lambda: rows)
    admission = STOP.capture(tmp_path, "agent-dispatch")
    assert "command_digest" in admission["identities"][0]
    assert "agent_dispatch" not in json.dumps(admission)
    rows[789] = (STOP.Identity(789, 30, "0" * 64, 1000), 123, ("unrelated",))
    with pytest.raises(RuntimeError, match="unclassified child"):
        STOP.capture(tmp_path, "agent-dispatch")


@pytest.mark.parametrize("gone", (False, True))
def test_original_process_stops_or_already_gone_settles_without_pid_signaling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gone: bool,
) -> None:
    original, argv = _identity(tmp_path, "agent-vault")
    monkeypatch.setattr(STOP.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(STOP, "_boot", lambda: "fixture-boot")
    monkeypatch.setattr(STOP, "_observe", lambda *_: None if gone else (original, 0, argv))
    monkeypatch.setattr(STOP, "_inventory", lambda: {})

    def open_descriptor(*_: object) -> int:
        if gone:
            raise ProcessLookupError
        return 456

    monkeypatch.setattr(STOP.os, "pidfd_open", open_descriptor, raising=False)
    closed = []
    monkeypatch.setattr(STOP.os, "close", lambda fd: closed.append(fd))
    signals = []
    monkeypatch.setattr(STOP.signal, "pidfd_send_signal", lambda *args: signals.append(args), raising=False)
    monkeypatch.setattr(STOP.select, "select", lambda *_: ([456], [], []))
    admission = {"version": 1, "root": str(tmp_path), "plugin": "agent-vault",
                 "boot": "fixture-boot", "identities": [asdict(original)]}
    STOP.stop(tmp_path, "agent-vault", admission)
    assert signals == ([] if gone else [(456, STOP.signal.SIGTERM, None, 0)])
    assert closed == ([] if gone else [456])


def test_exit_and_reuse_after_last_observation_cannot_signal_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original, argv = _identity(tmp_path, "agent-bridge")
    monkeypatch.setattr(STOP.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(STOP, "_boot", lambda: "fixture-boot")
    monkeypatch.setattr(STOP.os, "pidfd_open", lambda *_: 456, raising=False)
    monkeypatch.setattr(STOP.os, "close", lambda *_: None)
    monkeypatch.setattr(STOP, "_inventory", lambda: {})
    observed = []
    replacement = []

    def observe(_: int) -> tuple[object, int, tuple[str, ...]]:
        observed.append(True)
        if len(observed) == 2:
            replacement.append(True)
        return original, 0, argv

    def send(fd: int, *_: object) -> None:
        assert fd == 456 and replacement
        # The descriptor still names the departed admitted process, not the
        # replacement now occupying its numeric PID.
        raise ProcessLookupError

    monkeypatch.setattr(STOP, "_observe", observe)
    monkeypatch.setattr(STOP.signal, "pidfd_send_signal", send, raising=False)
    admission = {"version": 1, "root": str(tmp_path), "plugin": "agent-bridge",
                 "boot": "fixture-boot", "identities": [asdict(original)]}
    STOP.stop(tmp_path, "agent-bridge", admission)
    assert len(observed) == 2


def test_capture_exports_only_digest_not_secret_bearing_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = (str(tmp_path / "versions" / "1.0" / "bin" / "python"),
            "-m", "agent_dispatch", "serve", "--token", "fixture-secret")
    raw = b"\0".join(value.encode() for value in argv) + b"\0"

    class ProcessFiles:
        def __truediv__(self, _: object) -> "ProcessFiles":
            return self

        def read_bytes(self) -> bytes:
            return raw

        def stat(self) -> object:
            return type("Owner", (), {"st_uid": 1000})()

    monkeypatch.setattr(STOP, "Path", lambda *_: ProcessFiles())
    monkeypatch.setattr(STOP, "_stat", lambda *_: (10, 0, "S"))
    monkeypatch.setattr(STOP.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(STOP, "_boot", lambda: "fixture-boot")
    record = STOP._observe(123)
    monkeypatch.setattr(STOP, "_inventory", lambda: {123: record})
    admission = STOP.capture(tmp_path, "agent-dispatch")
    serialized = json.dumps(admission)
    assert "fixture-secret" not in serialized and "--token" not in serialized
    assert admission["identities"][0]["command_digest"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("plugin", ("agent-bridge", "agent-dispatch", "agent-vault"))
@pytest.mark.parametrize("phase", ("before-first-read", "after-service-stop"))
def test_native_caller_carries_original_identity_through_pid_reuse(
    tmp_path: Path, plugin: str, phase: str,
) -> None:
    kernel = tmp_path / "kernel.json"
    kernel.write_text('{"birth":10,"signals":[]}')
    model = tmp_path / "kernel_model.py"
    model.write_text(
        "import argparse,hashlib,importlib.util,json,os,pathlib,sys\n"
        f"spec=importlib.util.spec_from_file_location('stop_model',{str(SOURCE)!r})\n"
        "m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)\n"
        "p=argparse.ArgumentParser();p.add_argument('action');p.add_argument('--root');"
        "p.add_argument('--plugin');p.add_argument('--admission');p.add_argument('--pid-file');a=p.parse_args()\n"
        f"file=pathlib.Path({str(kernel)!r});state=json.loads(file.read_text());root=pathlib.Path(a.root)\n"
        "tails={'agent-bridge':['agent_bridge','start'],'agent-dispatch':['agent_dispatch','serve'],"
        "'agent-vault':['agent_vault.service','--foreground','--persistent','--handoff-stdin']}\n"
        "argv=(str(root/'versions'/'1.0'/'bin'/'python'),'-m',*tails[a.plugin])\n"
        "digest=hashlib.sha256(b'\\0'.join(v.encode() for v in argv)+b'\\0').hexdigest()\n"
        "identity=m.Identity(123,state['birth'],digest,1000)\n"
        "os.getuid=lambda:1000;m._boot=lambda:'fixture-boot';m._observe=lambda pid:(identity,0,argv)\n"
        "m._inventory=lambda:{123:(identity,0,argv)};os.pidfd_open=lambda *args:456;os.close=lambda fd:None\n"
        "def send(*args):\n"
        " state['signals'].append(state['birth']);file.write_text(json.dumps(state))\n"
        "m.signal.pidfd_send_signal=send;m.select.select=lambda *args:([456],[],[])\n"
        "try:\n"
        " if a.action=='replace':state['birth']=20;file.write_text(json.dumps(state))\n"
        " elif a.action=='capture':print(json.dumps(m.capture(root,a.plugin)))\n"
        " elif a.action=='pids':print(123)\n"
        " else:m.stop(root,a.plugin,json.loads(a.admission))\n"
        "except RuntimeError as error:print(str(error),file=sys.stderr);raise SystemExit(1)\n",
        encoding="utf-8",
    )
    bridge = plugin == "agent-bridge"
    source = REPO / "plugins" / plugin / "scripts" / ("install.sh" if bridge else "uninstall-runtime.sh")
    names = ("_bridge_uninstall_processes", "_bridge_uninstall_stop_owned") if bridge else (
        "_finite_owned_pids", "_finite_stop_owned",
    )
    tool = "_bridge_uninstall_tool" if bridge else "_finite_stop_tool"
    root = tmp_path / "runtime"
    home = tmp_path / "home"
    unit = home / ".config" / "systemd" / "user" / "fixture.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("fixture owned unit")
    call = f'"$PY" -B "$MODEL" replace --root "$INSTALL_DIR" --plugin {plugin}'
    script = "\n".join((
        "set -uo pipefail",
        f"PY={shlex.quote(_posix(Path(sys.executable)))};MODEL={shlex.quote(_posix(model))}",
        f"HOME={shlex.quote(_posix(home))};INSTALL_DIR={shlex.quote(_posix(root))}",
        'PID_FILE="$INSTALL_DIR/service.pid"; SYSTEMD_UNIT=fixture.service',
        "_bridge_uninstall_unit_state=loaded",
        f'{tool}() {{ "$PY" -B "$MODEL" "$@" --root "$INSTALL_DIR" --plugin {plugin}; }}',
        f"systemctl() {{ {call if phase == 'after-service-stop' else ':'}; }}",
        *[_function(source, name) for name in names],
        f"{names[0]} >/dev/null || exit $?",
        call if phase == "before-first-read" else ("" if bridge else "systemctl --user stop fixture"),
        f"{names[1]}",
    ))
    result = _shell(tmp_path, script)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "replaced" in result.stderr, result.stdout + result.stderr
    assert json.loads(kernel.read_text())["signals"] == []


@pytest.mark.skipif(not sys.platform.startswith("linux") or not hasattr(os, "pidfd_open"),
                    reason="Requires real Linux pidfds")
@pytest.mark.parametrize("mismatch", (False, True))
@pytest.mark.parametrize("plugin,mode", (
    ("agent-bridge", "module"), ("agent-bridge", "console"),
    ("agent-vault", "module"),
))
def test_real_pidfd_same_process_stop_and_original_birth_refusal(
    tmp_path: Path, mismatch: bool, plugin: str, mode: str,
) -> None:
    root = tmp_path / "runtime"
    executable = root / "versions" / "1.0" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    module = "agent_bridge"
    tail = ["start"]
    package = tmp_path / "agent_bridge.py"
    if plugin == "agent-vault":
        folder = tmp_path / "agent_vault"
        folder.mkdir()
        (folder / "__init__.py").touch()
        package = folder / "service.py"
        module = "agent_vault.service"
        tail = ["--foreground", "--persistent", "--handoff-stdin"]
    package.write_text("import time\ntime.sleep(90)\n")
    command = [str(executable), "-B", "-m", module, *tail]
    if mode == "console":
        console = executable.with_name("agent-bridge")
        console.write_text(f"#!{executable}\nimport time\ntime.sleep(90)\n")
        console.chmod(0o755)
        command = [str(console), "start"]
    child = subprocess.Popen(  # noqa: S603 - owned fixture interpreter/module only
        command, cwd=tmp_path,
    )
    try:
        admission = STOP.capture(root, plugin)
        assert [value["pid"] for value in admission["identities"]] == [child.pid]
        if mismatch:
            admission["identities"][0]["birth"] += 1
            with pytest.raises(RuntimeError, match="replaced"):
                STOP.stop(root, plugin, admission)
            assert child.poll() is None
        else:
            STOP.stop(root, plugin, admission)
            child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
