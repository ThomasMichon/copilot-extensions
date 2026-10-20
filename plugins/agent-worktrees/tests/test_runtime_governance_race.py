"""Actual construction, repair, cutover and installation-cell lock ordering."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

import pytest
import tomllib
from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job
from runtime_install_fixture import isolate_account_profile, remove_fixture_worktree
from test_status_monitor_cutover_process import _wait_for
from zdd import diagnostics, routing

from agent_worktrees import classify_daemon, locks, runtime_deployment
from agent_worktrees.status_monitor_cutover import ControlClient
from agent_worktrees.status_monitor_python import direct_monitor_python

PLUGIN = Path(__file__).resolve().parents[1]

WORKER = r"""
import json,os,sys,time
from contextlib import contextmanager
from pathlib import Path

scripts,stage,root,plugin,uv,signals=map(Path,sys.argv[1:7])
stage=str(stage)
sys.path.insert(0,str(scripts))
import runtime_install as lifecycle

def mark(name):
    (signals/name).write_text(str(os.getpid()),encoding="utf-8")

def wait(name):
    deadline=time.monotonic()+480
    while not (signals/name).exists():
        if time.monotonic()>deadline:
            raise TimeoutError(name)
        time.sleep(.05)

held=lifecycle.Governance.held
@contextmanager
def traced_held(self,*,timeout=480):
    mark(stage+"-governance-wait")
    with held(self,timeout=timeout):
        mark(stage+"-governance")
        yield
lifecycle.Governance.held=traced_held

mark(stage+"-started")
if stage=="governance":
    with lifecycle.Governance(root,plugin).held(timeout=480):
        pass
elif stage=="cutover":
    from agent_worktrees import status_monitor_cutover as cutover
    authority=lifecycle.Governance(root,plugin)
    guard=cutover._acquire_cutover_lock(root/"status-monitor-routing",timeout_s=480)
    try:
        mark("cutover-owned")
        wait("release-cutover")
        data=json.loads((signals/"original.json").read_text(encoding="utf-8"))
        result=lifecycle.settle_candidate(
            root,data["version"],payload=plugin,payload_hash=data["payload_hash"],
            expected_governance=authority.signature(),previous="serving-baseline",
            cutover_guard=guard,package_version=data["package_version"],
            force_monitor_transition=True,wait_timeout=480,
        )
        (signals/"cutover-result.json").write_text(json.dumps(result),encoding="utf-8")
    finally:
        guard.release()
else:
    if stage=="ordinary":
        from contextlib import contextmanager
        retain=lifecycle.engine._retained_inputs
        @contextmanager
        def traced_retain(*args,**kwargs):
            mark("ordinary-freezing")
            with retain(*args,**kwargs) as result:
                mark("ordinary-frozen")
                yield result
        lifecycle.engine._retained_inputs=traced_retain
        construct=lifecycle.engine._construct
        def paused_construct(*args,**kwargs):
            mark("ordinary-construction")
            wait("release-build")
            return construct(*args,**kwargs)
        lifecycle.engine._construct=paused_construct
    arguments=[
        "--root",str(root),"--plugin",str(plugin),"--payload",str(plugin),
        "--uv",str(uv),"--base-python",sys.executable,
        "--result",str(signals/(stage+"-result.json")),
        "--wait-timeout","480","--child-timeout","240",
    ]
    if stage=="repair":
        arguments+=["--mode","repair"]
    raise SystemExit(lifecycle.main(arguments))
"""


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("platform_name", ["posix", "nt"])
def test_account_profile_isolation_changes_only_copied_os_lookups(
    tmp_path, monkeypatch, platform_name,
):
    import runtime_install_fixture as fixture

    monkeypatch.setattr(fixture, "os", SimpleNamespace(name=platform_name))
    payload, home = tmp_path / "payload", tmp_path / "quoted ' profile"
    paths = (
        Path("scripts") / "installation-context" / "_installation_context_resolution.py",
        Path("src") / "agent_worktrees" / "_installation_context_resolution.py",
        Path("scripts") / "installation-context" / "installation-context.sh",
    )
    originals = {path: (PLUGIN / path).read_bytes() for path in paths}
    for path, source in originals.items():
        destination = payload / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source)
    isolate_account_profile(payload, home)
    assert all((PLUGIN / path).read_bytes() == source for path, source in originals.items())
    for path, source in originals.items():
        isolated = (payload / path).read_text(encoding="utf-8")
        if platform_name == "nt":
            assert (payload / path).read_bytes() == source
        elif path.suffix == ".py":
            import ast

            ast.parse(isolated)
            assert f"profile_value = Path({str(home)!r})" in isolated
            assert "profile_value = Path(pwd.getpwuid(os.getuid()).pw_dir)" not in isolated
        else:
            import shlex

            assert f'CURRENT_PROFILE_HOME="$(canonical_path {shlex.quote(str(home))} true)"' in isolated
            assert 'CURRENT_PROFILE_HOME="$(canonical_path "$home_path" true)"' not in isolated


def test_fixture_worktree_deletion_handles_readonly_git_objects(tmp_path):
    owner = tmp_path / "owner"
    pack = owner / ".git" / "objects" / "pack" / "fixture.idx"
    pack.parent.mkdir(parents=True)
    pack.write_bytes(b"git object")
    pack.chmod(stat.S_IREAD)
    published = tmp_path / "published"
    published.mkdir()
    (published / "marker").write_bytes(b"original")
    remove_fixture_worktree(owner)
    assert not owner.exists()
    assert (published / "marker").read_bytes() == b"original"


def test_governance_forwards_the_callers_lock_budget(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(PLUGIN / "scripts"))
    driver = _load(PLUGIN / "scripts" / "runtime_install.py", "race_budget_driver")
    owner = object.__new__(driver.Governance)
    owner.durable_home = tmp_path
    owner.initial = ({}, {"marketplaceId": "test", "cellRoot": str(tmp_path / "cell")})
    observed = []

    class Lock:
        def __init__(self, path, **kwargs):
            observed.append((path, kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    owner.helper = SimpleNamespace(_DirectoryLock=Lock)
    owner.recheck = lambda: None
    moments = iter((100, 101, 104))
    monkeypatch.setattr(driver.time, "monotonic", lambda: next(moments))
    with owner.held(timeout=23):
        assert [item[1]["kind"] for item in observed] == ["genesis", "install"]
        assert [item[1]["timeout_seconds"] for item in observed] == [22, 19]


@pytest.mark.timeout(1000)
def test_real_namespaced_build_repair_cutover_and_governance_do_not_invert(
    tmp_path, monkeypatch,
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("actual runtime construction requires the configured uv executable")
    monkeypatch.syspath_prepend(str(PLUGIN / "scripts"))
    driver = _load(PLUGIN / "scripts" / "runtime_install.py", "race_lifecycle")
    helper = _load(
        PLUGIN / "scripts" / "installation-context" / "installation_context.py",
        "race_installation_context",
    )
    home, signals = tmp_path / "home", tmp_path / "signals"
    home.mkdir()
    signals.mkdir()
    environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home))
    for name in (
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "COPILOT_PLUGIN_STAGED_FROM",
        "AGENT_WORKTREES_PAYLOAD_ROOT", "AGENT_WORKTREES_STATUS_MONITOR_ROUTING_DIR",
    ):
        environment.pop(name, None)
    environment["AGENT_WORKTREES_STATUS_MONITOR"] = "1"
    environment["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
    frozen = driver.engine.freeze_inputs(PLUGIN, tmp_path / "payload")
    payload = frozen.plugin
    isolate_account_profile(payload, home)
    old_version = tomllib.loads((payload / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    seed_root = tmp_path / "seed"
    with driver.engine.numbered_candidate(
        seed_root, old_version, plugin=payload, uv=Path(uv), base_python=Path(sys.executable),
        health_module="agent_worktrees.runtime_candidate",
        health_callable="verify_runtime_candidate",
        construction_callable="agent_worktrees.runtime_candidate:prepare_runtime_candidate",
        construction_data=driver._source_provenance(payload, old_version),
        process_retention=True, child_timeout=240,
    ) as seed:
        version = "99.0.0"
        project = payload / "pyproject.toml"
        project.write_text(
            project.read_text(encoding="utf-8").replace(
                f'version = "{old_version}"', f'version = "{version}"', 1,
            ), encoding="utf-8",
        )
        assert tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"] == version
        manifest_path = payload / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = version
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        context = helper.stamp_context(
            payload_version=version, payload_origin="explicit",
            expected_namespace_generation=0, expected_install_generation=0,
            payload_root=payload, plugin_id="agent-worktrees",
            durable_home=home / ".copilot-extensions",
            source_descriptor={"source": "github", "repo": "ThomasMichon/copilot-extensions"},
            marketplace_key="copilot-extensions", environment=environment,
        )
        (home / ".copilot-extensions" / "installation-mode.json").write_text(json.dumps({
            "schema": helper.POLICY_SCHEMA, "version": 1,
            "installationMode": {"enabled": True},
        }), encoding="utf-8")
        activation = helper.compare_and_swap_activation(
            context=context["installReceipt"], expected_marketplace_id=context["marketplaceId"],
            expected_plugin_id="agent-worktrees",
            expected_namespace_generation=context["namespaceGeneration"],
            expected_install_generation=context["generation"], expected_activation_generation=0,
            activation_mode="namespaced", activation_state="active", legacy_disposition="absent",
            legacy_probe={"declared": True, "result": "absent", "checkedAt": None},
            durable_home=home / ".copilot-extensions", legacy_root=home / ".agent-worktrees",
            environment=environment, os_profile=home,
        )
        assert activation["status"] == "ready"
        root = Path(context["pluginRoot"])
        original = root / "versions" / old_version
        shutil.copytree(seed.slot, original)
        baseline = root / "versions" / "serving-baseline"
        shutil.copytree(original, baseline)
        driver.runtime.marker_path(root, baseline.name).unlink()
        driver.runtime.mark_complete(root, baseline.name, payload_hash=seed.payload_hash)
        driver.runtime.activate(root, baseline.name, link_free=True)
        runtime_deployment.publish(
            root,
            runtime_deployment.manifest_bytes(
                driver._installed_identity(baseline), baseline, seed.payload_hash,
            ),
        )
    original_marker = driver.runtime.marker_path(root, old_version).read_bytes()
    baseline_marker = driver.runtime.marker_path(root, baseline.name).read_bytes()
    environment.update(
        COPILOT_EXTENSIONS_CONTEXT=context["installReceipt"],
        AGENT_WORKTREES_PAYLOAD_ROOT=str(payload),
    )
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER, encoding="utf-8")
    (signals / "original.json").write_text(json.dumps({
        "version": old_version, "payload_hash": seed.payload_hash, "package_version": old_version,
    }), encoding="utf-8")
    handles = []
    identities = {}
    stop = threading.Event()
    demand_errors = []
    keeper = None
    with ExitStack() as streams:
        def launch(stage, python=sys.executable, argv=None):
            stream = streams.enter_context((tmp_path / f"{stage}.log").open("w", encoding="utf-8"))
            command = argv or [
                str(python), "-I", "-B", str(worker), str(payload / "scripts"), stage,
                str(root), str(payload), uv, str(signals),
            ]
            process, job = spawn_sync_in_kill_on_close_job(
                command, require_job=os.name == "nt", env=environment, cwd=tmp_path,
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                **no_window_kwargs(),
                **({"start_new_session": True} if os.name != "nt" else {}),
            )
            handles.append((process, job))
            return process

        def logs():
            output = "\n".join(
                f"{path.name}:\n{path.read_text(encoding='utf-8', errors='replace')}"
                for path in tmp_path.glob("*.log")
            )
            owners = [
                Path(context["cellRoot"]) / ".locks"
                / "agent-worktrees.install.lock" / "owner.json",
                home / ".copilot-extensions" / "marketplaces" / ".locks"
                / f"{context['marketplaceId']}.genesis" / "owner.json",
            ]
            return output + "\nSignals: " + ", ".join(path.name for path in signals.iterdir()) + (
                "\nOwners: " + repr({
                    str(path): path.read_text(encoding="utf-8")
                    for path in owners if path.is_file()
                })
            )

        def keep_demand():
            endpoint = None
            try:
                while not stop.wait(.25):
                    record = locks.read_lock(root / "status-monitor.lock") or {}
                    if not locks.lock_is_live(record) or "worktree_status_endpoint" not in record:
                        continue
                    pid = record["pid"]
                    identities[pid] = diagnostics.process_start_time(pid)
                    host, port = record["worktree_status_endpoint"].split(":")
                    current = (host, int(port), record["worktree_status_token"])
                    if endpoint != current:
                        if endpoint is not None:
                            classify_daemon.wcs_client.release(*endpoint, "combined-race-demand")
                        endpoint = current
                    try:
                        classify_daemon.wcs_client.subscribe(
                            *endpoint, "combined-race-demand", timeout=2,
                        )
                    except classify_daemon.wcs_client.DaemonUnavailable:
                        # A retiring route can disappear between lock read and subscribe.
                        endpoint = None
            except Exception as error:
                demand_errors.append(repr(error))
            finally:
                if endpoint is not None:
                    classify_daemon.wcs_client.release(*endpoint, "combined-race-demand")

        try:
            python, extra = direct_monitor_python(
                str(driver.runtime.slot_python(root, baseline.name)),
            )
            environment.update(extra)
            baseline_process = launch("baseline", argv=[
                python, "-m", "agent_worktrees", "status-monitor", "--interval", "1",
            ])
            first = _wait_for(
                lambda: locks.read_lock(root / "status-monitor.lock"),
                timeout=60, message="namespaced baseline did not publish its lock",
            )
            assert first["pid"] == baseline_process.pid
            keeper = threading.Thread(target=keep_demand, daemon=True)
            keeper.start()
            _wait_for(
                lambda: routing.read_active_endpoint(root / "status-monitor-routing"),
                timeout=60, message="baseline did not publish its control route",
            )
            cutover_process = launch("cutover", driver.runtime.slot_python(root, old_version))
            _wait_for(
                lambda: (signals / "cutover-owned").exists(),
                timeout=60, message="cutover did not take authority",
            )
            ordinary = launch("ordinary")
            _wait_for(
                lambda: (signals / "ordinary-construction").exists(),
                timeout=180, message="ordinary build did not enter real construction",
            )
            with pytest.raises(driver.runtime.ConstructionTimeout):
                with driver.runtime.construction_lease(root, version, timeout=0):
                    pytest.fail("actual ordinary construction lost its lease")
            repair, governance = launch("repair"), launch("governance")
            (signals / "release-cutover").write_text("continue", encoding="utf-8")
            time.sleep(7)  # Deliberately exceed the helper's old five-second lock budget.
            assert cutover_process.poll() is repair.poll() is governance.poll() is None, logs()
            assert not (signals / "cutover-governance").exists()
            assert not (signals / "repair-governance").exists()
            assert not (signals / "governance-governance").exists()
            (signals / "release-build").write_text("continue", encoding="utf-8")

            def candidate_ready():
                if ordinary.poll() not in (None, 0):
                    raise AssertionError("ordinary construction failed")
                return driver.runtime.is_complete(root, version)

            _wait_for(
                candidate_ready,
                timeout=240, message="ordinary build never published its immutable candidate",
            )
            with driver.runtime.construction_lease(root, version, timeout=5):
                pass
            for process in (cutover_process, ordinary, repair, governance):
                assert process.wait(timeout=400) == 0, logs()
            assert (signals / "governance-governance").exists()
            results = {
                stage: json.loads((signals / f"{stage}-result.json").read_text(encoding="utf-8"))
                for stage in ("cutover", "ordinary", "repair")
            }
            assert results["cutover"]["runtime_version"] == old_version
            assert results["ordinary"]["runtime_version"] == version
            repaired = results["repair"]["runtime_version"]
            assert repaired.startswith(version + "+repair.")
            assert (
                results["ordinary"]["runtime_generation"]
                != results["repair"]["runtime_generation"]
            )
            assert driver.runtime.marker_path(root, old_version).read_bytes() == original_marker
            assert driver.runtime.marker_path(root, baseline.name).read_bytes() == baseline_marker
            selected = driver.runtime.current_version(root)
            assert selected in (version, repaired)
            manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
            assert manifest["source"]["version"] == version
            assert manifest["runtime_slot"] == selected
            record = locks.read_lock(root / "status-monitor.lock")
            assert locks.lock_is_live(record)
            assert record["runtime_generation"] == manifest["runtime_generation"]
            assert record["version"] == version and record["runtime_slot"] == selected
            endpoint = routing.read_active_endpoint(root / "status-monitor-routing")
            health = ControlClient(
                endpoint.base_url,
                token=(root / "status-monitor-control.token").read_text().strip(),
            ).health()
            assert health["published"] is True and health["status"] == "ready"
            assert health["runtime_generation"] == record["runtime_generation"]
            assert health["version"] == version and health["runtime_slot"] == selected
            assert not demand_errors, demand_errors
            for slot in (old_version, baseline.name, version, repaired):
                with driver.runtime.construction_lease(root, slot, timeout=0):
                    pass
            assert not list((root / ".installer-inputs").glob("mode-owner-*"))
        except (AssertionError, subprocess.TimeoutExpired) as error:
            pytest.fail(f"{error}\n{logs()}")
        finally:
            (signals / "release-cutover").write_text("cleanup", encoding="utf-8")
            (signals / "release-build").write_text("cleanup", encoding="utf-8")
            stop.set()
            if keeper is not None:
                keeper.join(timeout=10)
            record = locks.read_lock(root / "status-monitor.lock") or {}
            if locks.lock_is_live(record):
                identities[record["pid"]] = diagnostics.process_start_time(record["pid"])
            for pid, identity in identities.items():
                if identity is not None:
                    diagnostics.terminate_pid_if_identity(pid, identity, exit_timeout=10)
            for process, job in reversed(handles):
                if job is not None:
                    job.terminate_and_wait(15)
                elif process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=15)
