"""Copy, project-owned install, then publication for the isolated uv example."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import tomllib
import uuid
from pathlib import Path
from urllib.request import urlopen

KINDS = {"service": "services", "tool": "tools"}


def child_options() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0}


def stage(repository: str, ref: str, destination: Path, *, paths: tuple[str, ...] = ()) -> bool:
    """Use Git's shallow selected-ref checkout, or reuse an existing directory."""
    if destination.exists():
        return False
    destination.mkdir(parents=True)
    try:
        commands = [
            ["init", str(destination)],
            ["-C", str(destination), "fetch", "--depth", "1", repository, ref],
        ]
        if paths:
            commands.append(["-C", str(destination), "sparse-checkout", "set", "--no-cone", *paths])
        commands.append(["-C", str(destination), "checkout", "--detach", "FETCH_HEAD"])
        for args in commands:
            subprocess.run(
                ["git", "-c", "core.longpaths=true", *args],
                check=True, capture_output=True, timeout=120, **child_options(),
            )
    except Exception:
        destination.rename(destination.with_name(destination.name + ".failed." + uuid.uuid4().hex))
        raise
    return True


def identity(project: Path) -> tuple[str, str]:
    data = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    name, version = data["name"], data["version"]
    if not name.startswith("uv-slot-example-") or not all(
        re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.+-]*", value) for value in (name, version)
    ):
        raise ValueError("only synthetic uv-slot-example projects are supported")
    return name, version


def _write(path: Path, text: str) -> None:
    candidate = path.with_name(path.name + ".new")
    candidate.write_text(text, encoding="utf-8", newline="\n")
    candidate.replace(path)


def launcher(home: Path, kind: str, name: str) -> Path:
    """Publish marker-only global wrappers; uv belongs to the slot wrappers."""
    import shlex
    root = home / KINDS[kind] / name
    directory = home / "bin"
    directory.mkdir(parents=True, exist_ok=True)
    ps_root = "'" + str(root).replace("'", "''") + "'"
    cmd_root = str(root).replace("%", "%%")
    wrappers = {
        "ps1": (
            f"$root = {ps_root}\n"
            "$version = (Get-Content -LiteralPath (Join-Path $root 'current-version') -Raw).Trim()\n"
            "$slot = Join-Path (Join-Path $root 'versions') $version\n"
            "& (Join-Path $slot 'bin\\invoke.ps1') @args\nexit $LASTEXITCODE\n"
        ),
        "cmd": (
            "@echo off\nsetlocal EnableExtensions DisableDelayedExpansion\n"
            'set "VERSION="\n'
            f'set /p "VERSION="<"{cmd_root}\\current-version"\n'
            "if not defined VERSION exit /b 1\n"
            f'call "{cmd_root}\\versions\\%VERSION%\\bin\\invoke.cmd" %*\n'
            "exit /b %errorlevel%\n"
        ),
        "sh": (
            "#!/bin/sh\n"
            f"root={shlex.quote(root.as_posix())}\n"
            'version=$(cat "$root/current-version") || exit\n'
            'exec "$root/versions/$version/bin/invoke.sh" "$@"\n'
        ),
    }
    for extension, text in wrappers.items():
        binary = directory / (name + "." + extension)
        _write(binary, text)
        if extension == "sh":
            binary.chmod(0o700)
    return directory / (name + (".ps1" if os.name == "nt" else ".sh"))


def copy_candidate(project: Path, home: Path, kind: str) -> dict:
    """Copy a ready project without publishing any global invocation state."""
    started = time.monotonic()
    name, version = identity(project)
    root = home / KINDS[kind] / name
    slot = root / "versions" / version
    complete = ".install-complete"
    copied = False
    if slot.exists() and not (slot / complete).exists():
        slot.rename(slot.with_name(slot.name + ".failed." + uuid.uuid4().hex))
    if not slot.exists():
        if not (project / "uv.lock").is_file():
            raise ValueError("bootstrap requires an already locked install-source project")
        slot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            project, slot,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.egg-info"),
        )
        copied = True
    return {
        "name": name, "version": version, "slot": str(slot),
        "copied": copied,
        "environment_present": (slot / ".venv").exists(),
        "copy_seconds": round(time.monotonic() - started, 6),
    }


def uv_command(slot: Path, kind: str, *args: str) -> list[str]:
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("uv must already be installed")
    return [
        uv, "run", "--locked", "--no-dev", "--no-editable", "--link-mode", "hardlink",
        "--project", str(slot), "python", "-m", "example_" + kind, *args,
    ]


def environment(home: Path) -> dict:
    result = os.environ.copy()
    for key in (
        "UV_PROJECT_ENVIRONMENT", "UV_NO_SYNC", "UV_NO_PROJECT", "UV_WORKING_DIR",
        "UV_FROZEN", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
    ):
        result.pop(key, None)
    result["UV_CACHE_DIR"] = str(home / "cache" / "uv")
    return result


def install_candidate(candidate: dict, home: Path, kind: str, *, fail: bool = False) -> dict:
    """uv initializes the candidate; publish only after its install command succeeds."""
    slot = Path(candidate["slot"])
    complete = slot / ".install-complete"
    was_complete = complete.exists()
    started = time.monotonic()
    args = ["install", "--fail"] if fail else ["install"]
    try:
        result = subprocess.run(
            uv_command(slot, kind, *args), env=environment(home), capture_output=True,
            text=True, check=True, timeout=120, **child_options(),
        )
    except Exception:
        if not was_complete:
            slot.rename(slot.with_name(slot.name + ".failed." + uuid.uuid4().hex))
        raise
    install_seconds = time.monotonic() - started
    started = time.monotonic()
    binary = launcher(home, kind, candidate["name"])
    _write(home / KINDS[kind] / candidate["name"] / "current-version", candidate["version"])
    if not complete.exists():
        _write(complete, "Project install succeeded; binstub and current version published.\n")
    return {
        **candidate, "binstub": str(binary), "install_seconds": round(install_seconds, 6),
        "publish_seconds": round(time.monotonic() - started, 6),
        "environment_present": (slot / ".venv").exists(),
        "install_stdout": result.stdout, "install_stderr": result.stderr,
    }


def bootstrap(project: Path, home: Path, kind: str, *, fail: bool = False) -> dict:
    return install_candidate(copy_candidate(project, home, kind), home, kind, fail=fail)


def command(binary: Path) -> list[str]:
    if binary.suffix == ".ps1":
        return [
            shutil.which("pwsh") or "powershell.exe", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(binary),
        ]
    if binary.suffix == ".cmd":
        return [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c", "call", str(binary)]
    if os.name == "nt":
        git = shutil.which("git")
        bash = Path(git).parents[1] / "bin" / "bash.exe" if git else None
        if bash is None or not bash.is_file():
            raise FileNotFoundError("Git Bash is unavailable for the SH wrapper witness")
        return [str(bash), binary.as_posix()]
    return [str(binary)]


def hardlink_evidence(library: Path, cache: Path) -> dict:
    """Observe file identity, not package/content authenticity."""
    result = {"link_count": library.stat().st_nlink}
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.FindFirstFileNameW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint), ctypes.c_wchar_p,
        ]
        kernel.FindFirstFileNameW.restype = ctypes.c_void_p
        kernel.FindNextFileNameW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.c_wchar_p,
        ]
        kernel.FindClose.argtypes = [ctypes.c_void_p]
        size = ctypes.c_uint(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        handle = kernel.FindFirstFileNameW("\\\\?\\" + str(library), 0, ctypes.byref(size), buffer)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            while True:
                candidate = Path(library.drive + buffer.value)
                if candidate.is_relative_to(cache) and candidate.samefile(library):
                    result.update(cache_path=str(candidate), same_file=True)
                    break
                size.value = len(buffer)
                if not kernel.FindNextFileNameW(handle, ctypes.byref(size), buffer):
                    break
        finally:
            kernel.FindClose(handle)
    return result


def run_service(home: Path, name: str, *, fail: bool = False, run_dir: Path) -> dict:
    """Exercise a finite real listener, then settle its caller-owned process."""
    root = home / "services" / name
    version = (root / "current-version").read_text().strip()
    binary = home / "bin" / (name + (".ps1" if os.name == "nt" else ".sh"))
    run_dir.mkdir(parents=True, exist_ok=False)
    ready, stop = run_dir / "ready.json", run_dir / "stop"
    argv = command(binary) + [
        "--ready-file", str(ready), "--stop-file", str(stop), "--lifetime", "30",
    ]
    if fail:
        argv.append("--fail")
    started = time.monotonic()
    process = None
    details = {}
    service_handle = None
    with (run_dir / "stdout.log").open("wb") as stdout, (
        run_dir / "stderr.log"
    ).open("wb") as stderr:
        try:
            process = subprocess.Popen(
                argv, cwd=run_dir, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                **child_options(),
            )
            while not ready.exists():
                if process.poll() is not None:
                    raise RuntimeError(f"binstub exited {process.returncode} before readiness")
                if time.monotonic() - started > 120:
                    raise TimeoutError("first uv run did not become ready within 120 seconds")
                time.sleep(0.05)
            details = json.loads(ready.read_text(encoding="utf-8"))
            if os.name == "nt":
                import ctypes
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.restype = ctypes.c_void_p
                kernel.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
                kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                service_handle = kernel.OpenProcess(0x100000, False, details["pid"])
                if not service_handle:
                    raise OSError("could not retain owned synthetic service process handle")
            url = f"http://127.0.0.1:{details['port']}/health"
            with urlopen(url, timeout=5) as response:
                body = json.load(response)
            if body["pid"] != details["pid"] or body["version"] != version:
                raise RuntimeError("unexpected synthetic service response")
            details["ready_seconds"] = round(time.monotonic() - started, 3)
            details["library_links"] = Path(details["library"]).stat().st_nlink
            details["cache"] = str(home / "cache" / "uv")
            details["hardlink"] = hardlink_evidence(
                Path(details["library"]), home / "cache" / "uv",
            )
        finally:
            stop.touch()
            if process is not None:
                try:
                    process.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True, timeout=10, **child_options(),
                        )
                    else:
                        process.terminate()
                    process.wait(timeout=10)
            if service_handle:
                try:
                    if kernel.WaitForSingleObject(service_handle, 5000) != 0:
                        raise RuntimeError("owned service process did not exit")
                    details["service_handle_signaled"] = True
                finally:
                    kernel.CloseHandle(service_handle)
    if process.returncode:
        raise RuntimeError(f"binstub exited {process.returncode} during cleanup")
    try:
        with urlopen(url, timeout=1):
            raise RuntimeError("listener still responds after owned process exit")
    except OSError:
        details["listener_closed"] = True
    details["launcher_exited"] = True
    details["total_seconds"] = round(time.monotonic() - started, 3)
    return details


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    fetch = sub.add_parser("stage")
    fetch.add_argument("--repository", required=True)
    fetch.add_argument("--ref", required=True)
    fetch.add_argument("--dest", type=Path, required=True)
    install = sub.add_parser("bootstrap")
    install.add_argument("--project", type=Path, required=True)
    install.add_argument("--home", type=Path, required=True)
    install.add_argument("--kind", choices=KINDS, required=True)
    install.add_argument("--fail-install", action="store_true")
    serve = sub.add_parser("run")
    serve.add_argument("--home", type=Path, required=True)
    serve.add_argument("--name", default="uv-slot-example-service")
    serve.add_argument("--run-dir", type=Path, required=True)
    serve.add_argument("--fail", action="store_true")
    args = parser.parse_args()
    if args.action == "stage":
        result = {"copied": stage(args.repository, args.ref, args.dest.resolve())}
    elif args.action == "bootstrap":
        result = bootstrap(
            args.project.resolve(), args.home.resolve(), args.kind, fail=args.fail_install,
        )
    else:
        result = run_service(
            args.home.resolve(), args.name, fail=args.fail, run_dir=args.run_dir.resolve()
        )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
