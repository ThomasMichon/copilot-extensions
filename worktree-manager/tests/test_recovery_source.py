"""Fresh toolkit selection and native handoff, with no installed dispatch."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace
from contextlib import nullcontext
import hashlib
from typing import Any, TypeVar, cast

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tools"))
sys.path.insert(0, str(REPOSITORY / "plugins/agent-worktrees/src"))
sys.path.insert(0, str(REPOSITORY / "worktree-manager/scripts"))
spec = importlib.util.spec_from_file_location("recovery_source_tests_subject", REPOSITORY / "tools/recover_source.py")
assert spec is not None and spec.loader is not None
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
native_fixtures = importlib.import_module("test_local_install_source")
native_source = native_fixtures.native_source
install_copied_application = native_fixtures.test_copied_application_qualifies_after_the_prepared_source_is_removed
Result = TypeVar("Result")


def _record_result(events: list[Any], event: Any, result: Result) -> Result:
    events.append(event)
    return result


@pytest.fixture(autouse=True)
def isolated_manager_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "default-manager"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(recovery, "footprint_preview", lambda project: {"status": "unit-fixture"})


def request(**changes: Any) -> dict[str, Any]:
    return {
        "schema": recovery.REQUEST_SCHEMA, "version": 1, "operation": "preview",
        "selection": {"mode": "main", "commit": None, "checkout": None},
        "manager_root": None, "project": None, **changes,
    }


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=", "-c", "core.fsmonitor=false",
         "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "-C", str(directory), *args],
        capture_output=True, text=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    return result.stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "upstream"
    root.mkdir()
    git(root, "init", "-b", "main")
    for relative, content in {
        ".github/plugin/marketplace.json": json.dumps({"plugins": [{"name": "future-app"}]}),
        "plugins/future-app/plugin.json": json.dumps({"runtimeScope": "universal"}),
        "plugins/future-app/scripts/install.ps1": "# native future entry\n",
        "plugins/future-app/scripts/install.sh": "# native future entry\n",
        "worktree-manager/bootstrap.ps1": "# native legacy bootstrap\n",
        "worktree-manager/scripts/runtime_install.py": "# --install-only --source-digest\n",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "legacy")
    older = git(root, "rev-parse", "HEAD")
    (root / "plugins/future-app/source-install.json").write_text(json.dumps({
        "schema": "copilot-extensions.native-source-install", "version": 1,
        "windows": {"script": "scripts/install.ps1", "argument": "-InstallSource"},
        "posix": {"script": "scripts/install.sh", "argument": "--install-source"},
    }), encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "new capability")
    return root, older, git(root, "rev-parse", "HEAD")


@pytest.fixture
def declared_upstream(upstream: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, str, str]:
    root, older, current = upstream
    uri = "https://suite.example.invalid/source.git"
    configuration = tmp_path / "trusted-gitconfig"
    configuration.write_text(
        "[url " + json.dumps(str(root)) + "]\n    insteadOf = " + json.dumps(uri) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(configuration))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return root, older, current, uri


@pytest.mark.parametrize("changes", [
    {"version": True}, {"operation": "cleanup"}, {"unexpected": True},
    {"manager_root": "relative"}, {"project": False},
    {"selection": {"mode": "main", "commit": "a" * 40, "checkout": None}},
    {"selection": {"mode": "commit", "commit": "abc", "checkout": None}},
    {"selection": {"mode": "main", "commit": None, "checkout": "relative"}},
])
def test_unknown_request_shapes_are_errors(tmp_path: Path, changes: dict[str, Any]) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request(**changes)), encoding="utf-8")
    with pytest.raises(ValueError):
        recovery.read_request(path)


def test_duplicate_request_keys_refused(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text('{"schema":"one","schema":"two"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        recovery.read_request(path)


def test_fresh_toolkit_refuses_preloaded_installed_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(
        sys.modules, "installer_engine",
        SimpleNamespace(__file__=str(tmp_path / "installed/installer_engine.py")),
    )
    with pytest.raises(ValueError, match="installed installer instead of fresh source"):
        recovery.source_modules(REPOSITORY)


@pytest.mark.parametrize("mode", ["main", "commit", "configured-main-pin"])
def test_real_selection_preview_never_calls_installed_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upstream: tuple[Path, str, str], mode: str) -> None:
    from worktree_manager import engine_client, runtime_source_config

    root, older, current = upstream
    manager = tmp_path / "incumbent"
    if mode == "configured-main-pin":
        runtime_source_config.set_source(
            runtime_source_config.RuntimeSource("exact", repo=str(root), ref=older), root=manager,
        )
    before = (manager / "config.toml").read_bytes() if manager.exists() else None
    monkeypatch.setattr(recovery, "UPSTREAM", str(root))
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: pytest.fail("installed dispatch"))
    monkeypatch.setattr(recovery, "apply", lambda *a: pytest.fail("preview cannot install"))
    local = tmp_path / "caller"
    git(tmp_path, "clone", "--quiet", str(root), str(local))
    (local / "dirty.txt").write_text("caller files stay", encoding="utf-8")
    local_before = git(local, "status", "--porcelain")
    config_before = (local / ".git/config").read_bytes()
    value = request(
        manager_root=str(manager), selection={
            "mode": mode, "commit": older if mode == "commit" else None, "checkout": str(local),
        },
    )
    report: dict[str, Any] = {}
    assert recovery.recover(value, tmp_path / "work", report) == 0
    assert report["selection"]["source_commit"] == (current if mode == "main" else older)
    assert report["selection"]["source_tree"] == git(
        root, "rev-parse", (current if mode == "main" else older) + "^{tree}",
    )
    assert report["available_targets"]["future-app"] == (
        "prepared-install-source" if mode == "main" else "legacy-install-flow"
    )
    assert git(local, "status", "--porcelain") == local_before
    assert (local / ".git/config").read_bytes() == config_before
    assert git(local, "rev-parse", "HEAD") == current
    assert ((manager / "config.toml").read_bytes() if manager.exists() else None) == before


def test_local_main_is_not_ancestry_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upstream: tuple[Path, str, str]) -> None:
    root, _, _ = upstream
    local = tmp_path / "caller"
    git(tmp_path, "clone", "--quiet", str(root), str(local))
    (local / "foreign").write_text("unmerged", encoding="utf-8")
    git(local, "add", ".")
    git(local, "commit", "-qm", "not upstream")
    monkeypatch.setattr(recovery, "UPSTREAM", str(root))
    value = request(
        manager_root=str(tmp_path / "manager"),
        selection={"mode": "commit", "commit": git(local, "rev-parse", "HEAD"), "checkout": str(local)},
    )
    with pytest.raises(RuntimeError, match="not merged"):
        recovery.recover(value, tmp_path / "work", {})
    assert not (tmp_path / "manager").exists()


def test_local_object_source_need_not_have_a_local_main_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upstream: tuple[Path, str, str]) -> None:
    root, _, current = upstream
    local = tmp_path / "caller"
    git(tmp_path, "clone", "--quiet", str(root), str(local))
    git(local, "branch", "-m", "main", "work")
    before = (local / ".git/config").read_bytes()
    monkeypatch.setattr(recovery, "UPSTREAM", str(root))
    value = request(
        manager_root=str(tmp_path / "manager"),
        selection={"mode": "main", "commit": None, "checkout": str(local)},
    )
    report: dict[str, Any] = {}
    assert recovery.recover(value, tmp_path / "work", report) == 0
    assert report["selection"]["source_commit"] == current
    assert git(local, "branch", "--show-current") == "work"
    assert (local / ".git/config").read_bytes() == before


def test_actual_fresh_toolkit_preview_without_installed_python_packages(tmp_path: Path, upstream: tuple[Path, str, str]) -> None:
    from worktree_manager.runtime_source_config import RuntimeSource, set_source

    root, older, _ = upstream
    manager = tmp_path / "manager"
    set_source(RuntimeSource("exact", repo=str(root), ref=older), root=manager)
    config = (manager / "config.toml").read_bytes()

    def manager_state() -> dict[str, tuple[bytes, int, int, int]]:
        return {
            path.name: (path.read_bytes(), path.stat().st_dev, path.stat().st_ino, path.stat().st_mtime_ns)
            for path in manager.iterdir()
        }

    before = manager_state()
    value = request(
        manager_root=str(manager),
        selection={"mode": "configured-main-pin", "commit": None, "checkout": None},
    )
    path = tmp_path / "request.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
         str(REPOSITORY / "tools/recover_source.py"), "--request", str(path)],
        capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report: dict[str, Any] = json.loads(result.stdout)
    assert report["selection"]["source_commit"] == older
    assert report["effects"] == []
    assert report["stage"] == "preview"
    assert report["footprint"]["status"] in ("observed", "unavailable")
    assert manager_state() == before
    assert (manager / "config.toml").read_bytes() == config


def test_positive_cold_repository_reader_uses_toolkit_declared_parser(tmp_path: Path) -> None:
    adopter = tmp_path / "adopter"
    policy = adopter / ".copilot-extensions" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        "current_version: " + "a" * 40 + "\nlast_known_good: ['previous']\n",
        encoding="utf-8",
    )
    (adopter / "setup.py").write_text("raise AssertionError('adopter code ran')", encoding="utf-8")
    before = policy.read_bytes()
    workspace = tmp_path / "cold"
    workspace.mkdir()
    script = """
import importlib.util, json, sys
from pathlib import Path
toolkit = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("cold_recovery", toolkit / "tools/recover_source.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
recovery.source_modules(toolkit)
assert "yaml" not in sys.modules
try:
    import yaml
except ImportError:
    pass
else:
    raise AssertionError("cold interpreter unexpectedly has a YAML parser")
value = recovery.repository_declaration(Path(sys.argv[2]), Path(sys.argv[3]))
assert "yaml" not in sys.modules, "parser must run in its isolated child"
print(json.dumps(value))
"""
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B", "-c",
         script, str(REPOSITORY), str(adopter), str(workspace)],
        capture_output=True, text=True, timeout=150,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert observed["repository_root"] == str(adopter)
    assert observed["requested_reference"] == "a" * 40
    assert observed["reference"] == "a" * 40
    assert observed["fallback_reason"] is None
    assert policy.read_bytes() == before
    assert (workspace / "repository-parser" / "yaml" / "__init__.py").is_file()


def test_cold_declaration_checks_links_before_existence_or_tool_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "adopter"
    link = root / ".copilot-extensions"
    link.mkdir(parents=True)
    original = Path.stat

    def guarded(candidate: Path, *, follow_symlinks: bool=True) -> os.stat_result | SimpleNamespace:
        if candidate == link:
            if follow_symlinks:
                pytest.fail("existence probe followed the linked declaration")
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        if candidate.is_relative_to(link):
            pytest.fail("declaration target was accessed before link refusal")
        return original(candidate, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", guarded)
    monkeypatch.setattr(recovery.os, "get_exec_path", lambda *_a, **_k: pytest.fail("tool lookup before refusal"))
    with pytest.raises(ValueError, match="linked"):
        recovery.repository_declaration(root, tmp_path / "work")


def test_uv_candidate_link_refuses_before_executable_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "adopter"
    policy = root / ".copilot-extensions" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("current_version: main\n", encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    candidate = tools / ("uv.exe" if os.name == "nt" else "uv")
    candidate.write_bytes(b"fixture")
    original = os.stat

    def guarded(path: Any, *args: Any, follow_symlinks: bool=True, **kwargs: Any) -> os.stat_result | SimpleNamespace:
        if isinstance(path, (str, os.PathLike)) and Path(path) == candidate:
            if follow_symlinks:
                pytest.fail("executable discovery followed the linked UV target")
            return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
        return original(path, *args, follow_symlinks=follow_symlinks, **kwargs)

    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setattr(os, "stat", guarded)
    with pytest.raises(ValueError, match="absolute host tool path"):
        recovery.repository_declaration(root, tmp_path / "work")
    assert not (tmp_path / "work").exists()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows implicit executable lookup")
@pytest.mark.parametrize("layout", ["current-directory", "root-parent", "path-parent", "root-namespace"])
def test_actual_cold_parser_ignores_adopter_uv_executable_and_relative_path(tmp_path: Path, layout: str) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    tools = tmp_path / "trusted-tools"
    tools.mkdir()
    shutil.copyfile(uv, tools / "uv.exe")
    adopter = tmp_path / "adopter"
    policy = adopter / ".copilot-extensions" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("current_version: main\n", encoding="utf-8")
    (adopter / "uv.exe").write_bytes(b"MZ-not-an-executable")
    adopter_tools = adopter / "tool-bin"
    adopter_tools.mkdir()
    (adopter_tools / "uv.exe").write_bytes(b"MZ-not-an-executable")
    host_tools = tmp_path / "host-tools"
    host_tools.mkdir()
    addressed = adopter / ".." / adopter.name if layout == "root-parent" else adopter
    if layout == "root-namespace":
        addressed = Path("\\\\?\\" + str(adopter))
    unsafe_path = (
        host_tools / ".." / adopter.name / "tool-bin"
        if layout == "path-parent" else adopter_tools
    )
    workspace = tmp_path / "work"
    workspace.mkdir()
    script = """
import importlib.util, json, shutil, sys
from pathlib import Path
toolkit = Path(sys.argv[1])
assert Path(shutil.which("uv")).absolute().parent == Path.cwd(), "shadow control is not active"
spec = importlib.util.spec_from_file_location("cold_recovery", toolkit / "tools/recover_source.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
recovery.source_modules(toolkit)
if sys.argv[4] == "root-parent":
    try:
        recovery.repository_declaration(Path(sys.argv[2]), Path(sys.argv[3]))
    except ValueError as error:
        assert "parent" in str(error), error
        print(json.dumps({"refused": True}))
    else:
        raise AssertionError("parent-component repository was accepted")
else:
    print(json.dumps(recovery.repository_declaration(Path(sys.argv[2]), Path(sys.argv[3]))))
"""
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() != "NODEFAULTCURRENTDIRECTORYINEXEPATH"
    }
    environment["PATH"] = os.pathsep.join((".", "", str(unsafe_path), str(tools), os.environ["PATH"]))
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B", "-c",
         script, str(REPOSITORY), str(addressed), str(workspace), layout],
        cwd=adopter, env=environment, capture_output=True, text=True, timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    if layout == "root-parent":
        assert observed == {"refused": True}
        assert not (workspace / "repository-parser").exists()
    else:
        assert observed["reference"] == "main"


def repository_request(root: Path | None, **changes: Any) -> dict[str, Any]:
    return request(
        version=2, repository_root=str(root) if root is not None else None,
        selection={"mode": "repository", "commit": None, "checkout": None}, **changes,
    )


@pytest.mark.parametrize("changes", [
    {"version": 1}, {"version": 3}, {"repository_root": "relative"},
    {"operation": "install"}, {"extra": True},
    {"selection": {"mode": "main", "commit": None, "checkout": None}},
    {"selection": {"mode": "repository", "commit": "a" * 40, "checkout": None}},
])
def test_repository_request_keeps_revision_and_operation_boundaries(tmp_path: Path, changes: dict[str, Any]) -> None:
    value = repository_request(tmp_path)
    value.update(changes)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        recovery.read_request(path)


def test_actual_cold_repository_preview_selects_adopter_not_cwd_or_object_source(tmp_path: Path, declared_upstream: tuple[Path, str, str, str]) -> None:
    root, older, _, uri = declared_upstream
    adopter = tmp_path / "unregistered-adopter"
    policy = adopter / ".copilot-extensions" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        f"current_version: {older}\nrepository: {json.dumps(uri)}\n"
        "last_known_good: ['previous']\n",
        encoding="utf-8",
    )
    (adopter / "setup.py").write_text("raise AssertionError('adopter code ran')", encoding="utf-8")
    objects = tmp_path / "object-source"
    git(tmp_path, "clone", "--quiet", str(root), str(objects))
    git(objects, "branch", "-m", "main", "object-only")
    foreign_policy = objects / ".copilot-extensions" / "config.yaml"
    foreign_policy.parent.mkdir()
    foreign_policy.write_text("current_version: must-not-select\n", encoding="utf-8")
    manager = tmp_path / "manager"
    manager.mkdir()
    config = manager / "config.toml"
    config.write_text('[source]\nrepo = "https://example.invalid/independent.git"\n', encoding="utf-8")
    before = {path: path.read_bytes() for path in (policy, config, foreign_policy, objects / ".git/config")}
    value = repository_request(adopter, manager_root=str(manager))
    value["selection"]["checkout"] = str(objects)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
         str(REPOSITORY / "tools/recover_source.py"), "--request", str(path)],
        cwd=objects, capture_output=True, text=True, timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report: dict[str, Any] = json.loads(result.stdout)
    assert report["selection"]["source_commit"] == older
    assert report["selection"]["source_tree"] == git(root, "rev-parse", older + "^{tree}")
    assert report["repository"] == uri
    assert report["repository_policy"]["repository_root"] == str(adopter)
    assert report["repository_policy"]["declaration"] == str(policy)
    assert report["repository_policy"]["requested_reference"] == older
    assert report["repository_policy"]["fallback_reason"] is None
    assert report["stage"] == "preview" and report["effects"] == []
    assert report["available_targets"]["future-app"] == "legacy-install-flow"
    assert all(path.read_bytes() == content for path, content in before.items())
    assert {item.name for item in manager.iterdir()} == {"config.toml"}


@pytest.mark.parametrize("kind", [
    "locator", "invalid-locator", "malformed-yaml", "duplicate-key",
    "invalid-integer", "invalid-float",
])
def test_rejected_repository_credentials_are_not_serialized_in_preview_result(tmp_path: Path, kind: str) -> None:
    from urllib.parse import urlsplit

    scheme = "ht" + "tps"
    password = "sample-password"
    credentials = "u" + ":" + password
    repository = scheme + "://" + credentials + "@example.invalid/suite.git"
    assert urlsplit(repository).password == password
    if kind == "invalid-locator":
        repository = repository.replace("@", "\uff0f@")
    adopter = tmp_path / "adopter"
    policy = adopter / ".copilot-extensions" / "config.yaml"
    policy.parent.mkdir(parents=True)
    text = "repository: " + json.dumps(repository) + "\n"
    if kind == "malformed-yaml":
        text = 'repository: "' + repository + "\n"
    elif kind == "duplicate-key":
        text = (json.dumps(repository) + ": one\n") + (json.dumps(repository) + ": two\n")
    elif kind in {"invalid-integer", "invalid-float"}:
        tag = "int" if kind == "invalid-integer" else "float"
        text = "unrelated: !!" + tag + " " + json.dumps(password) + "\n"
    policy.write_text(text, encoding="utf-8")
    assert password.encode() in policy.read_bytes()
    manager = tmp_path / "untouched-manager"
    path = tmp_path / "request.json"
    path.write_text(json.dumps(repository_request(adopter, manager_root=str(manager))), encoding="utf-8")
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
         str(REPOSITORY / "tools/recover_source.py"), "--request", str(path)],
        capture_output=True, text=True, timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    report: dict[str, Any] = json.loads(result.stdout)
    locator = kind in {"locator", "invalid-locator"}
    assert report["stage"] == ("source-selection" if locator else "repository-interpretation")
    assert ("credential-free" if locator else "repository suite") in report["error"]
    assert password not in result.stdout + result.stderr
    assert "repository_policy" not in report and report["effects"] == []
    assert not manager.exists()


@pytest.mark.parametrize("layout", ["unresolved", "missing", "no-declaration"])
def test_repository_preview_reports_foundation_fallback_without_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upstream: tuple[Path, str, str], layout: str,
) -> None:
    root, _, current = upstream
    adopter = tmp_path / "adopter"
    if layout == "no-declaration":
        adopter.mkdir()
    monkeypatch.setattr(recovery, "UPSTREAM", str(root))
    report: dict[str, Any] = {}
    value = repository_request(None if layout == "unresolved" else adopter)
    assert recovery.recover(value, tmp_path / "work", report) == 0
    assert report["selection"]["source_commit"] == current
    assert report["repository_policy"]["fallback_reason"] == (
        "selection-absent" if layout == "no-declaration" else "repository-unavailable"
    )
    assert report["repository_policy"]["requested_reference"] is None
    assert not (tmp_path / "default-manager").exists()


@pytest.mark.parametrize("reference,channel,ref", [
    ("main", "main", ""), ("refs/heads/main", "main", ""),
    ("a" * 40, "exact", "a" * 40), ("refs/tags/v1.0", "stable", "v1.0"),
])
def test_repository_selection_uses_native_source_types(tmp_path: Path, reference: str, channel: str, ref: str) -> None:
    selected, repository = recovery.selection(
        repository_request(tmp_path), tmp_path / "manager",
        declaration={"repository": str(tmp_path), "reference": reference},
    )
    assert (selected.channel, selected.ref, repository) == (channel, ref, str(tmp_path))


@pytest.mark.parametrize("reference,repository", [
    ("dev", "https://example.invalid/suite.git"),
    ("main~2", "https://example.invalid/suite.git"),
    ("refs/heads/feature", "https://example.invalid/suite.git"),
    ("main", "https://user:secret@example.invalid/suite.git"),
])
def test_unsupported_repository_sources_are_not_main_fallback(tmp_path: Path, reference: str, repository: str) -> None:
    with pytest.raises(ValueError):
        recovery.selection(
            repository_request(tmp_path), tmp_path / "manager",
            declaration={"repository": repository, "reference": reference},
        )
    assert not (tmp_path / "manager").exists()


def test_repository_selection_preserves_independent_runtime_policy(tmp_path: Path) -> None:
    from worktree_manager.runtime_source_config import RuntimeSource, set_source

    manager = tmp_path / "manager"
    configured = RuntimeSource("exact", repo=str(tmp_path), ref="a" * 40)
    set_source(configured, root=manager)
    config = manager / "config.toml"
    before = config.read_bytes()
    value = repository_request(tmp_path)
    with pytest.raises(ValueError, match="independent runtime source policy"):
        recovery.selection(value, manager, declaration={"repository": str(tmp_path), "reference": "main"})
    selected, _ = recovery.selection(
        value, manager, declaration={"repository": str(tmp_path), "reference": "a" * 40},
    )
    assert selected == configured
    assert config.read_bytes() == before


def test_cold_repository_preview_does_not_retry_main_after_admitted_source_failure(tmp_path: Path, declared_upstream: tuple[Path, str, str, str]) -> None:
    root, _, _, uri = declared_upstream
    git(root, "checkout", "-qb", "unmerged")
    (root / "unmerged.txt").write_text("not on main", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "unmerged candidate")
    candidate = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "main")
    adopter = tmp_path / "adopter"
    policy = adopter / ".copilot-extensions" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        f"current_version: {candidate}\nrepository: {json.dumps(uri)}\n", encoding="utf-8",
    )
    before = policy.read_bytes()
    manager = tmp_path / "untouched-manager"
    path = tmp_path / "request.json"
    path.write_text(json.dumps(repository_request(adopter, manager_root=str(manager))), encoding="utf-8")
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
         str(REPOSITORY / "tools/recover_source.py"), "--request", str(path)],
        capture_output=True, text=True, timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    report: dict[str, Any] = json.loads(result.stdout)
    assert report["stage"] == "source-acquisition"
    assert "not merged into fetched main" in report["error"]
    assert report["repository_policy"]["requested_reference"] == candidate
    assert "selection" not in report and report["effects"] == []
    assert policy.read_bytes() == before
    assert not manager.exists()


def test_repository_tag_preview_keeps_one_resolved_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared_upstream: tuple[Path, str, str, str]) -> None:
    from worktree_manager import runtime_source_repository

    root, older, current, uri = declared_upstream
    git(root, "tag", "selected", older)
    adopter = tmp_path / "adopter"
    policy = adopter / ".copilot-extensions" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        f"current_version: refs/tags/selected\nrepository: {json.dumps(uri)}\n", encoding="utf-8",
    )
    reads = []

    def observe(addressed: Path | None, workspace: Path) -> dict[str, Any]:
        reads.append(addressed)
        return runtime_source_repository.read(addressed, default_repository=recovery.UPSTREAM)

    monkeypatch.setattr(recovery, "repository_declaration", observe)
    portfolio = recovery.portfolio

    def move_after_materialization(source: Path) -> dict[str, Any]:
        git(root, "tag", "-f", "selected", current)
        policy.write_text("current_version: main\n", encoding="utf-8")
        return cast(dict[str, Any], portfolio(source))

    monkeypatch.setattr(recovery, "portfolio", move_after_materialization)
    report: dict[str, Any] = {}
    assert recovery.recover(repository_request(adopter), tmp_path / "work", report) == 0
    assert reads == [adopter]
    assert report["repository_policy"]["requested_reference"] == "refs/tags/selected"
    assert report["selection"]["source_commit"] == older
    assert report["selection"]["source_tree"] == git(root, "rev-parse", older + "^{tree}")


@pytest.mark.parametrize("object_tag", ["different", "missing"])
def test_repository_tag_comes_from_declared_authority_not_object_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared_upstream: tuple[Path, str, str, str], object_tag: str,
) -> None:
    from worktree_manager import runtime_source_repository

    root, older, current, uri = declared_upstream
    git(root, "tag", "selected", older)
    objects = tmp_path / "objects"
    git(tmp_path, "clone", "--quiet", str(root), str(objects))
    if object_tag == "different":
        git(objects, "tag", "-f", "selected", current)
    else:
        git(objects, "tag", "-d", "selected")
    adopter = tmp_path / "adopter"
    policy = adopter / ".copilot-extensions" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        f"current_version: refs/tags/selected\nrepository: {json.dumps(uri)}\n", encoding="utf-8",
    )
    monkeypatch.setattr(
        recovery, "repository_declaration",
        lambda addressed, workspace: runtime_source_repository.read(
            addressed, default_repository=recovery.UPSTREAM,
        ),
    )
    value = repository_request(adopter)
    value["selection"]["checkout"] = str(objects)
    report: dict[str, Any] = {}
    assert recovery.recover(value, tmp_path / "work", report) == 0
    assert report["selection"]["source_commit"] == older
    assert report["selection"]["source_tree"] == git(root, "rev-parse", older + "^{tree}")
    assert report["repository_policy"]["repository"] == uri


def test_main_does_not_override_existing_exact_policy(tmp_path: Path) -> None:
    from worktree_manager.runtime_source_config import RuntimeSource, set_source

    set_source(RuntimeSource("exact", ref="a" * 40), root=tmp_path)
    with pytest.raises(ValueError, match="override"):
        recovery.selection(request(), tmp_path)
    with pytest.raises(ValueError, match="existing exact"):
        recovery.selection(
            request(selection={"mode": "configured-main-pin", "commit": None, "checkout": None}),
            tmp_path / "missing",
        )
    set_source(RuntimeSource("main", repo="https://example.invalid/custom.git"), root=tmp_path)
    with pytest.raises(ValueError, match="repository policy"):
        recovery.selection(request(), tmp_path)


def test_preview_rejects_unsupported_native_participant_without_installing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upstream: tuple[Path, str, str]) -> None:
    root, _, _ = upstream
    git(root, "rm", "worktree-manager/scripts/runtime_install.py")
    git(root, "commit", "-qm", "unsupported participant")
    monkeypatch.setattr(recovery, "UPSTREAM", str(root))
    monkeypatch.setattr(recovery, "apply", lambda *args: pytest.fail("no native handoff"))
    manager = tmp_path / "manager"
    with pytest.raises(ValueError, match="native installation participant"):
        recovery.recover(request(manager_root=str(manager)), tmp_path / "work", {})
    assert not manager.exists()


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is required")
def test_bootstrap_discovery_is_pure_even_with_broken_transport(tmp_path: Path) -> None:
    env = {**os.environ, "WORKTREE_MANAGER_ROOT": str(tmp_path / "untouched"),
           "WORKTREE_MANAGER_BOOTSTRAP_REPO": "missing", "WORKTREE_MANAGER_BOOTSTRAP_REF": "missing"}
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(REPOSITORY / "worktree-manager/bootstrap.ps1"),
         "--describe-recovery-source"], env=env, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    capability = json.loads(result.stdout)
    assert capability["schema"] == "copilot-extensions.recovery-source-capability"
    assert "prepared-install-source" in capability["capabilities"]
    assert capability["request_versions"] == [1, 2]
    assert "repository-preview" in capability["capabilities"]
    assert not (tmp_path / "untouched").exists()
    assert (REPOSITORY / "worktree-manager/bootstrap.ps1").read_text(
        encoding="utf-8",
    ).count("# copilot-extensions:recovery-source-contract=1") == 1


@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
@pytest.mark.parametrize("failure", ["acquisition", "native"])
def test_real_bootstrap_fresh_transport_and_exact_exit(
    tmp_path: Path, failure: str, host_name: str,
) -> None:
    from test_bootstrap_ps1 import _fake_tools

    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    toolkit = tmp_path / "current"
    (toolkit / "tools").mkdir(parents=True)
    (toolkit / "tools/recover_source.py").write_text(
        "import sys\nprint('fresh-toolkit-request=' + sys.argv[2])\nraise SystemExit(23)\n",
        encoding="utf-8",
    )
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request()), encoding="utf-8")
    bootstrap = REPOSITORY / "worktree-manager/bootstrap.ps1"
    tools = tmp_path / "launch-tools"
    _fake_tools(tools, uv_exit=0, python_exit=0)
    python = tools / ("fake-python.cmd" if os.name == "nt" else "fake-python")
    python.write_text(
        f'@echo off\r\n"{sys.executable}" %*\r\nexit /b %errorlevel%\r\n'
        if os.name == "nt" else
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n',
        encoding="utf-8",
    )
    poison_marker = tmp_path / "ambient-python-used"
    for name in ("python", "python3", "py"):
        poison = tools / (name + ".cmd" if os.name == "nt" else name)
        poison.write_text(
            f'@echo off\r\n>"{poison_marker}" echo ambient\r\nexit /b 91\r\n'
            if os.name == "nt" else
            f"#!/bin/sh\nprintf ambient > {shlex.quote(str(poison_marker))}\nexit 91\n",
            encoding="utf-8",
        )
        poison.chmod(0o755)
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    def quote(value: Path) -> str:
        return str(value).replace("'", "''")
    copy = (
        "$global:LASTEXITCODE = 41"
        if failure == "acquisition" else
        f"Copy-Item -Recurse -LiteralPath '{quote(toolkit)}' -Destination $args[-1]; "
        "$global:LASTEXITCODE = 0"
    )
    command = (
        "function git { "
        "if ($args -notcontains 'https://github.com/ThomasMichon/copilot-extensions.git') "
        "{ throw 'not current canonical upstream' }; "
        f"{copy} }}; & '{quote(bootstrap)}' --recover-source '{quote(path)}'; exit $LASTEXITCODE"
    )
    env = {
        **os.environ, "WORKTREE_MANAGER_ROOT": str(tmp_path / "untouched"),
        "WORKTREE_MANAGER_BOOTSTRAP_REPO": "must-not-use-configured-transport",
        "WORKTREE_MANAGER_BOOTSTRAP_REF": "must-not-select-this-target",
        "PATH": str(tools) + os.pathsep + os.environ.get("PATH", ""),
        "BOOTSTRAP_UV_VERSION_TRACE": str(tmp_path / "uv-version-calls"),
        "TEMP": str(temporary), "TMP": str(temporary), "TMPDIR": str(temporary),
    }
    result = subprocess.run(
        [host, "-NoProfile", "-Command", command], env=env,
        capture_output=True, text=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    assert result.returncode == (41 if failure == "acquisition" else 23), result.stderr
    assert ("fresh-toolkit-request=" in result.stdout) == (failure == "native")
    assert not (tmp_path / "untouched").exists()
    assert not poison_marker.exists()
    assert len((tmp_path / "uv-version-calls").read_text(encoding="utf-8-sig").splitlines()) == 1


@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
@pytest.mark.parametrize("prerequisite", ["uv", "managed-python"])
def test_recovery_requires_prepared_prerequisites_before_toolkit_acquisition(
    tmp_path: Path, host_name: str, prerequisite: str,
) -> None:
    from test_bootstrap_ps1 import _fake_tools

    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    tools = tmp_path / "tools"
    tools.mkdir()
    trace = tmp_path / "uv-calls"
    if prerequisite == "managed-python":
        _fake_tools(tools, uv_exit=2, python_exit=0)
        uv = tools / ("uv.cmd" if os.name == "nt" else "uv")
        original = uv.read_text(encoding="utf-8")
        uv.write_text(
            f'@echo off\r\n>>"{trace}" echo %*\r\n' + original
            if os.name == "nt" else
            original.replace("#!/bin/sh\n", "#!/bin/sh\n"
                             f'printf "%s\\n" "$*" >> {shlex.quote(str(trace))}\n', 1),
            encoding="utf-8",
        )
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    packet = tmp_path / "request.json"
    packet.write_text(json.dumps(request()), encoding="utf-8")
    root = tmp_path / "untouched"
    environment = {
        **os.environ, "PATH": str(tools), "WORKTREE_MANAGER_ROOT": str(root),
        "TEMP": str(temporary), "TMP": str(temporary), "TMPDIR": str(temporary),
    }
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File",
         str(REPOSITORY / "worktree-manager/bootstrap.ps1"), "--recover-source", str(packet)],
        env=environment, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    assert result.returncode != 0
    assert "setup" in result.stderr.lower()
    assert not root.exists()
    assert not list(temporary.glob("source-recovery.*"))
    if prerequisite == "uv":
        assert not trace.exists()
    else:
        assert trace.read_text(encoding="utf-8-sig").splitlines() == [
            "--version",
            "python find --no-project --system --managed-python --no-python-downloads 3.11",
        ]


@pytest.mark.parametrize(("manager_exit", "engine_exit", "suite_exit"), [
    (19, 0, 0), (0, 29, 0), (0, 0, 37), (0, 0, 0),
])
@pytest.mark.parametrize("future", [False, True])
def test_native_stages_preserve_exits_and_never_report_manager_as_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manager_exit: int, engine_exit: int, suite_exit: int, future: bool,
) -> None:
    from worktree_manager import engine_client, update_bootstrap
    from worktree_manager import runtime_source_update
    from worktree_manager import runtime_source_repair

    calls: list[str] = []
    monkeypatch.setattr(runtime_source_repair, "qualify_engine", lambda payload: None)
    monkeypatch.setattr(runtime_source_repair, "fresh_engine_plan", lambda *args: None)
    monkeypatch.setattr(
        recovery, "native_engine", lambda *args: _record_result(calls, "payloads", 0),
    )
    monkeypatch.setattr(recovery, "entry", lambda *args: None)
    monkeypatch.setattr(recovery.shutil, "which", lambda *args: "uv")

    def legacy(resolved: Any, workspace: Path, name: str) -> dict[str, Any]:
        payload = tmp_path / name
        payload.mkdir(exist_ok=True)
        return {
            "payload": str(payload), "identity": {
                "runtime_source": {
                    "plugin": name, "package_version": "1", "source_commit": "a" * 40,
                    "source_tree": "b" * 40,
                }, "runtime_version": "1+source.example",
            },
        }

    monkeypatch.setattr(recovery, "prepare_legacy", legacy)

    def prepared(resolved: Any, workspace: Path, names: list[str], uv: str, repository: str) -> dict[str, Any]:
        assert names == ["future-app"]
        row = legacy(resolved, workspace, "future-app")
        snapshot = tmp_path / "future-snapshot"
        snapshot.mkdir()
        row.update(install_source=str(snapshot), native={
            "identity": row["identity"],
            "runtime_version": row["identity"]["runtime_version"] + ".profile." + "d" * 16,
            "payload_hash": "e" * 64,
        })
        return {"future-app": row}

    monkeypatch.setattr(recovery, "prepare_new", prepared)
    monkeypatch.setattr(
        recovery, "install_manager", lambda *args: _record_result(calls, "manager", manager_exit),
    )
    monkeypatch.setattr(
        update_bootstrap, "_repair_installation",
        lambda *args, **kw: _record_result(
            calls, "engine", SimpleNamespace(exit_code=engine_exit, environment={}),
        ),
    )
    monkeypatch.setattr(
        engine_client, "installed_engine_command", lambda *args: pytest.fail("no installed dispatcher"),
    )
    monkeypatch.setattr(recovery, "native_inventory", lambda *args: {
        "runtime_sources": {
            "schema": runtime_source_update.SCHEMA, "version": 1, "prepared_install_source": 1,
        },
    })
    names = ["agent-worktrees", "legacy-app", *(["future-app"] if future else [])]
    monkeypatch.setattr(runtime_source_update, "_targets", lambda *args: names)

    def suite(project: str | None, packet: dict[str, Any]) -> int:
        calls.append("suite")
        assert packet["version"] == (2 if future else 1)
        assert set(packet["sources"]) == set(names)
        if future:
            assert "install_source" in packet["sources"]["future-app"]
            assert "install_source" not in packet["sources"]["legacy-app"]
        return suite_exit

    monkeypatch.setattr(recovery, "native_suite", suite)
    report: dict[str, Any] = {"manager_root": str(tmp_path / "manager"), "effects": [], "repository": recovery.UPSTREAM}
    rows = {name: {"flow": "legacy-install-flow"} for name in ("agent-worktrees", "legacy-app")}
    if future:
        rows["future-app"] = {"flow": "prepared-install-source"}
    code = recovery.apply(request(operation="install"), None, tmp_path, tmp_path, rows, report)
    assert code == (manager_exit or engine_exit or suite_exit)
    assert calls == (["payloads", "manager"] if manager_exit else ["payloads", "manager", "engine"] if engine_exit else [
        "payloads", "manager", "engine", "suite",
    ])
    assert [effect["stage"] for effect in report["effects"]] == [
        {"payloads": "payload-acquisition", "manager": "manager-installation",
         "engine": "engine-repair", "suite": "suite-execution"}[stage]
        for stage in calls
    ]


@pytest.mark.parametrize("native_source", [{"source_install": True}], indirect=True)
def test_native_recovery_inputs_reach_real_offline_application_install(
    native_source: tuple[ModuleType, Path], tmp_path: Path,
) -> None:
    source, snapshot = native_source
    payload, native = recovery.native_inputs(snapshot, "demo-app")
    selected = source.select_project(snapshot, "demo-app")
    assert payload == selected.payload
    assert native["identity"]["runtime_source"] == selected.identity.document()
    assert native["runtime_version"] != selected.identity.runtime_version
    if os.name == "nt":
        application = snapshot / selected.project["application"]
        launcher = (application / "install.ps1").read_text()
        assert "--python cpython-" in launcher
        assert "-windows-" in launcher
    install_copied_application(native_source, tmp_path, deep_slot=False)


@pytest.mark.parametrize("failure", ["error", "incomplete"])
def test_advertised_prepared_flow_failure_never_falls_back_to_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    monkeypatch.setattr(recovery, "entry", lambda *args: {"argument": "--install-source"})
    monkeypatch.setattr(recovery.shutil, "which", lambda *args: "uv")
    monkeypatch.setattr(
        recovery, "prepare_legacy", lambda *args: pytest.fail("no success-shaped legacy fallback"),
    )
    monkeypatch.setattr(
        recovery, "install_manager", lambda *args: pytest.fail("unsupported inputs cannot publish"),
    )

    def prepare(*args: Any) -> dict[str, Any]:
        if failure == "error":
            raise ValueError("native preparation failed")
        return {}

    monkeypatch.setattr(recovery, "prepare_new", prepare)
    report: dict[str, Any] = {
        "manager_root": str(tmp_path / "manager"), "effects": [], "repository": recovery.UPSTREAM,
    }
    with pytest.raises(ValueError, match="native preparation failed|omitted"):
        recovery.apply(
            request(operation="install"), None, tmp_path, tmp_path, {}, report,
        )
    assert report["stage"] == "native-preparation"
    assert report["effects"] == []


@pytest.mark.skipif(os.name != "nt", reason="Windows source-acquisition sharing errors")
@pytest.mark.parametrize("transient", [False, True])
def test_native_acquisition_rename_retries_only_transient_windows_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upstream: tuple[Path, str, str], transient: bool,
) -> None:
    from worktree_manager import runtime_source
    from worktree_manager.runtime_source_config import RuntimeSource

    repository, _older, current = upstream
    rename = Path.rename
    calls = []

    def blocked(path: Any, destination: Path) -> Path:
        if not path.parent.name.startswith(".acquire-"):
            return cast(Path, rename(path, destination))
        calls.append(path)
        if len(calls) == 1:
            error = PermissionError("fixture sharing error") if transient else OSError("fixture invalid parameter")
            setattr(error, "winerror", 5 if transient else 87)
            raise error
        return cast(Path, rename(path, destination))

    monkeypatch.setattr(Path, "rename", blocked)
    if transient:
        with runtime_source.acquire(
            RuntimeSource("main", str(repository)), root=tmp_path / "manager",
            deadline=time.monotonic() + 30,
        ) as selected:
            assert selected.commit == current
        assert len(calls) == 2
    else:
        with pytest.raises(OSError):
            with runtime_source.acquire(
                RuntimeSource("main", str(repository)), root=tmp_path / "manager",
                deadline=time.monotonic() + 30,
            ):
                pytest.fail("unclassified rename failure reached consumption")
        assert len(calls) == 1


def test_native_admission_binds_one_selection_without_peer_queue_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_install = importlib.import_module("runtime_install")
    from update_driver import RequestSpec, TrustedRunner
    from worktree_manager import engine_client

    monkeypatch.setenv("WORKTREE_MANAGER_ENGINE_ARGV", "broken-incumbent-dispatch")
    monkeypatch.setattr(
        engine_client, "inherited_engine_command", lambda: pytest.fail("no incumbent dispatch qualification"),
    )

    frozen = {"source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40}
    report: dict[str, Any] = {"selection": frozen, "manager_root": str(tmp_path / "manager"), "effects": []}
    outcome = {
        "schema": recovery.RESULT_SCHEMA, "version": 1, "selection": frozen,
        "effects": [{"stage": "engine-repair", "exit_code": 17}], "exit_code": 17,
    }
    log = tmp_path / "outcome.log"
    log.write_text(json.dumps(outcome) + "\n", encoding="utf-8")
    result = SimpleNamespace(id="fixture-request", state="completed", exit_code=17, log_path=log)
    observed: list[Any] = []
    driver = SimpleNamespace(
        RequestSpec=RequestSpec, TrustedRunner=TrustedRunner,
        run_explicit=lambda spec, **kw: _record_result(observed, (spec, kw["runner"]), result),
        wait=lambda *args, **kw: result,
    )
    monkeypatch.setattr(runtime_install, "_execution_driver", lambda *args: driver)
    monkeypatch.setattr(recovery, "prepare_tool_dependencies", lambda *_args: tmp_path / "tools")
    resolved = SimpleNamespace(
        commit="a" * 40, tree="b" * 40, document=lambda: {"commit": "a" * 40},
    )
    assert recovery.admitted_apply(request(operation="install"), resolved, tmp_path, tmp_path, {}, report) == 17
    spec, runner = observed[0]
    assert spec.backend == "source-recovery" and not spec.repair
    assert spec.cwd == runner.cwd == str(Path.cwd())
    assert "WORKTREE_MANAGER_ENGINE_ARGV" not in dict(spec.context)
    assert spec.source == hashlib.sha256((tmp_path / "execution.json").read_bytes()).hexdigest()
    assert "--execute-plan" in runner.argv
    assert report["effects"] == outcome["effects"]
    assert report["driver_request"] == result.id


def test_internal_execution_plan_cannot_grant_itself_custody(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_install = importlib.import_module("runtime_install")

    content = json.dumps({
        "schema": "copilot-extensions.source-recovery-execution", "version": 1,
        "request": {"operation": "install"},
    }).encode()
    path = tmp_path / "execution.json"
    path.write_bytes(content)
    monkeypatch.setattr(runtime_install, "_execution_driver", lambda *args: object())
    monkeypatch.setattr(runtime_install, "_installation_custody", lambda: nullcontext(None))
    monkeypatch.setattr(recovery, "apply", lambda *args: pytest.fail("no native custody"))
    with pytest.raises(ValueError, match="live native execution custody"):
        recovery.execute_plan(path, hashlib.sha256(content).hexdigest(), {})
    with pytest.raises(ValueError, match="changed after admission"):
        recovery.execute_plan(path, "a" * 64, {})


def test_selected_native_manager_owns_digest_and_exact_exit(tmp_path: Path) -> None:
    payload = tmp_path / "selected"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "selected-data").write_bytes(b"selected older tree")
    (scripts / "runtime_install.py").write_text(
        "import argparse,hashlib\nfrom pathlib import Path\n"
        "def _source_digest(payload):\n"
        "    return hashlib.sha256((payload / 'selected-data').read_bytes()).hexdigest()\n"
        "if __name__ == '__main__':\n"
        "    parser=argparse.ArgumentParser()\n"
        "    parser.add_argument('--root')\n"
        "    parser.add_argument('--payload',type=Path)\n"
        "    parser.add_argument('--install-only',action='store_true')\n"
        "    parser.add_argument('--source-digest')\n"
        "    args=parser.parse_args()\n"
        "    assert args.source_digest == _source_digest(args.payload)\n"
        "    raise SystemExit(17)\n",
        encoding="utf-8",
    )
    manager = tmp_path / "manager"
    assert recovery.install_manager({"payload": str(payload)}, manager) == 17
    assert not manager.exists()


@pytest.mark.parametrize("context", ["", "unsupported-context"])
def test_unsupported_namespace_refuses_before_manager_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, context: str) -> None:
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", context)
    monkeypatch.setattr(recovery, "prepare_new", lambda *args: pytest.fail("no construction"))
    monkeypatch.setattr(recovery, "install_manager", lambda *args: pytest.fail("no publication"))
    report: dict[str, Any] = {"effects": []}
    with pytest.raises(ValueError, match="native legacy installation scope"):
        recovery.apply(request(operation="install"), None, tmp_path, tmp_path, {}, report)
    assert report["effects"] == []


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("corruption", ["missing", "partial"])
@pytest.mark.parametrize("declared", [False, True], ids=["legacy", "custom-entry"])
def test_unattributable_engine_uses_native_fresh_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool, corruption: str, declared: bool,
) -> None:
    from agent_worktrees import reconcile, update_runtime_plan
    from worktree_manager import runtime_source_repair, update_bootstrap

    root = tmp_path / ".agent-worktrees"
    if existing:
        root.mkdir()
    artifact = tmp_path / "artifact"
    (artifact / "scripts").mkdir(parents=True)
    for extension in ("ps1", "sh"):
        (artifact / "scripts" / f"{'construct' if declared else 'install'}.{extension}").write_text(
            "# AGENT_WORKTREES_TARGETED_UPDATE repair\n", encoding="utf-8",
        )
    if declared:
        (artifact / "source-install.json").write_text(json.dumps({
            "schema": "copilot-extensions.native-source-install", "version": 1,
            "windows": {"script": "scripts/construct.ps1", "argument": "-FromBundle"},
            "posix": {"script": "scripts/construct.sh", "argument": "--from-bundle"},
        }), encoding="utf-8")
    prepared = SimpleNamespace(
        payload=artifact, install_source=tmp_path / "inputs" if declared else None,
    )
    primitive = SimpleNamespace(_current_environment=lambda **kw: ({}, str(tmp_path)))
    monkeypatch.setattr(update_bootstrap, "_policy_sdk", lambda *args: (
        None, None, {"_ordinary": lambda path: None}, {}, primitive,
    ))
    def captured(*args: Any) -> SimpleNamespace:
        if corruption == "missing":
            raise RuntimeError("unrecognized historical footprint")
        return SimpleNamespace(binding={}, payload=artifact)

    monkeypatch.setattr(update_bootstrap, "_legacy_owner_selection", captured)
    owner = tmp_path / "registered-owner"
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: owner)
    observed = []

    def plan(payload: Path, platform: str, **options: Any) -> Any:
        observed.append(options)
        return update_runtime_plan.RuntimeUpdateStep(
            "agent-worktrees", "engine_runtime", owner, source=cast(Any, prepared),
            runtime_root=root, environment={"AGENT_WORKTREES_DEFER_LOCAL_INTEGRATION": "1"},
        )

    monkeypatch.setattr(update_runtime_plan, "plan_engine", plan)
    monkeypatch.delenv("AGENT_WORKTREES_DEFER_LOCAL_INTEGRATION", raising=False)
    result = runtime_source_repair.fresh_engine_plan(tmp_path, prepared)
    assert observed[0]["action"] == ("repair" if existing else "install")
    assert observed[0]["source"] is prepared
    assert result.environment["AGENT_WORKTREES_TARGETED_UPDATE"] == "1"
    assert "AGENT_WORKTREES_DEFER_LOCAL_INTEGRATION" not in result.environment


def test_source_suite_excludes_repository_maintenance_and_manager_substitution(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(recovery, "native_engine", lambda project, args: _record_result(seen, args, 17))
    assert recovery.native_suite(None, {"frozen": "selection"}) == 17
    assert seen[0][:3] == ["--no-manager", "--phase", "runtimes"]
