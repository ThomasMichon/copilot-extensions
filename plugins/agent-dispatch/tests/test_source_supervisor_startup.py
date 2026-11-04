from __future__ import annotations

import argparse

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
