"""Public update request normalization, independent of the updater executor."""

from __future__ import annotations

from contextlib import contextmanager
import pytest
from pathlib import Path
from types import SimpleNamespace

from worktree_manager import update_driver_cli as driver

# Include interpreter, observer, worker startup and participant handoff.
_NATIVE_QUEUE_TIMEOUT = 30


@pytest.fixture
def short_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch):
    from agent_procutil.daemon_instances import REGISTRY_ENV, daemon_test_lifecycle

    # Exercise queue ownership, not the platform's AF_UNIX address-length limit.
    home = tmp_path_factory.mktemp("ud") / "home"
    home.mkdir()
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(home / ".worktree-manager"))
    registry = home.parent / "daemon-instances"
    cleanup = []
    with daemon_test_lifecycle(
        owner="manager-update-cli-tests", registry_root=registry, cleanup_results=cleanup,
    ) as environment:
        monkeypatch.setenv(REGISTRY_ENV, str(registry))
        for key, value in environment.items():
            monkeypatch.setenv(key, value)
        yield home
    assert all(result.status != "held" for result in cleanup), cleanup


def test_default_wait_preserves_canonical_update_arguments():
    flags = ["--force", "--project", "example", "--skip-modules", "agent-bridge"]
    options, forwarded = driver.parse_options(flags)
    assert forwarded == flags
    assert not options.detach
    assert not options.supervised_debug
    assert options.request_status is None


def test_standalone_controller_override_is_forwarded():
    flags = ["--no-project-controller", "--force"]
    _options, forwarded = driver.parse_options(flags)
    assert forwarded == flags


@pytest.mark.parametrize("trigger", [
    ["--automatic-trigger", "manager"],
    ["--automatic-trigger", "legacy-agent-machines"],
    ["--automatic-trigger", "external", "--controller", "example"],
])
def test_automatic_trigger_is_retained_in_durable_request(queued, trigger):
    seen, _request = queued
    flags = [*trigger, "--project", "example"]
    assert driver.cmd_update_request(flags) == 6
    assert seen["spec"].argv == tuple(flags)
    assert seen["runner"].argv[-len(flags) - 1:] == ("update", *flags)


def test_unattended_authentication_hint_is_manager_arguments_not_engine_context(queued, monkeypatch):
    monkeypatch.setenv("CE_UPDATE_UNATTENDED", "1")
    seen, _request = queued
    assert driver.cmd_update_request([]) == 6
    assert "CE_UPDATE_UNATTENDED" not in dict(seen["spec"].context)
    assert seen["spec"].argv == ("--unattended",)
    assert seen["runner"].argv[-1] == "--unattended"
    assert "CE_UPDATE_UNATTENDED" not in dict(seen["runner"].env)
    assert "CE_UPDATE_UNATTENDED" in seen["runner"].context_keys


def test_unattended_execution_sanitizes_the_current_executor_and_restores_it(monkeypatch, tmp_path):
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "AGENT_WORKTREES_AHP_AUTH_TOKEN",
                "GIT_SSH_COMMAND", "GIT_DIR", "GIT_CONFIG_COUNT", "SSH_ASKPASS"):
        monkeypatch.setenv(key, "conflicting-executor")
    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "force")
    monkeypatch.setenv("CE_UPDATE_UNATTENDED", "1")
    original = dict(driver.os.environ)
    context = (("WORKTREE_MANAGER_ROOT", str(tmp_path)), ("WORKTREE_MANAGER_ENGINE_ARGV", '["selected-provider"]'))
    with driver._request_environment(context, unattended=True):
        assert "conflicting-executor" not in driver.os.environ.values()
        assert driver.os.environ["SSH_ASKPASS_REQUIRE"] == "never"
        assert driver.os.environ["CE_UPDATE_UNATTENDED"] == "1"
        assert driver.os.environ["WORKTREE_MANAGER_ENGINE_ARGV"] == '["selected-provider"]'
    assert dict(driver.os.environ) == original
    with driver._request_environment((), unattended=False):
        assert "CE_UPDATE_UNATTENDED" not in driver.os.environ
    assert dict(driver.os.environ) == original


@pytest.mark.parametrize("observer", [[], ["--detach"], ["--supervised-debug"]])
def test_bootstrap_repair_prefix_survives_durable_observer_normalization(observer):
    arguments = ["--repair-engine", "/fresh/source with spaces", "--project", "example"]
    _options, forwarded = driver.parse_options([*observer, *arguments])
    assert forwarded == arguments


def test_bootstrap_repair_cannot_hide_after_ordinary_arguments():
    with pytest.raises(SystemExit):
        driver.parse_options(["--force", "--repair-engine", "/fresh/source"])


def test_detach_json_is_an_observer_option_not_an_installer_argument():
    options, forwarded = driver.parse_options(["--detach", "--json", "--force"])
    assert options.detach and options.json
    assert forwarded == ["--force"]


@pytest.mark.parametrize("flags,repair", [
    ([], True), (["--auto"], False), (["--auto", "--force"], False),
    (["--force"], True), (["--force-runtime", "agent-bridge"], True),
    (["--recreate-venv"], True), (["--repair-engine", "/fresh/source"], True),
])
def test_operator_repair_classification_is_persisted(queued, flags, repair):
    seen, _request = queued
    assert driver.cmd_update_request(flags) == 6
    assert seen["spec"].repair is repair
    assert seen["spec"].argv == tuple(flags)
    if not flags:
        assert "--force" not in seen["runner"].argv


@pytest.mark.parametrize("flags", [
    ["--recover-for-repair", "--force"],
    ["--recover-for-repair", "--auth-report"],
    ["--recover-for-repair", "--detach"],
])
def test_operator_recovery_rejects_unrelated_modes(flags):
    with pytest.raises(SystemExit):
        driver.parse_options(flags)


def test_explicit_operator_recovery_reports_authority_not_native_proof(queued, monkeypatch, capsys):
    import json
    import update_driver

    seen, request = queued
    request.state = "interrupted"
    request.recovery_authority, request.released_at = "operator_repair", 2.0
    request.settled_at = request.settlement_kind = None

    def recover(*, authorized):
        assert authorized is True
        return (request,)

    monkeypatch.setattr(update_driver, "recover_for_repair", recover)
    assert driver.cmd_update_request(["--recover-for-repair", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["replayed"] is False
    assert result["released"][0]["recovery_authority"] == "operator_repair"
    assert result["released"][0]["settlement_kind"] is None
    assert seen == {}


@pytest.mark.parametrize("flags", [
    ["--detach", "--wait"],
    ["--detach", "--supervised-debug"],
    ["--request-status", "one", "--force"],
    ["--wait", "--json"],
    ["--auth-report", "--detach"],
    ["--auth-report", "--supervised-debug"],
    ["--token", "synthetic-unknown-secret"],
    ["--aut"],
    ["--automatic"],
    ["--request-status", "one", "--auto"],
    ["--auth-source", "https://example.invalid/tools.git"],
    ["--request-status", "one", "--engine-acquisition-timeout", "600"],
    ["--recovery-status", "--engine-acquisition-timeout", "600"],
    ["--auth-report", "--engine-acquisition-timeout", "600"],
    ["--automatic-trigger", "unknown"],
    ["--automatic-trigger", "external"],
    ["--controller", "example"],
    ["--automatic-trigger", "manager", "--controller", "example"],
    ["--automatic-trigger", "manager", "--auth-report"],
    ["--request-status", "one", "--unattended"],
    ["--recovery-status", "--unattended"],
    ["--auth-report", "--unattended"],
])
def test_invalid_observer_combinations_refuse(flags):
    with pytest.raises(SystemExit) as error:
        driver.parse_options(flags)
    assert error.value.code == 2


def test_request_context_never_persists_credentials(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "synthetic-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "other-synthetic-secret")
    monkeypatch.setenv("AGENT_WORKTREES_UPDATE_CONTEXT", "example-context")
    context = driver.request_context()
    assert "GH_TOKEN" not in context
    assert "GITHUB_TOKEN" not in context
    assert context["AGENT_WORKTREES_UPDATE_CONTEXT"] == "example-context"


def test_relative_home_is_captured_from_execution_directory_not_source_anchor(
    tmp_path, monkeypatch,
):
    from worktree_manager.source_config import SOURCE_ANCHOR_ENV

    caller, anchor = tmp_path / "caller", tmp_path / "anchor"
    caller.mkdir()
    anchor.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("AGENT_HOME", "state")
    monkeypatch.setenv(SOURCE_ANCHOR_ENV, str(anchor))
    context = driver.request_context()
    assert context["AGENT_HOME"] == str(caller / "state")
    assert driver.request_source_anchor() == str(anchor)
    monkeypatch.chdir(anchor)
    with driver._request_environment(context, cwd=str(anchor)):
        assert driver.request_context()["AGENT_HOME"] == str(caller / "state")


def test_cold_bootstrap_context_requires_no_uninstalled_manager_dependencies():
    import os
    import subprocess
    import sys

    program = """
import json,os,sys
sys.path.insert(0,sys.argv[1])
from worktree_manager.update_driver_cli import request_context
assert 'worktree_manager.engine_client' not in sys.modules
os.environ['WORKTREE_MANAGER_ENGINE_ARGV']=json.dumps(['selected-engine'])
assert json.loads(request_context()['WORKTREE_MANAGER_ENGINE_ARGV'])==['selected-engine']
assert 'worktree_manager.engine_client' not in sys.modules
assert 'worktree_identity' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", program,
         str(Path(__file__).resolve().parents[1] / "src")],
        capture_output=True, text=True, timeout=15,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_queue_captures_consumed_provider_without_restoring_ambient_transport(queued, monkeypatch):
    import json
    from worktree_manager import engine_client

    provider = ["synthetic-selected-provider", "--provider-option"]
    monkeypatch.setattr(engine_client, "_INHERITED_ENGINE_COMMAND", provider)
    monkeypatch.delenv(engine_client.ENGINE_ARGV_ENV, raising=False)
    seen, _request = queued
    assert driver.cmd_update_request([]) == 6
    assert json.loads(dict(seen["spec"].context)[engine_client.ENGINE_ARGV_ENV]) == provider
    assert engine_client.ENGINE_ARGV_ENV in seen["runner"].context_keys
    assert engine_client.ENGINE_ARGV_ENV not in driver.os.environ


@pytest.mark.parametrize("flags", [[], ["--detach"]], ids=["wait", "detach"])
@pytest.mark.parametrize("context", ["", "relative", "missing", "file"])
def test_invalid_context_refuses_before_submission(queued, monkeypatch, tmp_path, capsys, flags, context):
    seen, _request = queued
    path = tmp_path / context
    if context == "file":
        path.write_text("not a directory")
    value = context if context in {"", "relative"} else str(path)
    monkeypatch.setenv("AGENT_WORKTREES_UPDATE_CONTEXT", value)

    assert driver.cmd_update_request(flags) == 1
    assert seen == {}
    assert "existing absolute directory" in capsys.readouterr().err


def test_source_identity_preserves_explicit_selection(tmp_path, monkeypatch):
    from worktree_manager.runtime_source_config import RuntimeSource
    from worktree_manager import self_install

    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path)
    monkeypatch.setattr(self_install, "manager_repo", lambda root: "https://example.invalid/tools.git")
    monkeypatch.setattr(self_install, "manager_ref", lambda root: "0123456789abcdef")
    assert driver.source_identity() == {
        "root": str(tmp_path.resolve()),
        "repo": "https://example.invalid/tools.git",
        "ref": "0123456789abcdef",
        "runtime_source": RuntimeSource().document(),
    }


def test_relative_source_is_anchored_before_worker_changes_directory(tmp_path, monkeypatch):
    from worktree_manager import self_install

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path / "manager")
    monkeypatch.setattr(self_install, "manager_repo", lambda root: "selected-source")
    monkeypatch.setattr(self_install, "manager_ref", lambda root: "canary")
    identity = driver.source_identity()
    assert identity["repo"] == str(tmp_path / "selected-source")
    assert identity["ref"] == "canary"


def test_installed_cli_retains_inherited_source_anchor_separately_from_runner_cwd(
    tmp_path, monkeypatch,
):
    import update_driver
    from update_driver.authority import UpdateAuthority
    from worktree_manager import engine_client, source_config

    root, anchor, caller, worker = (tmp_path / name for name in (
        "manager", "source-anchor", "caller", "worker",
    ))
    for directory in (root, anchor, caller, worker):
        directory.mkdir()
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    monkeypatch.setenv(source_config.SOURCE_ANCHOR_ENV, str(anchor))
    source_config.set_source(repo="selected-source", ref="dev", root=root)
    monkeypatch.chdir(caller)
    admitted = driver._source_key()
    seen = {}

    def enqueue(spec, *, runner):
        seen.update(spec=spec, runner=runner)
        return SimpleNamespace(
            id="anchored-request", state="pending", exit_code=None, error=None,
            log_path=tmp_path / "update.log",
        )

    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(update_driver, "current_authority", lambda **_kwargs: UpdateAuthority())
    monkeypatch.setattr(update_driver, "enqueue", enqueue)
    monkeypatch.setattr(update_driver, "runtime_command", lambda module, args=(): (
        ("synthetic-python", "-m", module, *args), {},
    ))
    monkeypatch.setattr(update_driver, "ensure_worker", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine_client, "get_value", lambda *_args: "")
    assert driver.cmd_update_request(["--detach"]) == 0
    spec, runner = seen["spec"], seen["runner"]
    assert spec.cwd == str(anchor) and runner.cwd == str(caller)
    assert spec.source == admitted
    monkeypatch.chdir(worker)
    monkeypatch.setenv(source_config.SOURCE_ANCHOR_ENV, str(worker))
    with driver._request_environment(spec.context, cwd=spec.cwd):
        assert driver._source_key() == admitted
        assert driver.source_identity()["repo"] == str(anchor / "selected-source")
        source_config.set_source(repo="changed-source", root=root)
        assert driver._source_key() != admitted
    assert driver.os.environ[source_config.SOURCE_ANCHOR_ENV] == str(worker)


def test_installed_cli_refuses_relative_inherited_source_anchor_before_enqueue(queued, monkeypatch):
    from worktree_manager.source_config import SOURCE_ANCHOR_ENV

    seen, _request = queued
    monkeypatch.setenv(SOURCE_ANCHOR_ENV, "relative-anchor")
    assert driver.cmd_update_request(["--detach"]) == 1
    assert seen == {}


def test_credential_bearing_source_is_not_written_to_queue(tmp_path, monkeypatch):
    from worktree_manager import self_install

    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path)
    monkeypatch.setattr(self_install, "manager_repo", lambda root: "https://user:synthetic@example.invalid/tools.git")
    with pytest.raises(ValueError, match="cannot be journaled"):
        driver.source_identity()


@pytest.fixture
def queued(monkeypatch, tmp_path):
    import update_driver
    from worktree_manager import __main__ as manager
    from worktree_manager import engine_client
    from update_driver.authority import UpdateAuthority

    seen = {}
    request = SimpleNamespace(
        id="synthetic-request", state="pending", exit_code=None, error=None,
        log_path=tmp_path / "update.log",
        runner=SimpleNamespace(env=()),
    )

    def enqueue(spec, *, runner):
        seen["spec"], seen["runner"] = spec, runner
        request.spec = spec
        request.runner = runner
        return request

    def wait(request_id, **kwargs):
        seen["wait"] = request_id
        return SimpleNamespace(**{**vars(request), "state": "completed", "exit_code": 6})

    def forbidden(*args, **kwargs):
        pytest.fail("ordinary caller executed the installer")

    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(update_driver, "current_authority", lambda **_kwargs: UpdateAuthority())
    monkeypatch.setattr(update_driver, "enqueue", enqueue)
    monkeypatch.setattr(update_driver, "runtime_command", lambda module, args=(): (
        (str(tmp_path / "python.exe"), "-m", module, *args), {},
    ))
    monkeypatch.setattr(update_driver, "ensure_worker", lambda *args, **kwargs: seen.update(worker=args))
    monkeypatch.setattr(update_driver, "wait", wait)
    monkeypatch.setattr(driver, "source_identity", lambda: {"repo": "synthetic-source", "ref": "fixed"})
    monkeypatch.setattr(engine_client, "get_value", lambda *_args: "")
    monkeypatch.setattr(manager, "_cmd_update", forbidden)
    return seen, request


def test_standalone_authority_skips_controller_before_manager_enqueue(queued, monkeypatch):
    import update_driver
    from update_driver.authority import selection
    from worktree_manager import engine_client

    seen, _request = queued
    monkeypatch.setattr(update_driver, "current_authority", lambda **_kwargs: selection("standalone"))
    monkeypatch.setattr(
        engine_client, "get_value",
        lambda *_args: pytest.fail("standalone authority probed a project controller"),
    )
    assert driver.cmd_update_request(["--project", "example"]) == 6
    assert "--no-project-controller" in seen["spec"].argv
    assert "--no-project-controller" in seen["runner"].argv


def test_public_submission_and_status_report_superseded_request(queued, monkeypatch, tmp_path, capsys):
    import json
    import update_driver
    from update_driver import core

    monkeypatch.setattr(core, "default_root", lambda: tmp_path / "queue")
    monkeypatch.setattr(update_driver, "enqueue", core.enqueue)
    monkeypatch.setattr(update_driver, "status", core.status)
    assert driver.cmd_update_request(["--detach", "--json", "--force"]) == 0
    first = json.loads(capsys.readouterr().out)["request_id"]
    assert driver.cmd_update_request(["--detach", "--json", "--no-manager"]) == 0
    second = json.loads(capsys.readouterr().out)["request_id"]
    assert first != second
    assert driver.cmd_update_request(["--request-status", first, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "completed" and result["exit_code"] == 125
    assert second in result["error"] and "nothing ran" in result["error"]
    assert core.status(second).state == "pending"


def test_downlevel_driver_refuses_cleanly_without_queueing(queued, monkeypatch, capsys):
    import update_driver

    seen, _request = queued
    monkeypatch.delattr(update_driver, "current_authority")
    assert driver.cmd_update_request([]) == 1
    assert "spec" not in seen
    assert "lacks authority support" in capsys.readouterr().err


def test_project_authority_hands_manager_updates_to_its_selected_project(queued, monkeypatch):
    import update_driver
    from update_driver.authority import selection
    from worktree_manager import engine_client

    seen, _request = queued
    calls = []
    monkeypatch.setattr(
        update_driver, "current_authority", lambda **_kwargs: selection("project", "harness"),
    )
    monkeypatch.setattr(
        engine_client, "get_value",
        lambda project, key: "tools/update.py" if (project, key) == (
            "harness", "update-controller",
        ) else pytest.fail("wrong project controller probed"),
    )
    monkeypatch.setattr(
        engine_client, "run_engine_passthrough",
        lambda project, args, **kwargs: calls.append((project, args, kwargs)) or 47,
    )
    assert driver.cmd_update_request(["--project", "different", "--force"]) == 47
    assert calls == [("harness", ["update", "--force"], {})]
    assert "spec" not in seen


@pytest.mark.parametrize("flags", [[], ["--no-project-controller"], ["--target", "worktree-manager"]])
def test_project_authority_never_falls_back_to_manager(queued, monkeypatch, flags):
    import update_driver
    from update_driver.authority import selection
    from worktree_manager import engine_client

    seen, _request = queued
    monkeypatch.setattr(
        update_driver, "current_authority", lambda **_kwargs: selection("project", "harness"),
    )
    monkeypatch.setattr(engine_client, "get_value", lambda *_args: "")
    assert driver.cmd_update_request(flags) == 1
    assert "spec" not in seen


@pytest.mark.parametrize("observer", [
    [], ["--detach", "--json"], ["--wait"], ["--supervised-debug"],
])
@pytest.mark.parametrize("project", [["--project", "example"], ["--project=example"]])
def test_project_controller_routes_before_manager_source(queued, monkeypatch, observer, project):
    from worktree_manager import engine_client

    calls = []
    monkeypatch.setattr(
        engine_client, "get_value",
        lambda project, key: "tools/update.py" if (project, key) == (
            "example", "update-controller",
        ) else pytest.fail("unexpected controller probe"),
    )
    monkeypatch.setattr(
        engine_client, "run_engine_passthrough",
        lambda project, args, **kwargs: calls.append((project, args, kwargs)) or 47,
    )
    monkeypatch.setattr(driver, "_source_key", lambda: pytest.fail("Manager source queried"))
    assert driver.cmd_update_request([*project, "--force", *observer]) == 47
    assert calls == [(
        "example", ["update", "--force", *observer],
        {"headless": True} if "--detach" in observer else {},
    )]


@pytest.mark.parametrize("root_form", ["default", "relative"])
def test_project_controller_handoff_freezes_manager_policy_root(
    queued, monkeypatch, tmp_path, root_form,
):
    import os
    from update_driver import RequestSpec
    from update_driver.core import automatic_update_refusal
    from worktree_manager import engine_client

    home = tmp_path / "home"
    caller = tmp_path / "caller"
    home.mkdir()
    caller.mkdir()
    root = home / ".worktree-manager" if root_form == "default" else caller / "manager"
    root.mkdir()
    (root / "config.toml").write_text(
        "[maintenance]\nautomatic = false\n", encoding="utf-8",
    )
    monkeypatch.chdir(caller)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "cell.json"))
    if root_form == "default":
        monkeypatch.delenv("WORKTREE_MANAGER_ROOT", raising=False)
    else:
        monkeypatch.setenv("WORKTREE_MANAGER_ROOT", "manager")
    seen = []
    monkeypatch.setattr(engine_client, "get_value", lambda *_args: "tools/update.py")

    def execute(_project, arguments, **_kwargs):
        context = {
            "WORKTREE_MANAGER_ROOT": os.environ.get("WORKTREE_MANAGER_ROOT", ""),
            "COPILOT_EXTENSIONS_CONTEXT": os.environ["COPILOT_EXTENSIONS_CONTEXT"],
        }
        request = RequestSpec(
            backend="engine", argv=tuple(arguments[1:]), cwd=str(caller),
            context=tuple(context.items()),
        )
        seen.append((context, automatic_update_refusal(request)))
        return 0

    monkeypatch.setattr(engine_client, "run_engine_passthrough", execute)
    assert driver.cmd_update_request(["--project", "example", "--auto"]) == 0
    assert seen[0][0]["WORKTREE_MANAGER_ROOT"] == str(root.resolve())
    assert "maintenance.automatic is false" in seen[0][1]
    assert os.environ.get("WORKTREE_MANAGER_ROOT") == (
        None if root_form == "default" else "manager"
    )


def test_project_controller_preserves_explicit_manager_skip(queued, monkeypatch):
    from worktree_manager import engine_client

    calls = []
    monkeypatch.setattr(engine_client, "get_value", lambda *_args: "tools/update.py")
    monkeypatch.setattr(
        engine_client, "run_engine_passthrough",
        lambda project, args, **kwargs: calls.append((project, args)) or 0,
    )
    assert driver.cmd_update_request(["--project", "example", "--no-manager", "--force"]) == 0
    assert calls == [("example", ["update", "--no-manager", "--force"])]


@pytest.mark.parametrize("project", [None, "missing"])
def test_neutral_controller_probe_does_not_block_manager_update(queued, monkeypatch, project):
    from worktree_manager import engine_client, project_update_controller

    def missing_project(*_args):
        raise project_update_controller._NoProjectResolved("Could not resolve a project for 'get'.")

    seen, _request = queued
    monkeypatch.setattr(engine_client, "get_value", missing_project)
    flags = ["--project", project] if project else []
    assert driver.cmd_update_request(flags) == (1 if project else 6)
    assert ("spec" in seen) is (project is None)


def test_project_controller_routes_borrowed_request_before_manager_source(
    queued, monkeypatch, tmp_path,
):
    import update_driver
    from worktree_manager import engine_client

    class Custody:
        request_id = "synthetic-request"
        root = tmp_path

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    request = SimpleNamespace(
        state="running",
        spec=SimpleNamespace(
            backend="manager", context=(), killswitch_path=None, cwd=str(tmp_path),
        ),
    )
    calls = []
    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: Custody())
    monkeypatch.setattr(update_driver, "status", lambda *_args, **_kwargs: request)
    monkeypatch.setattr(engine_client, "get_value", lambda *_args: "tools/update.py")
    monkeypatch.setattr(
        engine_client, "run_engine_passthrough",
        lambda project, args, **kwargs: calls.append((project, args)) or 47,
    )
    monkeypatch.setattr(driver, "_source_key", lambda: pytest.fail("Manager source queried"))
    assert driver.cmd_update_request(["--project", "example", "--force"]) == 47
    assert calls == [("example", ["update", "--force"])]


def test_legacy_project_request_reaches_manager_controller_under_real_custody(tmp_path, monkeypatch):
    import sqlite3
    import sys
    import update_driver
    from worktree_manager import engine_client

    root = tmp_path.resolve() / "driver"
    update_driver.change_authority(
        "project", project="example", root=root, timeout=5,
        verify_capabilities=lambda _deadline: True,
    )
    request = update_driver.enqueue(
        update_driver.RequestSpec(
            "manager", project="example", cwd=str(tmp_path.resolve()), authority="project",
        ),
        runner=update_driver.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path.resolve())),
        root=root,
    )
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute("PRAGMA user_version=5")
    calls = []
    monkeypatch.setattr(engine_client, "get_value", lambda *_args: "tools/update.py")
    monkeypatch.setattr(
        engine_client, "run_engine_passthrough",
        lambda project, args, **_kwargs: calls.append((project, args)) or 0,
    )
    borrow = update_driver.borrowed_custody

    def execute(transaction):
        monkeypatch.setattr(
            update_driver, "borrowed_custody",
            lambda: borrow(root=root, environ=transaction.participant_env()),
        )
        return driver.cmd_update_request(["--project", "example", "--force"])

    assert update_driver.drain(root=root, executor=execute) == 0
    assert update_driver.status(request.id, root=root).exit_code == 0
    assert calls == [("example", ["update", "--force"])]


def test_borrowed_standalone_authority_stays_standalone(queued, monkeypatch, tmp_path):
    import update_driver
    from update_driver.authority import selection
    from worktree_manager import __main__ as manager, engine_client

    class Custody:
        request_id = "synthetic-request"
        root = tmp_path

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def assert_owned(self):
            pass

    request = SimpleNamespace(
        state="running",
        spec=update_driver.RequestSpec(
            "manager", project="example", cwd=str(tmp_path), source="selected",
            authority="standalone",
        ),
    )
    calls = []
    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: Custody())
    monkeypatch.setattr(update_driver, "status", lambda *_args, **_kwargs: request)
    monkeypatch.setattr(update_driver, "current_authority", lambda **_kwargs: selection("standalone"))
    monkeypatch.setattr(
        engine_client, "get_value",
        lambda *_args: pytest.fail("standalone authority probed a project controller"),
    )
    monkeypatch.setattr(driver, "_source_key", lambda: "selected")
    monkeypatch.setattr(manager, "_cmd_update", lambda flags: calls.append(flags) or 8)
    assert driver.cmd_update_request(["--project", "example"]) == 8
    assert "--no-project-controller" in calls[0]


@pytest.mark.parametrize(("parent_flags", "child_flags", "expected"), [
    ((), ("--no-project-controller",), 8),
    ((), ("--target", "agent-bridge"), 8),
    ((), ("--target=agent-bridge",), 8),
    (("--no-project-controller",), ("--target", "agent-bridge"), 1),
    (("--target", "agent-bridge"), ("--target", "agent-bridge"), 1),
])
def test_borrowed_project_authority_allows_controller_delegation(
    queued, monkeypatch, tmp_path, parent_flags, child_flags, expected,
):
    import update_driver
    from update_driver.authority import selection
    from worktree_manager import __main__ as manager, engine_client

    class Custody:
        request_id = "synthetic-request"
        root = tmp_path

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def assert_owned(self):
            pass

    request = SimpleNamespace(
        state="running",
        spec=update_driver.RequestSpec(
            "engine", parent_flags, project="example", cwd=str(tmp_path), authority="project",
        ),
    )
    calls = []
    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: Custody())
    monkeypatch.setattr(update_driver, "status", lambda *_args, **_kwargs: request)
    monkeypatch.setattr(
        update_driver, "current_authority", lambda **_kwargs: selection("project", "example"),
    )
    monkeypatch.setattr(
        engine_client, "get_value",
        lambda *_args: pytest.fail("delegated bypass re-entered the controller"),
    )
    monkeypatch.setattr(manager, "_cmd_update", lambda flags: calls.append(flags) or 8)
    assert driver.cmd_update_request(["--project", "example", *child_flags]) == expected
    if expected == 8:
        assert all(flag in calls[0] for flag in child_flags)
    else:
        assert not calls


def test_ordinary_update_only_enqueues_and_observes_canonical_exit(queued):
    seen, request = queued
    assert driver.cmd_update_request(["--force", "--project", "example"]) == 6
    assert seen["spec"].argv == ("--force", "--project", "example")
    assert seen["spec"].project == "example"
    assert seen["runner"].argv[-4:] == ("update", "--force", "--project", "example")
    assert seen["wait"] == request.id
    assert "worker" in seen


@pytest.mark.parametrize("root_form", ["absolute", "relative", "home"])
@pytest.mark.parametrize("enabled", [False, True])
def test_submission_keeps_selected_policy_for_automatic_and_manual_updates(
    queued, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, root_form: str, enabled: bool,
) -> None:
    import update_driver
    from update_driver.core import automatic_update_refusal

    seen, _request = queued
    caller = tmp_path / "caller"
    home = tmp_path / "home"
    caller.mkdir()
    home.mkdir()
    root = home / "manager" if root_form == "home" else caller / "manager"
    root.mkdir()
    (root / "config.toml").write_text(
        f"[maintenance]\nautomatic = {str(enabled).lower()}\n", encoding="utf-8",
    )
    selected = {"absolute": str(root), "relative": "manager", "home": "~/manager"}
    monkeypatch.chdir(caller)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", selected[root_form])
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(tmp_path / "absent-hold.json"))
    enqueue = update_driver.enqueue

    def admit(spec: update_driver.RequestSpec, *, runner: update_driver.TrustedRunner):
        assert dict(spec.context)["WORKTREE_MANAGER_ROOT"] == str(root.resolve())
        assert spec.cwd == str(caller.resolve())
        if reason := automatic_update_refusal(spec):
            raise update_driver.StateError(reason)
        return enqueue(spec, runner=runner)

    monkeypatch.setattr(update_driver, "enqueue", admit)
    assert driver.cmd_update_request(["--auto"]) == (6 if enabled else 1)
    assert ("spec" in seen) is enabled
    assert driver.cmd_update_request([]) == 6
    assert seen["spec"].argv == ()
    assert driver.os.environ["WORKTREE_MANAGER_ROOT"] == selected[root_form]
    assert not (caller / "~").exists()


def test_source_identity_expands_home_before_freezing_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager import self_install

    home, caller = tmp_path / "home", tmp_path / "caller"
    home.mkdir()
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", "~/manager")
    selected = home / "manager"
    roots = []

    def repo(root: Path) -> str:
        roots.append(root)
        return "https://example.invalid/tools.git"

    monkeypatch.setattr(self_install, "manager_repo", repo)
    monkeypatch.setattr(self_install, "manager_ref", lambda root: "fixed")
    assert driver.source_identity()["root"] == str(selected.resolve())
    assert roots == [selected.resolve()]
    assert driver.request_context()["WORKTREE_MANAGER_ROOT"] == str(selected.resolve())
    assert not list(home.iterdir())
    assert not list(caller.iterdir())


@pytest.mark.parametrize("configured", [False, True])
def test_self_install_request_uses_app_slot_workspace_and_retains_finite_owner(
    queued, monkeypatch, tmp_path, configured,
):
    import update_driver
    from update_driver.authority import selection
    from worktree_manager import runtime_identity, self_install

    seen, _request = queued
    slot = tmp_path / "root" / "versions" / "1.2.3"
    slot.mkdir(parents=True)

    @contextmanager
    def no_custody():
        yield None

    monkeypatch.setattr(
        self_install, "_runtime_installer", lambda: SimpleNamespace(_installation_custody=no_custody),
    )
    monkeypatch.setattr(runtime_identity, "running_application_slot", lambda: slot)
    monkeypatch.setattr(self_install, "default_root", lambda: slot.parent.parent)
    monkeypatch.setattr(
        self_install, "running_payload_dir",
        lambda: pytest.fail("app-only installation cannot select a source workspace"),
    )
    monkeypatch.setattr(update_driver, "drain", lambda: seen.update(drained=True))
    if configured:
        monkeypatch.setattr(update_driver, "current_authority", lambda **_kwargs: selection("standalone"))
    assert driver.self_install_request(lambda: pytest.fail("observer executed install")) == 6
    assert seen["spec"].cwd == str(slot) and seen["runner"].cwd == str(slot)
    assert seen["spec"].project == str(slot.parent.parent)
    assert dict(seen["spec"].context)["WORKTREE_MANAGER_ROOT"] == str(slot.parent.parent)
    assert seen["runner"].argv[-2:] == ("self-install", "--apply")
    assert seen["spec"].authority == ("standalone" if configured else "")
    assert seen["drained"]


def test_self_install_reports_project_authority_refusal(queued, monkeypatch, tmp_path, capsys):
    import update_driver
    from update_driver.authority import selection
    from worktree_manager import runtime_identity, self_install

    @contextmanager
    def no_custody():
        yield None

    def refuse(*_args, **_kwargs):
        raise update_driver.StateError("authority refusal")

    monkeypatch.setattr(
        self_install, "_runtime_installer", lambda: SimpleNamespace(_installation_custody=no_custody),
    )
    monkeypatch.setattr(runtime_identity, "running_application_slot", lambda: tmp_path)
    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path)
    monkeypatch.setattr(update_driver, "current_authority", lambda **_kwargs: selection("project", "example"))
    monkeypatch.setattr(update_driver, "enqueue", refuse)
    assert driver.self_install_request(lambda: pytest.fail("unowned installation ran")) == 1
    assert "authority refusal" in capsys.readouterr().err


@pytest.mark.parametrize("flags", [
    ["--target", "agent-bridge"],
    ["--target", "agent-bridge", "--automatic-trigger", "manager"],
    ["--target", "agent-bridge", "--automatic-trigger", "external", "--controller", "example"],
    ["--target", "agent-bridge", "--auto"],
])
def test_public_component_update_queues_manager_admission_before_engine_dispatch(queued, monkeypatch, flags):
    from worktree_manager import engine_client

    monkeypatch.setattr(
        engine_client, "run_engine_passthrough",
        lambda *a, **k: pytest.fail("targeted caller bypassed Manager admission"),
    )
    seen, _request = queued
    assert driver.cmd_update_request(flags) == 6
    automatic = "--auto" in flags or "--automatic-trigger" in flags
    expected = [*flags, "--auto"] if automatic and "--auto" not in flags else flags
    assert seen["spec"].backend == "manager" and seen["spec"].argv == tuple(expected)
    assert seen["spec"].repair is not automatic
    if "--automatic-trigger" in flags:
        assert "--unattended" not in seen["spec"].argv
        assert "CE_UPDATE_UNATTENDED" not in dict(seen["spec"].context)


def test_fresh_repair_is_queued_without_running_in_the_observer(queued, monkeypatch):
    seen, _request = queued
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", "/registered/native/payload")
    arguments = ["--repair-engine", "/fresh/source with spaces", "--project", "example"]
    assert driver.cmd_update_request(arguments) == 6
    assert seen["spec"].argv == tuple(arguments)
    assert dict(seen["spec"].context)["AGENT_WORKTREES_PAYLOAD_ROOT"] == (
        "/registered/native/payload"
    )
    assert seen["runner"].argv[-len(arguments) - 1:] == ("update", *arguments)


def test_engine_acquisition_budget_is_captured_by_the_durable_request(queued, monkeypatch):
    seen, _request = queued
    key = "WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC"
    monkeypatch.setenv(key, "600")
    assert driver.cmd_update_request([]) == 6
    assert key not in dict(seen["spec"].context)
    assert key not in seen["runner"].context_keys
    assert seen["spec"].argv == ("--engine-acquisition-timeout", "600.0")
    options, forwarded = driver.parse_options(list(seen["spec"].argv))
    assert forwarded == []
    monkeypatch.setenv(key, "180")
    with driver._request_environment(seen["spec"].context), driver._acquisition_environment(
        options.engine_acquisition_timeout,
    ):
        assert driver.os.environ[key] == "600.0"
    assert driver.os.environ[key] == "180"
    with driver._acquisition_environment(None):
        assert key not in driver.os.environ
    assert driver.os.environ[key] == "180"


def test_manager_budget_is_consumed_before_engine_argument_forwarding():
    options, forwarded = driver.parse_options([
        "--repair-engine", "/fresh/source", "--engine-acquisition-timeout", "600",
        "--no-anchor-sync",
    ])
    assert options.engine_acquisition_timeout == "600"
    assert forwarded == ["--repair-engine", "/fresh/source", "--no-anchor-sync"]


def test_invalid_engine_acquisition_budget_is_not_enqueued(queued, monkeypatch):
    seen, _request = queued
    monkeypatch.setenv("WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC", "inf")
    assert driver.cmd_update_request([]) == 1
    assert "spec" not in seen


def test_borrowed_execution_passes_repair_to_the_canonical_body(queued, monkeypatch, tmp_path):
    import update_driver
    from worktree_manager import __main__ as manager

    seen, request = queued
    request.state = "running"
    request.spec = SimpleNamespace(
        backend="manager", source=driver._source_key(),
        context=(("AGENT_WORKTREES_PAYLOAD_ROOT", "/registered/native/payload"),),
        killswitch_path="", cwd=str(tmp_path), argv=(),
    )

    class Custody:
        root = tmp_path
        request_id = request.id

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def assert_owned(self):
            seen["custody"] = True

    monkeypatch.setattr(update_driver, "borrowed_custody", Custody)
    monkeypatch.setattr(update_driver, "status", lambda *args, **kwargs: request)
    monkeypatch.setattr(
        manager, "_cmd_update", lambda args: seen.update(body=args) or 7,
    )
    arguments = ["--repair-engine", "/fresh/source with spaces", "--force"]
    assert driver.cmd_update_request(arguments) == 7
    assert seen["custody"] is True and seen["body"] == arguments
    assert "spec" not in seen


@pytest.mark.parametrize("backend", ["manager", "engine"])
@pytest.mark.parametrize("root_form", ["absolute", "relative", "home"])
def test_borrowed_execution_rechecks_each_requests_switch_and_restores_worker(
    queued, monkeypatch, tmp_path, backend, root_form,
):
    import update_driver
    from worktree_manager import __main__ as manager, maintenance_policy

    seen, request = queued
    request.state = "running"
    caller = tmp_path / "caller"
    policy_root = caller / "manager"
    policy_root.mkdir(parents=True)
    (policy_root / "config.toml").write_text("[maintenance]\nautomatic = true\n", encoding="utf-8")
    roots = {"absolute": str(policy_root), "relative": "manager", "home": "~/manager"}
    context = (("WORKTREE_MANAGER_ROOT", roots[root_form]),)
    worker = tmp_path / "worker"
    (worker / "manager").mkdir(parents=True)
    (worker / "manager/config.toml").write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    monkeypatch.chdir(worker)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", "worker-original")
    held, allowed = tmp_path / "held.json", tmp_path / "allowed.json"
    held.write_text('{"active":true}', encoding="utf-8")
    allowed.write_text('{"active":false}', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: caller)
    monkeypatch.setenv("HOME", str(caller))
    monkeypatch.setenv("USERPROFILE", str(caller))
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(held))
    monkeypatch.setenv("GH_TOKEN", "fixture-session-credential")
    monkeypatch.setenv("GIT_SSH_COMMAND", "fixture-worker-transport")
    monkeypatch.setenv("SSH_ASKPASS", "fixture-worker-helper")

    class Custody:
        root = tmp_path
        request_id = request.id

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def assert_owned(self):
            seen["custody"] = True

    monkeypatch.setattr(update_driver, "borrowed_custody", Custody)
    monkeypatch.setattr(update_driver, "status", lambda *args, **kwargs: request)
    def execute(arguments):
        unattended = backend == "manager" and "--auto" in arguments
        assert ("GH_TOKEN" not in driver.os.environ) is unattended
        assert ("GIT_SSH_COMMAND" not in driver.os.environ) is unattended
        assert ("SSH_ASKPASS" not in driver.os.environ) is unattended
        if unattended:
            assert driver.os.environ["SSH_ASKPASS_REQUIRE"] == "never"
        return 3 if maintenance_policy.update_refusal(arguments) else 0

    monkeypatch.setattr(manager, "_cmd_update", execute)
    for selected, arguments, expected in (
        (str(allowed), ["--auto"], 0),
        (str(held), ["--auto"], 3),
        ("", ["--auto"], 0),
        (str(held), [], 0),
    ):
        request.spec = update_driver.RequestSpec(
            backend, tuple(arguments), source=driver._source_key(),
            context=context, killswitch_path=selected, cwd=str(caller),
        )
        from update_driver.core import automatic_update_refusal

        assert (automatic_update_refusal(request.spec) is not None) is (expected == 3)
        assert driver.cmd_update_request(arguments) == expected
        assert driver.os.environ["BOOTSTRAP_KILLSWITCH_STATE_FILE"] == str(held)
        assert driver.os.environ["WORKTREE_MANAGER_ROOT"] == "worker-original"
        assert driver.os.environ["GH_TOKEN"] == "fixture-session-credential"
        assert driver.os.environ["GIT_SSH_COMMAND"] == "fixture-worker-transport"
        assert driver.os.environ["SSH_ASKPASS"] == "fixture-worker-helper"
    assert "spec" not in seen


def test_detached_receipt_does_not_wait_or_claim_completion(queued, capsys):
    import json

    seen, request = queued
    assert driver.cmd_update_request(["--detach", "--json"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["request_id"] == request.id
    assert receipt["state"] == "pending"
    assert receipt["exit_code"] is None
    assert "wait" not in seen


def test_failed_worker_launch_is_not_foreground_fallback(queued, monkeypatch):
    import update_driver

    seen, _request = queued

    def fail(*args, **kwargs):
        raise update_driver.SpawnError("fixture launch denied")

    monkeypatch.setattr(update_driver, "ensure_worker", fail)
    assert driver.cmd_update_request([]) == 1
    assert "spec" in seen
    assert "wait" not in seen


def test_invalid_witness_cannot_become_an_ordinary_request(queued, monkeypatch):
    import update_driver

    seen, _request = queued

    def fail():
        raise update_driver.ParticipantError("stale custody")

    monkeypatch.setattr(update_driver, "borrowed_custody", fail)
    assert driver.cmd_update_request([]) == 1
    assert "spec" not in seen


def test_source_change_refuses_before_checked_participant_executes(queued, monkeypatch, tmp_path):
    import update_driver

    seen, request = queued
    request.state = "running"
    request.spec = SimpleNamespace(
        backend="manager", source="different-selection", context=(), killswitch_path="", cwd=str(tmp_path),
        argv=(),
    )

    class Custody:
        root = tmp_path
        request_id = request.id

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def assert_owned(self):
            seen["custody"] = True

    monkeypatch.setattr(update_driver, "borrowed_custody", Custody)
    monkeypatch.setattr(update_driver, "status", lambda *args, **kwargs: request)
    assert driver.cmd_update_request([]) == 1
    assert "spec" not in seen
    assert "custody" not in seen


def test_public_manager_route_uses_request_boundary(monkeypatch):
    from worktree_manager import __main__ as manager

    seen = []
    monkeypatch.setattr(driver, "cmd_update_request", lambda rest: seen.append(rest) or 17)
    assert manager.main(["update", "--wait"]) == 17
    assert seen == [["--wait"]]


@pytest.mark.timeout(2 * _NATIVE_QUEUE_TIMEOUT)
def test_real_cli_worker_preserves_exit_and_serializes_processes(tmp_path, monkeypatch, short_home):
    import json
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from agent_procutil.owned_process import run_installation_owned
    import update_driver

    home = short_home
    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    command = [sys.executable, str(fixture), "submit", "--wait"]
    with ThreadPoolExecutor(max_workers=2) as callers:
        futures = [
            callers.submit(
                run_installation_owned, command, timeout=_NATIVE_QUEUE_TIMEOUT,
                max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
            ) for _ in range(2)
        ]
        results = [future.result() for future in futures]
    assert [result.returncode for result in results] == [6, 6], [
        result.stderr for result in results
    ]
    assert all("Update request " in result.stderr for result in results)
    assert all("synthetic canonical updater started" in result.stderr for result in results)
    records = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    assert len(records) in (2, 4)
    for index in range(0, len(records), 2):
        assert records[index]["phase"] == "start"
        assert records[index + 1]["phase"] == "end"
        assert records[index]["pid"] == records[index + 1]["pid"]
    requests = update_driver.status()
    assert len(requests) in (1, 2)
    assert all(request.state == "completed" and request.exit_code == 6 for request in requests)


@pytest.mark.parametrize("automatic,held", [(False, True), (True, True), (True, False)])
@pytest.mark.timeout(_NATIVE_QUEUE_TIMEOUT)
def test_real_cli_manual_auto_hold_reaches_the_execution_owner(
    tmp_path, monkeypatch, short_home, automatic, held,
):
    import json
    import sys
    from agent_procutil.owned_process import run_installation_owned
    import update_driver

    events = tmp_path / "events.jsonl"
    hold = tmp_path / "hold.json"
    hold.write_text(json.dumps({"active": held}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(short_home))
    monkeypatch.setenv("USERPROFILE", str(short_home))
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(hold))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_ENGINE_HANDOFF", "1")
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    result = run_installation_owned(
        [sys.executable, str(fixture), "submit", "--wait", *(["--auto"] if automatic else [])],
        timeout=_NATIVE_QUEUE_TIMEOUT,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
    )
    skipped = automatic and held
    assert result.returncode == (3 if skipped else 6), result.stderr
    request, = update_driver.status()
    assert request.spec.repair is not automatic
    assert dict(request.spec.context)["WORKTREE_MANAGER_ROOT"] == str(short_home / ".worktree-manager")
    assert ("--auto" in request.runner.argv) is automatic
    assert "--force" not in request.runner.argv
    assert request.state == "completed" and request.exit_code == result.returncode
    assert events.exists() is not skipped
    if skipped:
        assert request.started_at is None and not request.log_path.exists()
        assert "automatic-update KillSwitch active; nothing ran" in result.stderr
    else:
        records = [json.loads(line) for line in events.read_text().splitlines()]
        assert any(record["phase"] == "engine" for record in records)


@pytest.mark.parametrize("fresh_repair", [False, True], ids=["ordinary", "fresh-repair"])
@pytest.mark.timeout(2 * _NATIVE_QUEUE_TIMEOUT)
def test_actual_queued_manager_keeps_consumed_provider(
    tmp_path, monkeypatch, short_home, fresh_repair,
):
    import json
    import sys
    from pathlib import Path
    from agent_procutil.owned_process import run_installation_owned
    from worktree_manager import engine_client

    home = short_home
    events = tmp_path / "events.jsonl"
    provider = [sys.executable, str(tmp_path / "selected-provider.py")]
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    monkeypatch.setenv(engine_client.ENGINE_ARGV_ENV, json.dumps(provider))
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    forwarded = ["--repair-engine", str(tmp_path / "fresh source")] if fresh_repair else []
    result = run_installation_owned(
        [sys.executable, str(fixture), "submit", "--wait", *forwarded],
        timeout=_NATIVE_QUEUE_TIMEOUT,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
    )
    assert result.returncode == 6, result.stderr
    records = [json.loads(line) for line in events.read_text().splitlines()]
    assert records[0]["provider"] == provider
    assert records[0]["provider"] != ["synthetic-marker-provider"]
    assert records[0]["arguments"] == forwarded


@pytest.mark.timeout(7 * _NATIVE_QUEUE_TIMEOUT)
def test_actual_queued_manager_isolates_explicit_and_absent_engine_overrides(
    tmp_path, monkeypatch, short_home,
):
    import json
    import sys
    from pathlib import Path
    import update_driver
    from agent_procutil.owned_process import run_owned
    from worktree_manager import engine_client

    home = short_home
    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    accepted = ["synthetic-accepted-provider"]
    budget_key = "WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC"
    monkeypatch.setenv(engine_client.ENGINE_ARGV_ENV, json.dumps(accepted))
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    for override, budget in (
        ("synthetic-first-provider", "600"), ("synthetic-second-provider", "1200"), (None, None),
    ):
        for key, value in ((engine_client.ENGINE_CMD_ENV, override), (budget_key, budget)):
            if value is None:
                monkeypatch.delenv(key)
            else:
                monkeypatch.setenv(key, value)
        result = run_owned(
            [sys.executable, str(fixture), "submit-only"], timeout=_NATIVE_QUEUE_TIMEOUT,
        )
        assert result.returncode == 0, result.stderr
    root = home / ".copilot-extensions" / "update-driver"
    requests = update_driver.status(root=root)
    assert len(requests) == 3
    assert [request.state for request in requests] == ["completed", "completed", "pending"]
    assert all(request.exit_code == 125 and request.started_at is None for request in requests[:-1])
    monkeypatch.setenv(engine_client.ENGINE_CMD_ENV, "wrong-worker-provider")
    monkeypatch.setenv(budget_key, "180")
    result = run_owned(
        [sys.executable, "-m", "update_driver", "worker"],
        timeout=len(requests) * _NATIVE_QUEUE_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in events.read_text().splitlines()]
    starts = [record for record in records if record["phase"] == "start"]
    assert [record["provider"] for record in starts] == [accepted]
    assert [record["acquisition_budget"] for record in starts] == [None]


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.timeout(4 * _NATIVE_QUEUE_TIMEOUT)
def test_real_manager_to_engine_custody_keeps_the_legacy_context_contract(
    tmp_path, monkeypatch, short_home, automatic,
):
    import json
    import sys
    import update_driver
    from agent_procutil.owned_process import run_installation_owned

    events = tmp_path / "events.jsonl"
    key = "WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC"
    monkeypatch.setenv("HOME", str(short_home))
    monkeypatch.setenv("USERPROFILE", str(short_home))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_ENGINE_HANDOFF", "1")
    monkeypatch.setenv(key, "600")
    forwarded = ["--automatic-trigger", "manager"] if automatic else []
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    result = run_installation_owned(
        [sys.executable, str(fixture), "submit", "--wait", *forwarded],
        timeout=3 * _NATIVE_QUEUE_TIMEOUT,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
    )
    assert result.returncode == 6, result.stderr
    records = [json.loads(line) for line in events.read_text().splitlines()]
    manager = next(record for record in records if record["phase"] == "start")
    engine = next(record for record in records if record["phase"] == "engine")
    assert manager["acquisition_budget"] == "600.0"
    assert manager["arguments"] == forwarded
    assert engine["no_manager"] is True and key not in engine["context_keys"]
    assert manager["pid"] != engine["pid"]
    assert "CE_UPDATE_UNATTENDED" not in engine["context_keys"]
    assert engine["unattended"] == ("1" if automatic else None)
    requests = update_driver.status(root=short_home / ".copilot-extensions" / "update-driver")
    assert len(requests) == 1 and requests[0].state == "completed"
    assert requests[0].exit_code == 6


def test_recovery_inspection_and_settlement_never_start_an_updater(queued, monkeypatch, capsys):
    import json
    import update_driver

    seen, request = queued
    monkeypatch.setattr(update_driver, "recovery_status", lambda: SimpleNamespace(
        eligible_ids=(request.id,), refused=(), error=None,
    ))
    assert driver.cmd_update_request(["--recovery-status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["eligible_requests"] == [request.id]
    monkeypatch.setattr(update_driver, "recover_after_restart", lambda: (request,))
    assert driver.cmd_update_request(["--recover-after-restart", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["replayed"] is False
    assert seen == {}


def test_same_boot_recovery_refusal_is_structured_and_does_not_enqueue(queued, monkeypatch, capsys):
    import json
    import update_driver

    seen, _request = queued

    def refuse():
        raise update_driver.RecoveryError("same_kernel_boot")

    monkeypatch.setattr(update_driver, "recover_after_restart", refuse)
    assert driver.cmd_update_request(["--recover-after-restart", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "same_kernel_boot"
    assert seen == {}


@pytest.mark.parametrize(("args", "carries_witness"), [
    (["update", "--no-manager"], True),
    (["reconcile-plugins", "--apply"], True),
    (["stage-update"], True),
    (["update", "--auth-report"], False),
    (["stage-update", "--indicator-state"], False),
    (["doctor"], False),
])
def test_engine_handoff_is_explicit_only_for_mutating_update_commands(args, carries_witness, monkeypatch):
    import update_driver
    from worktree_manager import engine_client

    seen = {}
    monkeypatch.setattr(update_driver, "participant_environment", lambda: {"UPDATE_DRIVER_WITNESS": "fixture"})
    monkeypatch.setattr(engine_client, "_engine_environment", lambda: {"SAFE": "fixture"})
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: ["fixture-engine"])

    def run(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(engine_client.subprocess, "run", run)
    assert engine_client.run_engine_passthrough(None, args) == 0
    assert ("UPDATE_DRIVER_WITNESS" in seen["env"]) == carries_witness
    if carries_witness and engine_client.os.name == "nt":
        assert seen["creationflags"] & engine_client.subprocess.CREATE_NO_WINDOW
        assert seen["stdout"] is driver.sys.stdout
        assert seen["stderr"] is driver.sys.stderr
    elif not carries_witness:
        assert "creationflags" not in seen
        assert "stdout" not in seen and "stderr" not in seen


@pytest.mark.skipif(driver.os.name != "nt", reason="native Windows no-window stream inheritance")
def test_scoped_engine_passthrough_retains_both_channels_and_native_exit():
    import subprocess
    import sys
    from agent_procutil import no_window_kwargs

    child = (
        "import sys;"
        "[(print('engine-out-'+str(i),flush=True),"
        "print('engine-err-'+str(i),file=sys.stderr,flush=True)) for i in range(3)];"
        "sys.exit(23)"
    )
    parent = """
import sys, update_driver
from worktree_manager import engine_client
update_driver.participant_environment = lambda: {"UPDATE_DRIVER_WITNESS": "fixture"}
engine_client.set_engine_command([sys.executable, "-I", "-B", "-c", sys.argv[1]])
sys.exit(engine_client.run_engine_passthrough(None, ["update", "--no-manager"], timeout=15))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", parent, child],
        capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL,
        **no_window_kwargs(),
    )
    assert result.returncode == 23, result.stdout + result.stderr
    assert result.stdout.splitlines() == [f"engine-out-{i}" for i in range(3)]
    assert result.stderr.splitlines() == [f"engine-err-{i}" for i in range(3)]


@pytest.mark.timeout(4 * _NATIVE_QUEUE_TIMEOUT)
def test_actual_queued_automatic_update_sanitizes_a_foreground_drivers_environment(
    tmp_path, monkeypatch, short_home,
):
    import json
    import sys
    from dataclasses import asdict
    import update_driver
    from agent_procutil.owned_process import run_owned

    events = tmp_path / "authentication-events.jsonl"
    monkeypatch.setenv("HOME", str(short_home))
    monkeypatch.setenv("USERPROFILE", str(short_home))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    monkeypatch.delenv("UPDATE_DRIVER_TEST_ENGINE_HANDOFF", raising=False)
    monkeypatch.delenv("CE_UPDATE_UNATTENDED", raising=False)
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    for arguments in ([], ["--automatic-trigger", "manager"]):
        accepted = run_owned(
            [sys.executable, str(fixture), "submit-only", *arguments],
            timeout=_NATIVE_QUEUE_TIMEOUT,
        )
        assert accepted.returncode == 0, accepted.stderr
    root = short_home / ".copilot-extensions/update-driver"
    requests = update_driver.status(root=root)
    assert len(requests) == 2
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "AGENT_WORKTREES_AHP_AUTH_TOKEN"):
        monkeypatch.setenv(key, "foreground-driver-session-token")
    monkeypatch.setenv("GIT_SSH_COMMAND", "unwanted-foreground-transport")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "unwanted-repository"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    monkeypatch.setenv("SSH_ASKPASS", "unwanted-foreground-helper")
    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "force")
    monkeypatch.setenv("CE_UPDATE_UNATTENDED", "1")
    completed = run_owned(
        [sys.executable, "-m", "update_driver", "worker"],
        timeout=2 * _NATIVE_QUEUE_TIMEOUT,
    )
    assert completed.returncode == 0, completed.stderr
    records = [json.loads(line) for line in events.read_text().splitlines()]
    foreground, automatic = [record for record in records if record["phase"] == "start"]
    assert foreground["session_credentials_present"] and foreground["git_override_present"]
    assert foreground["askpass_requirement"] == "force"
    assert foreground["unattended"] is None
    assert automatic["arguments"] == ["--automatic-trigger", "manager", "--auto"]
    assert not automatic["session_credentials_present"] and not automatic["git_override_present"]
    assert not automatic["askpass_present"] and automatic["askpass_requirement"] == "never"
    assert automatic["unattended"] == "1"
    requests = update_driver.status(root=root)
    assert all(request.state == "completed" and request.exit_code == 6 for request in requests)
    journaled = json.dumps([(asdict(request.spec), asdict(request.runner)) for request in requests])
    assert "foreground-driver-session-token" not in journaled
    assert all("CE_UPDATE_UNATTENDED" not in dict(request.spec.context) for request in requests)
