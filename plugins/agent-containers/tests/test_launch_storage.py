"""Actual Linux tmpfs/disk/symlink launch-storage refusal, without Docker."""

from __future__ import annotations

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


@pytest.fixture
def ram_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="launch-storage-test-", dir="/dev/shm") as root:
        monkeypatch.setattr(launch_storage, "RAM_ROOT", root)
        monkeypatch.setattr(launch_storage, "EXEC_ROOT", str(tmp_path))
        yield Path(root)


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
    filesystem = subprocess.run(
        ["stat", "-f", "-c", "%T", str(tmp_path)],
        capture_output=True, text=True, check=True, timeout=5,
    ).stdout.strip()
    if filesystem == "tmpfs":
        pytest.skip("disk refusal needs a non-tmpfs test directory")
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


@pytest.mark.parametrize("symlink", [False, True])
def test_disk_or_symlink_cleanup_owner_refuses_before_source(
    tmp_path: Path, ram_root: Path, local_transport: list,
    monkeypatch: pytest.MonkeyPatch, symlink: bool,
) -> None:
    directory = tmp_path
    if symlink:
        directory = tmp_path / "linked"
        directory.symlink_to(ram_root, target_is_directory=True)
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
    executable.mkdir(mode=0o700, parents=True)
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
