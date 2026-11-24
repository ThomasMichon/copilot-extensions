"""Run Python with extra requirements prepared under the seasoning policy.

`tools/run-python --with <requirement>...` delegates here. The requirements
are installed into a cached virtual environment through the canonical
seven-day configured-resolver policy (`tools/seasoned_uv.py`), never through a
plain `uv run --with`. The environment uses the interpreter that runs this
file, which `tools/run-python` selects from the repository's `.python-version`.

Usage: run_python_with.py [--with <requirement>...] [--] <python arguments...>
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".venv-tools" / "run-python"
_READY = ".run-python-ready"


def parse(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split leading `--with` requirements from the Python arguments.

    An optional `--` ends the requirements; the first other argument starts
    the Python arguments.
    """
    requirements: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            return requirements, argv[index + 1:]
        if argument == "--with" and index + 1 < len(argv):
            requirements.append(argv[index + 1])
            index += 2
            continue
        if argument.startswith("--with="):
            requirements.append(argument[len("--with="):])
            index += 1
            continue
        break
    return requirements, argv[index:]


def environment_key(requirements: list[str]) -> str:
    digest = hashlib.sha256(b"run-python-with-v1\n")
    digest.update(f"{sys.implementation.name}-{sys.version_info.major}.{sys.version_info.minor}\n".encode())
    for requirement in sorted(set(requirements)):
        digest.update(f"{requirement}\n".encode())
    return digest.hexdigest()[:16]


def _python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def prepare(uv: str, requirements: list[str], cache: Path = CACHE) -> Path:
    """Return the Python of a seasoned environment holding the requirements."""
    venv = cache / environment_key(requirements)
    if (venv / _READY).is_file():
        return _python(venv)
    sys.path.insert(0, str(ROOT / "tools"))
    from seasoned_uv import DependencyPolicyError, run_uv_install

    cache.mkdir(parents=True, exist_ok=True)
    staging = cache / f".staging-{uuid.uuid4().hex}"
    try:
        subprocess.run(
            [uv, "venv", "--quiet", "--relocatable", "--python", sys.executable, str(staging)],
            check=True, stdout=subprocess.DEVNULL,
        )
        try:
            run_uv_install(
                uv, ["--python", str(_python(staging)), *requirements],
                trusted_source_roots=(ROOT,), check=True,
            )
        except DependencyPolicyError as error:
            raise RuntimeError(f"dependency-seasoning: {error}") from error
        (staging / _READY).write_text("\n".join(requirements) + "\n", encoding="utf-8")
        try:
            staging.rename(venv)
        except OSError:
            # Another run prepared the same environment first.
            if not (venv / _READY).is_file():
                raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return _python(venv)


def main(argv: list[str] | None = None) -> int:
    requirements, arguments = parse(list(sys.argv[1:] if argv is None else argv))
    uv = os.environ.get("UV") or shutil.which("uv")
    if not uv:
        print("run-python: uv is required, but 'uv' was not found on PATH.", file=sys.stderr)
        return 1
    if not requirements:
        return subprocess.run([sys.executable, *arguments]).returncode
    try:
        python = prepare(uv, requirements)
    except subprocess.CalledProcessError as error:
        print(f"run-python: preparing --with requirements failed (exit {error.returncode}).", file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as error:
        print(f"run-python: preparing --with requirements failed: {error}", file=sys.stderr)
        return 1
    return subprocess.run([str(python), *arguments]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
