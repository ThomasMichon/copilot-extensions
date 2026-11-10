"""Read-only admission of Vault's canonical same-process Windows daemon."""

from __future__ import annotations

import ast
import ctypes
import json
import os
from pathlib import Path
import re
import sys


PAYLOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PAYLOAD / "scripts"))


def _read(path: Path, limit: int) -> str:
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("uninstall identity input exceeds its bound")
    return content.decode("utf-8-sig")


def _path(value: str) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("invalid native identity path")
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("native identity requires an absolute non-traversing path")
    return path


def _member(value: str, root: Path) -> Path:
    path = _path(value)
    path.relative_to(root)
    for member in (path, *path.parents):
        if member.lstat().st_file_attributes & 0x400:
            raise ValueError("redirected native identity member")
        if member == root:
            return path
    raise ValueError("native identity member escaped its root")


def _constant(path: Path, name: str) -> str:
    values = [
        ast.literal_eval(node.value)
        for node in ast.parse(_read(path, 1024 * 1024)).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    ]
    if len(values) != 1 or not isinstance(values[0], str):
        raise ValueError("canonical launch contract is unavailable")
    return values[0]


def _contracts() -> tuple[str, str]:
    import installer_engine as engine

    packages = {
        engine._package_name(package): package for package in engine._package_roots(PAYLOAD)
    }
    return (
        _constant(
            packages["agent-procutil"] / "src/agent_procutil/_daemon_instance_child.py", "_BOOTSTRAP",
        ),
        _constant(PAYLOAD / "src/agent_vault/launch.py", "_SERVICE_BOOTSTRAP"),
    )


def _arguments(command: str) -> list[str]:
    if not isinstance(command, str) or not command or len(command) > 32767 or "\0" in command:
        raise ValueError("invalid Windows command line")
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    parse = shell.CommandLineToArgvW
    parse.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    free = kernel.LocalFree
    free.argtypes, free.restype = [ctypes.c_void_p], ctypes.c_void_p
    count = ctypes.c_int()
    result = parse(command, ctypes.byref(count))
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [result[index] for index in range(count.value)]
    finally:
        free(result)


def admits(request: dict) -> bool:
    if not isinstance(request, dict) or set(request) != {"root", "image", "command"}:
        return False
    try:
        arguments = _arguments(request["command"])
        if len(arguments) not in (10, 13):
            return False
        daemon, service = _contracts()
        if arguments[1] != "-X":
            return False
        invocation = arguments[3:]
        bootstrap_package = None
        if len(arguments) == 13:
            if invocation[:2] != ["-c", daemon]:
                return False
            bootstrap_package = _path(invocation[2])
            invocation = invocation[3:]
        if invocation[:2] != ["-c", service] or invocation[3:6] != [
            "--foreground", "--persistent", "--configuration-root",
        ]:
            return False
        root = _path(request["root"])
        package = _member(invocation[2], root)
        if bootstrap_package is not None and bootstrap_package != package:
            return False
        if tuple(part.casefold() for part in package.parts[-2:]) != (
            "lib", "site-packages",
        ):
            return False
        slot = package.parent.parent
        if slot.parent != root / "versions" and slot != root / ".venv":
            return False
        _path(invocation[6])
        prefix = arguments[2].removeprefix("pycache_prefix=")
        if prefix == arguments[2]:
            return False
        cache = _path(prefix)
        if cache.parent != root / "cache/python" or not re.fullmatch(
            r"(?:installed-[0-9a-f]{64}|unsealed-[0-9a-f]{32})", cache.name,
        ):
            return False
        config = _member(str(slot / "pyvenv.cfg"), root)
        homes = [
            line.split("=", 1)[1].strip()
            for line in _read(config, 65536).splitlines()
            if re.match(r"^home\s*=", line)
        ]
        if len(homes) != 1:
            return False
        base = _path(homes[0]) / "python.exe"
        return base.samefile(_path(request["image"])) and base.samefile(_path(arguments[0]))
    except (OSError, ValueError, TypeError, KeyError, SyntaxError):
        return False


def main() -> int:
    if os.name != "nt":
        raise ValueError("native Vault process admission requires Windows")
    content = sys.stdin.buffer.read(65537)
    if len(content) > 65536:
        raise ValueError("native Vault process observation exceeds its bound")
    print(json.dumps(admits(json.loads(content))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
