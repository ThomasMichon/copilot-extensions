"""Pure contracts for caller classification and mandatory service admission."""

import importlib.util
import ast
import asyncio
from contextlib import asynccontextmanager, contextmanager
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "daemon_coverage", Path(__file__).with_name("check-daemon-coverage.py"),
)
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def fixture(root, text, sites=None, entries=None):
    source = root / "services" / "example" / "src" / "service.py"
    source.parent.mkdir(parents=True)
    source.write_text(text, encoding="utf-8")
    tools = root / "tools"
    tools.mkdir(exist_ok=True)
    census = {
        "schema": 1,
        "prefixes": {"s": "services/example/src"},
        "sites": sites or {},
        "entrypoints": entries or {},
        "context_required": list(entries or {}),
    }
    (tools / "daemon-coverage.json").write_text(json.dumps(census), encoding="utf-8")
    return source


@pytest.mark.parametrize("expression", [
    "subprocess.Popen(argv)",
    "Popen(argv)",
    "launch(argv)",
])
def test_unknown_dynamic_launch_fails_even_with_containment_comment(tmp_path, expression):
    fixture(
        tmp_path,
        "from subprocess import Popen as launch\n"
        "def serve(argv):\n"
        f"    return {expression}  # daemon-spawn: allow contained\n",
    )
    assert any("unclassified" in problem for problem in GUARD.verify(tmp_path))


def test_assigned_launch_alias_is_checked():
    sites, _ = GUARD.python_sites(
        "import subprocess\nlaunch = subprocess.Popen\n"
        "def go(argv):\n    return launch(argv)\n", "fixture.py",
    )
    assert sites == {("go", "Popen"): 1}


def test_getattr_alias_and_direct_native_spawn_are_checked():
    sites, _ = GUARD.python_sites(
        "launch = getattr(subprocess, 'Popen')\n"
        "def go(argv):\n"
        "    launch(argv)\n"
        "    getattr(subprocess, 'Popen')(argv)\n"
        "    os.posix_spawn(argv[0], argv, {})\n", "fixture.py",
    )
    assert sites == {("go", "Popen"): 2, ("go", "posix_spawn"): 1}


def test_checked_in_bin_launchers_are_not_skipped(tmp_path):
    fixture(tmp_path, "pass\n")
    directory = tmp_path / "worktree-manager" / "bin"
    directory.mkdir(parents=True)
    (directory / "unclassified.py").write_text("Popen(argv)\n", encoding="utf-8")
    assert any("bin/unclassified.py" in problem for problem in GUARD.verify(tmp_path))


def test_rebased_elevation_and_guidance_helpers_keep_their_finite_custody_classes():
    census = json.loads(Path(__file__).with_name("daemon-coverage.json").read_text(encoding="utf-8"))
    assert census["sites"]["finite-worker"][
        "r:plugins/agent-conduct-guidance/skills/requesting-windows-elevation/scripts/"
        "elevation-worker.ps1::<script>::Start-Process"
    ] == 1
    assert census["sites"]["caller-contained"][
        "r:plugins/agent-ssh/scripts/write_session_guidance.py::_run_owned_contributor::Popen"
    ] == 1


def test_rebased_profile_parser_and_removed_logger_bootstraps_are_classified():
    census = json.loads(Path(__file__).with_name("daemon-coverage.json").read_text(encoding="utf-8"))
    assert census["sites"]["caller-contained"][
        "r:fleet-manager/src/fleet_manager/host_profile.py::_parser::Popen"
    ] == 1
    assert not any(
        "plugins/agent-logger/scripts/bootstrap-check." in name
        for sites in census["sites"].values() for name in sites
    )


def test_session_host_direct_serve_is_the_admitted_startup_owner():
    census = json.loads(Path(__file__).with_name("daemon-coverage.json").read_text(encoding="utf-8"))
    key = "b:session_host/host.py::SessionHost.serve"
    assert census["entrypoints"][key] == "agent-bridge-session-host"
    assert census["admission_owners"]["b:session_host/host.py"] == [key]


def test_counted_classification_cannot_cover_second_launch(tmp_path):
    fixture(tmp_path, "def serve(argv):\n    Popen(argv)\n    Popen(argv)\n", {
        "caller-contained": {"s:service.py::serve::Popen": 1},
    })
    assert any("count 1 != actual 2" in problem for problem in GUARD.verify(tmp_path))


def test_stale_classification_fails(tmp_path):
    fixture(tmp_path, "def serve():\n    return 0\n", {
        "finite-worker": {"s:service.py::serve::Popen": 1},
    })
    assert any("actual 0" in problem for problem in GUARD.verify(tmp_path))


@pytest.mark.parametrize("body", [
    "def serve():\n    pass  # admit_daemon_instance('service')\n",
    "def serve():\n    detached_kwargs()\n",
    "def serve():\n    if False:\n        admit_daemon_instance('service')\n",
    "from other_owner import daemon_entrypoint\n"
    "@daemon_entrypoint('service', source=__file__)\ndef serve():\n    pass\n",
])
def test_no_comment_flags_dead_branch_or_foreign_decorator_admits(tmp_path, body):
    fixture(tmp_path, body, entries={"s:service.py::serve": "service"})
    assert any("lacks shared startup admission" in p for p in GUARD.verify(tmp_path))


def test_exact_shared_decorator_admits_async_entry(tmp_path):
    fixture(
        tmp_path,
        "from agent_procutil.daemon_instances import daemon_entrypoint as entry\n"
        "@entry('service', source=__file__)\nasync def serve():\n    pass\n",
        entries={"s:service.py::serve": "service"},
    )
    assert GUARD.verify(tmp_path) == []


def test_fail_closed_optional_import_preserves_direct_admission(tmp_path):
    fixture(
        tmp_path,
        "def serve():\n"
        "    try:\n"
        "        from agent_procutil.daemon_instances import admit_daemon_instance\n"
        "    except ImportError:\n"
        "        raise RuntimeError('service dependencies missing') from None\n"
        "    with admit_daemon_instance('service', source=__file__):\n"
        "        application_work()\n",
        entries={"s:service.py::serve": "service"},
    )
    assert GUARD.verify(tmp_path) == []


@pytest.mark.parametrize("handler", [
    "pass",
    "return",
    "application_work()",
    "raise RuntimeError(application_work())",
    "raise RuntimeError(reason=application_work())",
    "raise RuntimeError('missing') from application_work()",
])
def test_optional_import_cannot_admit_a_non_refusing_failure_branch(tmp_path, handler):
    fixture(
        tmp_path,
        "def serve():\n"
        "    try:\n"
        "        from agent_procutil.daemon_instances import admit_daemon_instance\n"
        "    except ImportError:\n"
        f"        {handler}\n"
        "    with admit_daemon_instance('service', source=__file__):\n"
        "        application_work()\n",
        entries={"s:service.py::serve": "service"},
    )
    assert any("lacks shared startup admission" in problem for problem in GUARD.verify(tmp_path))


def test_service_identity_must_match(tmp_path):
    fixture(
        tmp_path,
        "from agent_procutil.daemon_instances import daemon_entrypoint\n"
        "@daemon_entrypoint('other', source=__file__)\ndef serve():\n    pass\n",
        entries={"s:service.py::serve": "service"},
    )
    assert GUARD.verify(tmp_path)


def test_native_launches_are_counted_without_comment_allowance():
    assert GUARD.native_sites(
        "# Start-Process ignored\n"
        "Start-Process $program # daemon-spawn: allow contained\n"
        "ExecStart=/runtime/python -m service\n"
    ) == {("<script>", "Start-Process"): 1, ("<script>", "ExecStart"): 1}


def test_container_service_command_is_not_its_finite_health_probe():
    sites = GUARD.native_sites(
        'HEALTHCHECK --interval=5s \\\n'
        '    CMD ["python", "-m", "service", "ready"]\n'
        'ENTRYPOINT ["python", "-m", "service"]\n'
        'CMD ["serve"]\n', docker=True,
    )
    assert sites == {
        ("<script>", "ENTRYPOINT"): 1,
        ("<script>", "CMD"): 1,
        ("<script>", "HEALTHCHECK"): 1,
    }


def test_javascript_spawn_aliases_are_not_timer_unref():
    assert GUARD.javascript_sites(
        "import {spawn as launch} from 'node:child_process';\n"
        "launch(argv, {detached:true}).unref();\n"
        "timer.unref(); // spawn('not-code')\n"
    ) == {("<script>", "spawn"): 1}


def test_native_documentation_and_templates_are_not_admission_proof():
    text = (
        "<#\n$x = Enter-DaemonInstance -Service 'fake'\n#>\n"
        "$template = @'\n"
        "$x = Enter-DaemonInstance -Service 'fake'\n"
        "'@\n"
        "$actual = Enter-DaemonInstance -Service 'real'\n"
    )
    code = GUARD.native_executable_text(text)
    assert "'fake'" not in code
    assert "'real'" in code


def test_supported_roots_include_managers_standalone_and_tools(tmp_path):
    for root in ("plugins", "worktree-manager", "fleet-manager", "services", "tools"):
        directory = tmp_path / root
        directory.mkdir()
        (directory / "unknown.py").write_text("Popen(argv)\n", encoding="utf-8")
    (tmp_path / "tools" / "daemon-coverage.json").write_text(
        json.dumps({"prefixes": {}, "sites": {}, "entrypoints": {}}), encoding="utf-8",
    )
    assert len(GUARD.verify(tmp_path)) == 5


def test_resident_classification_requires_a_real_startup_owner(tmp_path):
    fixture(tmp_path, "def launch(argv):\n    return Popen(argv)\n", {
        "resident": {"s:service.py::launch::Popen": 1},
    })
    assert any("needs an admitted startup owner" in p for p in GUARD.verify(tmp_path))


def test_first_statement_owned_admission_is_not_a_late_body_hint(tmp_path):
    text = (
        "from agent_procutil.daemon_instances import admit_daemon_instance\n"
        "def serve():\n"
        "    guard = admit_daemon_instance('service', source=__file__)\n"
        "    try:\n        bind()\n"
        "    finally:\n        guard.close()\n"
    )
    fixture(tmp_path, text, entries={"s:service.py::serve": "service"})
    assert GUARD.verify(tmp_path) == []
    (tmp_path / "services" / "example" / "src" / "service.py").write_text(
        text.replace("    guard =", "    bind()\n    guard ="), encoding="utf-8",
    )
    assert GUARD.verify(tmp_path)


def test_native_kernel_commands_are_counted_not_python_decorated(tmp_path):
    fixture(tmp_path, "def unrelated():\n    pass\n")
    native = tmp_path / "services" / "kernel.c"
    native.write_text(
        'int main(int argc, char **argv) {\n'
        'if (!strcmp(argv[1], "supervise")) return 0;\n'
        'if (!strcmp(argv[1], "cleanup")) return 0;\nreturn 1;\n}\n',
        encoding="utf-8",
    )
    path = tmp_path / "tools" / "daemon-coverage.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["native_helpers"] = [{
        "source": "services/kernel.c", "entrypoint": "main",
        "owner": "agent_procutil.daemon_instances", "protocol": 1,
        "commands": {"supervise": "supervised-service-child", "cleanup": "finite-worker"},
    }]
    path.write_text(json.dumps(document), encoding="utf-8")
    assert GUARD.verify(tmp_path) == []
    native.write_text(native.read_text().replace(
        "return 1;", 'if (!strcmp(argv[1], "unknown")) return 0; return 1;',
    ), encoding="utf-8")
    assert any("command census differs" in p for p in GUARD.verify(tmp_path))


@pytest.mark.parametrize("fault", ["none", "extra", "dynamic", "missing-entry", "syntax"])
def test_shared_python_helper_has_an_exact_entrypoint_and_command_census(tmp_path, fault):
    source = fixture(tmp_path, "pass\n")
    code = (
        "def main():\n"
        "    parser.add_parser('supervise')\n"
        "    parser.add_parser('cleanup')\n"
    )
    if fault == "extra":
        code += "    parser.add_parser('unclassified')\n"
    elif fault == "dynamic":
        code = code.replace("'cleanup'", "unknown")
    elif fault == "missing-entry":
        code = code.replace("def main", "def different")
    elif fault == "syntax":
        code += "broken!\n"
    source.write_text(code, encoding="utf-8")
    path = tmp_path / "tools" / "daemon-coverage.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["native_helpers"] = [{
        "source": source.relative_to(tmp_path).as_posix(), "entrypoint": "main",
        "owner": "agent_procutil.daemon_instances", "protocol": 1,
        "commands": {"supervise": "supervised-service-child", "cleanup": "finite-worker"},
    }]
    path.write_text(json.dumps(document), encoding="utf-8")
    assert bool(GUARD.verify(tmp_path)) == (fault != "none")


def test_finite_artifact_helper_cannot_acquire_an_unclassified_spawn(tmp_path):
    fixture(tmp_path, "pass\n")
    helper = tmp_path / "tools" / "test_artifact_helper.py"
    helper.write_text(
        "def build():\n    subprocess.run(['compiler'], timeout=10)\n",
        encoding="utf-8",
    )
    path = tmp_path / "tools" / "daemon-coverage.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["prefixes"]["t"] = "tools"
    document["finite_helpers"] = {
        "t:test_artifact_helper.py": {
            "classification": "finite-worker", "commands": {"build": 1},
        },
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    assert GUARD.verify(tmp_path) == []
    helper.write_text(helper.read_text() + "    subprocess.Popen(argv)\n", encoding="utf-8")
    assert any("finite helper command census differs" in p for p in GUARD.verify(tmp_path))


def test_known_production_role_requires_an_explicit_declaration(tmp_path):
    source = fixture(
        tmp_path,
        "from agent_procutil.daemon_instances import daemon_entrypoint\n"
        "@daemon_entrypoint('service', source=__file__)\ndef serve():\n    pass\n",
        entries={"s:service.py::serve": "service"},
    )
    path = tmp_path / "tools" / "daemon-coverage.json"
    census = json.loads(path.read_text(encoding="utf-8"))
    census["context_required"] = []
    path.write_text(json.dumps(census), encoding="utf-8")
    assert GUARD.verify(tmp_path)
    source.write_text(source.read_text().replace(
        "source=__file__)", "source=__file__, purpose='production')",
    ), encoding="utf-8")
    assert GUARD.verify(tmp_path) == []


@pytest.mark.parametrize("relative", [
    "plugins/agent-bridge/src/agent_bridge/app_lifecycle.py",
    "plugins/agent-index/src/agent_index/server.py",
    "plugins/agent-index/server/src/agent_index_engine/app.py",
    "plugins/agent-index/src/agent_index/engine/shim.py",
])
@pytest.mark.parametrize("failed", [False, True])
def test_actual_asgi_entry_retains_admission_until_cleanup(relative, failed, monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    from agent_procutil import daemon_instances

    source = Path(__file__).resolve().parents[1] / relative
    tree = ast.parse(source.read_text(encoding="utf-8"))
    entry = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )
    events = []

    @contextmanager
    def admission(service, **kwargs):
        assert kwargs["purpose"] == "production"
        assert kwargs["source"] == str(source)
        events.append("admit")
        try:
            yield
        finally:
            events.append("closed")

    @asynccontextmanager
    async def body(app):
        events.append("body")
        try:
            yield
        finally:
            events.append("cleanup")

    monkeypatch.setattr(daemon_instances, "admit_daemon_instance", admission)
    package = ModuleType("caller_lifecycle_fixture")
    package.__path__ = []
    application = ModuleType("caller_lifecycle_fixture.app")
    application._lifespan = body
    roster = ModuleType("caller_lifecycle_fixture.app_agent_roster")

    async def stop_roster(app):
        assert events[-1] == "cleanup"
        assert "closed" not in events
        events.append("roster_cleanup")

    roster.stop_agent_roster_cache = stop_roster
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, application.__name__, application)
    monkeypatch.setitem(sys.modules, roster.__name__, roster)
    namespace = {
        "asynccontextmanager": asynccontextmanager, "__file__": str(source),
        "_lifespan": body, "__package__": package.__name__,
        "admit_daemon_instance": admission,
    }
    wrapper = ast.parse("from __future__ import annotations\n")
    wrapper.body.append(entry)
    exec(compile(wrapper, str(source), "exec"), namespace)

    async def run():
        async with namespace["lifespan"](SimpleNamespace(state=SimpleNamespace())):
            assert events == ["admit", "body"]
            if failed:
                raise RuntimeError("synthetic application failure")

    if failed:
        with pytest.raises(RuntimeError, match="synthetic"):
            asyncio.run(run())
    else:
        asyncio.run(run())
    expected = ["admit", "body", "cleanup"]
    if relative == "plugins/agent-bridge/src/agent_bridge/app_lifecycle.py":
        expected.append("roster_cleanup")
    assert events == [*expected, "closed"]
