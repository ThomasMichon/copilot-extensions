"""Source recipe and simulated guest preparation; no build, guest or service."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from test_reproducer_lab_monitoring import LAB

sys.path.insert(0, str(LAB))
import guest_runtime

REAL_RUNTIME_IDENTITY = guest_runtime._runtime_identity
REAL_SEED_DESCRIPTOR = guest_runtime._seed_descriptor


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: Any) -> SimpleNamespace:
    if sys.platform != "linux":
        pytest.skip("synthetic Linux directory-descriptor fixtures")
    seed, state = tmp_path / "seed", tmp_path / "state"
    seed.mkdir()
    state.mkdir()
    (state / guest_runtime.LOCK.name).touch()
    runtime = state / "runtime"
    monkeypatch.setattr(guest_runtime, "SEED", seed)
    monkeypatch.setattr(guest_runtime, "STATE", state)
    monkeypatch.setattr(guest_runtime, "RUNTIME", runtime)
    calls: list[tuple[list[str], dict[str, object]]] = []
    assertions: list[bool] = []
    descriptor = os.open(seed, os.O_RDONLY | os.O_DIRECTORY)
    monkeypatch.setattr(guest_runtime, "_seed_descriptor", lambda: os.dup(descriptor))
    # This fixture does not qualify root-owned read-only image or guest UID 1000.
    monkeypatch.setattr(guest_runtime, "_runtime_identity", runtime.lstat)

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        assert runtime.is_dir() and runtime.stat().st_mode & 0o777 == 0o700
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(guest_runtime, "run", run)
    try:
        yield SimpleNamespace(
            seed=seed, state=state, runtime=runtime, calls=calls, assertions=assertions,
            custody=SimpleNamespace(assert_owned=lambda: assertions.append(True)),
        )
    finally:
        os.close(descriptor)


def test_one_finite_copy_has_no_inherited_environment(paths: SimpleNamespace) -> None:
    assert guest_runtime.prepare_runtime(paths.custody) is None
    assert len(paths.calls) == 1 and len(paths.assertions) == 3
    argv, options = paths.calls[0]
    assert argv == [
        "/usr/bin/cp", "--recursive", "--no-dereference", "--preserve=timestamps",
        "--", str(paths.seed) + "/.", str(paths.runtime),
    ]
    assert options == {
        "timeout": 30, "max_stdout_bytes": 16 * 1024,
        "env": {"HOME": "/home/lab", "PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
    }
    assert list(paths.runtime.iterdir()) == []  # Simulated copy, not installed proof.
    with pytest.raises(ValueError, match="retained guest state"):
        guest_runtime.prepare_runtime(paths.custody)
    assert len(paths.calls) == 1


@pytest.mark.parametrize("existing", ["directory", "file", "link", "dangling"])
def test_existing_destination_never_resumes_or_overwrites(
    paths: SimpleNamespace, existing: str,
) -> None:
    if existing == "directory":
        paths.runtime.mkdir()
        (paths.runtime / "retained").write_text("unfinished", encoding="utf-8")
    elif existing == "file":
        paths.runtime.write_text("unfinished", encoding="utf-8")
    else:
        paths.runtime.symlink_to(paths.seed if existing == "link" else paths.state / "missing")
    with pytest.raises(ValueError, match="retained guest state"):
        guest_runtime.prepare_runtime(paths.custody)
    assert not paths.calls


@pytest.mark.parametrize("failure", ["exit", "exception"])
def test_copy_failure_retains_the_attempt_without_retry(
    paths: SimpleNamespace, monkeypatch: Any, failure: str,
) -> None:
    def fail(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        (paths.runtime / "partial").write_text("retained", encoding="utf-8")
        if failure == "exception":
            raise TimeoutError("synthetic capture timeout")
        return subprocess.CompletedProcess(argv, 1, "", "private synthetic diagnostic")

    monkeypatch.setattr(guest_runtime, "run", fail)
    with pytest.raises((RuntimeError, TimeoutError)):
        guest_runtime.prepare_runtime(paths.custody)
    assert (paths.runtime / "partial").read_text() == "retained"
    with pytest.raises(ValueError, match="retained guest state"):
        guest_runtime.prepare_runtime(paths.custody)


@pytest.mark.parametrize("retained", ["dispatch", "bridge", "registrar.d", "unknown"])
def test_retained_state_refuses_even_without_a_runtime(
    paths: SimpleNamespace, retained: str,
) -> None:
    (paths.state / retained).mkdir()
    with pytest.raises(ValueError, match="original owner"):
        guest_runtime.prepare_runtime(paths.custody)
    assert not paths.runtime.exists() and not paths.calls


def test_missing_original_lock_refuses_before_copy(paths: SimpleNamespace, monkeypatch: Any) -> None:
    # Select a different fixed name rather than deleting the fixture's lock.
    monkeypatch.setattr(guest_runtime, "LOCK", Path("/synthetic/absent.lock"))
    with pytest.raises(ValueError, match="retained guest state"):
        guest_runtime.prepare_runtime(paths.custody)
    assert not paths.runtime.exists() and not paths.calls


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_custody_loss_is_not_preparation_success(
    paths: SimpleNamespace, stage: int,
) -> None:
    checks = 0

    def assert_owned() -> None:
        nonlocal checks
        checks += 1
        if checks == stage:
            raise ValueError("synthetic lost operation")

    paths.custody.assert_owned = assert_owned
    with pytest.raises(ValueError, match="lost operation"):
        guest_runtime.prepare_runtime(paths.custody)
    assert len(paths.calls) == (1 if stage == 3 else 0)
    assert paths.runtime.exists() is (stage > 1)


def test_event_loop_refuses_before_filesystem_effect(paths: SimpleNamespace) -> None:
    async def invoke() -> None:
        guest_runtime.prepare_runtime(paths.custody)

    with pytest.raises(RuntimeError, match="outside an event loop"):
        asyncio.run(invoke())
    assert not paths.runtime.exists() and not paths.calls


@pytest.mark.parametrize("stage", ["before", "after"])
def test_changed_seed_refuses(paths: SimpleNamespace, monkeypatch: Any, stage: str) -> None:
    original = paths.seed
    replacement = paths.state.parent / "replacement"
    replacement.mkdir()
    run = guest_runtime.run

    if stage == "before":
        monkeypatch.setattr(guest_runtime, "SEED", replacement)
    else:
        def replace(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            result = run(argv, **kwargs)
            monkeypatch.setattr(guest_runtime, "SEED", replacement)
            return result
        monkeypatch.setattr(guest_runtime, "run", replace)
    with pytest.raises(ValueError, match="seed changed|source or destination changed"):
        guest_runtime.prepare_runtime(paths.custody)
    assert original.is_dir()
    assert len(paths.calls) == (1 if stage == "after" else 0)


def test_final_destination_posture_is_rechecked(paths: SimpleNamespace, monkeypatch: Any) -> None:
    calls = 0

    def identity() -> os.stat_result:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("synthetic changed destination posture")
        return paths.runtime.lstat()

    monkeypatch.setattr(guest_runtime, "_runtime_identity", identity)
    with pytest.raises(ValueError, match="changed destination posture"):
        guest_runtime.prepare_runtime(paths.custody)
    assert len(paths.calls) == 1


def test_state_appearing_during_copy_is_not_success(
    paths: SimpleNamespace, monkeypatch: Any,
) -> None:
    run = guest_runtime.run

    def copy(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        result = run(argv, **kwargs)
        (paths.state / "dispatch").mkdir()
        return result

    monkeypatch.setattr(guest_runtime, "run", copy)
    with pytest.raises(ValueError, match="original owner"):
        guest_runtime.prepare_runtime(paths.custody)
    assert len(paths.calls) == 1 and paths.runtime.exists()


@pytest.mark.parametrize("change", ["uid", "gid", "mode", "type", "device", "redirect"])
def test_runtime_destination_requires_fixed_posture(
    paths: SimpleNamespace, monkeypatch: Any, change: str,
) -> None:
    info = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1000, st_gid=1000, st_dev=42)
    if change == "mode":
        info.st_mode |= 0o020
    elif change == "type":
        info.st_mode = stat.S_IFREG | 0o700
    elif change == "device":
        info.st_dev = 99
    elif change in {"uid", "gid"}:
        setattr(info, "st_" + change, 0)
    monkeypatch.setattr(Path, "lstat", lambda path: info if path == paths.runtime else SimpleNamespace(st_dev=42))
    monkeypatch.setattr(Path, "resolve", lambda path, **_kw: paths.seed if change == "redirect" else path)
    with pytest.raises(ValueError, match="private guest-state"):
        REAL_RUNTIME_IDENTITY()


@pytest.mark.parametrize("change", ["uid", "gid", "mode", "writable", "identity", "redirect"])
def test_seed_requires_root_owned_read_only_image(
    paths: SimpleNamespace, monkeypatch: Any, change: str,
) -> None:
    info = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0, st_dev=42, st_ino=10)
    closed: list[int] = []
    if change == "mode":
        info.st_mode |= 0o020
    elif change in {"uid", "gid"}:
        setattr(info, "st_" + change, 1000)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", lambda path, **_kw: paths.state if change == "redirect" else path)
        patch.setattr(Path, "lstat", lambda _path: SimpleNamespace(
            st_dev=42, st_ino=11 if change == "identity" else 10,
        ))
        patch.setattr(guest_runtime.os, "open", lambda *_args: 123)
        patch.setattr(guest_runtime.os, "fstat", lambda _fd: info)
        patch.setattr(guest_runtime.os, "fstatvfs", lambda _fd: SimpleNamespace(
            f_flag=0 if change == "writable" else os.ST_RDONLY,
        ))
        patch.setattr(guest_runtime.os, "close", closed.append)
        with pytest.raises(ValueError, match="immutable image|read-only image"):
            REAL_SEED_DESCRIPTOR()
    assert closed == ([] if change == "redirect" else [123])


def test_recipe_resets_runtime_metadata_and_does_not_start_services() -> None:
    recipe = (LAB / "Dockerfile").read_text(encoding="utf-8")
    final = recipe.split("FROM scratch\n", 1)[1]
    assert 'ENV HOME=/home/lab PATH=/usr/local/bin:/usr/bin:/bin PYTHONUNBUFFERED=1' in final
    assert "USER 1000:1000" in final
    assert 'ENTRYPOINT ["/usr/bin/sleep", "infinity"]' in final
    for instruction in ("VOLUME ", "EXPOSE ", "HEALTHCHECK ", "ARG ", "RUN "):
        assert not any(line.startswith(instruction) for line in final.splitlines())
    for plugin in ("worktrees", "bridge", "dispatch"):
        assert f"UV_PROJECT_ENVIRONMENT=/lab/state/runtime/{plugin}" in recipe
        assert f"uv sync --project plugins/agent-{plugin} --no-dev --no-editable" in recipe
    assert recipe.count('--exclude-newer "7 days"') == 3
    assert "UV_PYTHON_DOWNLOADS=never" in recipe and "UV_LINK_MODE=copy" in recipe
    assert "mv /lab/state/runtime /opt/lab/runtime-seed" in recipe
    assert "COPY source/ /opt/lab/source/" in recipe
    assert "COPY source.json /opt/lab/source.json" in recipe
    assert "immutable image references required" in recipe
    assert "chmod -R a+rX /opt/lab/source && chmod 0644 /opt/lab/source.json" in recipe
    assert "chmod -R a+rX /opt/lab/runtime-seed" in recipe
    assert "ln -sfnT /usr/local/bin/python3 /usr/bin/python3" in recipe
    assert (LAB / "Dockerfile.dockerignore").read_text().splitlines() == [
        "**", "!source/", "!source/**", "!source.json", "source/.git", "source/.git/**",
    ]


def test_guest_cli_uses_only_fixed_persistent_runtime_launchers() -> None:
    launcher = (LAB / "guest_cli.sh").read_text(encoding="utf-8")
    for plugin in ("worktrees", "bridge", "dispatch"):
        assert f"exec /lab/state/runtime/{plugin}/bin/agent-{plugin}" in launcher
    assert "eval" not in launcher
    assert "Unsupported reproducer runtime command" in launcher


def test_container_keepalive_is_in_the_existing_venue_census() -> None:
    census = json.loads((LAB.parent / "daemon-coverage.json").read_text(encoding="utf-8"))
    key = "t:reproducer-lab/Dockerfile::<script>::ENTRYPOINT"
    assert census["sites"]["venue-agent"][key] == 1
    assert sum(key in sites for sites in census["sites"].values()) == 1
