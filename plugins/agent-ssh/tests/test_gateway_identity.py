from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import Mock

from agent_procutil import no_window_kwargs
import pytest

from agent_ssh import gateway_authorization as authorization
from agent_ssh import gateway_identity as identity
from agent_ssh import gateway_sshd
from test_gateway_native import key

pytestmark = pytest.mark.guard


@pytest.fixture
def keygen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setattr(gateway_sshd, "_account", lambda: "owner")
    monkeypatch.setattr(gateway_sshd, "_executable", Mock())

    def run(executable: Path, arguments: list[str]) -> bytes:
        path = Path(arguments[arguments.index("-f") + 1])
        selected = key(1 if path.name.startswith("client") else 2)
        if "-y" not in arguments:
            with path.open("xb") as stream:
                if sys.platform != "win32":
                    os.fchmod(stream.fileno(), 0o600)
                stream.write(b"fixture private identity, not a real key")
            path.with_suffix(".pub").write_text(selected + "\n", encoding="ascii")
        return (selected + "\n").encode("ascii")

    mock = Mock(side_effect=run)
    monkeypatch.setattr(identity, "_keygen", mock)
    return mock


def prepare(tmp_path: Path) -> identity.PreparedIdentity:
    return identity.prepare(tmp_path / "identity", tmp_path / "ssh-keygen", machine="receiver", user="owner")


def test_fresh_identity_and_resume_keep_both_keys_and_only_export_public_descriptor(
    tmp_path: Path, keygen: Mock, capsys: pytest.CaptureFixture[str],
) -> None:
    first = prepare(tmp_path)
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in first.client_key.parent.iterdir()}
    keygen.reset_mock()
    assert prepare(tmp_path) == first
    assert all("-y" in call.args[1] for call in keygen.call_args_list)
    assert {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in first.client_key.parent.iterdir()} == before
    assert first.descriptor == {
        "machine": "receiver", "user": "owner", "public_key": key(1), "host_key": key(2),
    }
    assert identity.main([
        "--directory", str(first.client_key.parent), "--ssh-keygen", str(tmp_path / "ssh-keygen"),
        "--machine", "receiver", "--user", "owner",
    ]) == 0
    public = capsys.readouterr().out
    assert "private" not in public and str(tmp_path) not in public
    assert "client_ed25519" not in public


def test_missing_public_sidecar_is_derived_without_rotating_private_keys(tmp_path: Path, keygen: Mock) -> None:
    first = prepare(tmp_path)
    before = first.client_key.read_bytes(), first.host_key.read_bytes()
    first.client_public_key.unlink()
    keygen.reset_mock()
    assert prepare(tmp_path) == first
    assert all("-y" in call.args[1] for call in keygen.call_args_list)
    assert (first.client_key.read_bytes(), first.host_key.read_bytes()) == before


@pytest.mark.parametrize("change", ["different-machine", "public-mismatch", "missing-private", "unrelated"])
def test_resume_refuses_foreign_or_inconsistent_state_without_replacing_keys(
    tmp_path: Path, keygen: Mock, change: str,
) -> None:
    first = prepare(tmp_path)
    root = first.client_key.parent
    if change == "different-machine":
        owner = root / "identity.json"
        owner.write_text(owner.read_text().replace("receiver", "another"), encoding="ascii")
    elif change == "public-mismatch":
        first.client_public_key.write_text(key(3) + "\n", encoding="ascii")
    elif change == "missing-private":
        first.client_key.unlink()
    else:
        (root / "unrelated").write_bytes(b"preserve")
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    keygen.reset_mock()
    with pytest.raises(ValueError):
        prepare(tmp_path)
    assert all("-y" in call.args[1] for call in keygen.call_args_list)
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


def test_wrong_login_refuses_before_filesystem_or_key_generation(tmp_path: Path, keygen: Mock) -> None:
    with pytest.raises(PermissionError, match="current login"):
        identity.prepare(tmp_path / "identity", tmp_path / "ssh-keygen", machine="receiver", user="another")
    assert not (tmp_path / "identity").exists()
    keygen.assert_not_called()


def test_partial_private_key_failure_is_preserved_instead_of_regenerated(tmp_path: Path, keygen: Mock) -> None:
    first = prepare(tmp_path)
    keygen.side_effect = ValueError("unreadable private key")
    before = first.client_key.read_bytes(), first.host_key.read_bytes()
    with pytest.raises(ValueError, match="unreadable"):
        prepare(tmp_path)
    assert (first.client_key.read_bytes(), first.host_key.read_bytes()) == before


def test_hardlinked_private_key_refuses_before_reading_it(tmp_path: Path, keygen: Mock) -> None:
    first = prepare(tmp_path)
    sibling = tmp_path / "private-link"
    sibling.hardlink_to(first.client_key)
    keygen.reset_mock()
    with pytest.raises(PermissionError, match="ordinary and unlinked"):
        prepare(tmp_path)
    keygen.assert_not_called()
    assert first.client_key.read_bytes() == sibling.read_bytes()


def test_missing_owner_never_adopts_existing_keys(tmp_path: Path, keygen: Mock) -> None:
    first = prepare(tmp_path)
    (first.client_key.parent / "identity.json").unlink()
    keygen.reset_mock()
    with pytest.raises(ValueError, match="no owner binding"):
        prepare(tmp_path)
    keygen.assert_not_called()


@pytest.mark.parametrize("role", ["directory", "private", "public"])
def test_linked_identity_paths_preserve_link_and_target_without_keygen(
    tmp_path: Path, keygen: Mock, role: str,
) -> None:
    first = prepare(tmp_path)
    link = {
        "directory": first.client_key.parent,
        "private": first.client_key,
        "public": first.client_public_key,
    }[role]
    target = tmp_path / "retained"
    link.rename(target)
    try:
        link.symlink_to(target, target_is_directory=role == "directory")
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("File symlink creation requires unavailable Windows privilege")
        raise
    before = (
        {path.name: path.read_bytes() for path in target.iterdir()}
        if target.is_dir() else target.read_bytes()
    )
    original = link.readlink()
    keygen.reset_mock()
    with pytest.raises(PermissionError):
        prepare(tmp_path)
    keygen.assert_not_called()
    assert link.readlink() == original
    after = (
        {path.name: path.read_bytes() for path in target.iterdir()}
        if target.is_dir() else target.read_bytes()
    )
    assert after == before


def test_keygen_failure_is_bounded_and_does_not_report_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = Mock(return_value=subprocess.CompletedProcess([], 1, b"", b"sensitive diagnostics"))
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError, match="could not prepare") as error:
        identity._keygen(tmp_path / "ssh-keygen", ["-y"])
    assert "sensitive" not in str(error.value)
    assert run.call_args.kwargs["timeout"] == 15
    assert run.call_args.kwargs["stdin"] == subprocess.DEVNULL


def test_native_key_generation_and_same_identity_resume(tmp_path: Path) -> None:
    executable = os.environ.get("CE_TEST_SSH_KEYGEN") or shutil.which("ssh-keygen")
    if not executable:
        pytest.skip("Native ssh-keygen is unavailable")
    if sys.platform != "win32" and os.geteuid() == 0:
        pytest.skip("Native Gateway identity requires a non-root login")
    root = tmp_path / "native"
    user = gateway_sshd._account()
    first = identity.prepare(root, Path(executable), machine="receiver", user=user)
    before = first.client_key.read_bytes(), first.host_key.read_bytes()
    assert first.descriptor["public_key"] != first.descriptor["host_key"]
    first.host_public_key.unlink()
    second = identity.prepare(root, Path(executable), machine="receiver", user=user)
    assert second == first
    assert (first.client_key.read_bytes(), first.host_key.read_bytes()) == before
    for path in root.iterdir():
        authorization._private(path, directory=False)
    result = subprocess.run(
        [
            sys.executable, "-m", "agent_ssh.gateway_identity",
            "--directory", str(root), "--ssh-keygen", executable,
            "--machine", "receiver", "--user", user,
        ],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=False,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert json.loads(result.stdout) == first.descriptor
    assert str(root).encode() not in result.stdout
    assert (first.client_key.read_bytes(), first.host_key.read_bytes()) == before
