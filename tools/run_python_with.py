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


# Settings that change where uv finds Python or caches files, not what it
# resolves. Every other UV_* or PIP_* variable can change the resolution.
_NON_RESOLVER_SETTINGS = frozenset({
    "UV", "UV_CACHE_DIR", "UV_NO_PROGRESS", "UV_LINK_MODE", "UV_PYTHON_INSTALL_DIR",
    "UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "UV_CONCURRENT_DOWNLOADS", "UV_CONCURRENT_INSTALLS",
    "UV_HTTP_TIMEOUT", "UV_NATIVE_TLS",
})


def _config_files(environment: dict[str, str]) -> list[Path]:
    """Return the uv configuration files that can affect a resolution."""
    explicit = environment.get("UV_CONFIG_FILE")
    if explicit:
        return [Path(explicit)]
    files = [Path.cwd() / "uv.toml", ROOT / "uv.toml", ROOT / "pyproject.toml"]
    if os.name == "nt":
        for variable in ("APPDATA", "PROGRAMDATA"):
            base = environment.get(variable)
            if base:
                files.append(Path(base) / "uv" / "uv.toml")
    else:
        config = environment.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        files += [Path(config) / "uv" / "uv.toml", Path("/etc/uv/uv.toml")]
    return files


def environment_key(requirements: list[str], environment: dict[str, str] | None = None) -> str:
    """Bind a cached environment to its requirements, interpreter and resolver settings."""
    environment = dict(os.environ if environment is None else environment)
    digest = hashlib.sha256(b"run-python-with-v2\n")
    digest.update(f"{sys.implementation.name}-{sys.version_info.major}.{sys.version_info.minor}\n".encode())
    for requirement in sorted(set(requirements)):
        digest.update(f"requirement={requirement}\n".encode())
    for name in sorted(environment):
        if name.startswith(("UV_", "PIP_")) and name not in _NON_RESOLVER_SETTINGS:
            digest.update(f"env={name}={environment[name]}\n".encode())
    for path in _config_files(environment):
        try:
            content = path.read_bytes()
        except OSError:
            continue
        digest.update(f"config={path}\n".encode())
        digest.update(hashlib.sha256(content).hexdigest().encode())
    return digest.hexdigest()[:16]


def is_local(requirement: str) -> bool:
    """Return whether a requirement names local source that can change in place."""
    value = requirement.strip()
    if value.startswith(("-e", "--editable", "file:", ".", "~")) or "://" not in value and (
        "/" in value or "\\" in value or (len(value) > 1 and value[1] == ":")
    ):
        return True
    return Path(value).exists()


def _python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _build(uv: str, requirements: list[str], target: Path) -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    from seasoned_uv import DependencyPolicyError, run_uv_install

    subprocess.run(
        [uv, "venv", "--quiet", "--relocatable", "--python", sys.executable, str(target)],
        check=True, stdout=subprocess.DEVNULL,
    )
    try:
        run_uv_install(
            uv, ["--python", str(_python(target)), *requirements],
            trusted_source_roots=(ROOT,), check=True,
        )
    except DependencyPolicyError as error:
        raise RuntimeError(f"dependency-seasoning: {error}") from error


def prepare(uv: str, requirements: list[str], cache: Path = CACHE) -> Path:
    """Return the Python of a cached seasoned environment holding the requirements.

    Use `ephemeral` instead when a requirement is local source.
    """
    venv = cache / environment_key(requirements)
    if (venv / _READY).is_file():
        return _python(venv)
    cache.mkdir(parents=True, exist_ok=True)
    staging = cache / f".staging-{uuid.uuid4().hex}"
    try:
        _build(uv, requirements, staging)
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


def ephemeral(uv: str, requirements: list[str], cache: Path = CACHE) -> Path:
    """Build a one-use environment; the caller removes its parent directory."""
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f".ephemeral-{uuid.uuid4().hex}"
    try:
        _build(uv, requirements, target)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return _python(target)

def main(argv: list[str] | None = None) -> int:
    requirements, arguments = parse(list(sys.argv[1:] if argv is None else argv))
    uv = os.environ.get("UV") or shutil.which("uv")
    if not uv:
        print("run-python: uv is required, but 'uv' was not found on PATH.", file=sys.stderr)
        return 1
    if not requirements:
        return subprocess.run([sys.executable, *arguments]).returncode
    local = any(is_local(requirement) for requirement in requirements)
    try:
        # Local source can change without changing the requirement text, so
        # it is never reused from the cache.
        python = (ephemeral if local else prepare)(uv, requirements)
    except subprocess.CalledProcessError as error:
        print(f"run-python: preparing --with requirements failed (exit {error.returncode}).", file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as error:
        print(f"run-python: preparing --with requirements failed: {error}", file=sys.stderr)
        return 1
    try:
        return subprocess.run([str(python), *arguments]).returncode
    finally:
        if local:
            shutil.rmtree(python.parent.parent, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
