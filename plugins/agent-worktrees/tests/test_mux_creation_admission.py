"""Short creation custody is distinct from seed and backend custody."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from types import SimpleNamespace
from typing import BinaryIO, cast

import pytest

from agent_worktrees import activity, embody_completion, sessions
from agent_remote_driver import mux_creation, windows_mux_version
from agent_remote_driver.mux_observation_types import Observation
from single_instance_lease import SingleInstance


def test_gate_rechecks_under_lease_and_releases_before_join(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    answers = iter([False, True])
    with mux_creation.session_gate("wt-a", exists=lambda: next(answers), enabled=True) as create:
        assert create is False
        path, = (tmp_path / ".psmux" / "launch-locks").iterdir()
        with SingleInstance(path.parent, lock_name=path.name):
            pass


def test_gate_releases_on_creation_failure_and_keeps_lock_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(RuntimeError, match="creation failed"):
        with mux_creation.session_gate("wt-a", exists=lambda: False, enabled=True) as create:
            assert callable(create)
            raise RuntimeError("creation failed")
    path, = (tmp_path / ".psmux" / "launch-locks").iterdir()
    before = path.stat().st_ino
    with mux_creation.session_gate("wt-a", exists=lambda: False, enabled=True):
        assert path.stat().st_ino == before


@pytest.mark.parametrize("failure", [2, 17, "timeout"])
def test_discovery_failure_never_builds_or_launches_a_backend(
    monkeypatch: pytest.MonkeyPatch, failure: int | str,
) -> None:
    from agent_remote_driver import mux_observation

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "psmux")
    monkeypatch.setattr(
        sessions, "build_mux_new_session_argv",
        lambda *args, **kwargs: pytest.fail("uncertain discovery is not absence"),
    )

    def observe(*args: object, **kwargs: object) -> Observation:
        assert kwargs["timeout"] == 2
        return Observation("unknown", str(failure))

    monkeypatch.setattr(mux_observation, "observe_psmux", observe)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("passive PSMux CLI"))
    result = sessions.mux_new_session("a", "checkout", ["copilot"])
    assert result["ok"] is False
    assert result["new_pane"] is None


def test_detached_join_does_not_build_a_second_pane_or_emit_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def joined(*args: object, **kwargs: object) -> Iterator[bool]:
        yield False

    monkeypatch.setattr(mux_creation, "session_gate", joined)
    monkeypatch.setattr(
        windows_mux_version, "require_creation_binary",
        lambda *_args: pytest.fail("a legacy join must not require a creation-version probe"),
    )
    monkeypatch.setattr(
        sessions, "build_mux_new_session_argv",
        lambda *args, **kwargs: pytest.fail("joined target must not prepare a pane"),
    )
    monkeypatch.setattr(
        activity, "log_event",
        lambda *args, **kwargs: pytest.fail("joined target is not a creation"),
    )
    result = sessions.mux_new_session("a", "checkout", ["copilot"])
    assert result == {
        "ok": True, "session": "wt-a", "new_pane": None, "error": None, "created": False,
    }

@pytest.mark.parametrize("failure", [
    "query", "nonzero-before-discovery", "nonzero-after-discovery",
])
@pytest.mark.parametrize("diagnostic", [b"", b"discarded-prefix" * 512 + b"\nfixture creation failure\n"])
def test_post_dispatch_failure_preserves_pending_pane_arguments(
    monkeypatch: pytest.MonkeyPatch, failure: str, diagnostic: bytes,
) -> None:
    released: list[bool] = []

    @contextmanager
    def acquired(*args: object, **kwargs: object) -> Iterator[Callable[[], None]]:
        yield lambda: released.append(True)

    class Client:
        killed = False
        returncode: int | None = None
        waits = 0

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            if self.killed:
                return 1
            self.waits += 1
            if failure == "nonzero-before-discovery" or (
                failure == "nonzero-after-discovery" and self.waits > 1
            ):
                self.returncode = 17
                return 17
            assert timeout is not None
            raise subprocess.TimeoutExpired(["psmux"], timeout)

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.killed = True
            self.returncode = 1

    child = Client()
    def failed_query(*args: object, **kwargs: object) -> bool:
        if failure == "query":
            raise RuntimeError("query unavailable")
        return True

    monkeypatch.setattr(mux_creation, "session_gate", acquired)
    monkeypatch.setattr(windows_mux_version, "require_creation_binary", lambda binary: binary)
    def start(*args: object, **kwargs: object) -> Client:
        stderr = cast(BinaryIO, kwargs["stderr"])
        stderr.write(diagnostic)
        stderr.flush()
        return child

    monkeypatch.setattr(subprocess, "Popen", start)
    monkeypatch.setattr(mux_creation, "has_session", failed_query)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "psmux")
    monkeypatch.setattr(sessions, "build_mux_new_session_argv", lambda *a, **k: ["psmux"])
    monkeypatch.setattr(sessions, "cleanup_mux_pane_args",
                        lambda argv: pytest.fail("post-dispatch is not definite non-creation"))
    result = sessions.mux_new_session("a", "checkout", ["copilot"])
    assert result["ok"] is False
    assert "uncertain" in result["error"]
    assert ("query unavailable" if failure == "query" else "exited 17") in result["error"]
    if failure != "query" and diagnostic:
        assert result["error"].endswith("fixture creation failure")
        assert len(result["error"]) < 4200
        assert diagnostic.decode() not in result["error"]
    assert child.killed is (failure == "query")
    assert bool(released) is (failure == "nonzero-after-discovery")


@pytest.mark.parametrize("acquisition", [False, True])
def test_join_completion_never_claims_or_types_a_seed(
    monkeypatch: pytest.MonkeyPatch, acquisition: bool,
) -> None:
    from agent_worktrees import agent_policy

    monkeypatch.setattr(agent_policy, "require", lambda path: None)
    replies: list[dict[str, object] | str] = []

    def json_error(message: str, *, exit_code: int) -> int:
        replies.append(message)
        return exit_code

    output = SimpleNamespace(
        _json_output=replies.append,
        _json_error=json_error,
    )
    result = embody_completion.complete_launch(
        None, wt_id="a", work_dir="checkout", record=object(), acquisition=acquisition,
        deferred={}, explicit_seed=True, seed="do not type this", prepared_context=None,
        resume_target=None, driver=None, selection=None,
        result={"created": False, "session": "wt-a"}, cfg=None, sessions=None,
        output=output, launch_seed_state=None, tracking_write=None,
        profile_assignment=None, clock=None,
    )
    assert result == (3 if acquisition else 0)
    assert len(replies) == 1
    if not acquisition:
        response = replies[0]
        assert isinstance(response, dict)
        assert response["created"] is False and response["resumed"] is True


@pytest.mark.parametrize("observation", ["present", "absent", "unavailable", "timeout"])
def test_requested_verification_controls_success_without_replaying_native_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observation: str,
) -> None:
    from agent_worktrees import agent_policy

    monkeypatch.setattr(agent_policy, "require", lambda path: None)
    replies: list[dict[str, object]] = []
    now = [0.0]
    queries: list[float] = []

    def probe(worktree_id: str, *, strict: bool, timeout: float) -> bool:
        assert worktree_id == "a" and strict
        assert 0 < timeout <= 1 - now[0]
        queries.append(now[0])
        if observation == "unavailable":
            raise RuntimeError("observation unavailable")
        if observation == "timeout":
            raise subprocess.TimeoutExpired(["mux", "has-session"], timeout)
        return observation == "present"

    def no_replay(*args: object, **kwargs: object) -> None:
        pytest.fail("Verification must not claim, restage or resend the native prompt")

    code = embody_completion.complete_launch(
        SimpleNamespace(verify_timeout=1), wt_id="a", work_dir="checkout",
        record=object(), acquisition=False, deferred={}, explicit_seed=True,
        seed="original prompt", prepared_context=None, resume_target=None, driver=None,
        selection=SimpleNamespace(assignment=None),
        result={"created": True, "session": "wt-a", "new_pane": "%1"},
        cfg=SimpleNamespace(tracking_dir=lambda: tmp_path),
        sessions=SimpleNamespace(
            has_mux_session=probe, mux_session_name=lambda wt: "wt-" + wt,
            mux_seed_pane=no_replay,
        ),
        output=SimpleNamespace(_json_output=replies.append),
        launch_seed_state=SimpleNamespace(
            settle_creation=lambda *args: {}, claim_creation=no_replay,
        ),
        tracking_write=None, profile_assignment=None,
        clock=SimpleNamespace(
            monotonic=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        ),
        native_seed_id="original-seed",
    )
    response, = replies
    assert response["created"] is True
    assert response["seed_id"] == "original-seed"
    assert response["seed_reason"] == "native-startup-pending"
    assert response["seeded"] is False and response["seed_submitted"] is False
    assert response["ok"] is (observation == "present")
    assert code == (0 if observation == "present" else 3)
    assert response["mux_verified"] is (
        True if observation == "present" else False if observation == "absent" else None
    )
    if observation != "present":
        error = response["error"]
        assert isinstance(error, str) and "retry" in error
    if observation == "absent":
        assert len(queries) > 1 and now[0] == 1
    else:
        assert len(queries) == 1


@pytest.mark.parametrize("version", [None, (3, 3, 5, 0), (3, 3, 8, 0), (3, 4, 0, 0), (3, 4, 1, 0)])
def test_new_creation_requires_passive_version_admission_before_preparation(
    monkeypatch: pytest.MonkeyPatch, version: tuple[int, int, int, int] | None,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(shutil, "which", lambda binary: r"C:\fixture\psmux.exe")
    monkeypatch.setattr(windows_mux_version, "file_version", lambda binary: version)
    monkeypatch.setattr(windows_mux_version, "default_search_roots", lambda: [])

    @contextmanager
    def acquired(*args: object, **kwargs: object) -> Iterator[Callable[[], None]]:
        yield lambda: None

    monkeypatch.setattr(mux_creation, "session_gate", acquired)
    calls: list[str] = []

    def prepare() -> list[str]:
        calls.append("prepare")
        return ["psmux", "new-session"]

    def start(
        argv: list[str], session: str, release: Callable[[], None],
    ) -> subprocess.CompletedProcess[str]:
        assert argv[0] == r"C:\fixture\psmux.exe"
        calls.append("start")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(mux_creation, "run_session", start)
    if version is None or version < windows_mux_version.MINIMUM_VERSION:
        with pytest.raises(RuntimeError, match="require version 3.4.0"):
            mux_creation.run_or_join([], binary="psmux", session="wt-fixture", prepare=prepare)
        assert calls == []
    else:
        result = mux_creation.run_or_join([], binary="psmux", session="wt-fixture", prepare=prepare)
        assert result is not None and result.returncode == 0
        assert calls == ["prepare", "start"]


def _versioned_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    versions: dict[str, tuple[int, int, int, int] | None],
) -> dict[str, str]:
    paths: dict[str, str] = {}
    for name in versions:
        directory = tmp_path / name
        directory.mkdir(parents=True)
        (directory / "psmux.exe").write_bytes(b"fixture")
        paths[name] = str(directory / "psmux.exe")
    by_path = {
        str(Path(path).resolve()).lower(): versions[name] for name, path in paths.items()
    }
    monkeypatch.setattr(
        windows_mux_version, "file_version",
        lambda binary: by_path.get(str(Path(binary).resolve()).lower()),
    )
    return paths


def test_creation_selects_newest_side_by_side_copy_when_path_copy_is_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _versioned_tree(tmp_path, monkeypatch, {
        "path-bin": (3, 3, 8, 0),
        "side/3.4.0": (3, 4, 0, 0),
        "side/3.5.1": (3, 5, 1, 0),
        "side/3.2.0": (3, 2, 0, 0),
        "package": (3, 4, 2, 0),
    })
    monkeypatch.setattr(shutil, "which", lambda binary: paths["path-bin"])
    selected = windows_mux_version.require_creation_binary(
        "psmux", search_roots=[str(tmp_path / "side"), str(tmp_path / "package")],
    )
    assert Path(selected) == Path(paths["side/3.5.1"])


def test_creation_ignores_incomplete_side_by_side_install_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _versioned_tree(tmp_path, monkeypatch, {
        "path-bin": (3, 3, 8, 0),
        "side/3.4.0": (3, 4, 0, 0),
        "side/3.9.0.partial-abc": (3, 9, 0, 0),
        "side/.staging-abc": (3, 9, 0, 0),
    })
    monkeypatch.setattr(shutil, "which", lambda binary: paths["path-bin"])
    selected = windows_mux_version.require_creation_binary(
        "psmux", search_roots=[str(tmp_path / "side")],
    )
    assert Path(selected) == Path(paths["side/3.4.0"])


def test_creation_keeps_compatible_path_copy_before_side_by_side_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _versioned_tree(tmp_path, monkeypatch, {
        "path-bin": (3, 4, 0, 0), "side/3.6.0": (3, 6, 0, 0),
    })
    monkeypatch.setattr(shutil, "which", lambda binary: paths["path-bin"])

    def no_scan(roots: object) -> list[tuple[tuple[int, int, int, int], str]]:
        raise AssertionError("a compatible PATH copy must not scan other roots")

    monkeypatch.setattr(windows_mux_version, "compatible_candidates", no_scan)
    assert windows_mux_version.require_creation_binary(
        "psmux", search_roots=[str(tmp_path / "side")],
    ) == paths["path-bin"]


def test_creation_refusal_names_side_by_side_remedy_when_no_copy_is_compatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _versioned_tree(tmp_path, monkeypatch, {
        "path-bin": None, "side/3.3.8": (3, 3, 8, 0),
    })
    monkeypatch.setattr(shutil, "which", lambda binary: paths["path-bin"])
    with pytest.raises(RuntimeError, match="require version 3.4.0") as raised:
        windows_mux_version.require_creation_binary(
            "psmux", search_roots=[str(tmp_path / "side")],
        )
    message = str(raised.value)
    assert "existing sessions were not changed" in message
    assert "side by side" in message and "version unknown" in message


def test_creation_puts_selected_side_by_side_directory_first_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    selected = tmp_path / "side" / "3.4.0"
    old = tmp_path / "old"
    monkeypatch.setenv("PATH", os.pathsep.join([str(old), str(selected)]))
    env = mux_creation._creation_env([str(selected / "psmux.exe"), "new-session"])
    assert env is not None
    key = next(name for name in env if name.upper() == "PATH")
    entries = env[key].split(os.pathsep)
    assert entries[0] == str(selected)
    assert entries.count(str(selected)) == 1 and str(old) in entries


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows file-version resource")
def test_version_read_uses_native_metadata_without_a_provider_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("version subprocess"))
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("version subprocess"))
    version = windows_mux_version.file_version(sys.executable)
    assert version is not None and version[:2] == sys.version_info[:2]
    invalid = tmp_path / "not-a-provider.exe"
    invalid.write_bytes(b"not a PE file")
    assert windows_mux_version.file_version(str(invalid)) is None
