"""Cooperative selector replacement across actual Windows readers and writers."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import venv

import pytest

LIBRARY = Path(__file__).resolve().parents[1]
REPO = LIBRARY.parents[1]
sys.path.insert(0, str(LIBRARY))
import versioned_runtime as runtime

OLD, NEW = "1.0.0", "2.0.0"
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows handle-sharing contract")
HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
RESOLVERS = {
    "canonical": LIBRARY / "resolve-runtime.ps1",
    "worktrees": REPO / "plugins" / "agent-worktrees" / "scripts" / "resolve-runtime.ps1",
    "update-driver": REPO / "libs" / "update-driver" / "scripts" / "resolve-runtime.ps1",
}


class PlatformOS:
    def __init__(self, name: str):
        self.name = name

    def __getattr__(self, key: str):
        return getattr(os, key)


@pytest.fixture
def case(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("s")
    value = str(directory)
    if os.name == "nt" and value.startswith("\\\\?\\") and not value.startswith("\\\\?\\UNC\\"):
        directory = Path(value[4:])
    root = directory / "r"
    root.mkdir()
    for version in (OLD, NEW):
        slot = root / "versions" / version
        interpreter = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b"synthetic interpreter")
        (slot / ".install-complete.json").write_text(json.dumps({
            "version": version, "completed_at": "fixture", "pid": 1,
        }), encoding="utf-8")
    runtime.activate(root, OLD, link_free=True)
    return root


@contextmanager
def native_handle(path: Path, access: int, share: int):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), access, share, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield kernel, handle
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def read_handle(kernel, handle) -> bytes:
    import ctypes
    from ctypes import wintypes

    kernel.ReadFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    kernel.ReadFile.restype = wintypes.BOOL
    buffer, size = ctypes.create_string_buffer(128), wintypes.DWORD()
    if not kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(size), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.raw[:size.value]


def handle_identity(kernel, handle):
    import ctypes
    from ctypes import wintypes

    class FileIdInfo(ctypes.Structure):
        _fields_ = [("volume", ctypes.c_ulonglong), ("file_id", ctypes.c_ubyte * 16)]

    kernel.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    info = FileIdInfo()
    if not kernel.GetFileInformationByHandleEx(handle, 18, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return info.volume, bytes(info.file_id)


def generated_cmd_launcher(case: Path, kind: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict]:
    launcher = case.parent / f"{kind}.cmd"
    env = dict(os.environ, AGENT_WORKTREES_ROOT=str(case), WORKTREE_MANAGER_ROOT=str(case), CE_TERMINAL_LAUNCH="1")
    if kind == "application":
        source = REPO / "libs" / "installer-engine" / "runtime_application_binstubs.py"
        spec = importlib.util.spec_from_file_location("_selector_test_application_binstubs", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        content = module.global_binstubs("agent-worktrees", "AGENT_WORKTREES_ROOT", ".agent-worktrees", root=case)[
            "agent-worktrees.cmd"
        ]
        for version in (OLD, NEW):
            slot_command = case / "versions" / version / "agent-worktrees.cmd"
            slot_command.write_text(f"@echo off\r\necho SELECTED:{version}\r\nexit /b 0\r\n",
                                    encoding="utf-8", newline="")
    else:
        source = REPO / "worktree-manager" / "src"
        monkeypatch.syspath_prepend(str(source))
        from worktree_manager import runtime_launchers

        assert Path(runtime_launchers.__file__).resolve() == (
            source / "worktree_manager" / "runtime_launchers.py"
        ).resolve()
        content = runtime_launchers._cmd_binstub()
        for version in (OLD, NEW):
            slot = case / "versions" / version
            venv.EnvBuilder(with_pip=False).create(slot)
            bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
            bootstrap.parent.mkdir()
            bootstrap.write_text(f"print('SELECTED:{version}')\n", encoding="utf-8")
    launcher.write_text(content, encoding="utf-8", newline="")
    return launcher, env


@WINDOWS
@pytest.mark.parametrize("kind", ["application", "manager"])
def test_generated_cmd_reads_after_delete_intent_released(case: Path, kind: str, monkeypatch: pytest.MonkeyPatch):
    launcher, env = generated_cmd_launcher(case, kind, monkeypatch)
    ready = case.parent / "cmd-ready"
    wrapper = case.parent / "invoke.cmd"
    wrapper.write_text('@echo off\r\n> "%FIXTURE_READY%" echo ready\r\n'
                       'call "%FIXTURE_LAUNCHER%"\r\nexit /b %errorlevel%\r\n',
                       encoding="utf-8", newline="")
    env.update(FIXTURE_READY=str(ready), FIXTURE_LAUNCHER=str(launcher))
    process = None
    try:
        with native_handle(case / "current-version", 0x80010000, 0x7):
            process = subprocess.Popen(
                [os.environ["COMSPEC"], "/d", "/q", "/c", str(wrapper)],
                env=env, cwd=case.parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, creationflags=HEADLESS,
            )
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.005)
            assert ready.exists(), "CMD wrapper did not start"
            if kind == "manager":
                cache = case / "cache" / "python"
                while (not cache.exists() or not list(cache.glob("unsealed-*"))) and (
                    process.poll() is None and time.monotonic() < deadline
                ):
                    time.sleep(0.005)
            time.sleep(0.1)
            assert process.poll() is None, "launcher failed before delete intent was released"
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (process.returncode, stdout, stderr)
        assert stdout.strip() == f"SELECTED:{OLD}"
        assert not stderr.strip()
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


@WINDOWS
@pytest.mark.parametrize("kind", ["application", "manager"])
def test_generated_cmd_preserves_marker_behavior(case: Path, kind: str, monkeypatch: pytest.MonkeyPatch):
    launcher, env = generated_cmd_launcher(case, kind, monkeypatch)
    marker = case / "current-version"
    cases = [
        (b"1.0.0", OLD), (b"1.0.0\n", OLD), (b"1.0.0\r\n", OLD),
        (b"", None), (None, None), (b"\r\n1.0.0\r\n", None),
        (b";ignored\r\n1.0.0\r\n", None), (b"1.0.0\r\n2.0.0\r\n", OLD),
        (b"1.0.0\n2.0.0\n", None),
    ]
    if kind == "application":
        cases.extend([
            (b"\n1.0.0\n", OLD), (b";ignored\n1.0.0\n", OLD),
            (b"1.0.0\nignored\n", None), (b" 1.0.0\n", None), (b"1!0\n", None),
        ])
    for data, version in cases:
        if data is None:
            marker.unlink(missing_ok=True)
        else:
            marker.write_bytes(data)
        process = subprocess.run(
            [os.environ["COMSPEC"], "/d", "/q", "/c", str(launcher)],
            env=env, cwd=case.parent, capture_output=True, text=True, timeout=10,
            creationflags=HEADLESS,
        )
        if version is None:
            assert process.returncode != 0, (data, process.stdout, process.stderr)
            assert "SELECTED:" not in process.stdout, (data, process.stdout, process.stderr)
            if data is None:
                assert len(process.stderr.strip().splitlines()) == 1
            elif data == b"":
                assert not process.stderr.strip()
        else:
            assert process.returncode == 0, (data, process.stdout, process.stderr)
            assert process.stdout.strip() == f"SELECTED:{version}"


@WINDOWS
@pytest.mark.parametrize("kind", ["application", "manager"])
def test_generated_cmd_persistent_delete_intent_fails_once(case: Path, kind: str, monkeypatch: pytest.MonkeyPatch):
    launcher, env = generated_cmd_launcher(case, kind, monkeypatch)
    with native_handle(case / "current-version", 0x80010000, 0x7):
        started = time.monotonic()
        process = subprocess.run(
            [os.environ["COMSPEC"], "/d", "/q", "/c", str(launcher)],
            env=env, cwd=case.parent, capture_output=True, text=True, timeout=10,
            creationflags=HEADLESS,
        )
        assert time.monotonic() - started < 10
    assert process.returncode != 0
    assert "SELECTED:" not in process.stdout
    assert len(process.stderr.strip().splitlines()) == 1


@WINDOWS
@pytest.mark.parametrize("kind", ["application", "manager"])
def test_generated_cmd_handles_metacharacters_in_root(case: Path, kind: str, monkeypatch: pytest.MonkeyPatch):
    root = case.with_name("root (space & bang!) 100%")
    case.rename(root)
    launcher, env = generated_cmd_launcher(root, kind, monkeypatch)
    result = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/q", "/c", str(launcher)],
        env=env, cwd=root.parent, capture_output=True, text=True, timeout=10,
        creationflags=HEADLESS,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout.strip() == f"SELECTED:{OLD}"


@WINDOWS
@pytest.mark.parametrize("kind", ["application", "manager"])
def test_generated_cmd_preserves_arguments_and_exit(case: Path, kind: str, monkeypatch: pytest.MonkeyPatch):
    launcher, env = generated_cmd_launcher(case, kind, monkeypatch)
    slot = case / "versions" / OLD
    if kind == "application":
        (slot / "agent-worktrees.cmd").write_text(
            "@echo off\r\necho FIRST:%~1\r\necho SECOND:%~2\r\nexit /b 7\r\n",
            encoding="utf-8", newline="",
        )
    else:
        (slot / ".runtime-bootstrap" / "runtime_launcher.py").write_text(
            "import sys\nprint('FIRST:' + sys.argv[3])\nprint('SECOND:' + sys.argv[4])\n"
            "raise SystemExit(7)\n",
            encoding="utf-8",
        )
    result = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/q", "/c", str(launcher), "alpha", "space value"],
        env=env, cwd=case.parent, capture_output=True, text=True, timeout=10,
        creationflags=HEADLESS,
    )
    assert result.returncode == 7, (result.stdout, result.stderr)
    assert result.stdout.splitlines() == ["FIRST:alpha", "SECOND:space value"]


def ps_host(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        pytest.skip(f"{name} unavailable")
    return executable


def quote_ps(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def resolve_in_powershell(
    host: str, owner: str, root: Path, *, pretend_posix: bool = False,
) -> subprocess.CompletedProcess[str]:
    trace = "$_awTraceSource" if owner == "worktrees" else "$_rtTraceSource"
    platform = "Set-Variable -Name IsWindows -Value $false -Force; " if pretend_posix else ""
    code = (
        "$ErrorActionPreference='Stop'; " + platform +
        f"$env:AGENT_RT_ROOT={quote_ps(root)}; try {{ "
        f". {quote_ps(RESOLVERS[owner])} -TraceMode 'read-only'; "
        f"@{{path=$AgentRtPy;source={trace}}} | ConvertTo-Json -Compress "
        "} catch { $e=$_.Exception; while ($e.InnerException) { $e=$e.InnerException }; "
        "[Console]::Out.Write(('{0:X8}' -f $e.HResult)); exit 43 }"
    )
    return subprocess.run(
        [ps_host(host), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", code],
        capture_output=True, text=True, timeout=20, creationflags=HEADLESS,
    )


@WINDOWS
@pytest.mark.parametrize("with_receipt", [False, True])
def test_held_delete_sharing_reader_survives_actual_activation(case: Path, with_receipt: bool):
    marker = case / "current-version"
    receipt = case / "receipt.jsonl" if with_receipt else None
    with native_handle(marker, 0x80000000, 0x7) as (kernel, handle):
        runtime.activate(case, NEW, link_free=True, ownership_receipt=receipt)
        assert read_handle(kernel, handle).strip() == OLD.encode()
        assert marker.read_text(encoding="utf-8").strip() == NEW
    assert runtime.read_last_known_good(case) == NEW


@WINDOWS
def test_python_and_isolated_bootstrap_read_while_delete_intent_is_open(case: Path):
    runtime.activate(case, NEW, link_free=True)
    (case / "last-known-good").write_text(OLD, encoding="utf-8")
    code = (
        "import pathlib,sys; sys.path.insert(0,sys.argv[1]); "
        "import versioned_runtime as r; print(r.current_version(pathlib.Path(sys.argv[2])))"
    )
    with native_handle(case / "current-version", 0x10000, 0x7):
        assert runtime.current_version(case) == NEW
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", code, str(LIBRARY), str(case)],
            capture_output=True, text=True, timeout=20, creationflags=HEADLESS,
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == NEW


@WINDOWS
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("owner", tuple(RESOLVERS))
def test_actual_powershell_resolver_reads_with_delete_intent(case: Path, host: str, owner: str):
    runtime.activate(case, NEW, link_free=True)
    (case / "last-known-good").write_text(OLD, encoding="utf-8")
    with native_handle(case / "current-version", 0x10000, 0x7):
        result = resolve_in_powershell(host, owner, case)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["source"] == "current-version"
    assert Path(data["path"]).parent.parent.name == NEW


@WINDOWS
@pytest.mark.parametrize("blocked", ["current-version", "last-known-good"])
def test_python_selector_denial_does_not_become_a_fallback(case: Path, blocked: str):
    runtime.activate(case, NEW, link_free=True)
    (case / "last-known-good").write_text(OLD, encoding="utf-8")
    if blocked == "last-known-good":
        (case / "current-version").unlink()
    with native_handle(case / blocked, 0x80000000, 0):
        with pytest.raises(OSError) as captured:
            runtime.resolve_python(case)
    assert captured.value.winerror == 32


def test_unreadable_slot_degrades_only_for_read_only_resolution(case: Path, monkeypatch: pytest.MonkeyPatch):
    (case / "last-known-good").write_text(NEW, encoding="utf-8")
    current = runtime.version_dir(case, OLD)
    exists = Path.exists

    def inaccessible(path):
        if path == current:
            raise PermissionError("selected slot is unreadable")
        return exists(path)

    monkeypatch.setattr(Path, "exists", inaccessible)
    with pytest.raises(PermissionError, match="selected slot"):
        runtime.current_version(case)
    assert runtime.resolve_python(case).parent.parent.name == NEW


@WINDOWS
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("owner", tuple(RESOLVERS))
@pytest.mark.parametrize("blocked", ["current-version", "last-known-good"])
def test_powershell_selector_denial_is_visible(case: Path, host: str, owner: str, blocked: str):
    runtime.activate(case, NEW, link_free=True)
    (case / "last-known-good").write_text(OLD, encoding="utf-8")
    if blocked == "last-known-good":
        (case / "current-version").unlink()
    with native_handle(case / blocked, 0x80000000, 0):
        result = resolve_in_powershell(host, owner, case)
    assert result.returncode == 43, result.stdout + result.stderr
    assert result.stdout.strip() == "80070020"


@WINDOWS
@pytest.mark.parametrize(("blocked", "with_receipt"), [
    ("current-version", False),
    ("current-version", True),
    ("last-known-good", True),
])
def test_permanent_selector_write_denial_is_bounded_and_visible(case: Path, blocked: str, with_receipt: bool):
    receipt = case / "denial-receipt.jsonl" if with_receipt else None
    with native_handle(case / blocked, 0x80000000, 0x1):
        started = time.monotonic()
        with pytest.raises(OSError) as captured:
            runtime.activate(case, NEW, link_free=True, ownership_receipt=receipt)
        assert time.monotonic() - started < 5
    assert captured.value.winerror in {5, 32, 33}
    expected_current = OLD if blocked == "current-version" else NEW
    assert (case / "current-version").read_text(encoding="utf-8").strip() == expected_current
    assert (case / "last-known-good").read_text(encoding="utf-8").strip() == OLD


@WINDOWS
def test_unguarded_last_good_share_denial_is_best_effort(case: Path):
    with native_handle(case / "last-known-good", 0x80000000, 0x1):
        started = time.monotonic()
        assert runtime.activate(case, NEW, link_free=True) == runtime.version_dir(case, NEW)
        assert time.monotonic() - started < 5
    assert (case / "current-version").read_text(encoding="utf-8").strip() == NEW
    assert (case / "last-known-good").read_text(encoding="utf-8").strip() == OLD


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_unguarded_last_good_write_error_preserves_success(
    case: Path, monkeypatch: pytest.MonkeyPatch, platform: str,
):
    publication = runtime._runtime_component("versioned_runtime_publication")
    replace = publication.replace_selector

    def fail_last(source, destination):
        if destination.name == "last-known-good":
            raise PermissionError("last-known-good write denied")
        return replace(source, destination)

    monkeypatch.setattr(publication, "os", PlatformOS(platform))
    monkeypatch.setattr(publication, "replace_selector", fail_last)
    assert runtime.activate(case, NEW, link_free=True) == runtime.version_dir(case, NEW)
    assert (case / "current-version").read_text(encoding="utf-8").strip() == NEW
    assert (case / "last-known-good").read_text(encoding="utf-8").strip() == OLD


@WINDOWS
def test_guarded_denial_recovers_owned_primary_after_handle_release(case: Path):
    proof = runtime._runtime_component("versioned_runtime_publication")
    targets = [case / "current-version", case / "last-known-good"]
    baseline = {path: proof.state_path(path) for path in targets}
    evidence = case.parent / "evidence"
    evidence.mkdir()
    bindings = []
    for index, target in enumerate(targets):
        previous = target.read_bytes()
        backup = evidence / f"before-{index}"
        with os.fdopen(proof._created_file(backup), "wb") as stream:
            stream.write(previous)
        bindings.append([str(target), str(backup), hashlib.sha256(previous).hexdigest()])
    context = evidence / "context.json"
    receipt = evidence / "activation.jsonl"
    recovery = evidence / "recovery.jsonl"
    proof.create_context(context, case, bindings)
    with native_handle(targets[1], 0x80000000, 0x1):
        with pytest.raises(OSError) as captured:
            runtime.activate(case, NEW, link_free=True, ownership_receipt=receipt)
    assert captured.value.winerror == 32
    assert [path.read_text(encoding="utf-8").strip() for path in targets] == [NEW, OLD]
    assert json.loads(receipt.read_text(encoding="utf-8").splitlines()[-1])["outcome"] == "error"
    expected = [[str(path), hashlib.sha256((NEW + "\n").encode()).hexdigest()] for path in targets]
    assert proof.recover_uncontested(context, [receipt], recovery, expected, baseline) == []
    assert [path.read_text(encoding="utf-8").strip() for path in targets] == [OLD, OLD]
    assert proof.state_path(targets[1]) == baseline[targets[1]]
    proof.check_owned(context, [receipt, recovery], [targets[0]], expected)
    assert context.exists() and receipt.exists() and recovery.exists()


@pytest.fixture
def guarded_recovery(case: Path):
    proof = runtime._runtime_component("versioned_runtime_publication")
    targets = [case / "current-version", case / "last-known-good"]
    baseline = {path: proof.state_path(path) for path in targets}
    old_bytes = {path: path.read_bytes() for path in targets}
    evidence = case.parent / "held-recovery-evidence"
    evidence.mkdir()
    bindings = []
    for index, target in enumerate(targets):
        previous = target.read_bytes()
        backup = evidence / f"before-{index}"
        with os.fdopen(proof._created_file(backup), "wb") as stream:
            stream.write(previous)
        bindings.append([str(target), str(backup), hashlib.sha256(previous).hexdigest()])
    context, receipt, recovery = (evidence / name for name in (
        "context.json", "activation.jsonl", "recovery.jsonl",
    ))
    proof.create_context(context, case, bindings)
    runtime.activate(case, NEW, link_free=True, ownership_receipt=receipt)
    expected = [[str(path), hashlib.sha256((NEW + "\n").encode()).hexdigest()] for path in targets]
    assert json.loads(receipt.read_text(encoding="utf-8").splitlines()[-1])["outcome"] == "success"
    proof.check_owned(context, [receipt], targets, expected)
    return proof, targets, baseline, old_bytes, context, receipt, recovery, expected


@WINDOWS
@pytest.mark.parametrize("target_name", ["current-version", "last-known-good"])
@pytest.mark.parametrize("recovery_owner", ["recover_owned", "recover_uncontested"])
def test_guarded_recovery_keeps_delete_shared_reader_on_new_selector(
    case: Path, guarded_recovery, target_name: str, recovery_owner: str,
):
    proof, targets, baseline, old_bytes, context, receipt, recovery, expected = guarded_recovery
    selected_target = case / target_name
    selected = proof.state_path(selected_target)
    assert selected in proof.read_receipt(receipt, proof.absolute(case), set(targets))[selected_target]
    with native_handle(selected_target, 0x80000000, 0x7) as (kernel, held):
        held_id = handle_identity(kernel, held)
        with native_handle(selected_target, 0x80000000, 0x7) as (fresh_kernel, fresh):
            assert handle_identity(fresh_kernel, fresh) == held_id
        if recovery_owner == "recover_owned":
            proof.recover_owned(context, [receipt], targets, recovery, expected)
        else:
            assert proof.recover_uncontested(context, [receipt], recovery, expected, baseline) == []
        assert handle_identity(kernel, held) == held_id
        assert read_handle(kernel, held) == (NEW + "\n").encode()
        with native_handle(selected_target, 0x80000000, 0x7) as (fresh_kernel, fresh):
            assert handle_identity(fresh_kernel, fresh) != held_id
            assert read_handle(fresh_kernel, fresh) == old_bytes[selected_target]
    recovered = proof.state_path(selected_target)
    assert (recovered.device, recovered.inode) != (selected.device, selected.inode)
    assert {path: path.read_bytes() for path in targets} == old_bytes
    assert json.loads(recovery.read_text(encoding="utf-8").splitlines()[-1])["outcome"] == "success"
    assert recovered in proof.read_receipt(recovery, proof.absolute(case), set(targets))[selected_target]
    proof.check_owned(context, [receipt, recovery], targets, expected)


@WINDOWS
def test_guarded_recovery_denial_is_bounded_and_retains_proof(guarded_recovery):
    proof, targets, baseline, old_bytes, context, receipt, recovery, expected = guarded_recovery
    selected = proof.state_path(targets[0])
    with native_handle(targets[0], 0x80000000, 0x1):
        started = time.monotonic()
        with pytest.raises(OSError) as captured:
            proof.recover_uncontested(context, [receipt], recovery, expected, baseline)
        assert time.monotonic() - started < 5
        assert captured.value.winerror in {5, 32, 33}
        assert proof.state_path(targets[0]) == selected
        assert all(path.read_bytes() == (NEW + "\n").encode() for path in targets)
    assert json.loads(recovery.read_text(encoding="utf-8").splitlines()[-1])["outcome"] == "error"
    proof.check_owned(context, [receipt, recovery], targets, expected)
    retry = recovery.with_name("retry.jsonl")
    assert proof.recover_uncontested(context, [receipt, recovery], retry, expected, baseline) == []
    assert {path: path.read_bytes() for path in targets} == old_bytes
    proof.check_owned(context, [receipt, recovery, retry], targets, expected)


@WINDOWS
def test_delete_shared_incumbent_last_good_does_not_veto_incoming(case: Path):
    with native_handle(case / "last-known-good", 0x10000, 0x7):
        runtime.activate(case, NEW, link_free=True)
    assert (case / "current-version").read_text(encoding="utf-8").strip() == NEW
    assert (case / "last-known-good").read_text(encoding="utf-8").strip() == NEW


@WINDOWS
def test_unreadable_but_delete_shared_incumbent_does_not_veto_incoming(case: Path):
    with native_handle(case / "last-known-good", 0x40000000, 0x6):
        with pytest.raises(OSError) as captured:
            runtime.read_last_known_good(case)
        assert captured.value.winerror == 32
        runtime.activate(case, NEW, link_free=True)
    assert (case / "current-version").read_text(encoding="utf-8").strip() == NEW
    assert (case / "last-known-good").read_text(encoding="utf-8").strip() == NEW


def test_unreadable_last_good_optimization_does_not_veto_incoming(
    case: Path, monkeypatch: pytest.MonkeyPatch,
):
    def unavailable(_root):
        raise PermissionError("incumbent selector is unreadable")

    monkeypatch.setattr(runtime, "read_last_known_good", unavailable)
    runtime.activate(case, NEW, link_free=True)
    assert (case / "current-version").read_text(encoding="utf-8").strip() == NEW
    assert (case / "last-known-good").read_text(encoding="utf-8").strip() == NEW


@pytest.mark.parametrize("blocked", ["current-version", "last-known-good"])
def test_non_windows_selector_io_error_preserves_fallback(
    case: Path, monkeypatch: pytest.MonkeyPatch, blocked: str,
):
    publication = runtime._runtime_component("versioned_runtime_publication")
    monkeypatch.setattr(publication, "os", PlatformOS("posix"))
    if blocked == "current-version":
        (case / "last-known-good").write_text(NEW, encoding="utf-8")
    else:
        (case / "current-version").unlink()
    original = Path.read_text
    target = case / blocked

    def denied(path, *args, **kwargs):
        if path == target:
            raise PermissionError("selector read denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    assert (runtime._read_current_marker(case) if blocked == "current-version"
            else runtime.read_last_known_good(case)) is None
    assert runtime.resolve_python(case).parent.parent.name == NEW


@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_completed_resolver_only_degrades_callback_errors_on_non_windows(
    case: Path, monkeypatch: pytest.MonkeyPatch, platform: str,
):
    construction = runtime._runtime_component("versioned_runtime_construction")
    monkeypatch.setattr(construction, "os", PlatformOS(platform))
    expected = case / "fallback"

    def denied(_root):
        raise PermissionError("current selection denied")

    def resolve():
        return construction.resolve_completed_python(
            case, current_version=denied, read_last_known_good=lambda _root: OLD,
            is_complete=lambda _root, version: version == OLD,
            slot_python=lambda _root, _version: expected,
            list_versions=lambda _root: [], requires_exact_selection=lambda _versions: False,
        )

    if platform == "nt":
        with pytest.raises(PermissionError, match="current selection"):
            resolve()
    else:
        assert resolve() == expected


@WINDOWS
@pytest.mark.parametrize("owner", tuple(RESOLVERS))
@pytest.mark.parametrize("blocked", ["current-version", "last-known-good"])
def test_emulated_posix_powershell_preserves_error_fallback(case: Path, owner: str, blocked: str):
    if blocked == "last-known-good":
        (case / "current-version").unlink()
    with native_handle(case / blocked, 0x80000000, 0):
        result = resolve_in_powershell("pwsh.exe", owner, case, pretend_posix=True)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["source"] == ("last-known-good" if blocked == "current-version" else "newest")
    assert Path(data["path"]).parent.parent.name == (OLD if blocked == "current-version" else NEW)


@WINDOWS
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("owner", tuple(RESOLVERS))
def test_missing_current_selector_preserves_last_good_fallback(case: Path, host: str, owner: str):
    (case / "current-version").unlink()
    assert runtime.resolve_python(case).parent.parent.name == OLD
    result = resolve_in_powershell(host, owner, case)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["source"] == "last-known-good"
    assert Path(data["path"]).parent.parent.name == OLD


def test_non_windows_selector_publication_keeps_os_replace(case: Path, monkeypatch: pytest.MonkeyPatch):
    publication = runtime._runtime_component("versioned_runtime_publication")
    actual = os.replace
    calls = []

    class PosixOS:
        name = "posix"

        def __getattr__(self, key):
            return getattr(os, key)

        def replace(self, source, destination):
            calls.append((Path(source), Path(destination)))
            return actual(source, destination)

    monkeypatch.setattr(publication, "os", PosixOS())
    publication.publish_selectors(case, NEW, None, lambda _root, _version: None)
    assert len(calls) == 1 and calls[0][1] == case / "current-version"
    assert (case / "current-version").read_text(encoding="utf-8").strip() == NEW


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_windows_sharing_failure_is_retried(case: Path, monkeypatch: pytest.MonkeyPatch, winerror: int):
    publication = runtime._runtime_component("versioned_runtime_publication")
    failure = OSError("transient sharing failure")
    failure.winerror = winerror
    attempts = []

    def rename(source, destination):
        attempts.append((source, destination))
        if len(attempts) == 1:
            raise failure

    monkeypatch.setattr(publication, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(publication, "time", SimpleNamespace(sleep=lambda _: None))
    monkeypatch.setattr(publication, "_rename_selector_windows", rename)
    publication.replace_selector(case / "staged", case / "current-version")
    assert len(attempts) == 2


def test_windows_other_errors_are_not_retried(case: Path, monkeypatch: pytest.MonkeyPatch):
    publication = runtime._runtime_component("versioned_runtime_publication")
    failure = OSError("non-sharing failure")
    failure.winerror = 87
    attempts = []

    def rename(source, destination):
        attempts.append((source, destination))
        raise failure

    monkeypatch.setattr(publication, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(publication, "_rename_selector_windows", rename)
    with pytest.raises(OSError) as captured:
        publication.replace_selector(case / "staged", case / "current-version")
    assert captured.value is failure
    assert len(attempts) == 1


def fail_native_selector_call(monkeypatch: pytest.MonkeyPatch, code: int, *, opening: bool = False):
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    calls = []

    class Failure:
        def __init__(self, value):
            self.value = value

        def __call__(self, *_args):
            calls.append(code)
            ctypes.set_last_error(code)
            return self.value

    fake = SimpleNamespace(
        CreateFileW=Failure(ctypes.c_void_p(-1).value) if opening else kernel.CreateFileW,
        CloseHandle=kernel.CloseHandle,
        SetFileInformationByHandle=Failure(False),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: fake)
    return calls


def track_windows_replace(monkeypatch: pytest.MonkeyPatch, publication):
    calls = []

    class WindowsOS(PlatformOS):
        def replace(self, source, destination):
            calls.append((source, destination))
            return os.replace(source, destination)

    monkeypatch.setattr(publication, "os", WindowsOS("nt"))
    monkeypatch.setattr(publication, "time", SimpleNamespace(sleep=lambda _: None))
    return calls


@WINDOWS
@pytest.mark.parametrize("winerror", [50, 120])
@pytest.mark.parametrize("target_missing", [False, True])
def test_windows_explicitly_unsupported_rename_uses_atomic_replace(
    case: Path, monkeypatch: pytest.MonkeyPatch, winerror: int, target_missing: bool,
):
    publication = runtime._runtime_component("versioned_runtime_publication")
    native_calls = fail_native_selector_call(monkeypatch, winerror)
    fallback_calls = track_windows_replace(monkeypatch, publication)
    staged, target = case / "candidate", case / "current-version"
    staged.write_text(NEW + "\n", encoding="utf-8")
    if target_missing:
        target.unlink()
    publication.replace_selector(staged, target)
    assert len(native_calls) == len(fallback_calls) == 1
    assert target.read_text(encoding="utf-8").strip() == NEW
    assert not staged.exists()


@WINDOWS
@pytest.mark.parametrize("share", [0x1, 0x7])
def test_windows_unsupported_fallback_reports_real_sharing_denial(
    case: Path, monkeypatch: pytest.MonkeyPatch, share: int,
):
    publication = runtime._runtime_component("versioned_runtime_publication")
    staged, target = case / "candidate", case / "current-version"
    staged.write_text(NEW + "\n", encoding="utf-8")
    with native_handle(target, 0x80000000, share):
        native_calls = fail_native_selector_call(monkeypatch, 50)
        fallback_calls = track_windows_replace(monkeypatch, publication)
        with pytest.raises(OSError) as captured:
            publication.replace_selector(staged, target)
    assert captured.value.winerror in {5, 32, 33}
    assert len(native_calls) == len(fallback_calls) == 1
    assert staged.exists() and target.read_text(encoding="utf-8").strip() == OLD


@WINDOWS
@pytest.mark.parametrize("winerror", [1, 5, 32, 33, 87, 1175])
def test_windows_other_native_errors_never_use_fallback(
    case: Path, monkeypatch: pytest.MonkeyPatch, winerror: int,
):
    publication = runtime._runtime_component("versioned_runtime_publication")
    native_calls = fail_native_selector_call(monkeypatch, winerror)
    fallback_calls = track_windows_replace(monkeypatch, publication)
    staged, target = case / "candidate", case / "current-version"
    staged.write_text(NEW + "\n", encoding="utf-8")
    with pytest.raises(OSError) as captured:
        publication.replace_selector(staged, target)
    assert captured.value.winerror == winerror
    assert native_calls and not fallback_calls
    assert staged.exists() and target.read_text(encoding="utf-8").strip() == OLD


@WINDOWS
def test_windows_unsupported_source_open_never_uses_fallback(case: Path, monkeypatch: pytest.MonkeyPatch):
    publication = runtime._runtime_component("versioned_runtime_publication")
    native_calls = fail_native_selector_call(monkeypatch, 50, opening=True)
    fallback_calls = track_windows_replace(monkeypatch, publication)
    staged, target = case / "candidate", case / "current-version"
    staged.write_text(NEW + "\n", encoding="utf-8")
    with pytest.raises(OSError) as captured:
        publication.replace_selector(staged, target)
    assert captured.value.winerror == 50
    assert len(native_calls) == 1 and not fallback_calls
    assert staged.exists() and target.read_text(encoding="utf-8").strip() == OLD


def wait_files(paths: list[Path], processes: list[subprocess.Popen], timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while not all(path.is_file() for path in paths):
        assert all(process.poll() is None for process in processes), "reader exited before barrier"
        assert time.monotonic() < deadline, "reader barrier deadline"
        time.sleep(0.005)


PS_LOOP = r"""
param($Root,$Resolver,$Owner,$Ready,$Start,$Active,$Seen,$Done)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$env:AGENT_RT_ROOT=$Root
$clock=[Diagnostics.Stopwatch]::StartNew()
[IO.File]::WriteAllText($Ready,'1')
while (-not [IO.File]::Exists($Start)) {
    if ($clock.ElapsedMilliseconds -gt 30000) { exit 24 }
    [Threading.Thread]::Sleep(2)
}
$attempts=0; $old=0; $new=0; $bad=0; $errors=0
while (-not [IO.File]::Exists($Done)) {
    if ($clock.ElapsedMilliseconds -gt 30000) { exit 24 }
    $attempts++
    try {
        . $Resolver -TraceMode 'read-only'
        $source=if ($Owner -eq 'worktrees') { $_awTraceSource } else { $_rtTraceSource }
        $version=if ($AgentRtPy) { Split-Path -Leaf (Split-Path -Parent (Split-Path -Parent $AgentRtPy)) } else { '' }
        if ($source -ne 'current-version') { $bad++ }
        elseif ($version -ceq '1.0.0') { $old++ }
        elseif ($version -ceq '2.0.0') { $new++; if (-not [IO.File]::Exists($Seen)) { [IO.File]::WriteAllText($Seen,'1') } }
        else { $bad++ }
    } catch { $errors++ }
    if ($attempts -eq 1) { [IO.File]::WriteAllText($Active,'1') }
}
@{attempts=$attempts;old=$old;new=$new;bad=$bad;errors=$errors} | ConvertTo-Json -Compress
"""

PY_LOOP = r"""
import json,pathlib,sys,time
sys.path.insert(0,sys.argv[1])
import versioned_runtime as runtime
root,ready,start,active,seen,done=map(pathlib.Path,sys.argv[2:])
ready.write_text('1')
deadline=time.monotonic()+30
while not start.exists():
    if time.monotonic()>deadline: raise TimeoutError('start')
    time.sleep(.002)
counts={'attempts':0,'old':0,'new':0,'bad':0,'errors':0}
while not done.exists():
    if time.monotonic()>deadline: raise TimeoutError('read')
    counts['attempts']+=1
    try:
        value=runtime._read_current_marker(root)
        selected=runtime.resolve_python(root)
        if selected is None or selected.parent.parent.name not in ('1.0.0','2.0.0'):
            counts['bad']+=1
        elif value=='1.0.0': counts['old']+=1
        elif value=='2.0.0':
            counts['new']+=1
            if not seen.exists(): seen.write_text('1')
        else: counts['bad']+=1
    except OSError:
        counts['errors']+=1
    if counts['attempts']==1: active.write_text('1')
print(json.dumps(counts))
"""


@WINDOWS
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("owner", tuple(RESOLVERS))
def test_actual_concurrent_resolvers_during_selection(case: Path, host: str, owner: str):
    executable = ps_host(host)
    directory = case.parent
    script = directory / "loop.ps1"
    script.write_text(PS_LOOP, encoding="utf-8")
    start, done = directory / "start", directory / "done"
    processes, ready, active, seen = [], [], [], []
    try:
        for index in range(3):
            r, a, n = [directory / f"{kind}{index}" for kind in ("ready", "active", "seen")]
            if index < 2:
                args = [
                    executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script),
                    str(case), str(RESOLVERS[owner]), owner, str(r), str(start), str(a), str(n), str(done),
                ]
            else:
                args = [sys.executable, "-I", "-S", "-B", "-c", PY_LOOP, str(LIBRARY),
                        str(case), str(r), str(start), str(a), str(n), str(done)]
            processes.append(subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=HEADLESS,
            ))
            ready.append(r)
            active.append(a)
            seen.append(n)
        wait_files(ready, processes)
        start.write_text("1")
        wait_files(active, processes)
        runtime.activate(case, NEW, link_free=True)
        wait_files(seen, processes)
        for index in range(60):
            runtime.activate(case, OLD if index % 2 == 0 else NEW, link_free=True)
            time.sleep(0.002)
    finally:
        done.write_text("1")
        outputs = []
        for process in processes:
            try:
                outputs.append(process.communicate(timeout=15))
            except subprocess.TimeoutExpired:
                process.kill()
                outputs.append(process.communicate(timeout=5))
    for process, (stdout, stderr) in zip(processes, outputs):
        assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
        data = json.loads(stdout.decode("utf-8-sig"))
        assert data["old"] > 0 and data["new"] > 0
        assert data["bad"] == 0 and data["errors"] == 0
