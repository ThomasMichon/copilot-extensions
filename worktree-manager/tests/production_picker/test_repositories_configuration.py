"""Configuration consumes the real native owner, never writes its files itself."""

import asyncio
import base64
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import shlex
import ssl
import subprocess
import sys
import threading
import time

from agent_procutil import no_window_kwargs
from knowledge_creation_fixture import URL as CREATED_URL, github_proxy, private_knowledge_provider as private_knowledge_provider
import pytest
from test_config_actions import _wait
from test_picker_tui import _fixture_source, _wait_for_initial_setup
from textual.widgets import Button
import yaml

from worktree_manager.production_picker import context
from worktree_manager.production_picker.picker_tui import pivots, tasks
from worktree_manager.production_picker.picker_tui.configuration_form import (
    ConfigurationFormScreen, ConfigurationResultScreen,
)
from worktree_manager.production_picker.picker_tui.configuration_preview_screen import ConfigurationPreviewScreen
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen

pytestmark = pytest.mark.contract("worktree_manager.configuration_backstage")
SECRET = "github_pat_SENTINEL_DO_NOT_RENDER"


@pytest.fixture
def public_https_knowledge(tmp_path, monkeypatch, request):
    private = request.node.callspec.params.get("remote") == "created"
    provider = request.getfixturevalue("private_knowledge_provider") if private else None
    hostname = "github.com" if private else "localhost"
    branch = "provider-default" if private else "main"
    source, web = tmp_path / "https-source", tmp_path / "web"
    source.mkdir()
    web.mkdir()

    def git(*args):
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull, *args], cwd=tmp_path,
            capture_output=True, text=True, timeout=10, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr

    git("init", "-b", branch, str(source))
    (source / "README").write_text("Disposable remote knowledge source\n", encoding="utf-8")
    (source / ".agent-worktrees").mkdir()
    (source / ".agent-worktrees" / "repository-setup.json").write_text(json.dumps({
        "version": 1, "entrypoints": {
            "windows": {"runtime": "powershell", "script": "setup.ps1"},
            "posix": {"runtime": "bash", "script": "setup.sh"},
        },
    }), encoding="utf-8")
    marker = tmp_path / "unexpected-setup"
    (source / "setup.ps1").write_text(
        "Set-Content -LiteralPath '" + str(marker).replace("'", "''") + "' -Value 'unexpected'\n"
        "throw 'Repository setup must not execute'\n", encoding="utf-8",
    )
    (source / "setup.sh").write_text(
        "touch " + shlex.quote(str(marker)) + "\nexit 99\n", encoding="utf-8",
    )
    git("-C", str(source), "add", ".")
    git("-C", str(source), "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture")
    bare = web / "selected" / "knowledge" if private else web / "knowledge.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git("clone", "--bare", "--quiet", str(source), str(bare))
    git("--git-dir", str(bare), "update-server-info")
    executable = shutil.which("openssl")
    if not executable and (git_executable := shutil.which("git")):
        executable = next((
            str(parent / "usr" / "bin" / "openssl.exe")
            for parent in Path(git_executable).resolve().parents
            if (parent / "usr" / "bin" / "openssl.exe").is_file()
        ), None)
    if not executable:
        pytest.skip("Disposable HTTPS consumer requires existing OpenSSL")
    certificate, key = tmp_path / "fixture.pem", tmp_path / "fixture-key.pem"
    result = subprocess.run([
        executable, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(certificate), "-days", "1",
        "-subj", "/CN=" + hostname, "-addext", "subjectAltName=DNS:" + hostname,
    ], capture_output=True, text=True, timeout=15, **no_window_kwargs())
    assert result.returncode == 0, result.stderr
    config = tmp_path / "fixture.gitconfig"
    for setting, value in (
        ("http.sslCAInfo", str(certificate)), ("http.schannelUseSSLCAInfo", "true"),
        ("credential.helper", "manager" if private else ""),
    ):
        git("config", "--file", str(config), setting, value)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    observed = {"requests": 0, "errors": 0, "authentication": set()}

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            observed["requests"] += 1
            if private:
                state = json.loads(provider.read_text("utf-8"))
                if not state["created"] or not state["protected"]:
                    self.send_error(404)
                    return
                authorization = self.headers.get("Authorization", "").split()
                if not authorization:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="fixture"')
                    self.end_headers()
                    return
                assert authorization[0].casefold() == "basic"
                credentials = {
                    b"selected:fixture-git-password": "credential-helper",
                    b"x-access-token:fixture-token": "configured-gh",
                }
                authenticated = base64.b64decode(authorization[1], validate=True)
                assert authenticated in credentials
                observed["authentication"].add(credentials[authenticated])
            super().do_GET()

        def log_message(self, *_):
            pass

    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            observed["errors"] += 1
            super().handle_error(request, client_address)

    server = Server(("127.0.0.1", 0), partial(Handler, directory=str(web)))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with github_proxy(server.server_port) if private else nullcontext(None) as proxy_port:
            if private:
                git("config", "--file", str(config), "http.https://github.com/.proxy",
                    f"http://127.0.0.1:{proxy_port}")
                observed["provider"] = provider
            yield CREATED_URL if private else f"https://localhost:{server.server_port}/{bare.name}", observed
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert observed["errors"] == 0


@pytest.mark.parametrize("placement", ["config", "left"])
def test_configuration_list_context_is_explicit_and_does_not_change_operational_pivots(monkeypatch, placement):
    monkeypatch.setattr(context, "_project", "selected")
    monkeypatch.setattr(context, "_bootstrap", context.ProjectBootstrap(
        "selected", False, "switch-destination", False, workspace="explicit-workspace",
    ))
    reg = pivots.parse_manifest(
        {"label": "Fixture", "placement": placement, "scope": "global",
         "list": ["unused", "{repo}", "{configuration_workspace}"]},
        name="fixture", source_path="fixture.json",
    )
    runtime = tasks.RegisteredPivotRuntime(reg)
    calls = []
    monkeypatch.setattr(runtime, "_exec_list", lambda ctx: (calls.append(ctx) or ("ready", [], "", {})))
    runtime._run_list("operational-machine")
    expected = {"machine": "operational-machine"}
    if placement == "config":
        expected.update(repo="selected", configuration_workspace="explicit-workspace")
    assert calls == [expected]
    assert runtime.get("operational-machine")[0] == "ready"


def test_legacy_bootstrap_switch_destination_is_not_a_configuration_workspace(monkeypatch):
    monkeypatch.setattr(context, "_project", "selected")
    monkeypatch.setattr(context, "_bootstrap", context.ProjectBootstrap(
        "selected", True, "switch-destination", False,
    ))
    reg = pivots.parse_manifest(
        {"label": "Fixture", "placement": "config", "scope": "global",
         "list": ["unused", "{configuration_workspace}"]},
        name="fixture", source_path="fixture.json",
    )
    runtime = tasks.RegisteredPivotRuntime(reg)
    calls = []
    monkeypatch.setattr(runtime, "_exec_list", lambda ctx: (calls.append(ctx) or ("ready", [], "", {})))
    runtime._run_list("")
    assert calls == [{"machine": "", "repo": "selected", "configuration_workspace": ""}]


def test_unbound_configuration_project_is_visible_error_not_a_hung_load(monkeypatch):
    monkeypatch.setattr(context, "_project", None)
    monkeypatch.setattr(context, "_bootstrap", None)
    reg = pivots.parse_manifest(
        {"label": "Fixture", "placement": "config", "scope": "global",
         "list": ["must-not-run", "{repo}"]},
        name="fixture", source_path="fixture.json",
    )
    runtime = tasks.RegisteredPivotRuntime(reg)
    runtime._run_list("")
    assert runtime.get("")[0] == "error" and "project is not bound" in runtime.get("")[2]
    assert not runtime._inflight


def test_configuration_owner_does_not_inherit_manager_python_runtime(monkeypatch):
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "VIRTUAL_ENV",
                "UV_INTERNAL__PYTHONHOME", "__PYVENV_LAUNCHER__", "COPILOT_PLUGIN_ROOT"):
        monkeypatch.setenv(key, "foreign-runtime")
    monkeypatch.setenv("AGENT_HOME", "preserved-native-state")
    child = tasks._child_process_env()
    assert child["AGENT_HOME"] == "preserved-native-state"
    assert child["PYTHONUTF8"] == child["PYTHONSAFEPATH"] == "1"
    assert "foreign-runtime" not in child.values()


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows caller-timeout/worker-handle contract")
def test_binding_worker_keeps_registration_locked_after_manager_timeout(tmp_path, monkeypatch):
    import _winapi

    owner_python = os.environ.get("REPOSITORIES_CONFIG_TEST_PYTHON")
    if not owner_python:
        pytest.skip("set REPOSITORIES_CONFIG_TEST_PYTHON to the isolated native engine interpreter")
    home, project = tmp_path / "home", tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(tasks, "ACTION_TIMEOUT", 5)

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value), encoding="utf-8")

    config = home / ".sample" / "config.yaml"
    write(config, {"repo_name": "sample", "repos": {"sample": {"anchor": str(project)}}})
    registry = home / ".agent-worktrees" / "repos.yaml"
    write(registry, {"repos": {"sample": {"class": "worktree", "windows": str(project)}}})
    write(home / ".agent-worktrees" / "projects.yaml", {
        "schema_version": 2, "projects": {"sample": {"config_dir": str(config.parent)}},
    })
    payload = home / ".copilot" / "installed-plugins" / "fixture" / "harness-knowledge"
    payload.mkdir(parents=True)
    (payload / "plugin.json").write_text(json.dumps({"name": "harness-knowledge"}), encoding="utf-8")
    (home / ".copilot" / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"harness-knowledge@fixture": True},
    }), encoding="utf-8")
    ready, release, writer_started = (tmp_path / name for name in ("ready.json", "release", "writer-started"))
    script = payload / "skills" / "binding-knowledge" / "scripts" / "bind_knowledge.py"
    script.parent.mkdir(parents=True)
    binding_yaml = Path(__file__).resolve().parents[3] / "plugins" / "harness-knowledge" / "skills" / "binding-knowledge" / "scripts" / "binding_yaml.py"
    script.write_text(
        "import importlib.util,json,os,time\nfrom pathlib import Path\n"
        f"spec=importlib.util.spec_from_file_location('binding_yaml',{str(binding_yaml)!r})\n"
        "owner=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner)\n"
        "def paused():\n"
        f" Path({str(ready)!r}).write_text(json.dumps({{'pid':os.getpid(),'at':time.monotonic()}}))\n"
        " deadline=time.monotonic()+20\n"
        f" while not Path({str(release)!r}).exists():\n"
        "  if time.monotonic()>deadline: raise RuntimeError('fixture release expired')\n"
        "  time.sleep(0.02)\n"
        f"owner.write_binding(Path({str(config)!r}),'sample','knowledge',preserve=True,precondition=paused)\n",
        encoding="utf-8",
    )
    worker = [owner_python, "-B", "-m", "agent_worktrees.repos_knowledge_configuration",
              "sample", "knowledge", "a" * 64, str(script)]
    # Keep delayed settlement within the enclosing test owner, not the expired caller.
    parent = (
        "import subprocess\n"
        f"child=subprocess.Popen({worker!r},stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)\n"
        "child.wait(timeout=25)\n"
    )
    runtime = tasks.RegisteredPivotRuntime(pivots.parse_manifest(
        {"label": "Fixture", "list": ["unused"]}, name="fixture", source_path="fixture",
    ))
    worker_handle = None
    writer = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        caller = pool.submit(runtime.run_resolved, [sys.executable, "-c", parent], preserve_output=True)
        try:
            deadline = time.monotonic() + 8
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists(), caller.result(timeout=3)
            receipt = json.loads(ready.read_text("utf-8"))
            assert time.monotonic() - receipt["at"] < 5
            worker_handle = _winapi.OpenProcess(0x00100001, False, receipt["pid"])
            ok, message = caller.result(timeout=8)
            assert not ok and "Command timed out after 5s" in message
            assert _winapi.WaitForSingleObject(worker_handle, 0) == _winapi.WAIT_TIMEOUT
            writer = subprocess.Popen(
                [owner_python, "-c",
                 "from pathlib import Path;from agent_worktrees import repos;"
                 f"Path({str(writer_started)!r}).touch();"
                 "assert repos.set_copilot_account('sample','after')"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=tasks._child_process_env(), **no_window_kwargs(),
            )
            deadline = time.monotonic() + 3
            while not writer_started.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert writer_started.exists() and writer.poll() is None
            with pytest.raises(subprocess.TimeoutExpired):
                writer.wait(timeout=0.2)
            assert yaml.safe_load(config.read_text("utf-8")).get("knowledge_repo") is None
            release.touch()
            assert _winapi.WaitForSingleObject(worker_handle, 5000) == _winapi.WAIT_OBJECT_0
            output, error = writer.communicate(timeout=5)
            assert writer.returncode == 0, output + error
            assert yaml.safe_load(config.read_text("utf-8"))["knowledge_repo"] == "knowledge"
            assert yaml.safe_load(registry.read_text("utf-8"))["repos"]["sample"]["copilot_account"] == "after"
        finally:
            release.touch()
            if writer is not None and writer.poll() is None:
                writer.terminate()
                writer.communicate(timeout=5)
            if worker_handle is not None:
                if _winapi.WaitForSingleObject(worker_handle, 5000) == _winapi.WAIT_TIMEOUT:
                    _winapi.TerminateProcess(worker_handle, 1)
                    assert _winapi.WaitForSingleObject(worker_handle, 5000) == _winapi.WAIT_OBJECT_0
                _winapi.CloseHandle(worker_handle)


def test_native_repositories_ui_real_cli_cancel_set_reset_and_concurrent_ordinary_write(tmp_path, monkeypatch):
    owner_python = os.environ.get("REPOSITORIES_CONFIG_TEST_PYTHON")
    if not owner_python:
        pytest.skip("set REPOSITORIES_CONFIG_TEST_PYTHON to the isolated native engine interpreter")
    root = Path(__file__).resolve().parents[3]
    native = tmp_path / "native"
    native.mkdir()
    for name in ("repository", "knowledge", "plugin", "preset"):
        folder = native / name
        folder.mkdir()
        (folder / "config.yaml").write_text(
            yaml.safe_dump({"secret": SECRET}), encoding="utf-8",
        )
    registry = native / ".agent-worktrees"
    registry.mkdir()
    platform = "windows" if os.name == "nt" else "linux"
    (registry / "repos.yaml").write_text(yaml.safe_dump({
        "repos": {
            "sample": {"class": "worktree", platform: str(native / "repository")},
            "knowledge": {"class": "worktree", platform: str(native / "knowledge")},
        },
    }), encoding="utf-8")
    (registry / "config.yaml").write_text(
        "default_copilot_account: inherited\n", encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_HOME", str(native))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(context, "_project", "sample")
    monkeypatch.setattr(context, "_bootstrap", None)
    workspaces = {name: native / f"{name}-worktree" for name in ("sample", "knowledge")}
    for name, sibling, role in (("sample", "knowledge", "harness"), ("knowledge", "sample", "knowledge")):
        workspaces[name].mkdir()
        project_dir = native / f".{name}"
        records = project_dir / "worktrees"
        records.mkdir(parents=True)
        (records / f"wt-{name}.yaml").write_text(yaml.safe_dump({
            "worktree_id": f"wt-{name}", "branch": f"worktree/wt-{name}",
            "worktree_path": str(workspaces[name]), "repo": name, "machine": "fixture",
            "platform": platform, "status": "active", "started_at": "2026-01-01T00:00:00",
            "last_resumed_at": "2026-01-01T00:00:00", "resume_count": 0, "sessions": [],
            "pair_id": "fixture-pair", "pair_role": role,
            "pair_ref": f"fixture/{sibling}/wt-{sibling}", "pair_kind": "worktree",
        }), encoding="utf-8")
    (native / ".sample" / "config.yaml").write_text("knowledge_repo: knowledge\n", encoding="utf-8")
    manifest_path = root / "plugins" / "agent-worktrees" / "pivots" / "repositories-config.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    prefix = [owner_python, "-m", "agent_worktrees"]
    owner_probe = subprocess.run(
        [owner_python, "-c",
         "import json,sys; from agent_worktrees import config as cfg,tracking; "
         "record=tracking.load_record(cfg.project_dir('sample')/'worktrees'/'wt-sample.yaml'); "
         "print(json.dumps({'prefix':sys.prefix,'workspace':record.worktree_path}))"],
        capture_output=True, text=True, timeout=15, env=tasks._child_process_env(),
        **no_window_kwargs(),
    )
    assert owner_probe.returncode == 0, (owner_python, owner_probe.stdout, owner_probe.stderr)
    assert Path(json.loads(owner_probe.stdout)["prefix"]) == Path(owner_python).parent.parent
    assert json.loads(owner_probe.stdout)["workspace"] == str(workspaces["sample"])
    assert tasks._resolve_command(prefix)[0] == owner_python
    bootstrap = subprocess.run(
        [*prefix, "--project", "sample", "picker-bootstrap", "--json"],
        capture_output=True, text=True, timeout=15, cwd=workspaces["sample"],
        env=tasks._child_process_env(), **no_window_kwargs(),
    )
    assert bootstrap.returncode == 0, bootstrap.stderr
    binding = context.bind_project_bootstrap(json.loads(bootstrap.stdout))
    assert binding.workspace == str(workspaces["sample"])
    assert binding.workspace != binding.cwd
    manifest["list"] = [*prefix, *manifest["list"][1:]]
    for action in manifest["actions"]:
        for key in ("run", "preview"):
            if key in action:
                action[key] = [*prefix, *action[key][1:]]
    pivot_root = tmp_path / "pivots"
    pivot_root.mkdir()
    (pivot_root / "repositories.json").write_text(json.dumps(manifest), encoding="utf-8")
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    monkeypatch.setenv("AGENT_WORKTREES_PIVOTS_DIR", str(pivot_root))
    monkeypatch.setenv("AGENT_WORKTREES_PLUGINS_DIR", str(plugin_root))

    def ordinary_write():
        result = subprocess.run(
            [*prefix, "repos", "account", "set", "example", "concurrent"],
            capture_output=True, text=True, timeout=15, env=tasks._child_process_env(),
            **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr

    def edit(engine, operation):
        reg = engine._reg_pivot()
        row = next(row for row in engine._task_rows() if row["id"] == "sample")
        action = next(action for action in reg.actions if action.key == operation)
        engine._open_pivot_form(reg, action, row, engine._task_action_ctx(reg, row), row["title"])

    async def proposed(app, pilot, value=None):
        await pilot.pause()
        assert isinstance(app.screen, ConfigurationFormScreen)
        if value is not None:
            app.screen._q[0]["primary"].value = value
        else:
            assert app.screen._collect() == {}
        app.screen.dismiss(app.screen._collect())
        await _wait(pilot, lambda: isinstance(app.screen, ConfigurationPreviewScreen)
                    and not app.screen.query_one("#config-preview-retry", Button).disabled)
        assert app.screen.preview and not app.screen.preview.blocked, str(
            app.screen.query_one("#config-preview").render()
        )
        assert SECRET not in app.screen.preview.describe()

    async def receipt(app, pilot):
        await pilot.click("#config-preview-apply")
        await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen) and not app.screen.pending)
        rendered = str(app.screen.query_one("#config-result").render())
        assert SECRET not in rendered
        return rendered

    async def run():
        before = snapshot(native)
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            engine = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, engine)
            engine._open_cfgmenu()
            while engine.htabs[engine.htab] != "Repositories":
                engine._rotate_machine(1)
                await pilot.pause()
            await _wait(pilot, lambda: engine._task_state()[0] in ("ready", "error"))
            assert engine._task_state()[0] == "ready", engine._task_state()
            sample = next(row for row in engine._task_rows() if row["id"] == "sample")
            assert sample["configuration"]["target"]["resource"] == "sample/sample"
            pair = sample["configuration"]["pair"]
            assert pair["status"] == "present" and pair["writable_workspace"] is True
            assert pair["workspace"] == str(workspaces["knowledge"])
            assert snapshot(native) == before
            edit(engine, "set")
            await proposed(app, pilot, "chosen")
            assert app.focused.id == "config-preview-cancel"
            await pilot.press("enter")
            await pilot.pause()
            assert snapshot(native) == before
            edit(engine, "set")
            await proposed(app, pilot, "chosen")
            assert '"status": "persisted"' in await receipt(app, pilot)
            after = snapshot(native)
            changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
            assert changed == {
                str(Path(".agent-worktrees") / "repos.yaml"),
                str(Path(".agent-worktrees") / "repos.lock"),
                str(Path(".agent-worktrees") / "pr-rewrite-authority.lock"),
            }
            app.screen.action_close()
            await _wait(pilot, lambda: any(row["id"] == "sample" and row["effective"] == "chosen"
                                          for row in engine._task_rows()))
            (registry / "config.yaml").write_text("default_copilot_account: changed-inheritance\n", encoding="utf-8")
            edit(engine, "reset")
            await proposed(app, pilot)
            assert "changed-inheritance" in app.screen.preview.describe()
            assert '"status": "persisted"' in await receipt(app, pilot)
            raw = yaml.safe_load((registry / "repos.yaml").read_text(encoding="utf-8"))
            assert "copilot_account" not in raw["repos"]["sample"]
            app.screen.action_close()
            await _wait(pilot, lambda: any(
                row["id"] == "sample" and row["effective"] == "changed-inheritance"
                for row in engine._task_rows()
            ))
            edit(engine, "set")
            await proposed(app, pilot, "stale-choice")
            await asyncio.to_thread(ordinary_write)
            concurrent = snapshot(native)
            assert "preview-conflict" in await receipt(app, pilot)
            assert snapshot(native) == concurrent
            for name in ("repository", "knowledge", "plugin", "preset"):
                assert snapshot(native / name) == {"config.yaml": (native / name / "config.yaml").read_bytes()}
                assert (native / name / "config.yaml").read_bytes() == before[str(Path(name) / "config.yaml")]
            assert manifest_path.read_bytes() == manifest_bytes

    asyncio.run(run())


def test_native_local_knowledge_registration_form_cancel_confirm_and_refresh(tmp_path, monkeypatch):
    owner_python = os.environ.get("REPOSITORIES_CONFIG_TEST_PYTHON")
    if not owner_python:
        pytest.skip("set REPOSITORIES_CONFIG_TEST_PYTHON to the isolated native engine interpreter")
    native = tmp_path / "native"
    harness, knowledge = native / "repository", native / "knowledge"
    harness.mkdir(parents=True)
    result = subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "--quiet", str(knowledge)],
        capture_output=True, text=True, timeout=5, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    policy = harness / ".agent-worktrees"
    policy.mkdir()
    (policy / "config.yaml").write_text(
        "stateless: true\nrequires_external_state_root: true\n", encoding="utf-8",
    )
    registry = native / ".agent-worktrees"
    registry.mkdir()
    platform = "windows" if os.name == "nt" else "linux"
    (registry / "repos.yaml").write_text(yaml.safe_dump({
        "unrecognized_owner_metadata": {"secret": SECRET},
        "repos": {"sample": {"class": "worktree", platform: str(harness)}},
    }), encoding="utf-8")
    monkeypatch.setenv("AGENT_HOME", str(native))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(context, "_project", "sample")
    monkeypatch.setattr(context, "_bootstrap", None)
    root = Path(__file__).resolve().parents[3]
    manifest_path = root / "plugins" / "agent-worktrees" / "pivots" / "repositories-config.json"
    manifest = json.loads(manifest_path.read_bytes())
    prefix = [owner_python, "-m", "agent_worktrees"]
    manifest["list"] = [*prefix, *manifest["list"][1:]]
    for action in manifest["actions"]:
        for key in ("run", "preview"):
            if key in action:
                action[key] = [*prefix, *action[key][1:]]
    pivot_root, plugin_root = tmp_path / "pivots", tmp_path / "plugins"
    pivot_root.mkdir()
    plugin_root.mkdir()
    (pivot_root / "repositories.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("AGENT_WORKTREES_PIVOTS_DIR", str(pivot_root))
    monkeypatch.setenv("AGENT_WORKTREES_PLUGINS_DIR", str(plugin_root))

    async def propose(app, engine, pilot):
        from worktree_manager.production_picker.picker_tui.pivot_actions import entry_matches

        reg = engine._reg_pivot()
        row = next(row for row in engine._task_rows() if row.get("knowledge_registration_available") == "true")
        action = next(action for action in reg.actions if action.key == "register-local-knowledge")
        assert entry_matches(action.when, row)
        assert not entry_matches(action.when, {"id": "sample", "knowledge_bindable": "true"})
        engine._open_pivot_form(reg, action, row, engine._task_action_ctx(reg, row), row["title"])
        await pilot.pause()
        assert isinstance(app.screen, ConfigurationFormScreen)
        for field, value in zip(app.screen._q, ("chosen-knowledge", str(knowledge), "main", "")):
            field["primary"].value = value
        app.screen.dismiss(app.screen._collect())
        await _wait(pilot, lambda: isinstance(app.screen, ConfigurationPreviewScreen)
                    and not app.screen.query_one("#config-preview-retry", Button).disabled)
        assert app.screen.preview and not app.screen.preview.blocked
        assert SECRET not in app.screen.preview.describe()

    async def run():
        before = snapshot(native)
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            engine = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, engine)
            engine._open_cfgmenu()
            while engine.htabs[engine.htab] != "Repositories":
                engine._rotate_machine(1)
                await pilot.pause()
            await _wait(pilot, lambda: engine._task_state()[0] in ("ready", "error"))
            assert engine._task_state()[0] == "ready", engine._task_state()
            await propose(app, engine, pilot)
            assert app.focused.id == "config-preview-cancel"
            await pilot.press("enter")
            await pilot.pause()
            assert snapshot(native) == before
            await propose(app, engine, pilot)
            await pilot.click("#config-preview-apply")
            await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen) and not app.screen.pending)
            rendered = str(app.screen.query_one("#config-result").render())
            assert '"status": "persisted"' in rendered and SECRET not in rendered
            app.screen.action_close()
            await _wait(pilot, lambda: any(row["id"] == "chosen-knowledge" for row in engine._task_rows()))
            row = next(row for row in engine._task_rows() if row["id"] == "chosen-knowledge")
            assert row["configuration"]["registration"]["class"] == "knowledge"
            assert row["configuration"]["binding"]["identity"] == ""
            after = snapshot(native)
            missing = object()
            assert {
                key for key in before.keys() | after.keys() if before.get(key, missing) != after.get(key, missing)
            } == {
                str(Path(".agent-worktrees") / "repos.yaml"), str(Path(".agent-worktrees") / "repos.lock"),
                str(Path(".agent-worktrees") / "pr-rewrite-authority.lock"),
                str(Path(".agent-worktrees") / "guarded-anchors"),
                str(Path(".agent-worktrees") / "guarded-anchors" / "publication.lock"),
            }

    asyncio.run(run())


@pytest.fixture
def readiness_engine(monkeypatch, tmp_path):
    from worktree_manager import engine_client, resident_picker

    monkeypatch.setattr(context, "_project", "sample")
    monkeypatch.setattr(context, "_bootstrap", None)
    monkeypatch.setattr(resident_picker, "root_for", lambda _project: None)
    scalars = {
        "config-dir": str(tmp_path / "config"), "repo-dir": str(tmp_path / "repo"),
        "install-dir": str(tmp_path / "install"), "machine": "anomalous-potato",
    }
    monkeypatch.setattr(engine_client, "get_value", lambda _project, key, **_kwargs: scalars.get(key, ""))


@pytest.mark.parametrize("observation", [
    {"version": 1, "ready": False, "code": "knowledge_binding_required"},
    {"version": 1, "ready": False, "code": "state_root_resolution_failed"},
    {"version": 1, "ready": True, "code": "state_root_resolution_failed"},
    {"error": "owner read failed"},
])
@pytest.mark.parametrize("live", [False, True])
def test_worktrees_readiness_is_async_visible_and_never_runs_a_binding(monkeypatch, observation, live, readiness_engine):
    from worktree_manager import engine_client, resident_picker
    from worktree_manager.production_picker import runner
    from worktree_manager.production_picker.picker_tui import data_local, data_ssh

    observation = dict(observation)
    calls = []
    monkeypatch.setattr(context, "_project", "sample")
    monkeypatch.setattr(context, "_bootstrap", None)
    monkeypatch.setattr(resident_picker, "root_for", lambda _project: None)

    def read(project, args, **kwargs):
        if args == ["coordination-readiness"]:
            calls.append((project, args, kwargs))
            return dict(observation)
        return {}

    monkeypatch.setattr(engine_client, "run_json", read)

    def select(**kwargs):
        binding = kwargs["prepare"]()
        assert binding.live is live
        return binding.source

    monkeypatch.setattr(runner, "run_tui_picker", select)
    source = runner.run("sample", mock_mode=True, bootstrap_supplier=lambda: (
        "sample", {"project": "sample", "default_live": live},
    ))
    assert source is (data_ssh if live else data_local)
    for name, value in vars(_fixture_source()).items():
        monkeypatch.setattr(source, name, value)

    async def run():
        app = PickerApp(source, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            engine = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, engine)
            await _wait(pilot, lambda: getattr(engine, "_knowledge_readiness", {}).get("code") not in (None, "loading"))
            assert calls == [("sample", ["coordination-readiness"], {"timeout": 20, "allow_nonzero": True})]
            assert engine._knowledge_readiness["ready"] is not True
            needs_configuration = observation.get("ready") is False and observation.get("code") in {
                "knowledge_binding_required", "state_root_resolution_failed",
            }
            assert ("KB" in engine.button_set()) is needs_configuration
            assert "N" in engine.button_set()
            if needs_configuration:
                label = (
                    "Inspect knowledge" if observation["code"] == "state_root_resolution_failed"
                    else "Configure knowledge"
                )
                assert label in engine.new_worktree_row(118, False, 0).plain
            text = "\n".join(row.text.plain for row in engine.build_body(118))
            assert "Knowledge" in text
            engine._open_knowledge_configuration()
            assert "unavailable" in engine.debug
            assert len(calls) == 1
            observation.update(version=1, ready=True, code="ready")
            engine._refresh_knowledge_readiness()
            await _wait(pilot, lambda: engine._knowledge_readiness.get("ready") is True)
            assert "KB" not in engine.button_set() and len(calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize("failure", [
    {"error": "fixture readiness read failed"},
    TimeoutError("fixture readiness timeout"),
], ids=["invalid-response", "timeout"])
def test_unknown_readiness_is_not_missing_setup_and_reload_retries(monkeypatch, failure, readiness_engine):
    entered, release = threading.Event(), threading.Event()
    source = _fixture_source()
    reads = []
    monkeypatch.setattr(
        "worktree_manager.engine_client.run_json", lambda *_args, **_kwargs: {},
    )

    def read():
        reads.append("read")
        if len(reads) == 1:
            entered.set()
            if not release.wait(5):
                raise TimeoutError("fixture release expired")
            if isinstance(failure, Exception):
                raise failure
            return failure
        return {"version": 1, "ready": True, "code": "ready"}

    source.knowledge_readiness = read

    async def run():
        app = PickerApp(source, live=False, mock_mode=True)
        async with app.run_test(size=(118, 40)) as pilot:
            engine = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, engine)
            await _wait(pilot, entered.is_set)
            engine.machine_idx = engine.local_index()
            engine.sel = ("BTN", 0)
            engine.btn_idx = engine.button_set().index("SY")
            try:
                assert engine._knowledge_readiness["code"] == "loading"
                assert "KB" not in engine.button_set()
            finally:
                release.set()
            await _wait(pilot, lambda: engine._knowledge_readiness.get("code") == "unavailable")
            assert engine._knowledge_readiness["ready"] is None
            assert "KB" not in engine.button_set()
            assert engine.active_button() == "SY"
            notice = engine.query_one("#nf-notices").render().plain
            assert "press r to retry" in notice.lower()
            assert "binding is required" not in notice.lower()
            await pilot.pause()
            assert reads == ["read"], "render ticks must not poll readiness"
            monkeypatch.setattr(engine, "_poll_orphan_state", lambda **_kwargs: None)
            await pilot.press("r")
            await _wait(pilot, lambda: engine._knowledge_readiness.get("ready") is True)
            assert reads == ["read", "read"]
            assert "KB" not in engine.button_set()
            await pilot.pause()
            assert "Knowledge" not in engine.query_one("#nf-notices").render().plain

    asyncio.run(run())


@pytest.mark.parametrize("next_ready", [False, True, None])
def test_readiness_refresh_keeps_button_identity_until_observation_completes(monkeypatch, next_ready, readiness_engine):
    entered, release = threading.Event(), threading.Event()
    reads = []
    source = _fixture_source()

    def read():
        reads.append("read")
        ready = True
        if len(reads) > 1:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("readiness fixture release expired")
            ready = next_ready
        if ready is None:
            return {"error": "fixture readiness read failed"}
        return {"version": 1, "ready": ready, "code": "ready" if ready else "knowledge_binding_required",
                "observation": len(reads)}

    source.knowledge_readiness = read

    async def run():
        app = PickerApp(source, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            engine = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, engine)
            await _wait(pilot, lambda: getattr(engine, "_knowledge_readiness", {}).get("ready") is True)
            engine.sel = ("BTN", 0)
            engine.btn_idx = engine.button_set().index("SY")
            engine._sync_focus_to_sel()
            engine.refresh()
            await pilot.pause()
            buttons = engine.button_set()
            try:
                engine._refresh_knowledge_readiness()
                await _wait(pilot, entered.is_set)
                assert engine.button_set() == buttons and engine.active_button() == "SY"
                await pilot.press("left", "right")
                assert engine.active_button() == "SY"
            finally:
                release.set()
            await _wait(pilot, lambda: (
                engine._knowledge_readiness.get("code") == "unavailable" if next_ready is None
                else engine._knowledge_readiness.get("observation") == 2
            ))
            assert engine.active_button() == ("N" if next_ready is False else "SY")
            assert ("KB" in engine.button_set()) is (next_ready is False)

    asyncio.run(run())


def test_remote_knowledge_case_skips_before_preparing_native_fixtures(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOSITORIES_CONFIG_TEST_PYTHON", raising=False)

    class Request:
        def getfixturevalue(self, name):
            pytest.fail("Native prerequisites must be checked before preparing HTTPS/Git fixtures.")

    with pytest.raises(pytest.skip.Exception):
        test_missing_knowledge_to_confirmed_native_binding_and_new(
            tmp_path, monkeypatch, Request(), empty=False, remote=True,
        )


@pytest.mark.parametrize(("empty", "remote"), [
    pytest.param(False, False, marks=pytest.mark.timeout(120)),
    pytest.param(True, False, marks=pytest.mark.timeout(120)),
    pytest.param(False, True, marks=pytest.mark.timeout(120)),
    pytest.param(False, "created", marks=pytest.mark.timeout(180), id="created"),
])
def test_missing_knowledge_to_confirmed_native_binding_and_new(tmp_path, monkeypatch, request, empty, remote):
    started = time.monotonic()

    def stage(label):
        if remote == "created":
            print(f"creation consumer: {label} at {time.monotonic() - started:.2f}s", flush=True)

    stage("starting")
    owner_python = os.environ.get("REPOSITORIES_CONFIG_TEST_PYTHON")
    if not owner_python:
        pytest.skip("set REPOSITORIES_CONFIG_TEST_PYTHON to the isolated native engine interpreter")
    https = request.getfixturevalue("public_https_knowledge") if remote else None
    from worktree_manager import engine_client
    from worktree_manager.production_picker.picker_tui import data_local

    root = Path(__file__).resolve().parents[3]
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(context, "_project", "sample")
    monkeypatch.setattr(context, "_bootstrap", None)
    prefix = [owner_python, "-m", "agent_worktrees"]
    monkeypatch.setattr(engine_client, "_ENGINE_CMD_OVERRIDE", prefix)
    platform = "windows" if os.name == "nt" else "linux"

    def native(*args, cwd=None):
        return subprocess.run(
            [*prefix, "--project", "sample", *args], cwd=cwd,
            capture_output=True, text=True, timeout=25,
            env=tasks._child_process_env(), **no_window_kwargs(),
        )

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value), encoding="utf-8")

    paths = {name: tmp_path / name for name in ("sample", "knowledge")}
    for name, path in paths.items():
        if remote and name == "knowledge":
            continue
        path.mkdir()
        for arguments in (
            ["init", "-b", "main", str(path)],
            ["-C", str(path), "config", "user.email", "example@example.com"],
            ["-C", str(path), "config", "user.name", "Example"],
        ):
            result = subprocess.run(["git", *arguments], capture_output=True, text=True,
                                    timeout=10, **no_window_kwargs())
            assert result.returncode == 0, result.stderr
        (path / "README").write_text("Isolated binding control\n", encoding="utf-8")
        if name == "sample":
            write(path / ".agent-worktrees" / "config.yaml", {
                "repo_name": "sample", "stateless": True, "requires_external_state_root": True,
            })
            settings = path / ".github" / "copilot" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({
                "enabledPlugins": {"harness-knowledge@copilot-extensions": True},
                "extraKnownMarketplaces": {"copilot-extensions": {
                    "source": {"source": "directory", "path": str(root)},
                }},
            }), encoding="utf-8")
        for arguments in (["add", "."], ["commit", "-m", "Isolated baseline"]):
            result = subprocess.run(["git", "-C", str(path), *arguments],
                                    capture_output=True, text=True, timeout=10, **no_window_kwargs())
            assert result.returncode == 0, result.stderr
    write(home / ".agent-worktrees" / "repos.yaml", {
        **({"account_map": {"selected": "selected"}} if remote == "created" else {}),
        "repos": {
        name: {"class": "worktree" if name == "sample" else "knowledge",
               platform: str(path), "default_branch": "main"}
        for name, path in paths.items()
        if not remote or name != "knowledge"
    }})
    write(home / ".agent-worktrees" / "projects.yaml", {
        "schema_version": 2, "projects": {"sample": {"config_dir": str(home / ".sample")}},
    })
    config = home / ".sample" / "config.yaml"
    write(config, {
        "repo_name": "sample", "machine": "fixture",
        "repos": {"sample": {"anchor": str(paths["sample"]), "worktree_root": str(tmp_path / "worktrees")}},
    })
    (home / "credential-sentinel").write_text(SECRET, encoding="utf-8")
    git_configuration = Path(os.environ["GIT_CONFIG_GLOBAL"]) if remote == "created" else None
    git_configuration_bytes = git_configuration.read_bytes() if git_configuration else None
    blocked = native("resolve", "--json", "--new", cwd=paths["sample"])
    assert blocked.returncode != 0 and not (tmp_path / "worktrees").exists()
    settings = paths["sample"] / ".github" / "copilot" / "settings.json"
    activation = json.loads(settings.read_text("utf-8"))
    activation["enabledPlugins"]["harness-knowledge@copilot-extensions"] = False
    settings.write_text(json.dumps(activation), encoding="utf-8")
    raw = json.loads((root / "plugins" / "agent-worktrees" / "pivots" / "repositories-config.json").read_text("utf-8"))
    raw["list"] = [*prefix, *raw["list"][1:]]
    for action in raw["actions"]:
        for key in ("run", "preview"):
            if key in action:
                action[key] = [*prefix, *action[key][1:]]
    reg = replace(pivots.parse_manifest(raw, name="repositories-config", source_path="fixture"),
                  source_owner="agent-worktrees@fixture")
    monkeypatch.setattr(PickerScreen, "_scan_pivot_payload", staticmethod(lambda: (
        [reg], [{"label": "Worktrees", "kind": "worktrees", "pivot": None},
                {"label": "Repositories", "kind": "registered", "pivot": reg}], [], [],
    )))
    source = _fixture_source()
    if empty:
        source.load = lambda: []
    source.knowledge_readiness = data_local.knowledge_readiness

    async def run():
        before = snapshot(home), snapshot(paths["sample"]), snapshot(paths["knowledge"])
        app = PickerApp(source, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            engine = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, engine)
            await _wait(pilot, lambda: getattr(engine, "_knowledge_readiness", {}).get("code") not in (None, "loading"))
            assert engine._knowledge_readiness.get("ready") is False, engine._knowledge_readiness
            assert "KB" in engine.button_set()
            engine.btn_idx = engine.button_set().index("KB")
            engine.sel = ("BTN", 0)
            await pilot.press("enter")
            await _wait(pilot, lambda: engine._task_state()[0] in ("ready", "error"))
            assert engine._reg_pivot() is reg and engine._task_state()[0] == "ready"
            assert all(row["knowledge_bindable"] == "false" for row in engine._task_rows())
            text = "\n".join(line.text.plain for line in engine.build_body(118))
            assert "Knowledge binding is unavailable" in text and "Enable and prepare" in text
            assert (snapshot(home), snapshot(paths["sample"]), snapshot(paths["knowledge"])) == before
            activation["enabledPlugins"]["harness-knowledge@copilot-extensions"] = True
            settings.write_text(json.dumps(activation), encoding="utf-8")
            before = snapshot(home), snapshot(paths["sample"]), snapshot(paths["knowledge"])
            engine._pivot_runtime(reg).repoll(engine._pivot_scope_key())
            if remote:
                from worktree_manager.production_picker.picker_tui.pivot_actions import entry_matches

                await _wait(pilot, lambda: any(
                    row.get("knowledge_acquisition_available") == "true" for row in engine._task_rows()
                ))

                async def propose_action(action_key, marker, values):
                    stage("preview " + action_key)
                    await _wait(pilot, lambda: engine._pivot_runtime(reg).actions_ready(("",)))
                    row = next(row for row in engine._task_rows() if row.get(marker) == "true")
                    action = next(action for action in reg.actions if action.key == action_key)
                    assert entry_matches(action.when, row)
                    assert not entry_matches(action.when, {"id": "sample", "editable": "true"})
                    engine._open_pivot_form(reg, action, row, engine._task_action_ctx(reg, row), row["title"])
                    await pilot.pause()
                    assert isinstance(app.screen, ConfigurationFormScreen)
                    for field, value in zip(app.screen._q, values):
                        field["primary"].value = value
                    app.screen.dismiss(app.screen._collect())
                    await _wait(pilot, lambda: isinstance(app.screen, ConfigurationPreviewScreen)
                                and not app.screen.query_one("#config-preview-retry", Button).disabled)
                    assert app.screen.preview and not app.screen.preview.blocked, str(
                        app.screen.query_one("#config-preview").render()
                    )

                acquisition_args = ("acquire-knowledge", "knowledge_acquisition_available",
                                    (https[0], str(paths["knowledge"])))
                if remote == "created":
                    provider = https[1]["provider"]

                    async def apply_remote():
                        await pilot.click("#config-preview-apply")
                        await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen)
                                    and not app.screen.pending)
                        rendered = str(app.screen.query_one("#config-result").render())
                        assert SECRET not in rendered and "fixture-token" not in rendered
                        return rendered

                    await propose_action("create-knowledge", "knowledge_creation_available", (CREATED_URL,))
                    assert app.screen.preview.operation == "create"
                    assert app.screen.preview.scope == "remote-repository"
                    assert app.focused.id == "config-preview-cancel"
                    await pilot.press("enter")
                    await pilot.pause()
                    assert not json.loads(provider.read_text())["posts"]
                    assert (snapshot(home), snapshot(paths["sample"]), snapshot(paths["knowledge"])) == before
                    await propose_action("create-knowledge", "knowledge_creation_available", (CREATED_URL,))
                    result = await apply_remote()
                    assert '"status": "incomplete"' in result and '"creation": "confirmed"' in result, result
                    assert '"http_status": 403' in result and '"binding_changed": false' in result
                    stage("creation confirmed / safeguards incomplete")
                    attempt = next((home / ".sample" / "knowledge-creation").glob("*.json"))
                    reference = json.loads(attempt.read_text())["receipt"]
                    assert len(json.loads(provider.read_text())["posts"]) == 2
                    app.screen.action_close()
                    await _wait(pilot, lambda: engine._pivot_runtime(reg).actions_ready(("",)))
                    recovery = next(row for row in engine._task_rows()
                                    if row.get("knowledge_creation_recovery_available") == "true")
                    action = next(action for action in reg.actions if action.key == "inspect-knowledge-creation")
                    engine._open_pivot_form(
                        reg, action, recovery, engine._task_action_ctx(reg, recovery), recovery["title"],
                    )
                    await pilot.pause()
                    for field, value in zip(app.screen._q, (CREATED_URL, reference)):
                        field["primary"].value = value
                    inspected_before = snapshot(home), provider.read_bytes()
                    app.screen.dismiss(app.screen._collect())
                    await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen)
                                and not app.screen.pending)
                    result = str(app.screen.query_one("#config-result").render())
                    assert '"observation_status": "observed"' in result and '"status": "incomplete"' in result
                    assert (snapshot(home), provider.read_bytes()) == inspected_before
                    stage("read-only inspection complete")
                    app.screen.action_close()
                    await _wait(pilot, lambda: engine._task_state()[0] == "ready")
                    protect_args = ("protect-knowledge", "knowledge_creation_recovery_available",
                                    (CREATED_URL, reference))
                    await propose_action(*protect_args)
                    assert app.screen.preview.operation == "protect-default"
                    assert app.focused.id == "config-preview-cancel"
                    await pilot.press("enter")
                    await pilot.pause()
                    assert len(json.loads(provider.read_text())["posts"]) == 2
                    state = json.loads(provider.read_text())
                    state["failure"] = ""
                    provider.write_text(json.dumps(state), encoding="utf-8")
                    await propose_action(*protect_args)
                    assert '"status": "protected"' in await apply_remote()
                    stage("safeguards verified")
                    assert len(json.loads(provider.read_text())["posts"]) == 3
                    assert engine._knowledge_readiness["ready"] is False
                    assert not paths["knowledge"].exists()
                    protected = snapshot(home)
                    for key, value in before[0].items():
                        assert protected[key] == value
                    assert protected.keys() - before[0].keys() <= {
                        str(Path(".sample") / "knowledge-creation"),
                        str(attempt.relative_to(home)),
                        str(Path(".agent-worktrees") / "repos.lock"),
                        str(Path(".agent-worktrees") / "pr-rewrite-authority.lock"),
                    }
                    app.screen.action_close()
                    await _wait(pilot, lambda: engine._task_state()[0] == "ready")
                    before = snapshot(home), snapshot(paths["sample"]), snapshot(paths["knowledge"])
                if remote != "created":
                    await propose_action(*acquisition_args)
                    assert app.focused.id == "config-preview-cancel" and https[1]["requests"] == 0
                    await pilot.press("enter")
                    await pilot.pause()
                    assert (snapshot(home), snapshot(paths["sample"]), snapshot(paths["knowledge"])) == before
                await propose_action(*acquisition_args)
                await pilot.click("#config-preview-apply")
                await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen) and not app.screen.pending)
                receipt = str(app.screen.query_one("#config-result").render())
                assert '"status": "acquired"' in receipt and '"registration_changed": false' in receipt, receipt
                stage("checkout acquired")
                assert https[1]["requests"] > 0 and paths["knowledge"].is_dir()
                if remote == "created":
                    assert "get" in json.loads(https[1]["provider"].read_text())["git_credentials"]
                    assert https[1]["authentication"] == {"credential-helper"}
                assert not (tmp_path / "unexpected-setup").exists()
                after = snapshot(home)
                missing = object()
                assert {
                    key for key in before[0].keys() | after.keys()
                    if before[0].get(key, missing) != after.get(key, missing)
                } <= {
                    str(Path(".agent-worktrees") / "repos.lock"),
                    str(Path(".agent-worktrees") / "pr-rewrite-authority.lock"),
                }
                assert snapshot(paths["sample"]) == before[1]
                app.screen.action_close()
                await _wait(pilot, lambda: engine._task_state()[0] == "ready")
                assert not any(row["id"] == "knowledge" for row in engine._task_rows())
                await propose_action(
                    "register-local-knowledge", "knowledge_registration_available",
                    ("knowledge", str(paths["knowledge"]), "provider-default" if remote == "created" else "main",
                     "selected" if remote == "created" else ""),
                )
                assert app.focused.id == "config-preview-cancel"
                await pilot.click("#config-preview-apply")
                await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen) and not app.screen.pending)
                receipt = str(app.screen.query_one("#config-result").render())
                assert '"status": "persisted"' in receipt and '"binding_changed": false' in receipt, receipt
                stage("checkout registered")
                app.screen.action_close()
                await _wait(pilot, lambda: any(row["id"] == "knowledge" for row in engine._task_rows()))
                before = snapshot(home), snapshot(paths["sample"]), snapshot(paths["knowledge"])
            await _wait(pilot, lambda: any(
                row["id"] == "knowledge" and row["knowledge_bindable"] == "true"
                for row in engine._task_rows()
            ))
            row = next(row for row in engine._task_rows() if row["id"] == "knowledge")
            assert row["knowledge_bindable"] == "true", (row["configuration"], engine._task_summary())
            action = next(action for action in reg.actions if action.key == "bind-knowledge")

            async def preview():
                stage("preview binding")
                engine._open_pivot_form(reg, action, row, engine._task_action_ctx(reg, row), row["title"])
                await pilot.pause()
                assert isinstance(app.screen, ConfigurationFormScreen)
                app.screen.dismiss(app.screen._collect())
                await _wait(pilot, lambda: isinstance(app.screen, ConfigurationPreviewScreen)
                            and not app.screen.query_one("#config-preview-retry", Button).disabled)
                assert app.screen.preview is not None, str(app.screen.query_one("#config-preview").render())
                assert not app.screen.preview.blocked, app.screen.preview.describe()
                stage("binding preview complete")

            if remote != "created":
                await preview()
                assert app.focused.id == "config-preview-cancel"
                await pilot.press("enter")
                await pilot.pause()
                assert (snapshot(home), snapshot(paths["sample"]), snapshot(paths["knowledge"])) == before
            await preview()
            assert app.focused.id == "config-preview-cancel"
            stage("click binding Apply")
            await pilot.click("#config-preview-apply")
            stage("binding Apply click returned")
            await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen) and not app.screen.pending)
            receipt = str(app.screen.query_one("#config-result").render())
            assert '"status": "persisted"' in receipt, receipt
            stage("binding persisted")
            app.screen.action_close()
            await _wait(pilot, lambda: engine._knowledge_readiness.get("ready") is True)
            assert "KB" not in engine.button_set()
            if remote == "created":
                await _wait(pilot, lambda: engine._pivot_runtime(reg).actions_ready(("",)))
                assert any(row.get("knowledge_creation_recovery_available") == "true"
                           for row in engine._task_rows())
                assert not any(row.get("knowledge_creation_available") == "true"
                               for row in engine._task_rows())
                assert (home / "credential-sentinel").read_text("utf-8") == SECRET
                assert git_configuration.read_bytes() == git_configuration_bytes
                assert len(json.loads(https[1]["provider"].read_text())["posts"]) == 3
            assert engine._config_return[3] == 0
            engine._leave_configuration()
            await pilot.pause()
            assert engine._kind() == "worktrees" and engine.sel == ("BTN", 0)
            assert engine.btn_idx == 0 and engine.active_button() == "N"
            if empty:
                assert engine.button_set() == ["N"]
            assert yaml.safe_load(config.read_text("utf-8"))["knowledge_repo"] == "knowledge"
            assert snapshot(paths["knowledge"]) == before[2]
            # Binding cannot commit or alter either repository's source history.
            for path in paths.values():
                status = subprocess.run(["git", "-C", str(path), "status", "--porcelain"],
                                        capture_output=True, text=True, timeout=10, **no_window_kwargs())
                assert status.returncode == 0
                assert not status.stdout.replace("?? .github/copilot/settings.local.json\n", "").strip()

    asyncio.run(run())
    admitted = native("resolve", "--json", "--new", "--no-mux", cwd=paths["sample"])
    assert admitted.returncode == 0, admitted.stdout + admitted.stderr
    assert (tmp_path / "worktrees").is_dir()
    if remote == "created":
        assert https[1]["authentication"] == {"credential-helper", "configured-gh"}
    stage("New admitted")


def test_remote_acquisition_is_optional_for_prior_catalog_and_manifest_contracts(tmp_path, monkeypatch):
    from worktree_manager.production_picker.picker_tui.pivot_actions import entry_matches

    root = Path(__file__).resolve().parents[3]
    raw = json.loads((root / "plugins" / "agent-worktrees" / "pivots" / "repositories-config.json").read_text())
    current = pivots.parse_manifest(raw, name="repositories", source_path="fixture")
    acquisition = next(action for action in current.actions if action.key == "acquire-knowledge")
    for prior_row in (
        {"id": "sample", "editable": "true"},
        {"id": "action:knowledge-registration", "knowledge_registration_available": "true"},
        {"id": "knowledge", "knowledge_bindable": "true"},
    ):
        assert not entry_matches(acquisition.when, prior_row)
    assert entry_matches(acquisition.when, {"knowledge_acquisition_available": "true"})
    prior = pivots.parse_manifest(
        {**raw, "actions": [action for action in raw["actions"] if action["key"] != "acquire-knowledge"]},
        name="repositories", source_path="fixture",
    )
    assert "register-local-knowledge" in {action.key for action in prior.actions}
    assert "bind-knowledge" in {action.key for action in prior.actions}
    assert not any(action.key == "acquire-knowledge" for action in prior.actions)
    provider = tmp_path / "prior-provider.py"
    provider.write_text(
        "import argparse,json\n"
        "p=argparse.ArgumentParser(allow_abbrev=False)\n"
        "p.add_argument('command',choices=['catalog'])\n"
        "p.add_argument('--context-project',required=True)\n"
        "p.add_argument('--workspace',default='')\n"
        "a=p.parse_args()\n"
        "print(json.dumps({'entries':[{'id':'sample','title':'sample',"
        "'editable':'true','context_project':a.context_project,'repository':'sample'}]}))\n",
        encoding="utf-8",
    )
    raw["list"] = [sys.executable, str(provider), "catalog", "--context-project", "{repo}"]
    rollback = pivots.parse_manifest(raw, name="repositories", source_path="prior-provider")
    monkeypatch.setattr(context, "_project", "sample")
    monkeypatch.setattr(context, "_bootstrap", None)
    runtime = tasks.RegisteredPivotRuntime(rollback)
    runtime._run_list("")
    state, rows, error = runtime.get("")
    assert state == "ready", error
    available = {action.key for action in rollback.actions if entry_matches(action.when, rows[0])}
    assert "set" in available and "acquire-knowledge" not in available


def test_creation_consumer_withholds_old_catalog_and_refuses_provider_rollback_without_replay(tmp_path, monkeypatch):
    from worktree_manager.production_picker.picker_tui.pivot_actions import entry_matches, format_form_template

    root = Path(__file__).resolve().parents[3]
    raw = json.loads((root / "plugins" / "agent-worktrees" / "pivots" / "repositories-config.json").read_text())
    keys = {"create-knowledge", "inspect-knowledge-creation", "protect-knowledge"}
    prior = pivots.parse_manifest(
        {**raw, "actions": [action for action in raw["actions"] if action["key"] not in keys]},
        name="repositories", source_path="prior-manifest",
    )
    assert {"set", "reset", "acquire-knowledge", "register-local-knowledge", "bind-knowledge"} <= {
        action.key for action in prior.actions
    }
    provider = tmp_path / "prior-provider.py"
    provider.write_text(
        "import argparse,json,sys\n"
        "p=argparse.ArgumentParser(allow_abbrev=False)\n"
        "p.add_argument('command',choices=['catalog'])\n"
        "p.add_argument('--context-project',required=True)\n"
        "p.add_argument('--workspace',default='')\n"
        "a=p.parse_args(sys.argv[3:])\n"
        "print(json.dumps({'entries':[{'id':'sample','title':'sample','editable':'true',"
        "'context_project':a.context_project,'repository':'sample'}]}))\n",
        encoding="utf-8",
    )
    prefix = [sys.executable, str(provider)]
    raw["list"] = [*prefix, *raw["list"][1:]]
    for action in raw["actions"]:
        for key in ("run", "preview"):
            if key in action:
                action[key] = [*prefix, *action[key][1:]]
    current = pivots.parse_manifest(raw, name="repositories", source_path="prior-provider")
    monkeypatch.setattr(context, "_project", "sample")
    monkeypatch.setattr(context, "_bootstrap", None)
    runtime = tasks.RegisteredPivotRuntime(current)
    before = snapshot(tmp_path)
    runtime._run_list("")
    state, rows, error = runtime.get("")
    assert state == "ready", error
    available = {action.key for action in current.actions if entry_matches(action.when, rows[0])}
    assert "set" in available and not keys & available
    # A provider rollback after an old capable read must not trigger another operation.
    ctx = {"context_project": "sample", "configuration_preview": "a" * 64}
    fields = {"url": CREATED_URL, "receipt": "b" * 64}
    for action in current.actions:
        if action.key not in keys:
            continue
        if action.preview:
            ok, result = runtime.preview_resolved(format_form_template(action.preview, ctx, fields))
            assert not ok and "invalid choice" in result
        ok, result = runtime.run_resolved(
            format_form_template(action.run, ctx, fields), preserve_output=True,
        )
        assert not ok and "invalid choice" in result
    assert snapshot(tmp_path) == before


def test_created_binding_account_uses_the_selected_credential_cli_shim(tmp_path, monkeypatch, private_knowledge_provider):
    home = tmp_path / "home"
    registry = home / ".agent-worktrees"
    registry.mkdir(parents=True)
    (registry / "repos.yaml").write_text("account_map:\n  selected: selected\nrepos: {}\n", encoding="utf-8")
    for key in ("AGENT_HOME", "HOME", "USERPROFILE"):
        monkeypatch.setenv(key, str(home))
    result = subprocess.run(
        [os.environ["REPOSITORIES_CONFIG_TEST_PYTHON"], "-B", "-m", "agent_worktrees", "repos", "gh",
         "selected/knowledge", "--", "api", "user", "--jq", ".login"],
        capture_output=True, text=True, timeout=15, env=tasks._child_process_env(), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "selected"
