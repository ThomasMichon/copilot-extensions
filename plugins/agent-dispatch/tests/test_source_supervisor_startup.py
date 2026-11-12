from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from agent_dispatch.source_start import prepare_scheduled_launchers as prepare_owner_launchers


@pytest.fixture(autouse=True)
def isolated_windows_scheduling(monkeypatch):
    from agent_dispatch import installation_policy, source_start

    monkeypatch.setattr(installation_policy, "windows_policy", lambda *_, **__: {
        "windows_scheduled_task": "preserve", "start_coordinator": True,
        "start_supervisor": True, "primary_start_supervisor": True,
    })
    # Most tests isolate readiness planning; launcher publication has its own test.
    monkeypatch.setattr(source_start, "prepare_scheduled_launchers", lambda *_, **__: None)


def _sources(monkeypatch, *, declarations=(), complete=True):
    from agent_dispatch import pool_source_snapshot, remote_dispatch

    monkeypatch.setattr(pool_source_snapshot, "read_supervisor_declarations", lambda _: (
        pool_source_snapshot.DeclarationRead(tuple(declarations), complete, "" if complete else "unconfirmed")
    ))
    monkeypatch.setattr(remote_dispatch, "is_peer_machine", lambda _: False)


@pytest.mark.parametrize("values", [
    {"AGENT_DISPATCH_SUPERVISE_MODE": "serve",
     "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS": "--no-declared --env primary --interval .2"},
    {"AGENT_DISPATCH_SUPERVISE_LABELS": "fixture",
     "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS": "--no-reactive --headless-agent worker --max-active-processes 3"},
])
def test_profile_preflight_uses_the_same_supervisor_grammar(values):
    from agent_dispatch import __main__ as cli
    from agent_dispatch.supervise_registration_cli import build_profile_parser
    from agent_dispatch.supervisor_lane_status import profile_arguments

    argv = profile_arguments(values)
    expected = vars(cli.build_parser().parse_args(argv))
    parsed = vars(build_profile_parser().parse_args(argv))
    assert parsed == {key: expected[key] for key in parsed}


def test_source_start_early_entry_retains_task_worker_dispatch(monkeypatch):
    import os
    from agent_dispatch import early_cli, source_start

    for key in tuple(os.environ):
        if key.startswith("AGENT_DISPATCH_TASK_"):
            monkeypatch.delenv(key)
    calls = []
    monkeypatch.setattr(source_start, "command", lambda args: calls.append(args.json) or 71)
    assert early_cli.dispatch(["__source-start", "--json"]) == 71
    assert calls == [True]
    monkeypatch.setenv("AGENT_DISPATCH_TASK_ID", "fixture")
    assert early_cli.dispatch(["__source-start", "--json"]) is None
    assert calls == [True]


def test_source_candidate_early_entry_retains_native_handler_and_task_fallback(monkeypatch):
    import os
    from agent_dispatch import early_cli, supervise_cli
    from agent_dispatch.supervise_registration_cli import parse_supervisor_arguments

    for key in tuple(os.environ):
        if key.startswith("AGENT_DISPATCH_TASK_"):
            monkeypatch.delenv(key)
    calls = []
    monkeypatch.setattr(supervise_cli, "_cmd_supervise", lambda args: calls.append(args.source_candidate) or 71)
    arguments = ["supervise", "serve", "--source-candidate", "fixture", "--env", "primary"]
    assert early_cli.dispatch(arguments) == 71
    assert calls == ["fixture"]
    parsed = parse_supervisor_arguments(["--local", *arguments])
    assert parsed.local and parsed.env == "primary" and parsed.source_candidate == "fixture"
    monkeypatch.setenv("AGENT_DISPATCH_TASK_ID", "fixture")
    assert early_cli.dispatch(arguments) is None
    assert calls == ["fixture"]


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
    from agent_dispatch import source_start

    _sources(monkeypatch)
    profiles = tmp_path / "supervisors"
    profiles.mkdir()
    (profiles / "fixture.env").write_text("AGENT_DISPATCH_SUPERVISE_LABELS=fixture\n", encoding="utf-8")
    plan = source_start.supervisor_plan(tmp_path)
    assert isinstance(plan, source_start.ClassicStartupPlan)
    assert len(plan.profiles) == 1
    assert plan.profiles[0][0] == profiles / "fixture.env"
    assert plan.profiles[0][1][:2] == ["supervise", "--all-repos"]
    assert "--label" in plan.profiles[0][1]


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


def test_primary_source_profile_captures_supported_extra_scope_and_discovery_intent(tmp_path, monkeypatch):
    from agent_dispatch import pool_source_snapshot, source_start

    _sources(monkeypatch)
    monkeypatch.setattr(pool_source_snapshot, "read_supervisor_declarations", lambda _: (
        pytest.fail("explicit --no-declared must not read modern declarations")
    ))
    (tmp_path / "supervisor.env").write_text(
        "AGENT_DISPATCH_SUPERVISE_MODE=serve\n"
        "AGENT_DISPATCH_SUPERVISE_MACHINE=fixture-host\n"
        "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--no-declared --env primary-fixture\n",
    )
    plan = source_start.supervisor_plan(tmp_path)
    assert plan.args.no_declared is True and plan.args.legacy_env is False
    assert (plan.machine, plan.env) == ("fixture-host", "primary-fixture")
    assert plan.scope == "supervisor:fixture-host:primary-fixture"
    assert plan.argv[-3:] == ("--no-declared", "--env", "primary-fixture")


@pytest.mark.parametrize("extra", [
    "--unknown-fixture", "--once", "--no-single-instance",
    "--source-candidate fixture", "--interval not-a-number",
])
def test_primary_source_profile_rejects_unsupported_or_nonresident_extras_before_mutation(
    tmp_path, monkeypatch, extra,
):
    _sources(monkeypatch)
    (tmp_path / "supervisor.env").write_text(
        "AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=" + extra + "\n",
    )
    with pytest.raises(ValueError, match="profile arguments|resident admitted role"):
        _command(monkeypatch, tmp_path)()


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
    original_read, original_bytes, original_iterdir = Path.read_text, Path.read_bytes, Path.iterdir

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

    def read_bytes(path):
        if kind == "primary-read" and path == primary:
            raise PermissionError("fixture configuration read denied")
        return original_bytes(path)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    from agent_dispatch.registrar import RegistrarError

    with pytest.raises((OSError, RegistrarError)):
        _command(monkeypatch, tmp_path)()


def test_primary_plan_binds_the_bytes_actually_parsed_not_a_racing_second_read(tmp_path, monkeypatch):
    import hashlib
    from agent_dispatch import registrar_discovery, source_start

    _sources(monkeypatch)
    primary = tmp_path / "supervisor.env"
    content = b"AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_INTERVAL=5\n"
    primary.write_bytes(content)
    original = registrar_discovery._parse_env_file

    def racing_read(path, **options):
        if path != primary:
            return original(path, **options)
        path.write_bytes(content.replace(b"=5", b"=9"))
        try:
            return original(path, **options)
        finally:
            path.write_bytes(content)

    monkeypatch.setattr(registrar_discovery, "_parse_env_file", racing_read)
    plan = source_start.supervisor_plan(tmp_path)
    assert plan.args.interval == 5
    assert plan.profile == primary
    assert plan.profile_generation == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize("change", ["changed", "missing", "unreadable"])
def test_captured_primary_binding_refuses_drift_before_candidate_creation(tmp_path, monkeypatch, change):
    from agent_dispatch import source_start, supervisor_source

    _sources(monkeypatch)
    primary = tmp_path / "supervisor.env"
    primary.write_text("AGENT_DISPATCH_SUPERVISE_MODE=serve\n")
    plan = source_start.supervisor_plan(tmp_path)
    if change == "changed":
        primary.write_text(primary.read_text() + "# later generation\n")
    elif change == "missing":
        primary.unlink()
    else:
        original = Path.read_bytes

        def denied(path):
            if path == primary:
                raise PermissionError("fixture binding read refused")
            return original(path)

        monkeypatch.setattr(Path, "read_bytes", denied)
    monkeypatch.setattr(supervisor_source, "spawn_candidate", lambda *_args, **_kwargs: pytest.fail("stale binding startup"))
    with pytest.raises((OSError, ValueError), match="binding|refused|cannot find|No such file"):
        source_start.ensure_supervisor(tmp_path, plan=plan)


def test_missing_optional_supervisor_configuration_is_absent(monkeypatch, tmp_path):
    from agent_dispatch import source_start

    _sources(monkeypatch)
    assert source_start.supervisor_plan(tmp_path) is None


@pytest.mark.parametrize("coordinator,supervisor", [(False, False), (True, False), (False, True), (True, True)])
def test_scheduling_absence_does_not_disable_independent_source_readiness(
    monkeypatch, tmp_path, coordinator, supervisor,
):
    from agent_dispatch import coordinator_cli, installation_policy, source_start

    calls = []
    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    monkeypatch.setattr(installation_policy, "windows_policy", lambda *_, **__: {
        "windows_scheduled_task": "absent", "start_coordinator": coordinator,
        "start_supervisor": supervisor, "primary_start_supervisor": supervisor,
    })
    monkeypatch.setattr(source_start, "supervisor_plan", lambda _: "captured")
    monkeypatch.setattr(coordinator_cli, "_cmd_cutover", lambda *_, **__: calls.append("coordinator") or 0)
    monkeypatch.setattr(source_start, "ensure_supervisor", lambda *_, **__: calls.append("supervisor"))
    assert source_start.command(argparse.Namespace(json=True)) == 0
    assert calls == (["coordinator"] if coordinator else []) + (["supervisor"] if supervisor else [])


def test_explicit_native_scheduling_prepares_only_missing_owner_launchers(monkeypatch, tmp_path):
    from agent_dispatch import source_start

    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    custom = tmp_path / "serve-service.ps1"
    custom.write_text("# existing custom launcher\n")
    prepare_owner_launchers(tmp_path)
    prepare_owner_launchers(tmp_path)
    assert custom.read_text() == "# existing custom launcher\n"
    supervisor = (tmp_path / "supervise-service.ps1").read_text()
    assert "__source-start" in supervisor
    assert "current-version" in supervisor
    assert "last-known-good" not in supervisor
    assert "Start-ScheduledTask" not in supervisor
    assert "Register-ScheduledTask" not in supervisor


def test_native_classic_startup_retains_independent_disabled_profile_intent(monkeypatch, tmp_path):
    from agent_dispatch import coordinator_cli, installation_policy, source_start, supervisor_lane_status

    _sources(monkeypatch)
    (tmp_path / "supervisor.env").write_text("AGENT_DISPATCH_SUPERVISE_LABELS=primary\n")
    profiles = tmp_path / "supervisors"
    profiles.mkdir()
    (profiles / "child.env").write_text(
        "AGENT_DISPATCH_SUPERVISE_LABELS=child\nAGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--no-reactive\n",
    )
    calls = []
    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    monkeypatch.setattr(installation_policy, "windows_policy", lambda *_, **options: {
        "windows_scheduled_task": "preserve", "start_coordinator": True,
        "primary_start_supervisor": False,
        "start_supervisor": (
            options.get("observe_supervisor") is False
            or options.get("supervisor_profile") == "child"
        ),
    })
    monkeypatch.setattr(coordinator_cli, "_cmd_cutover", lambda *_, **__: calls.append("coordinator") or 0)
    monkeypatch.setattr(supervisor_lane_status, "ensure", lambda root, profile, arguments, **options: (
        calls.append((profile.name, arguments, options["generation"]))
    ))
    assert source_start.command(argparse.Namespace(json=True)) == 0
    assert calls[0] == "coordinator"
    assert len(calls) == 2
    assert calls[1][0] == "child.env"
    assert "--no-reactive" in calls[1][1]
    assert calls[1][1][0] == "supervise"
    assert calls[1][1][1] != "serve"
    assert len(calls[1][2]) == 64


def test_native_classic_extra_flags_cannot_bypass_the_environment_label_gate(monkeypatch, tmp_path):
    from agent_dispatch import source_start

    _sources(monkeypatch)
    (tmp_path / "supervisor.env").write_text(
        "AGENT_DISPATCH_SUPERVISE_LABELS= , , \n"
        "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--label extra-only\n",
    )
    plan = source_start.supervisor_plan(tmp_path)
    assert isinstance(plan, source_start.ClassicStartupPlan)
    assert plan.profiles == ()
