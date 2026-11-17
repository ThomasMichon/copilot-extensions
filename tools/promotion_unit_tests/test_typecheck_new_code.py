"""Actor-free whole-file strict selection and honest pending-project results."""

from __future__ import annotations

import os
from pathlib import Path, WindowsPath
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from tools import typecheck
from tools import typecheck_source_views as views
from tools.typecheck_new_code import select_new_code
from tools.typecheck_source_views import INSTALLATION_ENTRY, SourceViewError, setup_program_view
from tools.typecheck_programs import ProgramError, independent_programs

ROOT = Path(__file__).resolve().parents[2]


def test_pr_ratchet_uses_the_feature_head_and_merge_base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.syspath_prepend(str(ROOT))
    from tools import typecheck

    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text("utf-8"))
    step = next(item for item in ci["jobs"]["checks"]["steps"]
                if item.get("name") == "Whole-file strict new-code ratchet")
    assert step["env"]["BASE"] == "${{ github.event.pull_request.base.sha || github.event.before }}"
    assert step["env"]["HEAD"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert 'BASE="$(git merge-base "$BASE" "$HEAD")"' in step["run"]
    feature, common = "a" * 40, "b" * 40
    comparisons: list[list[str]] = []

    def run(argv: list[str], cwd: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        assert cwd == tmp_path
        if argv[1] == "rev-parse":
            value = common if argv[-1] == common + "^{commit}" else feature
            return subprocess.CompletedProcess(argv, 0, value + "\n", "")
        comparisons.append(argv)
        return subprocess.CompletedProcess(argv, 0, "pr-owned.py\0", "")

    monkeypatch.setattr(typecheck, "_run", run)
    assert typecheck.changed_paths(tmp_path, common, feature) == ("pr-owned.py",)
    assert comparisons[0][-3:] == [common, feature, "--"]


def test_candidate_manager_application_has_an_owner_without_waiving_the_strict_ratchet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = typecheck.load_projects()
    files = (
        "apps/worktree-manager/_standalone_runtime_build.py",
        "apps/worktree-manager/setup.py",
        "apps/worktree-manager/src/worktree_manager/cli.py",
        "apps/worktree-manager/src/agent_procutil/owned_process.py",
        "apps/worktree-manager/src/worktree_manager/_runtime_assets/scripts/install.py",
    )
    for relative in files:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("value: int = 1\n", encoding="utf-8")
    owners = typecheck.inventory(files, projects)
    owner = owners[files[0]]
    assert owner.name == "worktree-manager-application"
    assert owner.level == "pending"
    assert all(project is owner for project in owners.values())
    with pytest.raises(typecheck.TypecheckError, match="unclassified"):
        typecheck.inventory(("apps/unregistered/program.py",), projects)
    with pytest.raises(typecheck.TypecheckError, match="unclassified"):
        typecheck.inventory(("apps/worktree-manager-other/program.py",), projects)
    calls: list[tuple[str, tuple[str, ...], bool]] = []

    def check(
        root: Path, project: typecheck.Project, selected: tuple[str, ...], *,
        all_paths: tuple[str, ...], strict: bool,
    ) -> int:
        assert root == tmp_path and all_paths == files
        calls.append((project.name, selected, strict))
        return 1

    monkeypatch.setattr(typecheck, "ROOT", tmp_path)
    monkeypatch.setattr(typecheck, "load_projects", lambda: projects)
    monkeypatch.setattr(typecheck, "python_paths", lambda: files)
    monkeypatch.setattr(typecheck, "new_code_changes", lambda *_args: files)
    monkeypatch.setattr(typecheck, "check_project", check)
    assert typecheck.main(["--new-code", "--base", "unchanged-base"]) == 1
    assert calls == [(owner.name, files, True)]


def test_independent_programs_preserve_complete_owner_selection() -> None:
    project = typecheck.Project(
        "sample", ("ordinary", "retained"), (), "pending", "3.10",
        ("linux", "win32"), "requirements.txt",
    )
    files = ("ordinary/first.py", "retained/first.py", "ordinary/second.py")
    assert independent_programs(files, files, ("retained",), project.owns) == (
        (files[0], files[2]), (files[1],),
    )
    assert independent_programs((files[1],), files, ("retained",), project.owns) == ((files[1],),)


@pytest.mark.parametrize("roots", [
    ("retained", "retained"), ("retained", "retained/nested"), ("missing",),
], ids=["duplicate-root", "nested-root", "empty-root"])
def test_independent_programs_refuse_ambiguous_or_empty_declarations(roots: tuple[str, ...]) -> None:
    files = ("ordinary/contract.py", "retained/contract.py")
    with pytest.raises(ProgramError, match="(overlap|no owned)"):
        independent_programs(files, files, roots, lambda _path: True)


@pytest.mark.parametrize("selected", [
    ("ordinary/contract.py", "ordinary/contract.py"), ("unowned.py",), ("retained/missing.py",),
], ids=["duplicate-file", "unowned-file", "missing-file"])
def test_independent_programs_refuse_noninventoried_or_unowned_selection(
    selected: tuple[str, ...],
) -> None:
    files = ("ordinary/contract.py", "retained/contract.py")
    with pytest.raises(ProgramError, match="unique inventoried owner"):
        independent_programs(selected, files, ("retained",), lambda path: path in files)

ROOT = Path(__file__).resolve().parents[2]


def test_pr_ratchet_uses_the_feature_head_and_merge_base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.syspath_prepend(str(ROOT))
    from tools import typecheck

    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text("utf-8"))
    step = next(item for item in ci["jobs"]["checks"]["steps"]
                if item.get("name") == "Whole-file strict new-code ratchet")
    assert step["env"]["BASE"] == "${{ github.event.pull_request.base.sha || github.event.before }}"
    assert step["env"]["HEAD"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert 'BASE="$(git merge-base "$BASE" "$HEAD")"' in step["run"]
    feature, common = "a" * 40, "b" * 40
    comparisons: list[list[str]] = []

    def run(argv: list[str], cwd: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        assert cwd == tmp_path
        if argv[1] == "rev-parse":
            value = common if argv[-1] == common + "^{commit}" else feature
            return subprocess.CompletedProcess(argv, 0, value + "\n", "")
        comparisons.append(argv)
        return subprocess.CompletedProcess(argv, 0, "pr-owned.py\0", "")

    monkeypatch.setattr(typecheck, "_run", run)
    assert typecheck.changed_paths(tmp_path, common, feature) == ("pr-owned.py",)
    assert comparisons[0][-3:] == [common, feature, "--"]


@pytest.mark.parametrize("level", ["pending", "basic", "strict"])
@pytest.mark.parametrize("failure", [False, True])
def test_new_code_frontdoor_checks_whole_files_without_admitting_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    level: typecheck.Level, failure: bool,
) -> None:
    project = typecheck.Project(
        "sample", ("sample",), (), level, "3.10", ("linux", "win32"), "requirements.txt",
    )
    files = ("sample/changed.py", "sample/unchanged.py")
    (tmp_path / "sample").mkdir()
    for path in files:
        (tmp_path / path).write_bytes(b"value: int = 1\n")
    monkeypatch.setattr(typecheck, "ROOT", tmp_path)
    monkeypatch.setattr(typecheck, "load_projects", lambda: (project,))
    monkeypatch.setattr(typecheck, "python_paths", lambda: files)
    monkeypatch.setattr(typecheck, "new_code_changes", lambda *_args: (files[0], "removed.py"))
    checked: list[tuple[tuple[str, ...], bool]] = []

    def check(
        root: Path, item: typecheck.Project, selected: tuple[str, ...], *,
        all_paths: tuple[str, ...], strict: bool = False,
    ) -> int:
        assert root == tmp_path and item == project and all_paths == files
        checked.append((selected, strict))
        return int(failure)

    monkeypatch.setattr(typecheck, "check_project", check)
    assert typecheck.main(["--new-code", "--base", "fixture"]) == int(failure)
    assert checked == [((files[0],), True)]
    assert project.level == level


@pytest.mark.parametrize(
    ("owner", "change"),
    [
        ("lib-payload-invocation", "libs/payload-invocation/templates/powershell-shim.tmpl"),
        ("lib-payload-invocation", "libs/agent-procutil/src/agent_procutil/python_cache.py"),
        ("lib-payload-invocation", "libs/payload-invocation/new.py"),
        ("copilot-extensions-maintainer", "plugins/copilot-extensions-maintainer/python-emitted-inputs.json"),
        ("copilot-extensions-maintainer", "plugins/copilot-extensions-maintainer/scripts/new.py"),
        ("lib-installation-context", "libs/installation-context/_installation_context_new.py"),
    ],
)
def test_new_code_selection_preserves_complete_composed_bindings(
    tmp_path: Path, owner: str, change: str,
) -> None:
    prefixes = {
        "lib-payload-invocation": "libs/payload-invocation",
        "copilot-extensions-maintainer": "plugins/copilot-extensions-maintainer/scripts",
        "lib-installation-context": "libs/installation-context",
    }
    prefix = prefixes[owner]
    first = prefix + ("/installation_context.py" if owner == "lib-installation-context" else "/new.py")
    second = prefix + ("/_installation_context_new.py" if owner == "lib-installation-context" else "/old.py")
    project = typecheck.Project(
        owner, (prefix,), (), "pending", "3.10", ("linux",), "requirements.txt",
        additional_python_versions=("3.11",) if owner == "lib-payload-invocation" else (),
    )
    for path in (first, second):
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"value: int = 1\n")
    assert select_new_code(tmp_path, {first: project, second: project}, (change,)) == {
        owner: (first, second),
    }


@pytest.mark.parametrize("entry_name", ["installation_context.py", "_installation_context.py"])
def test_vendored_installation_change_selects_its_complete_namespace(
    tmp_path: Path, entry_name: str,
) -> None:
    prefix = "plugins/example/src/example/"
    files = tuple(prefix + name for name in (
        entry_name, "_installation_context_a.py",
        "_installation_context_b.py", "_installation_context_worker.py", "unrelated.py",
    ))
    project = typecheck.Project(
        "example", ("plugins/example",), (), "pending", "3.10", ("linux",), "requirements.txt",
    )
    for path in files:
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"value: int = 1\n")
    assert select_new_code(tmp_path, dict.fromkeys(files, project), (files[1],)) == {
        "example": files[:3],
    }


@pytest.mark.parametrize("entry_name", ["installation_context.py", "_installation_context.py"])
def test_vendored_installation_view_preserves_local_custody(
    tmp_path: Path, entry_name: str,
) -> None:
    prefix = "plugins/example/src/example/"
    entry = prefix + entry_name
    fragment = prefix + "_installation_context_a.py"
    loader = views._WRAPPER.replace(
        "_FRAGMENT_FILES = ()", '_FRAGMENT_FILES = ("_installation_context_a.py",)',
    ).encode()
    (tmp_path / entry).parent.mkdir(parents=True)
    (tmp_path / entry).write_bytes(loader)
    (tmp_path / fragment).write_bytes(b"raise RuntimeError('must never execute')\n")
    output = tmp_path / "view.py"
    view = views.installation_view(tmp_path, (entry, fragment), output, entry_path=entry)
    view.verify(tmp_path)
    line = next(index for index, item in enumerate(view.lines, 1) if item.path == fragment)
    assert view.diagnostics(f"{output}:{line}: error: sample\n", tmp_path) == (
        f"{fragment}:1: error: sample\n"
    )
    (tmp_path / fragment).write_bytes(b"changed: int = 1\n")
    with pytest.raises(SourceViewError, match="source changed"):
        view.verify(tmp_path)
    (tmp_path / fragment).write_bytes(view.inputs[1][1])
    extra = prefix + "_installation_context_extra.py"
    (tmp_path / extra).write_bytes(b"extra: int = 1\n")
    with pytest.raises(SourceViewError, match="membership"):
        view.verify(tmp_path)
    with pytest.raises(SourceViewError, match="membership"):
        views.installation_view(tmp_path, (entry, fragment, extra), output, entry_path=entry)


@pytest.mark.parametrize("entry", ["installation_context.py", "_installation_context.py"])
@pytest.mark.parametrize("changed", ["entry", "fragment"])
def test_new_code_copied_fragment_selects_its_complete_owned_program(
    tmp_path: Path, entry: str, changed: str,
) -> None:
    prefix = "consumer/scripts"
    files = (
        prefix + "/" + entry, prefix + "/_installation_context_base.py",
        prefix + "/unrelated.py",
    )
    project = typecheck.Project(
        "consumer", (prefix,), (), "pending", "3.10", ("linux",), "requirements.txt",
    )
    for relative in files:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"value: int = 1\n")
    selected = files[0 if changed == "entry" else 1]
    assert select_new_code(tmp_path, dict.fromkeys(files, project), (selected,)) == {
        project.name: files[:2],
    }


@pytest.mark.parametrize("copied_entry", ["installation_context.py", "_installation_context.py"])
def test_checker_composes_canonical_and_copied_installation_programs_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, copied_entry: str,
) -> None:
    root = tmp_path / "source"
    canonical = Path(INSTALLATION_ENTRY).parent
    inputs: list[str] = []
    for source in sorted((typecheck.ROOT / canonical).glob("*.py")):
        if source.name != "installation_context.py" and not source.name.startswith("_installation_context_"):
            continue
        for directory, name in (
            (canonical, source.name),
            (Path("consumer"), copied_entry if source.name == "installation_context.py" else source.name),
        ):
            relative = directory / name
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
            inputs.append(relative.as_posix())
    paths = tuple(inputs)
    selected = tuple(path for path in paths if not path.endswith("_installation_context_worker.py"))
    environment = tmp_path / "environment"
    environment.mkdir()
    python = environment / "inert-python"
    python.write_bytes(b"never execute this analyzer")
    (environment / ".typecheck-prepared").write_bytes(b"fixture\n")
    events: list[str] = []

    class Lease:
        def release(self) -> None:
            events.append("released")

    class ForwardSlashWindowsPath(WindowsPath):
        def __str__(self) -> str:
            return super().__str__().replace("\\", "/")

    source_views = typecheck._SourceViews

    def posix_spelled_views(root: Path, stage: Path) -> typecheck._SourceViews:
        if os.name == "nt":
            stage = ForwardSlashWindowsPath(stage)
        return source_views(root, stage)

    def run(
        argv: list[str], source_root: Path, *, env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        assert source_root == root
        targets = Path(argv[-1][1:]).read_text(encoding="utf-8").splitlines()
        assert len(targets) == 2 and all(Path(target).is_file() for target in targets)
        assert all("/" in target and "\\" not in target for target in targets)
        assert sum(Path(target).name == "installation_context.py" for target in targets) == 1
        assert all(Path(target).parent == Path(targets[0]).parent for target in targets)
        assert Path(env["MYPYPATH"].split(os.pathsep)[0]) == Path(targets[0]).parent
        events.append("checked")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(typecheck, "environment_path", lambda *_args: (environment, "fixture"))
    monkeypatch.setattr(typecheck, "environment_python", lambda _path: python)
    monkeypatch.setattr(typecheck, "acquire_environment", lambda *_args: Lease())
    monkeypatch.setattr(typecheck, "_SourceViews", posix_spelled_views)
    monkeypatch.setattr(typecheck, "uuid4", lambda: SimpleNamespace(hex=""))
    monkeypatch.setattr(typecheck, "_run", run)
    project = typecheck.Project(
        "sample", ("libs/installation-context", "consumer"), (),
        "pending", "3.10", ("linux",), "requirements.txt",
    )
    assert typecheck.check_project(root, project, selected, all_paths=paths) == 0
    assert events == ["checked", "released"]
    assert not (tmp_path / "source-views-environment" / project.name).exists()


@pytest.mark.parametrize(
    "change",
    [
        "libs/payload-invocation/templates/powershell-shim.tmpl",
        "plugins/copilot-extensions-maintainer/python-emitted-inputs.json",
    ],
)
def test_new_code_refuses_program_inputs_without_physical_owner(tmp_path: Path, change: str) -> None:
    with pytest.raises(SourceViewError, match="lack their physical owner"):
        select_new_code(tmp_path, {}, (change,))


@pytest.mark.parametrize("version", ["3.10", "3.11"])
def test_new_code_invocation_requires_full_bootstrap_body_target(tmp_path: Path, version: str) -> None:
    path = "libs/payload-invocation/new.py"
    source = tmp_path / path
    source.parent.mkdir(parents=True)
    source.write_bytes(b"value: int = 1\n")
    project = typecheck.Project(
        "lib-payload-invocation", ("libs/payload-invocation",), (), "pending",
        version, ("linux",), "requirements.txt",
    )
    if version == "3.10":
        with pytest.raises(SourceViewError, match="3.11"):
            select_new_code(tmp_path, {path: project}, (path,))
    else:
        assert select_new_code(tmp_path, {path: project}, (path,)) == {project.name: (path,)}


@pytest.mark.parametrize("directive", ["# mypy: ignore-errors\n", "# type: ignore\n"])
def test_new_code_cannot_hide_the_whole_file(tmp_path: Path, directive: str) -> None:
    (tmp_path / "sample.py").write_text(directive + "value: int = 'wrong'\n", encoding="utf-8")
    project = typecheck.Project("sample", ("sample.py",), (), "pending", "3.10", ("linux",), "requirements.txt")
    with pytest.raises(SourceViewError, match="module-wide checker directives"):
        select_new_code(tmp_path, {"sample.py": project}, ("sample.py",))


def test_new_code_empty_or_deleted_scope_does_not_run_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = typecheck.Project("sample", ("sample.py",), (), "pending", "3.10", ("linux",), "requirements.txt")
    monkeypatch.setattr(typecheck, "ROOT", tmp_path)
    monkeypatch.setattr(typecheck, "load_projects", lambda: (project,))
    monkeypatch.setattr(typecheck, "python_paths", lambda: ("sample.py",))
    monkeypatch.setattr(typecheck, "new_code_changes", lambda *_args: ("deleted.py",))

    def check(*_args: object, **_kwargs: object) -> int:
        pytest.fail("No present changed Python or composed input was selected")

    monkeypatch.setattr(typecheck, "check_project", check)
    assert typecheck.main(["--new-code"]) == 0


@pytest.mark.parametrize("change", ["source", "output"])
def test_independent_setup_view_refuses_changed_bound_program(
    tmp_path: Path, change: str,
) -> None:
    relative = "consumer/project-name/setup.py"
    source = tmp_path / relative
    source.parent.mkdir(parents=True)
    source.write_bytes(b"value: int = 1\nraise RuntimeError('never execute this build')\n")
    view = setup_program_view(tmp_path, (relative,), tmp_path / "view" / "setup_program.py")
    view.verify(tmp_path)
    assert view.diagnostics(f"{view.output}:2: error: example\n", tmp_path) == (
        relative + ":2: error: example\n"
    )
    changed = source if change == "source" else view.output
    changed.write_bytes(b"value: int = 2\n")
    with pytest.raises(SourceViewError, match="source changed|checker source changed"):
        view.verify(tmp_path)
