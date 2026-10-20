from __future__ import annotations

import ctypes
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest

LIBRARY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRARY))
import versioned_runtime_source as publication

NAMES = ("payload-dir", "stamped-version", "payload-origin")


def _case(tmp_path: Path) -> tuple[Path, Path, dict[str, bytes]]:
    root, payload = tmp_path / "runtime", tmp_path / "next-payload"
    root.mkdir()
    payload.mkdir()
    before = {name: f"previous {name}\n".encode() for name in NAMES}
    for name, content in before.items():
        (root / name).write_bytes(content)
    return root, payload, before


def _assert_previous(root: Path, before: dict[str, bytes]) -> None:
    assert {name: (root / name).read_bytes() for name in NAMES} == before
    assert not list(root.glob(".*.redirect-*"))


def _source_case(payload: Path) -> tuple[str, str]:
    (payload / "plugin.json").write_text(
        json.dumps({"name": "agent-bridge", "version": "1.2.3"}), encoding="utf-8",
    )
    digest = publication.assembly_hash(payload)
    identity = publication.SourceIdentity(
        "agent-bridge", "1.2.3", "a" * 40, "b" * 40, digest,
        publication.runtime_version("1.2.3", "a" * 40, digest),
    )
    (payload / publication.SOURCE_FILE).write_text(json.dumps(identity.document()), encoding="utf-8")
    return identity.runtime_version, json.dumps(publication.manifest_fields(identity), sort_keys=True)


@pytest.mark.parametrize("existing", [False, True])
def test_redirect_publication_and_optional_marker_preservation(tmp_path: Path, existing: bool) -> None:
    root, payload, _ = _case(tmp_path)
    if not existing:
        for name in NAMES:
            (root / name).unlink()
    publication.publish_runtime_redirects(root, payload, "2.0.0", "next origin")
    assert (root / "payload-dir").read_text() == str(payload.resolve()) + "\n"
    assert (root / "stamped-version").read_text() == "2.0.0\n"
    assert (root / "payload-origin").read_text() == "next origin\n"
    publication.publish_runtime_redirects(root, payload)
    assert (root / "stamped-version").read_text() == "2.0.0\n"
    assert (root / "payload-origin").read_text() == "next origin\n"
    assert not list(root.glob(".*.redirect-*"))


@pytest.mark.parametrize("member", NAMES)
def test_partial_stage_write_failure_never_changes_live_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, member: str,
) -> None:
    root, payload, before = _case(tmp_path)
    values = {
        "payload-dir": (str(payload.resolve()) + "\n").encode(),
        "stamped-version": b"2.0.0\n", "payload-origin": b"next origin\n",
    }
    stage = publication._stage_redirect

    def fail_write(root: Path, name: str, content: bytes, mode: int,
                   owned: dict[Path, tuple[int, int]]) -> Path:
        temporary = stage(root, name, content, mode, owned)
        if name == member and content == values[member]:
            temporary.write_bytes(b"partial")
            raise OSError(errno.EIO, "injected partial staged write")
        return temporary

    monkeypatch.setattr(publication, "_stage_redirect", fail_write)
    with pytest.raises(OSError, match="partial staged write"):
        publication.publish_runtime_redirects(root, payload, "2.0.0", "next origin")
    _assert_previous(root, before)


@pytest.mark.parametrize("boundary", ["lease", "staging", "first-replace", "last-replace"])
def test_full_source_identity_is_pinned_inside_redirect_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    root, payload, before = _case(tmp_path)
    version, selected = _source_case(payload)

    def mutate() -> None:
        path = payload / publication.SOURCE_FILE
        receipt = json.loads(path.read_text())
        receipt["source_tree"] = "c" * 40
        path.write_text(json.dumps(receipt), encoding="utf-8")

    runtime = publication._runtime()
    lease = runtime._kernel_lease
    stage = publication._stage_redirect
    replace = os.replace

    @contextmanager
    def changed_lease(root: Path, key: str, **kwargs: object) -> Iterator[None]:
        with lease(root, key, **kwargs):
            if boundary == "lease":
                mutate()
            yield

    def changed_stage(root: Path, name: str, content: bytes, mode: int,
                      owned: dict[Path, tuple[int, int]]) -> Path:
        temporary = stage(root, name, content, mode, owned)
        if boundary == "staging" and content == (version + "\n").encode():
            mutate()
        return temporary

    def changed_replace(source: Path, destination: Path) -> None:
        replace(source, destination)
        if ((boundary == "first-replace" and Path(destination).name == "payload-dir")
                or (boundary == "last-replace" and Path(destination).name == "stamped-version")):
            mutate()

    monkeypatch.setattr(runtime, "_kernel_lease", changed_lease)
    monkeypatch.setattr(publication, "_stage_redirect", changed_stage)
    monkeypatch.setattr(publication.os, "replace", changed_replace)
    with pytest.raises(publication.SourceIdentityError, match="changed after runtime selection"):
        publication.publish_runtime_redirects(root, payload, version, expected_source_identity=selected)
    _assert_previous(root, before)


def test_source_redirects_refuse_an_unpinned_receipt_and_accept_the_exact_pin(tmp_path: Path) -> None:
    root, payload, before = _case(tmp_path)
    version, selected = _source_case(payload)
    with pytest.raises(publication.SourceIdentityError, match="selected full source identity"):
        publication.publish_runtime_redirects(root, payload, version)
    _assert_previous(root, before)
    published = publication.publish_runtime_redirects(root, payload, version, expected_source_identity=selected)
    assert published == {"payload-dir": str(payload.resolve()), "stamped-version": version}
    assert (root / "stamped-version").read_text().strip() == version


@pytest.mark.parametrize("member", NAMES)
@pytest.mark.parametrize("reported_after_move", [False, True])
def test_replace_failure_restores_every_previous_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, member: str, reported_after_move: bool,
) -> None:
    root, payload, before = _case(tmp_path)
    replace = os.replace
    injected = False

    def fail_replace(source: Path, destination: Path) -> None:
        nonlocal injected
        if Path(destination) == root / member and not injected:
            injected = True
            if reported_after_move:
                replace(source, destination)
            raise OSError(errno.EIO, "injected publication failure")
        replace(source, destination)

    monkeypatch.setattr(publication.os, "replace", fail_replace)
    with pytest.raises(OSError, match="publication failure"):
        publication.publish_runtime_redirects(root, payload, "2.0.0", "next origin")
    assert injected
    _assert_previous(root, before)


@pytest.mark.parametrize("member", NAMES)
def test_directory_collision_is_rejected_before_any_publication(tmp_path: Path, member: str) -> None:
    root, payload, before = _case(tmp_path)
    (root / member).unlink()
    (root / member).mkdir()
    (root / member / "sentinel").write_bytes(b"retained")
    with pytest.raises(publication.RedirectPublicationError, match="ordinary file"):
        publication.publish_runtime_redirects(root, payload, "2.0.0", "next origin")
    assert (root / member / "sentinel").read_bytes() == b"retained"
    assert all((root / name).read_bytes() == content for name, content in before.items() if name != member)
    assert not list(root.glob(".*.redirect-*"))


def test_failed_rollback_retains_old_bytes_for_explicit_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, payload, before = _case(tmp_path)
    replace = os.replace
    first_published = False

    def fail_replace(source: Path, destination: Path) -> None:
        nonlocal first_published
        if Path(destination) == root / "stamped-version":
            raise OSError(errno.EIO, "second marker refused")
        if Path(destination) == root / "payload-dir":
            if first_published:
                raise OSError(errno.EIO, "rollback refused")
            first_published = True
        replace(source, destination)

    monkeypatch.setattr(publication.os, "replace", fail_replace)
    with pytest.raises(publication.RedirectPublicationError, match="recovery files retained"):
        publication.publish_runtime_redirects(root, payload, "2.0.0", "next origin")
    assert first_published
    assert (root / "stamped-version").read_bytes() == before["stamped-version"]
    retained = [path.read_bytes() for path in root.glob(".*.redirect-*")]
    assert all(content in retained for content in before.values())


def test_concurrent_redirect_change_is_not_overwritten_during_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, payload, before = _case(tmp_path)
    replace = os.replace

    def replace_then_peer_write(source: Path, destination: Path) -> None:
        replace(source, destination)
        if Path(destination) == root / "payload-dir":
            (root / "payload-dir").unlink()
            (root / "payload-dir").write_bytes(b"peer decision\n")
            raise OSError(errno.EIO, "publication interrupted by peer")

    monkeypatch.setattr(publication.os, "replace", replace_then_peer_write)
    with pytest.raises(publication.RedirectPublicationError, match="recovery files retained"):
        publication.publish_runtime_redirects(root, payload, "2.0.0", "next origin")
    assert (root / "payload-dir").read_bytes() == b"peer decision\n"
    assert (root / "stamped-version").read_bytes() == before["stamped-version"]
    assert before["payload-dir"] in [path.read_bytes() for path in root.glob(".*.redirect-*")]


def test_tampered_staging_is_never_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, payload, before = _case(tmp_path)
    stage = publication._stage_redirect

    def changed_stage(root: Path, name: str, content: bytes, mode: int,
                      owned: dict[Path, tuple[int, int]]) -> Path:
        temporary = stage(root, name, content, mode, owned)
        if name == "payload-dir" and content != before[name]:
            temporary.write_bytes(b"foreign staged content\n")
        return temporary

    monkeypatch.setattr(publication, "_stage_redirect", changed_stage)
    with pytest.raises(publication.RedirectPublicationError, match="recovery files retained"):
        publication.publish_runtime_redirects(root, payload, "2.0.0", "next origin")
    assert {name: (root / name).read_bytes() for name in NAMES} == before
    assert before["payload-dir"] in [path.read_bytes() for path in root.glob(".*.redirect-*")]


def test_tampered_recovery_file_is_not_installed_as_a_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, payload, before = _case(tmp_path)
    replace = os.replace

    def fail_with_changed_backup(source: Path, destination: Path) -> None:
        if Path(destination) == root / "stamped-version":
            backups = list(root.glob(".payload-dir.redirect-*"))
            assert len(backups) == 1
            backups[0].write_bytes(b"foreign recovery content\n")
            raise OSError(errno.EIO, "second marker refused")
        replace(source, destination)

    monkeypatch.setattr(publication.os, "replace", fail_with_changed_backup)
    with pytest.raises(publication.RedirectPublicationError, match="recovery files retained"):
        publication.publish_runtime_redirects(root, payload, "2.0.0", "next origin")
    assert (root / "payload-dir").read_text() == str(payload.resolve()) + "\n"
    assert (root / "stamped-version").read_bytes() == before["stamped-version"]
    assert b"foreign recovery content\n" in [path.read_bytes() for path in root.glob(".*.redirect-*")]


@pytest.mark.skipif(os.name != "nt", reason="real Windows delete-sharing boundary")
def test_real_locked_second_marker_preserves_first_marker(tmp_path: Path) -> None:
    from ctypes import wintypes

    root, payload, before = _case(tmp_path)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(root / "stamped-version"), 0x80000000, 1, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    try:
        with pytest.raises(OSError) as error:
            publication.publish_runtime_redirects(root, payload, "2.0.0", "next origin")
        assert error.value.winerror in {5, 32}
        assert Path(error.value.filename2) == root / "stamped-version"
        _assert_previous(root, before)
    finally:
        assert kernel.CloseHandle(handle)
    _assert_previous(root, before)
    publication.publish_runtime_redirects(root, payload, "2.0.0", "next origin")
    assert (root / "stamped-version").read_text() == "2.0.0\n"


def test_real_cli_has_quiet_stdout_and_publishes_the_complete_pair(tmp_path: Path) -> None:
    root, payload, _ = _case(tmp_path)
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(LIBRARY / "versioned_runtime.py"),
         "--root", str(root), "publish-redirects", "--payload-dir", str(payload),
         "--version", "2.0.0", "--payload-origin", "next origin"],
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert (root / "payload-dir").read_text() == str(payload.resolve()) + "\n"
    assert (root / "stamped-version").read_text() == "2.0.0\n"


def test_cli_json_names_only_the_requested_published_markers(tmp_path: Path) -> None:
    root, payload, _ = _case(tmp_path)
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(LIBRARY / "versioned_runtime.py"),
         "--root", str(root), "--json", "publish-redirects", "--payload-dir", str(payload)],
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"published": {"payload-dir": str(payload.resolve())}}
    assert (root / "stamped-version").read_text() == "previous stamped-version\n"


@pytest.mark.parametrize("json_mode", [False, True])
def test_source_fingerprint_cli_preserves_its_output(tmp_path: Path, json_mode: bool) -> None:
    payload = tmp_path / "payload.txt"
    payload.write_text("source bytes\n", encoding="utf-8")
    command = [sys.executable, "-I", "-B", str(LIBRARY / "versioned_runtime.py"),
               "--root", str(tmp_path / "runtime")]
    if json_mode:
        command.append("--json")
    result = subprocess.run(
        [*command, "fingerprint", str(payload)], capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = publication._runtime().fingerprint_source([str(payload)])
    assert result.stdout == (json.dumps({"fingerprint": expected}) if json_mode else expected) + "\n"
    assert result.stderr == ""


def test_real_competing_publishers_hold_the_redirect_kernel_lease(tmp_path: Path) -> None:
    root, payload, _ = _case(tmp_path)
    other = tmp_path / "other-payload"
    other.mkdir()
    release, first_ready, blocked = (tmp_path / name for name in ("release", "first-ready", "blocked"))
    program = """
import os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import versioned_runtime_source as p
role, root, payload, release, ready, blocked = sys.argv[2:]
root, payload, release, ready, blocked = map(Path, (root, payload, release, ready, blocked))
if role == 'first':
    replace = os.replace
    def hold(source, destination):
        replace(source, destination)
        if Path(destination).name == 'payload-dir':
            ready.touch()
            deadline = time.monotonic() + 10
            while not release.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError('parent did not release first publisher')
                time.sleep(0.01)
    p.os.replace = hold
else:
    sleep = time.sleep
    def observed_contention(delay):
        blocked.touch()
        sleep(delay)
    p._runtime()._leases.time.sleep = observed_contention
p.publish_runtime_redirects(root, payload, '2.0.0' if role == 'first' else '3.0.0')
"""
    common = [str(root), str(payload), str(release), str(first_ready), str(blocked)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", program, str(LIBRARY), "first", *common],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=flags,
    ) as first:
        second = None
        try:
            deadline = time.monotonic() + 10
            while not first_ready.exists():
                assert first.poll() is None, first.communicate()
                assert time.monotonic() < deadline
                time.sleep(0.01)
            common[1] = str(other)
            second = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", program, str(LIBRARY), "second", *common],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=flags,
            )
            while not blocked.exists():
                assert second.poll() is None, second.communicate()
                assert time.monotonic() < deadline
                time.sleep(0.01)
        finally:
            release.touch()
            first_output = first.communicate(timeout=10)
            if second is not None:
                second_output = second.communicate(timeout=10)
        assert first.returncode == 0, first_output
        assert second is not None and second.returncode == 0, second_output
    assert (root / "payload-dir").read_text() == str(other.resolve()) + "\n"
    assert (root / "stamped-version").read_text() == "3.0.0\n"
    assert not list(root.glob(".*.redirect-*"))
