from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from machine_transport import detect_platform


@pytest.fixture
def linux_root(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    facts: dict[str, object] = {
        "cgroup": "0::/", "filesystem": "ext4", "mountpoint": "/",
        "interop": False, "kernel": "6.6-microsoft-standard-WSL2",
    }
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        "os.uname", lambda: SimpleNamespace(release=facts["kernel"]), raising=False,
    )
    monkeypatch.setattr(
        Path, "is_file",
        lambda path: str(path).replace("\\", "/").endswith("/WSLInterop") and facts["interop"],
    )

    def read(path: Path, **_kwargs: object) -> str:
        name = path.name
        if name == "cgroup":
            if facts["cgroup"] is None:
                raise OSError("fixture cgroup unavailable")
            return str(facts["cgroup"])
        if name == "mountinfo":
            if facts["filesystem"] is None:
                raise OSError("fixture mountinfo unavailable")
            return f"29 23 0:25 / {facts['mountpoint']} rw - {facts['filesystem']} root rw"
        raise AssertionError(f"unexpected proc read: {path}")

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: io.StringIO("Linux Microsoft"))
    monkeypatch.setenv("WSL_DISTRO_NAME", "inherited-name")
    monkeypatch.setenv("WSL_INTEROP", "inherited-socket")
    return facts


@pytest.mark.parametrize("filesystem", [
    "overlay", "fuse.overlayfs", "fuse-overlayfs", "fuse.fuse-overlayfs", "fuse.fuse.overlayfs",
])
def test_private_container_root_does_not_inherit_shared_wsl_kernel(
    linux_root: dict[str, object], filesystem: str,
) -> None:
    linux_root["filesystem"] = filesystem
    assert detect_platform() == "linux"


@pytest.mark.parametrize("cgroup", ["0::/", "0::/\n1:name=systemd:/", "", None])
def test_ambiguous_wsl_root_requires_actual_interop_not_inherited_environment(
    linux_root: dict[str, object], cgroup: str | None,
) -> None:
    linux_root["cgroup"] = cgroup
    with pytest.raises(ValueError, match="execution platform is unconfirmed"):
        detect_platform()
    linux_root["interop"] = True
    assert detect_platform() == "wsl"


def test_nonroot_cgroup_retains_headless_wsl_without_interop(
    linux_root: dict[str, object],
) -> None:
    linux_root.update(cgroup="0::/init.scope", filesystem=None)
    assert detect_platform() == "wsl"


def test_nested_overlay_is_not_a_container_execution_root(linux_root: dict[str, object]) -> None:
    linux_root.update(filesystem="overlay", mountpoint="/nested")
    with pytest.raises(ValueError, match="unconfirmed"):
        detect_platform()
    linux_root["interop"] = True
    assert detect_platform() == "wsl"


def test_proc_version_wsl_fallback_still_classifies_the_actual_root(
    linux_root: dict[str, object],
) -> None:
    linux_root.update(kernel="generic-kernel", filesystem="overlay")
    assert detect_platform() == "linux"


def test_missing_mount_information_cannot_confirm_private_wsl_root(
    linux_root: dict[str, object],
) -> None:
    linux_root["filesystem"] = None
    with pytest.raises(ValueError, match="unconfirmed"):
        detect_platform()
