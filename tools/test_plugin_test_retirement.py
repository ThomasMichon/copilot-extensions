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

import plugin_test_retirement as retirement
from plugin_test_resources import resource_root
from test_run_plugin_tests import runner


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    source = tmp_path / "source"
    (source / "plugins" / "example").mkdir(parents=True)
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(tmp_path / "cache"))
    root = resource_root(source) / "venvs" / sys.platform / "example"
    root.mkdir(parents=True)
    (root / "interpreter").write_bytes(b"fixture")
    (root / ".dep-fingerprint").write_text("fixture", encoding="utf-8")
    return source, root


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


def test_runner_cannot_rebuild_a_retired_inode(
    environment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, root = environment
    assert retirement.apply(retirement.preview(source, "example"))["outcome"] == "retired"
    monkeypatch.setattr(runner, "REPO", source)
    monkeypatch.setattr(runner, "VENV_ROOT", root.parent)
    with pytest.raises(ValueError, match="terminally retired"):
        runner._prepare_venv("example", "must-not-run", reinstall=True, environment={})


def test_standalone_prepare_cannot_recreate_retired_shared_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from _admission_protocol import acquire
    from test_run_standalone_tests import runner as standalone

    source = tmp_path / "source"
    (source / "worktree-manager" / "tests").mkdir(parents=True)
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(tmp_path / "cache"))
    monkeypatch.setattr(standalone, "REPO", source)
    python = standalone.default_python("worktree-manager")
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

    assert standalone.main(["worktree-manager", "--prepare"]) == 1

    assert "terminally retired" in capsys.readouterr().err
    assert not python.exists()
    assert (root.stat().st_dev, root.stat().st_ino) == (before.st_dev, before.st_ino)
    assert (root / retirement.MARKER).read_bytes() == marker
    acquire(0, root).release()


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
