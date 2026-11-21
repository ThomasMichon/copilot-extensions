"""Pinned historical source closures; not released-install or host-boot proof."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from agent_procutil import no_window_kwargs
from single_instance_lease import SingleInstance

from agent_pull_requests.watch_storage import watch_state_paths
from agent_pull_requests.watch_writer import WatchWriter

pytestmark = [
    pytest.mark.contract("agent_pull_requests.watch.writer_handoff"),
    pytest.mark.skipif(sys.platform != "win32", reason="native historical Windows sharing"),
]

_REVISIONS = [
    "24b79724ac81d65f80a50507f6a7bef5b5dc1a33",
    "a936d3b39f4d069b4e77ed11cf8e0e59bebbcd2b",
    "3c62c9442c5944640d2c13385d7ff00db614c1aa",
]
_RELEASES = {
    "9d11a84f794506956dbaeb8341578a0b0f13b899": "0.3.1-dev1",
    "c2ee1877fb55f965c141f400a31bc5e95dbebe6b": "0.3.2-dev1",
    "5ee1468f15967fd6e76a0be08ee3dc69da2551bb": "0.3.16-dev1",
}
_REVISIONS.extend(_RELEASES)
_HISTORICAL = r"""
import json, os, sys
from pathlib import Path
snapshot, root, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path[:0] = [str(snapshot / part) for part in json.loads(sys.argv[4])]
os.environ["AGENT_PULL_REQUESTS_HOME"] = str(root)
def offline(event, args):
    if event in ("subprocess.Popen", "os.system", "socket.bind", "socket.connect"):
        raise AssertionError("historical admission fixture must remain effect-free")
sys.addaudithook(offline)
from agent_pull_requests import __main__ as cli, watch_daemon
from single_instance_lease import SingleInstance, lease as native
def closure():
    prefixes = ("agent_pull_requests", "agent_procutil", "single_instance_lease",
                "work_coalescing_singleton", "zdd")
    loaded = {}
    for name, module in list(sys.modules.items()):
        if not any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes):
            continue
        path = Path(module.__file__).resolve()
        loaded[name] = str(path.relative_to(snapshot))
    for required in prefixes[:4]:
        assert required in loaded
    return loaded
if mode in ("delayed", "racer"):
    if mode == "delayed":
        original_open = native.os.open
        def delayed(path, *args, **kwargs):
            descriptor = original_open(path, *args, **kwargs)
            if Path(path) == root / "agent-pull-requests-watch.lock":
                (root / "historical-opened").write_text("opened", encoding="ascii")
                assert sys.stdin.readline() == "continue\n"
            return descriptor
        native.os.open = delayed
    owner = SingleInstance(root, service="agent-pull-requests-watch")
    owner.acquire()
    try:
        if mode == "racer":
            (root / "historical-opened").write_text("held", encoding="ascii")
            assert sys.stdin.readline() == "continue\n"
        print(json.dumps(dict(mode=mode, held=owner.held, loaded=closure(),
                              version=cli.__version__)), flush=True)
    finally:
        owner.release()
else:
    assert mode == "cold"
    refused = []
    for operation in ("serve", "restart"):
        try:
            cli.main([operation])
        except PermissionError as error:
            assert Path(error.filename) == root / "agent-pull-requests-watch.lock"
            refused.append(operation)
        else:
            raise AssertionError("historical startup admitted after sealing")
    print(json.dumps(dict(mode=mode, refused=refused, loaded=closure(),
                          version=cli.__version__)), flush=True)
"""


def _snapshot(tmp_path: Path, revision: str) -> tuple[Path, list[str]]:
    repository = Path(__file__).resolve().parents[3]
    git = shutil.which("git")
    if git is None or not (repository / ".git").exists():
        pytest.skip("historical qualification needs a local Git source checkout")
    available = subprocess.run(
        [git, "-C", str(repository), "cat-file", "-e", revision + "^{commit}"],
        capture_output=True, timeout=5, **no_window_kwargs(),
    )
    if available.returncode != 0:
        pytest.skip(f"historical qualification needs local source commit {revision}")
    plugin = "plugins/agent-pull-requests"
    dependencies = plugin + "/libs" if revision in _RELEASES else "libs"
    roots = [plugin + "/src", *[
        f"{dependencies}/{name}/src"
        for name in ("agent-procutil", "single-instance-lease", "work-coalescing-singleton")
    ]]
    if revision in (_REVISIONS[2], "5ee1468f15967fd6e76a0be08ee3dc69da2551bb"):
        roots.append(dependencies + "/zdd/src")
    archive = subprocess.run(
        [git, "-C", str(repository), "archive", "--format=tar", revision, "--", *roots],
        capture_output=True, timeout=10, **no_window_kwargs(),
    )
    assert archive.returncode == 0, archive.stderr.decode("utf-8", errors="replace")
    assert len(archive.stdout) <= 2 * 1024 * 1024
    destination = tmp_path / "historical-source"
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as source:
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            assert not path.is_absolute() and ".." not in path.parts
            if member.isdir():
                continue
            assert member.isfile()
            content = source.extractfile(member)
            assert content is not None
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.read())
    return destination, roots


def _command(snapshot: Path, root: Path, mode: str, roots: list[str]) -> list[str]:
    return [
        sys.executable, "-I", "-S", "-B", "-c", _HISTORICAL,
        str(snapshot), str(root), mode, json.dumps(roots),
    ]


def _wait_for_open(child: subprocess.Popen[str], root: Path) -> None:
    deadline = time.monotonic() + 5
    while not (root / "historical-opened").exists():
        if child.poll() is not None:
            output, errors = child.communicate(timeout=1)
            pytest.fail("historical child exited before opening: " + output + errors)
        assert time.monotonic() < deadline, "historical open deadline expired"
        time.sleep(0.01)


@pytest.mark.parametrize("revision", _REVISIONS)
@pytest.mark.parametrize("adapted", [False, True], ids=["v1", "v2"])
def test_complete_historical_source_cannot_cross_windows_sealing(
    tmp_path: Path, revision: str, adapted: bool,
) -> None:
    snapshot, roots = _snapshot(tmp_path, revision)
    root = tmp_path / "state"
    root.mkdir()
    paths = watch_state_paths(root)
    legacy = b" [] \n"
    paths.legacy_subscriptions.write_bytes(legacy)
    owner = WatchWriter(
        root=root, authorize=lambda _: None,
        fetch=lambda *_: pytest.fail("sealing must not fetch"),
        notify=lambda *_: pytest.fail("sealing must not notify"),
    )
    try:
        owner.adopt(timeout=3)
        prepared = owner.prepare_state_transition(timeout=3)
        if adapted:
            owner.recover_prepared_state_transition(timeout=3)
        seal = owner.seal_recovered_state_transition if adapted else owner.seal_state_transition
        with subprocess.Popen(
            _command(snapshot, root, "delayed", roots),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, **no_window_kwargs(),
        ) as child:
            try:
                _wait_for_open(child, root)
                with pytest.raises(PermissionError) as error:
                    seal(timeout=3)
                assert getattr(error.value, "winerror", None) == 32
                assert paths.historical_lock.is_file() and not paths.retained_lock.exists()
                assert paths.legacy_subscriptions.read_bytes() == legacy
                output, errors = child.communicate(input="continue\n", timeout=8)
                assert child.returncode == 0, output + errors
                delayed: dict[str, Any] = json.loads(output)
                assert delayed["mode"] == "delayed" and delayed["held"]
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=5)
        sealed = seal(timeout=3)
        assert sealed["lease"] == prepared["lease"] and sealed["phase"] == "sealed-legacy"
        assert sealed["version"] == (2 if adapted else 1)
        if adapted:
            assert sealed["origin"] == prepared
        result = subprocess.run(
            _command(snapshot, root, "cold", roots),
            capture_output=True, text=True, timeout=10, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        cold: dict[str, Any] = json.loads(result.stdout)
        assert cold["refused"] == ["serve", "restart"]
        if revision in _RELEASES:
            assert delayed["version"] == cold["version"] == _RELEASES[revision]
        assert "agent_pull_requests.__main__" in cold["loaded"]
        assert "agent_pull_requests.watch_daemon" in cold["loaded"]
        assert paths.legacy_subscriptions.read_bytes() == legacy
        assert not paths.isolated_subscriptions.parent.exists()
        assert not owner.read_ready and not owner.daemon.effects_enabled
    finally:
        owner.close(timeout=3)


@pytest.mark.parametrize("revision", _REVISIONS)
@pytest.mark.parametrize("adapted", [False, True], ids=["v1", "v2"])
def test_complete_historical_cold_creation_blocks_sealing_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision: str, adapted: bool,
) -> None:
    from agent_pull_requests import watch_private, watch_windows_sealing

    snapshot, roots = _snapshot(tmp_path, revision)
    root = tmp_path / "state"
    root.mkdir()
    paths = watch_state_paths(root)
    paths.legacy_subscriptions.write_bytes(b" [] \n")
    owner = WatchWriter(root=root, authorize=lambda _: None, fetch=lambda *_: pytest.fail("no fetch"))
    child: subprocess.Popen[str] | None = None
    move = watch_private.windows_move_no_replace

    def raced(source: Path, destination: Path) -> None:
        nonlocal child
        move(source, destination)
        if destination == paths.retained_lock:
            child = subprocess.Popen(
                _command(snapshot, root, "racer", roots),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, **no_window_kwargs(),
            )
            _wait_for_open(child, root)

    try:
        owner.adopt(timeout=3)
        prepared = owner.prepare_state_transition(timeout=3)
        if adapted:
            owner.recover_prepared_state_transition(timeout=3)
        seal = owner.seal_recovered_state_transition if adapted else owner.seal_state_transition
        monkeypatch.setattr(watch_windows_sealing, "windows_move_no_replace", raced)
        with pytest.raises(PermissionError, match="raced historical"):
            seal(timeout=8)
        assert child is not None
        retained, recreated = paths.retained_lock.read_bytes(), paths.historical_lock.read_bytes()
        original = paths.retained_lock.stat()
        competitor = paths.historical_lock.stat()
        assert (original.st_dev, original.st_ino) == (
            prepared["lease"]["device"], prepared["lease"]["inode"],
        )
        assert (competitor.st_dev, competitor.st_ino) != (original.st_dev, original.st_ino)

        def forbidden(*_args: Any, **_kwargs: Any) -> None:
            pytest.fail("raced recovery must not acquire either historical inode")

        monkeypatch.setattr(SingleInstance, "acquire", forbidden)
        with pytest.raises(PermissionError, match="raced historical"):
            seal(timeout=3)
        output, errors = child.communicate(input="continue\n", timeout=8)
        assert child.returncode == 0, output + errors
        receipt: dict[str, Any] = json.loads(output)
        assert receipt["mode"] == "racer" and receipt["held"]
        if revision in _RELEASES:
            assert receipt["version"] == _RELEASES[revision]
        with pytest.raises(PermissionError, match="raced historical"):
            seal(timeout=3)
        interrupted = json.loads(paths.journal.read_bytes())
        assert interrupted["version"] == (2 if adapted else 1)
        if adapted:
            assert interrupted["origin"] == prepared
        assert not owner.writer_held and not paths.writer_lock.exists()
        assert paths.retained_lock.read_bytes() == retained
        assert paths.historical_lock.read_bytes() == recreated
        assert paths.legacy_subscriptions.read_bytes() == b" [] \n"
        assert paths.staged_barrier.is_dir() and not paths.isolated_subscriptions.parent.exists()
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
            child.__exit__(None, None, None)
        owner.close(timeout=3)
