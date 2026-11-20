"""``scheduled-job`` resource and ``package.source``: schema, convergence,
withdrawal, run semantics, and the OS backends driven through fake runners."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

from agent_machines import resources as R
from agent_machines import scheduled_job_tasks as tasks
from agent_machines import scheduled_jobs as jobs
from agent_machines.manifest import ManifestError, load_package
from agent_machines.resources import ResourceContext, RunOutcome, apply_resources
from agent_machines.self_update_types import TIMEOUT_RETURNCODE, CommandResult

from ._helpers import base_package, write_package

JOB = {
    "type": "scheduled-job",
    "id": "hub-health",
    "command": {"application": "harness-tools", "entrypoint": "hub-health", "args": ["--quiet"]},
    "interval_seconds": 3600,
}
APP = {"type": "package", "id": "harness-tools", "manager": "uv-tool"}


def _apply(packages, machine, plat, ctx, dry_run):
    return apply_resources(
        packages, machine, plat, ctx, dry_run=dry_run, resource_types=frozenset({"scheduled-job"})
    )


def _pkg(tmp_path: Path, name: str, resources: list[dict]):
    data = base_package(name=name, resources=resources)
    path = write_package(tmp_path / name.replace("/", "_"), "pkg.yaml", data)
    return load_package(path, source_repo=name.split("/")[0])


def _ctx(tmp_path: Path, runner=None, plat: str = "windows") -> ResourceContext:
    return ResourceContext(
        home=tmp_path,
        repo_paths={"acme": tmp_path},
        platform=plat,
        runner=runner or (lambda argv: RunOutcome(0, "", "")),
    )


class FakeBackend:
    """Replaces the OS task backend; ``registered`` maps id -> (interval, timeout)."""

    def __init__(self, monkeypatch, registered=None):
        self.registered = dict(registered or {})
        self.calls: list[tuple] = []
        monkeypatch.setattr(tasks, "unsupported_reason", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "query", self.query)
        monkeypatch.setattr(tasks, "register", self.register)
        monkeypatch.setattr(tasks, "unregister", self.unregister)

    def query(self, platform, job_id, interval, timeout, **_):
        live = self.registered.get(job_id)
        return tasks.TaskState(live is not None, live == (interval, timeout))

    def register(self, platform, job_id, interval, timeout, **_):
        self.calls.append(("register", job_id, interval, timeout))
        self.registered[job_id] = (interval, timeout)
        return CommandResult([], 0, "", "")

    def unregister(self, platform, job_id, **kw):
        self.calls.append(("unregister", job_id, kw.get("stop", True)))
        self.registered.pop(job_id, None)
        return CommandResult([], 0, "", "")


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def test_valid_present_and_id_only_absent_declarations_load(tmp_path):
    pkg = _pkg(
        tmp_path, "acme/a", [JOB, {"type": "scheduled-job", "id": "old-job", "state": "absent"}]
    )
    assert [r["id"] for r in pkg.resources] == ["hub-health", "old-job"]


@pytest.mark.parametrize(
    "change",
    [
        {"id": "Bad_Id"},
        {"command": {"application": "t", "entrypoint": "bin/hub-health"}},
        {"command": {"application": "t", "entrypoint": "hub-health", "shell": "echo hi"}},
        {"command": {"application": "t", "entrypoint": "hub-health", "args": "--quiet"}},
        {"command": {"application": "t", "entrypoint": "python3", "args": ["-c", "print()"]}},
        {"command": {"application": "t", "entrypoint": "pwsh.exe"}},
        {"interval_seconds": 60},
        {"interval_seconds": 8 * 24 * 3600},
        {"timeout_seconds": 3600},
        {"command": None},
        {"unknown": 1},
    ],
)
def test_invalid_scheduled_job_declarations_are_rejected(tmp_path, change):
    decl = {k: v for k, v in {**JOB, **change}.items() if v is not None}
    with pytest.raises(ManifestError):
        _pkg(tmp_path, "acme/a", [decl])


@pytest.mark.parametrize(
    "app",
    [None, {**APP, "state": "absent"}, {**APP, "manager": "winget"}],
    ids=["undeclared", "absent", "unsupported-manager"],
)
def test_job_whose_application_is_not_a_declared_tool_package_is_unbound(
    tmp_path, monkeypatch, app
):
    backend = FakeBackend(monkeypatch)
    pkg = _pkg(tmp_path, "acme/a", [JOB] if app is None else [app, JOB])

    _, findings = R.resolve_resources([pkg], "box-1", "windows")
    results = _apply([pkg], "box-1", "windows", _ctx(tmp_path), dry_run=False)

    assert [f.code for f in findings] == ["scheduled-job-unbound"]
    assert [r.action for r in results] == ["error"] and backend.calls == []


def test_job_binds_to_its_declared_tool_package(tmp_path):
    resolved, findings = R.resolve_resources(
        [_pkg(tmp_path, "acme/a", [APP, JOB])], "box-1", "windows"
    )
    job = next(res for res in resolved if res.type == "scheduled-job")
    assert findings == []
    assert job.desired["application"] == {"manager": "uv-tool", "name": "harness-tools"}


# --------------------------------------------------------------------------- #
# Handler convergence
# --------------------------------------------------------------------------- #
def test_present_job_registers_then_is_idempotent_and_owned(tmp_path, monkeypatch):
    backend = FakeBackend(monkeypatch)
    pkg = _pkg(tmp_path, "acme/a", [APP, JOB])

    planned = _apply([pkg], "box-1", "windows", _ctx(tmp_path), dry_run=True)
    assert [(r.action, r.dry_run) for r in planned] == [("install", True)]
    assert backend.calls == [] and jobs.read_owned(tmp_path) == {}

    first = _apply([pkg], "box-1", "windows", _ctx(tmp_path), dry_run=False)
    assert [r.action for r in first] == ["install"]
    assert backend.calls == [("register", "hub-health", 3600, 900)]
    assert jobs.read_owned(tmp_path)["hub-health"]["sources"] == [str(pkg.source_path)]

    second = _apply([pkg], "box-1", "windows", _ctx(tmp_path), dry_run=False)
    assert [r.action for r in second] == ["none"]
    assert len(backend.calls) == 1


def test_changed_interval_reregisters(tmp_path, monkeypatch):
    backend = FakeBackend(monkeypatch, {"hub-health": (7200, 900)})
    results = _apply(
        [_pkg(tmp_path, "acme/a", [APP, JOB])], "box-1", "windows", _ctx(tmp_path), dry_run=False
    )
    assert [r.action for r in results] == ["install"]
    assert backend.registered["hub-health"] == (3600, 900)


def test_absent_declaration_unregisters(tmp_path, monkeypatch):
    backend = FakeBackend(monkeypatch, {"hub-health": (3600, 900)})
    pkg = _pkg(
        tmp_path, "acme/a", [{"type": "scheduled-job", "id": "hub-health", "state": "absent"}]
    )
    results = _apply([pkg], "box-1", "windows", _ctx(tmp_path), dry_run=False)
    assert [r.action for r in results] == ["uninstall"]
    assert backend.calls == [("unregister", "hub-health", True)]


def test_withdrawn_declaration_is_swept_but_out_of_scope_owner_is_kept(tmp_path, monkeypatch):
    backend = FakeBackend(monkeypatch, {"hub-health": (3600, 900), "repo-sync": (3600, 900)})
    pkg = _pkg(tmp_path, "acme/a", [])  # still in scope, no longer declares hub-health
    other = _pkg(tmp_path, "acme/b", [])  # exists on disk but not passed in scope
    jobs.record_owned(tmp_path, "hub-health", [str(pkg.source_path)])
    jobs.record_owned(tmp_path, "repo-sync", [str(other.source_path)])

    results = _apply([pkg], "box-1", "windows", _ctx(tmp_path), dry_run=False)

    assert [(r.id, r.action) for r in results] == [("hub-health", "uninstall")]
    assert set(jobs.read_owned(tmp_path)) == {"repo-sync"}
    assert "repo-sync" in backend.registered


def test_owned_job_whose_declaring_file_is_gone_is_swept(tmp_path, monkeypatch):
    FakeBackend(monkeypatch, {"hub-health": (3600, 900)})
    jobs.record_owned(tmp_path, "hub-health", [str(tmp_path / "deleted" / "pkg.yaml")])
    results = _apply([], "box-1", "windows", _ctx(tmp_path), dry_run=False)
    assert [(r.id, r.action) for r in results] == [("hub-health", "uninstall")]


def test_owned_job_from_an_unreachable_repo_is_not_swept(tmp_path, monkeypatch):
    backend = FakeBackend(monkeypatch, {"hub-health": (3600, 900)})
    unreachable = tmp_path.parent / f"{tmp_path.name}-unmounted" / "pkg.yaml"
    jobs.record_owned(tmp_path, "hub-health", [str(unreachable)])
    assert _apply([], "box-1", "windows", _ctx(tmp_path), dry_run=False) == []
    assert "hub-health" in backend.registered and "hub-health" in jobs.read_owned(tmp_path)


def test_unsupported_platform_is_skipped(tmp_path, monkeypatch):
    FakeBackend(monkeypatch)
    monkeypatch.setattr(tasks, "unsupported_reason", lambda *a, **k: "no systemd")
    results = _apply(
        [_pkg(tmp_path, "acme/a", [APP, JOB])],
        "box-1",
        "linux",
        _ctx(tmp_path, plat="linux"),
        dry_run=False,
    )
    assert [(r.action, r.skipped_reason) for r in results] == [("skip", "no systemd")]


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        (7200, (7200, "override")),
        (10, (901, "override")),  # clamped above the minimum and the timeout
        ("x", (3600, "declared")),
    ],
)
def test_interval_override_is_clamped(tmp_path, override, expected):
    root = jobs.jobs_root(tmp_path)
    root.mkdir(parents=True)
    (root / "overrides.json").write_text(
        json.dumps({"hub-health": {"interval_seconds": override}}), encoding="utf-8"
    )
    assert jobs.effective_interval("hub-health", 3600, 900, tmp_path) == expected


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
DESIRED = {
    "state": "present",
    "command": JOB["command"],
    "interval_seconds": 3600,
    "timeout_seconds": 5,
    "application": {"manager": "uv-tool", "name": "harness-tools"},
}


def _install(tmp_path: Path, name: str) -> Path:
    path = tmp_path / "bin" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


@pytest.mark.parametrize("tomllib_available", [True, False])
def test_run_resolves_entrypoint_from_uv_receipt_not_path(
    tmp_path, monkeypatch, tomllib_available
):
    if not tomllib_available:
        monkeypatch.setitem(sys.modules, "tomllib", None)
    entry, other = _install(tmp_path, "hub-health.exe"), _install(tmp_path, "repo-sync.exe")
    tool_dir = tmp_path / "tools"
    (tool_dir / "harness-tools").mkdir(parents=True)
    (tool_dir / "harness-tools" / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "harness-tools" }]\nentrypoints = [\n'
        f'    {{ name = "repo-sync", install-path = {json.dumps(str(other))} }},\n'
        f'    {{ name = "hub-health", install-path = \'{entry}\', from = "harness-tools" }},\n]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", str(tmp_path / "elsewhere"))
    seen = []

    def runner(argv, *, cwd=None, timeout=None):
        seen.append(argv)
        out = f"{tool_dir}\n" if argv == ["uv", "tool", "dir"] else ""
        return CommandResult(argv, 0, out, "")

    assert jobs.run_job("hub-health", DESIRED, home=tmp_path, runner=runner) == 0
    assert seen == [["uv", "tool", "dir"], [str(entry), "--quiet"]]


def test_run_resolves_entrypoint_from_pipx_metadata(tmp_path):
    entry = _install(tmp_path, "hub-health")
    listing = {
        "venvs": {
            "harness-tools": {
                "metadata": {"main_package": {"app_paths": [{"__Path__": str(entry)}]}}
            }
        }
    }
    seen = []

    def runner(argv, *, cwd=None, timeout=None):
        seen.append(argv)
        out = json.dumps(listing) if argv[0] == "pipx" else ""
        return CommandResult(argv, 0, out, "")

    desired = {**DESIRED, "application": {"manager": "pipx", "name": "harness-tools"}}
    assert jobs.run_job("hub-health", desired, home=tmp_path, runner=runner) == 0
    assert seen[-1] == [str(entry), "--quiet"]


def test_run_refuses_entrypoint_the_application_does_not_provide(tmp_path):
    listing = {"venvs": {"harness-tools": {"metadata": {"main_package": {"app_paths": []}}}}}
    seen = []

    def runner(argv, *, cwd=None, timeout=None):
        seen.append(argv)
        return CommandResult(argv, 0, json.dumps(listing), "")

    desired = {**DESIRED, "application": {"manager": "pipx", "name": "harness-tools"}}
    assert jobs.run_job("hub-health", desired, home=tmp_path, runner=runner) == 127
    assert seen == [["pipx", "list", "--json"]]
    log = (jobs.jobs_root(tmp_path) / "logs" / "hub-health.log").read_text("utf-8")
    assert "does not provide entrypoint 'hub-health'" in log


def test_run_records_timeout_and_log(tmp_path, monkeypatch):
    monkeypatch.setattr(
        jobs, "resolve_entrypoint", lambda app, name, runner: ("C:/bin/hub-health.exe", "")
    )
    seen = []

    def runner(argv, *, cwd=None, timeout=None):
        seen.append((argv, timeout))
        return CommandResult(argv, TIMEOUT_RETURNCODE, "partial", "command timed out after 5s")

    assert jobs.run_job("hub-health", DESIRED, home=tmp_path, runner=runner) == TIMEOUT_RETURNCODE
    assert seen == [(["C:/bin/hub-health.exe", "--quiet"], 5)]
    state = jobs.read_state("hub-health", tmp_path)
    assert state["exit_code"] == TIMEOUT_RETURNCODE and state["timed_out"] is True
    assert "partial" in (jobs.jobs_root(tmp_path) / "logs" / "hub-health.log").read_text("utf-8")


def test_run_is_noop_while_another_run_holds_the_lock(tmp_path, monkeypatch):
    class Held:
        def try_acquire(self):
            return "timeout"

        def release(self):
            raise AssertionError("never acquired")

        def close(self):
            pass

    monkeypatch.setattr(jobs, "_lock", lambda job_id, home: Held())
    runner = lambda *a, **k: pytest.fail("must not run")  # noqa: E731
    assert jobs.run_job("hub-health", DESIRED, home=tmp_path, runner=runner) == 0
    assert jobs.read_state("hub-health", tmp_path) == {}


def _cli_run(tmp_path, monkeypatch, *, sources, roots, examined=()):
    """Run ``cmd_run`` for an undeclared, owned job; return (rc, backend calls)."""
    from agent_machines import cli_scheduled_job as cli

    calls = []
    monkeypatch.setattr(jobs, "jobs_root", lambda home=None: tmp_path / "scheduled-jobs")
    jobs.record_owned(None, "hub-health", [str(s) for s in sources])
    monkeypatch.setattr(
        cli, "_declared", lambda args: cli.Resolution({}, {str(p) for p in examined}, roots)
    )
    monkeypatch.setattr(cli._discover, "current_platform", lambda: "windows")
    monkeypatch.setattr(tasks, "unsupported_reason", lambda *a, **k: None)
    monkeypatch.setattr(
        tasks,
        "unregister",
        lambda plat, job_id, **kw: (
            calls.append((job_id, kw["stop"])) or CommandResult([], 0, "", "")
        ),
    )
    monkeypatch.setattr(jobs, "run_job", lambda *a, **k: pytest.fail("must not run"))
    return cli.cmd_run(argparse.Namespace(id="hub-health", machine=None)), calls


def test_run_keeps_timer_when_declaring_repo_is_undiscovered(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "unmounted-repo"
    rc, calls = _cli_run(tmp_path, monkeypatch, sources=[repo / "pkg.yaml"], roots=[repo])
    assert (rc, calls) == (0, [])
    assert "hub-health" in jobs.read_owned()
    assert "declaration unavailable; keeping timer" in capsys.readouterr().err


def test_run_removes_timer_when_discovered_file_no_longer_declares_it(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source = write_package(repo, "pkg.yaml", base_package(name="acme/a", resources=[]))
    rc, calls = _cli_run(tmp_path, monkeypatch, sources=[source], roots=[repo], examined=[source])
    assert (rc, calls) == (0, [("hub-health", False)])
    assert jobs.read_owned() == {}


def test_run_removes_timer_when_discovered_file_was_deleted(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    rc, calls = _cli_run(tmp_path, monkeypatch, sources=[repo / "pkg.yaml"], roots=[repo])
    assert (rc, calls) == (0, [("hub-health", False)])
    assert jobs.read_owned() == {}


# --------------------------------------------------------------------------- #
# OS backends
# --------------------------------------------------------------------------- #
def test_windows_registration_is_headless_ignore_new_without_catch_up(tmp_path):
    scripts = []

    def runner(argv, *, timeout=None):
        scripts.append(argv[-1])
        return CommandResult(argv, 0, "", "")

    tasks.register(
        "windows",
        "hub-health",
        3600,
        900,
        runner=runner,
        resolve_binary=lambda name: f"C:/{name}.exe",
        home=tmp_path,
    )
    script = scripts[0]
    assert "-Execute 'conhost.exe'" in script and "--headless" in script
    assert "scheduled-job run hub-health" in script
    assert "-MultipleInstances IgnoreNew" in script and "StartWhenAvailable" not in script
    assert "-RunLevel Limited" in script and "-Seconds 3600" in script


def test_linux_units_register_and_match(tmp_path):
    calls = []

    def runner(argv, *, timeout=None):
        calls.append(argv)
        return CommandResult(argv, 0, "enabled\n" if "is-enabled" in argv else "", "")

    tasks.register(
        "linux", "hub-health", 3600, 900, runner=runner, resolve_binary=lambda n: n, home=tmp_path
    )
    service, timer = tasks.render_units("hub-health", 3600, 900, tmp_path)
    assert "Type=oneshot" in service and "TimeoutStartSec=900" in service
    assert "OnUnitActiveSec=3600s" in timer and "Persistent=false" in timer
    assert [
        "systemctl",
        "--user",
        "enable",
        "--now",
        "agent-machines-scheduled-job-hub-health.timer",
    ] in calls
    assert tasks.query(
        "linux", "hub-health", 3600, 900, runner=runner, resolve_binary=lambda n: n, home=tmp_path
    ).matching
    assert not tasks.query(
        "linux", "hub-health", 7200, 900, runner=runner, resolve_binary=lambda n: n, home=tmp_path
    ).matching


# --------------------------------------------------------------------------- #
# package.source
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("manager", "source", "expected"),
    [
        ("uv-tool", {"path": "tool"}, None),
        (
            "pipx",
            {"git": "https://example.com/tools.git", "ref": "v1"},
            ["pipx", "install", "--force", "git+https://example.com/tools.git@v1"],
        ),
    ],
)
def test_sourced_package_installs_from_source(tmp_path, monkeypatch, manager, source, expected):
    monkeypatch.setattr(R.shutil, "which", lambda b: f"C:/{b}.exe")
    calls = []

    def runner(argv):
        calls.append(argv)
        return RunOutcome(0, "", "")

    pkg = _pkg(
        tmp_path,
        "acme/a",
        [
            {"type": "package", "id": "harness-tools", "manager": manager, "source": source},
        ],
    )
    results = apply_resources([pkg], "box-1", "windows", _ctx(tmp_path, runner), dry_run=False)

    assert [r.action for r in results] == ["install"]
    if expected is None:
        expected = [
            "uv",
            "tool",
            "install",
            "--force",
            str((pkg.source_path.parent / "tool").resolve()),
        ]
    assert calls[-1] == expected


@pytest.mark.parametrize(
    ("manager", "source"),
    [
        ("winget", {"path": "C:/tool"}),
        ("uv-tool", {"path": "a", "git": "https://example.com/x.git"}),
        ("uv-tool", {"ref": "v1"}),
        ("uv-tool", {"git": "--upload-pack=x"}),
    ],
)
def test_invalid_package_source_is_rejected(tmp_path, manager, source):
    with pytest.raises(ManifestError):
        _pkg(
            tmp_path,
            "acme/a",
            [
                {"type": "package", "id": "harness-tools", "manager": manager, "source": source},
            ],
        )
