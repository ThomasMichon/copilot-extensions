"""Focused inventory and actual-checker controls for the local typecheck front door."""
from __future__ import annotations

import ast
from dataclasses import replace
import importlib.machinery
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Callable, cast
from uuid import uuid4

import pytest

from tools import plugin_test_resources, typecheck
from tools import typecheck_source_views as views
from installer_dependency_policy import DependencyPolicyError


@pytest.fixture(autouse=True)
def fixed_checker_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(typecheck, "uuid4", lambda: SimpleNamespace(hex=""))


@pytest.fixture(autouse=True)
def _isolated_scratch(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep temporary checkouts' environments out of the shared scratch root."""
    real = plugin_test_resources.resource_path
    original = os.environ.get("AGENT_SCRATCH_ROOT")
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(tmp_path_factory.mktemp("scratch")))

    def resource_path(root: Path) -> Path:
        if root.resolve() != typecheck.ROOT.resolve():
            return real(root)
        with monkeypatch.context() as scoped:
            if original is None:
                scoped.delenv("AGENT_SCRATCH_ROOT")
            else:
                scoped.setenv("AGENT_SCRATCH_ROOT", original)
            return real(root)

    monkeypatch.setattr(plugin_test_resources, "resource_path", resource_path)


def _harness_emitted_fixture(root: Path) -> tuple[str, ...]:
    declaration = json.loads((typecheck.ROOT / views.HARNESS_DECLARATION).read_bytes())
    paths = tuple(row["path"] for row in declaration["members"])
    for relative in (*paths, *(row["path"] for row in declaration["assets"]), views.HARNESS_DECLARATION):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((typecheck.ROOT / relative).read_bytes())
    return paths


def test_harness_emitted_exact_roles_and_escaping(tmp_path: Path) -> None:
    paths = _harness_emitted_fixture(tmp_path)
    inputs, programs = views.harness_emitted_inputs(tmp_path, paths)
    assert len(programs) == 20
    assert len(inputs) == len(paths) + 4
    assert any(b"pytest.mark.effect('process')" in body for _, body, _, _ in programs)
    assert any(b"raise SystemExit(7)" in body for _, body, _, _ in programs)
    assert sum(b"\r\n" in body for _, body, _, _ in programs) == 6
    for _, body, first, last in programs:
        compile(body, "<portable-typing-witness>", "exec")
        assert 1 <= first <= last


@pytest.mark.parametrize("change", ["private-record", "drop-epoch", "extra-member", "hidden-producer"])
def test_harness_emitted_refuses_incomplete_or_private_declaration(
    tmp_path: Path, change: str,
) -> None:
    import hashlib
    paths = _harness_emitted_fixture(tmp_path)
    path = tmp_path / views.HARNESS_DECLARATION
    data = json.loads(path.read_bytes())
    if change == "private-record":
        data["capture_record_id"] = "private-record"
    elif change == "drop-epoch":
        data["programs"].pop()
    elif change == "extra-member":
        paths = (*paths, views.HARNESS_PREFIX + "new_owned.py")
    else:
        import ast
        source = tmp_path / data["members"][0]["path"]
        source.write_bytes(source.read_bytes() + b"\nnew_unreviewed = ['-c', 'print(1)']\n")
        data["members"][0]["sha256"] = [hashlib.sha256(source.read_bytes()).hexdigest()]
        data["members"][0]["structure"] = views._shape(ast.parse(source.read_bytes()))
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(views.SourceViewError):
        views.harness_emitted_inputs(tmp_path, paths)


@pytest.mark.parametrize("change", ["module", "class", "async", "duplicate-epoch", "reassignment"])
def test_harness_emitted_census_rejects_reviewed_digest_hidden_sites(
    tmp_path: Path, change: str,
) -> None:
    import ast
    import hashlib
    paths = _harness_emitted_fixture(tmp_path)
    declaration_path = tmp_path / views.HARNESS_DECLARATION
    data = json.loads(declaration_path.read_bytes())
    relative = views.HARNESS_PREFIX + "tests/test_test_runner_containment.py"
    source = tmp_path / relative
    content = source.read_text(encoding="utf-8")
    if change == "module":
        content += "\nsubprocess.run([sys.executable, '-c', \"x: int = 'wrong'\"])\n"
    elif change == "class":
        content += "\nclass Hidden:\n    def run(self) -> None:\n        subprocess.run([sys.executable, '-c', 'pass'])\n"
    elif change == "async":
        content += "\nasync def hidden() -> None:\n    subprocess.run([sys.executable, '-c', 'pass'])\n"
    elif change == "duplicate-epoch":
        content = content.replace(
            "    assert rc == pytest.ExitCode.OK\n",
            "    assert rc == pytest.ExitCode.OK\n"
            "    test_file.write_text(\"import pytest\\n@pytest.mark.portfolio_tier('T0')\\n"
            "@pytest.mark.effect('process')\\ndef test_invalid() -> None: pass\\n\", encoding='utf-8')\n"
        )
    else:
        launch = (
            "    env = isolated_environment(os.environ, tmp_path)\n    rc = run_contained(\n"
            "        [sys.executable, \"-c\", script],"
        )
        content = content.replace(launch, "    script = 'pass'\n" + launch, 1)
    source.write_text(content, encoding="utf-8")
    tree = ast.parse(source.read_bytes())
    member = next(row for row in data["members"] if row["path"] == relative)
    member["sha256"] = [hashlib.sha256(source.read_bytes()).hexdigest()]
    member["structure"] = views._shape(tree)
    declaration_path.write_text(json.dumps(data), encoding="utf-8")
    match = (
        "Incomplete/extra" if change == "duplicate-epoch" else
        "ambiguous reassigned" if change == "reassignment" else
        "Unsupported module/class/async"
    )
    with pytest.raises(views.SourceViewError, match=match):
        views.harness_emitted_inputs(tmp_path, paths)


def test_harness_emitted_candidate_data_does_not_follow_host_drift(tmp_path: Path) -> None:
    paths = _harness_emitted_fixture(tmp_path / "source")
    _harness_emitted_fixture(tmp_path / "candidate")
    candidate = views.harness_emitted_views(tmp_path / "candidate", paths, tmp_path / "stage")
    assert len(candidate) == 20
    host = tmp_path / "source" / paths[0]
    host.write_bytes(host.read_bytes() + b"\n# host drift\n")
    for view in candidate:
        view.verify(tmp_path / "candidate")
    changed = tmp_path / "candidate" / paths[0]
    changed.write_bytes(changed.read_bytes() + b"\n# candidate drift\n")
    with pytest.raises(views.SourceViewError, match="source changed"):
        candidate[0].verify(tmp_path / "candidate")
    for view in candidate:
        view.output.unlink()


def test_harness_shared_binding_verification_still_checks_every_output(tmp_path: Path) -> None:
    paths = _harness_emitted_fixture(tmp_path)
    owner = typecheck._SourceViews(tmp_path, tmp_path / "stage")
    emitted = owner.add_harness(paths)
    last = emitted[-1].output
    content = last.read_bytes()
    last.write_bytes(content + b"\n# changed output\n")
    with pytest.raises(views.SourceViewError, match="Checker output changed"):
        owner.verify()
    last.write_bytes(content)
    extra = tmp_path / views.HARNESS_PREFIX / "unexpected.py"
    extra.write_text("pass\n", encoding="utf-8")
    with pytest.raises(views.SourceViewError, match="membership changed"):
        owner.verify()
    extra.unlink()
    owner.verify()
    owner.cleanup()


def test_harness_emitted_procutil_provider_is_candidate_local_and_unambiguous(tmp_path: Path) -> None:
    directory = tmp_path / "libs/agent-procutil/src"
    package = directory / "agent_procutil"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    assert typecheck._harness_procutil_provider(tmp_path, [str(directory)]) == package
    foreign = tmp_path / "other"
    foreign.mkdir()
    (foreign / "agent_procutil.py").write_text("", encoding="utf-8")
    assert typecheck._harness_procutil_provider(tmp_path, [str(directory), str(foreign)]) == package
    with pytest.raises(typecheck.TypecheckError, match="winning candidate-local"):
        typecheck._harness_procutil_provider(tmp_path, [str(foreign), str(directory)])
    (package / "__init__.pyi").write_text("", encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError, match="stub"):
        typecheck._harness_procutil_provider(tmp_path, [str(directory)])
    (package / "__init__.pyi").unlink()
    (directory / "agent_procutil.pyi").write_text("", encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError, match="stub"):
        typecheck._harness_procutil_provider(tmp_path, [str(directory)])
    (directory / "agent_procutil.pyi").unlink()
    stub = directory / "agent_procutil-stubs"
    stub.mkdir()
    (stub / "__init__.pyi").write_text("", encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError, match="stub"):
        typecheck._harness_procutil_provider(tmp_path, [str(directory)])


def test_harness_emitted_actual_mypy_ordered_provider_precedence(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    later = tmp_path / "later"
    for root, annotation, value in [(canonical, "int", "1"), (later, "str", "'later'")]:
        package = root / "agent_procutil"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(
            f"def answer() -> {annotation}:\n    return {value}\n", encoding="utf-8",
        )
    client = tmp_path / "client.py"
    client.write_text("from agent_procutil import answer\nvalue: int = answer()\n", encoding="utf-8")
    selected = next(p for p in typecheck.load_projects() if p.name == "checker")
    environment, _digest = typecheck.environment_path(typecheck.ROOT, selected)
    env = dict(os.environ)
    env["MYPYPATH"] = os.pathsep.join((str(canonical), str(later)))
    for platform in ("linux", "win32"):
        result = typecheck._run([
            str(typecheck.environment_python(environment)), "-I", "-m", "mypy",
            "--strict", "--python-version", "3.10", "--platform", platform,
            "--cache-dir", os.devnull, "--follow-imports", "silent", str(client),
        ], tmp_path, env=env)
        assert result.returncode == 0, result.stdout + result.stderr


def test_harness_emitted_real_body_error_maps_to_producer_range(tmp_path: Path) -> None:
    import ast
    import hashlib
    paths = _harness_emitted_fixture(tmp_path)
    declaration_path = tmp_path / views.HARNESS_DECLARATION
    declaration = json.loads(declaration_path.read_bytes())
    row = next(r for r in declaration["programs"] if r["role"] == "exit-seven")
    source = tmp_path / row["path"]
    source.write_bytes(source.read_bytes().replace(
        b"print('contained-output');raise SystemExit(7)",
        b"x: int = 'wrong';print('contained-output');raise SystemExit(7)",
    ))
    tree = ast.parse(source.read_bytes())
    member = next(r for r in declaration["members"] if r["path"] == row["path"])
    member["sha256"] = [hashlib.sha256(source.read_bytes()).hexdigest()]
    member["structure"] = views._shape(tree)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == row["function"])
    expression = next(
        n.elts[i + 1] for n in ast.walk(fn) if isinstance(n, ast.List)
        for i, value in enumerate(n.elts[:-1])
        if isinstance(value, ast.Constant) and value.value == "-c"
    )
    row["expression"] = views._shape(expression)
    container = next(
        n for n in ast.walk(fn) if isinstance(n, ast.List) and expression in n.elts
    )
    row["sites"] = [[container.lineno, container.col_offset, container.end_lineno, container.end_col_offset]]
    declaration_path.write_text(json.dumps(declaration), encoding="utf-8")
    emitted = views.harness_emitted_views(tmp_path, paths, tmp_path / "stage")
    item = next(v for v in emitted if b"x: int = 'wrong'" in v.content)
    selected = next(p for p in typecheck.load_projects() if p.name == "checker")
    environment, _digest = typecheck.environment_path(typecheck.ROOT, selected)
    result = typecheck._run([
        str(typecheck.environment_python(environment)), "-I", "-m", "mypy",
        "--strict", "--python-version", "3.10", "--platform", "win32",
        "--cache-dir", os.devnull, "--follow-imports", "silent",
        str(item.output),
    ], tmp_path)
    assert result.returncode != 0
    message = item.diagnostics(result.stdout, tmp_path)
    assert "[assignment]" in message
    assert row["path"] in message
    assert "emitted expression range" in message


def project(name: str = "sample", level: typecheck.Level = "strict") -> typecheck.Project:
    """Declare one synthetic Python input under its test workspace."""
    return typecheck.Project(
        name, ("sample.py",), (), level, "3.10", ("linux",), "requirements.txt",
    )


def test_inventory_rejects_new_or_ambiguous_python() -> None:
    """An added Python path cannot silently escape or share an admitted owner."""
    item = project()
    with pytest.raises(typecheck.TypecheckError, match="unclassified"):
        typecheck.inventory(("new.py",), (item,))
    with pytest.raises(typecheck.TypecheckError, match="expected one owner"):
        typecheck.inventory(("sample.py",), (item, project("other")))
    with pytest.raises(typecheck.TypecheckError, match="Empty Python inventory"):
        typecheck.inventory((), (item,))


def test_directory_prefixes_do_not_claim_neighboring_components() -> None:
    """An exact component prefix includes nested code but not lookalike siblings."""
    item = typecheck.Project(
        "sample", ("plugins/sample",), ("plugins/sample/tests",),
        "basic", "3.10", ("linux",), "requirements.txt",
    )
    assert item.owns("plugins/sample/src/nested/module.py")
    assert not item.owns("plugins/sample-other/module.py")
    assert not item.owns("plugins/sample/tests/test_module.py")


@pytest.mark.parametrize("service", ["dispatch", "index", "pr", "ssh"])
def test_materialized_service_libraries_keep_independent_admission(service: str) -> None:
    projects = typecheck.load_projects()
    root = f"services/{service}"
    owned = (f"{root}/src/service.py", f"{root}/tests/test_service.py", f"{root}/libs-other/helper.py")
    copied = f"{root}/libs/repository-policy/repository_policy.py"
    canonical = "libs/repository-policy/repository_policy.py"
    owners = typecheck.inventory((*owned, copied, canonical), projects)
    assert all(owners[path].name == f"service-{service}" and owners[path].level == "strict" for path in owned)
    assert owners[copied].name == "service-vendored-libraries"
    assert owners[copied].level == "pending"
    assert owners[canonical].name == "lib-repository-policy"
    assert owners[canonical].level == "strict"


def test_paths_cannot_inject_response_file_arguments() -> None:
    """Git filename line separators cannot become MyPy options."""
    for separator in ("\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
        path = "libs/worktree-identity/src/benign.pyi" + separator + "--ignore-errors" + separator + "cache.py"
        with pytest.raises(typecheck.TypecheckError, match="repository-relative path"):
            typecheck._relative(path)


def test_checker_bootstrap_does_not_require_python311_imports() -> None:
    """Exercise the import closure without the modules that broke Python 3.10."""
    source = (
        "import builtins, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "original = builtins.__import__\n"
        "def guarded(name, *args, **kwargs):\n"
        "    if name in {'tomllib', 'installer_engine'}:\n"
        "        raise ImportError('Python 3.11-only import reached')\n"
        "    return original(name, *args, **kwargs)\n"
        "builtins.__import__ = guarded\n"
        "import tools.typecheck\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", source, str(typecheck.ROOT)],
        capture_output=True, text=True, timeout=30,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    assert result.returncode == 0, result.stderr


def test_git_inventory_ignores_foreign_repository_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual Git process inventories the explicit source root, not a wrapper's."""
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "foreign.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "foreign-index"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    assert "tools/typecheck.py" in typecheck.python_paths(typecheck.ROOT)


def test_manifest_rejects_duplicate_keys_and_path_escape(tmp_path: Path) -> None:
    """Malformed ownership cannot relax the checker policy."""
    tools = tmp_path / "tools"
    tools.mkdir()
    manifest = tools / "typecheck-projects.json"
    manifest.write_text('{"version":1,"version":1}', encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError, match="Duplicate manifest field"):
        typecheck.load_projects(tmp_path)
    manifest.write_text(json.dumps({
        "version": 1, "python_version": "3.10", "platforms": ["linux"],
        "requirements": "requirements.txt",
        "projects": [{
            "name": "sample", "include": ["../outside"], "exclude": [], "level": "strict",
        }],
    }), encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError, match="repository-relative path"):
        typecheck.load_projects(tmp_path)


def test_additional_versions_preserve_minimum_defaults_and_physical_ownership(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    row: dict[str, object] = {
        "name": "sample", "include": ["sample.py"], "exclude": [], "level": "strict",
    }
    manifest = {
        "version": 1, "python_version": "3.10", "platforms": ["linux", "win32"],
        "requirements": "requirements.txt", "projects": [row],
    }
    path = tools / "typecheck-projects.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    default = typecheck.load_projects(tmp_path)
    assert default == (typecheck.Project(
        "sample", ("sample.py",), (), "strict", "3.10", ("linux", "win32"), "requirements.txt",
    ),)
    row["additional_python_versions"] = []
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert typecheck.load_projects(tmp_path) == default
    row["additional_python_versions"] = ["3.11"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    matrix = typecheck.load_projects(tmp_path)
    assert matrix[0].python_version == default[0].python_version
    assert matrix[0].additional_python_versions == ("3.11",)
    assert tuple(typecheck.inventory(("sample.py",), matrix)) == ("sample.py",)


@pytest.mark.parametrize("additional", [
    None, "3.11", [3.11], [""], ["3.11.0"], ["4.0"], ["3.011"],
    ["3.10"], ["3.9"], ["3.11", "3.11"],
])
def test_manifest_rejects_invalid_additional_version_policy(tmp_path: Path, additional: object) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "typecheck-projects.json").write_text(json.dumps({
        "version": 1, "python_version": "3.10", "platforms": ["linux", "win32"],
        "requirements": "requirements.txt",
        "projects": [{
            "name": "sample", "include": ["sample.py"], "exclude": [], "level": "strict",
            "additional_python_versions": additional,
        }],
    }), encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError):
        typecheck.load_projects(tmp_path)


def test_optional_constructor_positions_and_combined_manifest(tmp_path: Path) -> None:
    for name in ("tools", "helpers"):
        (tmp_path / name).mkdir()
    item = typecheck.Project(
        "sample", ("sample.py",), (), "strict", "3.10", ("linux", "win32"),
        "requirements.txt", ("3.11",), import_roots=("helpers",),
    )
    (tmp_path / "tools/typecheck-projects.json").write_text(json.dumps({
        "version": 1, "python_version": "3.10", "platforms": ["linux", "win32"],
        "requirements": "requirements.txt", "projects": [{
            "name": "sample", "include": ["sample.py"], "exclude": [], "level": "strict",
            "additional_python_versions": ["3.11"], "import_roots": ["helpers"],
        }],
    }), encoding="utf-8")
    assert typecheck.load_projects(tmp_path) == (item,)
    assert not item.owns("helpers/contract.py")
    assert project().additional_python_versions == () and project().import_roots == ()


@pytest.mark.parametrize("mutate", [False, True])
@pytest.mark.parametrize("fresh_namespace", [False, True])
def test_combined_matrix_preserves_import_custody_and_failure_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: bool, fresh_namespace: bool,
) -> None:
    (tmp_path / "helpers").mkdir()
    helper = tmp_path / "helpers/contract.py"
    helper.write_text("value: int = 1\n", encoding="utf-8")
    (tmp_path / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    retained = {
        tmp_path / "typecheck-targets.txt": b"other invocation targets\n",
        tmp_path / "typecheck-mypy.ini": b"other invocation configuration\n",
    }
    for path, content in retained.items():
        path.write_bytes(content)
    item = replace(
        project(), additional_python_versions=("3.11",), platforms=("linux", "win32"),
        import_roots=("helpers",),
    )
    if fresh_namespace:
        monkeypatch.setattr(typecheck, "uuid4", uuid4)
        old_stage = tmp_path.parent / ("source-views-" + tmp_path.name) / item.name
        old_stage.mkdir(parents=True)
        retained[old_stage / "retained.py"] = b"other invocation source view\n"
        (old_stage / "retained.py").write_bytes(retained[old_stage / "retained.py"])
    lease = Lease()
    calls: list[tuple[str, str]] = []

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return tmp_path, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert argv[argv.index("--cache-dir") + 1] == os.devnull
        assert env is not None and str(tmp_path / "helpers") in env["MYPYPATH"]
        assert Path(argv[-1][1:]).read_text(encoding="utf-8") == "sample.py\n"
        assert Path(argv[argv.index("--config-file") + 1]).read_text(encoding="utf-8") == "[mypy]\n"
        calls.append((argv[argv.index("--python-version") + 1], argv[argv.index("--platform") + 1]))
        if mutate and len(calls) == 3:
            helper.write_text("value: str = 'wrong'\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 7 if len(calls) == 1 else 0, "", "")

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(typecheck, "_run", run)
    if mutate:
        with pytest.raises(typecheck.TypecheckError, match="inputs changed"):
            typecheck.check_project(
                tmp_path, item, ("sample.py",), all_paths=("sample.py", "helpers/contract.py"),
            )
        assert calls == [("3.10", "linux"), ("3.10", "win32"), ("3.11", "linux")]
    else:
        assert typecheck.check_project(
            tmp_path, item, ("sample.py",), all_paths=("sample.py", "helpers/contract.py"),
        ) == 7
        assert calls == [("3.10", "linux"), ("3.10", "win32"), ("3.11", "linux"), ("3.11", "win32")]
    assert lease.released
    assert all(path.read_bytes() == content for path, content in retained.items())


def _matrix_git(root: Path, *arguments: str) -> str:
    result = typecheck._run(["git", *arguments], root, env=typecheck.git_environment())
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize(("additional", "regression"), [
    (False, "3.11"), (True, "none"), (True, "3.10"), (True, "3.11"),
])
def test_real_source_and_frozen_candidate_frontdoors_enforce_version_matrix(
    tmp_path: Path, additional: bool, regression: str,
) -> None:
    """Only test-interpreter selection is adapted; policy, Git transport and MyPy are real."""
    from tools import promote_release as promotion
    from tools import promotion_frozen_candidate as frozen

    policy = next(item for item in typecheck.load_projects() if item.name == "lib-versioned-runtime")
    root = tmp_path / "source"
    root.mkdir()
    closure = [
        "tools/typecheck.py", "tools/_admission_protocol.py", "tools/cache_directory_paths.py",
        "tools/plugin_test_resources.py",
        "tools/typecheck_source_views.py", "tools/typecheck_catalog_views.py",
        "tools/typecheck_bootstrap_views.py",
        "tools/native_test_paths.py", "tools/typecheck-requirements.txt",
        *(
            "libs/installer-engine/" + name
            for name in (
                "installer_dependency_policy.py", "installer_dependency_config.py",
                "installer_dependency_arguments.py", "installer_retained_content.py",
            )
        ),
        *(
            path.relative_to(typecheck.ROOT).as_posix()
            for path in (typecheck.ROOT / "libs/single-instance-lease/src").rglob("*.py")
        ),
    ]
    for name in closure:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(typecheck.ROOT / name, target)
    row: dict[str, object] = {
        "name": "sample", "include": ["sample.py"], "exclude": [], "level": "strict",
    }
    if additional:
        row["additional_python_versions"] = list(policy.additional_python_versions)
    (root / "tools/typecheck-projects.json").write_text(json.dumps({
        "version": 1, "python_version": policy.python_version if additional else "3.10",
        "platforms": ["linux", "win32"],
        "requirements": "tools/typecheck-requirements.txt", "projects": [
            row, {"name": "tooling", "include": ["tools", "libs"], "exclude": [], "level": "pending"},
        ],
    }), encoding="utf-8")
    (root / ".gitignore").write_text(".test-venvs/\n__pycache__/\n", encoding="utf-8")
    source = (
        "import sys\nfrom pathlib import Path\nfrom typing import TYPE_CHECKING\n"
        "def read_project(payload: Path) -> dict[str, object]:\n"
        "    if sys.version_info < (3, 11) and TYPE_CHECKING:\n"
        "        raise ModuleNotFoundError(\"No module named 'tomllib'\", name='tomllib')\n"
        "    else:\n"
        "        import tomllib\n"
        + ("        wrong: int = 'bad'\n" if regression == "3.11" else "")
        + "        return tomllib.loads(payload.read_text(encoding='utf-8'))\n"
        + ("if sys.version_info < (3, 11):\n    wrong_minimum: int = 'bad'\n" if regression == "3.10" else "")
    )
    (root / "sample.py").write_text(source, encoding="utf-8")
    _matrix_git(root, "init")
    _matrix_git(root, "config", "core.autocrlf", "false")
    _matrix_git(root, "config", "user.name", "Example")
    _matrix_git(root, "config", "user.email", "example@example.com")
    _matrix_git(root, "add", ".")
    _matrix_git(root, "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic base")
    main_before = _matrix_git(root, "rev-parse", "HEAD")
    _matrix_git(root, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Synthetic source")
    dev_sha = _matrix_git(root, "rev-parse", "HEAD")
    content_tree = _matrix_git(root, "rev-parse", "HEAD^{tree}")
    prepared_at = "2026-01-02T03:04:05Z"
    state = root / promotion.PIPELINE_STATE_PATH
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"last_promotion": {
        "dev_head": dev_sha, "main_before": main_before, "promoted_at": prepared_at,
    }}), encoding="utf-8")
    _matrix_git(root, "add", promotion.PIPELINE_STATE_PATH)
    tree = _matrix_git(root, "write-tree")
    candidate_sha = _matrix_git(root, "commit-tree", tree, "-p", main_before, "-m", "Synthetic candidate")
    transport = tmp_path / "frozen"
    frozen.export_candidate(
        transport, repo=root, report={
            "dev_head": dev_sha, "main_before": main_before, "commit": candidate_sha,
        }, content_tree=content_tree, prepared_at=prepared_at,
    )
    record = frozen.import_candidate(
        transport, repo=root, dev_sha=dev_sha, main_before=main_before, candidate_sha=candidate_sha,
    )
    candidate = tmp_path / "candidate"
    _matrix_git(root, "worktree", "add", "--detach", str(candidate), candidate_sha)
    bootstrap = (
        "import sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\n"
        "from tools import typecheck\n"
        "typecheck.environment_python = lambda path: Path(sys.executable)\n"
        "raise SystemExit(typecheck.main([sys.argv[2]]))\n"
    )
    expected = int(regression == "3.10" or (additional and regression == "3.11"))
    for checkout in (root, candidate):
        item = typecheck.load_projects(checkout)[0]
        environment, digest = typecheck.environment_path(checkout, item)
        environment.mkdir(parents=True)
        (environment / ".typecheck-prepared").write_text(digest + "\n", encoding="utf-8")
        for action in ("--prepare", "--enforced"):
            result = typecheck._run(
                [sys.executable, "-I", "-B", "-c", bootstrap, str(checkout), action], checkout,
            )
            assert result.returncode == (0 if action == "--prepare" else expected), result.stdout + result.stderr
            if action == "--prepare":
                continue
            versions = ("3.10", "3.11") if additional else ("3.10",)
            for version in versions:
                for platform in ("linux", "win32"):
                    label = f"[strict] sample (Python {version}, {platform},"
                    segment = result.stdout.split(label, 1)[1].split("[strict] sample", 1)[0]
                    assert (": error:" in segment) == (regression == version)
                    assert (environment / "mypy-cache" / platform / version).is_dir()
            assert result.stdout.count("[strict] sample (Python ") == len(versions) * 2
        if checkout == root:
            (root / "tools/typecheck-projects.json").write_text("{}", encoding="utf-8")
            (root / "sample.py").write_text("changed: int = 0\n", encoding="utf-8")
    assert frozen.import_candidate(
        transport, repo=root, dev_sha=dev_sha, main_before=main_before, candidate_sha=candidate_sha,
    ) == record
    assert (candidate / "sample.py").read_text(encoding="utf-8") == source
    assert (candidate / "tools/typecheck.py").read_bytes() == (typecheck.ROOT / "tools/typecheck.py").read_bytes()


@pytest.mark.timeout(30)
@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize("python_version", ["3.10", "3.11"])
@pytest.mark.parametrize("bad_template", [False, True])
@pytest.mark.parametrize("frontdoor", ["source", "frozen"])
def test_real_catalog_source_and_frozen_frontdoors(
    tmp_path: Path, platform: str, python_version: str, bad_template: bool, frontdoor: str,
    record_property: Callable[[str, object], None],
) -> None:
    _real_catalog_frontdoors(
        tmp_path, platform, python_version, bad_template, frontdoor, record_property,
    )


@pytest.mark.timeout(30)
@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize("frontdoor", ["source", "frozen"])
def test_real_bootstrap_source_and_frozen_frontdoors(
    tmp_path: Path, platform: str, frontdoor: str,
    record_property: Callable[[str, object], None],
) -> None:
    _real_catalog_frontdoors(
        tmp_path, platform, "3.11", False, frontdoor, record_property, bad_bootstrap=True,
    )


def _real_catalog_frontdoors(
    tmp_path: Path, platform: str, python_version: str, bad_template: bool, frontdoor: str,
    record_property: Callable[[str, object], None], *, bad_bootstrap: bool = False,
) -> None:
    """Reuse the test SDK, but exercise real Git transport, checker and MyPy."""
    from tools import promote_release as promotion
    from tools import promotion_frozen_candidate as frozen
    from tools import typecheck_catalog_views as catalog
    from tools import typecheck_bootstrap_views as bootstrap_views
    from tools.typecheck_source_views import _shape

    root = tmp_path / "source"
    root.mkdir()
    closure = {
        "tools/typecheck.py", "tools/_admission_protocol.py", "tools/cache_directory_paths.py",
        "tools/plugin_test_resources.py",
        "tools/typecheck_source_views.py", "tools/typecheck_catalog_views.py",
        "tools/typecheck_bootstrap_views.py",
        "tools/native_test_paths.py", "tools/typecheck-requirements.txt",
        catalog.DECLARATION, *catalog.MEMBERS, *bootstrap_views.INPUTS,
        *(
            "libs/installer-engine/" + name for name in (
                "installer_dependency_policy.py", "installer_dependency_config.py",
                "installer_dependency_arguments.py", "installer_retained_content.py",
            )
        ),
        *(
            path.relative_to(typecheck.ROOT).as_posix()
            for path in (typecheck.ROOT / "libs/single-instance-lease/src").rglob("*.py")
        ),
        *(
            path.relative_to(typecheck.ROOT).as_posix()
            for library in ("agent-procutil", "zdd")
            for path in (typecheck.ROOT / "libs" / library / "src").rglob("*.py")
        ),
        *(
            path.relative_to(typecheck.ROOT).as_posix()
            for path in (typecheck.ROOT / catalog.PREFIX / "templates").glob("*.tmpl")
        ),
    }
    for relative in sorted(closure):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(typecheck.ROOT / relative, destination)
    template = root / catalog.TEMPLATE
    if bad_template:
        lines = template.read_text(encoding="utf-8").splitlines(keepends=True)
        assert lines[59].startswith("    return context +")
        lines[59] = "    return 0\n"
        template.write_text("".join(lines), encoding="utf-8")
    if bad_bootstrap:
        producer = root / catalog.PRODUCER
        original = producer.read_text(encoding="utf-8")
        changed = original.replace(
            "print(sys.executable,flush=True); ",
            "probe: int = ''wrong''; print(sys.executable,flush=True); ", 1,
        )
        assert changed != original
        producer.write_text(changed, encoding="utf-8")
        producer_tree = ast.parse(producer.read_bytes())
        (root / catalog.DECLARATION).write_text(json.dumps({
            "kind": "in-process-catalog-v1", "module": _shape(producer_tree),
            "functions": {n.name: _shape(n) for n in producer_tree.body
                          if isinstance(n, ast.FunctionDef)
                          and n.name in {"_load_command", "load_manifest", "render", "expected_files"}},
        }), encoding="utf-8")
    expected_template = template.read_bytes()
    expected_declaration = (root / catalog.DECLARATION).read_bytes()
    policy = next(p for p in typecheck.load_projects() if p.name == "lib-payload-invocation")
    assert policy.level == "pending" and policy.additional_python_versions == ("3.11",)
    declared_versions = (policy.python_version, *policy.additional_python_versions)
    assert declared_versions == ("3.10", "3.11")
    assert python_version in declared_versions
    record_property("production_python_policy", json.dumps({
        "python_version": policy.python_version,
        "additional_python_versions": list(policy.additional_python_versions),
    }, sort_keys=True))
    record_property("partition_python_version", python_version)
    (root / "tools/typecheck-projects.json").write_text(json.dumps({
        "version": 1, "python_version": policy.python_version, "platforms": [platform],
        "requirements": policy.requirements, "projects": [
            {
                "name": policy.name, "include": list(policy.include), "exclude": list(policy.exclude),
                "level": policy.level, "python_version": python_version,
                "additional_python_versions": [],
            },
            {
                "name": "tooling",
                "include": [
                    "tools", "libs/installer-engine", "libs/single-instance-lease",
                    "libs/agent-procutil", "libs/zdd",
                ],
                "exclude": [], "level": "pending",
            },
        ],
    }), encoding="utf-8")
    (root / ".gitignore").write_text(".test-venvs/\n__pycache__/\n", encoding="utf-8")
    _matrix_git(root, "init")
    _matrix_git(root, "config", "core.autocrlf", "false")
    _matrix_git(root, "config", "user.name", "Example")
    _matrix_git(root, "config", "user.email", "example@example.com")
    _matrix_git(root, "add", ".")
    _matrix_git(root, "-c", "commit.gpgsign=false", "commit", "-m", "Catalog base")
    main_before = _matrix_git(root, "rev-parse", "HEAD")
    _matrix_git(root, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Catalog source")
    dev_sha = _matrix_git(root, "rev-parse", "HEAD")
    content_tree = _matrix_git(root, "rev-parse", "HEAD^{tree}")
    checkout = root
    record_property("sdk_adapter", "test interpreter plus prepared marker; no first-provision qualification")
    record_property("frontdoor", frontdoor)
    record_property("source_identity", json.dumps({
        "dev_sha": dev_sha, "main_before": main_before, "content_tree": content_tree,
    }, sort_keys=True))
    if frontdoor == "frozen":
        prepared_at = "2026-01-02T03:04:05Z"
        state = root / promotion.PIPELINE_STATE_PATH
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"last_promotion": {
            "dev_head": dev_sha, "main_before": main_before, "promoted_at": prepared_at,
        }}), encoding="utf-8")
        _matrix_git(root, "add", promotion.PIPELINE_STATE_PATH)
        tree = _matrix_git(root, "write-tree")
        candidate_sha = _matrix_git(root, "commit-tree", tree, "-p", main_before, "-m", "Catalog candidate")
        transport = tmp_path / "transport"
        exported = frozen.export_candidate(
            transport, repo=root, report={
                "dev_head": dev_sha, "main_before": main_before, "commit": candidate_sha,
            }, content_tree=content_tree, prepared_at=prepared_at,
        )
        imported = frozen.import_candidate(
            transport, repo=root, dev_sha=dev_sha, main_before=main_before, candidate_sha=candidate_sha,
        )
        assert imported == exported
        checkout = tmp_path / "candidate"
        _matrix_git(root, "worktree", "add", "--detach", str(checkout), candidate_sha)
        assert _matrix_git(checkout, "rev-parse", "HEAD") == imported["candidate_sha"]
        record_property("frozen_candidate", json.dumps(imported, sort_keys=True))
        template.write_text("host template must not be checked\n", encoding="utf-8")
        (root / catalog.DECLARATION).write_text("{}", encoding="utf-8")
    selected = typecheck.load_projects(checkout)[0]
    assert selected.python_version == python_version and selected.additional_python_versions == ()
    environment, digest = typecheck.environment_path(checkout, selected)
    environment.mkdir(parents=True)
    (environment / ".typecheck-prepared").write_text(digest + "\n", encoding="utf-8")
    bootstrap = (
        "import sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\n"
        "from tools import typecheck\n"
        "typecheck.environment_python = lambda path: Path(sys.executable)\n"
        "raise SystemExit(typecheck.main(['lib-payload-invocation', '--strict']))\n"
    )
    result = typecheck._run(
        [sys.executable, "-I", "-B", "-c", bootstrap, str(checkout)], checkout,
    )
    (tmp_path / "catalog-check.stdout.txt").write_bytes(result.stdout.encode("utf-8"))
    (tmp_path / "catalog-check.stderr.txt").write_bytes(result.stderr.encode("utf-8"))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Pending project probes do not certify admission." in result.stderr
    label_prefix = "[pending] lib-payload-invocation (Python "
    assert result.stdout.count(label_prefix) == 1, result.stdout + result.stderr
    for version in (python_version,):
        label = f"{label_prefix}{version}, {platform}, 4 physical files, 9 targets)"
        assert label in result.stdout, result.stdout + result.stderr
        segment = result.stdout.split(label, 1)[1].split(label_prefix, 1)[0]
        errors = [line for line in segment.splitlines() if ": error:" in line]
        if bad_bootstrap:
            assert len(errors) == 1, segment
            assert errors[0].startswith(catalog.PRODUCER + ":") and "[assignment]" in errors[0], segment
            assert "bootstrap canonical source" in errors[0] and "full body requires Python 3.11" in errors[0]
        elif bad_template:
            assert len(errors) == 4, segment
            assert all(
                line.startswith(catalog.TEMPLATE + ":60:") and "[return-value]" in line
                for line in errors
            ), segment
        else:
            assert not errors and "Success: no issues found" in segment, segment
    assert (checkout / catalog.TEMPLATE).read_bytes() == expected_template
    assert (checkout / catalog.DECLARATION).read_bytes() == expected_declaration
    assert (checkout / "tools/typecheck_catalog_views.py").read_bytes() == (
        typecheck.ROOT / "tools/typecheck_catalog_views.py"
    ).read_bytes()


@pytest.mark.parametrize("roots", [None, [], ["helpers", "other"]])
def test_manifest_optional_import_roots_preserve_old_defaults(tmp_path: Path, roots: object) -> None:
    """Only explicit per-project directories extend the seven-field constructor."""
    for name in ("tools", "helpers", "other"):
        (tmp_path / name).mkdir()
    row: dict[str, object] = {
        "name": "sample", "include": ["sample.py"], "exclude": [], "level": "pending",
    }
    if roots is not None:
        row["import_roots"] = roots
    (tmp_path / "tools/typecheck-projects.json").write_text(json.dumps({
        "version": 1, "python_version": "3.10", "platforms": ["linux"],
        "requirements": "requirements.txt", "projects": [row],
    }), encoding="utf-8")
    item, = typecheck.load_projects(tmp_path)
    assert item == replace(project(level="pending"), import_roots=("helpers", "other") if roots else ())
    assert project().import_roots == ()
    assert not item.owns("helpers/contract.py")


@pytest.mark.parametrize("field, roots", [
    ("import_roots", None), ("import_roots", "helpers"), ("import_roots", [1]), ("import_roots", [""]),
    ("import_roots", ["helpers", "helpers"]), ("import_roots", ["../helpers"]),
    ("import_roots", ["/helpers"]), ("import_roots", ["helpers\\nested"]),
    ("import_roots", ["missing"]), ("import_roots", ["sample.py"]),
    ("unknown_import_roots", []),
])
def test_manifest_refuses_invalid_import_roots(tmp_path: Path, field: str, roots: object) -> None:
    """Metadata cannot add arbitrary checker arguments or unresolved directories."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "helpers").mkdir()
    (tmp_path / "sample.py").write_text("value: int = 1\n", encoding="utf-8")
    (tmp_path / "tools/typecheck-projects.json").write_text(json.dumps({
        "version": 1, "python_version": "3.10", "platforms": ["linux"],
        "requirements": "requirements.txt", "projects": [{
            "name": "sample", "include": ["sample.py"], "exclude": [], "level": "pending",
            field: roots,
        }],
    }), encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError):
        typecheck.load_projects(tmp_path)


@pytest.mark.parametrize("location", ["repository", "directory"])
def test_import_roots_refuse_native_path_list_separators(
    tmp_path: Path, location: str,
) -> None:
    root = tmp_path / ("repo" + os.pathsep + "split" if location == "repository" else "repo")
    relative = "helpers" + os.pathsep + "split" if location == "directory" else "helpers"
    (root / "tools").mkdir(parents=True)
    (root / relative).mkdir()
    (root / "sample.py").write_text("value: int = 1\n", encoding="utf-8")
    (root / "requirements.txt").write_text("", encoding="utf-8")
    (root / relative / "contract.py").write_text("value: int = 1\n", encoding="utf-8")
    (root / "tools/typecheck-projects.json").write_text(json.dumps({
        "version": 1, "python_version": "3.10", "platforms": ["linux"],
        "requirements": "requirements.txt", "projects": [{
            "name": "sample", "include": ["sample.py"], "exclude": [], "level": "pending",
            "import_roots": [relative],
        }],
    }), encoding="utf-8")
    message = (
        "repository-relative path" if os.pathsep == ":" and location == "directory"
        else "native path-list separator"
    )
    with pytest.raises(typecheck.TypecheckError, match=message):
        typecheck.load_projects(root)
    with pytest.raises(typecheck.TypecheckError, match=message):
        typecheck._import_directory(root, relative)


@pytest.mark.parametrize("component", ["src", "view"])
@pytest.mark.parametrize("explicit", [False, True])
def test_final_search_components_use_native_separator_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, component: str, explicit: bool,
) -> None:
    root = tmp_path / "source"
    (root / "helpers").mkdir(parents=True)
    (root / "helpers/contract.py").write_text("value: int = 1\n", encoding="utf-8")
    (root / "sample.py").write_text("value: int = 1\n", encoding="utf-8")
    (root / "requirements.txt").write_text("", encoding="utf-8")
    environment = tmp_path / "environment"
    environment.mkdir()
    (environment / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()
    paths = ["sample.py", "helpers/contract.py"]
    name = "sample"
    if component == "src":
        extra = "provider" + os.pathsep + "split/src/extra.py"
        (root / extra).parent.mkdir(parents=True)
        (root / extra).write_text("value: int = 1\n", encoding="utf-8")
        paths.append(extra)
    else:
        name += os.pathsep + "split"
        (root / "tools").mkdir()
        (root / "tools/typecheck-projects.json").write_text(json.dumps({
            "version": 1, "python_version": "3.10", "platforms": ["linux"],
            "requirements": "requirements.txt", "projects": [{
                "name": name, "include": ["sample.py"], "exclude": [], "level": "pending",
                "import_roots": ["helpers"],
            }],
        }), encoding="utf-8")
        with pytest.raises(typecheck.TypecheckError, match="Invalid project name"):
            typecheck.load_projects(root)
        for source in (typecheck.ROOT / "libs/installation-context").glob("*.py"):
            relative = "libs/installation-context/" + source.name
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_bytes(source.read_bytes())
            paths.append(relative)
    item = replace(project(name=name), import_roots=("helpers",) if explicit else ())
    output = environment.parent / ("source-views-" + environment.name) / name / "installation_context.py"
    calls: list[str] = []

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return environment, "test-context"

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert env is not None
        expected = sorted({str(root), str(root / "libs/installer-engine"), *(
            [str((root / paths[-1]).parent)] if component == "src" else []
        )})
        if component == "view":
            expected.insert(0, str(output.parent))
            assert output.is_file()
        assert env["MYPYPATH"] == os.pathsep.join(expected)
        calls.append(env["MYPYPATH"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", lambda _path: Path(sys.executable))
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(typecheck, "_run", run)
    if explicit:
        with pytest.raises(typecheck.TypecheckError, match="native path-list separator"):
            typecheck.check_project(root, item, ("sample.py",), all_paths=tuple(paths))
        assert not calls
    else:
        assert typecheck.check_project(root, item, ("sample.py",), all_paths=tuple(paths)) == 0
        assert len(calls) == 1
    assert lease.released
    assert not output.exists()
    assert not (environment / "typecheck-targets.txt").exists()
    assert not (environment / "typecheck-mypy.ini").exists()


@pytest.mark.parametrize("foreign", [False, True])
def test_import_directory_refuses_redirected_roots(
    tmp_path: Path, native_file_symlink: Callable[..., None], foreign: bool,
) -> None:
    """Neither an internal alias nor a foreign source becomes the declared root."""
    root = tmp_path / "source"
    root.mkdir()
    target = tmp_path / "foreign" if foreign else root / "real"
    target.mkdir()
    native_file_symlink(root / "helpers", target, target_is_directory=True)
    with pytest.raises(typecheck.TypecheckError, match="ordinary repository directory"):
        typecheck._import_directory(root, "helpers")


@pytest.mark.parametrize("roots, inventoried, expected", [
    ((), (), 0),
    (("helpers", "other"), ("helpers/contract.py", "other/contract.py"), 0),
    (("helpers",), (), 2),
])
def test_import_roots_are_ordered_project_local_and_lease_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    roots: tuple[str, ...], inventoried: tuple[str, ...], expected: int,
) -> None:
    """Custody units inspect delivery, not actual MyPy contract enforcement."""
    for name in ("helpers", "other"):
        (tmp_path / name).mkdir()
    for name in inventoried:
        (tmp_path / name).write_text("value: int = 1\n", encoding="utf-8")
    (tmp_path / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()
    calls: list[list[str]] = []
    item = replace(project(level="pending"), import_roots=roots)

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return tmp_path, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert env is not None and "PYTHONPATH" not in env and "PYTHONHOME" not in env
        baseline = sorted([str(tmp_path), str(tmp_path / "libs/installer-engine")])
        assert env["MYPYPATH"].split(os.pathsep) == baseline + [str(tmp_path / name) for name in roots]
        assert Path(argv[-1][1:]).read_text(encoding="utf-8") == "sample.py\n"
        assert argv[argv.index("--follow-imports") + 1] == "silent"
        assert argv[argv.index("--cache-dir") + 1] == (
            os.devnull if roots else str(tmp_path / "mypy-cache/linux/3.10")
        )
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv("MYPYPATH", str(tmp_path / "foreign"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "foreign"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "foreign"))
    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(typecheck, "_run", run)
    if expected:
        with pytest.raises(typecheck.TypecheckError, match="no inventoried Python source"):
            typecheck.check_project(tmp_path, item, ("sample.py",), all_paths=("sample.py",))
        assert not calls
    else:
        assert typecheck.check_project(
            tmp_path, item, ("sample.py",), all_paths=("sample.py", *inventoried),
        ) == expected
        assert len(calls) == 1
    assert lease.released and item.level == "pending"
    assert not (tmp_path / "typecheck-targets.txt").exists()
    assert not (tmp_path / "typecheck-mypy.ini").exists()


def test_actual_mypy_rechecks_projects_and_changed_dependencies_with_shared_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reusing dependency analysis must not reuse another project's verdict."""
    (tmp_path / "requirements.txt").write_text("mypy==1.18.2\n", encoding="utf-8")
    (tmp_path / "shared.py").write_text("value: int = 'invalid'\n", encoding="utf-8")
    (tmp_path / "first.py").write_text(
        "from shared import value\nresult: int = value\n", encoding="utf-8",
    )
    (tmp_path / "second.py").write_text(
        "from shared import value\nresult: str = value\n", encoding="utf-8",
    )
    first = replace(project("first"), include=("first.py",))
    second = replace(project("second"), include=("second.py",))
    dependency = replace(project("dependency"), include=("shared.py",))
    environment, digest = typecheck.environment_path(tmp_path, first)
    environment.mkdir(parents=True)
    (environment / ".typecheck-prepared").write_text(digest + "\n", encoding="utf-8")
    leases: list[Lease] = []
    caches: list[str] = []
    original_run = typecheck._run

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        lease = Lease()
        leases.append(lease)
        return lease

    def run(
        argv: list[str], root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        caches.append(argv[argv.index("--cache-dir") + 1])
        return original_run(argv, root, env=env)

    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(typecheck, "_run", run)
    paths = ("first.py", "second.py", "shared.py")
    assert typecheck.check_project(tmp_path, first, ("first.py",), all_paths=paths) == 0
    assert typecheck.check_project(tmp_path, second, ("second.py",), all_paths=paths) == 1
    assert typecheck.check_project(tmp_path, dependency, ("shared.py",), all_paths=paths) == 1
    (tmp_path / "shared.py").write_text("value: str = 'changed'\n", encoding="utf-8")
    assert typecheck.check_project(tmp_path, first, ("first.py",), all_paths=paths) == 1
    assert typecheck.check_project(tmp_path, second, ("second.py",), all_paths=paths) == 0
    assert len(caches) == 5 and len(set(caches)) == 1
    assert all(lease.released for lease in leases)
    assert not (environment / "typecheck-targets.txt").exists()
    assert not (environment / "typecheck-mypy.ini").exists()


def test_import_inputs_accept_owned_wip_and_namespace_members_only(tmp_path: Path) -> None:
    """Namespace packages need no initializer; non-module fixture paths stay data."""
    namespace = tmp_path / "helpers/contracts"
    namespace.mkdir(parents=True)
    member = "helpers/contracts/model.py"
    (tmp_path / member).write_text("value: int = 1\n", encoding="utf-8")
    fixtures = tmp_path / "helpers/negative-cases"
    fixtures.mkdir()
    (fixtures / "shadow.pyi").write_text("invalid fixture\n", encoding="utf-8")
    (tmp_path / "helpers/not.a.module.pyi").write_text("invalid fixture\n", encoding="utf-8")
    captured = typecheck._import_inputs(tmp_path, ("helpers",), (member,))
    assert [name for name, _identity, _content in captured.files] == [member]
    captured.verify(tmp_path)
    added = "helpers/contracts/working.py"
    (tmp_path / added).write_text("value: int = 2\n", encoding="utf-8")
    updated = typecheck._import_inputs(tmp_path, ("helpers",), (member, added))
    assert len(updated.files) == 2


@pytest.mark.parametrize("suffix", [".PYI", ".PyI"])
def test_import_inputs_follow_native_stub_extension_aliases(tmp_path: Path, suffix: str) -> None:
    """A native lowercase lookup must not bypass the first-party stub refusal."""
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    (helpers / "contract.py").write_text("value: int = 1\n", encoding="utf-8")
    shadow = helpers / ("contract" + suffix)
    shadow.write_text("value: str\n", encoding="utf-8")
    lookup = helpers / "contract.pyi"
    aliases = os.name == "nt" and lookup.is_file() and lookup.samefile(shadow)
    if aliases:
        with pytest.raises(typecheck.TypecheckError, match="stubs cannot substitute"):
            typecheck._import_inputs(tmp_path, ("helpers",), ("helpers/contract.py",))
    else:
        captured = typecheck._import_inputs(tmp_path, ("helpers",), ("helpers/contract.py",))
        assert [name for name, _identity, _content in captured.files] == ["helpers/contract.py"]


@pytest.mark.parametrize("suffix", [".PY", ".Py"])
def test_import_inputs_preserve_native_owned_source_extension_aliases(
    tmp_path: Path, suffix: str,
) -> None:
    """FileFinder proves the native source spelling without executing its body."""
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    source = helpers / ("contract" + suffix)
    source.write_text("raise AssertionError('not executed')\n", encoding="utf-8")
    finder = importlib.machinery.FileFinder(
        str(helpers),
        (importlib.machinery.SourceFileLoader, importlib.machinery.SOURCE_SUFFIXES),
    )
    spec = finder.find_spec("contract")
    if spec is not None:
        assert os.name == "nt"
        assert (helpers / "contract.py").samefile(source)
        captured = typecheck._import_inputs(tmp_path, ("helpers",), ("helpers/contract.py",))
        assert [name for name, _identity, _content in captured.files] == ["helpers/contract.py"]
        captured.verify(tmp_path)
    else:
        assert typecheck._import_suffix(source) == suffix


@pytest.mark.parametrize("change", ["bytes", "missing", "added", "stub", "replacement", "root"])
def test_import_input_snapshot_refuses_member_and_root_changes(tmp_path: Path, change: str) -> None:
    """Accepted identities, bytes and reachable membership remain analysis inputs."""
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    member = helpers / "contract.py"
    member.write_text("value: int = 1\n", encoding="utf-8")
    captured = typecheck._import_inputs(tmp_path, ("helpers",), ("helpers/contract.py",))
    if change == "bytes":
        member.write_text("value: int = 2\n", encoding="utf-8")
    elif change == "missing":
        member.unlink()
    elif change == "added":
        (helpers / "extra.py").write_text("value: int = 1\n", encoding="utf-8")
    elif change == "stub":
        (helpers / "contract.pyi").write_text("value: str\n", encoding="utf-8")
    elif change == "replacement":
        member.rename(helpers / "original.txt")
        member.write_text("value: int = 1\n", encoding="utf-8")
    else:
        helpers.rename(tmp_path / "original")
        helpers.mkdir()
        member.write_text("value: int = 1\n", encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError):
        captured.verify(tmp_path)


@pytest.mark.parametrize("directory", [False, True])
def test_import_inputs_refuse_indirect_members(
    tmp_path: Path, native_file_symlink: Callable[..., None], directory: bool,
) -> None:
    """An importable alias cannot redirect namespace traversal or source bytes."""
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    (helpers / "contract.py").write_text("value: int = 1\n", encoding="utf-8")
    target = tmp_path / "foreign"
    target.mkdir()
    (target / "contract.py").write_text("value: str = 'foreign'\n", encoding="utf-8")
    native_file_symlink(
        helpers / ("alias" if directory else "alias.py"),
        target if directory else target / "contract.py", target_is_directory=directory,
    )
    with pytest.raises(typecheck.TypecheckError, match="ordinary repository"):
        typecheck._import_inputs(tmp_path, ("helpers",), ("helpers/contract.py",))


@pytest.mark.parametrize("mode, attributes", [(stat.S_IFREG, 0x400), (stat.S_IFIFO, 0)])
def test_import_inputs_refuse_nonordinary_files_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int, attributes: int,
) -> None:
    """The Windows reparse attribute is independent of the apparent file mode."""
    def lstat(_path: Path) -> os.stat_result:
        return cast(os.stat_result, SimpleNamespace(
            st_mode=mode, st_file_attributes=attributes, st_dev=1, st_ino=2,
        ))

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(typecheck.TypecheckError, match="ordinary repository file"):
        typecheck._import_identity(tmp_path / "contract.py")


@pytest.mark.parametrize("outcome", ["success", "failure", "timeout", "cleanup"])
def test_import_input_drift_is_refused_and_settles_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    """Neither a failed checker nor late cleanup can credit changed helper inputs."""
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    member = helpers / "contract.py"
    member.write_text("value: int = 1\n", encoding="utf-8")
    (tmp_path / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()
    unlink = Path.unlink

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return tmp_path, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    def cleanup(path: Path, missing_ok: bool = False) -> None:
        unlink(path, missing_ok=missing_ok)
        if path.name == "typecheck-mypy.ini":
            member.write_text("value: int = 2\n", encoding="utf-8")

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if outcome != "cleanup":
            member.write_text("value: int = 2\n", encoding="utf-8")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, 120)
        return subprocess.CompletedProcess(argv, int(outcome == "failure"), "", "")

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(typecheck, "_run", run)
    if outcome == "cleanup":
        monkeypatch.setattr(Path, "unlink", cleanup)
    with pytest.raises(typecheck.TypecheckError, match="inputs changed"):
        typecheck.check_project(
            tmp_path, replace(project(), import_roots=("helpers",)), ("sample.py",),
            all_paths=("sample.py", "helpers/contract.py"),
        )
    assert lease.released
    assert not (tmp_path / "typecheck-targets.txt").exists()
    assert not (tmp_path / "typecheck-mypy.ini").exists()


def test_git_comparison_and_physical_inventory_cover_developer_edits(tmp_path: Path) -> None:
    """Real Git covers staged/unstaged edits, pinned comparison and add/delete paths."""
    env = typecheck.git_environment()
    for command in (
        ["init"], ["config", "user.name", "Example"],
        ["config", "user.email", "example@example.com"],
        ["config", "commit.gpgsign", "true"],
        ["config", "gpg.program", "missing-fixture-signer"],
    ):
        assert typecheck._run(["git", *command], tmp_path, env=env).returncode == 0
    for name in ("staged.py", "unstaged.py", "removed.py"):
        (tmp_path / name).write_text("value: int = 1\n", encoding="utf-8")
    assert typecheck._run(["git", "add", "."], tmp_path, env=env).returncode == 0
    assert typecheck._run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic base"],
        tmp_path, env=env,
    ).returncode == 0
    (tmp_path / "staged.py").write_text("value: int = 2\n", encoding="utf-8")
    assert typecheck._run(["git", "add", "staged.py"], tmp_path, env=env).returncode == 0
    (tmp_path / "unstaged.py").write_text("value: int = 3\n", encoding="utf-8")
    (tmp_path / "added.py").write_text("value: int = 4\n", encoding="utf-8")
    (tmp_path / "removed.py").unlink()
    assert set(typecheck.changed_paths(tmp_path, "HEAD")) == {"staged.py", "unstaged.py", "removed.py"}
    assert typecheck.changed_paths(tmp_path, "HEAD", "HEAD") == ()
    assert set(typecheck.python_paths(tmp_path)) == {"staged.py", "unstaged.py", "added.py"}
    with pytest.raises(typecheck.TypecheckError, match="Cannot resolve"):
        typecheck.changed_paths(tmp_path, "missing-comparison-base")


def test_changed_selection_conservatively_checks_all_admitted_consumers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Even an empty diff cannot omit a consumer before dependency qualification."""
    first = project()
    caller = typecheck.Project(
        "caller", ("caller.py",), (), "strict", "3.10", ("linux",), "requirements.txt",
    )
    checked: list[str] = []

    def load_projects() -> tuple[typecheck.Project, ...]:
        return first, caller

    def python_paths() -> tuple[str, ...]:
        return "sample.py", "caller.py"

    def changed_paths(_root: Path, base: str, head: str | None) -> tuple[str, ...]:
        assert base == "origin/dev" and head is None
        return ()

    def check_project(
        _root: Path, item: typecheck.Project, _files: tuple[str, ...], *,
        all_paths: tuple[str, ...], strict: bool = False,
    ) -> int:
        checked.append(item.name)
        return 0

    monkeypatch.setattr(typecheck, "load_projects", load_projects)
    monkeypatch.setattr(typecheck, "python_paths", python_paths)
    monkeypatch.setattr(typecheck, "changed_paths", changed_paths)
    monkeypatch.setattr(typecheck, "check_project", check_project)
    assert typecheck.main(["--changed"]) == 0
    assert checked == ["sample", "caller"]
    assert "conservative dependency impact" in capsys.readouterr().out


def test_missing_preparation_is_actionable_and_does_not_create_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A check is not an implicit installer."""
    scratch = tmp_path.parent / "unallocated-scratch"
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(scratch))
    (tmp_path / "requirements.txt").write_text("mypy==1.18.2\n", encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError, match="sample --prepare"):
        typecheck.check_project(
            tmp_path, project(), ("sample.py",), all_paths=("sample.py",),
        )
    assert not typecheck.environment_path(tmp_path, project())[0].exists()
    assert not (tmp_path / ".test-venvs").exists()
    assert not scratch.exists()


def test_nonallocating_lookup_still_refuses_unsafe_existing_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX fallback ownership contract")
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.delenv("AGENT_SCRATCH_ROOT", raising=False)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    scratch = tmp_path / f"ce-{os.getuid()}"
    scratch.mkdir()
    scratch.chmod(0o755)
    with pytest.raises(ValueError, match="private and owned"):
        plugin_test_resources.resource_path(source)


def test_pending_probe_never_certifies_admission(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """A clean probe of pending code remains distinct from an enforced result."""
    item = project(level="pending")

    def load_projects() -> tuple[typecheck.Project, ...]:
        return (item,)

    def python_paths() -> tuple[str, ...]:
        return ("sample.py",)

    def check_project(
        _root: Path, _project: typecheck.Project, _files: tuple[str, ...], *,
        all_paths: tuple[str, ...], strict: bool = False,
    ) -> int:
        assert all_paths == ("sample.py",)
        return 0

    monkeypatch.setattr(typecheck, "load_projects", load_projects)
    monkeypatch.setattr(typecheck, "python_paths", python_paths)
    monkeypatch.setattr(typecheck, "check_project", check_project)
    assert typecheck.main(["--all"]) == 1
    assert "do not certify admission" in capsys.readouterr().err
    assert typecheck.main(["sample"]) == 1
    assert "do not certify admission" in capsys.readouterr().err
    assert typecheck.main(["--inventory", "--require-complete"]) == 2


def test_enforced_invocation_tests_preserve_the_independent_catalog_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = tuple(
        item for item in typecheck.load_projects()
        if item.name in {"lib-payload-invocation", "lib-payload-invocation-tests"}
    )
    files = tuple(
        path for path in typecheck.python_paths()
        if any(item.owns(path) for item in projects)
    )
    expected = tuple(
        path for path in files if path.startswith("libs/payload-invocation/tests/")
    )
    calls: list[tuple[str, tuple[str, ...]]] = []

    def check(
        root: Path, item: typecheck.Project, selected: tuple[str, ...], *,
        all_paths: tuple[str, ...], strict: bool = False,
    ) -> int:
        assert root == typecheck.ROOT and all_paths == files
        calls.append((item.name, selected))
        return 0

    monkeypatch.setattr(typecheck, "load_projects", lambda: projects)
    monkeypatch.setattr(typecheck, "python_paths", lambda: files)
    monkeypatch.setattr(typecheck, "check_project", check)
    assert expected
    assert typecheck.main(["--enforced"]) == 0
    assert calls == [
        (item.name, tuple(path for path in files if item.owns(path)))
        for item in projects if item.level == "strict"
    ]
    assert ("lib-payload-invocation-tests", expected) in calls


def test_dependency_policy_refusal_has_the_setup_error_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """A supported policy refusal returns an actionable error, not a traceback."""
    item = project()

    def load_projects() -> tuple[typecheck.Project, ...]:
        return (item,)

    def python_paths() -> tuple[str, ...]:
        return ("sample.py",)

    def prepare(_root: Path, _project: typecheck.Project) -> None:
        raise DependencyPolicyError("unsupported UV configuration")

    monkeypatch.setattr(typecheck, "load_projects", load_projects)
    monkeypatch.setattr(typecheck, "python_paths", python_paths)
    monkeypatch.setattr(typecheck, "prepare", prepare)
    assert typecheck.main(["--prepare"]) == 2
    stderr = capsys.readouterr().err
    assert "typecheck: unsupported UV configuration" in stderr
    assert "Traceback" not in stderr


class Lease:
    """Only the release surface is substituted in checker subprocess controls."""

    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


def test_preparation_installs_the_same_lock_bytes_that_keyed_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed original cannot replace the snapshot admitted under the old digest."""
    requirements = tmp_path / "requirements.txt"
    initial = b"mypy==1.18.2\n"
    requirements.write_bytes(initial)
    lease = Lease()
    captured: list[bytes] = []

    def acquire(_wait: float, _path: Path) -> Lease:
        requirements.write_bytes(b"mypy==0.1\n")
        return lease

    def which(_name: str) -> str:
        return "uv"

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "", "")

    def install(
        _uv: str, arguments: list[str], *, cwd: Path,
        trusted_source_roots: tuple[Path, ...], check: bool, timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        captured.append(Path(arguments[arguments.index("-r") + 1]).read_bytes())
        assert timeout == 300
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(typecheck, "_run", run)
    monkeypatch.setattr(typecheck, "run_uv_install", install)
    typecheck.prepare(tmp_path, project())
    assert captured == [initial]
    assert lease.released
    requirements.write_bytes(initial)
    path, digest = typecheck.environment_path(tmp_path, project())
    assert (path / ".typecheck-prepared").read_text(encoding="utf-8").strip() == digest
    owner = plugin_test_resources.resource_path(tmp_path) / "scratch-owner.json"
    assert json.loads(owner.read_text(encoding="utf-8"))["source_checkout"] == str(tmp_path.resolve())


def test_failed_cache_repair_cannot_preserve_preparation_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restored interpreter alone must not certify a failed dependency repair."""
    (tmp_path / "requirements.txt").write_bytes(b"mypy==1.18.2\n")
    path, digest = typecheck.environment_path(tmp_path, project())
    path.mkdir(parents=True)
    marker = path / ".typecheck-prepared"
    marker.write_text(digest + "\n", encoding="utf-8")
    leases: list[Lease] = []
    installs: list[bool] = []

    def acquire(_wait: float, _path: Path) -> Lease:
        lease = Lease()
        leases.append(lease)
        return lease

    def which(_name: str) -> str:
        return "uv"

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert not marker.exists()
        python = typecheck.environment_python(path)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.touch()
        return subprocess.CompletedProcess(argv, 0, "", "")

    def install(
        _uv: str, arguments: list[str], *, cwd: Path,
        trusted_source_roots: tuple[Path, ...], check: bool, timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        installs.append(True)
        if len(installs) == 1:
            raise subprocess.TimeoutExpired(arguments, timeout)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(typecheck, "_run", run)
    monkeypatch.setattr(typecheck, "run_uv_install", install)
    with pytest.raises(subprocess.TimeoutExpired):
        typecheck.prepare(tmp_path, project())
    assert not marker.exists()
    with pytest.raises(typecheck.TypecheckError, match="--prepare"):
        typecheck.check_project(tmp_path, project(), ("sample.py",), all_paths=("sample.py",))
    typecheck.prepare(tmp_path, project())
    assert len(installs) == 2
    assert all(lease.released for lease in leases)
    assert marker.read_text(encoding="utf-8").strip() == digest


def test_preparation_refuses_unqualified_requirement_includes(tmp_path: Path) -> None:
    """A mutable include graph cannot pretend to be a one-file lock snapshot."""
    (tmp_path / "requirements.txt").write_text("-r elsewhere.txt\n", encoding="utf-8")
    with pytest.raises(typecheck.TypecheckError, match="flat hash-locked export"):
        typecheck.environment_path(tmp_path, project())


def test_preparation_refuses_replaced_leased_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preparation cannot switch filesystem identities beneath the shared lease."""
    (tmp_path / "requirements.txt").write_text("mypy==1.18.2\n", encoding="utf-8")
    environment = tmp_path / "environment"
    environment.mkdir()
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return environment, "test-context"

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    def which(_name: str) -> str:
        return "uv"

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment.rename(tmp_path / "original-environment")
        environment.mkdir()
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(typecheck, "_run", run)
    with pytest.raises(typecheck.TypecheckError, match="replaced the leased"):
        typecheck.prepare(tmp_path, project())
    assert lease.released
    assert not (environment / ".typecheck-prepared").exists()


def test_readiness_is_revalidated_under_the_checker_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repair between preflight and acquisition cannot launch an unprepared checker."""
    marker = tmp_path / ".typecheck-prepared"
    marker.write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return tmp_path, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        marker.unlink()
        return lease

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        pytest.fail("MyPy must not start after admission was invalidated")

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(typecheck, "_run", run)
    with pytest.raises(typecheck.TypecheckError, match="--prepare"):
        typecheck.check_project(tmp_path, project(), ("sample.py",), all_paths=("sample.py",))
    assert lease.released
    assert not (tmp_path / "typecheck-targets.txt").exists()


def test_checker_timeout_settles_targets_and_environment_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed checker leaves neither a target-file writer nor an admitted lease."""
    (tmp_path / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return tmp_path, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, 120)

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(typecheck, "_run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        typecheck.check_project(
            tmp_path, project(), ("sample.py",), all_paths=("sample.py",),
        )
    assert lease.released
    assert not (tmp_path / "typecheck-targets.txt").exists()
    assert not (tmp_path / "typecheck-mypy.ini").exists()


@pytest.mark.parametrize(
    ("source", "level", "expected", "filename"),
    [
        ("def accept(value: int) -> int:\n    return value\naccept('wrong')\n", "basic", "[arg-type]", "sample.py"),
        ("def body(value):\n    wrong: int = 'text'\n    return wrong\n", "basic", "[assignment]", "sample.py"),
        ("def missing(value):\n    return value\n", "strict", "[no-untyped-def]", "sample.py"),
        ("def generic(value: dict) -> dict:\n    return value\n", "strict", "[type-arg]", "sample.py"),
        ("wrong: int = 'text'\n", "strict", "[assignment]", "caf\u00e9.py"),
    ],
)
def test_actual_mypy_rejects_type_regressions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    source: str, level: typecheck.Level, expected: str, filename: str,
) -> None:
    """Exercise the real front door's MyPy argv, not a mocked checker result."""
    root = tmp_path / "source with spaces"
    root.mkdir()
    (root / filename).write_text(source, encoding="utf-8")
    environment = tmp_path / "environment with spaces"
    environment.mkdir()
    (environment / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return environment, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    assert typecheck.check_project(
        root, project(level=level), (filename,), all_paths=(filename,),
    ) != 0
    assert expected in capsys.readouterr().out
    assert lease.released
    assert not (environment / "typecheck-targets.txt").exists()
    assert not (environment / "typecheck-mypy.ini").exists()


@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize("case, diagnostic", [
    ("positive", ""), ("argument", "[arg-type]"), ("return", "[assignment]"),
    ("missing-root", "[import-not-found]"), ("missing-helper", "[import-not-found]"),
    ("missing-type", "[attr-defined]"), ("foreign", "[arg-type]"), ("mutation", "[arg-type]"),
])
def test_actual_mypy_real_import_root_consumed_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    platform: str, case: str, diagnostic: str,
) -> None:
    """Actual MyPy must consume real helper types on either analysis target."""
    root = tmp_path / "source"
    helpers = root / "helpers"
    helpers.mkdir(parents=True)
    helper = "def accept(value: int) -> int:\n    return 1\n"
    (helpers / "present.py").write_text("value: int = 1\n", encoding="utf-8")
    if case != "missing-helper":
        (helpers / "helper_contract.py").write_text(helper, encoding="utf-8")
    argument = "'wrong'" if case in {"argument", "foreign"} else "1"
    result_type = "str" if case == "return" else "int"
    source = f"from helper_contract import accept\nresult: {result_type} = accept({argument})\n"
    if case == "missing-type":
        source = "from helper_contract import Missing\nresult: Missing\n"
    (root / "sample.py").write_text(source, encoding="utf-8")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "helper_contract.py").write_text(
        "def accept(value: str) -> int:\n    return 1\n", encoding="utf-8",
    )
    monkeypatch.setenv("MYPYPATH", str(foreign))
    monkeypatch.setenv("PYTHONPATH", str(foreign))
    environment = tmp_path / "environment"
    environment.mkdir()
    (environment / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return environment, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    item = replace(
        project(), platforms=(platform,), import_roots=() if case == "missing-root" else ("helpers",),
    )
    paths = ("sample.py", "helpers/present.py", *(
        ("helpers/helper_contract.py",) if case != "missing-helper" else ()
    ))
    if case == "mutation":
        assert typecheck.check_project(root, item, ("sample.py",), all_paths=paths) == 0
        capsys.readouterr()
        contract = helpers / "helper_contract.py"
        before = contract.stat()
        contract.write_text(helper.replace("value: int", "value: str"), encoding="utf-8")
        os.utime(contract, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert typecheck.check_project(root, item, ("sample.py",), all_paths=paths) == int(bool(diagnostic))
    output = capsys.readouterr().out
    assert diagnostic in output
    assert "1 physical files, 1 targets" in output
    assert lease.released
    assert not (environment / "typecheck-targets.txt").exists()
    assert not (environment / "typecheck-mypy.ini").exists()


@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize("regression", [False, True])
def test_actual_mypy_combines_import_roots_and_additional_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    platform: str, regression: bool,
) -> None:
    root = tmp_path / "source"
    helpers = root / "helpers"
    helpers.mkdir(parents=True)
    modern_type = "str" if regression else "int"
    (helpers / "contract.py").write_text(
        "import sys\n"
        "if sys.version_info >= (3, 11):\n"
        f"    def accept(value: {modern_type}) -> {modern_type}:\n        return value\n"
        "else:\n"
        "    def accept(value: int) -> int:\n        return value\n",
        encoding="utf-8",
    )
    (root / "sample.py").write_text(
        "from contract import accept\nresult: int = accept(1)\n", encoding="utf-8",
    )
    environment = tmp_path / "environment"
    environment.mkdir()
    (environment / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return environment, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    item = replace(
        project(), platforms=(platform,), additional_python_versions=("3.11",),
        import_roots=("helpers",),
    )
    assert typecheck.check_project(
        root, item, ("sample.py",), all_paths=("sample.py", "helpers/contract.py"),
    ) == int(regression)
    output = capsys.readouterr().out
    minimum, modern = output.split(f"[strict] sample (Python 3.11, {platform},", 1)
    assert "Success: no issues found" in minimum and ": error:" not in minimum
    if regression:
        assert "[arg-type]" in modern and "[assignment]" in modern
    else:
        assert "Success: no issues found" in modern
    assert lease.released
    assert not (environment / "typecheck-targets.txt").exists()
    assert not (environment / "typecheck-mypy.ini").exists()


@pytest.mark.parametrize("helper", ["self_override", "core_helper"])
@pytest.mark.parametrize("invalid", [False, True])
def test_actual_mypy_preserves_lazy_helper_callable_signatures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    helper: str, invalid: bool,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    shutil.copyfile(
        typecheck.ROOT / "libs/lazy-cli-dispatch/src/lazy_cli_dispatch/__init__.py",
        root / "lazy_cli_dispatch.py",
    )
    owner = "{}" if helper == "self_override" else "SimpleNamespace()"
    resolve = f"{helper}({owner}, 'local', local)"
    source = (
        f"from lazy_cli_dispatch import {helper}\n"
        "from types import SimpleNamespace\n"
        "def local(value: int, *, enabled: bool = False) -> str:\n"
        "    return str(value)\n"
        f"valid: str = {resolve}(3, enabled=True)\n"
    )
    if invalid:
        source += (
            f"{resolve}('wrong', enabled=True)\n"
            f"wrong_return: int = {resolve}(3, enabled=True)\n"
        )
    (root / "sample.py").write_text(source, encoding="utf-8")
    environment = tmp_path / "environment"
    environment.mkdir()
    (environment / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return environment, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    paths = ("sample.py", "lazy_cli_dispatch.py")
    assert typecheck.check_project(root, project(), paths, all_paths=paths) == int(invalid)
    output = capsys.readouterr().out
    if invalid:
        assert output.count(": error:") == 2
        assert "[arg-type]" in output and "[assignment]" in output
    else:
        assert "Success: no issues found" in output
    assert lease.released


@pytest.mark.parametrize("invalid", [False, True])
@pytest.mark.parametrize("contract", ["decorator", "cleanup", "receipt-fixtures", "slot-release"])
def test_actual_mypy_preserves_installation_context_client_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    invalid: bool, contract: str,
) -> None:
    """Check a client of the complete pending implementation, not library admission."""
    root = tmp_path / "source"
    root.mkdir()
    view = views.installation_view(
        typecheck.ROOT, typecheck.python_paths(typecheck.ROOT),
        root / "installation_context.py",
    )
    source = (
        "from installation_context import _validation_scope\n"
        "@_validation_scope\n"
        "def local(value: int, *, enabled: bool = False) -> str:\n"
        "    return str(value)\n"
        "valid: str = local(3, enabled=True)\n"
    )
    if invalid:
        source += (
            "local('wrong', enabled=True)\n"
            "local(3, enabled='wrong')\n"
            "local(3, True)\n"
            "wrong_return: int = local(3, enabled=True)\n"
        )
    if contract == "cleanup":
        source = (
            "from collections.abc import Callable\n"
            "from pathlib import Path\n"
            "from installation_context import _FailedCandidateRuntime, discard_failed_runtime_slot\n"
            "class Runtime:\n"
            "    def discard_failed_candidate(self, root: Path, version: str, *,\n"
            "                                 validate: Callable[[Path], None]) -> bool:\n"
            "        validate(root)\n"
            "        return True\n"
            "def cleanup(runtime: _FailedCandidateRuntime) -> bool:\n"
            "    return discard_failed_runtime_slot(\n"
            "        runtime=runtime, context='context', expected_marketplace_id='market',\n"
            "        expected_plugin_id='plugin', snapshot_id='snapshot', runtime_version='1',\n"
            "        expected_ownership_sha256='digest', expected_namespace_generation=1,\n"
            "        expected_install_generation=1, expected_payload_root='payload',\n"
            "        expected_payload_version='1', durable_home='durable')\n"
            "valid: bool = cleanup(Runtime())\n"
        )
        if invalid:
            source += (
                "class WrongRuntime:\n"
                "    def discard_failed_candidate(self, root: Path, version: str, *,\n"
                "                                 validate: Callable[[Path], None]) -> str:\n"
                "        return 'wrong'\n"
                "cleanup(WrongRuntime())\n"
                "cleanup(object())\n"
                "cleanup('wrong')\n"
                "wrong_return: str = cleanup(Runtime())\n"
            )
    if contract in {"receipt-fixtures", "slot-release"}:
        fixture = typecheck.ROOT / "libs" / "installation-context" / "tests" / "test_snapshot_provenance.py"
        (root / fixture.name).write_bytes(fixture.read_bytes())
        source = (
            "from pathlib import Path\n"
            "from test_snapshot_provenance import (\n"
            "    _receipt_layout, _stamp_with_python, _provision_slot_with_python)\n"
            "layout = _receipt_layout(Path('test-root'))\n"
            "payload: Path = layout['payload']\n"
            "plugin: str = layout['plugin_id']\n"
            "snapshot = _stamp_with_python(layout)\n"
            "snapshot_root = Path(snapshot['snapshotRoot'])\n"
            "generation: int = snapshot['installGeneration']\n"
            "slot = _provision_slot_with_python(layout)\n"
            "slot_root = Path(slot['slotRoot'])\n"
            "changed: bool = slot['slotChanged']\n"
        )
        if invalid:
            source += (
                "layout['plugin_id'] = Path('wrong')\n"
                "Path(snapshot['snapshotChanged'])\n"
                "slot['slotRoot'] = False\n"
                "layout['unexpected'] = 'wrong'\n"
            )
        if contract == "slot-release":
            release = fixture.with_name("test_slot_release.py")
            (root / release.name).write_bytes(release.read_bytes())
            source = (
                "from pathlib import Path\n"
                "from subprocess import CompletedProcess\n"
                "from test_slot_release import reservation, run_release\n"
                "layout, slot, receipt, arguments = reservation(Path('test-root'))\n"
                "payload: Path = layout['payload']\n"
                "directory: Path = slot\n"
                "receipt_path: Path = receipt\n"
                "result: CompletedProcess[str] = run_release(\n"
                "    ('python', ('python',), 'long'), arguments)\n"
            )
            if invalid:
                source += (
                    "reservation('wrong')\n"
                    "run_release(('python', (), 'long'), {'context': Path('wrong')})\n"
                    "wrong_payload: str = layout['payload']\n"
                    "arguments['generation'] = Path('wrong')\n"
                )
    (root / "sample.py").write_text(source, encoding="utf-8")
    environment = tmp_path / "environment"
    environment.mkdir()
    (environment / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return environment, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    paths = ("sample.py",)
    assert typecheck.check_project(root, project(), paths, all_paths=paths) == int(invalid)
    view.verify(typecheck.ROOT)
    output = capsys.readouterr().out
    if invalid:
        assert output.count(": error:") == 4
        assert "[arg-type]" in output
        if contract == "decorator":
            assert "[assignment]" in output and "[misc]" in output
        if contract == "receipt-fixtures":
            assert "[typeddict-item]" in output
            assert "[typeddict-unknown-key]" in output
    else:
        assert "Success: no issues found" in output
    assert lease.released


def fragment_source(tmp_path: Path) -> tuple[str, ...]:
    """Use the real loader with a small, deliberately interdependent namespace."""
    directory = tmp_path / "libs" / "installation-context"
    directory.mkdir(parents=True)
    entry = (typecheck.ROOT / views.INSTALLATION_ENTRY).read_text(encoding="utf-8")
    first = entry.index("_FRAGMENT_FILES = (")
    last = entry.index("\n)", first) + 2
    entry = entry[:first] + (
        '_FRAGMENT_FILES = ("_installation_context_base.py", "_installation_context_files.py")'
    ) + entry[last:]
    (directory / "installation_context.py").write_text(entry, encoding="utf-8")
    (directory / "_installation_context_base.py").write_text(
        "from __future__ import annotations\n"
        "def shared(value: int) -> int:\n    return value\n",
        encoding="utf-8",
    )
    (directory / "_installation_context_files.py").write_text(
        "from __future__ import annotations\nresult: int = shared('wrong')\n"
        "raise RuntimeError('static checking must not execute this module')\n",
        encoding="utf-8",
    )
    return tuple(path.relative_to(tmp_path).as_posix() for path in sorted(directory.glob("*.py")))


def guard_source(
    root: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[typecheck.Project, tuple[str, ...], Path, Lease]:
    """Keep the original guard body and one extra current owned target."""
    source = root / views.GUARD_ENTRY
    source.parent.mkdir(parents=True)
    source.write_bytes((typecheck.ROOT / views.GUARD_ENTRY).read_bytes())
    extra = source.with_name("new_owned_member.py")
    extra.write_bytes(b"value: int = 1\n")
    (root / "libs/installer-engine").mkdir(parents=True)
    environment = root / "environment"
    environment.mkdir()
    (environment / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()
    monkeypatch.setattr(typecheck, "environment_path", lambda *_args, **_kwargs: (environment, "test-context"))
    monkeypatch.setattr(typecheck, "environment_python", lambda _path: Path(sys.executable))
    monkeypatch.setattr(typecheck, "acquire_environment", lambda *_args: lease)
    item = typecheck.Project(
        "copilot-extensions-harness", ("plugins/copilot-extensions-harness/scripts",), (),
        "pending", "3.10", ("linux", "win32"), "requirements.txt",
        import_roots=("plugins/copilot-extensions-harness/scripts",),
    )
    paths = (views.GUARD_ENTRY, extra.relative_to(root).as_posix())
    return item, paths, environment, lease


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_guard_alias_exact_scope_bytes_full_map_and_current_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    selected: bool, newline: bytes,
) -> None:
    item, paths, environment, lease = guard_source(tmp_path, monkeypatch)
    source = tmp_path / views.GUARD_ENTRY
    content = source.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", newline)
    source.write_bytes(content)
    if not selected:
        item = replace(project(), platforms=("linux", "win32"))
        (tmp_path / "sample.py").write_bytes(b"value: int = 1\n")
    owned = paths if selected else ("sample.py",)
    calls = 0

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        assert env is not None
        targets = Path(argv[-1][1:]).read_text(encoding="utf-8").splitlines()
        assert len(targets) == len(owned)
        if not selected:
            assert targets == ["sample.py"]
            assert "source-views-" not in env["MYPYPATH"]
            return subprocess.CompletedProcess(argv, 0, "", "")
        alias = Path(targets[0])
        assert alias.name == views.GUARD_MODULE + ".py" and alias.read_bytes() == content
        assert targets[1:] == list(paths[1:]) and views.GUARD_ENTRY not in targets
        assert env["MYPYPATH"].split(os.pathsep).count(str(alias.parent)) == 1
        assert argv[argv.index("--cache-dir") + 1] == os.devnull
        diagnostics = "".join(
            f"{alias}:{number}:7: error: mapped\n"
            for number in range(1, len(content.splitlines()) + 1)
        )
        return subprocess.CompletedProcess(argv, 0, diagnostics, "")

    monkeypatch.setattr(typecheck, "_run", run)
    assert typecheck.check_project(tmp_path, item, owned, all_paths=(*paths, *(() if selected else owned))) == 0
    output = capsys.readouterr().out
    if selected:
        for number in range(1, len(content.splitlines()) + 1):
            assert f"{views.GUARD_ENTRY}:{number}:7: error: mapped\n" in output
        assert f"{len(paths)} physical files, {len(paths)} targets" in output
    assert calls == 2 and lease.released and source.read_bytes() == content
    assert not list(environment.glob("typecheck-*"))
    assert not list((tmp_path / "source-views-environment").rglob("*.py"))


@pytest.mark.parametrize("change", [
    "source-bytes", "source-identity", "source-parent", "view-bytes", "view-identity",
    "stage-identity", "provider", "stub", "namespace", "extra-stage", "timeout", "cleanup",
    "installation-source", "unchanged",
])
def test_guard_alias_refuses_drift_and_cleans_only_owned_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    item, paths, environment, lease = guard_source(tmp_path, monkeypatch)
    installation = fragment_source(tmp_path)
    source = tmp_path / views.GUARD_ENTRY
    original = source.read_bytes()
    stage = tmp_path / "source-views-environment" / item.name
    alias = stage / (views.GUARD_MODULE + ".py")
    installation_output = stage / "installation_context.py"
    unlink = Path.unlink
    calls = 0

    def cleanup(path: Path, missing_ok: bool = False) -> None:
        unlink(path, missing_ok=missing_ok)
        if path.name == "typecheck-mypy.ini":
            source.write_bytes(original + b"\n")

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        assert alias.is_file() and installation_output.is_file() and env is not None
        assert env["MYPYPATH"].split(os.pathsep).count(str(stage)) == 1
        if change in {"source-bytes", "timeout"}:
            before = source.stat()
            source.write_bytes(original + b"\n")
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        elif change == "source-identity":
            source.rename(source.with_suffix(".saved"))
            source.write_bytes(original)
        elif change == "source-parent":
            source.parent.rename(source.parent.with_name("saved-scripts"))
            source.parent.mkdir()
            source.write_bytes(original)
            (tmp_path / paths[1]).write_bytes(b"value: int = 1\n")
        elif change == "view-bytes":
            alias.write_bytes(original + b"\n")
        elif change == "view-identity":
            alias.rename(alias.with_suffix(".saved"))
            alias.write_bytes(original)
        elif change == "stage-identity":
            stage.rename(stage.with_name("saved-stage"))
            stage.mkdir()
            alias.write_bytes(original)
            installation_output.write_bytes(b"foreign: int = 1\n")
        elif change in {"provider", "stub"}:
            source.with_name(views.GUARD_MODULE + (".py" if change == "provider" else ".pyi")).write_bytes(b"")
        elif change == "namespace":
            source.with_name(views.GUARD_MODULE).mkdir()
        elif change == "extra-stage":
            (stage / "unknown.py").write_bytes(b"")
        elif change == "installation-source":
            fragment = tmp_path / installation[0]
            fragment.write_bytes(fragment.read_bytes() + b"\n")
        if change == "timeout":
            raise subprocess.TimeoutExpired(argv, 120)
        return subprocess.CompletedProcess(argv, int(change != "unchanged"), "", "")

    if change == "cleanup":
        monkeypatch.setattr(Path, "unlink", cleanup)
    monkeypatch.setattr(typecheck, "_run", run)
    if change == "unchanged":
        assert typecheck.check_project(tmp_path, item, paths, all_paths=(*paths, *installation)) == 0
    else:
        with pytest.raises((typecheck.TypecheckError, views.SourceViewError)):
            typecheck.check_project(tmp_path, item, paths, all_paths=(*paths, *installation))
    assert calls == (2 if change == "unchanged" else 1) and lease.released
    assert not list(environment.glob("typecheck-*"))
    assert alias.exists() == (change in {"view-identity", "stage-identity"})
    assert installation_output.exists() == (change == "stage-identity")
    if change == "extra-stage":
        assert (stage / "unknown.py").exists()


@pytest.mark.parametrize("refusal", [
    "missing", "unowned", "incomplete", "provider", "stub", "package", "native-case", "stage",
])
def test_guard_alias_refuses_incomplete_binding_or_existing_shadows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: str,
) -> None:
    item, paths, environment, lease = guard_source(tmp_path, monkeypatch)
    owned = paths
    source = tmp_path / views.GUARD_ENTRY
    if refusal == "missing":
        source.unlink()
    elif refusal == "unowned":
        item = replace(item, include=(paths[1],))
    elif refusal == "incomplete":
        owned = paths[1:]
    elif refusal in {"provider", "stub", "native-case"}:
        name = views.GUARD_MODULE + (".pyi" if refusal == "stub" else ".py")
        if refusal == "native-case":
            name = name.upper()
            if os.name != "nt":
                pytest.skip("case-alias refusal follows the native filesystem")
        source.with_name(name).write_bytes(b"")
    elif refusal == "package":
        package = source.with_name(views.GUARD_MODULE)
        package.mkdir()
        (package / "__init__.py").write_bytes(b"")
    else:
        stage = tmp_path / "source-views-environment" / item.name
        stage.mkdir(parents=True)
        (stage / "unknown.py").write_bytes(b"")

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        pytest.fail("A refused binding cannot reach the analyzer")

    monkeypatch.setattr(typecheck, "_run", run)
    with pytest.raises((OSError, typecheck.TypecheckError, views.SourceViewError)):
        typecheck.check_project(tmp_path, item, owned, all_paths=paths)
    assert lease.released and not list(environment.glob("typecheck-*"))
    if refusal == "stage":
        assert (stage / "unknown.py").exists()


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_source_view_accounts_for_order_bytes_and_physical_lines(
    tmp_path: Path, newline: bytes,
) -> None:
    """Every physical line, including replaced future imports, has a source mapping."""
    paths = fragment_source(tmp_path)
    for path in paths:
        source = tmp_path / path
        content = source.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        source.write_bytes(content.replace(b"\n", newline))
    view = views.installation_view(tmp_path, paths, tmp_path / "view" / "installation_context.py")
    view.verify(tmp_path)
    assert tuple(path for path, _content in view.inputs) == (
        views.INSTALLATION_ENTRY,
        "libs/installation-context/_installation_context_base.py",
        "libs/installation-context/_installation_context_files.py",
    )
    for path, content in view.inputs:
        mapped = [line.number for line in view.lines if line.path == path]
        assert mapped == list(range(1, len(content.splitlines()) + 1))
    diagnostic_line = next(
        number for number, line in enumerate(view.lines, 1)
        if line.path.endswith("_files.py") and line.number == 2
    )
    assert view.diagnostics(f"{view.output}:{diagnostic_line}: error: example\n", tmp_path) == (
        "libs/installation-context/_installation_context_files.py:2: error: example\n"
    )


def test_native_fragment_loader_remains_source_mapped(tmp_path: Path) -> None:
    paths = fragment_source(tmp_path)
    entry = (tmp_path / views.INSTALLATION_ENTRY).read_bytes().splitlines()
    view = views.installation_view(tmp_path, paths, tmp_path / "view" / "installation_context.py")
    composed = view.content.splitlines()
    inputs = {path: content.splitlines() for path, content in view.inputs}
    assert len(composed) == len(view.lines)
    for text, source in zip(composed, view.lines):
        original = inputs[source.path][source.number - 1]
        replaced = (
            source.path == views.INSTALLATION_ENTRY and original.strip() == b"_load_fragments()"
            or source.path != views.INSTALLATION_ENTRY
            and original == b"from __future__ import annotations"
        )
        assert text == (b"" if replaced else original)
    for token in (
        b"def _native_fragment(",
        b"if os.name",
        b"return Path(",
        b"compile(_native_fragment(",
    ):
        physical = next(index for index, line in enumerate(entry, 1) if token in line)
        logical = next(
            index for index, line in enumerate(view.lines, 1)
            if line.path == views.INSTALLATION_ENTRY and line.number == physical
        )
        assert composed[logical - 1] == entry[physical - 1]
        assert view.diagnostics(f"{view.output}:{logical}: error: sample\n", tmp_path) == (
            f"{views.INSTALLATION_ENTRY}:{physical}: error: sample\n"
        )


@pytest.mark.parametrize(("old", "new"), [
    ('if os.name != "nt"', 'if os.name == "nt"'),
    ("return path\n", "return path.resolve()\n"),
    ("_native_fragment(fragment_path).read_text", "fragment_path.read_text"),
    ('str(fragment_path), "exec"', 'str(_native_fragment(fragment_path)), "exec"'),
])
def test_source_view_refuses_unqualified_native_loader_changes(
    tmp_path: Path, old: str, new: str,
) -> None:
    paths = fragment_source(tmp_path)
    entry = tmp_path / views.INSTALLATION_ENTRY
    content = entry.read_text(encoding="utf-8")
    assert content.count(old) == 1
    entry.write_text(content.replace(old, new), encoding="utf-8")
    with pytest.raises(views.SourceViewError, match="loader changed"):
        views.installation_view(tmp_path, paths, tmp_path / "view" / "installation_context.py")


@pytest.mark.parametrize("directive", [b"# mypy: ignore-errors\n", b"# type: ignore\n"])
def test_source_view_refuses_loader_wide_suppressions(tmp_path: Path, directive: bytes) -> None:
    paths = fragment_source(tmp_path)
    entry = tmp_path / views.INSTALLATION_ENTRY
    entry.write_bytes(directive + entry.read_bytes())
    with pytest.raises(views.SourceViewError, match="module-wide checker directives"):
        views.installation_view(tmp_path, paths, tmp_path / "view" / "installation_context.py")


def test_source_view_inlines_the_declared_fragment_order(tmp_path: Path) -> None:
    paths = fragment_source(tmp_path)
    entry = tmp_path / views.INSTALLATION_ENTRY
    content = entry.read_text(encoding="utf-8").replace(
        '"_installation_context_base.py", "_installation_context_files.py"',
        '"_installation_context_files.py", "_installation_context_base.py"',
    )
    entry.write_text(content, encoding="utf-8")
    view = views.installation_view(tmp_path, paths, tmp_path / "view" / "installation_context.py")
    declared = tuple(path for path, _content in view.inputs[1:])
    assert declared == (
        "libs/installation-context/_installation_context_files.py",
        "libs/installation-context/_installation_context_base.py",
    )
    mapped = [line.path for line in view.lines if line.path != views.INSTALLATION_ENTRY]
    assert mapped == [name for name, content in view.inputs[1:] for _line in content.splitlines()]


@pytest.mark.parametrize("change", ["source", "order", "view", "extra", "missing", "loader"])
def test_source_view_refuses_stale_or_incomplete_composition(tmp_path: Path, change: str) -> None:
    """Omission, reordering and changed code cannot reuse an old qualification."""
    paths = fragment_source(tmp_path)
    output = tmp_path / "view" / "installation_context.py"
    view = views.installation_view(tmp_path, paths, output)
    entry = tmp_path / views.INSTALLATION_ENTRY
    fragment = entry.with_name("_installation_context_files.py")
    if change == "source":
        fragment.write_bytes(fragment.read_bytes() + b"extra: int = 1\n")
    elif change == "order":
        content = entry.read_bytes().replace(
            b'"_installation_context_base.py", "_installation_context_files.py"',
            b'"_installation_context_files.py", "_installation_context_base.py"',
        )
        entry.write_bytes(content)
    elif change == "view":
        output.write_bytes(view.content + b"extra: int = 1\n")
    elif change == "extra":
        new = entry.with_name("_installation_context_new.py")
        new.write_bytes(b"extra: int = 1\n")
        paths = (*paths, new.relative_to(tmp_path).as_posix())
    elif change == "missing":
        paths = tuple(path for path in paths if not path.endswith("_files.py"))
    else:
        entry.write_bytes(entry.read_bytes().replace(b"for fragment_name in _FRAGMENT_FILES:", b"for fragment_name in reversed(_FRAGMENT_FILES):"))
    with pytest.raises(views.SourceViewError):
        if change in {"extra", "missing", "loader"}:
            views.installation_view(tmp_path, paths, output)
        else:
            view.verify(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "from __future__ import annotations; wrong: int = 'text'\n",
        "# mypy: ignore-errors\nwrong: int = 'text'\n",
        "# type: ignore\nwrong: int = 'text'\n",
        "# harmless\r# mypy: ignore-errors\rwrong: int = 'text'\r",
        "# harmless\r# type: ignore\rwrong: int = 'text'\r",
        "value: int = 1\nfrom __future__ import annotations\n",
    ],
)
def test_composition_cannot_drop_code_or_spread_file_wide_suppressions(
    tmp_path: Path, source: str,
) -> None:
    paths = fragment_source(tmp_path)
    fragment = tmp_path / "libs" / "installation-context" / "_installation_context_base.py"
    fragment.write_text(source, encoding="utf-8")
    with pytest.raises(views.SourceViewError):
        views.installation_view(tmp_path, paths, tmp_path / "view" / "installation_context.py")


@pytest.mark.parametrize(
    ("level", "argument", "expected"),
    [("basic", "'wrong'", 1), ("strict", "'wrong'", 1), ("strict", "3", 0)],
)
@pytest.mark.parametrize("consumer", [False, True])
def test_actual_mypy_sees_shared_fragment_contract_and_maps_its_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    level: typecheck.Level, argument: str, expected: int, consumer: bool,
) -> None:
    """Direct targets and imported dependencies retain real, unexecuted contracts."""
    paths = fragment_source(tmp_path)
    fragment = tmp_path / "libs" / "installation-context" / "_installation_context_files.py"
    fragment.write_bytes(
        fragment.read_bytes().replace(b"'wrong'", b"3" if consumer else argument.encode("ascii"))
    )
    targets = paths
    if consumer:
        source = f"from installation_context import shared\nresult: int = shared({argument})\n"
        if expected:
            source += "wrong_return: str = shared(3)\n"
        (tmp_path / "sample.py").write_text(source, encoding="utf-8")
        targets = ("sample.py",)
        paths = (*paths, *targets)
    environment = tmp_path / "environment"
    environment.mkdir()
    (environment / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return environment, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    assert typecheck.check_project(tmp_path, project(level=level), targets, all_paths=paths) == expected
    output = capsys.readouterr().out
    assert f"{len(targets)} physical files, 1 targets" in output
    if expected:
        location = "sample.py:2" if consumer else "_installation_context_files.py:2"
        assert f"{location}: error:" in output
        assert "[arg-type]" in output and "[name-defined]" not in output
        if consumer:
            assert output.count(": error:") == 2
            assert "[assignment]" in output
    else:
        assert "Success: no issues found" in output
    assert lease.released
    assert not (tmp_path / "source-views-environment" / "sample" / "installation_context.py").exists()


def test_imported_view_refuses_source_drift_and_settles_its_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Import-only composition cannot credit changed source or become an owned target."""
    paths = (*fragment_source(tmp_path), "sample.py")
    (tmp_path / "sample.py").write_text(
        "from installation_context import shared\nresult: int = shared(3)\n", encoding="utf-8",
    )
    environment = tmp_path / "environment"
    environment.mkdir()
    (environment / ".typecheck-prepared").write_text("test-context\n", encoding="utf-8")
    lease = Lease()

    def environment_path(
        _root: Path, _project: typecheck.Project, *, captured_lock: bytes | None = None,
    ) -> tuple[Path, str]:
        return environment, "test-context"

    def environment_python(_path: Path) -> Path:
        return Path(sys.executable)

    def acquire(_wait: float, _path: Path) -> Lease:
        return lease

    def run(
        argv: list[str], _root: Path, *, env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert Path(argv[-1][1:]).read_text(encoding="utf-8") == "sample.py\n"
        fragment = tmp_path / "libs/installation-context/_installation_context_base.py"
        fragment.write_bytes(fragment.read_bytes() + b"\nextra: int = 1\n")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(typecheck, "environment_path", environment_path)
    monkeypatch.setattr(typecheck, "environment_python", environment_python)
    monkeypatch.setattr(typecheck, "acquire_environment", acquire)
    monkeypatch.setattr(typecheck, "_run", run)
    with pytest.raises(views.SourceViewError, match="source changed"):
        typecheck.check_project(tmp_path, project(), ("sample.py",), all_paths=paths)
    assert lease.released
    assert not (environment / "typecheck-targets.txt").exists()
    assert not (environment / "typecheck-mypy.ini").exists()
    assert not (tmp_path / "source-views-environment/sample/installation_context.py").exists()
