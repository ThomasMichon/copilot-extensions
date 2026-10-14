"""Read-only definition suppliers never become execution workspaces."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict

import pytest

from agent_dispatch import registrar_discovery as discovery, repo_config
from agent_dispatch import repository_resources as resources
from agent_dispatch.registrar import RegistrarError
from agent_dispatch.registrar_pointer import Pointer


@pytest.fixture
def supplier(tmp_path, monkeypatch):
    roots = {}
    for name in ("state-notes", "control-harness"):
        roots[name] = tmp_path / name
        roots[name].mkdir()
    calls = []

    def capture(*args, **kwargs):
        assert args[:2] == ("repos", "find")
        assert args[3:] == ("--registered-only", "--json")
        name = args[2]
        calls.append(name)
        return subprocess.CompletedProcess(args, 0, json.dumps({
            "registration": "ready", "name": name,
            "class": "knowledge" if name == "state-notes" else "worktree",
            "path": str(roots[name]),
        }), "")

    monkeypatch.setattr(resources, "run_agent_worktrees_capture", capture)
    directory = roots["state-notes"] / repo_config.CANONICAL_REPO_CONFIG_DIR / "registrar"
    directory.mkdir(parents=True)

    def discover(*documents):
        for index, document in enumerate(documents):
            (directory / f"{index}.json").write_text(json.dumps(document), encoding="utf-8")
        return discovery.discover_trusted([
            Pointer(name="definitions", kind="repo", repository="state-notes"),
        ])[1]

    return roots, calls, discover


def test_supplier_discovers_loop_with_independent_execution_context(supplier):
    from agent_dispatch.repository_issue_loops import validate_config
    from tests.test_repository_issue_loops import _config

    roots, calls, discover = supplier
    (roots["state-notes"] / "backlog.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "assert Path.cwd().name == 'control-harness'\n"
        "print(json.dumps({'issues':[]}))\n", encoding="utf-8",
    )
    data = _config(
        execution_repo="control-harness",
        forge={"provider": "script", "producer_login": "fixture",
               "command": ["repo:state-notes:backlog.py"],
               "cwd": "repo:control-harness:."},
        pool={"body": {"type": "headless", "agent": "control-harness"}},
    )
    source, workers = discover(data)
    assert calls == ["state-notes"]  # execution resources still wait for admission
    assert source.spec["cwd"] == "repo:control-harness:."
    assert workers.repos == "example/project"
    assert workers.body.agent == "control-harness"
    assert workers.body.no_pair is False
    config = validate_config(source.spec["repository_issue_loop"], cwd=source.spec["cwd"])
    assert config["forge"]["command"] == [sys.executable, str(roots["state-notes"] / "backlog.py")]
    assert config["forge"]["cwd"] == str(roots["control-harness"])
    from agent_dispatch.repository_issue_loops import _forge_provider_for

    assert _forge_provider_for(config).list_open_issues(config["repo"]) == []


def test_supplier_emitter_and_evaluator_run_in_named_harness(supplier, monkeypatch):
    from agent_dispatch.producers.emitter import run_tick
    from agent_dispatch.producers.evaluator import NoOp, load_evaluator
    from tests.test_producers_emitter import FakeClient

    roots, _, discover = supplier
    (roots["state-notes"] / "emit.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "assert Path.cwd().name == 'control-harness'\n"
        "print(json.dumps({'title':'work','dedup_key':'supplier-smoke'}))\n",
        encoding="utf-8",
    )
    (roots["state-notes"] / "verify.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "assert json.load(sys.stdin)['type']=='task.submitted'\n"
        "assert Path.cwd().name == 'control-harness'\n"
        "print(json.dumps({'decision':'noop','reason':'harness'}))\n",
        encoding="utf-8",
    )
    evaluator_spec = {
        "scripts": {"verify": [sys.executable, "repo:state-notes:verify.py"]},
        "cwd": "repo:control-harness:.", "timeout_seconds": 10,
    }
    declarations = discover(
        {"name": "emit", "kind": "emitter", "spec": {
            "id": "supplier-emitter", "interval_seconds": 60, "timeout_seconds": 10,
            "command": ["{python}", "repo:state-notes:emit.py"],
            "cwd": "repo:control-harness:.", "task_output": "json",
        }},
        {"name": "verify", "kind": "evaluator", "spec": {
            "repo": "example/project", "evaluator_ref": "verify",
            "evaluator_spec": evaluator_spec,
        }},
    )
    emitter, evaluator = declarations
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(roots["control-harness"] / "dispatch"))
    result = run_tick(FakeClient(), dict(emitter.spec), holder="fixture")
    assert result["returncode"] == 0
    assert len(result["created"]) == 1
    loaded = load_evaluator(evaluator.spec["evaluator_spec"], evaluator_ref="verify")
    assert loaded.evaluate({"type": "task.submitted"}) == [NoOp(reason="harness")]


@pytest.mark.parametrize("repo_class", ["knowledge", "reference"])
@pytest.mark.parametrize("cwd", [None, ".", "repo:state-notes:."])
def test_supplier_runtime_refuses_read_only_execution(tmp_path, monkeypatch, repo_class, cwd):
    from tests.test_repository_resources import _registry

    (tmp_path / "script.py").write_text("raise AssertionError('must not run')", encoding="utf-8")
    _registry(monkeypatch, tmp_path, name="state-notes", repo_class=repo_class)
    with pytest.raises(RegistrarError, match=f"{repo_class}-only"):
        resources.resolve_execution_resources(
            [sys.executable, "repo:state-notes:script.py"], cwd,
        )


@pytest.mark.parametrize("fault", ["missing", "unregistered", "mismatch"])
def test_supplier_discovery_refuses_invalid_registered_source(tmp_path, monkeypatch, fault):
    response = {
        "registration": "ready", "name": "state-notes", "class": "knowledge",
        "path": str(tmp_path / "missing" if fault == "missing" else tmp_path),
    }
    if fault == "mismatch":
        response["name"] = "another-repo"
    monkeypatch.setattr(resources, "run_agent_worktrees_capture", lambda *a, **k:
        subprocess.CompletedProcess(a, 1 if fault == "unregistered" else 0, json.dumps(response), ""))
    with pytest.raises(RegistrarError):
        discovery.discover_trusted([
            Pointer(name="definitions", kind="repo", repository="state-notes"),
        ])


def test_supplier_worker_allocates_paired_harness_through_owner_boundary(supplier, monkeypatch, tmp_path):
    from agent_dispatch import embody, bridge_agent_registry
    from agent_dispatch.spawn_factories import make_headless_spawn
    from tests._helpers import RepoDefaultingQueue
    from tests.test_repository_issue_loops import _config
    from tests.test_supervisor import QueueBackedClient

    _, _, discover = supplier
    _, workers = discover(_config(
        execution_repo="control-harness",
        pool={"body": {"type": "headless", "agent": "control-harness"}},
    ))
    monkeypatch.setattr(
        bridge_agent_registry, "_resolve_agent_record",
        lambda *a, **k: {"name": "control-harness", "project": "control-harness"},
    )
    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: ["fixture-aw"])
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[:4] == ["fixture-aw", "--project", "control-harness", "create"]
        assert "--no-pair" not in argv
        assert "--creation-key" in argv
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "worktree": {"id": "worker-space", "repo": "control-harness",
                         "path": str(tmp_path / "worker-space")},
            "creation": {"version": 1, "status": "complete"},
        }), "")

    monkeypatch.setattr(embody.subprocess, "run", run)
    queue = RepoDefaultingQueue(tmp_path / "queue.db")
    task = queue.create("work", repo=workers.repos)
    reservation, _ = queue.reserve_spawn(task.id, reserved_by="fixture")
    spawn = make_headless_spawn(agent=workers.body.agent, no_pair=workers.body.no_pair)
    prepared = embody.prepare_reusable_worktree(
        asdict(task), asdict(reservation), project=spawn.allocation_project_for(asdict(task)),
        interface="acp", driver="agent-dispatch", supervisor="fixture",
        no_pair=workers.body.no_pair, allocation_client=QueueBackedClient(queue),
    )
    assert prepared["project"] == "control-harness"
    assert prepared["path"] == str(tmp_path / "worker-space")
    assert queue.get_reservation(reservation.key).allocation_project == "control-harness"
    assert len(calls) == 1


def test_supplier_headless_worker_requests_harness_not_task_lane(supplier, monkeypatch, tmp_path):
    from agent_dispatch import bridge_agent_registry, ordinary_bridge
    from agent_dispatch.spawn_factories import make_headless_spawn
    from agent_dispatch.supervisor import Supervisor
    from tests._helpers import RepoDefaultingQueue
    from tests.test_repository_issue_loops import _config
    from tests.test_supervisor import QueueBackedClient

    _, _, discover = supplier
    _, workers = discover(_config(
        execution_repo="control-harness",
        pool={"body": {"type": "headless", "agent": "control-harness"}},
    ))
    monkeypatch.setattr(bridge_agent_registry, "_resolve_agent_record",
                        lambda *a, **k: {"project": "control-harness"})
    monkeypatch.setattr(ordinary_bridge, "agent_bridge_launch_prefix", lambda: ["fixture-bridge"])
    queue = RepoDefaultingQueue(tmp_path / "queue.db")

    class Client(QueueBackedClient):
        def get_reservation(self, key):
            return asdict(queue.get_reservation(key))

        def record_created_agent(self, key, **facts):
            return asdict(ordinary_bridge.record_created_agent(queue, key, **facts))

    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert "--no-pair" not in argv
        if argv[2] == "create":
            assert argv[3] == "control-harness@control-harness"
            assert "--creation-key" in argv
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "session_id": "bridge-worker", "ready": True, "status": "awaiting_first_prompt",
                "connection": {"repo": "control-harness", "worktree_id": "worker-space",
                               "target_dir": str(tmp_path / "worker-space"),
                               "native_session_id": "native-worker"},
            }), "")
        assert argv[2] == "send"
        assert queue.latest_reservation(task.id).allocation_project == "control-harness"
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "session_id": "bridge-worker", "native_session_id": "native-worker",
            "outcome": "delivered",
        }), "")

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", run)
    task = queue.create("work", repo=workers.repos)
    reservation, _ = queue.reserve_spawn(task.id, reserved_by="fixture", driver="ordinary-bridge")
    spawn = make_headless_spawn(agent=workers.body.agent, no_pair=workers.body.no_pair)
    supervisor = Supervisor(Client(queue), spawn_fn=spawn, repo=workers.repos, machine="fixture")
    prepared = supervisor._prepare_spawn_task(asdict(task), asdict(reservation))
    ok, result = spawn(prepared)
    assert ok, result
    assert result["worktree"] == "worker-space"
    assert len(calls) == 2


@pytest.mark.parametrize("repo_class", ["knowledge", "reference"])
def test_supplier_actual_consumers_refuse_read_only_cwd(tmp_path, monkeypatch, repo_class):
    from agent_dispatch.producers.emitter import run_tick
    from agent_dispatch.producers.evaluator import EvaluatorRuntimeError, ScriptEvaluator
    from tests.test_producers_emitter import FakeClient
    from tests.test_repository_resources import _registry

    _registry(monkeypatch, tmp_path, name="state-notes", repo_class=repo_class)

    def forbidden(*a, **k):
        pytest.fail("read-only cwd reached subprocess")

    spec = {
        "id": "refused-emitter", "interval_seconds": 60,
        "command": [sys.executable, "unused.py"], "cwd": "repo:state-notes:.",
    }
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "dispatch"))
    with pytest.raises(RegistrarError, match=f"{repo_class}-only"):
        run_tick(FakeClient(), spec, holder="fixture", runner=forbidden)
    evaluator = ScriptEvaluator(
        "verify", spec["command"], cwd=spec["cwd"], runner=forbidden,
    )
    with pytest.raises(EvaluatorRuntimeError, match=f"{repo_class}-only"):
        evaluator.evaluate({"type": "task.submitted"})


def test_supplier_mixed_corpus_enumerates_without_admitting_implicit_execution(supplier, monkeypatch):
    from agent_dispatch.producers.emitter import run_tick
    from agent_dispatch.producers.evaluator import EvaluatorRuntimeError, load_registration_evaluator
    from tests.test_producers_emitter import FakeClient
    from tests.test_repository_issue_loops import _config

    roots, calls, discover = supplier
    source = roots["state-notes"]
    documents = [_config(
        name="managed", execution_repo="control-harness",
        pool={"body": {"type": "headless", "agent": "control-harness"}},
    )]
    for index, cwd in enumerate((None, ".", str(source), str(source / "unused" / ".."))):
        context = {"cwd": cwd} if cwd is not None else {}
        documents.extend((
            {"name": f"old-emitter-{index}", "kind": "emitter",
             "filters": {"permit": {"machine": ["other-host"]}}, "spec": {
                 "id": f"old-emitter-{index}", "interval_seconds": 60,
                 "command": [sys.executable, "unused.py"], **context,
             }},
            {"name": f"old-evaluator-{index}", "kind": "evaluator", "spec": {
                "repo": "example/project", "evaluator_ref": "verify",
                "evaluator_spec": {"scripts": {"verify": [sys.executable, "unused.py"]},
                                   **context},
            }},
        ))
    documents.append(_config(name="old-loop"))
    declarations = {d.name: d for d in discover(*documents)}
    assert len(declarations) == 12
    assert calls == ["state-notes"]
    assert declarations["managed-source"].spec["cwd"] == "repo:control-harness:."
    assert declarations["managed-workers"].body.agent == "control-harness"
    assert declarations["managed-workers"].body.no_pair is False
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(source / "dispatch"))

    def forbidden(*a, **k):
        pytest.fail("metadata enumeration admitted supplier execution")

    for name, declaration in declarations.items():
        if not name.startswith("old-") or name.endswith("-workers"):
            continue
        if declaration.kind == "emitter":
            assert declaration.spec["cwd"].startswith("repo:state-notes:")
            with pytest.raises(RegistrarError, match="knowledge-only"):
                run_tick(FakeClient(), dict(declaration.spec), holder="fixture", runner=forbidden)
        else:
            assert declaration.spec["evaluator_spec"]["cwd"] == "repo:state-notes:."
            evaluator = load_registration_evaluator(dict(declaration.spec), runner=forbidden)
            with pytest.raises(EvaluatorRuntimeError, match="knowledge-only"):
                evaluator.evaluate({"type": "task.submitted"})


def test_supplier_ordinary_nested_forge_cwd_retains_read_only_authority(supplier):
    from agent_dispatch.repository_issue_loops import validate_config
    from tests.test_repository_issue_loops import _config

    roots, _, discover = supplier
    source, _ = discover(_config(
        execution_repo="control-harness",
        forge={"provider": "script", "producer_login": "fixture",
               "command": ["repo:state-notes:backlog.py"],
               "cwd": str(roots["state-notes"])},
    ))
    loop = source.spec["repository_issue_loop"]
    assert loop["forge"]["cwd"] == "repo:state-notes:."
    with pytest.raises(RegistrarError, match="knowledge-only"):
        validate_config(loop, cwd=source.spec["cwd"])


@pytest.mark.parametrize("repo_class", ["worktree", "singleton"])
def test_supplier_managed_class_keeps_legacy_cwd_behavior(tmp_path, monkeypatch, repo_class):
    from tests.test_repository_resources import _registry

    directory = tmp_path / repo_config.CANONICAL_REPO_CONFIG_DIR / "registrar"
    directory.mkdir(parents=True)
    documents = [
        {"name": "implicit", "kind": "emitter", "spec": {
            "id": "implicit", "interval_seconds": 60, "command": ["python", "unused.py"],
        }},
        {"name": "relative", "kind": "emitter", "spec": {
            "id": "relative", "interval_seconds": 60, "command": ["python", "unused.py"], "cwd": ".",
        }},
        {"name": "verify", "kind": "evaluator", "spec": {
            "repo": "example/project", "evaluator_ref": "verify",
            "evaluator_spec": {"scripts": {"verify": ["python", "unused.py"]}},
        }},
    ]
    for index, document in enumerate(documents):
        (directory / f"{index}.json").write_text(json.dumps(document), encoding="utf-8")
    _registry(monkeypatch, tmp_path, repo_class=repo_class)
    _, declarations = discovery.discover_trusted([
        Pointer(name="definitions", kind="repo", repository="tools"),
    ])
    implicit, relative, evaluator = declarations
    assert "cwd" not in implicit.spec
    assert relative.spec["cwd"] == str(directory.resolve())
    assert "cwd" not in evaluator.spec["evaluator_spec"]


@pytest.mark.parametrize("flavor", ["posix", "windows"])
@pytest.mark.parametrize("kind", ["emitter", "evaluator"])
def test_supplier_qualification_preserves_foreign_absolute_cwd(monkeypatch, flavor, kind):
    import ntpath
    import posixpath
    from pathlib import PurePosixPath, PureWindowsPath
    from types import SimpleNamespace

    from agent_dispatch import registrar_document as document
    from agent_dispatch.registrar import ProfileDeclaration

    if flavor == "posix":
        path_type, path_module = PurePosixPath, posixpath
        root = path_type("/workspace/state-notes")
        foreign = [r"C:\work\repo", r"\\server\share\repo", "C:/work/repo"]
    else:
        path_type, path_module = PureWindowsPath, ntpath
        root = path_type(r"C:\workspace\state-notes")
        foreign = ["/work/repo", "/workspace/state-notes"]
    # Pure paths exercise both OS contracts without foreign filesystem access.
    monkeypatch.setattr(document, "Path", path_type)
    monkeypatch.setattr(document, "os", SimpleNamespace(path=path_module))

    for cwd in foreign:
        context = {"cwd": cwd}
        spec = context if kind == "emitter" else {
            "evaluator_spec": {"scripts": {"verify": ["python", "verify.py"]}, **context},
        }
        declaration = ProfileDeclaration(name="fixture", kind=kind, spec=spec)
        qualified = document.qualify_supplier_context(declaration, "state-notes", root)
        context = qualified.spec if kind == "emitter" else qualified.spec["evaluator_spec"]
        assert context["cwd"] == cwd
        assert declaration.spec == spec


@pytest.mark.parametrize("kind", ["emitter", "evaluator"])
def test_supplier_qualification_native_contexts(tmp_path, kind):
    from agent_dispatch.registrar import ProfileDeclaration
    from agent_dispatch.registrar_document import qualify_supplier_context

    for cwd in ["tools", str(tmp_path / "tools"), None]:
        context = {"cwd": cwd} if cwd is not None else {}
        spec = context if kind == "emitter" else {
            "evaluator_spec": {"scripts": {"verify": ["python", "verify.py"]}, **context},
        }
        declaration = ProfileDeclaration(name="fixture", kind=kind, spec=spec)
        qualified = qualify_supplier_context(declaration, "state-notes", tmp_path)
        context = qualified.spec if kind == "emitter" else qualified.spec["evaluator_spec"]
        assert context["cwd"] == f"repo:state-notes:{'.' if cwd is None else 'tools'}"


@pytest.mark.parametrize("kind", ["emitter", "evaluator"])
@pytest.mark.parametrize("leaf", ["", "not-created"])
def test_supplier_native_cwd_alias_is_fenced_at_actual_consumer(
    supplier, monkeypatch, tmp_path, kind, leaf,
):
    from agent_dispatch.producers.emitter import run_tick
    from agent_dispatch.producers.evaluator import EvaluatorRuntimeError, load_registration_evaluator
    from tests.test_producers_emitter import FakeClient

    roots, _, discover = supplier
    root = roots["state-notes"]
    alias = tmp_path / "supplier-alias"
    try:
        alias.symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.fail(f"required native directory symlink could not be created: {exc}")
    assert alias.is_symlink()
    assert alias.resolve() == root
    cwd = str(alias / leaf)
    spec = {
        "id": "alias-emitter", "interval_seconds": 60, "command": ["python", "unused.py"],
        "cwd": cwd,
    } if kind == "emitter" else {
        "repo": "example/project", "evaluator_ref": "verify",
        "evaluator_spec": {"scripts": {"verify": ["python", "unused.py"]}, "cwd": cwd},
    }
    declaration, = discover({"name": "alias", "kind": kind, "spec": spec})
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "dispatch"))

    def forbidden(*a, **k):
        pytest.fail("supplier symlink alias bypassed execution admission")

    if kind == "emitter":
        with pytest.raises(RegistrarError, match="knowledge-only"):
            run_tick(FakeClient(), dict(declaration.spec), holder="fixture", runner=forbidden)
        qualified = declaration.spec["cwd"]
    else:
        evaluator = load_registration_evaluator(dict(declaration.spec), runner=forbidden)
        with pytest.raises(EvaluatorRuntimeError, match="knowledge-only"):
            evaluator.evaluate({"type": "task.submitted"})
        qualified = declaration.spec["evaluator_spec"]["cwd"]
    assert qualified == f"repo:state-notes:{leaf or '.'}"


def test_supplier_local_symlink_escape_keeps_source_authority(supplier, monkeypatch, tmp_path):
    from agent_dispatch.producers.emitter import run_tick
    from tests.test_producers_emitter import FakeClient

    roots, _, discover = supplier
    link = roots["state-notes"] / "outside-alias"
    try:
        link.symlink_to(roots["control-harness"], target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.fail(f"required native directory symlink could not be created: {exc}")
    assert link.resolve() == roots["control-harness"]
    declaration, = discover({"name": "escape", "kind": "emitter", "spec": {
        "id": "escape", "interval_seconds": 60, "command": ["python", "unused.py"],
        "cwd": str(link),
    }})
    assert declaration.spec["cwd"] == "repo:state-notes:outside-alias"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "dispatch"))
    with pytest.raises(RegistrarError, match="knowledge-only"):
        run_tick(
            FakeClient(), dict(declaration.spec), holder="fixture",
            runner=lambda *a, **k: pytest.fail("source-local symlink escape reached subprocess"),
        )
