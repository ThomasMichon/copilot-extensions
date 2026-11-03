"""Census-scoped contracts for resident daemon working directories."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("daemon_cwd_guard", ROOT / "tools" / "check-daemon-coverage.py")
assert SPEC is not None and SPEC.loader is not None
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def _fixture(
    root: Path, expression: str, *, classification: str = "resident", marker: str = "",
    parameters: str = "runtime_root", setup: str = "",
) -> None:
    source = root / "services" / "example" / "src" / "service.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import os as machine_os\n"
        "from os import getcwd as working_dir\n"
        "import pathlib as paths\n"
        "from pathlib import Path as LocalPath\n"
        "from agent_procutil import spawn_daemon as launch\n"
        "from agent_procutil.daemon_instances import daemon_entrypoint\n"
        "@daemon_entrypoint('example', source=__file__)\n"
        "def serve():\n"
        "    pass\n"
        f"def start({parameters}):\n"
        f"{setup}"
        "    return launch(['service'],\n"
        f"                  cwd={expression}, {marker}\n"
        "                  log_path=runtime_root / 'service.log')\n",
        encoding="utf-8",
    )
    tools = root / "tools"
    tools.mkdir()
    census = {
        "schema": 1,
        "prefixes": {"s": "services/example/src"},
        "sites": {classification: {"s:service.py::start::spawn_daemon": 1}},
        "entrypoints": {"s:service.py::serve": "example"},
        "context_required": ["s:service.py::serve"],
        "admission_owners": {"s:service.py": ["s:service.py::serve"]},
    }
    (tools / "daemon-coverage.json").write_text(json.dumps(census), encoding="utf-8")


@pytest.mark.parametrize("expression", [
    "machine_os.getcwd()",
    "working_dir()",
    "LocalPath.cwd()",
    "paths.Path.cwd()",
    "str(LocalPath.cwd() / 'child')",
], ids=["os-alias", "getcwd-alias", "path-alias", "pathlib-alias", "wrapped"])
def test_resident_daemon_cannot_directly_inherit_working_directory(
    tmp_path: Path, expression: str,
) -> None:
    _fixture(tmp_path, expression)
    problems = GUARD.verify(tmp_path)
    assert len(problems) == 1
    assert "inherited working directory" in problems[0]
    assert "service.py:12" in problems[0]


@pytest.mark.parametrize("expression", [
    "runtime_root",
    "LocalPath.home()",
    "machine_os.path.expanduser('~')",
    "another_object.cwd()",
], ids=["runtime-root", "home", "expanduser", "unrelated"])
def test_durable_or_unrelated_working_directory_is_not_misclassified(
    tmp_path: Path, expression: str,
) -> None:
    _fixture(tmp_path, expression)
    assert GUARD.verify(tmp_path) == []


def test_explicit_caller_containment_is_not_a_resident_cwd_contract(tmp_path: Path) -> None:
    _fixture(tmp_path, "machine_os.getcwd()", classification="caller-contained")
    assert GUARD.verify(tmp_path) == []


def test_local_import_is_traced_without_confusing_a_shadowed_parameter(tmp_path: Path) -> None:
    _fixture(tmp_path, "local_os.getcwd()", setup="    import os as local_os\n")
    assert any("inherited working directory" in problem for problem in GUARD.verify(tmp_path))

    source = tmp_path / "services" / "example" / "src" / "service.py"
    text = source.read_text(encoding="utf-8")
    source.write_text(
        text.replace("def start(runtime_root):\n    import os as local_os\n",
                     "def start(runtime_root, local_os):\n"),
        encoding="utf-8",
    )
    assert GUARD.verify(tmp_path) == []


def test_shadowed_import_does_not_make_an_unrelated_object_look_like_os(tmp_path: Path) -> None:
    _fixture(tmp_path, "machine_os.getcwd()", parameters="runtime_root, machine_os")
    assert GUARD.verify(tmp_path) == []


@pytest.mark.parametrize("expression", [
    "(lambda machine_os: machine_os.getcwd())(another_object)",
    "next(LocalPath.cwd() for LocalPath in objects)",
], ids=["lambda", "comprehension"])
def test_nested_expression_bindings_do_not_shadow_outer_imports(
    tmp_path: Path, expression: str,
) -> None:
    _fixture(tmp_path, expression)
    assert GUARD.verify(tmp_path) == []


def test_comprehension_target_does_not_shadow_enclosing_import(tmp_path: Path) -> None:
    _fixture(tmp_path, "machine_os.getcwd()", setup="    [machine_os for machine_os in objects]\n")
    assert any("inherited working directory" in problem for problem in GUARD.verify(tmp_path))


def test_comprehension_assignment_can_shadow_enclosing_import(tmp_path: Path) -> None:
    _fixture(tmp_path, "machine_os.getcwd()", setup="    [(machine_os := item) for item in objects]\n")
    assert GUARD.verify(tmp_path) == []


def test_malformed_resident_site_is_reported_without_crashing(tmp_path: Path) -> None:
    _fixture(tmp_path, "runtime_root")
    census = tmp_path / "tools" / "daemon-coverage.json"
    data = json.loads(census.read_text(encoding="utf-8"))
    data["sites"]["resident"] = {"s:service.py::start": 1}
    census.write_text(json.dumps(data), encoding="utf-8")
    assert any("census count" in problem for problem in GUARD.verify(tmp_path))


def test_nested_cwd_is_checked_only_for_its_own_census_classification(tmp_path: Path) -> None:
    _fixture(tmp_path, "runtime_root")
    source = tmp_path / "services" / "example" / "src" / "service.py"
    text = source.read_text(encoding="utf-8")
    text = text.replace(
        "def start(runtime_root):\n",
        "def start(runtime_root):\n"
        "    def child():\n"
        "        return launch(['service'], cwd=machine_os.getcwd(), log_path=runtime_root / 'child.log')\n",
    )
    source.write_text(text, encoding="utf-8")
    census = tmp_path / "tools" / "daemon-coverage.json"
    data = json.loads(census.read_text(encoding="utf-8"))
    nested = "s:service.py::start.child::spawn_daemon"
    data["sites"]["caller-contained"] = {nested: 1}
    census.write_text(json.dumps(data), encoding="utf-8")
    assert GUARD.verify(tmp_path) == []
    data["sites"]["resident"][nested] = 1
    del data["sites"]["caller-contained"]
    census.write_text(json.dumps(data), encoding="utf-8")
    assert any("inherited working directory" in problem for problem in GUARD.verify(tmp_path))


def test_actual_owner_launch_rejects_an_inherited_cwd_mutation(tmp_path: Path) -> None:
    _fixture(tmp_path, "runtime_root")
    original = ROOT / "plugins" / "agent-codespaces" / "src" / "agent_codespaces" / "owner_startup.py"
    text = original.read_text(encoding="utf-8")
    text += (
        "\nfrom agent_procutil.daemon_instances import daemon_entrypoint\n"
        "@daemon_entrypoint('example', source=__file__)\n"
        "def serve():\n"
        "    pass\n"
    )
    source = tmp_path / "services" / "example" / "src" / "service.py"
    source.write_text(text, encoding="utf-8")
    census = tmp_path / "tools" / "daemon-coverage.json"
    data = json.loads(census.read_text(encoding="utf-8"))
    data["sites"]["resident"] = {"s:service.py::spawn_owner_process::spawn_daemon": 1}
    census.write_text(json.dumps(data), encoding="utf-8")
    assert GUARD.verify(tmp_path) == []
    before = 'cwd=os.path.expanduser("~"),'
    assert text.count(before) == 1
    source.write_text(text.replace(before, "cwd=os.getcwd(),", 1), encoding="utf-8")
    assert any("inherited working directory" in problem for problem in GUARD.verify(tmp_path))


@pytest.mark.parametrize(
    "marker,allowed",
    [
        ("# daemon-spawn: allow explicitly bounded fixture", True),
        ("# daemon-spawn: allow", False),
        ('# other "daemon-spawn: allow not a reason"', False),
    ],
    ids=["reason", "missing-reason", "other-comment"],
)
def test_cwd_exception_requires_a_real_reasoned_comment(
    tmp_path: Path, marker: str, allowed: bool,
) -> None:
    _fixture(tmp_path, "machine_os.getcwd()", marker=marker)
    problems = GUARD.verify(tmp_path)
    assert (problems == []) is allowed
    if not allowed:
        assert any("inherited working directory" in problem for problem in problems)


def test_cwd_exception_may_precede_the_argument(tmp_path: Path) -> None:
    _fixture(tmp_path, "machine_os.getcwd()")
    source = tmp_path / "services" / "example" / "src" / "service.py"
    text = source.read_text(encoding="utf-8")
    source.write_text(
        text.replace("                  cwd=", "                  # daemon-spawn: allow bounded fixture\n"
                     "                  cwd=", 1),
        encoding="utf-8",
    )
    assert GUARD.verify(tmp_path) == []
