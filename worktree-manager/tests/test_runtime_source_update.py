"""Source-aware suite caller: qualify the actual provider before any installation."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import engine_client as ec
from worktree_manager import runtime_source_config as config
from worktree_manager import runtime_source_prepare as preparation
from worktree_manager import runtime_source_update as update
from worktree_manager import self_install, update_orchestration
from worktree_manager.runtime_source import ResolvedSource


def inventory(*names: str) -> dict:
    return {
        "contract": 1,
        "runtime_sources": {
            "schema": update.SCHEMA, "version": update.CONTRACT, "application_profiles": {},
        },
        "entries": [{
            "id": name, "title": name, "kind": "universal", "enabled": "active",
            "installed": True, "payload": "1", "selected": "1", "running": "1",
            "source": "marketplace", "updatable": True, "limitation": "",
        } for name in names],
    }


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


def enable() -> None:
    config.set_source(config.RuntimeSource("dev", allow_dev=True))


@pytest.mark.parametrize("phase", [
    "complete", "reuse", "portfolio", "path", "outside", "mixed", "timeout", "late",
])
@pytest.mark.parametrize("extras", [None, {"agent-bridge": ("fixture-extra",)}])
def test_complete_application_handoff_uses_one_owned_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, extras,
) -> None:
    directory = tmp_path / "trusted" / "tools"
    directory.mkdir(parents=True)
    provider = directory / "local_install_source.py"
    provider.write_text("# trusted provider fixture\n", encoding="utf-8")
    tool = directory / "prepare_runtime_source.py"
    resolved = ResolvedSource(
        config.RuntimeSource("dev", allow_dev=True), tmp_path / "checkout",
        "a" * 40, "b" * 40, "c" * 40, "d" * 40,
    )
    output = tmp_path / "prepared"
    names = ("agent-worktrees", "agent-bridge")
    expected = {name: str(output / "profiles" / name / "snapshot") for name in names}
    if phase == "reuse":
        expected = {name: str(output.parent / "previous" / "profiles" / name / "snapshot") for name in names}
    deadline = time.monotonic() + 30
    clock = time.monotonic
    late = [False]
    monkeypatch.setattr(preparation.time, "monotonic", lambda: deadline + 1 if late[0] else clock())
    calls = []

    def run(command, **options):
        calls.append(command)
        assert command == [
            sys.executable, "-I", "-B", "-X", "utf8", str(provider),
            "--source-checkout", str(resolved.checkout), "--source-commit", resolved.commit,
            "--main-head", resolved.main_head, "--branch", "dev",
            "--output", str(output), "--prepare-applications", "--uv", "selected-uv",
            "--deadline", str(deadline),
            "--project", "agent-worktrees", "--project", "agent-bridge",
            *(["--application-extras", json.dumps(extras, sort_keys=True)] if extras is not None else []),
        ]
        assert options["cwd"] == provider.parent
        assert options["env"] == {"FEED_SETTING": "retained", "PYTHONUTF8": "1", "PYTHONSAFEPATH": "1"}
        assert options["deadline"] == deadline and 0 < options["timeout"] <= 30
        assert options["procutil"] is preparation.agent_procutil
        assert options["construction_fd"] is None
        if phase == "timeout":
            raise subprocess.TimeoutExpired(command, options["timeout"])
        late[0] = phase == "late"
        receipt = dict(expected)
        if phase == "portfolio":
            receipt.pop("agent-bridge")
        if phase == "path":
            receipt["agent-bridge"] = str(tmp_path / "foreign")
        if phase == "outside":
            receipt["agent-bridge"] = str(tmp_path / "other-root" / "previous" / "profiles" / "agent-bridge" / "snapshot")
        if phase == "mixed":
            receipt["agent-bridge"] = str(tmp_path / "previous" / "profiles" / "agent-bridge" / "snapshot")
        return SimpleNamespace(stdout=json.dumps(receipt))

    monkeypatch.setattr(preparation.shutil, "which", lambda name: "located-uv" if name == "uv" else None)
    monkeypatch.setattr(preparation, "_runtime_installer", lambda: SimpleNamespace(engine=SimpleNamespace(
        _select_uv_executable=lambda value: "selected-uv",
        _native_retained_path=lambda path: str(path), run_owned=run,
    )))
    options = dict(
        deadline=deadline, environment={"FEED_SETTING": "retained", "PYTHONPATH": "discard"}, extras=extras,
    )
    if phase in {"complete", "reuse"}:
        assert preparation.prepare_applications(tool, resolved, names, output, **options) == {
            name: Path(path) for name, path in expected.items()
        }
    else:
        message = {
            "portfolio": "different portfolio", "path": "different snapshot",
            "outside": "different snapshot", "mixed": "different preparations",
            "timeout": "preparation failed", "late": "over-budget receipt",
        }[phase]
        with pytest.raises(ValueError, match=message):
            preparation.prepare_applications(tool, resolved, names, output, **options)
    assert len(calls) == 1


@pytest.mark.parametrize("case", ["deadline", "names", "main", "assets", "uv", "extras"])
def test_complete_application_handoff_refuses_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    provider = tmp_path / "local_install_source.py"
    if case != "assets":
        provider.write_text("# trusted provider fixture\n", encoding="utf-8")
    resolved = ResolvedSource(
        config.RuntimeSource("dev", allow_dev=True), tmp_path / "checkout",
        "a" * 40, "b" * 40, "c" * 40, None if case == "main" else "d" * 40,
    )
    monkeypatch.setattr(preparation.shutil, "which", lambda _: None)
    monkeypatch.setattr(preparation, "_runtime_installer", lambda: SimpleNamespace())
    with pytest.raises(ValueError, match={
        "deadline": "live finite deadline", "names": "bounded portfolio", "main": "Main seed",
        "assets": "assets are unavailable", "uv": "uv is required",
        "extras": "extras require",
    }[case]):
        preparation.prepare_applications(
            tmp_path / "prepare_runtime_source.py", resolved,
            ("../escape",) if case == "names" else ("agent-worktrees",),
            tmp_path / "prepared", deadline=0 if case == "deadline" else time.monotonic() + 30,
            extras={"unselected": ()} if case == "extras" else None,
        )


def test_marketplace_uses_no_capability_or_source_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ec, "engine_base_command", lambda: pytest.fail("default path is unchanged"))
    monkeypatch.setattr(update, "acquire", lambda *_: pytest.fail("no source acquisition"))
    arguments = ["--force"]
    assert update.prepare_suite(None, arguments) == update.PreparedSuite(arguments)
    assert update.prepare_suite(None, arguments).arguments is arguments


@pytest.mark.parametrize("capability", [None, {}, {"schema": update.SCHEMA, "version": True},
                                           {"schema": update.SCHEMA, "version": 1}])
def test_unsupported_provider_refused_before_acquisition(
    monkeypatch: pytest.MonkeyPatch, capability: object,
) -> None:
    enable()
    document = inventory("agent-worktrees")
    document["runtime_sources"] = capability
    monkeypatch.setattr(ec, "engine_base_command", lambda: ["exact-provider"])
    monkeypatch.setattr(ec, "run_json", lambda *a, **kw: document)
    monkeypatch.setattr(update, "acquire", lambda *_: pytest.fail("unsupported engine cannot acquire"))
    with pytest.raises(ValueError, match="No update was run"):
        update.prepare_suite(None, [])


@pytest.mark.parametrize(("arguments", "expected"), [
    ([], ["agent-bridge", "agent-worktrees"]),
    (["--skip-modules"], ["agent-worktrees"]),
    (["--skip-modules", "agent-bridge", "--force"], ["agent-worktrees"]),
    (["--skip-modules=agent-bridge"], ["agent-worktrees"]),
])
def test_exact_inventory_eligibility_and_skip_semantics(arguments: list[str], expected: list[str]) -> None:
    document = inventory("agent-worktrees", "agent-bridge", "disabled", "payload-only")
    document["entries"][2]["updatable"] = False
    document["entries"][3]["kind"] = "none"
    assert update._targets(document, arguments) == expected


def test_refuses_second_packet_and_missing_engine() -> None:
    with pytest.raises(ValueError, match="second source packet"):
        update._targets(inventory("agent-worktrees"), ["--runtime-sources", "{}"])
    with pytest.raises(ValueError, match="no eligible engine"):
        update._targets(inventory("agent-bridge"), [])
    with pytest.raises(ValueError, match="Invalid source-backed update"):
        update._targets(inventory("agent-worktrees"), ["--runtime-sources"])
    document = inventory("agent-worktrees")
    document["entries"][0]["kind"] = "unsupported"
    with pytest.raises(ValueError, match="unsupported runtime scope"):
        update._targets(document, [])


@pytest.mark.parametrize("capability", [
    None, {}, {"schema": "copilot-extensions.runtime-application-profile", "version": True},
    {"schema": "copilot-extensions.runtime-application-profile", "version": 2},
    {"schema": "foreign", "version": 1},
    {"schema": "copilot-extensions.runtime-application-profile", "version": 1},
])
def test_required_profile_target_uses_actual_engine_capability(
    tmp_path: Path, capability: dict | None,
) -> None:
    actual = inventory("agent-worktrees")
    value = None if capability is None else {
        **capability, "plugin": "agent-containers", "runtime_root": str(tmp_path),
        "profile": "provider-exec", "extras": ["provider-exec"],
    }
    actual["runtime_sources"]["application_profiles"] = {"agent-containers": {"selection": value}}
    if capability == {"schema": "copilot-extensions.runtime-application-profile", "version": 1} and type(capability["version"]) is int:
        assert update._application_profiles(actual, ["agent-containers"]) == {"agent-containers": value}
    else:
        with pytest.raises(ValueError, match="profile.*unavailable"):
            update._application_profiles(actual, ["agent-containers"])


def test_unprofiled_application_has_no_invented_extras(tmp_path: Path) -> None:
    assert update._application_profiles(inventory("agent-worktrees"), ["agent-worktrees"]) == {
        "agent-worktrees": None,
    }
    with pytest.raises(ValueError, match="profile selection is unavailable"):
        update._application_profiles(inventory("agent-worktrees"), ["agent-containers"])


def test_required_target_refusal_precedes_both_update_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager import update_events

    enable()
    monkeypatch.setattr(ec, "engine_base_command", lambda: ["old-v1-engine"])
    document = inventory("agent-worktrees", "agent-containers")
    document["runtime_sources"]["version"] = 1
    monkeypatch.setattr(ec, "run_json", lambda *args, **kwargs: document)
    monkeypatch.setattr(update, "_preparer", lambda: tmp_path / "trusted-preparer")

    @contextmanager
    def acquire(selection: config.RuntimeSource):
        yield ResolvedSource(selection, tmp_path, "a" * 40, "b" * 40, "a" * 40, "e" * 40)

    monkeypatch.setattr(update, "acquire", acquire)
    monkeypatch.setattr(update, "prepare_applications", lambda *a, **kw: pytest.fail("no build"))
    monkeypatch.setattr(update_events, "_ut", None)
    monkeypatch.setattr(self_install, "self_update", lambda **kwargs: pytest.fail("no self-update"))
    monkeypatch.setattr(ec, "run_engine_passthrough", lambda *args, **kwargs: pytest.fail("no suite"))
    run = update_events.UpdateRun()
    assert update_orchestration.run_update_steps([], run, version="1") == 1
    assert run.finish(1) == 1


@pytest.mark.parametrize("value", [
    {"schema": "foreign", "version": 1, "planner": "scripts/runtime_profile.py"},
    {"schema": "copilot-extensions.runtime-application-profile", "version": True},
    {"error": "owned profile query unavailable"},
])
def test_invalid_source_target_declaration_is_not_an_ignored_optional_field(
    tmp_path: Path, value: dict,
) -> None:
    document = inventory("agent-worktrees")
    document["runtime_sources"]["application_profiles"] = {"agent-containers": {"selection": value}}
    with pytest.raises(ValueError, match="profile.*unavailable"):
        update._application_profiles(document, ["agent-containers"])


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("profiled", [False, True])
def test_one_selection_frozen_provider_and_persistent_packet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scoped: bool, profiled: bool,
) -> None:
    enable()
    calls = []
    command = ["selected-python", "-I", "selected-engine"]
    environment = {"HOME": str(tmp_path), "FIXTURE": "selected"} if scoped else None
    profile = {
        "schema": "copilot-extensions.runtime-application-profile", "version": 1,
        "plugin": "agent-bridge", "runtime_root": str(tmp_path / "bridge"),
        "profile": "fixture-profile", "extras": ["fixture-extra"],
    }
    monkeypatch.setattr(ec, "engine_base_command", lambda: command if not scoped else pytest.fail("stale handle"))
    monkeypatch.setattr(ec, "installed_engine_command", lambda env: command if env is environment else None)

    def read(project: str | None, arguments: list[str], **kwargs: object) -> dict:
        assert project == "example"
        assert arguments == ["update", "--inventory", "--json"]
        assert kwargs == {"engine_command": command, **({"environment": environment} if scoped else {})}
        calls.append("capability")
        document = inventory("agent-worktrees", "agent-bridge")
        if profiled:
            document["runtime_sources"]["application_profiles"] = {
                "agent-bridge": {"selection": profile},
            }
        return document

    @contextmanager
    def acquire(selection: config.RuntimeSource, **kwargs):
        assert kwargs["environment"] is environment
        assert kwargs["deadline"] > time.monotonic()
        calls.append("acquire")
        yield ResolvedSource(selection, tmp_path, "a" * 40, "b" * 40, "a" * 40, "e" * 40)
        calls.append("released")

    def prepare(tool: Path, resolved: ResolvedSource, names: tuple[str, ...], output: Path, **kwargs) -> dict:
        assert calls[0:2] == ["capability", "acquire"]
        calls.append("portfolio")
        assert resolved.commit == "a" * 40
        assert resolved.main_head == "e" * 40
        assert output.parent == tmp_path / "manager/runtime-sources/applications"
        assert names == ("agent-bridge", "agent-worktrees")
        assert kwargs["extras"] == ({"agent-bridge": ("fixture-extra",)} if profiled else {})
        assert kwargs["environment"] is environment
        return {name: output / "profiles" / name / "snapshot" for name in names}

    monkeypatch.setattr(ec, "run_json", read)
    monkeypatch.setattr(update, "acquire", acquire)
    monkeypatch.setattr(update, "_preparer", lambda: tmp_path / "trusted-preparer")
    monkeypatch.setattr(update, "prepare_applications", prepare)
    prepared = update.prepare_suite("example", ["--force"], environment=environment)
    command.append("changed-after-probe")
    assert prepared.engine_command == command[:-1]
    assert calls == ["capability", "acquire", "portfolio", "released"]
    assert prepared.arguments[:2] == ["--force", "--runtime-sources"]
    packet = json.loads(prepared.arguments[2])
    assert packet["schema"] == update.SCHEMA and packet["version"] == 2
    assert set(packet["sources"]) == {"agent-bridge", "agent-worktrees"}
    assert packet["source"] == {"commit": "a" * 40, "tree": "b" * 40, "main_seed": "e" * 40}
    assert packet["sources"]["agent-bridge"]["profile"] == (profile if profiled else None)


def test_orchestration_preparation_failure_precedes_independent_self_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable()
    from worktree_manager import update_events

    monkeypatch.setattr(update_events, "_ut", None)
    monkeypatch.setattr(self_install, "self_update", lambda **kw: pytest.fail("no Manager mutation"))
    monkeypatch.setattr(ec, "run_engine_passthrough", lambda *a, **kw: pytest.fail("no suite mutation"))

    def refuse(*args: object, **kwargs: object) -> None:
        raise ValueError("selected provider unsupported")

    monkeypatch.setattr(update, "prepare_suite", refuse)
    run = update_events.UpdateRun()
    assert update_orchestration.run_update_steps([], run, version="1") == 1
    assert run.finish(1) == 1


def test_orchestration_passes_same_provider_and_source_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager import __main__ as manager, update_events

    enable()
    calls = []
    selected = ["exact", "provider"]
    environment = {"FIXTURE": "selected"}
    monkeypatch.setattr(update_events, "_ut", None)

    def prepare(project: str | None, arguments: list[str], **kwargs: object) -> update.PreparedSuite:
        assert project == "sample" and arguments == ["--force"]
        assert kwargs == {"environment": environment}
        calls.append("prepare")
        return update.PreparedSuite(["--force", "--runtime-sources", "packet"], selected)

    def self_update(**kwargs: object) -> self_install.SelfUpdateResult:
        calls.append("manager")
        assert kwargs == {"dry_run": False}
        return self_install.SelfUpdateResult(action="already-current", version="1")

    def execute(project: str | None, arguments: list[str], **kwargs: object) -> int:
        calls.append("engine")
        assert project == "sample"
        assert arguments == ["update", "--no-manager", "--force", "--runtime-sources", "packet"]
        assert kwargs == {"environment": environment, "engine_command": selected}
        return 17

    monkeypatch.setattr(update, "prepare_suite", prepare)
    monkeypatch.setattr(self_install, "self_update", self_update)
    monkeypatch.setattr(ec, "run_engine_passthrough", execute)
    monkeypatch.setattr("worktree_manager.update_bootstrap.prepare_update", lambda arguments: SimpleNamespace(
        arguments=arguments, fresh_manager=False, exit_code=0, environment=environment,
    ))
    assert manager._cmd_update(["--project", "sample", "--force"]) == 17
    assert calls == ["prepare", "manager", "engine"]


@pytest.mark.parametrize("fault", ["", "exit", "timeout", "commit", "outside", "bytes"])
def test_preparer_process_is_own_isolated_code_and_receipt_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str,
) -> None:
    source = self_install._runtime_installer().runtime_source
    output = tmp_path / "prepared"
    digest = "c" * 64
    version = source.runtime_version("1.0.0", "a" * 40, digest)
    identity = source.SourceIdentity("agent-worktrees", "1.0.0", "a" * 40, "b" * 40, digest, version)
    document = identity.document()
    payload = output / "agent-worktrees" / version
    resolved = ResolvedSource(
        config.RuntimeSource("dev", allow_dev=True), tmp_path / "untrusted",
        "a" * 40, "b" * 40, "a" * 40, "e" * 40,
    )
    tool = tmp_path / "trusted/preparer.py"

    def execute(command: list[str], **kwargs: object) -> SimpleNamespace:
        assert command == [
            sys.executable, "-I", "-B", "-X", "utf8", str(tool),
            "--source", str(resolved.checkout), "--commit", "a" * 40,
            "--branch", "dev", "--plugin", "agent-worktrees", "--output", str(output),
        ]
        assert kwargs["timeout"] == 600 and kwargs["stdin"] == subprocess.DEVNULL
        assert "PYTHONPATH" not in kwargs["env"]
        if fault == "timeout":
            raise subprocess.TimeoutExpired(command, 600)
        if fault == "commit":
            document["source_commit"] = "d" * 40
        return SimpleNamespace(returncode=23 if fault == "exit" else 0, stderr="refused", stdout=json.dumps({
            "payload": str(tmp_path / version if fault == "outside" else payload), "identity": document,
        }))

    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "hostile"))
    monkeypatch.setattr(preparation.subprocess, "run", execute)
    monkeypatch.setattr(source, "read_source_identity", lambda *a, **kw: None if fault == "bytes" else identity)
    if fault:
        with pytest.raises(ValueError):
            preparation.prepare(tool, resolved, "agent-worktrees", output)
    else:
        record = preparation.prepare(tool, resolved, "agent-worktrees", output)
        assert record == {"payload": str(payload), "identity": {"runtime_source": document, "runtime_version": version}}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows extended-length paths")
@pytest.mark.parametrize("location", ["inside", "outside", "traversal"])
def test_preparer_accepts_only_the_same_native_output_parent(monkeypatch, tmp_path, location):
    installer = self_install._runtime_installer()
    source = installer.runtime_source
    output = tmp_path / "prepared"
    digest = "c" * 64
    version = source.runtime_version("1.0.0", "a" * 40, digest)
    identity = source.SourceIdentity("agent-worktrees", "1.0.0", "a" * 40, "b" * 40, digest, version)
    parent = output / "agent-worktrees" if location == "inside" else tmp_path / "other"
    payload = Path(installer.engine._native_retained_path(parent / version))
    if location == "traversal":
        payload = (
            Path(installer.engine._native_retained_path(output))
            / "other" / ".." / "agent-worktrees" / version
        )
    resolved = ResolvedSource(
        config.RuntimeSource("dev", allow_dev=True), tmp_path / "untrusted",
        "a" * 40, "b" * 40, "a" * 40,
    )
    monkeypatch.setattr(
        preparation.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stderr="", stdout=json.dumps({
                "payload": str(payload), "identity": identity.document(),
            }),
        ),
    )
    monkeypatch.setattr(source, "read_source_identity", lambda *args, **kwargs: identity)
    if location == "inside":
        record = preparation.prepare(tmp_path / "tool", resolved, "agent-worktrees", output)
        assert record["payload"] == str(payload)
    else:
        with pytest.raises(ValueError, match="different selection"):
            preparation.prepare(tmp_path / "tool", resolved, "agent-worktrees", output)


def test_preparer_real_process_returns_a_usable_selected_payload(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(checkout), *args], check=True, capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=20,
        ).stdout.strip()

    git("init", "-q", "--initial-branch=dev")
    git("config", "core.autocrlf", "false")
    plugin = checkout / "plugins" / "agent-fixture"
    (plugin / "src").mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"agent-fixture","version":"1.0.0"}')
    (plugin / "pyproject.toml").write_text('[project]\nname="agent-fixture"\nversion="1.0.0"\n')
    (plugin / "src" / "example.py").write_text("value = 1\n")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "Fixture")
    commit, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    git("update-ref", "refs/remotes/origin/dev", commit)
    resolved = ResolvedSource(config.RuntimeSource("dev", allow_dev=True), checkout, commit, tree, commit)
    tool = Path(__file__).resolve().parents[2] / "tools" / "prepare_runtime_source.py"
    output = tmp_path / "prepared"
    first = preparation.prepare(tool, resolved, "agent-fixture", output)
    second = preparation.prepare(tool, resolved, "agent-fixture", output)
    assert first == second
    assert (Path(first["payload"]) / "src" / "example.py").read_text() == "value = 1\n"
    assert first["identity"]["runtime_source"]["source_commit"] == commit


@pytest.mark.parametrize("relative", ["prepare_runtime_source.py", "tools/prepare_runtime_source.py"])
def test_installed_preparer_does_not_treat_wheel_assets_as_a_source_payload(
    tmp_path, monkeypatch, relative,
):
    scripts = tmp_path / "_runtime_assets" / "scripts"
    tool = scripts / "runtime-source-tools" / relative
    tool.parent.mkdir(parents=True)
    tool.write_text("# installed preparer", encoding="utf-8")
    monkeypatch.setattr(preparation, "_runtime_scripts", lambda: scripts)
    monkeypatch.setattr(
        preparation, "running_payload_dir",
        lambda: pytest.fail("installed asset lookup attempted source-payload discovery"),
    )
    assert preparation.preparer() == tool


def test_json_and_passthrough_share_exact_scoped_command_and_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = ["selected", "engine"]
    environment = {"SELECTED": "1", "PYTHONPATH": "parent-must-not-leak"}
    calls = []
    monkeypatch.setattr(ec, "engine_base_command", lambda: pytest.fail("no stale engine handle"))
    monkeypatch.setattr(ec, "installed_engine_command", lambda env: command)
    monkeypatch.setattr("worktree_manager.update_driver_cli.handoff_environment", lambda args: {})

    def execute(argv: list[str], **kwargs: object) -> SimpleNamespace:
        assert argv[:2] == command
        assert kwargs["env"] == {"SELECTED": "1", "PYTHONUTF8": "1", "PYTHONSAFEPATH": "1"}
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout='{"contract": 1}')

    monkeypatch.setattr(ec.subprocess, "run", execute)
    assert ec.run_json(None, ["update", "--inventory", "--json"], environment=environment) == {"contract": 1}
    assert ec.run_engine_passthrough(None, ["update", "--no-manager"], environment=environment, engine_command=command) == 0
    assert len(calls) == 2
