"""Selected cache retirement preserves leases, source and unrelated environments."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import venv

import pytest

from _admission_protocol import acquire
from plugin_test_cache import PREPARATION_GENERATION, advance_preparation_generation, preparation_generation
import plugin_test_retirement as retirement
from plugin_test_resources import ENVIRONMENT_LAYOUT, retirement_venv_root, venv_root
from test_run_plugin_tests import runner


def declare_support(source: Path) -> Path:
    directory = source / "tools"
    directory.mkdir()
    path = directory / "test-environment-capabilities.json"
    path.write_bytes(Path(retirement.__file__).with_name(path.name).read_bytes())
    return path


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    source = tmp_path / "source"
    (source / "plugins" / "example").mkdir(parents=True)
    declare_support(source)
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(tmp_path / "cache"))
    root = retirement_venv_root(source) / "example"
    root.mkdir(parents=True)
    (root / "interpreter").write_bytes(b"fixture")
    (root / ".dep-fingerprint").write_text("fixture", encoding="utf-8")
    return source, root


@pytest.fixture
def standalone_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    (source / "worktree-manager" / "tests").mkdir(parents=True)
    declare_support(source)
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(tmp_path / "cache"))
    root = retirement_venv_root(source) / "worktree-manager"
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture")
    return source, root, python


def test_retirement_keeps_identity_source_and_other_targets(environment: tuple[Path, Path]) -> None:
    source, root = environment
    before = root.stat()
    unrelated = root.with_name("another")
    unrelated.mkdir()
    (unrelated / "keep").write_bytes(b"unrelated")
    history = source / "history"
    history.write_bytes(b"preserve")

    plan = retirement.preview(source, "example")
    assert (root / "interpreter").exists()
    result = retirement.apply(plan)

    assert result["outcome"] == "retired"
    assert result["source_retirement_safe"] is True
    assert result["scope"] == "selected-environment-only"
    assert (root.stat().st_dev, root.stat().st_ino) == (before.st_dev, before.st_ino)
    assert [child.name for child in root.iterdir()] == [retirement.MARKER]
    assert history.read_bytes() == b"preserve"
    assert (unrelated / "keep").read_bytes() == b"unrelated"
    with pytest.raises(ValueError, match="terminally retired"):
        retirement.require_active(root)


@pytest.mark.parametrize("name", [".dep-fingerprint.pending", ".dep-fingerprint.pending.update"])
def test_retirement_refuses_unsettled_preparation_under_the_lease(
    environment: tuple[Path, Path], name: str,
) -> None:
    source, root = environment
    plan = retirement.preview(source, "example")
    (root / ".source-inputs").mkdir()
    (root / ".source-inputs" / "retained.py").write_text("RETAIN = True\n")
    (root / name).write_text('{"owner":"unsettled-fixture"}')
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="Unsettled preparation"):
        retirement.apply(plan)
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert not (root / retirement.MARKER).exists()
    acquire(0, root).release()


def test_runner_cannot_rebuild_a_retired_inode(
    environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, root = environment
    assert retirement.apply(retirement.preview(source, "example"))["outcome"] == "retired"
    monkeypatch.setattr(runner, "REPO", source)
    monkeypatch.setattr(runner, "VENV_ROOT", root.parent)
    with pytest.raises(ValueError, match="terminally retired"):
        runner._prepare_venv("example", "must-not-run", reinstall=True, environment={})


def test_plugin_cli_selects_and_refuses_the_retired_protocol_environment(
    environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, root = environment
    assert retirement.apply(retirement.preview(source, "example"))["outcome"] == "retired"
    tests = source / "plugins" / "example" / "tests"
    tests.mkdir()
    (tests / "test_unused.py").write_text("raise AssertionError('must not import')\n", encoding="utf-8")
    monkeypatch.setattr(runner, "REPO", source)
    monkeypatch.setattr(runner, "PLUGINS", source / "plugins")
    monkeypatch.setattr(runner, "VENV_ROOT", None)
    monkeypatch.setattr(runner, "RETIREMENT_READY", False)

    assert runner.main(["example", "--retirement-ready", "--prepare-only"]) == 1

    assert "terminally retired" in capsys.readouterr().err
    assert [child.name for child in root.iterdir()] == [retirement.MARKER]
    acquire(0, root).release()


def test_standalone_prepare_cannot_recreate_retired_shared_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from test_run_standalone_tests import runner as standalone

    source = tmp_path / "source"
    (source / "worktree-manager" / "tests").mkdir(parents=True)
    declare_support(source)
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(tmp_path / "cache"))
    monkeypatch.setattr(standalone, "REPO", source)
    python = standalone.default_python("worktree-manager", retirement_ready=True)
    python.parent.mkdir(parents=True)
    python.touch()
    root = python.parent.parent
    assert retirement.apply(retirement.preview(source, "worktree-manager"))["outcome"] == "retired"
    before = root.stat()
    marker = (root / retirement.MARKER).read_bytes()
    monkeypatch.setattr(
        standalone.subprocess, "run",
        lambda *args, **kwargs: pytest.fail("retired environment started preparation"),
    )

    assert standalone.main(["worktree-manager", "--prepare", "--retirement-ready"]) == 1

    assert "terminally retired" in capsys.readouterr().err
    assert not python.exists()
    assert (root.stat().st_dev, root.stat().st_ino) == (before.st_dev, before.st_ino)
    assert (root / retirement.MARKER).read_bytes() == marker
    acquire(0, root).release()


def test_standalone_execution_refuses_an_interrupted_retirement_with_interpreter_present(
    standalone_environment: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from test_run_standalone_tests import runner as standalone

    source, root, python = standalone_environment

    def interrupt(path: Path, *, preserve: frozenset[str] = frozenset()) -> None:
        raise PermissionError("fixture interrupted before clearing interpreter")

    monkeypatch.setattr(retirement, "clear_cache_contents", interrupt)
    result = retirement.apply(retirement.preview(source, "worktree-manager"))
    assert result["outcome"] == "partial-failure" and python.is_file()
    assert json.loads((root / retirement.MARKER).read_text())["state"] == "retiring"
    monkeypatch.setattr(standalone, "REPO", source)
    monkeypatch.setattr(
        standalone, "run_contained",
        lambda *args, **kwargs: pytest.fail("fenced environment executed tests"),
    )

    assert standalone.main(["worktree-manager", "--retirement-ready"]) == 1

    assert "terminally retired" in capsys.readouterr().err
    assert python.is_file()
    acquire(0, root).release()


@pytest.mark.parametrize("preparer", ["plugin", "standalone"])
def test_preparation_generation_invalidates_preview_before_deep_mutation(
    standalone_environment: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, preparer: str,
) -> None:
    from test_run_standalone_tests import runner as standalone

    source, root, python = standalone_environment
    payload = root / "lib" / "python" / "site-packages" / "fixture.py"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"old")
    lease = acquire(0, root)
    try:
        advance_preparation_generation(root)
    finally:
        lease.release()
    before = preparation_generation(root)
    plan = retirement.preview(source, "worktree-manager")
    lease = acquire(0, root)
    try:
        if preparer == "standalone":
            def update(uv: str, arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                payload.write_bytes(b"new")
                return subprocess.CompletedProcess(arguments, 0, "", "")

            monkeypatch.setattr(standalone, "REPO", source)
            monkeypatch.setattr(standalone, "run_uv_install", update)
            standalone.prepare("worktree-manager", python)
        else:
            def interrupted_clear(path: Path) -> None:
                payload.write_bytes(b"new")
                raise PermissionError("fixture interrupted inside a deep rebuild")

            monkeypatch.setattr(runner, "REPO", source)
            monkeypatch.setattr(runner, "VENV_ROOT", root.parent)
            monkeypatch.setattr(runner, "_dep_fingerprint", lambda name: "fixture")
            monkeypatch.setattr(runner, "clear_cache_contents", interrupted_clear)
            with pytest.raises(PermissionError, match="deep rebuild"):
                runner._prepare_venv("worktree-manager", "must-not-run", reinstall=True, environment={})
    finally:
        lease.release()

    assert preparation_generation(root) != before
    with pytest.raises(ValueError, match="stale"):
        retirement.apply(plan)
    assert payload.read_bytes() == b"new"
    assert not (root / retirement.MARKER).exists()


def test_corrupt_preparation_generation_refuses_before_mutation(environment: tuple[Path, Path]) -> None:
    source, root = environment
    generation = root / PREPARATION_GENERATION
    generation.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="preparation generation"):
        retirement.preview(source, "example")
    lease = acquire(0, root)
    try:
        with pytest.raises(ValueError, match="preparation generation"):
            advance_preparation_generation(root)
    finally:
        lease.release()
    assert generation.read_bytes() == b"corrupt" and (root / "interpreter").is_file()


def test_failed_preparation_generation_publication_keeps_prior_generation(
    environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, root = environment
    lease = acquire(0, root)
    try:
        advance_preparation_generation(root)
        before = preparation_generation(root)
        original = Path.replace

        def fail(path: Path, target: str | Path) -> Path:
            if path.name.startswith(PREPARATION_GENERATION + ".new-"):
                raise PermissionError("fixture generation publication failed")
            return original(path, target)

        monkeypatch.setattr(Path, "replace", fail)
        with pytest.raises(PermissionError, match="generation publication"):
            advance_preparation_generation(root)
    finally:
        lease.release()
    assert preparation_generation(root) == before
    assert not list(root.glob(PREPARATION_GENERATION + ".new-*"))
    assert (root / "interpreter").is_file()


@pytest.mark.parametrize("target", ["fleet-manager", "services/index"])
def test_standalone_preparation_and_retirement_select_the_same_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    from test_run_standalone_tests import runner as standalone

    source = tmp_path / "source"
    project = source / target
    (project / "tests").mkdir(parents=True)
    declare_support(source)
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(tmp_path / "cache"))
    monkeypatch.setattr(standalone, "REPO", source)
    python = standalone.default_python(target, retirement_ready=True)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture")
    root = python.parent.parent
    before = root.stat()

    plan = retirement.preview(source, target)
    assert Path(plan.environment) == root
    result = retirement.apply(plan)

    assert result["outcome"] == "retired"
    assert not python.exists() and project.is_dir()
    assert (root.stat().st_dev, root.stat().st_ino) == (before.st_dev, before.st_ino)
    assert [child.name for child in root.iterdir()] == [retirement.MARKER]


@pytest.mark.parametrize("target", ["services/unregistered", "services/../index", "../fleet-manager"])
def test_nested_target_allowlist_does_not_admit_arbitrary_paths(
    environment: tuple[Path, Path], target: str,
) -> None:
    source, root = environment
    with pytest.raises(ValueError, match="Invalid test target"):
        retirement.preview(source, target)
    assert (root / "interpreter").read_bytes() == b"fixture"


@pytest.mark.parametrize("support", ["missing", "incompatible", "boolean-version"])
def test_legacy_source_is_not_retired_by_a_new_tool(
    environment: tuple[Path, Path], support: str,
) -> None:
    source, root = environment
    declaration = source / "tools" / "test-environment-capabilities.json"
    if support == "missing":
        declaration.unlink()
    elif support == "boolean-version":
        value = json.loads(declaration.read_text())
        value["retirement"]["version"] = True
        declaration.write_text(json.dumps(value), encoding="utf-8")
    else:
        declaration.write_text('{"version": 0}', encoding="utf-8")
    with pytest.raises((OSError, ValueError)):
        retirement.preview(source, "example")
    assert (root / "interpreter").read_bytes() == b"fixture"
    assert not (root / retirement.MARKER).exists()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_legacy_waiter_cannot_rebuild_the_retirement_aware_namespace(
    environment: tuple[Path, Path],
) -> None:
    source, root = environment
    legacy = venv_root(source) / "example"
    legacy.mkdir(parents=True)
    ready = source.parent / "legacy-waiter-ready"
    code = (
        "import pathlib,sys;"
        "sys.path.insert(0,sys.argv[1]);"
        "from _admission_protocol import acquire;"
        "root=pathlib.Path(sys.argv[2]);"
        "pathlib.Path(sys.argv[3]).write_text('legacy waiter ready');"
        "sys.stdin.readline();"
        "lease=acquire(10,root);"
        "(root/'legacy-imports').write_text('legacy rebuild');"
        "lease.release()"
    )
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(Path(__file__).parent), str(legacy), str(ready)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    lease = acquire(0, legacy)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "legacy waiter did not become ready"
        assert process.stdin is not None
        process.stdin.write("\n")
        process.stdin.flush()
        assert ENVIRONMENT_LAYOUT in root.parts and root != legacy
        assert retirement.apply(retirement.preview(source, "example"))["outcome"] == "retired"
    finally:
        lease.release()
        _, error = process.communicate(timeout=10)
    assert process.returncode == 0, error
    assert (legacy / "legacy-imports").read_text() == "legacy rebuild"
    assert [child.name for child in root.iterdir()] == [retirement.MARKER]
    with pytest.raises(ValueError, match="terminally retired"):
        retirement.require_active(root)


@pytest.mark.parametrize("change", ["contents", "replacement", "source-replacement", "wrong-source", "foreign-platform"])
def test_stale_or_wrong_scope_preview_never_removes(
    environment: tuple[Path, Path], change: str,
) -> None:
    source, root = environment
    plan = retirement.preview(source, "example")
    if change == "contents":
        (root / "later").write_bytes(b"new")
    elif change == "replacement":
        root.rename(root.with_name("retained-old"))
        root.mkdir()
        (root / "interpreter").write_bytes(b"replacement")
    elif change == "source-replacement":
        source.rename(source.with_name("retained-source"))
        (source / "plugins" / "example").mkdir(parents=True)
    elif change == "wrong-source":
        plan = replace(plan, source=str(source.parent))
    else:
        plan = replace(plan, platform="foreign")
    with pytest.raises(ValueError):
        retirement.apply(plan)
    assert (root / "interpreter").exists()
    assert not (root / retirement.MARKER).exists()


def test_partial_retirement_holds_source_and_can_be_repreviewed(
    environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, root = environment
    clear = retirement.clear_cache_contents

    def fail(path: Path, *, preserve: frozenset[str] = frozenset()) -> None:
        (path / "interpreter").unlink()
        raise PermissionError("selected cache file is held")

    monkeypatch.setattr(retirement, "clear_cache_contents", fail)
    result = retirement.apply(retirement.preview(source, "example"))
    assert result["outcome"] == "partial-failure"
    assert result["source_retirement_safe"] is False
    assert source.is_dir() and (root / ".dep-fingerprint").exists()
    with pytest.raises(ValueError, match="terminally retired"):
        retirement.require_active(root)
    (root / (retirement.MARKER + ".new")).write_bytes((root / retirement.MARKER).read_bytes())
    monkeypatch.setattr(retirement, "clear_cache_contents", clear)
    assert retirement.apply(retirement.preview(source, "example"))["outcome"] == "retired"


def test_failed_marker_publication_cleans_its_own_temporary_file(
    environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, root = environment
    replace_path = Path.replace

    def failed(path: Path, target: str | Path) -> Path:
        if path.name.startswith(retirement.MARKER + ".new-"):
            raise PermissionError("fixture marker publication failed")
        return replace_path(path, target)

    monkeypatch.setattr(Path, "replace", failed)
    with pytest.raises(PermissionError, match="fixture marker"):
        retirement.apply(retirement.preview(source, "example"))
    assert (root / "interpreter").exists()
    assert not list(root.glob(retirement.MARKER + ".new-*"))
    assert not (root / retirement.MARKER).exists()
    monkeypatch.setattr(Path, "replace", replace_path)
    assert retirement.apply(retirement.preview(source, "example"))["outcome"] == "retired"


def test_metadata_substitution_refuses_before_mutation(environment: tuple[Path, Path]) -> None:
    source, root = environment
    owner = root.parents[2] / "scratch-owner.json"
    data = json.loads(owner.read_text(encoding="utf-8"))
    data["source_checkout"] = str(source.parent)
    owner.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="another source"):
        retirement.preview(source, "example")
    assert (root / "interpreter").exists()


def test_corrupt_retirement_marker_keeps_preparation_and_removal_closed(
    environment: tuple[Path, Path],
) -> None:
    source, root = environment
    (root / retirement.MARKER).write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        retirement.preview(source, "example")
    with pytest.raises(ValueError, match="terminally retired"):
        retirement.require_active(root)
    assert (root / "interpreter").exists()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_cli_requires_the_exact_preview_before_apply(
    environment: tuple[Path, Path], tmp_path: Path,
) -> None:
    source, root = environment

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(Path(retirement.__file__)), *args],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    shown = run("preview", "--source", str(source), "--target", "example")
    assert shown.returncode == 0, shown.stderr
    plan = json.loads(shown.stdout)
    preview_file = tmp_path / "preview.json"
    preview_file.write_text(json.dumps(plan), encoding="utf-8")
    applied = run("apply", "--preview-file", str(preview_file))
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)["outcome"] == "retired"
    replayed = run("apply", "--preview-file", str(preview_file))
    assert replayed.returncode == 2, replayed.stderr
    assert json.loads(replayed.stdout)["outcome"] == "refused"
    assert root.is_dir()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_busy_native_process_blocks_retirement(environment: tuple[Path, Path], tmp_path: Path) -> None:
    source, root = environment
    plan = retirement.preview(source, "example")
    ready = tmp_path / "ready"
    code = (
        "import pathlib,sys,threading,os;"
        "sys.path.insert(0,sys.argv[1]);"
        "from _admission_protocol import acquire;"
        "lease=acquire(0,pathlib.Path(sys.argv[2]));"
        "threading.Timer(15,lambda:os._exit(124)).start();"
        "pathlib.Path(sys.argv[3]).write_text('ready');"
        "sys.stdin.readline();lease.release();os._exit(0)"
    )
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(Path(__file__).parent), str(root), str(ready)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "lease holder did not become ready"
        with pytest.raises(retirement.AlreadyRunningError):
            retirement.apply(plan)
        assert (root / "interpreter").exists()
        assert not (root / retirement.MARKER).exists()
    finally:
        _, error = process.communicate("\n", timeout=10)
    assert process.returncode == 0, error
    assert retirement.apply(plan)["outcome"] == "retired"


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_interrupted_owner_keeps_the_fence_and_requires_a_new_preview(
    environment: tuple[Path, Path], tmp_path: Path,
) -> None:
    source, root = environment
    plan = retirement.preview(source, "example")
    ready = tmp_path / "retirement-ready"
    code = "\n".join((
        "import json,os,pathlib,sys,threading",
        "sys.path.insert(0,sys.argv[1])",
        "import plugin_test_retirement as r",
        "threading.Timer(15,lambda:os._exit(124)).start()",
        "original=r.clear_cache_contents",
        "def paused(path,*,preserve=frozenset()):",
        " pathlib.Path(sys.argv[3]).write_text('ready')",
        " sys.stdin.readline()",
        " original(path,preserve=preserve)",
        "r.clear_cache_contents=paused",
        "r.apply(r.parse_preview(json.loads(sys.argv[2])))",
    ))
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(Path(__file__).parent), json.dumps(asdict(plan)), str(ready)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "retirement owner did not reach its write fence"
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)
    assert process.returncode != 0
    assert (root / "interpreter").exists() and source.is_dir()
    with pytest.raises(ValueError, match="terminally retired"):
        retirement.require_active(root)
    with pytest.raises(ValueError, match="stale"):
        retirement.apply(plan)
    assert retirement.apply(retirement.preview(source, "example"))["outcome"] == "retired"


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_interrupted_final_marker_publication_recovers_with_a_new_preview(
    environment: tuple[Path, Path], tmp_path: Path,
) -> None:
    source, root = environment
    plan = retirement.preview(source, "example")
    ready = tmp_path / "marker-publication-ready"
    code = "\n".join((
        "import json,os,pathlib,sys,threading",
        "sys.path.insert(0,sys.argv[1])",
        "import plugin_test_retirement as r",
        "threading.Timer(15,lambda:os._exit(124)).start()",
        "original=pathlib.Path.replace",
        "def paused(path,target):",
        " if path.name.startswith(r.MARKER+'.new-') and json.loads(path.read_text())['state']=='retired':",
        "  pathlib.Path(sys.argv[3]).write_text('ready')",
        "  sys.stdin.readline()",
        " return original(path,target)",
        "pathlib.Path.replace=paused",
        "r.apply(r.parse_preview(json.loads(sys.argv[2])))",
    ))
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(Path(__file__).parent), json.dumps(asdict(plan)), str(ready)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "retirement owner did not reach final marker publication"
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)
    assert process.returncode != 0
    assert json.loads((root / retirement.MARKER).read_text())["state"] == "retiring"
    assert len(list(root.glob(retirement.MARKER + ".new-*"))) == 1
    assert source.is_dir()
    assert retirement.apply(retirement.preview(source, "example"))["outcome"] == "retired"
    assert [path.name for path in root.iterdir()] == [retirement.MARKER]


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_prepared_import_depends_on_source_after_process_exit_and_retirement_disables_it(
    environment: tuple[Path, Path],
) -> None:
    source, root = environment
    module = source / "retained_fixture.py"
    module.write_text("VALUE = 73\n", encoding="utf-8")
    venv.EnvBuilder(with_pip=False).create(root)
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    def run(code: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(python), "-I", "-c", code], capture_output=True, text=True,
            timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    location = run("import sysconfig;print(sysconfig.get_path('purelib'))")
    assert location.returncode == 0, location.stderr
    (Path(location.stdout.strip()) / "retained-fixture.pth").write_text(str(source) + "\n", encoding="utf-8")
    imported = run("import retained_fixture;print(retained_fixture.VALUE)")
    assert imported.returncode == 0 and imported.stdout.strip() == "73"
    assert retirement.apply(retirement.preview(source, "example"))["outcome"] == "retired"
    assert not python.exists()
    assert module.read_text(encoding="utf-8") == "VALUE = 73\n"


def test_preview_roundtrip_rejects_unknown_fields(environment: tuple[Path, Path]) -> None:
    source, _ = environment
    data = asdict(retirement.preview(source, "example"))
    assert retirement.parse_preview(data).target == "example"
    data["unexpected"] = True
    with pytest.raises(ValueError, match="Unsupported"):
        retirement.parse_preview(data)
