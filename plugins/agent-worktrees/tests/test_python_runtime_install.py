from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs


@pytest.fixture
def driver(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    # Track both first import and replacement so teardown restores the prior module.
    monkeypatch.setitem(sys.modules, "runtime_install_governance", None)
    monkeypatch.delitem(sys.modules, "runtime_install_governance")
    spec = importlib.util.spec_from_file_location("worktrees_python_runtime_install", scripts / "runtime_install.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_installed_identity", lambda slot: {
        "package_version": module.runtime.package_version(slot.name),
        "build_info": {"version": module.runtime.package_version(slot.name),
                       "runtime_generation": "test-generation"},
    })
    return module


def test_driver_governance_uses_current_scripts(driver):
    governance = sys.modules[driver.Governance.__module__]
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    assert Path(governance.__file__).resolve().parent == scripts
    assert governance.SCRIPTS == scripts


@pytest.mark.parametrize(("section", "field"), [
    ("state", key) for key in (
        "actualMode", "desiredMode", "status", "reason", "runtimeRoot",
        "context", "activationGeneration", "installGeneration", "allowMutation",
    )
] + [
    ("receipt", key) for key in (
        "marketplaceId", "namespaceGeneration", "generation", "pluginRoot",
    )
])
def test_governance_recheck_uses_signed_fields(driver, monkeypatch, section, field):
    governance = driver.Governance.__new__(driver.Governance)
    governance.initial = ({}, {
        key: "original" for key in (
            "marketplaceId", "namespaceGeneration", "generation", "pluginRoot",
        )
    })
    state, receipt = (dict(value) for value in governance.initial)
    monkeypatch.setattr(governance, "read", lambda: (state, receipt))
    governance.recheck()
    observed = state if section == "state" else receipt
    observed["untracked"] = "ignored"
    governance.recheck()
    observed[field] = "changed"
    diagnostic = "governance" if section == "state" else "receipt"
    with pytest.raises(driver.InstallationRefused, match=f"Installation {diagnostic} changed"):
        governance.recheck()


@pytest.fixture(scope="module")
def native_runtime_source(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, str]]:
    repository = Path(__file__).resolve().parents[3]
    plugin = repository / "plugins" / "agent-worktrees"
    owned = tmp_path_factory.mktemp("native-runtime-source")
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(plugin / "scripts"))
        spec = importlib.util.spec_from_file_location(
            "backend_fixture_engine", repository / "libs/installer-engine/installer_engine.py",
        )
        assert spec and spec.loader
        engine = importlib.util.module_from_spec(spec)
        patch.setitem(sys.modules, spec.name, engine)
        spec.loader.exec_module(engine)
        frozen = engine.freeze_inputs(plugin, owned / "repository")
    root = owned / "repository"
    hooks = owned / "empty-hooks"
    hooks.mkdir()
    git = shutil.which("git")
    assert git is not None, "real provenance qualification requires native Git"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    options = [
        git, "-C", str(root), "-c", f"core.hooksPath={hooks}",
        "-c", "core.autocrlf=false", "-c", "core.longpaths=true",
        "-c", "commit.gpgsign=false",
        "-c", "user.name=Runtime test", "-c", "user.email=runtime-test@example.invalid",
    ]

    def run(*arguments: str) -> str:
        return subprocess.check_output(
            [*options, *arguments], env=environment, text=True, **no_window_kwargs(),
        ).strip()

    run("init", "-q", "--template", str(hooks))
    # Preserve literal copied inputs even if a package carries text/filter attributes.
    (root / ".git/info").mkdir(exist_ok=True)
    (root / ".git/info/attributes").write_text(
        "* -text -filter -ident -working-tree-encoding\n", encoding="utf-8",
    )
    run("add", "--all", "--force", ".")
    run("commit", "-qm", "Frozen runtime dependency fixture")
    head = run("rev-parse", "HEAD")
    assert run("status", "--porcelain") == ""
    assert engine.runtime.fingerprint_source(engine._source_files(frozen.packages)) == frozen.payload_hash
    yield frozen.plugin, head
    assert engine.runtime.fingerprint_source(engine._source_files(frozen.packages)) == frozen.payload_hash
    assert run("rev-parse", "HEAD") == head
    assert run("status", "--porcelain", "--untracked-files=no") == ""


def _slot(driver, root, version):
    slot = root / "versions" / version
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"interpreter")
    driver.runtime.mark_complete(root, version, payload_hash="previous")
    return slot


@pytest.mark.parametrize("tampered", [False, True])
def test_source_deployment_uses_exact_verified_snapshot_before_construction(
    driver, tmp_path, monkeypatch, tampered,
):
    from types import SimpleNamespace

    root, plugin = tmp_path / "runtime", tmp_path / "payload"
    plugin.mkdir()
    (plugin / "plugin.json").write_text(
        '{"name":"agent-worktrees","version":"1.0.0"}', encoding="utf-8",
    )
    (plugin / "pyproject.toml").write_text(
        '[project]\nname="agent-worktrees"\nversion="1.0.0"\n', encoding="utf-8",
    )
    source = driver.runtime_source
    digest = source.assembly_hash(plugin)
    identity = source.SourceIdentity(
        "agent-worktrees", "1.0.0", "a" * 40, "b" * 40, digest,
        source.runtime_version("1.0.0", "a" * 40, digest),
    )
    (plugin / source.SOURCE_FILE).write_text(json.dumps(identity.document()), encoding="utf-8")
    if tampered:
        (plugin / "changed.py").write_text("VALUE = 2\n", encoding="utf-8")

    @contextmanager
    def held(**_kwargs):
        yield

    monkeypatch.setattr(driver, "Governance", lambda requested, payload: SimpleNamespace(
        root=requested, payload=payload, held=held,
    ))
    provenance = {}

    def attribute(payload, version, selected):
        assert payload == plugin and version == "1.0.0" and selected == identity
        return provenance

    class ConstructionReached(Exception):
        pass

    def construct(requested, version, **kwargs):
        assert not tampered
        assert requested == root and version == identity.runtime_version
        snapshot = root / "snapshots" / identity.runtime_version
        assert kwargs["plugin"] == snapshot
        assert source.read_source_identity(snapshot, "1.0.0") == identity
        assert kwargs["reuse_generations"] is False
        assert kwargs["construction_data"] is provenance
        raise ConstructionReached

    monkeypatch.setattr(driver, "_source_provenance", attribute)
    monkeypatch.setattr(driver.engine, "numbered_candidate", construct)
    error = source.SourceIdentityError if tampered else ConstructionReached
    with pytest.raises(error):
        driver.install(
            root, plugin, payload=plugin, uv=tmp_path / "unused",
            base_python=Path(sys.executable),
        )
    if tampered:
        assert not root.exists()


@pytest.mark.parametrize("rollback,interrupted", [
    (False, False), (True, False), (False, True), (True, True),
])
def test_one_owner_retains_candidate_and_rollback_until_actual_cutover(
    driver, tmp_path, monkeypatch, rollback, interrupted,
):
    from agent_worktrees import config as cfg, status_monitor_cutover as cutover

    root = tmp_path / "runtime"
    previous = _slot(driver, root, "1.0.0")
    driver.runtime.activate(root, "1.0.0", link_free=True)
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "scripts").mkdir()
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_source.py", "versioned_runtime_construction.py",
        "versioned_runtime_publication.py",
    ):
        shutil.copyfile(driver.SCRIPTS / name, plugin / "scripts" / name)
    (plugin / "pyproject.toml").write_text(
        '[project]\nname="agent-worktrees"\nversion="2.0.0"\n', encoding="utf-8",
    )
    authority = []

    class Governance:
        def __init__(self, requested, payload):
            self.root, self.payload = requested, payload
            self.manifest_publication = nullcontext

        def signature(self):
            return {}

        def recheck(self):
            pass

        @contextmanager
        def held(self, *, timeout=180):
            authority.append("enter")
            try:
                yield
            finally:
                authority.append("leave")

    class Guard:
        def release(self):
            authority.append("cutover-released")

    def construct(inputs, slot, *_args):
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir(parents=True)
        python.write_bytes(b"interpreter")
        return python

    def actual_transition(**arguments):
        assert arguments["cutover_guard"] is guard
        assert authority == ["enter", "leave", "enter"]
        assert driver.runtime.current_version(root) == "2.0.0"
        assert driver.runtime.gc(root) == []
        for version in ("1.0.0", "2.0.0"):
            with pytest.raises(driver.runtime.RetentionTimeout):
                with driver.runtime._kernel_lease(root, version, kind="retention", shared=False, timeout=0):
                    pytest.fail("rollback/candidate retention was released")
        with driver.runtime.construction_lease(root, "2.0.0", timeout=0):
            pass
        if interrupted:
            raise RuntimeError("cutover interrupted")
        return {"action": "cutover", "result": {"committed": not rollback, "rolled_back": rollback}}

    guard = Guard()

    class Process:
        returncode = 0

        def __init__(self, argv, **_kwargs):
            def argument(name):
                return argv[argv.index(name) + 1]

            try:
                result = driver.settle_candidate(
                    root, argument("--settle-slot"), payload=plugin,
                    payload_hash=argument("--payload-hash"), expected_governance={},
                    previous=argument("--previous-version"),
                )
            except RuntimeError:
                self.returncode = 1
            else:
                Path(argument("--result")).write_text(json.dumps(result), encoding="utf-8")

        def communicate(self):
            return "", ""

    monkeypatch.setattr(driver, "Governance", Governance)
    monkeypatch.setattr(driver, "_source_provenance", lambda *_args: {})
    monkeypatch.setattr(driver.engine, "_construct", construct)
    monkeypatch.setattr(
        driver.engine, "_retained_inputs", lambda inputs, *_args, **_kwargs: nullcontext(inputs),
    )
    monkeypatch.setattr(driver.engine, "_prepare_installed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(driver.engine, "_verify_installed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(driver, "_load_installed", lambda _slot: None)
    monkeypatch.setattr(driver, "_monitor_identity", lambda _root: None)
    monkeypatch.setattr(driver, "_candidate_serving", lambda *_args: False)
    monkeypatch.setattr(cfg, "install_dir", lambda: root)
    monkeypatch.setattr(cutover, "_acquire_cutover_lock", lambda _root: guard)
    monkeypatch.setattr(cutover, "activate_after_update", actual_transition)
    monkeypatch.setattr(driver, "_settle_monitor", lambda *_args: not rollback)
    monkeypatch.setattr(driver.subprocess, "Popen", Process)
    if rollback or interrupted:
        with pytest.raises(subprocess.CalledProcessError):
            driver.install(root, plugin, payload=plugin, uv=tmp_path / "unused", base_python=Path(sys.executable))
        expected = "1.0.0" if rollback else "2.0.0"
        assert driver.runtime.current_version(root) == expected
        assert driver.runtime.read_last_known_good(root) == expected
    else:
        result = driver.install(root, plugin, payload=plugin, uv=tmp_path / "unused", base_python=Path(sys.executable))
        assert result["runtime_version"] == "2.0.0"
        assert driver.runtime.current_version(root) == "2.0.0"
    assert previous.is_dir()
    assert authority == ["enter", "leave", "enter", "cutover-released", "leave"]
    for version in ("1.0.0", "2.0.0"):
        with driver.runtime._kernel_lease(root, version, kind="retention", shared=False, timeout=0):
            pass


def test_failed_passive_exit_is_observed_before_returning(driver, tmp_path, monkeypatch):
    events = []

    class Handle:
        pid = 123

        def poll(self):
            events.append("poll")
            return None if len(events) == 1 else 0

    monkeypatch.setattr(driver.time, "sleep", lambda _duration: events.append("wait"))
    assert driver._settle_monitor(
        tmp_path, tmp_path / "candidate",
        {"action": "cutover", "result": {"rolled_back": True}}, None, [Handle()],
    ) is False
    assert events == ["poll", "wait", "poll"]


@pytest.mark.parametrize("failure_point", ["before", "current", "both"])
@pytest.mark.parametrize("monitor_scope,committed", [(False, False), (True, False), (True, True)])
def test_publication_failure_retains_custody_through_observed_outcome(
    driver, tmp_path, monkeypatch, failure_point, monitor_scope, committed,
):
    from agent_worktrees import config as cfg, status_monitor_cutover as cutover

    root = tmp_path / "runtime"
    _slot(driver, root, "1.0.0")
    _slot(driver, root, "2.0.0")
    _slot(driver, root, "0.9.0")
    driver.runtime.activate(root, "1.0.0", link_free=True)
    snapshot = driver._selector_snapshot(root)
    events = []
    failure = OSError("publication storage failure")
    activate = driver.runtime.activate
    restore = driver._restore_selectors

    class Governance:
        def __init__(self, requested, payload):
            self.root, self.payload = requested, payload
            self.manifest_publication = nullcontext

        def signature(self):
            return {}

        def recheck(self):
            pass

        @contextmanager
        def held(self, *, timeout=180):
            events.append("governance-held")
            try:
                yield
            finally:
                events.append("governance-released")

    class Guard:
        def release(self):
            events.append("cutover-released")

    def assert_custody():
        assert events[0] == "governance-held"
        assert "governance-released" not in events
        assert "cutover-released" not in events
        versions = ("0.9.0", "1.0.0", "2.0.0") if monitor_scope else ("1.0.0", "2.0.0")
        for version in versions:
            with pytest.raises(driver.runtime.RetentionTimeout):
                with driver.runtime._kernel_lease(root, version, kind="retention", shared=False, timeout=0):
                    pytest.fail("publication failure released runtime custody")

    def fail_publication(*args, **kwargs):
        assert_custody()
        if "publication-failed" in events:
            return activate(*args, **kwargs)
        events.append("publication-failed")
        if failure_point == "current":
            (root / "current-version").write_text("2.0.0\n", encoding="utf-8")
        elif failure_point == "both":
            activate(*args, **kwargs)
        raise failure

    def settle(*_args):
        assert_custody()
        events.append("outcome-observed")
        return committed

    def retry_restore(*args):
        assert_custody()
        events.append("restore")
        if events.count("restore") == 1:
            raise OSError("transient rollback storage failure")
        restore(*args)

    monkeypatch.setattr(driver, "Governance", Governance)
    monkeypatch.setattr(driver, "_load_installed", lambda _slot: None)
    monkeypatch.setattr(cfg, "install_dir", lambda: root if monitor_scope else tmp_path / "other")
    monkeypatch.setattr(cutover, "_acquire_cutover_lock", lambda _root: Guard())
    monkeypatch.setattr(driver, "_monitor_identity", lambda _root: (123, "previous-identity"))
    monkeypatch.setattr(driver, "_monitor_slot", lambda *_args: "0.9.0")
    monkeypatch.setattr(driver, "_candidate_serving", lambda *_args: False)
    monkeypatch.setattr(driver, "_settle_monitor", settle)
    monkeypatch.setattr(driver.runtime, "activate", fail_publication)
    monkeypatch.setattr(driver, "_restore_selectors", retry_restore)
    monkeypatch.setattr(driver.time, "sleep", lambda _duration: None)
    with pytest.raises(OSError) as raised:
        driver.settle_candidate(
            root, "2.0.0", payload=tmp_path, payload_hash="previous",
            expected_governance={}, previous="1.0.0",
        )
    assert raised.value is failure
    assert events[-2:] == ["cutover-released", "governance-released"]
    if committed:
        assert events.count("outcome-observed") == 1
        assert "restore" not in events
        assert driver.runtime.current_version(root) == "2.0.0"
        assert driver.runtime.read_last_known_good(root) == "2.0.0"
    else:
        assert events.count("restore") == 2
        assert driver._selector_snapshot(root) == snapshot
    for version in ("0.9.0", "1.0.0", "2.0.0"):
        with driver.runtime._kernel_lease(root, version, kind="retention", shared=False, timeout=0):
            pass


@pytest.mark.parametrize("summary", [
    {"action": "cutover", "result": {"committed": True}},
    {"action": "restart", "restart": {"spawned": True}},
])
def test_reported_commit_or_restart_cannot_hide_actual_child_failure(
    driver, tmp_path, monkeypatch, summary,
):
    class Handle:
        def poll(self):
            return 1

    monkeypatch.setattr(driver, "_candidate_serving", lambda *_args: False)
    assert driver._settle_monitor(tmp_path, tmp_path / "candidate", summary, None, [Handle()]) is False


def test_uncertain_publication_requires_terminal_owner_witness(driver, tmp_path, monkeypatch, capsys):
    from zdd import breadcrumb

    records = iter([None, {"state": "unrecognized"}, {"state": "rolled_back"}])
    waits = []
    monkeypatch.setattr(breadcrumb, "read_breadcrumb", lambda _root: next(records))
    monkeypatch.setattr(driver.time, "sleep", waits.append)
    monkeypatch.setattr(driver, "_candidate_serving", lambda *_args: False)
    assert driver._settle_monitor(
        tmp_path, tmp_path / "candidate",
        {"action": "cutover", "result": {"publication_uncertain": True}}, None, [],
    ) is False
    assert waits == [1, 1]
    assert capsys.readouterr().err.count("publication is uncertain") == 1


def test_restart_spawn_returns_the_real_process_handle_to_its_owner(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agent_worktrees import status_monitor_cutover, status_monitor_runtime as smr

    handle = object()
    handles = []
    monkeypatch.setattr(smr, "_core_helper", lambda _name, local: local)
    monkeypatch.setattr(smr, "_core", lambda: SimpleNamespace(windowless_daemon_kwargs=lambda **_kwargs: {}))
    monkeypatch.setattr(smr, "_daemon_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(smr, "_daemon_environment", lambda: {})
    monkeypatch.setattr(smr, "direct_monitor_python", lambda python: (python, {}))
    monkeypatch.setattr(status_monitor_cutover, "spawn_monitor", lambda *_args, **_kwargs: handle)
    assert smr._spawn_detached(["candidate-python", "-m", "agent_worktrees"], spawned_handles=handles)
    assert handles == [handle]


@pytest.mark.parametrize("legacy_lock", ["absent", "stale"])
@pytest.mark.parametrize("slot", ["serving", None, "..", "missing"])
def test_routed_predecessor_is_captured_before_runtime_retention(driver, tmp_path, legacy_lock, slot):
    from agent_worktrees import locks, status_monitor_cutover as cutover
    from zdd import routing

    root = tmp_path / "runtime"
    (root / "versions" / "serving").mkdir(parents=True)
    (root / "status-monitor-control.token").write_text("fixture-token", encoding="utf-8")
    if legacy_lock == "stale":
        (root / "status-monitor.lock").write_text(json.dumps({"pid": 0}), encoding="utf-8")
    pid = os.getpid()
    server = cutover.ControlServer(
        lambda *_args: {"pid": pid, "status": "ready", "runtime_slot": slot},
        token="fixture-token",
    )
    server.start()
    try:
        routing.publish_active(
            root / "status-monitor-routing", bind="127.0.0.1", port=server.port, pid=pid,
        )
        identity = driver._monitor_identity(root)
        assert identity == (pid, locks.process_start_time(pid))
        if slot == "serving":
            assert driver._monitor_slot(root, identity) == "serving"
        else:
            with pytest.raises(driver.InstallationRefused):
                driver._monitor_slot(root, identity)
    finally:
        server.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows retained process object semantics")
def test_native_exit_evidence_does_not_mistake_a_retained_handle_for_a_live_process():
    from zdd import diagnostics

    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import time; time.sleep(2)"],
        stdin=subprocess.DEVNULL, **no_window_kwargs(),
    )
    try:
        assert diagnostics._pid_alive(process.pid)
        assert process.wait(timeout=10) == 0
        assert not diagnostics._pid_alive(process.pid)
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


@pytest.mark.parametrize("handle,wait,error,alive", [
    (None, 0, 87, False), (None, 0, 5, True),
    (123, 0, 0, False), (123, 0x102, 0, True), (123, 0xFFFFFFFF, 0, True),
])
def test_native_exit_signal_preserves_ambiguous_process_liveness(
    monkeypatch, handle, wait, error, alive,
):
    import ctypes
    from types import SimpleNamespace
    from zdd import diagnostics

    closed = []

    def open_process(access, inherit, pid):
        assert access & 0x00100000
        assert pid == 202 and inherit is False
        return handle

    def wait_process(observed, timeout):
        assert observed == handle and timeout == 0
        return wait

    kernel = SimpleNamespace(
        OpenProcess=open_process, CloseHandle=lambda value: closed.append(value),
        WaitForSingleObject=wait_process,
    )
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    assert diagnostics._pid_alive(202) is alive
    assert closed == ([] if handle is None else [handle])


@pytest.mark.timeout(900)
@pytest.mark.parametrize("legacy_lock", ["live", "absent", "stale"])
def test_real_installer_retains_the_serving_slot_until_monitor_drain(
    driver, tmp_path, native_runtime_source, legacy_lock,
):
    from agent_worktrees import classify_daemon, locks
    from agent_worktrees.status_monitor_cutover import ControlClient
    from agent_worktrees.status_monitor_python import direct_monitor_python
    from test_status_monitor_cutover_process import _wait_for
    from zdd import breadcrumb, diagnostics, routing

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("real construction requires the configured uv executable")
    plugin, expected_commit = native_runtime_source
    home = tmp_path / "home"
    root = home / ".agent-worktrees"
    home.mkdir()
    result = tmp_path / "result.json"
    environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home))
    environment["AGENT_WORKTREES_STATUS_MONITOR"] = "1"
    for name in (
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "COPILOT_PLUGIN_STAGED_FROM",
        "AGENT_WORKTREES_PAYLOAD_ROOT", "AGENT_WORKTREES_STATUS_MONITOR_ROUTING_DIR",
    ):
        environment.pop(name, None)
    argv = [
        sys.executable, "-I", "-B", str(plugin / "scripts" / "runtime_install.py"),
        "--root", str(root), "--plugin", str(plugin), "--payload", str(plugin),
        "--base-python", sys.executable, "--result", str(result), "--uv", uv,
    ]
    log = tmp_path / "installer.log"
    with log.open("w", encoding="utf-8") as stream:
        try:
            built = subprocess.run(
                argv, env=environment, stdout=stream, stderr=subprocess.STDOUT,
                timeout=300, **no_window_kwargs(),
            )
        except subprocess.TimeoutExpired:
            pytest.fail("Construction did not settle:\n" + log.read_text(encoding="utf-8", errors="replace"))
    assert built.returncode == 0, log.read_text(encoding="utf-8", errors="replace")
    installed = json.loads(result.read_text(encoding="utf-8"))
    candidate = root / "versions" / installed["runtime_version"]
    info = json.loads(subprocess.check_output(
        [str(driver.runtime.slot_python(root, candidate.name)), "-I", "-B", "-c",
         "import json; from agent_worktrees._build_info import BUILD_INFO; print(json.dumps(BUILD_INFO))"],
        env=environment, text=True, timeout=30, **no_window_kwargs(),
    ))
    assert info["commit"] == expected_commit
    assert info["source"] == plugin.as_posix()
    old = root / "versions" / "serving-baseline"
    shutil.copytree(candidate, old)
    driver.runtime.marker_path(root, old.name).unlink()
    driver.runtime.mark_complete(root, old.name, payload_hash=installed["payload_hash"])
    driver.runtime.activate(root, old.name, link_free=True)
    python, extra = direct_monitor_python(str(driver.runtime.slot_python(root, old.name)))
    monitor_environment = {**environment, **extra}
    lock_path = root / "status-monitor.lock"
    route_root = root / "status-monitor-routing"
    token = root / "status-monitor-control.token"
    processes = []
    identities = {}
    monitor_waiter = None
    monitor_exit = None
    hook_thread = None
    demand_thread = None
    demand_stop = threading.Event()
    demand_endpoint = None
    request_started = tmp_path / "request-started"
    request_release = tmp_path / "request-release"
    monitor_probe = """
import runpy, sys, threading
from pathlib import Path
from agent_worktrees import classify_daemon
started, release = map(Path, sys.argv[1:])
start_server = classify_daemon.start_server
def controlled_server(compute, **kwargs):
    def controlled_compute(kind, payload):
        if payload.get("retention_probe") is True:
            started.touch()
            wait = threading.Event()
            for _ in range(1800):
                if release.is_file():
                    return {"delayed": True}
                wait.wait(0.1)
            raise TimeoutError("retention probe was not released")
        return compute(kind, payload)
    return start_server(controlled_compute, **kwargs)
classify_daemon.start_server = controlled_server
sys.argv = ["agent_worktrees", "status-monitor", "--interval", "1"]
runpy.run_module("agent_worktrees", run_name="__main__", alter_sys=True)
"""
    try:
        with (tmp_path / "monitor.log").open("w", encoding="utf-8") as monitor_log:
            monitor = subprocess.Popen(
                [python, "-I", "-B", "-c", monitor_probe, str(request_started), str(request_release)],
                env=monitor_environment, cwd=tmp_path, stdin=subprocess.DEVNULL,
                stdout=monitor_log, stderr=subprocess.STDOUT, **no_window_kwargs(),
            )
            processes.append(monitor)
            monitor_waiter = ThreadPoolExecutor(max_workers=1, thread_name_prefix="baseline-reaper")
            monitor_exit = monitor_waiter.submit(monitor.wait)
            try:
                old_record = _wait_for(
                    lambda: (
                        record if "classify_endpoint" in (record := locks.read_lock(lock_path) or {})
                        else None
                    ),
                    message="isolated baseline never published its lock",
                )
            except AssertionError:
                pytest.fail(
                    "Baseline startup failed:\n"
                    + (tmp_path / "monitor.log").read_text(encoding="utf-8", errors="replace")
                    + f"\nlock={lock_path.read_text() if lock_path.exists() else 'absent'}"
                )
            old_pid = old_record["pid"]
            identities[old_pid] = diagnostics.process_start_time(old_pid)
            _wait_for(
                lambda: routing.read_active_endpoint(route_root, verify_listener=False),
                message="isolated baseline never published its route",
            )
            demand_host, demand_port = old_record["worktree_status_endpoint"].split(":")
            demand_endpoint = (demand_host, int(demand_port), old_record["worktree_status_token"])
            classify_daemon.wcs_client.subscribe(*demand_endpoint, "retained-cutover-demand")
            demand_errors = []

            def keep_demand():
                while not demand_stop.wait(1):
                    try:
                        classify_daemon.wcs_client.subscribe(
                            *demand_endpoint, "retained-cutover-demand", timeout=2,
                        )
                    except classify_daemon.wcs_client.DaemonUnavailable as error:
                        demand_errors.append(str(error))
                        return

            demand_thread = threading.Thread(target=keep_demand, daemon=True)
            demand_thread.start()
            driver.runtime.activate(root, installed["runtime_version"], link_free=True)
            hook_result = {}
            host, port, classify_token = classify_daemon.endpoint_from_rendezvous(old_record)
            def request():
                try:
                    hook_result.update(classify_daemon.wcs_client.request(
                        host, port, classify_token, kind=classify_daemon.KIND,
                        key="retained-cutover-request", payload={"retention_probe": True},
                        request_deadline_s=180,
                    ))
                except classify_daemon.wcs_client.DaemonUnavailable as error:
                    hook_result["error"] = str(error)

            hook_thread = threading.Thread(target=request, daemon=True)
            hook_thread.start()
            _wait_for(
                request_started.is_file, timeout=10,
                message="baseline did not begin its controlled request",
            )
            assert hook_thread.is_alive(), "baseline did not retain the delayed request"
            if legacy_lock == "absent":
                lock_path.unlink()
            elif legacy_lock == "stale":
                lock_path.write_text(json.dumps({"pid": 0}), encoding="utf-8")
            argv[-1] = str(tmp_path / "uv-must-not-run")
            with log.open("w", encoding="utf-8") as stream:
                transition = subprocess.Popen(
                    argv, env=environment, stdout=stream, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, **no_window_kwargs(),
                )
                processes.append(transition)
                successor = _wait_for(
                    lambda: (
                        endpoint if (endpoint := routing.read_active_endpoint(
                            route_root, verify_listener=False,
                        )) is not None and endpoint.pid != old_pid else None
                    ),
                    timeout=120,
                    message="installer never published the successor route",
                )
                identities[successor.pid] = diagnostics.process_start_time(successor.pid)
                demand_stop.set()
                demand_thread.join(timeout=5)
                classify_daemon.wcs_client.release(*demand_endpoint, "retained-cutover-demand")
                assert not demand_errors, demand_errors
                assert transition.poll() is None, "installer returned before predecessor drain"
                assert hook_thread.is_alive(), "predecessor request finished before the retention probe"
                for version in (old.name, installed["runtime_version"]):
                    with pytest.raises(driver.runtime.RetentionTimeout):
                        with driver.runtime._kernel_lease(
                            root, version, kind="retention", shared=False, timeout=0,
                        ):
                            pytest.fail("installer released a live cutover generation")
                request_release.touch()
                assert transition.wait(timeout=180) == 0, log.read_text(encoding="utf-8", errors="replace")
            hook_thread.join(timeout=15)
            assert not hook_thread.is_alive()
            assert hook_result.get("delayed") is True
            assert monitor.returncode is not None, "installer returned before its predecessor was reaped"
            assert monitor_exit.result() == 0
            assert not diagnostics._pid_alive(old_pid), "installer returned before actual predecessor exit"
            new_lock = json.loads(lock_path.read_text(encoding="utf-8"))
            assert new_lock["pid"] == successor.pid
            assert Path(new_lock["prefix"]).resolve() == candidate.resolve()
            health = ControlClient(successor.base_url, token=token.read_text().strip()).health()
            assert health["published"] is True and health["status"] == "ready"
    except (AssertionError, subprocess.TimeoutExpired) as error:
        pytest.fail(
            f"{error}\nInstaller:\n{log.read_text(encoding='utf-8', errors='replace')}"
            f"\nMonitor:\n{(tmp_path / 'monitor.log').read_text(encoding='utf-8', errors='replace')}"
            f"\nRoute:\n{routing.routing_table_path(route_root).read_text()}"
            f"\nBreadcrumb:\n{breadcrumb.read_breadcrumb(route_root)}"
            f"\nInstaller-process={processes[-1].poll()} request={hook_result}"
        )
    finally:
        try:
            request_release.touch()
            demand_stop.set()
            if demand_thread is not None:
                demand_thread.join(timeout=5)
            if demand_endpoint is not None:
                classify_daemon.wcs_client.release(*demand_endpoint, "retained-cutover-demand")
            for pid, identity in identities.items():
                if identity is not None:
                    diagnostics.terminate_pid_if_identity(pid, identity, exit_timeout=10)
        finally:
            try:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=15)
                if hook_thread is not None:
                    hook_thread.join(timeout=15)
            finally:
                if monitor_waiter is not None:
                    monitor_waiter.shutdown(wait=True)
                    assert monitor_exit.done(), "baseline reaper did not join"
                    assert monitor_exit.result() == monitor.returncode
                    assert monitor.returncode is not None, "baseline child was not reaped"


def test_preparation_stamps_installed_metadata_and_preserves_native_scripts(tmp_path, monkeypatch):
    import ast
    import agent_worktrees
    from agent_worktrees import runtime_candidate as installer

    package = tmp_path / "site-packages" / "agent_worktrees"
    package.mkdir(parents=True)
    (tmp_path / ".runtime-bootstrap").mkdir()
    scripts = tmp_path / ("Scripts" if os.name == "nt" else "bin")
    scripts.mkdir()
    native_script = scripts / "agent-worktrees"
    native_script.write_text("native console script")
    trampoline = scripts / "agent-worktrees.exe"
    trampoline.write_bytes(b"unsigned")
    python = scripts / "python.exe"
    python.write_bytes(b"signed interpreter")
    monkeypatch.setattr(agent_worktrees, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(installer.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(installer, "distribution_version", lambda _name: "2.0.0.dev1")
    provenance = {
        "version": "2.0.0-dev1", "commit": "abc", "branch": 'quoted"branch',
        "source": str(tmp_path / "source"),
        "deployment_source": json.dumps({"path": str(tmp_path / "source")}),
        "frozen_plugin": str(tmp_path / "source"),
    }
    frozen_scripts = Path(provenance["frozen_plugin"]) / "scripts"
    frozen_scripts.mkdir(parents=True)
    assert {
        "command_binding.py", "command_lifecycle.py", "runtime_install_governance.py",
    } <= set(installer._LIFECYCLE_FILES)
    for name in installer._LIFECYCLE_FILES:
        (frozen_scripts / name).write_text("frozen lifecycle", encoding="utf-8")
    (frozen_scripts / "installation-context").mkdir()
    (frozen_scripts / "installation-context" / "installation_context.py").write_text(
        "frozen validator", encoding="utf-8",
    )
    from agent_worktrees import update_stage

    monkeypatch.setattr(update_stage, "fingerprint", lambda _path: "frozen-fingerprint")
    installer.prepare_runtime_candidate(provenance)
    content = (package / "_build_info.py").read_text(encoding="utf-8")
    info = ast.literal_eval(ast.parse(content).body[-1].value)
    assert all(info[key] == value for key, value in provenance.items()
               if key not in ("deployment_source", "frozen_plugin"))
    assert json.loads(info["deployment_source"])["payload_fingerprint"] == "frozen-fingerprint"
    assert "frozen_plugin" not in info
    for name in installer._LIFECYCLE_FILES:
        assert (tmp_path / ".runtime-bootstrap" / name).read_text() == "frozen lifecycle"
    assert (
        tmp_path / ".runtime-bootstrap" / "installation-context" / "installation_context.py"
    ).read_text() == "frozen validator"
    assert len(info["runtime_generation"]) == 32
    assert info["build_timestamp"].endswith("Z")
    assert native_script.is_file() and python.is_file()
    assert trampoline.exists() == (os.name != "nt")
    with pytest.raises(RuntimeError, match="Frozen package version"):
        installer.prepare_runtime_candidate({**provenance, "version": "1.0.0"})
    assert (package / "_build_info.py").read_text(encoding="utf-8") == content


@pytest.mark.parametrize("missing", [None, "installer_wheel_snapshot.py", "installer_wheel_inputs.py"])
def test_prepared_bootstrap_loads_actual_installer_without_source(tmp_path, monkeypatch, missing):
    import agent_worktrees
    from agent_worktrees import runtime_candidate as installer, update_stage

    plugin = Path(__file__).resolve().parents[1]
    frozen = tmp_path / "frozen-plugin"
    shutil.copytree(plugin / "scripts", frozen / "scripts")
    package = tmp_path / "site-packages" / "agent_worktrees"
    package.mkdir(parents=True)
    bootstrap = tmp_path / ".runtime-bootstrap"
    bootstrap.mkdir()
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_source.py", "versioned_runtime_construction.py",
        "versioned_runtime_publication.py",
    ):
        shutil.copyfile(frozen / "scripts" / name, bootstrap / name)
    monkeypatch.setattr(agent_worktrees, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(installer.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(installer, "distribution_version", lambda _name: "2.0.0.dev1")
    monkeypatch.setattr(update_stage, "fingerprint", lambda _path: "frozen-fingerprint")
    installer.prepare_runtime_candidate({
        "version": "2.0.0-dev1", "commit": "abc", "branch": "test",
        "source": str(frozen), "deployment_source": json.dumps({"path": str(frozen)}),
        "frozen_plugin": str(frozen),
    })
    shutil.rmtree(frozen)
    if missing is not None:
        (bootstrap / missing).unlink(missing_ok=True)
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(bootstrap / "runtime_install.py"), "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    if missing is not None:
        assert result.returncode != 0
        assert f"No module named '{Path(missing).stem}'" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "--settle-slot" in result.stdout


def test_bash_bootstrap_skips_unusable_alias_and_searches_all_candidates(tmp_path):
    bash = shutil.which("bash") if os.name != "nt" else None
    if os.name == "nt" and (git := shutil.which("git")) is not None:
        candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if candidate.is_file():
            bash = str(candidate)
    if bash is None:
        pytest.skip("native Bash is unavailable")
    text = (Path(__file__).resolve().parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")
    start = text.index("\n_bootstrap_python() {\n")
    end = text.index("\n}\n", start) + 3
    actual = Path(sys.executable).as_posix()
    program = (
        text[start:end] + "\n"
        "LINK_DIR=/absent; VENV_DIR=/absent\n"
        "type() { printf '%s\\n' '/c/Users/example/Microsoft/WindowsApps/python3' "
        f"'/absent/python' '{actual}'; }}\n"
        'selected="$(_bootstrap_python exclude-venv-dir)" || exit 1\n'
        "\"$selected\" -I -B -c 'import sys; print(sys.executable)'\n"
    )
    result = subprocess.run(
        [bash, "-c", program], capture_output=True, text=True,
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == Path(sys.executable).resolve()


@pytest.mark.timeout(900)
def test_real_python_lifecycle_constructs_then_reuses_without_uv(
    driver, tmp_path, native_runtime_source,
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("real construction requires the configured uv executable")
    plugin, expected_commit = native_runtime_source
    root, result = tmp_path / "runtime", tmp_path / "result.json"
    home = tmp_path / "home"
    home.mkdir()
    environment = dict(os.environ)
    environment.update(HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home))
    for name in (
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
        "COPILOT_PLUGIN_STAGED_FROM", "AGENT_WORKTREES_PAYLOAD_ROOT",
    ):
        environment.pop(name, None)
    argv = [
        sys.executable, "-I", str(plugin / "scripts" / "runtime_install.py"),
        "--root", str(root), "--plugin", str(plugin), "--payload", str(plugin),
        "--base-python", sys.executable, "--result", str(result), "--uv", uv,
    ]
    source_files = driver.engine._source_files(driver.engine._package_roots(plugin))
    source_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}

    def run(command, label, timeout):
        log = tmp_path / f"{label}.log"
        with log.open("w", encoding="utf-8") as stream:
            try:
                completed = subprocess.run(
                    command, stdout=stream, stderr=subprocess.STDOUT, env=environment,
                    timeout=timeout, **no_window_kwargs(),
                )
            except subprocess.TimeoutExpired:
                pytest.fail(
                    f"Lifecycle did not settle ({label}): {command}\n"
                    + log.read_text(encoding="utf-8", errors="replace")
                )
        changed = [
            str(path) for path, digest in source_hashes.items()
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ]
        assert not changed, f"{label} modified source inputs: {changed}"
        assert driver.engine._source_files(driver.engine._package_roots(plugin)) == source_files
        assert completed.returncode == 0, log.read_text(encoding="utf-8", errors="replace")

    run(argv, "construct", 300)
    installed = json.loads(result.read_text(encoding="utf-8"))
    assert installed["reused"] is False
    assert (root / "current-version").read_text().strip() == installed["runtime_version"]
    python = Path(installed["runtime_python"])
    info_probe = [
        str(python), "-I", "-B", "-c",
        "import json; from agent_worktrees._build_info import BUILD_INFO; print(json.dumps(BUILD_INFO))",
    ]
    info = json.loads(subprocess.check_output(
        info_probe, env=environment, text=True, timeout=30, **no_window_kwargs(),
    ))
    assert info["version"] == installed["package_version"]
    assert info["source"] == plugin.as_posix()
    assert len(info["runtime_generation"]) == 32
    assert info["commit"] == expected_commit
    if os.name == "nt":
        assert not list(python.parent.glob("agent-*.exe"))
    argv[-1] = str(tmp_path / "uv-must-not-run")
    run(argv, "reuse-without-uv", 180)
    reused = json.loads(result.read_text(encoding="utf-8"))
    assert reused["reused"] is True
    assert reused["payload_hash"] == installed["payload_hash"]
    assert reused["runtime_python"] == installed["runtime_python"]
    assert json.loads(subprocess.check_output(
        info_probe, env=environment, text=True, timeout=30, **no_window_kwargs(),
    )) == info
    native = []
    if os.name == "nt":
        for name in ("powershell.exe", "pwsh.exe"):
            executable = shutil.which(name)
            if executable is not None:
                native.append([
                    executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(plugin / "scripts" / "install.ps1"), "provision", "-InstallDir", str(root),
                ])
    bash = shutil.which("bash") if os.name != "nt" else None
    if os.name == "nt" and (git := shutil.which("git")) is not None:
        git_bash = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if git_bash.is_file():
            bash = str(git_bash)
    if bash is not None:
        probe = subprocess.run(
            [bash, "-c", "printf '%s' \"$OSTYPE\""], capture_output=True, text=True,
            timeout=10, **no_window_kwargs(),
        )
        if os.name != "nt" or probe.stdout.startswith(("msys", "cygwin")):
            if os.name == "nt":
                native.append([
                    bash, "-c", 'exec bash "$1" provision --install-dir "$(cygpath -u "$2")"',
                    "--", (plugin / "scripts" / "install.sh").as_posix(), str(root),
                ])
            else:
                native.append([
                    bash, str(plugin / "scripts" / "install.sh"), "provision",
                    "--install-dir", str(root),
                ])
    assert native, "real native entrypoint proof needs an available supported shell"
    for index, command in enumerate(native):
        run(command, f"native-{index}", 180)
        assert (root / "current-version").read_text().strip() == installed["runtime_version"]
        manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
        assert manifest["source"]["version"] == installed["package_version"]
        assert Path(manifest["source"]["path"]).resolve() == plugin.resolve()
        assert Path(manifest["venv"]).resolve() == Path(installed["runtime_python"]).parent.parent.resolve()
        assert json.loads(subprocess.check_output(
            info_probe, env=environment, text=True, timeout=30, **no_window_kwargs(),
        )) == info
