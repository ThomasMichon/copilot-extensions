"""Selected-source reads preserve privacy, limits, attribution and partials."""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import json
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml
from machine_transport import MachineEntry, SSHEnvironment, normalize_machine_entries

from worktree_manager import engine_client, launch_history_cli, launch_history_sweep as sweep, machine_roster
from worktree_manager.launch_attempts import LaunchAttempts


def arguments(**changes):
    return argparse.Namespace(
        **{
            "attempt_id": None, "include_input": False, "summary": False,
            "limit": 100, "before": None, "before_id": None, "after": None,
            "project": None, "action": None, "machine": None, "environment": None,
            "provider": None, "venue": None, "outcome": None, "event_limit": 100,
            "after_sequence": 0, "receipt_limit": 100, "receipt_after": None,
            "sweep_project": "source-project", "source_machine": ["host-linux"],
            **changes,
        },
    )


def entry(key="host-linux", *, alias="host-profile", shell="bash", ready=True):
    return MachineEntry(
        key=key, display_name="Example machine", environment="linux",
        execution_platform="linux", physical_host=key.removesuffix("-linux"),
        ssh_ready=ready, ssh_environments=[SSHEnvironment("linux", alias, shell)],
    )


@pytest.fixture
def page(tmp_path):
    store = LaunchAttempts(tmp_path)
    attempt = uuid4().hex
    store.record_intent(
        attempt, project="journal-project", action="new", surface="new-dialog",
        machine="selected-venue", environment="linux", is_local=False,
        prompt="PRIVATE-FIXTURE", options={"no_mux": True},
    )
    return {
        "schema": "worktree-manager-launch-history", "version": 1, "scope": "local-manager",
        "attempts": store.entries(), "has_more": False, "next_before": None, "next_before_id": None,
    }


def test_sweep_routes_only_explicit_sources_and_preserves_filters(page, monkeypatch, capsys):
    machines = {"host-linux": entry(), "unselected-linux": entry("unselected-linux")}
    monkeypatch.setattr(machine_roster, "selected_entries", lambda project: machines)
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **kw: pytest.fail("engine called"))
    monkeypatch.setattr(launch_history_cli, "LaunchAttempts", lambda: pytest.fail("local journal opened"))
    calls = []

    async def read(target, query, **kwargs):
        calls.append((target.key, query, kwargs))
        return json.dumps(page).encode()

    monkeypatch.setattr(sweep, "query_source", read)
    assert launch_history_cli.main([
        "--sweep-project", "source-project", "--source-machine", "host-linux",
        "--project", "journal-project", "--machine", "selected-venue", "--json",
    ]) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["scope"] == "selected-configured-ssh-managers"
    assert result["prompt_acceptance"] == "unknown" and result["atomic"] is False
    assert result["roster_project"] == "source-project"
    assert len(result["sources"]) == 1
    assert result["sources"][0]["machine"] == "host-linux"
    assert result["sources"][0]["environment"] == "linux"
    assert result["sources"][0]["page"] == page
    assert "PRIVATE-FIXTURE" not in output and '"input"' not in output
    assert calls == [("host-linux", [
        "launch-attempts", "--json", "--limit", "100", "--project", "journal-project",
        "--machine", "selected-venue",
    ], {"timeout": sweep.SOURCE_TIMEOUT})]


@pytest.mark.parametrize("changes", [
    {"sweep_project": None}, {"source_machine": []}, {"source_machine": ["a"] * 9},
    {"source_machine": [""]}, {"source_machine": ["\0"]},
    {"attempt_id": "a" * 32}, {"include_input": True}, {"summary": True},
    {"event_limit": 1}, {"after_sequence": 1}, {"receipt_limit": 1}, {"receipt_after": "x"},
    {"limit": 0}, {"limit": 101}, {"after": float("nan")}, {"before": float("inf")},
    {"after": -1}, {"before_id": "a" * 32}, {"before_id": "invalid", "before": 2},
    {"action": "replay"},
])
def test_invalid_query_never_reads_configuration(changes, monkeypatch):
    monkeypatch.setattr(machine_roster, "selected_entries", lambda *a: pytest.fail("registry read"))
    with pytest.raises(sweep.SweepError, match="invalid-query"):
        asyncio.run(sweep.read_sweep(arguments(**changes)))


def test_source_failure_is_not_empty_success_and_keeps_independent_page(page, monkeypatch, capsys):
    machines = {"first-linux": entry("first-linux"), "second-linux": entry("second-linux")}
    monkeypatch.setattr(machine_roster, "selected_entries", lambda project: machines)

    async def read(target, *a, **kw):
        if target.key == "first-linux":
            raise sweep.SweepError("source-timeout")
        return json.dumps(page).encode()

    monkeypatch.setattr(sweep, "query_source", read)
    assert launch_history_cli.main([
        "--sweep-project", "source-project", "--source-machine", "first-linux",
        "--source-machine", "missing", "--source-machine", "second-linux", "--json",
    ]) == 1
    sources = json.loads(capsys.readouterr().out)["sources"]
    assert [source["status"] for source in sources] == ["unavailable", "unavailable", "read"]
    assert [source.get("code") for source in sources] == ["source-timeout", "not-configured", None]
    assert sources[2]["page"] == page


def test_invalid_roster_preserves_each_requested_source(monkeypatch):
    def fail(*a):
        raise ValueError("PRIVATE-PATH")
    monkeypatch.setattr(machine_roster, "selected_entries", fail)
    report = asyncio.run(sweep.read_sweep(arguments(source_machine=["one", "two"])))
    assert report["errors"] == [{"source": "configured-roster", "code": "unavailable-or-invalid"}]
    assert [source["selection"] for source in report["sources"]] == ["one", "two"]
    assert all(source["code"] == "roster-unavailable" for source in report["sources"])
    assert "PRIVATE-PATH" not in json.dumps(report)


def test_aliases_do_not_query_the_same_source_twice(page, monkeypatch):
    target = entry()
    monkeypatch.setattr(machine_roster, "selected_entries", lambda project: {target.key: target})
    calls = []
    async def read(target, *a, **kw):
        calls.append(target.key)
        return json.dumps(page).encode()
    report = asyncio.run(sweep.read_sweep(
        arguments(source_machine=[target.key, "host-profile"]), read_source=read,
    ))
    assert calls == ["host-linux"]
    assert report["sources"][1]["code"] == "duplicate-source"


@pytest.mark.parametrize("selection", ["mixed-host", "non-default-linux"])
def test_unsplit_legacy_profiles_never_dispatch_the_default_for_another_selection(monkeypatch, selection):
    mixed = MachineEntry(
        key="mixed-host", display_name="Mixed legacy host", environment="windows", ssh_ready=True,
        ssh_environments=[
            SSHEnvironment("windows", "default-windows", "pwsh"),
            SSHEnvironment("linux", "non-default-linux", "bash"),
        ],
    )
    machines = normalize_machine_entries({mixed.key: mixed})
    monkeypatch.setattr(machine_roster, "selected_entries", lambda _: machines)
    monkeypatch.setattr(
        sweep, "SSHProfileSource", lambda *a, **kw: pytest.fail("ambiguous source must not connect"),
    )
    report = asyncio.run(sweep.read_sweep(arguments(source_machine=[selection])))
    assert report["sources"][0]["status"] == "unavailable"
    assert report["sources"][0]["code"] == "ambiguous-ssh-profile"
    assert "page" not in report["sources"][0]


def test_total_deadline_does_not_expand_per_source(page, monkeypatch):
    machines = {"first-linux": entry("first-linux"), "second-linux": entry("second-linux")}
    monkeypatch.setattr(machine_roster, "selected_entries", lambda project: machines)
    times = iter([0, 10, sweep.TOTAL_TIMEOUT + 1])
    monkeypatch.setattr(sweep, "monotonic", lambda: next(times))
    calls = []
    async def read(target, *a, **kw):
        calls.append((target.key, kw["timeout"]))
        return json.dumps(page).encode()
    report = asyncio.run(sweep.read_sweep(
        arguments(source_machine=list(machines)), read_source=read,
    ))
    assert calls == [("first-linux", sweep.SOURCE_TIMEOUT)]
    assert report["sources"][1]["code"] == "sweep-deadline"


@pytest.mark.parametrize("change", [
    lambda p: p.update(version=True),
    lambda p: p.update(scope="all-machines"),
    lambda p: p.update(input="PRIVATE"),
    lambda p: p.update(attempts=[] , has_more=True),
    lambda p: p.update(next_before=1),
    lambda p: p["attempts"][0].update(input="PRIVATE"),
    lambda p: p["attempts"][0].update(submitted_at=True),
    lambda p: p["attempts"][0].update(manager_outcome="accepted"),
    lambda p: p["attempts"][0].update(is_local=0),
    lambda p: p["attempts"][0].update(last_manager_event={"details": "PRIVATE"}),
])
def test_remote_response_is_strict_and_never_echoes_unknown_fields(page, change):
    change(page)
    with pytest.raises(sweep.SweepError, match="incompatible-response"):
        sweep.validate_page(json.dumps(page).encode(), arguments())


def test_independent_continuation_roundtrips_without_fetching_more(page):
    row = page["attempts"][0]
    page.update(
        has_more=True, next_before=row["submitted_at"], next_before_id=row["attempt_id"],
    )
    assert sweep.validate_page(json.dumps(page).encode(), arguments(limit=1)) == page
    next_args = arguments(before=page["next_before"], before_id=page["next_before_id"])
    query = sweep.query_argv(next_args)
    assert "--before" in query and "--before-id" in query
    with pytest.raises(sweep.SweepError, match="incompatible-response"):
        sweep.validate_page(json.dumps(page).encode(), next_args)


def test_ignored_filters_and_duplicate_rows_are_incompatible(page):
    with pytest.raises(sweep.SweepError):
        sweep.validate_page(json.dumps(page).encode(), arguments(project="different"))
    duplicate = copy.deepcopy(page["attempts"][0])
    page["attempts"].append(duplicate)
    with pytest.raises(sweep.SweepError):
        sweep.validate_page(json.dumps(page).encode(), arguments())


def test_output_cap_applies_before_parsing():
    with pytest.raises(sweep.SweepError, match="response-too-large"):
        sweep.validate_page(b"x" * (sweep.MAX_RESPONSE_BYTES + 1), arguments())


@pytest.mark.parametrize("sequence", [None, 0, -1, True, 1.0, "1", 1])
def test_malformed_event_cannot_carry_private_details(page, sequence):
    page["attempts"][0].update(
        manager_outcome="unknown", last_manager_event={
            "sequence": sequence, "observed_at": 1, "phase": "confirmed",
            "details": {"input": "PRIVATE-FIXTURE"},
        },
    )
    with pytest.raises(sweep.SweepError, match="incompatible-response"):
        sweep.validate_page(json.dumps(page).encode(), arguments())


@pytest.mark.parametrize("observed_at", [None, True, float("nan"), float("inf"), -1])
def test_nonnull_event_requires_a_real_finite_observation_time(page, observed_at):
    page["attempts"][0]["last_manager_event"] = {
        "sequence": 1, "observed_at": observed_at, "phase": "confirmed", "details": {},
    }
    with pytest.raises(sweep.SweepError, match="incompatible-response"):
        sweep.validate_page(json.dumps(page).encode(), arguments())


def test_invalid_event_never_leaks_input_from_cli(page, monkeypatch, capsys):
    page["attempts"][0]["last_manager_event"] = {
        "sequence": None, "observed_at": 1, "phase": "confirmed",
        "details": {"input": "PRIVATE-FIXTURE"},
    }
    monkeypatch.setattr(machine_roster, "selected_entries", lambda _: {"host-linux": entry()})
    async def read(*a, **kw):
        return json.dumps(page).encode()
    monkeypatch.setattr(sweep, "query_source", read)
    assert launch_history_cli.main([
        "--sweep-project", "source-project", "--source-machine", "host-linux", "--json",
    ]) == 1
    output = capsys.readouterr().out
    assert "PRIVATE-FIXTURE" not in output and '"input"' not in output
    assert json.loads(output)["sources"][0]["code"] == "incompatible-response"


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_posix_remote_uses_configured_shell_and_absolute_manager(shell):
    command = sweep.remote_command(shell, ["launch-attempts", "--project", "a'; echo unsafe"])
    assert command.startswith(shell + " -lc ")
    assert '"$HOME/.local/bin/worktree-manager"' in command
    assert "agent-worktrees" not in command and "setup" not in command


def test_windows_remote_uses_published_manager_and_literal_arguments():
    command = sweep.remote_command("pwsh", ["launch-attempts", "--project", "a'; unsafe"])
    script = base64.b64decode(command.split()[-1]).decode("utf-16-le")
    assert ".local\\bin\\worktree-manager.ps1" in script
    assert "'a''; unsafe'" in script
    assert "Test-Path -LiteralPath" in script and "exit $LASTEXITCODE" in script
    with pytest.raises(sweep.SweepError, match="unsupported-shell"):
        sweep.remote_command("unknown", [])


def test_transport_uses_existing_proxy_owner_and_cleans_up(monkeypatch, page):
    calls, cleanups = [], []
    config = SimpleNamespace(port=2200, user="example")
    monkeypatch.setattr(sweep.shutil, "which", lambda _: "ssh")
    class Profile:
        def __init__(self, *a, **kw):
            calls.append(("profile", a, kw))
        def get_ssh_config(self):
            return config
    monkeypatch.setattr(sweep, "SSHProfileSource", Profile)
    async def spawn(*argv, **kwargs):
        calls.append(("spawn", argv, kwargs))
        stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
        stdout.feed_data(json.dumps(page).encode())
        stdout.feed_eof()
        stderr.feed_data(b"PRIVATE STDERR")
        stderr.feed_eof()
        async def wait():
            return 0
        return SimpleNamespace(stdout=stdout, stderr=stderr, wait=wait)
    async def cleanup(process, **kwargs):
        cleanups.append((process, kwargs))
    monkeypatch.setattr(sweep, "create_ssh_subprocess", spawn)
    monkeypatch.setattr(sweep, "terminate_ssh_process_tree", cleanup)
    assert asyncio.run(sweep.query_source(entry(), ["launch-attempts", "--json"], timeout=10)) == (
        json.dumps(page).encode()
    )
    assert calls[0] == ("profile", ("host-profile",), {
        "user": None, "port": None, "resolve_timeout": 5,
    })
    argv, kwargs = calls[1][1:]
    assert argv[-3:-1] == ("--", "host-profile")
    assert "StrictHostKeyChecking=yes" in argv and "ClearAllForwardings=yes" in argv
    assert "ControlMaster=no" in argv and "UpdateHostKeys=no" in argv
    assert "-a" in argv and "-x" in argv
    assert kwargs["config"] is config and kwargs["stdin"] == asyncio.subprocess.DEVNULL
    assert len(cleanups) == 1 and cleanups[0][1] == {"grace": 1}


@pytest.mark.parametrize("failure", ["timeout", "too-large", "nonzero", "cancel"])
def test_transport_failure_and_cancellation_close_owned_process(monkeypatch, failure):
    monkeypatch.setattr(sweep.shutil, "which", lambda _: "ssh")
    monkeypatch.setattr(sweep, "SSHProfileSource", lambda *a, **kw: SimpleNamespace(
        get_ssh_config=lambda: SimpleNamespace(port=None, user=None),
    ))
    cleaned = []
    async def spawn(*a, **kw):
        stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
        if failure == "too-large":
            stdout.feed_data(b"x" * (sweep.MAX_RESPONSE_BYTES + 1))
        if failure == "nonzero":
            stdout.feed_eof()
            stderr.feed_eof()
        async def wait():
            return 127
        return SimpleNamespace(stdout=stdout, stderr=stderr, wait=wait)
    async def cleanup(process, **kw):
        cleaned.append(process)
    monkeypatch.setattr(sweep, "create_ssh_subprocess", spawn)
    monkeypatch.setattr(sweep, "terminate_ssh_process_tree", cleanup)
    async def run():
        task = asyncio.create_task(sweep.query_source(entry(), [], timeout=0.05))
        if failure == "cancel":
            await asyncio.sleep(0.01)
            task.cancel()
        return await task
    expected = asyncio.CancelledError if failure == "cancel" else sweep.SweepError
    with pytest.raises(expected):
        asyncio.run(run())
    assert len(cleaned) == 1


def test_profile_resolution_timeout_is_passed_to_existing_owner(monkeypatch):
    import ssh_manager.config_sources as configs
    monkeypatch.setattr(configs.shutil, "which", lambda _: "ssh")
    calls = []
    def run(*a, **kw):
        calls.append(kw)
        raise subprocess.TimeoutExpired("ssh", kw["timeout"])
    monkeypatch.setattr(configs.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        configs.SSHProfileSource("example", resolve_timeout=0.5).get_ssh_config()
    assert calls[0]["timeout"] == 0.5


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def machine(alias):
    return {
        "environment": "Linux",
        "ssh": {"ready": True, "environments": [{"name": "linux", "alias": alias, "shell": "zsh"}]},
    }


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    root, overlay, home = tmp_path / "project", tmp_path / "knowledge", tmp_path / "home"
    root.mkdir()
    overlay.mkdir()
    write(home / ".agent-worktrees" / "repos.yaml", {"repos": {
        "example": {"windows": str(root), "linux": str(root)},
        "knowledge": {"windows": str(overlay), "linux": str(overlay)},
        "unrelated": {"windows": str(tmp_path / "must-not-read")},
    }})
    write(home / ".agent-worktrees" / "projects.yaml", {"projects": {
        "example": {"config_dir": "~/.example"}, "knowledge": {},
        "unrelated": {},
    }})
    write(home / ".example" / "config.yaml", {"knowledge_repo": "knowledge"})
    return root, overlay, home


def test_pure_selected_roster_merges_files_without_engine_or_other_project(configured, monkeypatch):
    root, _, home = configured
    write(root / "machines.yaml", {"machines": {"first": machine("old"), "legacy": machine("legacy")}})
    write(root / ".agent-worktrees" / "machines.yaml", {"machines": {"first": machine("new")}})
    monkeypatch.setattr(engine_client, "get_value", lambda *a: pytest.fail("engine scalar"))
    result = machine_roster.selected_entries("example", home_dir=home)
    assert {item.ssh_environments[0].alias for item in result.values()} == {"new", "legacy"}


def test_selected_roster_supplier_preserves_canonical_first_fallback(configured):
    _, overlay, home = configured
    write(overlay / "machines.yaml", {"machines": {"old": machine("old")}})
    write(overlay / ".agent-worktrees" / "machines.yaml", {"machines": {"current": machine("current")}})
    result = machine_roster.selected_entries("example", home_dir=home)
    assert [item.ssh_environments[0].alias for item in result.values()] == ["current"]


@pytest.mark.parametrize("supplier", [False, True])
def test_explicit_registered_anchors_work_without_optional_repos_registry(configured, supplier):
    root, overlay, home = configured
    (home / ".agent-worktrees" / "repos.yaml").unlink()
    write(home / ".agent-worktrees" / "projects.yaml", {"projects": {
        "example": {"anchor": str(root)}, "knowledge": {"anchor": str(overlay)},
    }})
    selected = overlay if supplier else root
    write(selected / "machines.yaml", {"machines": {"host": machine("configured")}})
    result = machine_roster.selected_entries("example", home_dir=home)
    assert [item.ssh_environments[0].alias for item in result.values()] == ["configured"]


@pytest.mark.parametrize("content", ["[bad", "[]", "{}", "machines: []", "machines: {}\nmachines: {}"])
def test_invalid_selected_roster_refuses_instead_of_empty_success(configured, content):
    root, _, home = configured
    (root / "machines.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        machine_roster.selected_entries("example", home_dir=home)


def test_empty_configured_mapping_is_a_valid_empty_roster(configured):
    root, _, home = configured
    write(root / "machines.yaml", {"machines": {}})
    assert machine_roster.selected_entries("example", home_dir=home) == {}


def test_cell_context_never_silently_uses_legacy_registry(configured, monkeypatch):
    _, _, home = configured
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "selected-cell")
    with pytest.raises(ValueError, match="Cell-scoped"):
        machine_roster.selected_entries("example", home_dir=home)
