"""Real acquisition-only Configuration boundary and preserved native state."""

import json
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

from agent_procutil.owned_budget import OwnedExecutionBudget
import pytest
import yaml

from agent_worktrees import config as cfg, repos, repository_bootstrap as bootstrap
from agent_worktrees import repos_knowledge_acquisition as acquisition
from agent_worktrees.repos_configuration_sources import ConfigurationError
from test_repos_configuration import cli, local_knowledge, native as native, tree
from test_repository_bootstrap_https import https_remote as https_remote
from test_repository_setup import git, source as source


URL = "https://git.example.test/team/remote-knowledge.git"


def fields(path, url=URL):
    return ["--context-project", "sample", "--repository-url", url, "--checkout-path", str(path)]


@pytest.mark.parametrize("access", ["configured-private", "public", "denied"])
def test_native_https_acquisition_is_unregistered_without_setup_or_auth_ui(native, https_remote, access):
    local_knowledge(native)
    url, observed, helper, gitconfig = https_remote
    if access == "public":
        observed["private"] = False
    if access != "configured-private":
        git(native, "config", "--file", str(gitconfig), "--unset-all", "credential.helper")
    before = tree(native)
    config_before = gitconfig.read_bytes()
    destination = native / "acquired"
    arguments = fields(destination, url)
    proposed = cli("knowledge-acquire-preview", *arguments)
    assert proposed.returncode == 0, proposed.stderr
    proposal = json.loads(proposed.stdout)
    assert observed["requests"] == 0 and not helper.with_suffix(".called").exists()
    assert tree(native) == before
    old_host = cli("knowledge-acquire-apply", *arguments, "--preview-token", "")
    assert old_host.returncode and json.loads(old_host.stdout)["code"] == "missing-preview"
    assert tree(native) == before and observed["requests"] == 0
    completed = cli("knowledge-acquire-apply", *arguments, "--preview-token", proposal["token"])
    receipt = json.loads(completed.stdout)
    assert receipt["registration_changed"] is False and receipt["binding_changed"] is False
    assert receipt["target"] == proposal["target"]
    assert gitconfig.read_bytes() == config_before
    assert "disposable-test-value" not in proposed.stdout + proposed.stderr + completed.stdout + completed.stderr
    after = tree(native)
    allowed = {str(Path(".agent-worktrees") / name) for name in ("repos.lock", "pr-rewrite-authority.lock")}
    allowed.add(str(helper.with_suffix(".called").relative_to(native)))
    assert {key: value for key, value in after.items() if not key.startswith("acquired") and key not in allowed} == before
    assert "remote-knowledge" not in repos.read_registry(strict=True).repos
    assert not (native / "unexpected-setup").exists()
    if access == "denied":
        assert completed.returncode and receipt["status"] == "incomplete"
        assert receipt["target_exists"] == destination.exists()
    else:
        assert completed.returncode == 0 and receipt["status"] == "acquired"
        assert git(destination, "rev-parse", "HEAD") == receipt["commit"]
        assert git(destination, "remote", "get-url", "origin") == url
        assert (destination / "setup.ps1").is_file()
        if access == "configured-private":
            assert json.loads(helper.with_suffix(".called").read_text()) == {
                "GCM_INTERACTIVE": "0", "GCM_GUI_PROMPT": "0", "GIT_TERMINAL_PROMPT": "0",
            }
    preserved = tree(native)
    retry = cli("knowledge-acquire-preview", *arguments)
    if destination.exists():
        assert retry.returncode
        assert tree(native) == preserved


@pytest.mark.parametrize("change", ["registry", "account", "binding", "parent"])
def test_native_acquisition_stale_preview_refuses_before_network(native, change):
    local_knowledge(native)
    parent = native / "destination-parent"
    parent.mkdir()
    destination = parent / "new"
    proposal = acquisition.preview("sample", URL, str(destination))
    if change in {"registry", "account"}:
        path = repos._repos_yaml_path()
        raw = yaml.safe_load(path.read_text())
        raw["account_map" if change == "account" else "new_owner_field"] = (
            {"example": "changed"} if change == "account" else "changed"
        )
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    elif change == "binding":
        path = cfg.project_dir("sample") / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("knowledge_repo: elsewhere\n", encoding="utf-8")
    else:
        parent.rename(native / "retained-parent")
        parent.mkdir()
    before = tree(native)
    with pytest.raises(ConfigurationError):
        acquisition.apply("sample", URL, str(destination), proposal["token"])
    after = tree(native)
    assert {key: value for key, value in after.items() if not key.endswith(".lock")} == before
    assert not destination.exists()


@pytest.mark.parametrize("kind", [
    "existing", "nested", "bare", "metadata", "indirect", "registered-path", "registered-remote",
    "registered-name", "rewrite", "relative", "source-parameters", "malformed-binding",
])
def test_native_preview_refuses_unsafe_destinations_without_effects(native, kind):
    root = local_knowledge(native)
    destination = native / "new"
    url = URL
    if kind == "existing":
        destination.mkdir()
    elif kind == "nested":
        destination = root / "new"
    elif kind == "bare":
        bare = native / "unregistered-bare"
        git(native, "init", "--bare", "--quiet", str(bare))
        destination = bare / "new"
    elif kind == "metadata":
        destination = root / ".git" / "new"
    elif kind == "indirect":
        backing = native / "plain-parent"
        backing.mkdir()
        alias = native / "indirect-parent"
        try:
            alias.symlink_to(backing, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"native directory symlinks are unavailable: {error}")
        destination = alias / "new"
    elif kind.startswith("registered-"):
        raw = yaml.safe_load(repos._repos_yaml_path().read_text())
        raw["repos"]["remote-knowledge" if kind == "registered-name" else "alias"] = {"class": "reference", **(
            {repos._current_platform(): str(destination)} if kind == "registered-path" else
            {"remote": "https://git.example.test/other/remote-knowledge.git" if kind == "registered-name" else URL}
        )}
        repos._repos_yaml_path().write_text(yaml.safe_dump(raw), encoding="utf-8")
    elif kind == "rewrite":
        git(native, "config", "--file", str(native / ".gitconfig"),
            "url.https://elsewhere.example.test/.insteadOf", "https://git.example.test/")
        # The contained fixture's Git global selector is explicit, never the host's config.
        previous = os.environ.get("GIT_CONFIG_GLOBAL")
        os.environ["GIT_CONFIG_GLOBAL"] = str(native / ".gitconfig")
    elif kind == "source-parameters":
        url += "?unexpected=value"
    elif kind == "malformed-binding":
        path = cfg.project_dir("sample") / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("knowledge_repo: [\n", encoding="utf-8")
    else:
        destination = Path("relative")
    before = tree(native)
    try:
        with pytest.raises((ConfigurationError, bootstrap.protocol.SetupError)):
            acquisition.preview("sample", url, str(destination))
    finally:
        if kind == "rewrite":
            if previous is None:
                os.environ.pop("GIT_CONFIG_GLOBAL", None)
            else:
                os.environ["GIT_CONFIG_GLOBAL"] = previous
    assert tree(native) == before


@pytest.mark.parametrize("failure", ["partial", "concurrent", "late-binding", "late-source"])
def test_acquisition_preserves_partial_target_and_rechecks_late_inputs(native, source, monkeypatch, failure):
    local_knowledge(native)
    git(source, "remote", "set-url", "origin", URL)
    destination = native / "new"
    budget = OwnedExecutionBudget.from_timeout(26, cleanup_reserve=2)
    selected = acquisition._selection("sample", URL, str(destination))
    request = {
        "operation": "apply", "project": "sample", "url": URL, "path": str(destination),
        "token": acquisition._token(selected), "deadline": budget.deadline, "cleanup_reserve": 2,
    }

    def clone(url, path, *_args, **_kwargs):
        if failure == "partial":
            (path / "preserve.txt").write_text("unfinished", encoding="utf-8")
            raise bootstrap.protocol.SetupError("Git acquisition exited with status 9.")
        shutil.copytree(source, path, dirs_exist_ok=True)
        if failure == "late-source":
            (path / "setup.ps1").write_text("preserve changed source\n", encoding="utf-8")
            return
        local = cfg.project_dir("sample")
        local.mkdir(parents=True, exist_ok=True)
        (local / "config.yaml").write_text("knowledge_repo: changed\n", encoding="utf-8")

    monkeypatch.setattr(bootstrap, "_clone", clone)
    if failure == "concurrent":
        original = bootstrap._credential_args

        def raced(*args, **kwargs):
            destination.mkdir()
            (destination / "other-owner.txt").write_text("preserved", encoding="utf-8")
            return original(*args, **kwargs)

        monkeypatch.setattr(bootstrap, "_credential_args", raced)
    before = repos._repos_yaml_path().read_bytes()
    receipt = acquisition._worker(request)
    assert receipt["status"] == "incomplete" and receipt["target_exists"] is True
    assert repos._repos_yaml_path().read_bytes() == before
    assert destination.is_dir()
    assert (destination / ("preserve.txt" if failure == "partial" else
                           "other-owner.txt" if failure == "concurrent" else "setup.ps1")).is_file()


def test_native_deadline_owns_descendants_and_keeps_partial_target(native, monkeypatch):
    local_knowledge(native)
    destination = native / "partial"
    proposed = acquisition.preview("sample", URL, str(destination))
    original = acquisition.run_caller_owned
    marker = native / "late-descendant"

    def slow(argv, **kwargs):
        child = "import pathlib,time;time.sleep(3);pathlib.Path(" + repr(str(marker)) + ").touch()"
        script = (
            "import pathlib,subprocess,sys,time;"
            f"pathlib.Path({str(destination)!r}).mkdir();"
            f"subprocess.Popen([sys.executable,'-c',{child!r}],"
            "**({'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}));"
            "time.sleep(20)"
        )
        kwargs["budget"] = OwnedExecutionBudget.from_timeout(2, cleanup_reserve=1)
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(acquisition, "run_caller_owned", slow)
    started = time.monotonic()
    receipt = acquisition.apply("sample", URL, str(destination), proposed["token"])
    assert time.monotonic() - started < 4
    assert receipt["status"] == "incomplete" and receipt["target_exists"] is True
    time.sleep(3)
    assert not marker.exists() and destination.is_dir()


def test_native_explicit_account_refuses_ambient_helper_without_authentication(native, monkeypatch):
    local_knowledge(native)
    path = repos._repos_yaml_path()
    raw = yaml.safe_load(path.read_text())
    raw["account_map"] = {"example": "selected-login"}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = native / "unqualified.gitconfig"
    git(native, "config", "--file", str(config), "credential.helper", "store")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    destination = native / "absent"
    url = "https://github.com/example/remote-knowledge.git"
    proposed = acquisition.preview("sample", url, str(destination))
    assert any("selected-login" in effect for effect in proposed["effects"])
    before = tree(native)
    receipt = acquisition.apply("sample", url, str(destination), proposed["token"])
    assert receipt["status"] == "incomplete" and receipt["target_exists"] is False
    assert "qualified GCM" in receipt["reason"]
    after = tree(native)
    assert {key: value for key, value in after.items() if not key.endswith(".lock")} == before


@pytest.mark.parametrize("extra", [
    ["--repository", "unexpected"], ["--account", "unexpected"], ["--default-branch", "main"],
    ["--login", "unexpected"], ["--workspace", "unexpected"], ["--operation", "reset"],
    ["--accepted-default"], ["--unsupported-acquisition-argument"], ["--repository-u", URL],
])
def test_native_acquisition_parser_refuses_unrelated_or_unknown_fields(native, extra):
    before = tree(native)
    result = cli("knowledge-acquire-preview", *fields(native / "new"), *extra)
    assert result.returncode == 1 and json.loads(result.stdout)["code"] == "invalid-request"
    assert tree(native) == before


def test_two_confirmed_native_applies_cannot_share_or_replace_a_destination(native, https_remote):
    local_knowledge(native)
    url, observed, _, _ = https_remote
    observed["private"] = False
    destination = native / "new"
    token = acquisition.preview("sample", url, str(destination))["token"]

    def apply():
        try:
            return acquisition.apply("sample", url, str(destination), token)
        except ConfigurationError:
            return {"status": "refused"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: apply(), range(2)))
    assert sorted(result["status"] for result in results) == ["acquired", "refused"]
    acquired = next(result for result in results if result["status"] == "acquired")
    assert git(destination, "rev-parse", "HEAD") == acquired["commit"]
    assert "remote-knowledge" not in repos.read_registry(strict=True).repos


@pytest.mark.parametrize("process_present", [False, True])
def test_unconfirmed_cleanup_preserves_original_owner_through_configuration_reporting(native, monkeypatch, process_present):
    from agent_procutil.owned_process import OwnedCleanupError
    from agent_worktrees.repos_configuration import command

    process = SimpleNamespace(pid=123456) if process_present else None
    job, resource = object(), object()
    settled, notified = [], []
    owner = OwnedCleanupError(
        process, job, cleanup_resources=(("reader", resource),),
        cleanup_retry=lambda _deadline: settled.append(resource),
        on_settled=lambda: notified.append(True),
    )

    def unconfirmed(*args, **kwargs):
        raise owner

    monkeypatch.setattr(acquisition, "run_caller_owned", unconfirmed)
    with pytest.raises(OwnedCleanupError) as caught:
        command(["knowledge-acquire-apply", *fields(native / "absent"), "--preview-token", "a" * 64])
    assert caught.value is owner and owner.process is process and owner.job is job
    assert owner.cleanup_resources == (("reader", resource),)
    assert settled == notified == []
    owner.retry_cleanup(deadline=time.monotonic() + 1)
    owner.retry_cleanup(deadline=time.monotonic() + 1)
    assert settled == [resource] and notified == [True]
    assert not (native / "absent").exists()


def test_worker_does_not_convert_nested_cleanup_failure_into_a_completed_receipt(native, monkeypatch):
    from agent_procutil.owned_process import OwnedCleanupError

    selected = {
        "name": "remote-knowledge", "path": str(native / "absent"), "url": URL,
        "target": acquisition.action_target("sample"),
    }
    owner = OwnedCleanupError(None, object(), cleanup_resources=(("reader", object()),))
    monkeypatch.setattr(acquisition, "_selection", lambda *_args: selected)

    def unconfirmed(*args, **kwargs):
        raise owner

    monkeypatch.setattr(bootstrap, "acquire", unconfirmed)
    budget = OwnedExecutionBudget.from_timeout(26, cleanup_reserve=2)
    request = {
        "operation": "apply", "project": "sample", "url": URL, "path": selected["path"],
        "token": acquisition._token(selected), "deadline": budget.deadline, "cleanup_reserve": 2,
    }
    with pytest.raises(OwnedCleanupError) as caught:
        acquisition._worker(request)
    assert caught.value is owner and owner.job is not None and owner.cleanup_resources


@pytest.mark.parametrize("operation", ["preview", "apply"])
def test_lost_native_completion_is_sanitized_and_preserves_partial_target(native, monkeypatch, capsys, operation):
    from agent_worktrees.repos_configuration import command

    destination = native / "partial"
    secret = "synthetic-private-diagnostic-do-not-render"
    attempts = []

    def missing_frame(*args, **kwargs):
        attempts.append(json.loads(kwargs["input_text"])["operation"])
        if operation == "apply":
            destination.mkdir()
            (destination / "unfinished").write_text("preserved", encoding="utf-8")
        raise RuntimeError(secret)

    monkeypatch.setattr(acquisition, "run_caller_owned", missing_frame)
    args = ["knowledge-acquire-" + operation, *fields(destination)]
    if operation == "apply":
        args += ["--preview-token", "a" * 64]
    assert command(args) == 1
    output = capsys.readouterr()
    receipt = json.loads(output.out)
    assert secret not in output.out + output.err and "Traceback" not in output.err
    assert attempts == [operation]
    if operation == "apply":
        assert receipt["status"] == "incomplete" and receipt["cleanup"] == "unknown"
        assert receipt["target_exists"] is True and receipt["stage"] == "worker"
        assert (destination / "unfinished").read_text() == "preserved"
    else:
        assert receipt["code"] == "acquisition-preview-unavailable" and not destination.exists()


@pytest.mark.parametrize("account", ["ghp_SYNTHETIC_DO_NOT_RENDER", "github_pat_SYNTHETIC_DO_NOT_RENDER"])
def test_native_preview_refuses_secret_shaped_account_without_rendering_it(native, account):
    local_knowledge(native)
    path = repos._repos_yaml_path()
    raw = yaml.safe_load(path.read_text())
    raw["account_map"] = {"example": account}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    destination = native / "absent"
    before = tree(native)
    result = cli("knowledge-acquire-preview", *fields(destination, "https://github.com/example/remote-knowledge.git"))
    assert result.returncode == 1
    assert json.loads(result.stdout)["code"] == "invalid-identity"
    assert account not in result.stdout + result.stderr
    assert tree(native) == before


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop observation")
@pytest.mark.skipif(os.environ.get("COPILOT_EXTENSIONS_NATIVE_WINDOWS") != "1", reason="opt-in desktop observation")
def test_native_acquisition_from_windowless_parent_has_no_windows_or_focus_changes(native, https_remote):
    import agent_procutil as pu

    local_knowledge(native)
    url, observed, _, _ = https_remote
    observed["private"] = False
    root = Path(__file__).resolve().parents[3]
    path = root / "libs" / "agent-procutil" / "tests" / "test_spawn_daemon_windows_integration.py"
    spec = importlib.util.spec_from_file_location("acquisition_window_observer", path)
    observer_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = observer_module
    spec.loader.exec_module(observer_module)
    user32, kernel32, callback_type = observer_module._configure_win32()
    if not user32.GetForegroundWindow():
        pytest.skip("desktop observation requires an observable foreground window")
    pythonw = pu.windowless_python(sys.executable)
    if not pythonw.lower().endswith("pythonw.exe") or not Path(pythonw).is_file():
        pytest.skip("this interpreter has no real windowless parent")
    parent = native / "parent.py"
    parent.write_text(
        "import ctypes,json,subprocess,sys\nfrom pathlib import Path\n"
        "spec=json.loads(sys.argv[1]);sys.path[:0]=spec['paths']\n"
        "from agent_procutil import no_window_kwargs\n"
        "assert not ctypes.windll.kernel32.GetConsoleWindow()\n"
        "for cycle in range(2):\n"
        " args=[spec['console'],'-B','-m','agent_worktrees','repos','configuration']\n"
        " fields=['--context-project','sample','--repository-url',spec['url'],"
        "'--checkout-path',str(Path(spec['root'])/('checkout'+str(cycle)))]\n"
        " proposed=subprocess.run(args+['knowledge-acquire-preview']+fields,"
        "capture_output=True,text=True,timeout=20,**no_window_kwargs())\n"
        " assert proposed.returncode==0,proposed.stdout+proposed.stderr\n"
        " token=json.loads(proposed.stdout)['token']\n"
        " done=subprocess.run(args+['knowledge-acquire-apply']+fields+['--preview-token',token],"
        "capture_output=True,text=True,timeout=29,**no_window_kwargs())\n"
        " assert done.returncode==0,done.stdout+done.stderr\n"
        " assert json.loads(done.stdout)['status']=='acquired'\n"
        "Path(spec['result']).write_text('2',encoding='ascii')\n",
        encoding="utf-8",
    )
    result, log_path = native / "result", native / "parent.log"
    arguments = {
        "paths": [str(Path(item).resolve()) for item in sys.path if item],
        "console": sys.executable, "root": str(native),
        "url": url, "result": str(result),
    }
    with observer_module._WindowObserver(user32, kernel32, callback_type) as observer:
        with log_path.open("wb") as log:
            process, job = pu.spawn_sync_in_kill_on_close_job(
                [pythonw, "-I", "-B", str(parent), json.dumps(arguments)],
                require_job=True, allow_breakaway=False, cwd=native,
                env={**os.environ, **pu.windowless_python_env(sys.executable)},
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                **pu.no_window_kwargs(),
            )
            try:
                assert job is not None
                assert process.wait(timeout=55) == 0, log_path.read_text(errors="replace")
            finally:
                job.terminate_and_wait(10)
                process.wait(timeout=10)
    assert result.read_text(encoding="ascii") == "2"
    assert observer.surfaced == {} and observer.focus_changes == {}
