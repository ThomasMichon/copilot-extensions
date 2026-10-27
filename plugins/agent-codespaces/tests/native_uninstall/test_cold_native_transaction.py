"""Cold native admission and the existing inode owner's attached FIFO transport."""

# ruff: noqa: S101
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

SOURCE = Path(__file__).resolve().parents[4]
SCRIPTS = SOURCE / "plugins" / "agent-codespaces" / "scripts"
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _bash() -> str:
    """Prefer actual stock Darwin Bash, or an explicitly supplied native binary."""
    override = os.environ.get("NATIVE_UNINSTALL_BASH")
    if override:
        candidate = Path(override)
    elif sys.platform == "darwin":
        candidate = Path("/bin/bash")
    elif os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parent.parent / "bin/bash.exe" if git else Path("missing")
    else:
        found = shutil.which("bash")
        candidate = Path(found) if found else Path("missing")
    if not candidate.is_file():
        pytest.skip("native Bash unavailable")
    return str(candidate)


def _q(value: Path | str) -> str:
    text = str(value).replace("\\", "/")
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        text = "/" + text[0].lower() + text[2:]
    return "'" + text.replace("'", "'\\''") + "'"


def _env(home: Path, path: str | None = None) -> dict[str, str]:
    result = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_", "PYTHON", "UV_"))
        and key not in ("VIRTUAL_ENV", "BASH_ENV", "ENV", "SHELLOPTS")
    }
    home.mkdir(parents=True, exist_ok=True)
    result.update(HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1")
    if path is not None:
        result["PATH"] = path
    return result


def _run(script: Path, home: Path, *, path: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash(), str(script)],
        cwd=script.parent,
        env=_env(home, path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        creationflags=FLAGS,
    )


def _frame(tmp_path: Path, root: Path, body: str, *, fallback: bool = False) -> Path:
    script = tmp_path / "transaction.sh"
    script.write_text(
        f"""set -euo pipefail
SCRIPT_DIR={_q(SCRIPTS)}
INSTALL_DIR={_q(root)}
COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC=1
COPILOT_EXT_NO_FLOCK={"1" if fallback else "0"}
_bootstrap_python() {{ printf '%s' {_q(sys.executable)}; }}
source "$SCRIPT_DIR/install-transaction.sh"
{body}
""",
        encoding="utf-8",
    )
    return script


@contextmanager
def _owner(root: Path) -> Iterator[None]:
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", str(SCRIPTS / "install-transaction.py"), str(root), "2"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=FLAGS,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        yield
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stdout + stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX root mode and caller umask")
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_group_writable_umask_creates_private_root_but_refuses_existing_unsafe_root(
    tmp_path: Path, fallback: bool, existing: bool,
) -> None:
    root = tmp_path / "install"
    if existing:
        root.mkdir(mode=0o775)
        root.chmod(0o775)
        (root / "retained").write_bytes(b"operator data")
    frame = _frame(
        tmp_path, root,
        "umask 0002\nenter_codespaces_install_transaction\n"
        "exit_codespaces_install_transaction\n",
        fallback=fallback,
    )
    result = _run(frame, tmp_path / "home")
    if existing:
        assert result.returncode != 0, result.stdout + result.stderr
        assert root.stat().st_mode & 0o777 == 0o775
        assert (root / "retained").read_bytes() == b"operator data"
        assert not (root / ".install.lock").exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert root.stat().st_mode & 0o777 == 0o700
        assert (root / ".install.lock").is_file()
        assert not list(root.glob(".install-lock-channel.*"))


def _root(tmp_path: Path) -> tuple[Path, Path, int]:
    root = tmp_path / "install"
    root.mkdir()
    lock = root / ".install.lock"
    lock.write_bytes(b"persistent transaction inode")
    return root, lock, lock.stat().st_ino


def _native_path(tmp_path: Path, *, flock: bool) -> Path:
    """Expose native tools but no Python, package manager or ambient launcher."""
    directory = tmp_path / "native-path"
    directory.mkdir()
    names = (
        "awk",
        "basename",
        "cat",
        "chmod",
        "cp",
        "cut",
        "date",
        "dirname",
        "dscl",
        "env",
        "find",
        "getent",
        "git",
        "head",
        "id",
        "ln",
        "ls",
        "mkdir",
        "mkfifo",
        "mktemp",
        "mv",
        "readlink",
        "realpath",
        "rm",
        "rmdir",
        "sed",
        "sha256sum",
        "sh",
        "shasum",
        "sleep",
        "sort",
        "stat",
        "tail",
        "tr",
        "uname",
    )
    for name in (*names, *(("flock",) if flock else ())):
        executable = shutil.which(name)
        if executable:
            (directory / name).symlink_to(executable)
    (directory / "bash").symlink_to(_bash())
    return directory


def _declared_library_closure(project: Path) -> set[Path]:
    """Follow only bounded, repository-contained local library declarations."""
    project = project.resolve(strict=True)
    pending = [project]
    visited: set[Path] = set()
    libraries: set[Path] = set()
    while pending:
        parent = pending.pop()
        if parent in visited:
            continue
        assert len(visited) < 128, "fixture local dependency closure exceeded its bound"
        visited.add(parent)
        document = tomllib.loads((parent / "pyproject.toml").read_text(encoding="utf-8"))
        mappings = document.get("tool", {}).get("uv", {}).get("sources", {})
        assert isinstance(mappings, dict)
        for name, entries in mappings.items():
            for entry in entries if isinstance(entries, list) else [entries]:
                if not isinstance(entry, dict) or "path" not in entry:
                    continue
                relative = entry["path"]
                assert isinstance(relative, str) and not Path(relative).is_absolute()
                library = (parent / relative).resolve(strict=True)
                assert library.is_relative_to(SOURCE / "libs") or library.is_relative_to(
                    project / "libs"
                ), f"fixture dependency escapes declared library roots: {name}"
                manifest = tomllib.loads((library / "pyproject.toml").read_text(encoding="utf-8"))
                assert manifest["project"]["name"].lower().replace("_", "-") == (
                    name.lower().replace("_", "-")
                ), f"fixture dependency identity mismatch: {name}"
                libraries.add(library)
                if library not in visited:
                    pending.append(library)
    return libraries


def _payload(tmp_path: Path, plugin: str) -> Path:
    original = SOURCE / "plugins" / plugin
    source = tmp_path / "source"
    payload = source / "plugins" / plugin
    for name in ("scripts", "src"):
        shutil.copytree(
            original / name,
            payload / name,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    shutil.copytree(
        SOURCE / "libs" / "installer-engine",
        source / "libs" / "installer-engine",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("plugin.json", "pyproject.toml", "payload-invocation.json"):
        shutil.copyfile(original / name, payload / name)
    for library in sorted(_declared_library_closure(original)):
        shutil.copytree(
            library,
            source / library.relative_to(SOURCE),
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache", ".git"),
        )
    return payload


@pytest.mark.parametrize("plugin", ("agent-codespaces", "agent-ssh", "agent-pull-requests"))
def test_payload_contains_exact_declared_library_closure(tmp_path: Path, plugin: str) -> None:
    original = SOURCE / "plugins" / plugin
    expected = _declared_library_closure(original)
    payload = _payload(tmp_path, plugin)
    fixture_source = tmp_path / "source"
    for library in expected:
        copied = fixture_source / library.relative_to(SOURCE)
        assert (copied / "pyproject.toml").read_bytes() == (
            library / "pyproject.toml"
        ).read_bytes()
    for parent in (original, *sorted(expected)):
        mappings = (
            tomllib.loads((parent / "pyproject.toml").read_text(encoding="utf-8"))
            .get("tool", {})
            .get("uv", {})
            .get("sources", {})
        )
        for entries in mappings.values():
            for entry in entries if isinstance(entries, list) else [entries]:
                if isinstance(entry, dict) and "path" in entry:
                    actual = (parent / entry["path"]).resolve(strict=True)
                    copied = (fixture_source / parent.relative_to(SOURCE) / entry["path"]).resolve(
                        strict=True
                    )
                    assert copied.is_relative_to(fixture_source)
                    assert (copied / "pyproject.toml").read_bytes() == (
                        actual / "pyproject.toml"
                    ).read_bytes()
    expected_shared = {
        library.relative_to(SOURCE).parts[1]
        for library in expected
        if library.is_relative_to(SOURCE / "libs")
    } | {"installer-engine"}
    assert {
        path.name for path in (fixture_source / "libs").iterdir() if path.is_dir()
    } == expected_shared
    assert (payload / "pyproject.toml").read_bytes() == (original / "pyproject.toml").read_bytes()


def test_native_language_parse_and_status_without_any_lock_capability(tmp_path: Path) -> None:
    host = _bash()
    parsed = subprocess.run(
        [host, "-n", str(SCRIPTS / "install-transaction.sh")],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        creationflags=FLAGS,
    )
    assert parsed.returncode == 0, parsed.stderr
    root = tmp_path / "absent"
    script = _frame(
        tmp_path,
        root,
        "if codespaces_requires_install_transaction status false; then exit 7; fi\nprintf status",
    )
    result = _run(script, tmp_path / "home", path="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "status"
    assert not root.exists()


def test_actual_bash32_language_and_status(tmp_path: Path) -> None:
    version = subprocess.run(
        [_bash(), "-c", 'printf "%s" "$BASH_VERSION"'],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        creationflags=FLAGS,
    )
    assert version.returncode == 0, version.stderr
    if not re.match(r"^3\.2[.(-]", version.stdout):
        pytest.skip(f"actual Bash 3.2 unavailable; selected native version is {version.stdout}")
    test_native_language_parse_and_status_without_any_lock_capability(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="actual POSIX entrypoint/status lane")
def test_real_status_needs_neither_python_nor_flock(tmp_path: Path) -> None:
    payload = _payload(tmp_path, "agent-codespaces")
    home = tmp_path / "home"
    root = home / ".agent-codespaces"
    path = _native_path(tmp_path, flock=False)
    result = subprocess.run(
        [_bash(), str(payload / "scripts/install.sh"), "status", "--install-dir", str(root)],
        cwd=tmp_path,
        env=_env(home, str(path)),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not root.exists()


def test_neither_native_flock_nor_python_explicitly_refuses(tmp_path: Path) -> None:
    root, lock, inode = _root(tmp_path)
    script = _frame(
        tmp_path,
        root,
        "_bootstrap_python() { return 1; }\n"
        "enter_codespaces_install_transaction || exit 73\nprintf falsely-admitted",
    )
    result = _run(script, tmp_path / "home", path="")
    assert result.returncode != 0
    assert "native flock or existing Python" in result.stderr
    assert "falsely-admitted" not in result.stdout
    assert lock.stat().st_ino == inode
    assert lock.read_bytes() == b"persistent transaction inode"


@pytest.mark.skipif(os.name == "nt", reason="requires the actual native POSIX flock capability")
def test_native_flock_cold_admission_is_python_free_and_preserves_inode(tmp_path: Path) -> None:
    if not shutil.which("flock"):
        pytest.skip("native flock unavailable; the Python-free capability is not present")
    root, lock, inode = _root(tmp_path)
    path = _native_path(tmp_path, flock=True)
    tripwire = tmp_path / "unexpected-python"
    script = _frame(
        tmp_path,
        root,
        f"_bootstrap_python() {{ printf invoked > {_q(tripwire)}; return 1; }}\n"
        "enter_codespaces_install_transaction || exit 73\n"
        '[[ -n "${CODESPACES_INSTALL_LOCK_FD:-}" ]] || exit 74\n'
        "exit_codespaces_install_transaction\nprintf admitted",
    )
    with _owner(root):
        busy = _run(script, tmp_path / "home", path=str(path))
        assert busy.returncode != 0
        assert "admitted" not in busy.stdout
    result = _run(script, tmp_path / "home", path=str(path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "admitted"
    assert not tripwire.exists()
    assert lock.stat().st_ino == inode
    assert lock.read_bytes() == b"persistent transaction inode"


@pytest.mark.skipif(os.name == "nt", reason="real POSIX cold stamp needs native flock")
@pytest.mark.parametrize("plugin", ("agent-codespaces", "agent-ssh", "agent-pull-requests"))
def test_real_python_free_cold_stamp_with_native_flock(tmp_path: Path, plugin: str) -> None:
    if not shutil.which("flock"):
        pytest.skip("native flock unavailable; no Python-free stamp qualification")
    payload = _payload(tmp_path, plugin)
    home = tmp_path / "home"
    root = home / ("." + plugin)
    path = _native_path(tmp_path, flock=True)
    tripwire = tmp_path / "unexpected-uv"
    uv = path / "uv"
    uv.write_text("#!/bin/sh\nprintf invoked > " + _q(tripwire) + "\nexit 91\n", encoding="utf-8")
    uv.chmod(0o755)
    environment = _env(home, str(path))
    result = subprocess.run(
        [_bash(), str(payload / "scripts/install.sh"), "stamp", "--install-dir", str(root)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".local/bin" / plugin).is_file()
    assert "provision" in (home / ".local/bin" / plugin).read_text(encoding="utf-8")
    assert (root / "payload-dir").is_file()
    assert (root / ".install.lock").is_file()
    assert not tripwire.exists()
    assert not (root / ".venv").exists()
    assert not (root / "versions").exists()


def test_python_fifo_transport_admission_contention_and_eof(tmp_path: Path) -> None:
    root, lock, inode = _root(tmp_path)
    script = _frame(
        tmp_path,
        root,
        "enter_codespaces_install_transaction || exit 73\n"
        "trap 'exit_codespaces_install_transaction' EXIT\n"
        "printf 'admitted\\n'\nIFS= read -r finish || :\n",
        fallback=True,
    )
    with _owner(root):
        busy = _run(script, tmp_path / "home")
        assert busy.returncode != 0
        assert "admitted" not in busy.stdout
    process = subprocess.Popen(
        [_bash(), str(script)],
        cwd=tmp_path,
        env=_env(tmp_path / "home"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=FLAGS,
    )
    try:
        assert process.stdout is not None
        admitted = process.stdout.readline().strip()
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate(timeout=8)
    assert admitted == "admitted", stdout + stderr
    assert process.returncode == 0, stdout + stderr
    with _owner(root):
        assert lock.stat().st_ino == inode
    assert lock.read_bytes() == b"persistent transaction inode"
    assert not list(root.glob(".install-lock-channel.*"))


def test_waiting_python_owner_settles_when_caller_closes_pipe(tmp_path: Path) -> None:
    root, lock, inode = _root(tmp_path)
    with _owner(root):
        waiting = subprocess.Popen(
            [sys.executable, "-I", "-B", str(SCRIPTS / "install-transaction.py"), str(root), "30"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=FLAGS,
        )
        assert waiting.stdin is not None
        waiting.stdin.close()
        waiting.stdin = None
        stdout, stderr = waiting.communicate(timeout=5)
        assert waiting.returncode == 0, stdout + stderr
        assert "locked" not in stdout
    assert lock.stat().st_ino == inode
    assert lock.read_bytes() == b"persistent transaction inode"
