"""Actual Linux tmpfs/disk/symlink launch-storage refusal, without Docker."""

from __future__ import annotations

import errno
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_containers import copilot_process_auth as auth
from agent_containers import launch_storage, ssh_transport
from agent_containers.config import ContainersConfig, FleetConfig

pytestmark = [
    pytest.mark.guard,
    pytest.mark.skipif(sys.platform != "linux", reason="actual Linux tmpfs semantics"),
]


def _ram_temporary_directory() -> tempfile.TemporaryDirectory[str]:
    try:
        return tempfile.TemporaryDirectory(prefix="launch-storage-test-", dir="/dev/shm")
    except OSError as exc:
        if exc.errno not in {
            errno.ENOENT, errno.EACCES, errno.EPERM,
            errno.EROFS, errno.ENOSPC,
        }:
            raise
        raise pytest.skip.Exception(
            f"RAM-storage regression requires writable /dev/shm (errno {exc.errno})"
        ) from exc


def _require_non_tmpfs(directory: Path) -> None:
    filesystem = subprocess.run(
        ["stat", "-f", "-c", "%T", str(directory)],
        capture_output=True, text=True, check=True, timeout=5,
    ).stdout.strip()
    if filesystem == "tmpfs":
        pytest.skip("disk refusal needs a non-tmpfs test directory")


@pytest.fixture
def ram_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    with _ram_temporary_directory() as root:
        monkeypatch.setattr(launch_storage, "RAM_ROOT", root)
        monkeypatch.setattr(launch_storage, "EXEC_ROOT", str(tmp_path))
        yield Path(root)


@pytest.mark.parametrize("number", [
    errno.ENOENT, errno.EACCES, errno.EPERM,
    errno.EROFS, errno.ENOSPC,
])
def test_unavailable_ram_prerequisite_is_an_explicit_skip(
    monkeypatch: pytest.MonkeyPatch, number: int,
) -> None:
    def unavailable(**kwargs: object) -> tempfile.TemporaryDirectory[str]:
        raise OSError(number, "synthetic unavailable prerequisite")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", unavailable)
    with pytest.raises(pytest.skip.Exception, match="requires writable /dev/shm"):
        _ram_temporary_directory()


def test_unexpected_ram_creation_error_is_not_silently_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(**kwargs: object) -> tempfile.TemporaryDirectory[str]:
        raise OSError(errno.EIO, "synthetic unexpected failure")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", unexpected)
    with pytest.raises(OSError, match="synthetic unexpected failure"):
        _ram_temporary_directory()


@pytest.mark.parametrize("filesystem", ["tmpfs", "overlayfs"])
def test_disk_prerequisite_uses_actual_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filesystem: str,
) -> None:
    def stat(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[-1] == str(tmp_path)
        assert kwargs["check"] is True
        return subprocess.CompletedProcess(args, 0, filesystem + "\n", "")

    monkeypatch.setattr(subprocess, "run", stat)
    if filesystem == "tmpfs":
        with pytest.raises(pytest.skip.Exception, match="non-tmpfs test directory"):
            _require_non_tmpfs(tmp_path)
    else:
        _require_non_tmpfs(tmp_path)


def test_unexpected_disk_probe_error_is_not_silently_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_stat(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, ["stat"], stderr="synthetic probe failure")

    monkeypatch.setattr(subprocess, "run", failed_stat)
    with pytest.raises(subprocess.CalledProcessError):
        _require_non_tmpfs(tmp_path)


@pytest.fixture
def local_transport(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], str | None]]:
    calls: list[tuple[list[str], str | None]] = []

    def run(
        args: list[str], *, input_text: str | None = None, timeout: float = 30,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, input_text))
        command = args[args.index("fixture") + 1:]
        return subprocess.run(
            command, input=input_text, text=True,
            capture_output=True, timeout=min(timeout, 5), check=False,
        )

    monkeypatch.setattr(ssh_transport, "_run", run)
    return calls


def test_disk_home_never_receives_launch_inputs(
    tmp_path: Path, ram_root: Path, local_transport: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    path = ssh_transport.write_remote_env("fixture", "user", {"SECRET": "synthetic-only"})
    assert path is not None
    assert Path(path).is_relative_to(ram_root)
    assert Path(path).read_text() == "export SECRET=synthetic-only\n"
    assert Path(path).stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.iterdir())
    assert all("synthetic-only" not in str(argv) for argv, _ in local_transport)
    assert ssh_transport.cleanup_remote_env("fixture", "user", path)
    assert not Path(path).exists()


def test_disk_storage_refuses_before_creating_or_writing(
    tmp_path: Path, local_transport: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_non_tmpfs(tmp_path)
    monkeypatch.setattr(launch_storage, "RAM_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="private, non-symlink tmpfs"):
        ssh_transport.write_remote_env("fixture", "user", {"SECRET": "synthetic-only"})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("location", ["base", "private", "launch"])
def test_symlink_storage_refuses_without_touching_target(
    tmp_path: Path, ram_root: Path, local_transport: list,
    monkeypatch: pytest.MonkeyPatch, location: str,
) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    sentinel = target / "sentinel"
    sentinel.write_text("unchanged")
    if location == "base":
        link = tmp_path / "linked-ram"
        link.symlink_to(ram_root, target_is_directory=True)
        monkeypatch.setattr(launch_storage, "RAM_ROOT", str(link))
    elif location == "private":
        (ram_root / "agent-containers-user").symlink_to(target, target_is_directory=True)
    else:
        private = ram_root / "agent-containers-user"
        private.mkdir(mode=0o700)
        (private / "launch").symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match="private, non-symlink tmpfs"):
        ssh_transport.write_remote_env("fixture", "user", {"SECRET": "synthetic-only"})
    assert list(target.iterdir()) == [sentinel]
    assert sentinel.read_text() == "unchanged"


def test_existing_public_directory_is_not_silently_adopted(
    ram_root: Path, local_transport: list,
) -> None:
    private = ram_root / "agent-containers-user"
    private.mkdir(mode=0o755)
    with pytest.raises(RuntimeError, match="private, non-symlink tmpfs"):
        ssh_transport.write_remote_env("fixture", "user", {"SECRET": "synthetic-only"})
    assert private.stat().st_mode & 0o777 == 0o755
    assert not list(private.iterdir())


def test_existing_final_symlink_is_never_followed(
    tmp_path: Path, ram_root: Path, local_transport: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = ssh_transport.write_remote_env("fixture", "user", {"SETTING": "ordinary"})
    assert first is not None
    outside = tmp_path / "outside"
    outside.write_text("unchanged")
    collision = Path(first).with_name("b" * 32 + ".env")
    collision.symlink_to(outside)
    monkeypatch.setattr(ssh_transport.uuid, "uuid4", lambda: SimpleNamespace(hex="b" * 32))
    with pytest.raises(RuntimeError, match="Failed to stage"):
        ssh_transport.write_remote_env("fixture", "user", {"SECRET": "synthetic-only"})
    assert outside.read_text() == "unchanged"


def test_nonregular_final_collision_is_never_silently_consumed(
    ram_root: Path, local_transport: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A symlink to a nonregular target (e.g. /dev/null) bypasses noclobber.

    POSIX noclobber only refuses to overwrite an *existing regular file*;
    special files and symlinks are explicitly excluded from that protection.
    Staging must refuse such a collision outright rather than silently
    consuming the credential payload while still reporting success.
    """
    first = ssh_transport.write_remote_env("fixture", "user", {"SETTING": "ordinary"})
    assert first is not None
    collision = Path(first).with_name("b" * 32 + ".env")
    collision.symlink_to("/dev/null")
    monkeypatch.setattr(ssh_transport.uuid, "uuid4", lambda: SimpleNamespace(hex="b" * 32))
    with pytest.raises(RuntimeError, match="Failed to stage"):
        ssh_transport.write_remote_env("fixture", "user", {"SECRET": "synthetic-only"})
    assert collision.resolve(strict=False) == Path("/dev/null")


@pytest.mark.parametrize("symlink", [False, True])
def test_disk_or_symlink_cleanup_owner_refuses_before_source(
    tmp_path: Path, ram_root: Path, local_transport: list,
    monkeypatch: pytest.MonkeyPatch, symlink: bool,
) -> None:
    directory = tmp_path
    if symlink:
        directory = tmp_path / "linked"
        directory.symlink_to(ram_root, target_is_directory=True)
    else:
        _require_non_tmpfs(directory)
    monkeypatch.setattr(
        auth, "resolve_copilot_cli_token_or_raise",
        lambda *args: pytest.fail("must refuse before acquiring the credential"),
    )
    with pytest.raises(RuntimeError, match="not on private RAM-backed storage"):
        auth.prepare_launch(
            ContainersConfig(copilot_cli_account="example"),
            FleetConfig(), "fixture", "user",
            cleanup_owner=str(directory / ("a" * 32 + ".env")),
        )
    assert len(local_transport) == 1
    assert local_transport[0][1] is None


def test_guard_rejects_non_normalized_paths() -> None:
    with pytest.raises(RuntimeError, match="normalized absolute"):
        launch_storage.directory_guard("/tmp/../dev/shm")


def test_guard_rejects_double_leading_slash_without_looping() -> None:
    """POSIX normpath() preserves an *exact* double leading slash, under
    which dirname("//") == "//" forever; reject it before the ancestor walk
    instead of growing `ancestors` without bound.
    """
    with pytest.raises(RuntimeError, match="normalized absolute"):
        launch_storage.directory_guard("//home/user/.agent-containers/launch")


def test_session_host_rejects_double_leading_slash_cleanup_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_containers import __main__ as cli

    monkeypatch.setattr(
        cli, "_trusted_session_host_context",
        lambda *args: (None, None, "user", "/workspace"),
    )
    monkeypatch.setattr(cli, "cleanup_remote_env", lambda *args: pytest.fail("no deletion"))
    with pytest.raises(RuntimeError, match="normalized absolute"):
        cli._cmd_session_host_cleanup(SimpleNamespace(
            name="fixture", expected_instance=None,
            remote_env="//home/user/.agent-containers/launch/" + "a" * 32 + ".env",
        ))


def test_session_host_original_ram_handle_removes_both_roots(
    ram_root: Path, local_transport: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_containers import __main__ as cli

    owner = ssh_transport.write_remote_env("fixture", "user", {"SETTING": "ordinary"})
    assert owner is not None
    auth_directory = Path(owner + ".d")
    auth_directory.mkdir(mode=0o700)
    (auth_directory / "auth.env").write_text("synthetic-only")
    executable = Path(launch_storage.executable_directory(owner))
    for directory in (executable.parent.parent, executable.parent, executable):
        directory.mkdir(mode=0o700)
    (executable / "copilot").write_text("nonsecret wrapper")
    monkeypatch.setattr(
        cli, "_trusted_session_host_context",
        lambda *args: (None, None, "user", "/workspace"),
    )
    assert cli._cmd_session_host_cleanup(SimpleNamespace(
        name="fixture", expected_instance=None, remote_env=owner,
    )) == 0
    assert not Path(owner).exists()
    assert not auth_directory.exists()
    assert not executable.exists()


@pytest.mark.parametrize("parent", [
    "/dev/shm/agent-containers-other/launch",
    "/dev/shm/agent-containers-user/launch/..",
    "/tmp/launch",
])
def test_session_host_rejects_foreign_or_unsafe_ram_handle(
    parent: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_containers import __main__ as cli

    monkeypatch.setattr(
        cli, "_trusted_session_host_context",
        lambda *args: (None, None, "user", "/workspace"),
    )
    monkeypatch.setattr(cli, "cleanup_remote_env", lambda *args: pytest.fail("no deletion"))
    with pytest.raises(RuntimeError, match="unsafe Session Host env cleanup"):
        cli._cmd_session_host_cleanup(SimpleNamespace(
            name="fixture", expected_instance=None,
            remote_env=parent + "/" + "a" * 32 + ".env",
        ))


@pytest.mark.parametrize("unsafe", ["disk", "symlink", "permissions"])
def test_session_host_cleanup_checks_actual_parent_before_any_deletion(
    tmp_path: Path, ram_root: Path, local_transport: list,
    monkeypatch: pytest.MonkeyPatch, unsafe: str,
) -> None:
    from agent_containers import __main__ as cli

    if unsafe == "disk":
        _require_non_tmpfs(tmp_path)
        disk = tmp_path / "disk"
        disk.mkdir()
        monkeypatch.setattr(launch_storage, "RAM_ROOT", str(disk))
    parent = Path(launch_storage.launch_directory("user"))
    parent.mkdir(mode=0o700, parents=True)
    if unsafe == "symlink":
        outside = ram_root / "outside"
        outside.mkdir(mode=0o700)
        parent.rmdir()
        parent.symlink_to(outside, target_is_directory=True)
    elif unsafe == "permissions":
        parent.chmod(0o755)
    owner = parent / ("a" * 32 + ".env")
    owner.write_text("must remain")
    monkeypatch.setattr(
        cli, "_trusted_session_host_context",
        lambda *args: (None, None, "user", "/workspace"),
    )
    monkeypatch.setattr(cli, "cleanup_remote_env", lambda *args: pytest.fail("no deletion"))
    with pytest.raises(RuntimeError, match="unsafe Session Host cleanup directory"):
        cli._cmd_session_host_cleanup(SimpleNamespace(
            name="fixture", expected_instance=None, remote_env=str(owner),
        ))
    assert owner.read_text() == "must remain"
    assert len(local_transport) == 1
    assert local_transport[0][1] is None


def test_auth_promotion_refuses_a_destination_created_after_mkdir(
    ram_root: Path, local_transport: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = ssh_transport.write_remote_env("fixture", "user", {"SETTING": "ordinary"})
    assert owner is not None
    destination = Path(owner + ".d/auth.env")
    original = ssh_transport._run
    observed: list[bool] = []

    def interleaved(
        args: list[str], *, input_text: str | None = None, timeout: float = 30,
    ) -> subprocess.CompletedProcess[str]:
        if "--secret-env-vars" in args[-1]:
            return subprocess.CompletedProcess(args, 0, "/usr/bin/copilot\n", "")
        if input_text and input_text.startswith("#!/bin/sh"):
            args = [*args[:-1], args[-1].replace(
                "ln -T --",
                f"printf '%s' stale > {shlex.quote(str(destination))}\nln -T --",
                1,
            )]
            result = original(args, input_text=input_text, timeout=timeout)
            observed.append(result.returncode != 0 and destination.read_text() == "stale")
            return result
        return original(args, input_text=input_text, timeout=timeout)

    monkeypatch.setattr(ssh_transport, "_run", interleaved)
    monkeypatch.setattr(auth, "resolve_copilot_cli_token_or_raise", lambda *args: "synthetic")
    with pytest.raises(RuntimeError, match="could not stage process-only Copilot launcher"):
        auth.prepare_launch(
            ContainersConfig(copilot_cli_account="example"), FleetConfig(),
            "fixture", "user", cleanup_owner=owner,
        )
    assert observed == [True]
    assert not Path(owner).exists()
    assert not list(ram_root.rglob("auth.env"))


@pytest.mark.parametrize("directory,expected", [
    ("/dev/shm/agent-containers-user/launch", "/tmp/agent-containers-user/launch"),
    ("/home/user/.agent-containers/launch", "/home/user/.agent-containers/launch"),
])
def test_executable_root_mapping_does_not_require_a_synthetic_owner(
    directory: str, expected: str,
) -> None:
    assert launch_storage.executable_root_for_launch_dir(directory) == expected
    assert launch_storage.executable_directory(directory + "/abc.env") == expected + "/abc.env.d"
    assert launch_storage.executable_directory(directory + "/abc.env.d/auth.env") == expected + "/abc.env.d"


@pytest.mark.parametrize("unsafe", ["symlink", "permissions"])
def test_unsafe_executable_root_blocks_reaping_staging_and_cleanup(
    tmp_path: Path, ram_root: Path, local_transport: list,
    monkeypatch: pytest.MonkeyPatch, unsafe: str,
) -> None:
    owner = ssh_transport.write_remote_env("fixture", "user", {"SETTING": "ordinary"})
    assert owner is not None
    executable = Path(launch_storage.executable_directory(owner))
    executable.parent.parent.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    if unsafe == "symlink":
        executable.parent.symlink_to(outside, target_is_directory=True)
    else:
        executable.parent.mkdir(mode=0o755)
    executable.mkdir(mode=0o700)
    sentinel = executable / "copilot"
    sentinel.write_text("must remain")
    assert ssh_transport.cleanup_remote_env("fixture", "user", owner) is False
    assert Path(owner).exists()
    assert sentinel.read_text() == "must remain"
    with pytest.raises(RuntimeError, match="Failed to stage"):
        ssh_transport.write_remote_env("fixture", "user", {"SECRET": "synthetic-only"})
    with pytest.raises(RuntimeError, match="Could not clear abandoned"):
        ssh_transport.cleanup_remote_envs("fixture", "user")
    assert list(ram_root.rglob("*.env")) == [Path(owner)]
    assert sentinel.read_text() == "must remain"


@pytest.mark.parametrize("scope", ["auth", "executable"])
def test_symlinked_per_launch_scope_refuses_cleanup_deletion(
    tmp_path: Path, ram_root: Path, local_transport: list,
    monkeypatch: pytest.MonkeyPatch, scope: str,
) -> None:
    """A symlinked `<uuid>.env.d` scope must not redirect `rm -f` elsewhere.

    The root-level guards alone are not enough: `cleanup_remote_env` deletes
    files *inside* the per-launch `.env.d` scopes, so each scope itself must
    be refused when it is a symlink, independent of the valid root it sits
    under.
    """
    owner = ssh_transport.write_remote_env("fixture", "user", {"SETTING": "ordinary"})
    assert owner is not None
    executable = Path(launch_storage.executable_directory(owner))
    executable.parent.mkdir(mode=0o700, parents=True)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    auth_directory = Path(owner + ".d")
    if scope == "auth":
        sentinel = outside / "auth.env"
        sentinel.write_text("must remain")
        auth_directory.symlink_to(outside, target_is_directory=True)
        executable.mkdir(mode=0o700)
    else:
        sentinel = outside / "copilot"
        sentinel.write_text("must remain")
        auth_directory.mkdir(mode=0o700)
        executable.symlink_to(outside, target_is_directory=True)
    assert ssh_transport.cleanup_remote_env("fixture", "user", owner) is False
    assert sentinel.read_text() == "must remain"
    assert Path(owner).exists()


def test_session_host_cleanup_is_idempotent_with_missing_ram_root(
    ram_root: Path, local_transport: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing RAM root (e.g. after a container restart) must not block
    removal of surviving `/tmp` executable-root artifacts.
    """
    from agent_containers import __main__ as cli

    owner = str(Path(launch_storage.launch_directory("user")) / ("a" * 32 + ".env"))
    executable = Path(launch_storage.executable_directory(owner))
    for directory in (executable.parent.parent, executable.parent, executable):
        directory.mkdir(mode=0o700)
    (executable / "copilot").write_text("nonsecret wrapper")
    assert not Path(launch_storage.launch_directory("user")).exists()
    monkeypatch.setattr(
        cli, "_trusted_session_host_context",
        lambda *args: (None, None, "user", "/workspace"),
    )
    assert cli._cmd_session_host_cleanup(SimpleNamespace(
        name="fixture", expected_instance=None, remote_env=owner,
    )) == 0
    assert not executable.exists()
