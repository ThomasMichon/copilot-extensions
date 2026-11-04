from __future__ import annotations

import argparse
from pathlib import Path

import pytest


def _sources(monkeypatch, *, declarations=(), complete=True):
    from agent_dispatch import pool_source_snapshot, remote_dispatch

    monkeypatch.setattr(pool_source_snapshot, "read_supervisor_declarations", lambda _: (
        pool_source_snapshot.DeclarationRead(tuple(declarations), complete, "" if complete else "unconfirmed")
    ))
    monkeypatch.setattr(remote_dispatch, "is_peer_machine", lambda _: False)


def _command(monkeypatch, tmp_path):
    from agent_dispatch import coordinator_cli, source_start

    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    monkeypatch.setattr(coordinator_cli, "_cmd_cutover", lambda *_args, **_kwargs: (
        pytest.fail("supervisor preflight must precede coordinator mutation")
    ))
    return lambda: source_start.command(argparse.Namespace(json=True))


def test_unknown_supervisor_source_preflight_preserves_coordinator(monkeypatch, tmp_path):
    _sources(monkeypatch, complete=False)
    command = _command(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="declaration source is unconfirmed"):
        command()


def test_foreign_supervisor_preflight_preserves_coordinator(monkeypatch, tmp_path):
    from agent_dispatch import remote_dispatch

    _sources(monkeypatch, declarations=[object()])
    (tmp_path / "supervisor.env").write_text(
        "AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_MACHINE=foreign-fixture\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(remote_dispatch, "is_peer_machine", lambda _: True)
    command = _command(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="foreign supervisor"):
        command()


def test_classic_child_profile_preflight_cannot_silently_disable_work(monkeypatch, tmp_path):
    _sources(monkeypatch)
    profiles = tmp_path / "supervisors"
    profiles.mkdir()
    (profiles / "fixture.env").write_text("AGENT_DISPATCH_SUPERVISE_LABELS=fixture\n", encoding="utf-8")
    command = _command(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="classic supervisor"):
        command()


@pytest.mark.parametrize("legacy", [False, True])
def test_master_preflight_preserves_actual_modern_and_legacy_source_selection(
    monkeypatch, tmp_path, legacy,
):
    from agent_dispatch import source_start

    _sources(monkeypatch, declarations=[object()])
    (tmp_path / "supervisor.env").write_text(
        "AGENT_DISPATCH_SUPERVISE_MODE=serve\n"
        "AGENT_DISPATCH_SUPERVISE_MACHINE=fixture-host\n"
        "AGENT_DISPATCH_SUPERVISE_INTERVAL=2.5\n"
        + ("AGENT_DISPATCH_SUPERVISE_LABELS=fixture\n" if legacy else ""),
        encoding="utf-8",
    )
    plan = source_start.supervisor_plan(tmp_path)
    assert plan.args.legacy_env is legacy
    assert plan.args.interval == 2.5
    assert plan.machine == "fixture-host"


@pytest.mark.parametrize("interval", ["0", "-1", "nan", "inf"])
def test_invalid_supervisor_interval_preflight_refuses_before_mutation(
    monkeypatch, tmp_path, interval,
):
    _sources(monkeypatch, declarations=[object()])
    (tmp_path / "supervisor.env").write_text(
        f"AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_INTERVAL={interval}\n",
        encoding="utf-8",
    )
    command = _command(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="interval"):
        command()


def test_startup_uses_one_captured_supervisor_plan(monkeypatch, tmp_path):
    from agent_dispatch import coordinator_cli, source_start

    calls = []
    plan = object()
    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    monkeypatch.setattr(source_start, "supervisor_plan", lambda root: calls.append("preflight") or plan)
    monkeypatch.setattr(coordinator_cli, "_cmd_cutover", lambda *_args, **_kwargs: calls.append("coordinator") or 0)
    monkeypatch.setattr(source_start, "ensure_supervisor", lambda root, **kwargs: (
        calls.append(("supervisor", kwargs["plan"]))
    ))
    assert source_start.command(argparse.Namespace(json=True)) == 0
    assert calls == ["preflight", "coordinator", ("supervisor", plan)]


@pytest.mark.parametrize("kind", ["primary-read", "primary-directory", "profiles-file", "profiles-list", "profile-read", "profile-malformed"])
def test_supervisor_intent_io_failure_refuses_before_coordinator_mutation(monkeypatch, tmp_path, kind):
    _sources(monkeypatch)
    primary = tmp_path / "supervisor.env"
    profiles = tmp_path / "supervisors"
    if kind == "primary-directory":
        primary.mkdir()
    else:
        primary.write_text("AGENT_DISPATCH_SUPERVISE_MODE=serve\n", encoding="utf-8")
    if kind == "profiles-file":
        profiles.write_text("not a profile directory", encoding="utf-8")
    else:
        profiles.mkdir()
    if kind in {"profile-read", "profile-malformed"}:
        primary.write_text(
            "AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_LABELS=known\n",
            encoding="utf-8",
        )
        (profiles / "fixture.env").write_text("unsupported-line\n", encoding="utf-8")
    original_read, original_iterdir = Path.read_text, Path.iterdir

    def read(path, *args, **kwargs):
        if (kind == "primary-read" and path == primary) or (
            kind == "profile-read" and path == profiles / "fixture.env"
        ):
            raise PermissionError("fixture configuration read denied")
        return original_read(path, *args, **kwargs)

    def iterdir(path):
        if kind == "profiles-list" and path == profiles:
            raise PermissionError("fixture profile enumeration denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    from agent_dispatch.registrar import RegistrarError

    with pytest.raises((OSError, RegistrarError)):
        _command(monkeypatch, tmp_path)()


def test_missing_optional_supervisor_configuration_is_absent(monkeypatch, tmp_path):
    from agent_dispatch import source_start

    _sources(monkeypatch)
    assert source_start.supervisor_plan(tmp_path) is None
