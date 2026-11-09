from __future__ import annotations

from concurrent.futures import Future
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_supervisor_daemon import FakeClient, FakeLauncher, FakeLock, _daemon


VERSION = "1.2.3+source." + "a" * 40 + "." + "b" * 16 + ".profile." + "c" * 16


@pytest.mark.parametrize("case", ["matching", "settings", "scope", "authority", "absent", "unreadable", "explicit-named"])
def test_old_source_caller_without_markers_gets_matching_primary_binding(tmp_path, monkeypatch, case):
    from contextlib import ExitStack
    from agent_dispatch import __main__ as cli, source_start, supervisor_source
    from agent_dispatch.supervisor_lane_status import PROFILE_ENV, GENERATION_ENV
    from agent_dispatch.supervisor_profile import OWNER_ENV, MasterProfile
    from tests.test_source_supervisor_startup import _sources

    for key in (PROFILE_ENV, GENERATION_ENV, OWNER_ENV):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    _sources(monkeypatch)
    primary = tmp_path / "supervisor.env"
    primary.write_text(
        "AGENT_DISPATCH_SUPERVISE_MODE=serve\n"
        "AGENT_DISPATCH_SUPERVISE_MACHINE=anomalous-potato\n"
        "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--no-declared\n",
    )
    plan = source_start.supervisor_plan(tmp_path)
    # Older producers reconstructed effective flags and sent no binding markers.
    argv = ["supervise", "serve", "--interval", "5.0", "--env", "default",
            "--machine", "anomalous-potato", "--no-declared"]
    if case == "settings":
        argv[3] = "7"
    elif case == "scope":
        argv[5] = "other-scope"
    elif case == "authority":
        argv = ["--url", "http://127.0.0.1:7777", *argv]
    args = cli.build_parser().parse_args(argv)
    daemon = _daemon(
        FakeClient([]), FakeLauncher(), lock=FakeLock(),
        self_update_enabled=True, self_update_argv=argv,
        self_update_install_dir=lambda: tmp_path,
    )
    daemon.env = args.env
    existing = None
    if case == "absent":
        primary.unlink()
    elif case == "unreadable":
        original_read = Path.read_bytes

        def denied(path):
            if path == primary:
                raise PermissionError("fixture primary read refused")
            return original_read(path)

        monkeypatch.setattr(Path, "read_bytes", denied)
    elif case == "explicit-named":
        named = tmp_path / "supervisors" / "named.env"
        named.parent.mkdir()
        named.write_text("AGENT_DISPATCH_SUPERVISE_MODE=serve\n")
        existing = MasterProfile(named, None, argv)
        daemon._master_profile = existing
    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    with ExitStack() as clients:
        supervisor_source.configure_admission(daemon, args, object(), clients)
        binding = getattr(daemon, "_master_profile", None)
        if case != "matching":
            assert binding is existing
            return
        assert binding is not None, "new Source admission did not adopt the matching old producer"
        assert binding.source_primary
        assert binding.path == primary and binding.generation == plan.profile_generation
        assert binding.arguments == argv
        assert all(key not in os.environ for key in (PROFILE_ENV, GENERATION_ENV, OWNER_ENV))


def test_preparation_directory_io_is_not_an_absent_candidate(tmp_path, monkeypatch):
    from agent_dispatch import supervisor_source

    directory = tmp_path / "run" / "supervisor-candidates"
    assert supervisor_source.read_prepared(tmp_path, VERSION, "fixture") is None
    directory.mkdir(parents=True)
    original = os.scandir
    original_iterdir = Path.iterdir

    def denied(path):
        if not isinstance(path, int) and Path(path) == directory:
            raise PermissionError("fixture preparation enumeration denied")
        return original(path)

    def denied_iterdir(path):
        if path == directory:
            raise PermissionError("fixture preparation enumeration denied")
        return original_iterdir(path)

    monkeypatch.setattr(os, "scandir", denied)
    monkeypatch.setattr(Path, "iterdir", denied_iterdir)
    with pytest.raises(PermissionError, match="enumeration denied"):
        supervisor_source.read_prepared(tmp_path, VERSION, "fixture")


def _incumbent(tmp_path, monkeypatch):
    from agent_dispatch import supervisor_source

    (tmp_path / "current-version").write_text(VERSION, encoding="utf-8")
    daemon = _daemon(
        FakeClient([]), FakeLauncher(), lock=FakeLock(),
        self_update_enabled=True, self_update_argv=["supervise", "serve"],
        self_update_install_dir=lambda: tmp_path,
        self_update_stale_target=lambda *_: Path("incoming-python"),
        self_update_running_version="old",
    )
    monkeypatch.setattr(daemon, "_recheck_governance", lambda _: None)
    monkeypatch.setattr(supervisor_source, "is_profiled_application", lambda _root, version, _target: ".profile." in version)
    monkeypatch.setattr(supervisor_source, "read_prepared", lambda *_args, **_kwargs: None)
    return daemon, supervisor_source


def test_source_incumbent_does_not_stop_before_independent_preparation(tmp_path, monkeypatch):
    daemon, source = _incumbent(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(source, "spawn_candidate", lambda *_args, **_kwargs: (
        calls.append("spawn") or SimpleNamespace(instance_receipt=object(), poll=lambda: None)
    ))
    monkeypatch.setattr(daemon, "shutdown", lambda: pytest.fail("unready incumbent stop"))
    assert daemon._maybe_self_update() is False
    assert daemon._maybe_self_update() is False
    assert calls == ["spawn"]
    assert not daemon._lock.released


def test_source_incumbent_retains_custody_of_unfinished_native_work(tmp_path, monkeypatch):
    daemon, source = _incumbent(tmp_path, monkeypatch)
    ready = {"instance_id": "d" * 32}
    monkeypatch.setattr(source, "read_prepared", lambda *_args, **_kwargs: ready)
    future = Future()
    daemon._probe_inflight.add(future)
    monkeypatch.setattr(daemon, "shutdown", lambda: pytest.fail("unfinished work stop"))
    assert daemon._maybe_self_update() is False
    assert not daemon._lock.released
    assert daemon._source_handoff["phase"] == "withdrawing"


def test_source_handoff_requires_settled_workers_before_singleton_release(tmp_path, monkeypatch):
    from agent_dispatch import supervisor_retirement

    daemon, source = _incumbent(tmp_path, monkeypatch)
    monkeypatch.setattr(source, "read_prepared", lambda *_args, **_kwargs: {"instance_id": "d" * 32})
    monkeypatch.setattr(daemon, "shutdown", lambda: True)
    monkeypatch.setattr(supervisor_retirement, "_retire_owned_workers", lambda *_: False)
    assert daemon._maybe_self_update() is False
    assert not daemon._lock.released
    monkeypatch.setattr(supervisor_retirement, "_retire_owned_workers", lambda *_: True)
    assert daemon._maybe_self_update() is True
    assert daemon._lock.released


def test_passive_supervisor_never_reconciles_before_original_singleton_admission(tmp_path, monkeypatch):
    daemon = _daemon(FakeClient([]), FakeLauncher(), lock=FakeLock())
    calls = []
    ownership = iter([False, True])
    monkeypatch.setattr(daemon, "acquire_singleton", lambda: calls.append("acquire") or next(ownership))
    monkeypatch.setattr(daemon, "reconcile_once", lambda: (
        pytest.fail("once not allowed for a passive candidate")
    ))
    admission = SimpleNamespace(
        prepare=lambda _daemon: calls.append("prepared"),
        check=lambda: calls.append("checked"),
        admitted=lambda: calls.append("admitted"),
    )
    with pytest.raises(ValueError, match="singleton"):
        daemon.serve(once=True, source_admission=admission)
    assert calls == []


@pytest.mark.parametrize("incumbent_base", ["runtime", "caller"])
def test_supervisor_admission_uses_coordinator_database_directory(tmp_path, monkeypatch, incumbent_base):
    from contextlib import ExitStack
    from agent_dispatch import config, source_start, supervise_cli, supervisor_source
    from zdd.diagnostics import process_start_time

    runtime = tmp_path / "runtime"
    caller = tmp_path / "caller"
    runtime.mkdir()
    caller.mkdir()
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(runtime))
    monkeypatch.setenv("AGENT_DISPATCH_DB", str(Path("data") / "tasks.db"))
    cfg = config.load_config()
    monkeypatch.chdir(runtime if incumbent_base == "runtime" else caller)
    identity = source_start.config_identity(cfg)
    monkeypatch.chdir(caller)
    birth = process_start_time(os.getpid())
    assert birth

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            return {
                "status": "ok", "runtime_config_identity": identity,
                "slot": {"pid": os.getpid(), "process_start_time": birth},
            }

    monkeypatch.setattr(source_start, "require_selected_source", lambda: runtime)
    monkeypatch.setattr(supervise_cli, "_client", lambda *_args, **_kwargs: Client())
    with ExitStack() as clients:
        admission = supervisor_source.configure_admission(SimpleNamespace(), None, object(), clients)
        if incumbent_base == "runtime":
            admission.probe()
        else:
            with pytest.raises(RuntimeError, match="data access"):
                admission.probe()


def test_passive_supervisor_reads_cohort_without_queue_or_unit_mutation(monkeypatch, tmp_path):
    from agent_dispatch import source_start, supervisor_source
    from agent_dispatch.pool_source_snapshot import DeclarationRead

    calls = []
    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    daemon = SimpleNamespace(
        machine=None, env="default",
        declared_source=lambda: calls.append("cohort") or DeclarationRead((), True),
        _recheck_governance=lambda _: None,
    )
    monkeypatch.setattr(supervisor_source, "__version__", VERSION)
    admission = supervisor_source.CandidateAdmission(
        tmp_path, VERSION, object(), lambda: None, probe=lambda: None,
    )
    monkeypatch.setattr(admission, "_publish", lambda _: calls.append("prepared"))
    admission.prepare(daemon)
    assert calls == ["cohort", "prepared"]


def test_unknown_cohort_is_not_passive_application_readiness(monkeypatch, tmp_path):
    from agent_dispatch import source_start, supervisor_source
    from agent_dispatch.pool_source_snapshot import DeclarationRead

    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    daemon = SimpleNamespace(
        machine=None, env="default", declared_source=lambda: DeclarationRead((), False, "unknown"),
        _recheck_governance=lambda _: None,
    )
    monkeypatch.setattr(supervisor_source, "__version__", VERSION)
    admission = supervisor_source.CandidateAdmission(
        tmp_path, VERSION, object(), lambda: None, probe=lambda: None,
    )
    monkeypatch.setattr(admission, "_publish", lambda _: pytest.fail("unknown prepared"))
    with pytest.raises(RuntimeError, match="cohort"):
        admission.prepare(daemon)


def test_preparation_requires_native_artifact_custody_not_running_bit(monkeypatch, tmp_path):
    from agent_dispatch import companion, supervisor_source
    from agent_procutil import _daemon_instance_identity, daemon_instances

    identity = "e" * 32
    directory = tmp_path / "run" / "supervisor-candidates"
    directory.mkdir(parents=True)
    path = directory / (identity + ".json")
    document = {
        "runtime_version": VERSION, "scope": "fixture",
        "generation": {"pid": os.getpid(), "start_token": "start"},
        "progress": {"phase": "prepared"}, "instance_id": identity,
        "registry_root": str(tmp_path / "registry"), "birth": "birth",
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    native = {
        "instance_id": identity, "state": "running", "runtime_root": str(tmp_path),
        "pid": str(os.getpid()), "birth": "birth", "artifact_count": "0",
    }
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda _: [native])
    monkeypatch.setattr(companion, "process_start_token", lambda _: "start")
    monkeypatch.setattr(_daemon_instance_identity, "identity_state", lambda *_: "matching")
    assert supervisor_source.read_prepared(tmp_path, VERSION, "fixture") is None
    device, inode = _daemon_instance_identity.path_identity(path)
    native.update({
        "artifact_count": "1", "artifact_0_path": str(path),
        "artifact_0_device": device, "artifact_0_inode": inode,
    })
    assert supervisor_source.read_prepared(tmp_path, VERSION, "fixture") == document
    assert supervisor_source.read_prepared(tmp_path, VERSION, "fixture", exclude_pid=os.getpid()) is None
    document.update(settings={"interval": 7}, profile_binding={"path": str(tmp_path / "profile.env"), "generation": "new"})
    path.write_text(json.dumps(document), encoding="utf-8")
    expected = {key: document[key] for key in ("settings", "profile_binding")}
    assert supervisor_source.read_prepared(tmp_path, VERSION, "fixture", expected=expected) == document
    assert supervisor_source.read_prepared(tmp_path, VERSION, "fixture", expected={
        **expected, "settings": {"interval": 5},
    }) is None
    assert supervisor_source.read_prepared(tmp_path, VERSION, "fixture", expected={
        **expected, "profile_binding": {**expected["profile_binding"], "generation": "old"},
    }) is None


@pytest.mark.parametrize("peer_kind", ["equivalent", "settings", "profile"])
def test_same_version_candidate_stands_down_only_for_equivalent_preparation(tmp_path, monkeypatch, peer_kind):
    from agent_dispatch import companion, config, single_instance, supervisor_source as source
    from agent_dispatch.supervisor_status_io import runtime_status_path
    from agent_procutil import _daemon_instance_identity as identity, daemon_instances

    daemon = _daemon(FakeClient([]), FakeLauncher(), lock=FakeLock())
    scope = source.supervisor_lease_scope(daemon.machine, daemon.env)
    monkeypatch.setattr(config, "run_dir", lambda: tmp_path / "run")
    monkeypatch.setattr(daemon, "_recheck_governance", lambda _: None)
    granted = iter([False, True])
    monkeypatch.setattr(daemon, "acquire_singleton", lambda: next(granted))
    monkeypatch.setattr(single_instance, "is_locked", lambda _: True)
    monkeypatch.setattr(single_instance, "read_holder_pid", lambda _: 901)
    monkeypatch.setattr(companion, "process_start_token", lambda _: "start")
    monkeypatch.setattr(identity, "identity_state", lambda *_: "matching")
    status = runtime_status_path(scope)
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({
        "runtime_version": VERSION, "generation": {"pid": 901, "start_token": "start"},
    }))
    directory = tmp_path / "run" / "supervisor-candidates"
    directory.mkdir()
    incarnation = "e" * 32
    path = directory / (incarnation + ".json")
    own = {
        "settings": {"interval": 7},
        "profile_binding": {"path": str(tmp_path / "supervisor.env"), "generation": "new"},
        "owner": {"authority_target": "fixture"},
    }
    peer = {
        **own, "runtime_version": VERSION, "scope": scope, "instance_id": incarnation,
        "registry_root": str(tmp_path / "registry"), "birth": "peer-birth",
        "generation": {"pid": 901, "start_token": "start"}, "progress": {"phase": "prepared"},
    }
    if peer_kind == "settings":
        peer["settings"] = {"interval": 5}
    elif peer_kind == "profile":
        peer["profile_binding"] = {**own["profile_binding"], "generation": "old"}
    path.write_text(json.dumps(peer))
    device, inode = identity.path_identity(path)
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda _: [{
        "instance_id": incarnation, "state": "running", "runtime_root": str(tmp_path),
        "pid": "901", "birth": "peer-birth", "artifact_count": "1",
        "artifact_0_path": str(path), "artifact_0_device": device, "artifact_0_inode": inode,
    }])
    admitted = []
    admission = SimpleNamespace(
        root=tmp_path, version=VERSION, record=own, prepare=lambda _: None,
        check=lambda: None, probe=lambda: None, admitted=lambda: admitted.append(True),
    )
    assert source.acquire_candidate(daemon, admission, once=False, single_instance=True) is (peer_kind != "equivalent")
    assert admitted == ([] if peer_kind == "equivalent" else [True])


def test_source_retirement_does_not_forget_unit_with_unconfirmed_native_exit(tmp_path):
    daemon = _daemon(FakeClient([]), FakeLauncher(), lock=FakeLock())
    daemon._source_handoff = {"phase": "withdrawing"}

    def wait(**_kwargs):
        raise TimeoutError("native writer remains")

    proc = SimpleNamespace(poll=lambda: None, terminate=lambda: None, wait=wait)
    unit = SimpleNamespace(proc=proc, kind="supervised-lane")
    daemon._units["fixture"] = unit
    assert daemon._stop("fixture") is False
    assert daemon._units["fixture"] is unit


def test_passive_admission_waits_for_original_scope_before_any_reconcile(monkeypatch, tmp_path):
    from agent_dispatch import supervisor_source

    daemon = _daemon(FakeClient([]), FakeLauncher(), lock=FakeLock())
    calls = []
    granted = iter([False, True])
    monkeypatch.setattr(daemon, "_recheck_governance", lambda _: None)
    monkeypatch.setattr(daemon, "acquire_singleton", lambda: calls.append("acquire") or next(granted))
    monkeypatch.setattr(daemon, "reconcile_once", lambda: pytest.fail("passive reconciliation"))
    daemon.sleep = lambda _: calls.append("wait")
    admission = SimpleNamespace(
        root=tmp_path, version=VERSION,
        prepare=lambda _: calls.append("prepared"), check=lambda: calls.append("check"),
        probe=lambda: calls.append("probe"), admitted=lambda: calls.append("admitted"),
    )
    assert supervisor_source.acquire_candidate(
        daemon, admission, once=False, single_instance=True,
    )
    assert calls == ["prepared", "check", "acquire", "wait", "check", "acquire", "check", "probe", "admitted"]


@pytest.mark.parametrize("phase", ["preparing", "withdrawing"])
@pytest.mark.parametrize("selected", ["rollback", "new-source"])
def test_changed_selection_settles_only_owned_candidate_then_resumes_or_retargets(
    tmp_path, monkeypatch, phase, selected,
):
    from agent_procutil import daemon_instances

    daemon, source = _incumbent(tmp_path, monkeypatch)
    newer = VERSION.replace("a" * 40, "f" * 40)
    candidate = SimpleNamespace(instance_receipt=object(), poll=lambda: None)
    daemon._source_handoff = {
        "root": tmp_path, "version": VERSION, "target": Path("old-incoming"),
        "phase": phase, "child": candidate,
    }
    calls = []

    def cleanup(receipt, *, terminate):
        assert receipt is candidate.instance_receipt and terminate is False
        calls.append("settled-owned")
        return SimpleNamespace(status="settled")

    monkeypatch.setattr(daemon_instances, "cleanup_instance", cleanup)
    monkeypatch.setattr(source, "spawn_candidate", lambda target, *_args, **_kwargs: (
        calls.append(("spawn", target)) or SimpleNamespace(instance_receipt=object(), poll=lambda: None)
    ))
    version = "old" if selected == "rollback" else newer
    (tmp_path / "current-version").write_text(version, encoding="utf-8")
    daemon._self_update_stale_target = lambda *_: None if selected == "rollback" else Path("new-incoming")
    assert daemon._maybe_self_update() is False
    assert calls == ["settled-owned"]
    assert daemon._source_handoff is None
    assert not daemon._lock.released
    assert daemon._maybe_self_update() is False
    if selected == "new-source":
        assert calls[-1] == ("spawn", Path("new-incoming"))
        assert daemon._source_handoff["version"] == newer
    else:
        assert calls == ["settled-owned"]


def test_changed_selection_keeps_unknown_creation_without_freezing_reconciliation(tmp_path, monkeypatch):
    daemon, _ = _incumbent(tmp_path, monkeypatch)
    state = {
        "root": tmp_path, "version": VERSION, "target": Path("incoming"),
        "phase": "unconfirmed", "child": None,
    }
    daemon._source_handoff = state
    (tmp_path / "current-version").write_text("old", encoding="utf-8")
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff is state
    assert state["phase"] != "withdrawing"
    assert not daemon._lock.released


@pytest.mark.parametrize("observation", ["matching", "unknown", "dead"])
def test_retarget_never_terminates_discovered_peer(tmp_path, monkeypatch, observation):
    from agent_procutil import _daemon_instance_identity, daemon_instances

    daemon, _ = _incumbent(tmp_path, monkeypatch)
    state = {
        "root": tmp_path, "version": VERSION, "target": Path("incoming"),
        "phase": "preparing", "prepared": {"generation": {"pid": 901}, "birth": "peer-birth"},
    }
    daemon._source_handoff = state
    (tmp_path / "current-version").write_text("old", encoding="utf-8")
    monkeypatch.setattr(daemon_instances, "cleanup_instance", lambda *_args, **_kwargs: pytest.fail("foreign cleanup"))
    monkeypatch.setattr(_daemon_instance_identity, "identity_state", lambda *_: observation)
    assert daemon._maybe_self_update() is False
    assert (daemon._source_handoff is None) is (observation == "dead")
    assert not daemon._lock.released


@pytest.mark.parametrize("reselected", [False, True])
def test_withdrawal_rollback_reopens_same_owner_only_after_local_work_settles(
    tmp_path, monkeypatch, reselected,
):
    from agent_procutil import daemon_instances

    daemon, _ = _incumbent(tmp_path, monkeypatch)
    pool = daemon._runtime_pool()
    pool.submit(lambda: 19).result(timeout=2)
    pool.shutdown(wait=True)
    state = {
        "root": tmp_path, "version": VERSION, "target": Path("incoming"),
        "phase": "withdrawing", "child": SimpleNamespace(instance_receipt=object(), poll=lambda: 1),
        "pools": {pool},
    }
    daemon._source_handoff = state
    daemon._close_pool_metadata_admission()
    daemon._shutdown_drained = False
    pending = Future()
    daemon._probe_inflight.add(pending)
    custody = daemon._pool_publisher_custody
    (tmp_path / "current-version").write_text("old", encoding="utf-8")
    monkeypatch.setattr(daemon_instances, "cleanup_instance", lambda *_args, **_kwargs: SimpleNamespace(status="settled"))
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff is state
    assert daemon._pool_metadata_closed.is_set()
    if reselected:
        (tmp_path / "current-version").write_text(VERSION, encoding="utf-8")
    pending.set_result(None)
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff is None
    assert daemon._pool_publisher_custody is custody
    assert not daemon._pool_metadata_closed.is_set()
    assert daemon._shutdown_drained
    assert not daemon._lock.released
    replacement = daemon._runtime_pool()
    try:
        assert replacement is not pool
        assert replacement.submit(lambda: 23).result(timeout=2) == 23
    finally:
        replacement.shutdown(wait=True)


def test_retarget_unsettled_owned_candidate_retains_original_scope(tmp_path, monkeypatch):
    from agent_procutil import daemon_instances

    daemon, source = _incumbent(tmp_path, monkeypatch)
    state = {
        "root": tmp_path, "version": VERSION, "target": Path("incoming"),
        "phase": "withdrawing", "child": SimpleNamespace(instance_receipt=object()),
    }
    daemon._source_handoff = state
    (tmp_path / "current-version").write_text("old", encoding="utf-8")
    monkeypatch.setattr(daemon_instances, "cleanup_instance", lambda *_args, **_kwargs: SimpleNamespace(status="refused"))
    monkeypatch.setattr(source, "spawn_candidate", lambda *_args, **_kwargs: pytest.fail("unsettled successor replay"))
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff is state
    assert not daemon._lock.released


def test_managed_uncertainty_defers_withdrawal_so_normal_reconciliation_can_refresh(tmp_path, monkeypatch):
    daemon, source = _incumbent(tmp_path, monkeypatch)
    monkeypatch.setattr(source, "read_prepared", lambda *_args, **_kwargs: {"instance_id": "d" * 32})
    daemon._managed_uncertain.add("foreign-or-unknown-native-work")
    monkeypatch.setattr(daemon, "shutdown", lambda: pytest.fail("uncertainty must reconcile before withdrawal"))
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff is None or daemon._source_handoff["phase"] != "withdrawing"
    assert not daemon._lock.released
    daemon._managed_uncertain.clear()
    monkeypatch.setattr(daemon, "shutdown", lambda: True)
    assert daemon._maybe_self_update() is True
    assert daemon._lock.released


@pytest.mark.parametrize("native_state", ["dead", "reused", "unknown"])
def test_stale_preparation_does_not_mask_later_healthy_candidate_or_unknown_identity(
    tmp_path, monkeypatch, native_state,
):
    from agent_dispatch import companion, supervisor_source
    from agent_procutil import _daemon_instance_identity, daemon_instances

    directory = tmp_path / "run" / "supervisor-candidates"
    directory.mkdir(parents=True)
    records, native = [], []
    for index, identity in enumerate(("a" * 32, "b" * 32)):
        path = directory / (identity + ".json")
        document = {
            "runtime_version": VERSION, "scope": "fixture",
            "generation": {"pid": 100 + index, "start_token": "start"},
            "progress": {"phase": "prepared"}, "instance_id": identity,
            "registry_root": str(tmp_path / "registry"), "birth": "birth",
        }
        path.write_text(json.dumps(document), encoding="utf-8")
        device, inode = _daemon_instance_identity.path_identity(path)
        records.append(document)
        native.append({
            "instance_id": identity, "state": "running", "runtime_root": str(tmp_path),
            "pid": str(100 + index), "birth": "birth", "artifact_count": "1",
            "artifact_0_path": str(path), "artifact_0_device": device, "artifact_0_inode": inode,
        })
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda _: native)
    monkeypatch.setattr(companion, "process_start_token", lambda pid: "start" if pid == 101 else None)
    monkeypatch.setattr(_daemon_instance_identity, "identity_state", lambda pid, _: native_state if pid == 100 else "matching")
    if native_state == "unknown":
        with pytest.raises(RuntimeError, match="native"):
            supervisor_source.read_prepared(tmp_path, VERSION, "fixture")
    else:
        assert supervisor_source.read_prepared(tmp_path, VERSION, "fixture") == records[1]
    assert len(list(directory.iterdir())) == 2
