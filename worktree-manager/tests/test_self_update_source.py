"""Manager's merged-Dev acquisition remains independent of service source policy."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest

from worktree_manager import runtime_source_self_update as update
from worktree_manager import self_install as si
from worktree_manager import source_config
from worktree_manager.runtime_source import ResolvedSource, SourceAcquisitionError
from worktree_manager.runtime_source_config import RuntimeSource, set_source


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))


def test_helper_pins_only_manager_source_and_prepares_under_its_held_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "manager"
    repo = str(tmp_path / "manager-repository")
    set_source(RuntimeSource("main", str(tmp_path / "unavailable-service-source")), root=root)
    config = (root / "config.toml").read_bytes()
    auth = {"APPROVED_AUTH": "fixture"}
    calls = []
    tool = tmp_path / "own-tool"
    output = root / "manager-source/runtime-sources/prepared"
    payload = output / "worktree-manager/qualified"
    monkeypatch.setattr(update, "preparer", lambda: tool)

    @contextmanager
    def acquire(selection: RuntimeSource, **kwargs: object):
        assert selection == RuntimeSource("dev", repo, allow_dev=True)
        assert kwargs == {"root": root / "manager-source", "environment": auth}
        calls.append("held")
        yield ResolvedSource(selection, tmp_path / "clean", "a" * 40, "b" * 40, "a" * 40)
        calls.append("released")

    def prepare(actual: Path, resolved: ResolvedSource, name: str, destination: Path) -> dict:
        assert calls == ["held"]
        assert actual == tool and resolved.commit == "a" * 40
        assert name == "worktree-manager" and destination == output
        return {"payload": str(payload)}

    monkeypatch.setattr(update, "acquire", acquire)
    monkeypatch.setattr(update, "prepare", prepare)
    assert update.fetch(repo, root, environment=auth) == payload
    assert calls == ["held", "released"]
    assert (root / "config.toml").read_bytes() == config


@pytest.mark.parametrize("relative_root", [False, True])
def test_source_acceptance_patches_activation_in_the_drivers_actual_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative_root: bool,
) -> None:
    import json
    import os
    import sys

    native = Path(__file__).parent / "native"
    monkeypatch.syspath_prepend(str(native))
    import test_manager_source_install as fixture

    scripts = tmp_path / "payload" / "scripts"
    scripts.mkdir(parents=True)
    root = tmp_path / "fixture"
    (root / "versions" / "candidate").mkdir(parents=True)
    (scripts / "runtime_install.py").write_text(
        """
import json,os,subprocess,sys
from pathlib import Path
from types import SimpleNamespace
runtime=SimpleNamespace(is_complete=lambda root,version: version=="candidate")
driver=SimpleNamespace(TrustedRunner=lambda argv: SimpleNamespace(argv=argv))
def _execution_driver(payload):
    return driver
def _activate_after_bootstrap_update(root,slot):
    raise AssertionError("fixture child missed the activation patch")
def main(arguments):
    root=Path(arguments[1])
    if arguments[0]=="child":
        result=_activate_after_bootstrap_update(root,root/"versions"/"candidate")
        print(json.dumps({"pid":os.getpid(),"activation":result}))
        return 0
    runner=driver.TrustedRunner([
        sys.executable,"-I","-S","-B",str(Path(__file__)),"child",str(root),
    ])
    child=subprocess.run(
        runner.argv,capture_output=True,text=True,timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0,
    )
    if child.returncode:
        print(child.stderr,file=sys.stderr)
        return child.returncode
    print(json.dumps({"pid":os.getpid(),"child":json.loads(child.stdout)}))
    return 0
if __name__=="__main__":
    raise SystemExit(main(sys.argv[1:]))
""",
        encoding="utf-8",
    )
    result = fixture._fixture_command([
        Path(sys.executable), "-I", "-S", "-B", scripts / "runtime_install.py", "parent",
        root.relative_to(tmp_path) if relative_root else root,
    ], dict(os.environ), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert len({os.getpid(), observed["pid"], observed["child"]["pid"]}) == 3
    assert observed["child"]["activation"] == {
        "action": "fixture", "slot": str(root / "versions" / "candidate"),
    }
    receipt, = (root / "source-fixture-activation.jsonl").read_text().splitlines()
    assert json.loads(receipt) == {
        "pid": observed["child"]["pid"], "root": str(root), "slot": str(root / "versions" / "candidate"),
    }


def test_source_acceptance_snapshot_capture_keeps_complete_inputs_and_restores_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "native"))
    import test_manager_source_install as fixture

    snapshot = tmp_path / "applications" / "owned" / "profiles" / "worktree-manager" / "snapshot"
    calls = []

    def prepare(*args: object, **options: object) -> dict[str, Path]:
        calls.append((args, options))
        return {"worktree-manager": snapshot}

    monkeypatch.setattr(update, "prepare_applications", prepare)
    with fixture._selected_snapshot() as captured:
        assert update.prepare_applications("trusted-tool", deadline=100) == {"worktree-manager": snapshot}
        assert captured == [snapshot]
    assert update.prepare_applications is prepare
    assert calls == [(("trusted-tool",), {"deadline": 100})]


@pytest.mark.parametrize("mismatch", [None, "commit", "tree", "seed", "application"])
def test_complete_manager_selection_is_revalidated_then_held_through_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str | None,
) -> None:
    import builtins

    root = tmp_path / "manager"
    repo = str(tmp_path / "manager-repository")
    set_source(RuntimeSource("main", str(tmp_path / "service-source")), root=root)
    before = (root / "config.toml").read_bytes()
    auth = {"APPROVED_AUTH": "fixture"}
    calls = []
    budget = []
    payload = tmp_path / "prepared-payload"
    snapshot = tmp_path / "snapshot"
    wheels = object()
    application = tmp_path / "application"
    monkeypatch.setattr(update, "preparer", lambda: tmp_path / "own-tool")

    @contextmanager
    def acquire(selection, **options):
        assert selection == RuntimeSource("dev", repo=repo, allow_dev=True)
        assert options["root"] == root / "manager-source" and options["environment"] is auth
        assert time.monotonic() < options["deadline"] <= time.monotonic() + 600
        budget.append(options["deadline"])
        calls.append("acquired")
        yield ResolvedSource(selection, tmp_path / "checkout", "a" * 40, "b" * 40, "c" * 40, "d" * 40)
        calls.append("revalidated")

    def prepare(tool, resolved, names, output, **options):
        assert tool == tmp_path / "own-tool" and names == ("worktree-manager",)
        assert output.parent == root / "manager-source" / "runtime-sources" / "applications"
        assert len(output.name) == 32
        assert options == {"deadline": budget[0], "environment": auth}
        assert calls == ["acquired"]
        calls.append("prepared")
        return {"worktree-manager": snapshot}

    @contextmanager
    def held(actual):
        assert actual == snapshot and calls == ["acquired", "prepared", "revalidated"]
        calls.append("inputs-held")
        selected = SimpleNamespace(
            payload=payload,
            identity=SimpleNamespace(
                source_commit="e" * 40 if mismatch == "commit" else "a" * 40,
                source_tree="e" * 40 if mismatch == "tree" else "b" * 40,
            ),
            composition=None if mismatch == "seed" else {"main_seed": "d" * 40, "tree": "f" * 40},
        )
        options = {"wheel_inputs": wheels, "local_packages": ("manager",)}
        if mismatch != "application":
            options["application"] = application
        try:
            yield selected, options
        finally:
            calls.append("inputs-released")

    monkeypatch.setattr(update, "acquire", acquire)
    monkeypatch.setattr(update, "prepare_applications", prepare)
    monkeypatch.setattr(update, "load_runtime_installer", lambda _: SimpleNamespace(prepared_manager_inputs=held))
    importer = builtins.__import__

    def cold_import(name, *args, **kwargs):
        if name.split(".")[0] == "packaging":
            pytest.fail("complete Manager handoff borrowed an incumbent Packaging dependency")
        return importer(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", cold_import)
    if mismatch:
        with pytest.raises(ValueError, match="independent source selection"):
            with update.prepared(repo, root, environment=auth):
                pytest.fail("different source reached installation")
    else:
        with update.prepared(repo, root, environment=auth) as (actual, options):
            assert actual == payload
            assert options == {
                "_wheel_inputs": wheels, "_local_packages": ("manager",), "_application": application,
            }
            assert calls == ["acquired", "prepared", "revalidated", "inputs-held"]
            calls.append("consumed")
    assert calls[-1] == "inputs-released"
    assert (root / "config.toml").read_bytes() == before


@pytest.mark.parametrize("dry_run", [False, True])
def test_self_update_uses_existing_publication_and_cutover_with_qualified_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool,
) -> None:
    root = tmp_path / "manager"
    repo = str(tmp_path / "chosen-manager")
    source_config.set_source(repo=repo, ref="dev", root=root)
    before = (root / "config.toml").read_bytes()
    payload = tmp_path / "qualified"
    payload.mkdir()
    (payload / "pyproject.toml").write_text("[project]\nversion='1'\n")
    auth = {"APPROVED_AUTH": "fixture"}
    seen = []
    monkeypatch.setattr(si.shutil, "which", lambda name: "git")

    def auth_environment(source: str, *, cwd: Path) -> dict[str, str]:
        assert source == repo and cwd == root / "manager-source"
        assert cwd.is_dir()
        return auth

    wheel_inputs = object()

    @contextmanager
    def prepared(source: str, destination: Path, *, environment: dict[str, str]):
        assert (source, destination, environment) == (repo, root, auth)
        seen.append("qualified")
        try:
            yield payload, {"_wheel_inputs": wheel_inputs, "_application": payload / "application"}
        finally:
            seen.append("released")

    def install(**kwargs: object) -> si.SelfInstallResult:
        assert kwargs["payload_dir"] == payload and kwargs["root"] == root
        assert kwargs["dry_run"] is dry_run and seen == ["qualified"]
        assert kwargs["_wheel_inputs"] is wheel_inputs
        assert kwargs["_application"] == payload / "application"
        assert kwargs["_transactional_activation"] is True
        if dry_run:
            assert kwargs["_after_activate"] is None
        else:
            kwargs["_after_activate"](root / "versions/1+source.qualified")
        return si.SelfInstallResult(
            action="planned" if dry_run else "installed", version="1", root=str(root),
        )

    def cutover(**kwargs: object) -> dict:
        assert kwargs == {
            "root": root, "slot": root / "versions/1+source.qualified",
            "version": "1+source.qualified",
        }
        seen.append("cutover")
        return {"action": "fixture"}

    monkeypatch.setattr("worktree_manager.update_auth.environment", auth_environment)
    monkeypatch.setattr(update, "prepared", prepared)
    monkeypatch.setattr(si, "self_install", install)
    monkeypatch.setattr(
        si._runtime_installer(), "_activate_after_bootstrap_update",
        lambda actual_root, slot: cutover(root=actual_root, slot=slot, version=slot.name),
    )
    result = si.self_update(root=root, dry_run=dry_run)
    assert result.action == "updated" and result.version == "1"
    assert seen == (["qualified", "released"] if dry_run else ["qualified", "cutover", "released"])
    assert (root / "config.toml").read_bytes() == before
    assert not (root / si.STAGING_DIR).exists()


@pytest.mark.parametrize("fault", ["acquire", "revalidate", "prepare"])
def test_source_failures_never_install_or_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    root = tmp_path / "manager"
    source_config.set_source(repo=str(tmp_path / "upstream"), ref="dev", root=root)
    monkeypatch.setattr(si.shutil, "which", lambda name: "git")
    monkeypatch.setattr("worktree_manager.update_auth.environment", lambda *a, **kw: None)
    monkeypatch.setattr(update, "preparer", lambda: tmp_path / "own-tool")
    monkeypatch.setattr(si, "self_install", lambda **kw: pytest.fail("no installation"))
    monkeypatch.setattr(si, "_fetch_via_tarball", lambda *a: pytest.fail("no fallback"))

    @contextmanager
    def acquire(selection: RuntimeSource, **kwargs: object):
        if fault == "acquire":
            raise SourceAcquisitionError("fixture acquisition refusal")
        yield ResolvedSource(selection, tmp_path, "a" * 40, "b" * 40, "a" * 40, "c" * 40)
        if fault == "revalidate":
            raise SourceAcquisitionError("fixture revalidation refusal")

    def prepare(*args: object, **kwargs: object) -> dict:
        if fault == "prepare":
            raise ValueError("fixture preparation refusal")
        return {"worktree-manager": tmp_path / "unpublished"}

    monkeypatch.setattr(update, "acquire", acquire)
    monkeypatch.setattr(update, "prepare_applications", prepare)
    result = si.self_update(root=root)
    diagnostic = {"acquire": "acquisition", "revalidate": "revalidation", "prepare": "preparation"}
    assert result.action == "error" and f"fixture {diagnostic[fault]} refusal" in result.reason


def test_complete_input_change_after_install_reports_failure_without_replaying(tmp_path, monkeypatch):
    root = tmp_path / "manager"
    source_config.set_source(repo=str(tmp_path / "source"), ref="dev", root=root)
    (root / "current-version").write_text("previous\n")
    payload = tmp_path / "incoming"
    payload.mkdir()
    (payload / "pyproject.toml").write_text("[project]\nversion='1'\n")
    monkeypatch.setattr(si.shutil, "which", lambda _: "git")
    monkeypatch.setattr("worktree_manager.update_auth.environment", lambda *args, **kwargs: None)
    calls = []

    @contextmanager
    def prepared(*args, **kwargs):
        yield payload, {}
        raise ValueError("fixture held input changed")

    monkeypatch.setattr(update, "prepared", prepared)
    monkeypatch.setattr(si, "self_install", lambda **kwargs: (
        calls.append(kwargs) or si.SelfInstallResult(action="installed", version="1", root=str(root))
    ))
    result = si.self_update(root=root)
    assert result.action == "error" and result.version == "1" and result.previous == "previous"
    assert "fixture held input changed" in result.reason
    assert len(calls) == 1
    assert (root / "current-version").read_text() == "previous\n"


def test_dev_without_git_is_explicitly_unsupported_before_auth_or_tarball(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "manager"
    source_config.set_source(ref="dev", root=root)
    monkeypatch.setattr(si.shutil, "which", lambda name: None)
    monkeypatch.setattr("worktree_manager.update_auth.environment", lambda *a, **kw: pytest.fail("no auth"))
    monkeypatch.setattr(si, "_fetch_via_tarball", lambda *a: pytest.fail("no unqualified fallback"))
    result = si.self_update(root=root)
    assert result.action == "skipped" and "requires git" in result.reason
    assert not (root / "manager-source").exists()


def test_dev_auth_required_is_reported_before_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager.update_auth import AuthRequired

    root = tmp_path / "manager"
    source_config.set_source(ref="dev", root=root)
    monkeypatch.setattr(si.shutil, "which", lambda name: "git")
    monkeypatch.setattr(update, "fetch", lambda *a, **kw: pytest.fail("no acquisition"))

    def refuse(*args: object, **kwargs: object) -> None:
        raise AuthRequired("fixture auth required")

    monkeypatch.setattr("worktree_manager.update_auth.environment", refuse)
    result = si.self_update(root=root)
    assert result.action == "auth_required" and result.reason == "fixture auth required"


@pytest.mark.parametrize("command", ("clone", "fetch", "checkout"))
def test_main_git_failure_preserves_captured_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str,
) -> None:
    root = tmp_path / "manager"
    source_config.set_source(repo=str(tmp_path / "source"), ref="main", root=root)
    if command != "clone":
        (root / si.STAGING_DIR / ".git").mkdir(parents=True)
    marker = root / "current-version"
    marker.write_text("0.9.0\n", encoding="utf-8")
    monkeypatch.setattr(si.shutil, "which", lambda name: "git")
    monkeypatch.setattr("worktree_manager.update_auth.environment", lambda *a, **kw: None)
    monkeypatch.setattr(si, "self_install", lambda **kw: pytest.fail("No acquired candidate"))

    def run(argv: list[str], **options: object) -> subprocess.CompletedProcess:
        assert argv[:3] == ["git", "-c", "core.longpaths=true"]
        if command in argv:
            raise subprocess.CalledProcessError(128, argv, stderr="fatal: fixture checkout I/O failure")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(si.subprocess, "run", run)
    result = si.self_update(root=root)
    assert result.action == "error"
    assert "fatal: fixture checkout I/O failure" in result.reason
    assert marker.read_text(encoding="utf-8") == "0.9.0\n"
