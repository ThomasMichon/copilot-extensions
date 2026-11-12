"""Real uv front-door controls; no production application or service is installed."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import venv
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
from runtime_application_binstubs import global_binstubs, write_slot_binstubs
from runtime_application_project import emit, install_callbacks
import runtime_application_install
from local_install_profile import source_inputs as install_source
from installer_wheel_snapshot import retain_wheel_inputs

engine = install_source.engine


@pytest.mark.parametrize("abi,target", [
    ("win32", "x86"),
    ("win_amd64", "x86_64"),
    ("win_arm64", "aarch64"),
])
@pytest.mark.parametrize("implementation", [None, "cpython"])
def test_emitted_launchers_follow_interpreter_abi_not_host_machine(
    tmp_path, abi, target, implementation,
):
    from packaging.markers import Marker

    wheel = tmp_path / "fixture_app-1.0.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture")
    environment = {
        "sys_platform": "win32", "platform_machine": "AMD64", "python_full_version": "3.13.0",
        **({"implementation_name": implementation} if implementation is not None else {}),
    }
    original = dict(environment)
    application = emit(
        tmp_path / "application", distribution="fixture-app", version="1.0.0",
        requires_python=">=3.13", commands={"fixture-app": "fixture_app:main"},
        wheels={"fixture-app": wheel},
        target_environment=environment,
        target_tags=[f"cp313-cp313-{abi}", "py3-none-any"],
    )
    assert (application / ".python-version").read_text(encoding="utf-8") == (
        f"{implementation or 'cpython'}-3.13.0-windows-{target}-none\n"
    )
    for name in ("fixture-app.ps1", "fixture-app.cmd", "fixture-app.sh",
                 "install.ps1", "install.cmd", "install.sh"):
        launcher = (application / name).read_text(encoding="utf-8")
        assert f"cpython-3.13.0-windows-{target}-none" in launcher
        assert "--python-platform" not in launcher
    constraints = next(
        line.removeprefix("environments = ")
        for line in (application / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("environments = ")
    )
    assert json.loads(constraints) == ["sys_platform == 'win32' and platform_machine == 'AMD64'"]
    marker = Marker(json.loads(constraints)[0])
    assert marker.evaluate({"sys_platform": "win32", "platform_machine": "AMD64"})
    assert not marker.evaluate({"sys_platform": "win32", "platform_machine": "aarch64"})
    assert not marker.evaluate({"sys_platform": "linux", "platform_machine": "AMD64"})
    assert environment == original


def test_linux_application_keeps_plain_python_request_and_exact_marker_constraints(tmp_path):
    wheel = tmp_path / "fixture_app-1.0.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture")
    environment = {
        "sys_platform": "linux", "platform_machine": "aarch64", "python_full_version": "3.13.0",
        "implementation_name": "pypy",
    }
    original = dict(environment)
    application = emit(
        tmp_path / "application", distribution="fixture-app", version="1.0.0",
        requires_python=">=3.13", commands={"fixture-app": "fixture_app:main"},
        wheels={"fixture-app": wheel}, target_environment=environment, target_tags=["py3-none-any"],
    )
    assert (application / ".python-version").read_text(encoding="utf-8") == "3.13.0\n"
    assert "sys_platform == 'linux' and platform_machine == 'aarch64'" in (
        application / "pyproject.toml"
    ).read_text(encoding="utf-8")
    for name in ("fixture-app.ps1", "fixture-app.cmd", "fixture-app.sh",
                 "install.ps1", "install.cmd", "install.sh"):
        assert "--python-platform" not in (application / name).read_text(encoding="utf-8")
    assert environment == original


@pytest.mark.parametrize("tags", [None, ["py3-none-any"], ["cp313-cp313-win32", "cp313-cp313-win_amd64"]])
def test_windows_application_refuses_missing_or_ambiguous_interpreter_abi(tmp_path, tags):
    with pytest.raises(ValueError, match="unambiguous Windows Python ABI"):
        emit(
            tmp_path / "application", distribution="fixture-app", version="1.0.0",
            requires_python=">=3.13", commands={"fixture-app": "fixture_app:main"}, wheels={},
            target_environment={"sys_platform": "win32", "platform_machine": "AMD64"},
            target_tags=tags,
        )
    assert not (tmp_path / "application").exists()


@pytest.mark.parametrize("damage", [
    "none", "missing", "extra", "bytes", "directory", "deadline", "permission",
])
def test_application_wheel_copy_damage_requires_distinct_repair_not_deadline_replay(
    tmp_path, monkeypatch, damage,
):
    from installer_wheel_inputs import WheelDeadlineError
    content = b"fixture wheel bytes"
    wheels = tmp_path / "wheels"
    if damage != "missing":
        wheels.mkdir()
        path = wheels / "fixture.whl"
        if damage == "directory":
            path.mkdir()
        else:
            path.write_bytes(b"damaged" if damage == "bytes" else content)
        if damage == "extra":
            (wheels / "unexpected.whl").write_bytes(content)
    inputs = SimpleNamespace(
        distributions=[("fixture", "1.0", "fixture.whl", "sha256:" + hashlib.sha256(content).hexdigest())],
        deadline=0 if damage == "deadline" else time.monotonic() + 30,
    )
    if damage == "permission":
        def denied(_path):
            raise PermissionError("fixture permission denied")
        monkeypatch.setattr(sys.modules[engine.verify_application_wheels.__module__], "_open", denied)
    if damage == "none":
        engine.verify_application_wheels(inputs, tmp_path)
    else:
        failure = {
            "deadline": WheelDeadlineError, "permission": PermissionError,
        }.get(damage, engine.HealthRepairRequired)
        with pytest.raises(failure):
            engine.verify_application_wheels(inputs, tmp_path)


@pytest.mark.parametrize("variant", ["cp313d", "cp313t"])
def test_windows_application_refuses_variants_even_with_standard_compatibility_tags(tmp_path, variant):
    with pytest.raises(ValueError, match="standard CPython target"):
        emit(
            tmp_path / "application", distribution="fixture-app", version="1.0.0",
            requires_python=">=3.13", commands={"fixture-app": "fixture_app:main"}, wheels={},
            target_environment={
                "sys_platform": "win32", "platform_machine": "AMD64",
                "python_full_version": "3.13.0", "implementation_name": "cpython",
            },
            target_tags=[f"cp313-{variant}-win_amd64", "cp313-cp313-win_amd64", "py3-none-any"],
        )
    assert not (tmp_path / "application").exists()


def test_windows_application_refuses_non_cpython_target(tmp_path):
    with pytest.raises(ValueError, match="standard CPython target"):
        emit(
            tmp_path / "application", distribution="fixture-app", version="1.0.0",
            requires_python=">=3.13", commands={"fixture-app": "fixture_app:main"}, wheels={},
            target_environment={
                "sys_platform": "win32", "platform_machine": "AMD64",
                "python_full_version": "3.13.0", "implementation_name": "pypy",
            },
            target_tags=["cp313-cp313-win_amd64", "py3-none-any"],
        )
    assert not (tmp_path / "application").exists()


@pytest.fixture
def install_transaction(tmp_path, monkeypatch):
    root = tmp_path / "root"
    slot = root / "versions" / "2.0"
    (slot / ".venv").mkdir(parents=True)
    (slot / ".venv" / "pyvenv.cfg").write_text("fixture", encoding="utf-8")
    (root / "current-version").write_bytes(b"1.0\n")
    (root / "last-known-good").write_bytes(b"0.9\n")
    record = {
        "name": "fixture-app", "module": "fixture_app", "commands": ["fixture-app"],
        "root_environment": "FIXTURE_APP_HOME", "default_directory": ".fixture-app",
        "setup": [], "start": ["--start"], "construction": {"payload_hash": "fixture"},
    }
    (slot / "application.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(runtime_application_install.sys, "prefix", str(slot / ".venv"))
    actual_is_complete = engine.runtime.is_complete
    monkeypatch.setattr(engine.runtime, "is_complete", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        runtime_application_install, "_resources", lambda _slot: (engine.runtime, engine),
    )

    @contextmanager
    def qualified(*_, **_kwargs):
        yield engine.runtime, lambda: None

    monkeypatch.setattr(runtime_application_install, "_qualified", qualified)
    monkeypatch.setattr(
        runtime_application_install, "_load",
        lambda *_: SimpleNamespace(global_binstubs=global_binstubs),
    )
    return SimpleNamespace(
        root=root, slot=slot, global_bin=root / "global", record=record,
        publication=engine.runtime._runtime_component("versioned_runtime_publication"),
        actual_is_complete=actual_is_complete,
    )


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
@pytest.mark.parametrize("changed", [
    None, "current-version", "last-known-good", "command", "receipt", "completion",
])
def test_publication_current_requires_complete_selected_receipt_and_all_commands(
    install_transaction, monkeypatch, ending, changed,
):
    fixture = install_transaction
    fixture.record["commands"].append("fixture-alias")
    fixture.record["setup"] = []
    fixture.record["start"] = []
    (fixture.slot / "application.json").write_text(json.dumps(fixture.record), encoding="utf-8")
    monkeypatch.setattr(engine.runtime, "is_complete", fixture.actual_is_complete)
    marker = engine.runtime.mark_complete(
        fixture.root, fixture.slot.name,
        payload_hash=fixture.record["construction"]["payload_hash"],
    )
    fixture.global_bin.mkdir()
    commands = {}
    for name in fixture.record["commands"]:
        commands.update(global_binstubs(
            name, fixture.record["root_environment"], fixture.record["default_directory"],
            root=fixture.root,
        ))
    for name, content in commands.items():
        (fixture.global_bin / name).write_bytes(content.encode("utf-8"))
    selectors = [fixture.root / name for name in ("current-version", "last-known-good")]
    for path in selectors:
        path.write_bytes(fixture.slot.name.encode("utf-8") + ending)
    receipt = fixture.slot / "install-success.json"
    receipt.write_bytes(json.dumps({
        "state": "installed", "version": fixture.slot.name,
    }, sort_keys=True).encode("utf-8") + b"\n")
    if changed in {"current-version", "last-known-good"}:
        (fixture.root / changed).write_bytes(b"another-generation\n")
    elif changed == "command":
        (fixture.global_bin / "fixture-alias.cmd").write_bytes(b"changed secondary command\n")
    elif changed == "receipt":
        receipt.write_bytes(b'{"state":"installed","version":"another-generation"}\n')
    elif changed == "completion":
        content = json.loads(marker.read_bytes())
        content["payload_hash"] = "another-payload"
        marker.write_text(json.dumps(content), encoding="utf-8")
    watched = [*selectors, receipt, marker, *(fixture.global_bin / name for name in commands)]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in watched}

    def forbidden(*_args, **_kwargs):
        pytest.fail("publication proof must not execute callbacks or publish")

    monkeypatch.setattr(runtime_application_install, "_run", forbidden)
    monkeypatch.setattr(runtime_application_install, "_write", forbidden)
    monkeypatch.setattr(fixture.publication, "publish_selectors", forbidden)
    assert runtime_application_install._publication_current(
        fixture.slot, fixture.record, global_bin=fixture.global_bin,
    ) is (changed is None)
    if changed is None:
        assert runtime_application_install.install(
            fixture.slot, stage=False, global_bin=fixture.global_bin,
        ) == {"state": "installed", "slot": str(fixture.slot)}
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in watched} == before


@pytest.mark.parametrize("setup,start,arguments", [
    ([], [], ()),
    (["--setup"], [], ()),
    ([], ["--start"], ()),
    (["--setup"], ["--start"], ()),
    (["--setup"], [], ("--config", "fixture")),
    ([], ["--start"], ("--config", "fixture")),
])
def test_current_publication_reenters_configuration_and_readiness_owners(
    install_transaction, monkeypatch, setup, start, arguments,
):
    fixture = install_transaction
    fixture.record.update(setup=setup, start=start)
    (fixture.slot / "application.json").write_text(json.dumps(fixture.record), encoding="utf-8")
    calls = []

    def owner(_record, command, root, slot):
        assert root == fixture.root and slot == fixture.slot
        if command:
            calls.append(tuple(command))

    monkeypatch.setattr(runtime_application_install, "_run", owner)
    expected = {"state": "installed", "slot": str(fixture.slot)}
    assert runtime_application_install.install(
        fixture.slot, stage=False, global_bin=fixture.global_bin,
    ) == expected
    assert runtime_application_install._publication_current(
        fixture.slot, fixture.record, global_bin=fixture.global_bin,
    )
    calls.clear()
    assert runtime_application_install.install(
        fixture.slot, stage=False, global_bin=fixture.global_bin, arguments=arguments,
    ) == expected
    assert calls == [
        tuple([*callback, *arguments]) for callback in (setup, start) if callback
    ], f"setup={setup!r} start={start!r} arguments={arguments!r} owner_calls={calls!r}"


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("phase", ["setup", "launcher", "publication"])
def test_install_transaction_before_start_failure_restores_prior_state(
    install_transaction, monkeypatch, phase, existing,
):
    fixture = install_transaction
    starts = []
    previous = {}
    if existing:
        fixture.global_bin.mkdir()
        for name in global_binstubs(
            fixture.record["name"], fixture.record["root_environment"],
            fixture.record["default_directory"], root=fixture.root,
        ):
            previous[name] = b"unrecognized old launcher: " + name.encode() + b"\r\n"
            (fixture.global_bin / name).write_bytes(previous[name])

    def command(_record, arguments, _root, _slot):
        if arguments == ["--setup"]:
            raise RuntimeError("setup refused")
        if arguments:
            starts.append(arguments)

    monkeypatch.setattr(runtime_application_install, "_run", command)
    if phase == "setup":
        fixture.record["setup"] = ["--setup"]
        (fixture.slot / "application.json").write_text(json.dumps(fixture.record), encoding="utf-8")
    elif phase == "launcher":
        write = runtime_application_install._write
        failed = False

        def partial_launchers(path, content):
            nonlocal failed
            if path.name.endswith(".ps1") and not failed:
                failed = True
                raise RuntimeError("launcher refused")
            write(path, content)

        monkeypatch.setattr(runtime_application_install, "_write", partial_launchers)
    else:
        def partial_publication(root, version, *_):
            (root / "current-version").write_bytes((version + "\n").encode())
            raise RuntimeError("publication refused")

        monkeypatch.setattr(fixture.publication, "publish_selectors", partial_publication)
    with pytest.raises(RuntimeError, match=phase + " refused"):
        runtime_application_install.install(fixture.slot, stage=False, global_bin=fixture.global_bin)
    assert starts == []
    assert (fixture.root / "current-version").read_bytes() == b"1.0\n"
    assert (fixture.root / "last-known-good").read_bytes() == b"0.9\n"
    assert {
        path.name: path.read_bytes() for path in fixture.global_bin.glob("*")
        if path.name != ".publication-leases"
    } == previous
    assert not (fixture.slot / "install-success.json").exists()


@pytest.mark.parametrize("with_start", [False, True])
def test_install_replaces_all_declared_old_launchers_without_incumbent_provenance(
    install_transaction, monkeypatch, with_start,
):
    fixture = install_transaction
    fixture.record["commands"].append("fixture-alias")
    fixture.record["start"] = ["--start"] if with_start else []
    (fixture.slot / "application.json").write_text(json.dumps(fixture.record), encoding="utf-8")
    commands = {}
    for name in fixture.record["commands"]:
        commands.update(global_binstubs(
            name, fixture.record["root_environment"],
            fixture.record["default_directory"], root=fixture.root,
        ))
    fixture.global_bin.mkdir()
    for name in commands:
        (fixture.global_bin / name).write_bytes(b"unknown legacy launcher\n")
    unrelated = fixture.global_bin / "another-command"
    unrelated.write_bytes(b"unrelated command\n")
    data = fixture.root / "application.db"
    data.write_bytes(b"preserved application state")
    starts = []

    def command(_record, arguments, root, slot):
        if arguments:
            assert (root / "current-version").read_text().strip() == slot.name
            assert all((fixture.global_bin / name).read_bytes() == text.encode()
                       for name, text in commands.items())
            starts.append(arguments)

    monkeypatch.setattr(runtime_application_install, "_run", command)
    result = runtime_application_install.install(
        fixture.slot, stage=False, global_bin=fixture.global_bin,
    )
    assert result["state"] == "installed"
    assert starts == ([["--start"]] if with_start else [])
    assert all((fixture.global_bin / name).read_bytes() == text.encode()
               for name, text in commands.items())
    assert (fixture.root / "current-version").read_bytes() == b"2.0\n"
    assert (fixture.root / "last-known-good").read_bytes() == b"2.0\n"
    assert (fixture.slot / "install-success.json").is_file()
    assert unrelated.read_bytes() == b"unrelated command\n"
    assert data.read_bytes() == b"preserved application state"


def test_install_replacement_preserves_launcher_changed_during_setup(
    install_transaction, monkeypatch,
):
    fixture = install_transaction
    fixture.global_bin.mkdir()
    command = fixture.global_bin / "fixture-app"
    command.write_bytes(b"old launcher\n")
    calls = []

    def setup(_record, arguments, _root, _slot):
        calls.append(arguments)
        command.write_bytes(b"concurrent replacement\n")

    monkeypatch.setattr(runtime_application_install, "_run", setup)
    with pytest.raises(RuntimeError, match="launcher changed before publication"):
        runtime_application_install.install(fixture.slot, stage=False, global_bin=fixture.global_bin)
    assert len(calls) == 1
    assert command.read_bytes() == b"concurrent replacement\n"
    assert [path for path in fixture.global_bin.iterdir() if path.name != ".publication-leases"] == [command]
    assert (fixture.root / "current-version").read_bytes() == b"1.0\n"
    assert (fixture.root / "last-known-good").read_bytes() == b"0.9\n"
    assert not (fixture.slot / "install-success.json").exists()


@pytest.mark.parametrize("kind", ["directory", "oversized"])
def test_install_replacement_refuses_nonordinary_or_unbounded_launcher(
    install_transaction, monkeypatch, kind,
):
    fixture = install_transaction
    fixture.global_bin.mkdir()
    command = fixture.global_bin / "fixture-app"
    if kind == "directory":
        command.mkdir()
    else:
        command.write_bytes(b"x" * 65537)
    calls = []
    monkeypatch.setattr(runtime_application_install, "_run", lambda *_: calls.append(True))
    with pytest.raises(RuntimeError, match="not an ordinary file"):
        runtime_application_install.install(fixture.slot, stage=False, global_bin=fixture.global_bin)
    assert not calls
    assert command.is_dir() if kind == "directory" else command.stat().st_size == 65537
    assert (fixture.root / "current-version").read_bytes() == b"1.0\n"
    assert (fixture.root / "last-known-good").read_bytes() == b"0.9\n"
    assert not (fixture.slot / "install-success.json").exists()


def test_install_transaction_verifies_inputs_outside_selector_update(
    install_transaction, monkeypatch,
):
    fixture = install_transaction
    verified = []

    def unchanged():
        with engine.runtime._publication_lease(fixture.root, timeout=0):
            verified.append(True)

    @contextmanager
    def qualified(*_, **_kwargs):
        yield engine.runtime, unchanged

    monkeypatch.setattr(runtime_application_install, "_qualified", qualified)
    monkeypatch.setattr(runtime_application_install, "_run", lambda *_: None)
    runtime_application_install.install(fixture.slot, stage=False, global_bin=fixture.global_bin)
    assert verified
    assert (fixture.root / "last-known-good").read_bytes() == b"2.0\n"


@pytest.mark.parametrize("serving_committed", [False, True])
def test_install_transaction_unknown_start_retains_selection_not_last_good(
    install_transaction, monkeypatch, serving_committed,
):
    fixture = install_transaction
    serving = fixture.root / "serving-fixture"
    cause = subprocess.TimeoutExpired(["fixture-start"], 180)

    def start(_record, arguments, root, slot):
        if not arguments:
            return
        assert (root / "current-version").read_text().strip() == slot.name
        assert (root / "last-known-good").read_bytes() == b"0.9\n"
        if serving_committed:
            serving.write_text("fixture models accepted work", encoding="utf-8")
        raise cause

    monkeypatch.setattr(runtime_application_install, "_run", start)
    with pytest.raises(RuntimeError, match="startup outcome is unknown") as failure:
        runtime_application_install.install(fixture.slot, stage=False, global_bin=fixture.global_bin)
    assert failure.value.__cause__ is cause
    assert (fixture.root / "current-version").read_bytes() == b"2.0\n"
    assert (fixture.root / "last-known-good").read_bytes() == b"0.9\n"
    assert serving.is_file() == serving_committed
    assert (fixture.global_bin / "fixture-app.cmd").is_file()
    assert not (fixture.slot / "install-success.json").exists()


@pytest.mark.parametrize("reply_lost", [False, True])
@pytest.mark.parametrize("changed_selection", [False, True])
def test_install_transaction_unknown_start_preserves_concurrent_selector_winner(
    install_transaction, monkeypatch, reply_lost, changed_selection,
):
    fixture = install_transaction
    winner = b"concurrent owner's command\n"

    def start(_record, arguments, root, _slot):
        if not arguments:
            return
        if changed_selection:
            (root / "current-version").write_bytes(b"3.0\n")
            (root / "last-known-good").write_bytes(b"2.9\n")
        (fixture.global_bin / "fixture-app.cmd").write_bytes(winner)
        if reply_lost:
            raise RuntimeError("lost startup reply")

    monkeypatch.setattr(runtime_application_install, "_run", start)
    with pytest.raises(RuntimeError, match="startup outcome is unknown"):
        runtime_application_install.install(fixture.slot, stage=False, global_bin=fixture.global_bin)
    assert (fixture.root / "current-version").read_bytes() == (
        b"3.0\n" if changed_selection else b"2.0\n"
    )
    assert (fixture.root / "last-known-good").read_bytes() == (
        b"2.9\n" if changed_selection else b"0.9\n"
    )
    assert (fixture.global_bin / "fixture-app.cmd").read_bytes() == winner
    assert not (fixture.slot / "install-success.json").exists()


def test_install_transaction_pre_start_failure_preserves_concurrent_winner(
    install_transaction, monkeypatch,
):
    fixture = install_transaction
    monkeypatch.setattr(runtime_application_install, "_run", lambda *_: None)

    def publication(root, *_):
        (root / "current-version").write_bytes(b"3.0\n")
        (root / "last-known-good").write_bytes(b"2.9\n")
        raise RuntimeError("publication outcome changed")

    monkeypatch.setattr(fixture.publication, "publish_selectors", publication)
    with pytest.raises(RuntimeError, match="selector changed during recovery"):
        runtime_application_install.install(fixture.slot, stage=False, global_bin=fixture.global_bin)
    assert (fixture.root / "current-version").read_bytes() == b"3.0\n"
    assert (fixture.root / "last-known-good").read_bytes() == b"2.9\n"
    assert (fixture.global_bin / "fixture-app.cmd").is_file()
    assert not (fixture.slot / "install-success.json").exists()


@pytest.mark.parametrize("phase", ["publication", "launcher"])
def test_failed_update_recovers_before_another_installer_can_commit(
    install_transaction, monkeypatch, phase,
):
    fixture = install_transaction
    lock = engine.runtime._publication_lease
    publish = fixture.publication.publish_selectors
    write = runtime_application_install._write
    won = []
    winner = fixture.slot.name if phase == "publication" else "3.0"
    commands = global_binstubs(
        fixture.record["name"], fixture.record["root_environment"],
        fixture.record["default_directory"], root=fixture.root,
    )

    @contextmanager
    def competing_update(root):
        try:
            with lock(root):
                yield
        finally:
            if not won:
                won.append(True)
                with lock(root):
                    for name, content in commands.items():
                        (fixture.global_bin / name).write_bytes(content.encode("utf-8"))
                    publish(root, winner, None, lambda home, version: (
                        home / "last-known-good"
                    ).write_bytes((version + "\n").encode("utf-8")))

    def failed_publication(*args):
        publish(*args)
        raise RuntimeError("publication refused")

    def failed_launcher(path, content):
        if path.name.endswith(".ps1"):
            raise RuntimeError("launcher refused")
        write(path, content)

    monkeypatch.setattr(engine.runtime, "_publication_lease", competing_update)
    monkeypatch.setattr(runtime_application_install, "_run", lambda *_: None)
    if phase == "publication":
        monkeypatch.setattr(fixture.publication, "publish_selectors", failed_publication)
    else:
        monkeypatch.setattr(runtime_application_install, "_write", failed_launcher)
    with pytest.raises(RuntimeError, match=phase + " refused"):
        runtime_application_install.install(fixture.slot, stage=False, global_bin=fixture.global_bin)
    assert won
    assert (fixture.root / "current-version").read_text().strip() == winner
    assert (fixture.root / "last-known-good").read_text().strip() == winner
    assert all((fixture.global_bin / name).read_bytes() == text.encode("utf-8")
               for name, text in commands.items())


def test_shared_launchers_serialize_recovery_across_distinct_installation_roots(
    install_transaction, monkeypatch, tmp_path,
):
    fixture = install_transaction
    commands = global_binstubs(
        fixture.record["name"], fixture.record["root_environment"],
        fixture.record["default_directory"], root=fixture.root,
    )
    fixture.global_bin.mkdir()
    for name in commands:
        (fixture.global_bin / name).write_bytes(b"old shared launcher\n")
    other = tmp_path / "other" / "versions" / "3.0"
    (other / ".venv").mkdir(parents=True)
    (other / ".venv/pyvenv.cfg").write_text("fixture", encoding="utf-8")
    record = {**fixture.record, "start": []}
    (other / "application.json").write_text(json.dumps(record), encoding="utf-8")
    program = """
import contextlib,json,pathlib,sys
library,runtime_library,slot,commands=map(pathlib.Path,sys.argv[1:])
sys.path[:0]=[str(library),str(runtime_library)]
import runtime_application_install as app
import versioned_runtime as runtime
from runtime_application_binstubs import global_binstubs
from types import SimpleNamespace
sys.prefix=str(slot/'.venv')
runtime.is_complete=lambda *a,**k:True
@contextlib.contextmanager
def qualified(*a,**k):
    yield runtime,lambda:None
app._qualified=qualified
app._load=lambda *a:SimpleNamespace(global_binstubs=global_binstubs)
app._run=lambda *a:None
lease=runtime._publication_lease
@contextlib.contextmanager
def entered(root):
    with lease(root):
        print('contending',flush=True)
        yield
runtime._publication_lease=entered
print(json.dumps(app.install(slot,stage=False,global_bin=commands)),flush=True)
"""
    children = []
    write = runtime_application_install._write

    def fail_after_competitor_enters(path, content):
        if path.name.endswith(".ps1"):
            raise RuntimeError("launcher refused")
        if not children:
            child = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", program,
                 str(Path(runtime_application_install.__file__).parent),
                 str(Path(engine.runtime.__file__).parent), str(other),
                 str(fixture.global_bin)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            children.append(child)
            assert child.stdout.readline().strip() == "contending", child.communicate(timeout=5)
            with pytest.raises(subprocess.TimeoutExpired):
                child.wait(timeout=1)
        write(path, content)

    monkeypatch.setattr(runtime_application_install, "_write", fail_after_competitor_enters)
    monkeypatch.setattr(runtime_application_install, "_run", lambda *_: None)
    try:
        with pytest.raises(RuntimeError, match="launcher refused"):
            runtime_application_install.install(fixture.slot, stage=False, global_bin=fixture.global_bin)
        child, = children
        output, error = child.communicate(timeout=10)
        assert child.returncode == 0, output + error
        assert json.loads(output)["state"] == "installed"
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
    expected = global_binstubs(
        record["name"], record["root_environment"], record["default_directory"], root=other.parent.parent,
    )
    assert all((fixture.global_bin / name).read_bytes() == text.encode("utf-8")
               for name, text in expected.items())
    assert (fixture.root / "current-version").read_bytes() == b"1.0\n"


def test_install_without_start_commits_one_update_after_unlocked_verification(
    install_transaction, monkeypatch,
):
    fixture = install_transaction
    fixture.record["start"] = []
    (fixture.slot / "application.json").write_text(json.dumps(fixture.record), encoding="utf-8")
    checks = []
    updates = []
    lock = engine.runtime._publication_lease

    def unchanged():
        checks.append(True)
        with lock(fixture.root, timeout=0):
            pass

    @contextmanager
    def update(root):
        updates.append(True)
        with lock(root):
            yield

    @contextmanager
    def qualified(*_, **_kwargs):
        yield engine.runtime, unchanged

    monkeypatch.setattr(runtime_application_install, "_qualified", qualified)
    monkeypatch.setattr(engine.runtime, "_publication_lease", update)
    monkeypatch.setattr(runtime_application_install, "_run", lambda *_: None)
    runtime_application_install.install(fixture.slot, stage=False, global_bin=fixture.global_bin)
    assert checks and len(updates) == 1
    assert (fixture.root / "current-version").read_bytes() == b"2.0\n"
    assert (fixture.root / "last-known-good").read_bytes() == b"2.0\n"
    assert (fixture.global_bin / "fixture-app.cmd").is_file()
    assert (fixture.slot / "install-success.json").is_file()


def test_fleet_client_install_publishes_without_a_host_callback(install_transaction, monkeypatch):
    fixture = install_transaction
    setup, start = install_callbacks("fleet-manager")
    fixture.record.update(
        name="fleet-manager", module="fleet_manager.__main__:main", commands=["fleet-manager"],
        root_environment="FLEET_MANAGER_HOME", default_directory=".fleet-manager",
        setup=setup, start=start,
    )
    (fixture.slot / "application.json").write_text(json.dumps(fixture.record), encoding="utf-8")

    def refuse_callback(_record, arguments, *_):
        if arguments:
            pytest.fail("ordinary Fleet client installation must not invoke a hosting callback")

    monkeypatch.setattr(runtime_application_install, "_run", refuse_callback)
    runtime_application_install.install(fixture.slot, stage=False, global_bin=fixture.global_bin)
    assert (fixture.root / "current-version").read_bytes() == b"2.0\n"
    assert (fixture.root / "last-known-good").read_bytes() == b"2.0\n"
    assert (fixture.global_bin / "fleet-manager.cmd").is_file()
    assert (fixture.slot / "install-success.json").is_file()


def test_install_transaction_cancelled_start_retains_selection(
    install_transaction, monkeypatch, capsys,
):
    fixture = install_transaction
    cancellation = KeyboardInterrupt()

    def start(_record, arguments, _root, _slot):
        if arguments:
            raise cancellation

    monkeypatch.setattr(runtime_application_install, "_run", start)
    with pytest.raises(KeyboardInterrupt) as failure:
        runtime_application_install.install(fixture.slot, stage=False, global_bin=fixture.global_bin)
    assert failure.value is cancellation
    assert "startup outcome is unknown" in capsys.readouterr().err
    assert (fixture.root / "current-version").read_bytes() == b"2.0\n"
    assert (fixture.root / "last-known-good").read_bytes() == b"0.9\n"
    assert (fixture.global_bin / "fixture-app.cmd").is_file()
    assert not (fixture.slot / "install-success.json").exists()


def test_install_transaction_last_good_follows_start_acknowledgement(
    install_transaction, monkeypatch,
):
    fixture = install_transaction
    starts = []

    def start(_record, arguments, root, slot):
        if arguments:
            assert (root / "current-version").read_text().strip() == slot.name
            assert (root / "last-known-good").read_bytes() == b"0.9\n"
            starts.append(arguments)

    monkeypatch.setattr(runtime_application_install, "_run", start)
    result = runtime_application_install.install(
        fixture.slot, stage=False, global_bin=fixture.global_bin,
    )
    assert result == {"state": "installed", "slot": str(fixture.slot)}
    assert starts == [["--start"]]
    assert (fixture.root / "last-known-good").read_bytes() == b"2.0\n"
    assert json.loads((fixture.slot / "install-success.json").read_text())["state"] == "installed"


@pytest.mark.parametrize("stage,failure", [(True, False), (False, False), (False, True)])
def test_manager_regular_install_routes_to_its_owner_before_generic_publication(
    tmp_path, monkeypatch, stage, failure,
):
    slot = tmp_path / "root" / "versions" / "1.2.3"
    (slot / ".venv").mkdir(parents=True)
    (slot / ".venv" / "pyvenv.cfg").write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(runtime_application_install.sys, "prefix", str(slot / ".venv"))
    monkeypatch.setattr(
        runtime_application_install, "_application", lambda _slot: {"name": "worktree-manager"},
    )
    calls = []

    def owner(record, command, root, selected):
        calls.append((command, root, selected))
        if failure:
            raise RuntimeError("finite installation owner refused")

    monkeypatch.setattr(runtime_application_install, "_run", owner)
    if failure:
        with pytest.raises(RuntimeError, match="owner refused"):
            runtime_application_install.install(slot, stage=stage)
    else:
        result = runtime_application_install.install(slot, stage=stage)
        assert result["state"] == ("staged" if stage else "installed")
    assert calls == ([] if stage else [(["self-install", "--apply"], slot.parent.parent, slot)])
    assert not (slot.parent.parent / "current-version").exists()


@pytest.mark.skipif(os.name == "nt", reason="native POSIX interpreter links")
@pytest.mark.parametrize("refusal", [None, "foreign-alias", "completed", "indirect-directory", "busy"])
def test_staging_materializes_only_the_running_interpreter_links(tmp_path, monkeypatch, refusal):
    interpreter = sys.executable
    slot = tmp_path / "slot"
    directory = slot / ".venv" / "bin"
    directory.mkdir(parents=True)
    source = tmp_path / "python-base"
    source.write_bytes(b"selected interpreter bytes")
    source.chmod(0o700)
    aliases = [directory / name for name in (
        "python", f"python{sys.version_info.major}",
        f"python{sys.version_info.major}.{sys.version_info.minor}",
    )]
    for index, alias in enumerate(aliases):
        alias.symlink_to(aliases[0] if index == 2 else source)
    package = directory / "hardlinked-package"
    os.link(source, package)
    before = package.stat()
    monkeypatch.setattr(runtime_application_install.sys, "executable", str(aliases[0]))
    if refusal is None:
        aliases[1].unlink()
        os.link(source, aliases[1])
    if refusal == "foreign-alias":
        foreign = tmp_path / "foreign"
        foreign.write_bytes(b"not the selected interpreter")
        aliases[-1].unlink()
        aliases[-1].symlink_to(foreign)
    elif refusal == "completed":
        (slot / ".install-complete.json").write_text("completed")
    elif refusal == "indirect-directory":
        moved = directory.with_name("elsewhere")
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    if refusal == "busy":
        with subprocess.Popen([
            interpreter, "-c",
            "import fcntl,os,sys;"
            "fd=os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY);"
            "fcntl.flock(fd,fcntl.LOCK_EX);"
            "print('held',flush=True);sys.stdin.readline();os.close(fd)",
            str(directory),
        ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0) as owner:
            assert owner.stdout.readline().strip() == "held"
            with pytest.raises(RuntimeError, match="already in progress"):
                runtime_application_install._materialize_interpreter(slot)
            assert all(alias.is_symlink() for alias in aliases)
            owner.communicate("\n", timeout=10)
            assert owner.returncode == 0
        runtime_application_install._materialize_interpreter(slot)
        assert all(not alias.is_symlink() for alias in aliases)
    elif refusal:
        with pytest.raises(RuntimeError, match="interpreter"):
            runtime_application_install._materialize_interpreter(slot)
        assert all(alias.is_symlink() for alias in aliases)
    else:
        runtime_application_install._materialize_interpreter(slot)
        assert aliases[1].samefile(source)
        assert all(not alias.is_symlink() and alias.read_bytes() == source.read_bytes()
                   and os.access(alias, os.X_OK) for alias in aliases)
        runtime_application_install._materialize_interpreter(slot)
    assert package.samefile(source)
    assert (package.stat().st_dev, package.stat().st_ino) == (before.st_dev, before.st_ino)


@pytest.mark.parametrize("wheel_present", [False, True])
def test_application_source_fallback_only_on_real_wheel_absence(tmp_path, wheel_present):
    source = tmp_path / "source"
    package = source / "src" / "fallback_app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "pyproject.toml").write_text(
        '[project]\nname="fallback-app"\nversion="1.0.0"\n'
        '[project.scripts]\nfallback-app="fallback_app:main"\n',
        encoding="utf-8",
    )
    wheel = tmp_path / "fallback_app-1.0.0-py3-none-any.whl"
    if wheel_present:
        wheel.write_bytes(b"not a wheel")
    destination = emit(
        tmp_path / "application", distribution="fallback-app", version="1.0.0",
        requires_python=">=3.10", commands={"fallback-app": "fallback_app:main"},
        wheels={"fallback-app": wheel}, source_fallbacks={"fallback-app": source},
    )
    manifest = (destination / "pyproject.toml").read_text()
    if wheel_present:
        assert (destination / "wheels" / wheel.name).read_bytes() == b"not a wheel"
        assert not (destination / "source").exists()
    else:
        assert 'path = "source/fallback-app", editable = false' in manifest
        assert (destination / "source" / "fallback-app" / "src" / "fallback_app" /
                "__init__.py").read_text() == "VALUE = 1\n"
        assert not (destination / "wheels" / wheel.name).exists()


@pytest.fixture
def application(tmp_path, monkeypatch, request):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("a real uv executable is required")
    long_repair = getattr(request, "param", None) == "long-repair"
    standalone = getattr(request, "param", None) == "standalone-service"
    distribution = "agent-ssh-service" if standalone else "fixture-app"
    component = "ssh-service" if standalone else "fixture-app"
    wheel_prefix = distribution.replace("-", "_") + "-1.0.0"
    root = tmp_path / "runtime root"
    artifact = tmp_path / "artifacts"
    artifact.mkdir()
    wheel = artifact / (wheel_prefix + "-py3-none-any.whl")
    files = {
        "fixture_app.py": (
            "import json, os, sys\nfrom pathlib import Path\n"
            "def cli():\n"
            "    if '--setup' in sys.argv:\n"
            "        Path(os.environ['FIXTURE_APP_HOME'], 'setup.json').write_text('setup')\n"
            "    if '--start' in sys.argv:\n"
            "        root = Path(os.environ['FIXTURE_APP_HOME'])\n"
            "        assert (root / 'current-version').is_file()\n"
            "        (root / 'started.json').write_text('started')\n"
            "    if '--unicode-output' in sys.argv:\n"
            "        print('\\u2192\\u03b1')\n"
            "    print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(), 'prefix': sys.prefix}))\n"
            "    return 17 if '--fail' in sys.argv else 0\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(cli())\n"
        ).encode(),
        wheel_prefix + ".dist-info/METADATA": (
            f"Metadata-Version: 2.1\nName: {distribution}\nVersion: 1.0.0\n"
            "Requires-Python: >=3.10\n"
        ).encode(),
        wheel_prefix + ".dist-info/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ).encode(),
    }
    if standalone:
        files["fixture_app.py"] = files["fixture_app.py"].replace(
            b"FIXTURE_APP_HOME", b"AGENT_SSH_SERVICE_HOME",
        )
    if getattr(request, "param", None) == "long-asset":
        member = "/".join(["fixture_assets", *["nested-resources" * 2] * 5, "operational.txt"])
        files[member] = b"retained operational asset\n"
    if getattr(request, "param", None) == "selector-lock":
        files["fixture_app.py"] = files["fixture_app.py"].replace(
            b"def cli():\n",
            (
                "def cli():\n"
                "    if '--setup' in sys.argv or '--start' in sys.argv:\n"
                "        import importlib.util\n"
                "        root = Path(os.environ['FIXTURE_APP_HOME'])\n"
                "        path = Path(sys.prefix).parent / 'runtime' / 'versioned_runtime_leases.py'\n"
                "        spec = importlib.util.spec_from_file_location('fixture_selector', path)\n"
                "        leases = importlib.util.module_from_spec(spec)\n"
                "        sys.modules[spec.name] = leases\n"
                "        spec.loader.exec_module(leases)\n"
                "        with leases._publication_lease(root, timeout=0):\n"
                "            with (root / 'callback-selection-checks').open('a') as log:\n"
                "                log.write(('setup' if '--setup' in sys.argv else 'start') + '\\n')\n"
                "        if '--start' in sys.argv:\n"
                "            path = path.with_name('versioned_runtime.py')\n"
                "            spec = importlib.util.spec_from_file_location('fixture_runtime', path)\n"
                "            runtime = importlib.util.module_from_spec(spec)\n"
                "            sys.modules[spec.name] = runtime\n"
                "            spec.loader.exec_module(runtime)\n"
                "            runtime.activate(root, Path(sys.prefix).parent.name, link_free=True)\n"
            ).encode(),
        )
    if long_repair:
        files["fixture_app.py"] += (
            b"\nif sys.argv[0] == '-c':\n"
            b"    assert Path.cwd().samefile(Path(sys.prefix).parent), 'wrong physical health cwd'\n"
        )
    rows = [
        (name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode(),
         len(raw))
        for name, raw in files.items()
    ]
    record = wheel_prefix + ".dist-info/RECORD"
    output = io.StringIO()
    csv.writer(output, lineterminator="\n").writerows([*rows, (record, "", "")])
    files[record] = output.getvalue().encode()
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    source = tmp_path / "actual source"
    source.mkdir()
    (source / "fixture_app.py").write_bytes(files["fixture_app.py"])
    (source / "pyproject.toml").write_text(
        f'[project]\nname="{distribution}"\nversion="1.0.0"\n'
        f'[project.scripts]\n{distribution}="fixture_app:cli"\n', encoding="utf-8",
    )
    source_hash = engine.runtime_source.assembly_hash(source)
    identity = engine.runtime_source.SourceIdentity(
        component, "1.0.0", "a" * 40, "b" * 40, source_hash,
        engine.runtime_source.runtime_version("1.0.0", "a" * 40, source_hash),
    )
    (source / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest()
    profile = {
        "component": component,
        "release": {"identity": "source:" + identity.source_commit,
                    "sha256": "sha256:" + identity.assembly_hash},
        "wheels": [{
            "name": distribution, "version": "1.0.0", "filename": wheel.name,
            "sha256": digest, "requires_python": ">=3.10", "requires_dist": [],
            "provides_extras": [], "selected_extras": [],
        }],
    }
    selected = install_source.ProjectSource(source, identity, {
        "projects": {component: {"artifact": {"wheels": [
            {"name": distribution, "filename": wheel.name, "sha256": digest},
        ]}}},
    }, component)
    with retain_wheel_inputs(
        {wheel.name: wheel}, {wheel.name: digest}, max_wheels=1,
        max_archive_bytes=1 << 20, max_total_bytes=1 << 20, max_snapshot_bytes=1 << 20,
        deadline=time.monotonic() + 60,
    ) as retained:
        inputs = engine.WheelInstallInputs(
            retained, json.dumps(profile, sort_keys=True, separators=(",", ":")),
            ((distribution, "1.0.0", wheel.name, digest),),
        )
        construction = install_source.application_inputs(selected, inputs)
    generation = construction["runtime_version"]
    if long_repair:
        generation += ".repair." + "c" * 32
        shortfall = 280 - len(str(root / "versions" / generation))
        if shortfall >= 0:
            root /= "physical-" + "r" * shortfall
    slot = root / "versions" / generation
    emit(
        slot, distribution=distribution, version="1.0.0",
        requires_python=f">={sys.version_info.major}.{sys.version_info.minor}",
        commands={distribution: "fixture_app:cli", "fixture-alias": "fixture_app:cli"},
        wheels={distribution: wheel},
        setup=["--setup"], start=["--start"],
        construction=construction, runtime_source=identity.document(),
    )
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("UV_") and key not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}
    }
    environment["UV_CACHE_DIR"] = str(root / "cache" / "uv")
    home = tmp_path / "home"
    home.mkdir()
    environment.update(HOME=str(home), USERPROFILE=str(home))
    if os.name == "nt":
        environment.update(HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive):])
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(
        [uv, "lock", *(["--project", engine._native_retained_path(slot)] if long_repair else []),
         "--offline", "--python", sys.executable],
        cwd=tmp_path if long_repair else slot,
        env=environment, capture_output=True, text=True, timeout=30, creationflags=flags,
    )
    assert result.returncode == 0, result.stderr
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "fixture_app.py").write_text("raise RuntimeError('caller hijack')", encoding="utf-8")
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    return root, slot, caller, environment


def invoke(path, arguments, caller, environment, *, host=None):
    if path.suffix == ".ps1":
        host = host or shutil.which("powershell.exe") or shutil.which("pwsh")
        if host is None:
            pytest.skip("PowerShell is unavailable")
        command = [host, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path), *arguments]
    elif path.suffix == ".cmd":
        if os.name != "nt":
            pytest.skip("native CMD is unavailable")
        invocation = subprocess.list2cmdline([str(path), *arguments])
        command = f'"{os.environ["COMSPEC"]}" /d /s /c "{invocation}"'
    else:
        if os.name == "nt":
            pytest.skip("native POSIX execution is unavailable")
        command = [str(path), *arguments]
    return subprocess.run(
        command, cwd=caller, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("application", ["standalone-service"], indirect=True)
@pytest.mark.parametrize("failure", [
    pytest.param(None, id="accepted"), "component", "source", "profile", "wheel",
    "completion", "construction", "selection", "entry-point", "python",
])
def test_read_only_installed_application_source_under_publication_lease(application, failure):
    root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    extension = ".ps1" if os.name == "nt" else ".sh"
    installed = invoke(slot / ("install" + extension), [], caller, environment)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    (slot / "install-success.json").unlink()
    (root / "last-known-good").write_bytes(b"0.9.0\n")
    if failure == "source":
        path = slot / "runtime-source.json"
        data = json.loads(path.read_bytes())
        data["source_tree"] = "c" * 40
        path.write_text(json.dumps(data), encoding="utf-8")
    elif failure == "profile":
        path = slot / ".runtime-input-origins.json"
        data = json.loads(path.read_bytes())
        data["packages"]["agent-ssh-service"] = "file:///unselected.whl"
        path.write_text(json.dumps(data), encoding="utf-8")
    elif failure == "wheel":
        path = next((slot / "wheels").glob("*.whl"))
        with path.open("ab") as stream:
            stream.write(b"altered")
    elif failure == "completion":
        (slot / engine.runtime.COMPLETE_MARKER).unlink()
    elif failure == "construction":
        path = slot / "application.json"
        data = json.loads(path.read_bytes())
        data["construction"]["source_hash"] = "0" * 64
        path.write_text(json.dumps(data), encoding="utf-8")
    elif failure == "selection":
        (root / "current-version").write_bytes(b"1.0.0\n")
    python = sys.executable if failure == "python" else str(engine.runtime.slot_python(root, slot.name))
    script = r"""
import builtins, importlib.util, json, pathlib, subprocess, sys
slot, failure = pathlib.Path(sys.argv[1]), json.loads(sys.argv[2])
root = slot.parent.parent
def snapshot():
    files = {}
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        relative, state = path.relative_to(root), path.stat()
        try:
            data = path.read_bytes()
        except PermissionError:
            assert relative.parts[0] == '.publication-leases'
            data = ('held-native-lease', state.st_size)
        files[str(relative)] = state.st_mtime_ns, data
    return files
before = snapshot()
spec = importlib.util.spec_from_file_location('qualification_api', slot / 'install.py')
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    assert name != 'fixture_app', 'reader imported the application'
    return original_import(name, *args, **kwargs)
def forbidden(*args, **kwargs):
    raise AssertionError('reader attempted lifecycle effects')
builtins.__import__ = guarded_import
api._write = forbidden
subprocess.run = forbidden
resources = api._resources
def guarded_resources(slot):
    runtime, engine = resources(slot)
    for action in ('construction_lease', 'retention_lease', '_publication_lease', 'mark_complete'):
        setattr(runtime, action, forbidden)
    return runtime, engine
api._resources = guarded_resources
try:
    result = api.qualify_installed_application(
        slot, component='wrong' if failure == 'component' else 'ssh-service',
        command='agent-ssh-service',
        entry_point='wrong:main' if failure == 'entry-point' else 'fixture_app:cli',
        deadline=__import__('time').monotonic() + 10,
    )
except (ValueError, RuntimeError) as error:
    assert failure is not None, str(error)
    assert snapshot() == before, 'reader changed the installation on refusal'
    print(json.dumps({'rejected': failure, 'reason': str(error)}))
else:
    assert failure is None, 'reader accepted invalid installed inputs'
    assert result.slot.samefile(slot)
    assert result.root.samefile(root)
    assert result.python.samefile(sys.executable)
    source = result.identity.document()
    assert source['plugin'] == 'ssh-service'
    assert source == json.loads((slot / 'runtime-source.json').read_bytes())
    assert snapshot() == before, 'reader changed the installation'
    print(json.dumps({'qualified': source, 'profile': result.profile_lock_id}))
"""
    with engine.runtime._publication_lease(root):
        result = subprocess.run(
            [engine._native_retained_path(python), "-I", "-B", "-c", script,
             str(slot), json.dumps(failure)],
            executable=engine._native_retained_path(python),
            cwd=caller, env=environment, capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    response = json.loads(result.stdout)
    assert ("qualified" in response) is (failure is None)


@pytest.mark.parametrize("extension", [".cmd", ".ps1", ".sh"])
def test_isolated_frontdoor_prints_unicode_without_environment_encoding(application, extension):
    _root, slot, caller, environment = application
    environment.update(PYTHONUTF8="0", PYTHONIOENCODING="ascii", UV_PYTHON=sys.executable)
    result = invoke(slot / ("fixture-app" + extension), ["--unicode-output"], caller, environment)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "\u2192\u03b1"


@pytest.mark.parametrize("extension", [".cmd", ".ps1", ".sh"])
def test_install_stage_uses_uv_without_touching_root_selection(application, extension):
    root, slot, caller, environment = application
    environment.update(UV_PYTHON=sys.executable, UV_PROJECT_ENVIRONMENT=str(caller / "wrong-env"),
                       UV_NO_SYNC="1", PYTHONPATH=str(caller), VIRTUAL_ENV=str(caller / "wrong-env"))
    result = invoke(slot / ("install" + extension), ["--stage"],
                    caller, environment)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record == {"state": "staged", "slot": str(slot.resolve())}
    assert (slot / ".venv" / "pyvenv.cfg").is_file()
    interpreter = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert interpreter.is_file() and not interpreter.is_symlink()
    assert not (root / "current-version").exists()
    assert not (root / ".venv").exists()
    assert not (caller / "wrong-env").exists()
    assert not (root / "setup.json").exists()
    assert not (root / "started.json").exists()
    assert not (slot / engine.runtime.COMPLETE_MARKER).exists()
    assert not (slot / ".runtime-input-origins.json").exists()
    assert not (slot / ".runtime-bootstrap").exists()
    assert not (Path(environment["HOME"]) / ".local" / "bin").exists()


@pytest.mark.parametrize("extension", [".cmd", ".ps1", ".sh"])
def test_regular_install_runs_setup_publishes_then_starts(application, extension):
    root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    result = invoke(slot / ("install" + extension), [], caller, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["state"] == "installed"
    assert (root / "setup.json").read_text() == "setup"
    assert (root / "started.json").read_text() == "started"
    assert (root / "current-version").read_text().strip() == slot.name
    assert (slot / "install-success.json").is_file()
    completion = json.loads((slot / engine.runtime.COMPLETE_MARKER).read_text())
    declaration = json.loads((slot / "application.json").read_text())
    assert completion["payload_hash"] == declaration["construction"]["payload_hash"]
    assert not (root / ".venv").exists()
    globals_root = Path(environment["HOME"]) / ".local" / "bin"
    followup = invoke(globals_root / ("fixture-app" + extension), ["--fail", "", "\u03b1", "100% ready!"],
                      caller, environment)
    assert followup.returncode == 17, followup.stderr
    assert json.loads(followup.stdout)["args"] == ["--fail", "", "\u03b1", "100% ready!"]
    alias = invoke(globals_root / ("fixture-alias" + extension), ["--fail"], caller, environment)
    assert alias.returncode == 17, alias.stderr


@pytest.mark.parametrize("application", ["selector-lock"], indirect=True)
def test_regular_install_callbacks_can_update_selection_without_parent_lock(application):
    root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    result = invoke(slot / ("install.ps1" if os.name == "nt" else "install.sh"),
                    [], caller, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "callback-selection-checks").read_text().splitlines() == ["setup", "start"]
    assert (root / "current-version").read_text().strip() == slot.name
    assert (root / "last-known-good").read_text().strip() == slot.name
    assert (slot / "install-success.json").is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows legacy path-length behavior")
@pytest.mark.parametrize("application", ["long-asset"], indirect=True)
def test_installed_wheel_health_keeps_long_path_content_checks(application):
    _root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    result = invoke(slot / "install.cmd", [], caller, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((slot / ".runtime-input-origins.json").read_text())
    wheels = engine.WheelReuseInputs(
        slot.name, json.dumps(record["wheel_inputs"], sort_keys=True, separators=(",", ":")),
        time.monotonic() + 60,
    )
    legacy_calls = """
import pathlib
def legacy(operation):
    def call(path, *args, **kwargs):
        if 'fixture_assets' in path.parts and len(str(path)) >= 260 and not str(path).startswith(chr(92) * 2 + '?' + chr(92)):
            raise FileNotFoundError('legacy Windows path exceeds MAX_PATH')
        return operation(path, *args, **kwargs)
    return call
pathlib.Path.stat = legacy(pathlib.Path.stat)
pathlib.Path.open = legacy(pathlib.Path.open)
"""

    def run(arguments):
        arguments = list(arguments)
        index = arguments.index("-c") + 1
        arguments[index] = legacy_calls + arguments[index]
        result = subprocess.run(
            arguments, executable=arguments[0], cwd=caller, env=environment,
            capture_output=True, text=True,
            timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        result.check_returncode()

    def verify():
        engine.verify_installed_inputs(
            slot, slot / ".venv" / "Scripts" / "python.exe", "fixture_app",
            origins=record["packages"], extras=tuple(record.get("extras", [])),
            local_packages=tuple(record["local_packages"]), excluded_packages=[],
            wheel_inputs=wheels, run=run,
        )

    verify()
    asset = next(Path(engine._native_retained_path(slot / ".venv")).rglob("operational.txt"))
    assert len(str(asset)) >= 260
    contents = asset.read_bytes()
    asset.write_bytes(b"X" + contents[1:])
    with pytest.raises(subprocess.CalledProcessError, match="non-zero exit status") as failure:
        verify()
    assert "installed content differs from its wheel" in failure.value.stderr


@pytest.mark.skipif(os.name != "nt", reason="native Windows long repair-slot cwd")
@pytest.mark.parametrize("application", ["long-repair"], indirect=True)
@pytest.mark.parametrize("host_name", ["powershell.exe", "pwsh"])
def test_generated_install_verifies_actual_long_repair_slot(application, host_name):
    root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"native {host_name} is unavailable")
    assert ".repair." in slot.name and len(str(slot)) >= 260
    assert (slot / "install.py").read_bytes() == Path(
        runtime_application_install.__file__,
    ).read_bytes()
    source = (slot / "runtime-source.json").read_bytes()
    declaration = (slot / "application.json").read_bytes()
    print(json.dumps({"repair_slot_length": len(str(slot))}))
    staged = invoke(slot / "install.ps1", ["--stage"], caller, environment, host=host)
    assert staged.returncode == 0, staged.stdout + staged.stderr
    python = engine.runtime.slot_python(root, slot.name)
    assert python is not None
    print(json.dumps({"repair_python_length": len(str(python))}))
    installed = invoke(slot / "install.ps1", [], caller, environment, host=host)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    completion = json.loads((slot / engine.runtime.COMPLETE_MARKER).read_bytes())
    construction = json.loads(declaration)["construction"]
    assert completion["payload_hash"] == construction["payload_hash"]
    assert (root / "current-version").read_text().strip() == slot.name
    assert (slot / "runtime-source.json").read_bytes() == source
    assert (slot / "application.json").read_bytes() == declaration
    command = invoke(slot / "fixture-app.ps1", ["--fail"], caller, environment, host=host)
    assert command.returncode == 17, command.stdout + command.stderr
    assert Path(json.loads(command.stdout)["prefix"]).samefile(slot / ".venv")


@pytest.mark.parametrize("foreign_interpreter", [False, True])
def test_retention_bootstrap_uses_the_completed_application_environment(
    application, foreign_interpreter,
):
    import runtime_launcher

    root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    extension = ".cmd" if os.name == "nt" else ".sh"
    installed = invoke(slot / ("install" + extension), [], caller, environment)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    completion = (slot / engine.runtime.COMPLETE_MARKER).read_bytes()
    identity = runtime_launcher.runtime_identity(slot)
    metadata = (
        slot / engine.runtime.COMPLETE_MARKER,
        slot / ".runtime-bootstrap" / "runtime-generation",
    )
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in metadata}
    reused = invoke(slot / ("install" + extension), [], caller, environment)
    assert reused.returncode == 0, reused.stdout + reused.stderr
    assert runtime_launcher.runtime_identity(slot) == identity
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in metadata} == before
    python = sys.executable if foreign_interpreter else str(engine.runtime.slot_python(root, slot.name))
    interpreters = [python]
    if os.name == "nt" and not foreign_interpreter:
        interpreters.append(str(engine._native_retained_path(Path(python))))
    for python in interpreters:
        result = subprocess.run(
            [python, "-I", "-S", "-B", "-X", "utf8",
             str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
             "--runtime-identity", identity, "-m", "fixture_app", "--fail"],
            executable=python, cwd=caller, env=environment,
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if foreign_interpreter:
            assert result.returncode != 0
            assert "interpreter resolved outside its slot" in result.stderr
            assert not result.stdout.strip()
        else:
            assert result.returncode == 17, result.stdout + result.stderr
            assert Path(json.loads(result.stdout)["prefix"]).samefile(slot / ".venv")
    assert (slot / engine.runtime.COMPLETE_MARKER).read_bytes() == completion


@pytest.mark.parametrize("metadata", ["runtime-generation", ".install-complete.json"])
@pytest.mark.parametrize("kind", ["missing", "oversized", "malformed", "linked", "fifo"])
def test_retained_identity_refuses_unusable_metadata_before_launch(tmp_path, metadata, kind):
    import runtime_launcher

    if kind in {"linked", "fifo"} and os.name == "nt":
        pytest.skip("POSIX metadata fixture")
    slot = tmp_path / "versions" / "1.2.3"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    (bootstrap / "runtime-generation").write_text("1" * 32, encoding="ascii")
    completion = slot / ".install-complete.json"
    completion.write_text(json.dumps({
        "version": slot.name, "completed_at": "2026-01-01", "pid": 0, "payload_hash": "a" * 64,
    }), encoding="utf-8")
    path = bootstrap / metadata if metadata == "runtime-generation" else completion
    if kind == "malformed" and metadata == ".install-complete.json":
        pytest.skip("Completion schema admission belongs to the existing retention lease")
    if kind == "oversized":
        path.write_bytes(b"1" * 65537)
    elif kind == "malformed":
        path.write_text("not-a-generation", encoding="ascii")
    else:
        previous = path.with_name("original-metadata")
        path.rename(previous)
        if kind == "linked":
            path.symlink_to(previous)
        elif kind == "fifo":
            os.mkfifo(path)
    with pytest.raises((OSError, ValueError)):
        runtime_launcher.runtime_identity(slot)


def test_retention_bootstrap_preserves_the_historical_direct_slot(tmp_path):
    root = tmp_path / "runtime"
    slot = root / "versions" / "1.2.3"
    venv.EnvBuilder(with_pip=False).create(slot)
    python = engine.runtime.slot_python(root, slot.name)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    locations = subprocess.run(
        [str(python), "-I", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
        executable=str(python), capture_output=True, text=True, timeout=30, creationflags=flags,
    )
    assert locations.returncode == 0, locations.stderr
    (Path(locations.stdout.strip()) / "historical_fixture.py").write_text(
        "import sys\nprint(sys.prefix)\nraise SystemExit(7)\n", encoding="utf-8",
    )
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    for path in Path(engine.runtime.__file__).parent.glob("versioned_runtime*.py"):
        shutil.copyfile(path, bootstrap / path.name)
    shutil.copyfile(
        Path(runtime_application_install.__file__).with_name("runtime_launcher.py"),
        bootstrap / "runtime_launcher.py",
    )
    engine.runtime.mark_complete(root, slot.name, payload_hash="historical-fixture")
    result = subprocess.run(
        [str(python), "-I", "-S", "-B", "-X", "utf8",
         str(bootstrap / "runtime_launcher.py"), "-m", "historical_fixture"],
        executable=str(python), cwd=tmp_path, capture_output=True,
        text=True, timeout=30, creationflags=flags,
    )
    assert result.returncode == 7, result.stdout + result.stderr
    assert Path(result.stdout.strip()) == slot


@pytest.mark.parametrize("stage", [False, True])
def test_install_forwards_explicit_application_arguments_only_when_regular(application, stage):
    root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    forwarded = ["--config", str(caller / "config with spaces.json"), "--role", "host"]
    extension = ".cmd" if os.name == "nt" else ".sh"
    result = invoke(
        slot / ("install" + extension),
        [*(["--stage"] if stage else []), "--", *forwarded], caller, environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    if stage:
        assert records == [{"state": "staged", "slot": str(slot)}]
        assert not (root / "setup.json").exists()
        assert not (root / "started.json").exists()
        assert not (root / "current-version").exists()
    else:
        assert records[0]["args"] == ["--setup", *forwarded]
        assert records[1]["args"] == ["--start", *forwarded]
        assert records[2]["state"] == "installed"
        record = json.loads((slot / "application.json").read_text())
        module_record = {**record, "module": "json.tool"}
        runtime_application_install._run(module_record, ["--help"], root, slot)
        with pytest.raises(RuntimeError, match="exit code 2"):
            runtime_application_install._run(
                module_record, ["--unrecognized-fixture-option"], root, slot,
            )


@pytest.mark.parametrize("extension", [".cmd", ".ps1", ".sh"])
def test_failed_start_retains_selection_without_claiming_installation(application, extension):
    root, slot, caller, environment = application
    record = json.loads((slot / "application.json").read_text())
    record["start"] = ["--fail"]
    (slot / "application.json").write_text(json.dumps(record), encoding="utf-8")
    environment["UV_PYTHON"] = sys.executable
    result = invoke(slot / ("install" + extension), [], caller, environment)
    assert result.returncode != 0
    assert "startup outcome is unknown" in result.stderr
    assert (root / "current-version").read_text().strip() == slot.name
    assert not (root / "last-known-good").exists()
    assert not (slot / "install-success.json").exists()
    assert (Path(environment["HOME"]) / ".local" / "bin" / "fixture-app.cmd").is_file()
    assert (Path(environment["HOME"]) / ".local" / "bin" / "fixture-alias.cmd").is_file()


def test_partial_selector_failure_restores_previous_publication(application, monkeypatch):
    root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    extension = ".cmd" if os.name == "nt" else ".sh"
    staged = invoke(slot / ("install" + extension), ["--stage"], caller, environment)
    assert staged.returncode == 0, staged.stderr
    record = json.loads((slot / "application.json").read_text())
    record.update(setup=[], start=[])
    (slot / "application.json").write_text(json.dumps(record), encoding="utf-8")
    original = b"0.9.0.source.original\n"
    (root / "current-version").write_bytes(original)
    (root / "last-known-good").write_bytes(original)
    spec = importlib.util.spec_from_file_location("_fixture_install", slot / "install.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    replace = installer.os.replace

    def fail_last_good(source, destination):
        if Path(destination) == root / "last-known-good":
            raise OSError("injected publication failure")
        return replace(source, destination)

    monkeypatch.setattr(sys, "prefix", str(slot / ".venv"))
    monkeypatch.setattr(sys, "executable", str(
        slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
    ))
    monkeypatch.setattr(installer.os, "replace", fail_last_good)
    global_bin = root / "global"
    with pytest.raises(OSError, match="injected publication failure"):
        installer.install(slot, stage=False, global_bin=global_bin)
    assert (root / "current-version").read_bytes() == original
    assert (root / "last-known-good").read_bytes() == original
    assert not tuple(global_bin.iterdir())
    assert not (slot / "install-success.json").exists()


@pytest.mark.parametrize("failure_point", ["after-start", "after-success", "direct-exit"])
def test_real_qualification_finishes_before_install_success(
    application, monkeypatch, failure_point,
):
    root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    extension = ".cmd" if os.name == "nt" else ".sh"
    staged = invoke(slot / ("install" + extension), ["--stage"], caller, environment)
    assert staged.returncode == 0, staged.stderr
    spec = importlib.util.spec_from_file_location("_qualification_install", slot / "install.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    load = installer._load
    success = slot / "install-success.json"
    trigger = success if failure_point == "after-success" else root / "started.json"

    def observed_load(path, name):
        module = load(path, name)
        if name == "_application_engine":
            verify = module.verify_application_wheels

            def guarded_verify(*args, **kwargs):
                if trigger.exists():
                    raise RuntimeError("injected qualification failure " + failure_point)
                return verify(*args, **kwargs)

            monkeypatch.setattr(module, "verify_application_wheels", guarded_verify)
        return module

    monkeypatch.setattr(installer, "_load", observed_load)
    monkeypatch.setattr(sys, "prefix", str(slot / ".venv"))
    monkeypatch.setattr(sys, "executable", str(
        slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
    ))
    if failure_point == "direct-exit":
        record = json.loads((slot / "application.json").read_bytes())
        with pytest.raises(RuntimeError, match="injected qualification failure direct-exit"):
            with installer._qualified(slot, record):
                trigger.write_text("activation fixture", encoding="utf-8")
        assert not success.exists()
        assert not (root / "current-version").exists()
        return
    if failure_point == "after-start":
        with pytest.raises(installer.ApplicationStartupUncertain) as failed:
            installer.install(slot, stage=False, global_bin=root / "global")
        assert str(failed.value.__cause__) == "injected qualification failure after-start"
        assert not success.exists()
        assert not (root / "last-known-good").exists()
    else:
        assert installer.install(
            slot, stage=False, global_bin=root / "global",
        )["state"] == "installed"
        assert success.is_file()
        assert (root / "last-known-good").read_text().strip() == slot.name
    assert (root / "started.json").is_file()
    assert (root / "current-version").read_text().strip() == slot.name


@pytest.mark.parametrize("failure", ["missing-inputs", "source-mismatch", "installed-content"])
def test_unqualified_application_does_not_publish_or_run_setup(application, failure):
    root, slot, caller, environment = application
    extension = ".cmd" if os.name == "nt" else ".sh"
    environment["UV_PYTHON"] = sys.executable
    staged = invoke(slot / ("install" + extension), ["--stage"], caller, environment)
    assert staged.returncode == 0, staged.stderr
    if failure == "installed-content":
        site = slot / ".venv" / (
            "Lib/site-packages" if os.name == "nt"
            else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
        (site / "fixture_app.py").write_text("raise RuntimeError('damaged installed content')\n")
    else:
        record = json.loads((slot / "application.json").read_text())
        if failure == "missing-inputs":
            record["construction"] = None
        else:
            record["construction"]["source_hash"] = "0" * 64
        (slot / "application.json").write_text(json.dumps(record), encoding="utf-8")
    result = invoke(slot / ("install" + extension), [], caller, environment)
    assert result.returncode != 0, result.stdout + result.stderr
    for path in (
        root / "current-version", root / "last-known-good", root / "setup.json",
        root / "started.json", slot / engine.runtime.COMPLETE_MARKER, slot / "install-success.json",
        Path(environment["HOME"]) / ".local" / "bin",
    ):
        assert not path.exists(), path


def test_regular_install_reuses_canonical_completion_without_restamping(application):
    root, slot, caller, environment = application
    extension = ".cmd" if os.name == "nt" else ".sh"
    environment["UV_PYTHON"] = sys.executable
    first = invoke(slot / ("install" + extension), [], caller, environment)
    assert first.returncode == 0, first.stdout + first.stderr
    marker = slot / engine.runtime.COMPLETE_MARKER
    before = marker.read_bytes(), marker.stat().st_mtime_ns
    second = invoke(slot / ("install" + extension), [], caller, environment)
    assert second.returncode == 0, second.stdout + second.stderr
    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before
    assert (root / "current-version").read_text().strip() == slot.name


def test_fresh_repair_generation_replaces_selection_and_retires_old_frontend(application):
    root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    incoming = slot.with_name(slot.name + ".repair." + "d" * 32)
    assert not (slot / ".venv").exists()
    shutil.copytree(slot, incoming)
    extension = ".ps1" if os.name == "nt" else ".sh"
    first = invoke(slot / ("install" + extension), [], caller, environment)
    assert first.returncode == 0, first.stdout + first.stderr
    original = (slot / engine.runtime.COMPLETE_MARKER).read_bytes()
    assert not (incoming / ".venv").exists()
    updated = invoke(incoming / ("install" + extension), [], caller, environment)
    assert updated.returncode == 0, updated.stdout + updated.stderr
    assert json.loads(updated.stdout.splitlines()[-1])["state"] == "installed"
    for selector in ("current-version", "last-known-good"):
        assert (root / selector).read_text().strip() == incoming.name
    assert (slot / engine.runtime.COMPLETE_MARKER).read_bytes() == original
    selected_python = incoming / ".venv" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    collected = subprocess.run(
        [str(selected_python), "-I", "-B", str(incoming / "runtime" / "versioned_runtime.py"),
         "--root", str(root), "--json", "gc", "--protect-pids", "--min-age-days", "0"],
        executable=engine._native_retained_path(selected_python),
        cwd=caller, env=environment, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert json.loads(collected.stdout) == {"removed": [slot.name]}
    assert not slot.exists() and selected_python.is_file()
    public = Path(environment["HOME"]) / ".local" / "bin" / ("fixture-app" + extension)
    result = invoke(public, ["--after-replacement"], caller, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    facts = json.loads(result.stdout)
    assert Path(facts["prefix"]).samefile(incoming / ".venv")
    assert facts["args"] == ["--after-replacement"]


@pytest.mark.parametrize("extension", [".cmd", ".ps1", ".sh"])
def test_global_uses_nested_home_relative_root(application, extension):
    root, slot, caller, environment = application
    nested = Path(environment["HOME"]) / ".fixture-app" / "engine"
    copied = nested / "versions" / slot.name
    shutil.copytree(slot, copied)
    (nested / "current-version").write_text(slot.name + "\n", encoding="utf-8")
    globals_root = root / "nested-global"
    globals_root.mkdir()
    for name, text in global_binstubs(
        "fixture-app", "FIXTURE_APP_HOME", ".fixture-app/engine",
    ).items():
        path = globals_root / name
        path.write_text(text, encoding="utf-8", newline="")
        if path.suffix == ".sh" or not path.suffix:
            path.chmod(0o700)
    environment.pop("FIXTURE_APP_HOME", None)
    environment["UV_PYTHON"] = sys.executable
    result = invoke(globals_root / ("fixture-app" + extension), ["--fail"], caller, environment)
    assert result.returncode == 17, result.stderr
    assert Path(json.loads(result.stdout)["prefix"]).samefile(copied / ".venv")


@pytest.mark.parametrize("extension", [".cmd", ".ps1", ".sh"])
def test_global_forwards_to_slot_uv_and_preserves_failure(application, extension):
    root, slot, caller, environment = application
    binaries = root / "global"
    binaries.mkdir()
    for name, text in global_binstubs("fixture-app", "FIXTURE_APP_HOME", ".fixture-app").items():
        path = binaries / name
        path.write_text(text, encoding="utf-8", newline="")
        if path.suffix == ".sh" or not path.suffix:
            path.chmod(0o700)
    (root / "current-version").write_text(slot.name + "\n", encoding="utf-8")
    environment.update(FIXTURE_APP_HOME=str(root), UV_PYTHON=sys.executable)
    result = invoke(binaries / ("fixture-app" + extension), ["--fail", "space value", "100% ready!"],
                    caller, environment)
    assert result.returncode == 17, result.stderr
    record = json.loads(result.stdout)
    assert record["args"] == ["--fail", "space value", "100% ready!"]
    assert Path(record["cwd"]) == caller
    assert Path(record["prefix"]).samefile(slot / ".venv")


@pytest.mark.parametrize("extension", [".cmd", ".ps1", ".sh"])
@pytest.mark.parametrize("version", ["..", "1/../../outside", "1 & echo unsafe"])
def test_global_refuses_invalid_marker_without_invocation(application, extension, version):
    root, _slot, caller, environment = application
    binary = root / ("fixture-app" + extension)
    binary.write_text(
        global_binstubs("fixture-app", "FIXTURE_APP_HOME", ".fixture-app")[binary.name],
        encoding="utf-8", newline="",
    )
    if extension == ".sh":
        binary.chmod(0o700)
    (root / "current-version").write_text(version + "\n", encoding="utf-8")
    environment["FIXTURE_APP_HOME"] = str(root)
    result = invoke(binary, [], caller, environment)
    assert result.returncode != 0
    assert not result.stdout.strip()


def test_existing_launcher_is_not_partially_overwritten(tmp_path):
    (tmp_path / "install.cmd").write_text("retained", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_slot_binstubs(tmp_path, "fixture-app", "fixture_app")
    assert (tmp_path / "install.cmd").read_text(encoding="utf-8") == "retained"
    assert not (tmp_path / "fixture-app.ps1").exists()
