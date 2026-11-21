"""bootstrap.ps1 must fail when a native step fails.

``$ErrorActionPreference = 'Stop'`` does not apply to native exit codes, and
``pwsh -File`` reports success unless the script itself fails. Without explicit
checks a failed ``git`` fetch or ``uv`` step still exited 0, so callers only
failed later with a misleading "command is missing" error (or silently
installed a stale staged payload).
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import contextmanager
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BOOTSTRAP = Path(__file__).resolve().parents[1] / "bootstrap.ps1"
PWSH = shutil.which("pwsh") or ""

pytestmark = pytest.mark.skipif(
    not PWSH or shutil.which("git") is None,
    reason="pwsh and git are required to exercise bootstrap.ps1",
)

_GIT_ID = [
    "-c", "user.email=e2e@example.invalid",
    "-c", "user.name=e2e",
    "-c", "commit.gpgsign=false",
]


_LONG_SOURCE_PATH = Path("source-fixtures") / ("nested-" * 15) / ("file-" * 20 + ".txt")


@contextmanager
def _unprivileged_link_creator(directory: Path) -> Iterator[None]:
    """RedirectionGuard permits links created by an administrator."""
    import ctypes
    from ctypes import wintypes

    shell = getattr(ctypes, "WinDLL")("shell32", use_last_error=True)
    if not shell.IsUserAnAdmin():
        yield
        return
    kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    security = getattr(ctypes, "WinDLL")("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetCurrentThread.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    security.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    security.OpenThreadToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE),
    ]
    security.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID),
    ]
    security.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    security.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
    ]

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

    class Trustee(ctypes.Structure):
        _fields_ = [
            ("MultipleTrustee", wintypes.LPVOID), ("Operation", ctypes.c_int),
            ("Form", ctypes.c_int), ("Type", ctypes.c_int), ("Name", wintypes.LPVOID),
        ]

    class ExplicitAccess(ctypes.Structure):
        _fields_ = [
            ("Permissions", wintypes.DWORD), ("Mode", ctypes.c_int),
            ("Inheritance", wintypes.DWORD), ("Trustee", Trustee),
        ]

    security.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    ]
    security.SetEntriesInAclW.argtypes = [
        wintypes.DWORD, ctypes.POINTER(ExplicitAccess), wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    security.CreateRestrictedToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(SidAndAttributes), wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.LPVOID, ctypes.POINTER(wintypes.HANDLE),
    ]
    security.ImpersonateLoggedOnUser.argtypes = [wintypes.HANDLE]
    process_token, restricted_token, thread_token = (wintypes.HANDLE() for _ in range(3))
    administrators = wintypes.LPVOID()
    descriptor, original_acl, fixture_acl = (wintypes.LPVOID() for _ in range(3))
    impersonating = False
    try:
        if security.OpenThreadToken(kernel.GetCurrentThread(), 8, True, ctypes.byref(thread_token)):
            raise RuntimeError("Link fixture must begin without an impersonated thread token")
        if getattr(ctypes, "get_last_error")() != 1008:  # ERROR_NO_TOKEN
            raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 10, ctypes.byref(process_token)):
            raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
        size = wintypes.DWORD()
        security.GetTokenInformation(process_token, 1, None, 0, ctypes.byref(size))
        user = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(
            process_token, 1, user, len(user), ctypes.byref(size),
        ):
            raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
        user_sid = SidAndAttributes.from_buffer(user).Sid
        # Preserve pytest's ACL and admit only this same user to the owned fixture.
        error = security.GetNamedSecurityInfoW(
            str(directory), 1, 4, None, None, ctypes.byref(original_acl), None,
            ctypes.byref(descriptor),
        )
        if error:
            raise getattr(ctypes, "WinError")(error)
        access = ExplicitAccess(0x1F01FF, 1, 3, Trustee(None, 0, 0, 1, user_sid))
        error = security.SetEntriesInAclW(1, ctypes.byref(access), original_acl, ctypes.byref(fixture_acl))
        if error:
            raise getattr(ctypes, "WinError")(error)
        error = security.SetNamedSecurityInfoW(str(directory), 1, 4, None, None, fixture_acl, None)
        if error:
            raise getattr(ctypes, "WinError")(error)
        if not security.ConvertStringSidToSidW("S-1-5-32-544", ctypes.byref(administrators)):
            raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
        disabled = SidAndAttributes(administrators, 0)
        if not security.CreateRestrictedToken(
            process_token, 1, 1, ctypes.byref(disabled), 0, None, 0, None,
            ctypes.byref(restricted_token),
        ):  # DISABLE_MAX_PRIVILEGE and deny-only Administrators membership.
            raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
        if not security.ImpersonateLoggedOnUser(restricted_token):
            raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
        impersonating = True
        if shell.IsUserAnAdmin():
            raise RuntimeError("Link creator still has administrator membership")
        yield
    finally:
        try:
            if impersonating and not security.RevertToSelf():
                raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
        finally:
            for token in (thread_token, restricted_token, process_token):
                if token:
                    kernel.CloseHandle(token)
            for allocation in (administrators, descriptor, fixture_acl):
                if allocation:
                    kernel.LocalFree(allocation)


def _create_untrusted_junction(target: Path, link: Path) -> None:
    import ctypes
    from ctypes import wintypes
    import struct

    kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.DeviceIoControl.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    substitute = ("\\??\\" + str(target)).encode("utf-16le")
    printed = str(target).encode("utf-16le")
    names = substitute + b"\0\0" + printed + b"\0\0"
    payload = struct.pack("<HHHH", 0, len(substitute), len(substitute) + 2, len(printed)) + names
    packet = struct.pack("<LHH", 0xA0000003, len(payload), 0) + payload
    with _unprivileged_link_creator(link.parent):
        link.mkdir()
        handle = kernel.CreateFileW(str(link), 0x40000000, 7, None, 3, 0x02200000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
        try:
            returned = wintypes.DWORD()
            if not kernel.DeviceIoControl(
                handle, 0x900A4, ctypes.create_string_buffer(packet), len(packet),
                None, 0, ctypes.byref(returned), None,
            ):
                raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
        finally:
            kernel.CloseHandle(handle)


def _source_repo(root: Path, *, long_path: bool = False, uv_contract: str | None = "1") -> Path:
    repo = root / "source"
    (repo / "worktree-manager").mkdir(parents=True)
    (repo / "worktree-manager" / "README.md").write_text("payload\n", "utf-8")
    scripts = repo / "worktree-manager" / "scripts"
    scripts.mkdir()
    (scripts / "runtime_install.py").write_text(
        "# legacy strict installer\n" if uv_contract is None
        else f"# copilot-extensions:bootstrap-uv={uv_contract}\n", encoding="utf-8",
    )
    if long_path:
        entry = repo / _LONG_SOURCE_PATH
        entry.parent.mkdir(parents=True)
        entry.write_text("long source path\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(repo)], check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    subprocess.run(
        ["git", "-C", str(repo), "-c", "core.longpaths=true", "add", "-A"], check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    subprocess.run(
        ["git", "-C", str(repo), *_GIT_ID, "commit", "-q", "-m", "payload"], check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return repo


def _fake_tools(
    bin_dir: Path, uv_exit: int, python_exit: int, *, uv_version: str = "0.13.7",
) -> None:
    """A fake ``uv`` whose ``python find`` names a fake interpreter."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        python = bin_dir / "fake-python.cmd"
        python.write_text(
            "@echo off\r\n"
            'if defined BOOTSTRAP_PYTHON_HANDOFF >"%BOOTSTRAP_PYTHON_HANDOFF%" '
            "echo %COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON%\r\n"
            'if defined BOOTSTRAP_ARGUMENT_HANDOFF >"%BOOTSTRAP_ARGUMENT_HANDOFF%" echo %*\r\n'
            f"exit /b {python_exit}\r\n",
            "utf-8",
        )
        (bin_dir / "uv.cmd").write_text(
            "@echo off\r\n"
            'if "%~1"=="--version" (\r\n'
            '  if defined BOOTSTRAP_UV_VERSION_TRACE >>"%BOOTSTRAP_UV_VERSION_TRACE%" echo %~f0\r\n'
            f"  echo uv {uv_version}\r\n  exit /b 0\r\n)\r\n"
            "if defined BOOTSTRAP_UV_TRACE (\r\n"
            '  >>"%BOOTSTRAP_UV_TRACE%" echo %~f0^|%*\r\n'
            '  if "%~2"=="install" exit /b 0\r\n'
            '  if not exist "%BOOTSTRAP_UV_TRACE%.seen" (\r\n'
            '    type nul >"%BOOTSTRAP_UV_TRACE%.seen"\r\n'
            "    if defined BOOTSTRAP_UV_LINK (\r\n"
            '      rmdir "%BOOTSTRAP_UV_LINK%" >nul 2>nul\r\n'
            '      mklink /J "%BOOTSTRAP_UV_LINK%" "%BOOTSTRAP_UV_POISON%" >nul\r\n'
            "      if errorlevel 1 exit /b 18\r\n"
            "    )\r\n"
            "    exit /b 3\r\n"
            "  )\r\n"
            ")\r\n"
            f"echo {python}\r\nexit /b {uv_exit}\r\n", "utf-8"
        )
    else:
        python = bin_dir / "fake-python"
        python.write_text(
            "#!/bin/sh\n"
            'if [ -n "${BOOTSTRAP_PYTHON_HANDOFF:-}" ]; then\n'
            '  printf "%s\\n" "$COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON" > "$BOOTSTRAP_PYTHON_HANDOFF"\n'
            "fi\n"
            'if [ -n "${BOOTSTRAP_ARGUMENT_HANDOFF:-}" ]; then\n'
            '  printf "%s\\n" "$@" > "$BOOTSTRAP_ARGUMENT_HANDOFF"\n'
            "fi\n"
            f"exit {python_exit}\n",
            "utf-8",
        )
        python.chmod(0o755)
        uv = bin_dir / "uv"
        uv.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = --version ]; then\n'
            '  if [ -n "${BOOTSTRAP_UV_VERSION_TRACE:-}" ]; then\n'
            '    printf "%s\\n" "$0" >> "$BOOTSTRAP_UV_VERSION_TRACE"\n'
            '  fi\n'
            f'  printf "%s\\n" "uv {uv_version}"; exit 0\nfi\n'
            'if [ -n "${BOOTSTRAP_UV_TRACE:-}" ]; then\n'
            '  printf "%s\\n" "$*" >> "$BOOTSTRAP_UV_TRACE"\n'
            '  if [ "${BOOTSTRAP_UV_MISSING:-0}" = 1 ] && '
            '[ "$(wc -l < "$BOOTSTRAP_UV_TRACE")" -eq 1 ]; then exit 2; fi\n'
            "fi\n"
            f'if [ "$2" = install ]; then exit {uv_exit}; fi\n'
            f"printf '%s\\n' {shlex.quote(str(python))}\nexit {uv_exit}\n",
            "utf-8",
        )
        uv.chmod(0o755)


def _retained_sparse(repo: Path, root: Path) -> None:
    installer = repo / "plugins" / "agent-worktrees" / "scripts" / "install.ps1"
    installer.parent.mkdir(parents=True)
    installer.write_text("# Engine installer fixture\n", encoding="utf-8")
    installer.with_suffix(".sh").write_text("# Engine installer fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), *_GIT_ID, "commit", "-qm", "engine"], check=True)
    staging = root / "staging"
    subprocess.run(["git", "clone", "-q", "--no-checkout", str(repo), str(staging)], check=True)
    sparse = staging / ".git" / "info" / "sparse-checkout"
    sparse.write_text("/worktree-manager/\n/libs/\n/.gitattributes\n", encoding="utf-8")
    subprocess.run([
        "git", "-c", "core.sparseCheckout=true", "-c", "core.sparseCheckoutCone=false",
        "-C", str(staging), "checkout", "-q", "HEAD",
    ], check=True)
    assert not (staging / installer.relative_to(repo)).exists()


def _without_uv(path: str) -> str:
    return os.pathsep.join(
        entry for entry in path.split(os.pathsep)
        if not any((Path(entry) / name).is_file() for name in ("uv", "uv.exe", "uv.cmd", "uv.ps1"))
    )


def _run(
    tmp_path: Path, uv_exit: int, ref: str = "main", python_exit: int = 0,
    *, host: str | None = None, encoded: bool = False, linked_uv: bool = False,
    long_path: bool = False, incomplete_checkout: bool = False,
    longpaths_disabled: bool = False, retained_sparse: bool = False,
    uv_version: str = "0.13.7",
    handoff: bool = False,
    shadow_uv: str | None = None,
    uv_contract: str | None = "1",
) -> subprocess.CompletedProcess[str]:
    repo = _source_repo(tmp_path, long_path=long_path, uv_contract=uv_contract)
    root = tmp_path / "manager-root"
    root.mkdir()
    if retained_sparse:
        _retained_sparse(repo, root)
    (root / "config.toml").write_text(
        f'[source]\nrepo = "{repo.as_posix()}"\nref = "{ref}"\n', "utf-8"
    )
    fake_bin = tmp_path / "fake-bin"
    _fake_tools(fake_bin, uv_exit, python_exit, uv_version=uv_version)
    if incomplete_checkout:
        real_git = shutil.which("git")
        assert real_git is not None
        missing = root / "staging" / "worktree-manager" / "README.md"
        (fake_bin / "git.ps1").write_text(
            f"& {_quote(real_git)} @args\n"
            "$code = $LASTEXITCODE\n"
            "if ($code -eq 0 -and $args -contains 'checkout') {\n"
            f"  Remove-Item -LiteralPath {_quote(missing)}\n"
            "}\n"
            "exit $code\n",
            encoding="utf-8",
        )
    selected_bin = fake_bin
    if linked_uv:
        selected_bin = tmp_path / "linked-tools"
        subprocess.run(
            [os.environ["COMSPEC"], "/d", "/c", "mklink", "/J",
             str(selected_bin), str(fake_bin)],
            capture_output=True, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    env = dict(os.environ)
    env["WORKTREE_MANAGER_ROOT"] = str(root)
    env["USERPROFILE"] = str(tmp_path / "profile")
    if uv_version == "0.7.0":
        env["PATH"] = _without_uv(env.get("PATH", ""))
    env["PATH"] = str(selected_bin) + os.pathsep + env.get("PATH", "")
    if handoff:
        env["COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON"] = "previous-selection"
        env["BOOTSTRAP_PYTHON_HANDOFF"] = str(tmp_path / "python-handoff")
        env["BOOTSTRAP_ARGUMENT_HANDOFF"] = str(tmp_path / "argument-handoff")
    installer_mock = ""
    if shadow_uv:
        shadow = tmp_path / "shadow-tools"
        shadow.mkdir()
        if shadow_uv == "script":
            (shadow / "uv.ps1").write_text("throw 'PowerShell-only UV must not execute'\n", encoding="utf-8")
        elif os.name == "nt":
            reports = {
                "old": "echo uv 0.7.0\r\nexit /b 0\r\n",
                "malformed": "echo not uv\r\nexit /b 0\r\n",
                "failed": "exit /b 17\r\n",
                "policy": "echo WinError 448 1>&2\r\nexit /b 17\r\n",
            }
            (shadow / "uv.cmd").write_text(
                '@echo off\r\n>>"%BOOTSTRAP_UV_VERSION_TRACE%" echo %~f0\r\n' + reports[shadow_uv],
                encoding="utf-8",
            )
        else:
            reports = {
                "old": "printf '%s\\n' 'uv 0.7.0'\n",
                "malformed": "printf '%s\\n' 'not uv'\n",
                "failed": "exit 17\n",
                "policy": "printf '%s\\n' 'WinError 448' >&2\nexit 17\n",
            }
            executable = shadow / "uv"
            executable.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$0" >> "$BOOTSTRAP_UV_VERSION_TRACE"\n' + reports[shadow_uv],
                encoding="utf-8",
            )
            executable.chmod(0o755)
        env["PATH"] = str(shadow) + os.pathsep + env["PATH"]
        env["BOOTSTRAP_UV_VERSION_TRACE"] = str(tmp_path / "uv-versions")
        installer_mock = "function Invoke-RestMethod { throw 'Unexpected UV acquisition' }\n"
    if uv_version == "0.7.0":
        _fake_tools(Path(env["USERPROFILE"]) / ".local" / "bin", uv_exit, python_exit)
        installer_mock = (
            "function Invoke-RestMethod { param([string]$Uri)\n"
            "if ($Uri -cne 'https://astral.sh/uv/install.ps1') { throw 'Unexpected download' }\n"
            f"[IO.File]::WriteAllText({_quote(tmp_path / 'uv-prepared')}, 'prepared')\n"
            "'$null = 0'\n}\n"
        )
    if long_path:
        config = tmp_path / "global.gitconfig"
        config.write_text(
            "[core]\nlongpaths = false\n" if longpaths_disabled else "", encoding="utf-8",
        )
        env["GIT_CONFIG_GLOBAL"] = str(config)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        for key in list(env):
            if key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")) or key == "GIT_CONFIG_COUNT":
                env.pop(key)
    if linked_uv:
        poison = tmp_path / "poison"
        poison.mkdir()
        (poison / "uv.cmd").write_text("@echo off\r\necho UV was reselected 1>&2\r\nexit /b 19\r\n", "utf-8")
        env["BOOTSTRAP_UV_TRACE"] = str(tmp_path / "uv-trace.txt")
        env["BOOTSTRAP_UV_POISON"] = str(poison)
        env["BOOTSTRAP_UV_LINK"] = str(selected_bin)
    command = [host or PWSH, "-NoProfile", "-NonInteractive"]
    if encoded or installer_mock or handoff:
        script = str(BOOTSTRAP).replace("'", "''")
        text = installer_mock + (
            "[Threading.Thread]::CurrentThread.CurrentCulture = "
            "[Globalization.CultureInfo]::GetCultureInfo('de-DE'); "
            f"& '{script}' --version; $code = $LASTEXITCODE; "
            + (
                f"[IO.File]::WriteAllText({_quote(tmp_path / 'restored-python')}, "
                "$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON); "
                if handoff else ""
            )
            + "exit $code"
        )
        command += ["-EncodedCommand", base64.b64encode(text.encode("utf-16le")).decode("ascii")]
    else:
        command += ["-File", str(BOOTSTRAP), "--version"]
    return subprocess.run(
        command,
        capture_output=True, text=True, env=env, timeout=120, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def test_failed_uv_fails_the_bootstrap(tmp_path: Path) -> None:
    result = _run(tmp_path, uv_exit=3)
    assert result.returncode != 0
    assert "Python acquisition failed" in result.stdout + result.stderr


def test_failed_runtime_command_fails_the_bootstrap(tmp_path: Path) -> None:
    result = _run(tmp_path, uv_exit=0, python_exit=4)
    assert result.returncode == 4
    assert "failed (4)" in result.stdout + result.stderr


def test_successful_uv_succeeds(tmp_path: Path) -> None:
    result = _run(tmp_path, uv_exit=0)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("python_exit", [0, 4])
def test_bootstrap_hands_selected_python_to_children_and_restores_caller(
    tmp_path: Path, python_exit: int,
) -> None:
    result = _run(tmp_path, uv_exit=0, python_exit=python_exit, handoff=True)
    assert result.returncode == python_exit, result.stdout + result.stderr
    expected = tmp_path / "fake-bin" / ("fake-python.cmd" if os.name == "nt" else "fake-python")
    assert Path((tmp_path / "python-handoff").read_text(encoding="utf-8").strip()) == expected
    assert (tmp_path / "restored-python").read_text(encoding="utf-8") == "previous-selection"


def test_fresh_bootstrap_restores_engine_sdk_from_retained_sparse_staging(tmp_path: Path) -> None:
    from worktree_manager.core_install import install_script

    result = _run(tmp_path, uv_exit=0, retained_sparse=True)
    assert result.returncode == 0, result.stdout + result.stderr
    staging = tmp_path / "manager-root" / "staging"
    expected = staging / "plugins" / "agent-worktrees" / "scripts" / (
        "install.ps1" if os.name == "nt" else "install.sh"
    )
    assert install_script(staging) == expected


@pytest.mark.skipif(os.name != "nt", reason="native Windows Git long-path checkout")
@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
def test_bootstrap_checks_out_long_source_paths_without_global_git_changes(
    tmp_path: Path, host_name: str,
) -> None:
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    result = _run(tmp_path, uv_exit=0, host=host, long_path=True)
    assert result.returncode == 0, result.stdout + result.stderr
    entry = tmp_path / "manager-root" / "staging" / _LONG_SOURCE_PATH
    assert len(str(entry)) > 260
    assert entry.read_text(encoding="utf-8") == "long source path\n"
    assert "Filename too long" not in result.stdout + result.stderr
    assert (tmp_path / "global.gitconfig").read_text(encoding="utf-8") == ""
    checkout_config = (tmp_path / "manager-root" / "staging" / ".git" / "config").read_text(
        encoding="utf-8",
    )
    assert "longpaths" not in checkout_config.lower()


@pytest.mark.skipif(os.name != "nt", reason="native Windows Git long-path configuration")
def test_bootstrap_preserves_global_disable_without_accepting_partial_source(tmp_path: Path) -> None:
    result = _run(tmp_path, uv_exit=0, long_path=True, longpaths_disabled=True)
    assert (tmp_path / "global.gitconfig").read_text(encoding="utf-8") == (
        "[core]\nlongpaths = false\n"
    )
    if result.returncode == 0:
        entry = tmp_path / "manager-root" / "staging" / _LONG_SOURCE_PATH
        assert entry.read_text(encoding="utf-8") == "long source path\n"
        assert "Filename too long" not in result.stdout + result.stderr
    else:
        assert "source checkout is incomplete or changed" in result.stdout + result.stderr
        assert not (tmp_path / "manager-root" / "current-version").exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows Git script adapter")
def test_bootstrap_refuses_incomplete_source_even_after_zero_exit_checkout(tmp_path: Path) -> None:
    result = _run(tmp_path, uv_exit=0, incomplete_checkout=True)
    assert result.returncode != 0
    assert "source checkout is incomplete or changed" in result.stdout + result.stderr
    assert "refusing Manager construction" in result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction contract")
@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
def test_bootstrap_retains_physical_uv_across_find_install_and_link_change(
    tmp_path: Path, host_name: str,
) -> None:
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    result = _run(tmp_path, uv_exit=0, host=host, linked_uv=True)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "uv-trace.txt").read_text(encoding="utf-8-sig").splitlines()
    expected = str(tmp_path / "fake-bin" / "uv.cmd")
    assert calls == [
        expected + "|python find --no-project --system --managed-python --no-python-downloads 3.11",
        expected + "|python install --no-bin --no-registry 3.11",
        expected + "|python find --no-project --system --managed-python --no-python-downloads 3.11",
    ]
    assert result.stdout == ""


def test_bootstrap_prepares_compatible_uv_before_managed_python(tmp_path: Path) -> None:
    result = _run(tmp_path, uv_exit=0, uv_version="0.7.0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "uv-prepared").read_text(encoding="utf-8") == "prepared"
    assert "uv 0.8.0+ required" in result.stderr


@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
@pytest.mark.parametrize("shadow_uv", ["old", "malformed", "failed", "script"])
def test_bootstrap_reuses_supported_uv_later_on_path(
    tmp_path: Path, host_name: str, shadow_uv: str,
) -> None:
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    result = _run(tmp_path, uv_exit=0, host=host, shadow_uv=shadow_uv, handoff=True)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = tmp_path / "fake-bin" / ("fake-python.cmd" if os.name == "nt" else "fake-python")
    assert Path((tmp_path / "python-handoff").read_text(encoding="utf-8").strip()) == expected
    assert "preparing it" not in result.stderr
    assert (tmp_path / "uv-versions").read_text(encoding="utf-8-sig").splitlines() == [
        *([] if shadow_uv == "script" else [
            str(tmp_path / "shadow-tools" / ("uv.cmd" if os.name == "nt" else "uv")),
        ]),
        str(tmp_path / "fake-bin" / ("uv.cmd" if os.name == "nt" else "uv")),
    ]
    arguments = (tmp_path / "argument-handoff").read_text(encoding="utf-8")
    assert "--bootstrap-uv" in arguments
    assert str(tmp_path / "fake-bin" / ("uv.cmd" if os.name == "nt" else "uv")) in arguments


@pytest.mark.parametrize("uv_contract", [None, "2"])
def test_bootstrap_refuses_unknown_uv_handoff_before_legacy_installation(
    tmp_path: Path, uv_contract: str | None,
) -> None:
    result = _run(tmp_path, uv_exit=0, handoff=True, uv_contract=uv_contract)
    assert result.returncode != 0
    assert "does not support qualified UV handoff" in result.stderr
    assert not (tmp_path / "argument-handoff").exists()
    assert not (tmp_path / "manager-root" / "current-version").exists()


@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
def test_bootstrap_uv_discovery_preserves_explicit_os_policy_refusal(
    tmp_path: Path, host_name: str,
) -> None:
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    result = _run(tmp_path, uv_exit=0, host=host, shadow_uv="policy", handoff=True)
    assert result.returncode != 0
    assert "WinError 448" in result.stderr
    assert "preparing it" not in result.stderr
    assert not (tmp_path / "python-handoff").exists()
    assert len((tmp_path / "uv-versions").read_text(encoding="utf-8-sig").splitlines()) == 1


def _resolver() -> str:
    source = BOOTSTRAP.read_text(encoding="utf-8")
    match = re.search(r"^function Resolve-BootstrapExecutable.*?^}", source, re.M | re.S)
    assert match is not None
    version = re.search(r"^function Test-BootstrapUvVersion\b.*?^}", source, re.M | re.S)
    assert version is not None
    discovery = re.search(r"^function Find-BootstrapUv\b.*?^}", source, re.M | re.S)
    assert discovery is not None
    return "\n".join((match[0], version[0], discovery[0]))

def _python_resolvers() -> str:
    source = BOOTSTRAP.read_text(encoding="utf-8")
    functions = [_resolver()]
    for name in ("Find-PhysicalBootstrapPython", "Find-BootstrapPython"):
        match = re.search(rf"^function {name}\b.*?^}}", source, re.M | re.S)
        assert match is not None
        functions.append(match[0])
    return "\n".join(functions)


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
def test_managed_python_selection_excludes_ambient_python_and_active_venv(
    tmp_path: Path, host_name: str,
) -> None:
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    uv = tmp_path / "uv.ps1"
    selected = tmp_path / "managed-python" / "python.exe"
    uv.write_text(
        "$expected = 'python find --no-project --system --managed-python --no-python-downloads 3.11'\n"
        "if (($args -join ' ') -cne $expected) { throw 'Unqualified interpreter request' }\n"
        f"Write-Output {_quote(selected)}\nexit 0\n",
        encoding="utf-8",
    )
    script = (
        "$ErrorActionPreference = 'Stop'\n" + _python_resolvers() + "\n"
        "function Get-Command { throw 'Ambient Python discovery was attempted' }\n"
        f"$env:VIRTUAL_ENV = {_quote(tmp_path / 'active-venv')}\n"
        f"$env:UV_PYTHON = {_quote(tmp_path / 'ambient-python' / 'python.exe')}\n"
        f"$env:PATH = {_quote(tmp_path / 'older-python')}\n"
        f"Find-BootstrapPython {_quote(uv)}\n"
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == str(selected)


@pytest.mark.skipif(os.name != "nt", reason="native Windows Python alias fallback")
@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
@pytest.mark.parametrize("lookup_error", [448, 2])
def test_failed_managed_python_lookup_preserves_os_refusal_without_ambient_fallback(
    tmp_path: Path, host_name: str, lookup_error: int,
) -> None:
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    uv = tmp_path / "uv.ps1"
    trace = tmp_path / "trace.txt"
    uv.write_text(
        f"Add-Content -LiteralPath {_quote(trace)} -Value ($args -join ' ')\n"
        "if ($args[1] -eq 'find') {\n"
        f"  Write-Error 'interpreter query failed (os error {lookup_error})' -ErrorAction Continue\n"
        "  exit 2\n"
        "}\n"
        "exit 3\n",
        encoding="utf-8",
    )
    script = "$ErrorActionPreference = 'Stop'\n" + _python_resolvers() + "\n"
    script += (
        "function Get-Command { param($Name, $CommandType, [switch]$All, $ErrorAction)\n"
        "  throw 'Ambient Python discovery was attempted'\n"
        "}\n"
        f"$selected = Find-BootstrapPython {_quote(uv)}\n"
        "Write-Output $selected\n"
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    calls = trace.read_text(encoding="utf-8-sig").splitlines()
    assert result.returncode != 0
    lookup = "python find --no-project --system --managed-python --no-python-downloads 3.11"
    if lookup_error == 448:
        assert "blocked by operating-system policy" in result.stdout + result.stderr
        assert calls == [lookup]
    else:
        assert "Python acquisition failed" in result.stdout + result.stderr
        assert calls == [lookup, "python install --no-bin --no-registry 3.11"]


@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
@pytest.mark.parametrize("scenario", ["prepared", "missing", "blocked", "relative"])
def test_managed_python_inspection_never_acquires_or_uses_ambient_python(
    tmp_path: Path, host_name: str, scenario: str,
) -> None:
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    selected = tmp_path / "managed-python" / "python.exe"
    selected.parent.mkdir()
    selected.touch()
    trace = tmp_path / "uv-calls"
    uv = tmp_path / "uv.ps1"
    response = (
        "Write-Error 'No interpreter found for Python 3.11' -ErrorAction Continue; exit 2"
        if scenario == "missing" else
        "Write-Error 'interpreter query denied (os error 448)' -ErrorAction Continue; exit 2"
        if scenario == "blocked" else
        "Write-Output 'relative-python.exe'; exit 0"
        if scenario == "relative" else
        f"Write-Output {_quote(selected)}; exit 0"
    )
    uv.write_text(
        f"Add-Content -LiteralPath {_quote(trace)} -Value ($args -join ' ')\n"
        "if ($args[1] -eq 'install') { throw 'inspection attempted acquisition' }\n"
        + response + "\n", encoding="utf-8",
    )
    script = (
        "$ErrorActionPreference = 'Stop'\n" + _python_resolvers() + "\n"
        "function Get-Command { throw 'inspection attempted ambient discovery' }\n"
        "$selected = $null; $failure = $null\n"
        f"try {{ $selected = Find-BootstrapPython {_quote(uv)} -Operation Inspect }}\n"
        "catch { $failure = $_.Exception.Message }\n"
        "@{python=$selected;error=$failure} | ConvertTo-Json -Compress\n"
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert trace.read_text(encoding="utf-8-sig").splitlines() == [
        "python find --no-project --system --managed-python --no-python-downloads 3.11",
    ]
    if scenario == "prepared":
        assert Path(observed["python"]).samefile(selected)
        assert observed["error"] is None
    else:
        assert observed["python"] is None
        assert observed["error"]
        if scenario == "missing":
            assert "setup" in observed["error"].lower()
        if scenario == "blocked":
            assert "blocked by operating-system policy" in observed["error"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows interpreter protocol")
@pytest.mark.parametrize("report", [
    {"version": [3, 14], "base": False},
    {"version": [3, 10], "base": True},
    {"version": [3, 14], "base": "true"},
    {"version": ["3", "14"], "base": True},
    {"version": [3, 14], "base": True, "executable": "relative-python.exe"},
    'not-json',
])
def test_physical_python_fallback_rejects_venv_old_version_and_bad_protocol(
    tmp_path: Path, report: dict[str, object] | str,
) -> None:
    if isinstance(report, dict):
        report = json.dumps({"executable": getattr(sys, "_base_executable", sys.executable), **report})
    fake = tmp_path / "python.ps1"
    fake.write_text(f"Write-Output {_quote(report)}\nexit 0\n", encoding="utf-8")
    script = "$ErrorActionPreference = 'Stop'\n" + _python_resolvers() + "\n"
    script += (
        "function Get-Command { param($Name, $CommandType, [switch]$All, $ErrorAction)\n"
        f"  [pscustomobject]@{{ Source = {_quote(fake)} }}\n"
        "}\n"
        "if (Find-PhysicalBootstrapPython) { throw 'Invalid interpreter was accepted' }\n"
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("after_install", [False, True])
def test_python_lookup_rejects_empty_success(tmp_path: Path, after_install: bool) -> None:
    uv = tmp_path / "uv.ps1"
    trace = tmp_path / "calls.txt"
    uv.write_text(
        f"Add-Content -LiteralPath {_quote(trace)} -Value ($args -join ' ')\n"
        "if ($args[1] -eq 'install') { Write-Output 'installation progress'; exit 0 }\n"
        f"if (${str(after_install).lower()} -and @(Get-Content {_quote(trace)}).Count -eq 1) "
        "{ Write-Error 'No Python found' -ErrorAction Continue; exit 2 }\n"
        "Write-Output ''; exit 0\n",
        encoding="utf-8",
    )
    script = _python_resolvers() + f"\nFind-BootstrapPython {_quote(uv)}\n"
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != 0
    expected = "uv-managed Python 3.11" if after_install else "invalid executable"
    assert expected in result.stdout + result.stderr
    assert not result.stdout.strip()


def test_managed_python_lookup_preserves_post_install_failure(tmp_path: Path) -> None:
    uv = tmp_path / "uv.ps1"
    uv.write_text(
        "if ($args[1] -eq 'install') { exit 0 }\n"
        "Write-Error 'Managed interpreter query denied' -ErrorAction Continue\n"
        "exit 2\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command",
         _python_resolvers() + f"\nFind-BootstrapPython {_quote(uv)}\n"],
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != 0
    assert "Managed interpreter query denied" in result.stderr
    assert not result.stdout.strip()


@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
@pytest.mark.parametrize("operation", ["version", "post-install"])
def test_managed_python_native_diagnostics_are_not_result_records(
    tmp_path: Path, host_name: str, operation: str,
) -> None:
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    selected = str(Path(sys.executable))
    marker = tmp_path / "lookup"
    program = tmp_path / "uv.py"
    program.write_text(
        "import pathlib,sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('uv 0.13.7')\n"
        "elif sys.argv[2] == 'install':\n"
        "    sys.exit(0)\n"
        f"elif not pathlib.Path({str(marker)!r}).exists():\n"
        f"    pathlib.Path({str(marker)!r}).touch(); sys.exit(2)\n"
        f"else: print({selected!r})\n"
        "sys.stderr.write('uv debug diagnostic\\n')\n",
        encoding="utf-8",
    )
    uv = tmp_path / ("uv.cmd" if os.name == "nt" else "uv")
    uv.write_text(
        f'@"{sys.executable}" -I -S -B "{program}" %*\n'
        if os.name == "nt" else
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -I -S -B {shlex.quote(str(program))} \"$@\"\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        uv.chmod(0o755)
    action = "Test-BootstrapUvVersion" if operation == "version" else "Find-BootstrapPython"
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command",
         _python_resolvers() + f"\n{action} {_quote(uv)}\n"],
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == ("True" if operation == "version" else selected)


@pytest.mark.skipif(os.name != "nt", reason="native Windows RedirectionGuard")
def test_unprivileged_link_creator_restores_thread_identity_after_failure(tmp_path: Path) -> None:
    import ctypes

    shell = getattr(ctypes, "WinDLL")("shell32", use_last_error=True)
    original = bool(shell.IsUserAnAdmin())
    with pytest.raises(RuntimeError, match="fixture failure"):
        with _unprivileged_link_creator(tmp_path):
            assert not shell.IsUserAnAdmin()
            raise RuntimeError("fixture failure")
    assert bool(shell.IsUserAnAdmin()) == original


@pytest.mark.skipif(os.name != "nt", reason="native Windows RedirectionGuard")
@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
def test_guarded_legacy_system_python_lookup_reuses_real_physical_python_twice(
    tmp_path: Path, host_name: str,
) -> None:
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    physical = Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)
    linked = tmp_path / "python-link"
    _create_untrusted_junction(physical.parent, linked)
    logical = linked / physical.name
    script = "$ErrorActionPreference = 'Stop'\n" + _python_resolvers() + "\n"
    script += r"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class BootstrapPythonGuard {
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool GetProcessMitigationPolicy(
        IntPtr process, int policy, out uint flags, UIntPtr size);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool SetProcessMitigationPolicy(int policy, ref uint flags, UIntPtr size);
}
'@
$size = [UIntPtr]::new([uint32]4)
$before = [uint32]0
if (-not [BootstrapPythonGuard]::GetProcessMitigationPolicy(
    [IntPtr](-1), 16, [ref]$before, $size)) { throw 'Cannot inspect caller guard' }
$requested = [uint32]($before -bor 1)
if (-not [BootstrapPythonGuard]::SetProcessMitigationPolicy(
    16, [ref]$requested, $size)) { throw 'Cannot enable isolated caller guard' }
"""
    script += (
        "function Get-Command { param($Name, $CommandType, [switch]$All, $ErrorAction)\n"
        f"  [pscustomobject]@{{ Source = {_quote(logical)} }}\n"
        "}\n"
        f"$logical = {_quote(logical)}\n"
        f"$expected = {_quote(physical)}\n"
        "foreach ($cycle in 1..2) {\n"
        "  $blocked = $false\n"
        "  try { & $logical --version 2>$null | Out-Null } catch { $blocked = $true }\n"
        "  if (-not $blocked) { throw 'Logical interpreter was not guarded' }\n"
        "  $selected = Find-PhysicalBootstrapPython\n"
        "  if ($selected -ine $expected) { throw 'Wrong physical interpreter selected' }\n"
        "}\n"
        "$after = [uint32]0\n"
        "if (-not [BootstrapPythonGuard]::GetProcessMitigationPolicy("
        "[IntPtr](-1), 16, [ref]$after, $size)) { throw 'Cannot recheck guard' }\n"
        "if ($after -ne $requested) { throw 'Caller mitigation changed' }\n"
        "Write-Output 'physical-python-ready'\n"
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "physical-python-ready"


@pytest.mark.skipif(os.name != "nt", reason="native Windows RedirectionGuard contract")
@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
@pytest.mark.parametrize("link_kind", ["junction", "file"])
def test_physical_uv_runs_twice_while_logical_uv_is_guarded(
    tmp_path: Path, host_name: str, link_kind: str,
) -> None:
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    uv = shutil.which("uv")
    assert uv is not None, "contained runner must provide UV"
    configuration = BOOTSTRAP.parents[1] / "libs" / "installer-engine" / "installer_dependency_config.py"
    spec = importlib.util.spec_from_file_location("bootstrap_uv_configuration", configuration)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    selected = module.physical_executable(uv)
    if selected.startswith("\\\\?\\UNC\\"):
        selected = "\\\\" + selected[8:]
    elif selected.startswith("\\\\?\\"):
        selected = selected[4:]
    physical = Path(selected)
    logical = tmp_path / "uv-link"
    if link_kind == "junction":
        _create_untrusted_junction(physical.parent, logical)
    else:
        logical.mkdir()
        try:
            with _unprivileged_link_creator(logical):
                os.symlink(physical, logical / physical.name)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                pytest.skip("file symlink creation requires Windows developer mode or privilege")
            raise
    script = (
        "$ErrorActionPreference = 'Stop'\n" + _resolver() + r"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class BootstrapGuard {
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool GetProcessMitigationPolicy(
        IntPtr process, int policy, out uint flags, UIntPtr size);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool SetProcessMitigationPolicy(int policy, ref uint flags, UIntPtr size);
}
'@
$before = [uint32]0
$size = [UIntPtr]::new([uint32]4)
if (-not [BootstrapGuard]::GetProcessMitigationPolicy(
    [IntPtr](-1), 16, [ref]$before, $size)) { throw 'Cannot read caller guard' }
$requested = [uint32]($before -bor 1)
if (-not [BootstrapGuard]::SetProcessMitigationPolicy(
    16, [ref]$requested, $size)) { throw 'Cannot enable isolated caller guard' }
"""
        + f"$logical = {_quote(logical / physical.name)}\n"
        + f"$expected = {_quote(physical)}\n"
        + "$env:PATH = [IO.Path]::GetDirectoryName($logical) + ';' + $env:PATH\n"
        + "$Uv = Find-BootstrapUv\n"
        + r"""
$selected = $Uv
if ($selected -ine $expected) { throw "Not the selected physical UV: $selected (expected $expected)" }
foreach ($cycle in 1..2) {
    $blocked = $false
    try { & $logical --version 2>$null | Out-Null } catch { $blocked = $true }
    if (-not $blocked) { throw 'Logical UV unexpectedly started under guard' }
    $version = & $selected --version
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^uv ') { throw 'Physical UV failed' }
}
$after = [uint32]0
if (-not [BootstrapGuard]::GetProcessMitigationPolicy(
    [IntPtr](-1), 16, [ref]$after, $size) -or $after -ne $requested) {
    throw 'Caller guard changed'
}
"""
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("kind", ["missing", "directory", "cycle", "unsupported"])
def test_bootstrap_executable_refuses_invalid_targets(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "tool"
    if kind == "directory":
        target.mkdir()
    mock = ""
    if kind in ("cycle", "unsupported"):
        value = _quote(target) if kind == "cycle" else "$null"
        mock = f"""
function Get-Item {{
    param($LiteralPath, [switch]$Force, $ErrorAction)
    return [pscustomobject]@{{
        Attributes = [IO.FileAttributes]::ReparsePoint
        Target = {value}
    }}
}}
"""
    script = "$ErrorActionPreference = 'Stop'\n" + _resolver() + mock
    script += f"\nResolve-BootstrapExecutable {_quote(target)}\n"
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != 0
    assert result.stdout == ""
    expected = {
        "missing": "does not exist",
        "directory": "does not identify a regular file",
        "cycle": "excessive indirection",
        "unsupported": "unsupported link",
    }
    assert expected[kind] in result.stderr


def test_failed_git_fetch_fails_the_bootstrap(tmp_path: Path) -> None:
    result = _run(tmp_path, uv_exit=0, ref="no-such-branch")
    assert result.returncode != 0
    assert "git fetch of" in result.stdout + result.stderr


@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
@pytest.mark.parametrize("encoded", [False, True])
def test_bootstrap_progress_is_once_on_stderr_without_information_records(
    tmp_path: Path, host_name: str, encoded: bool,
) -> None:
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    result = _run(tmp_path, uv_exit=0, host=host, encoded=encoded)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr.count("Worktree Manager bootstrap") == 1
    assert "HostInformationMessage" not in result.stderr
    progress = [
        line for line in result.stderr.splitlines()
        if "update/bootstrap " in line
    ]
    assert len(progress) == 2, result.stderr
    assert " running Worktree Manager bootstrap " in progress[0]
    assert " warning Add " in progress[1]
    for line in progress:
        assert re.fullmatch(
            r"\[\d{2}:\d{2}:\d{2}\.\d{3}Z\] update/bootstrap "
            r"(?:running|warning) .+ \[elapsed \d+\.\d+s\]", line,
        ), line
    elapsed = []
    for line in progress:
        match = re.search(r"\[elapsed (\d+\.\d+)s\]$", line)
        assert match is not None
        elapsed.append(float(match[1]))
    assert elapsed == sorted(elapsed)
