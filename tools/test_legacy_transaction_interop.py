"""Both legacy publisher/removal backends must lock the same persistent inode."""
from __future__ import annotations

import os
import select
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PRODUCTS = ("agent-machines", "agent-containers")
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
BASH = str(GIT_BASH) if GIT_BASH.is_file() else shutil.which("bash")
LINUX_FLOCK = sys.platform.startswith("linux") and BASH is not None and shutil.which("flock") is not None


def _posix(path: Path) -> str:
    value = path.as_posix()
    return "/" + value[0].lower() + value[2:] if os.name == "nt" else value


def _script(tmp_path: Path, product: str, root: Path, body: str) -> Path:
    helper = REPO / "plugins" / product / "scripts" / "legacy-transaction.sh"
    script = tmp_path / ("gate-" + str(len(list(tmp_path.glob("gate-*")))) + ".sh")
    script.write_text("\n".join((
        "set -euo pipefail",
        f"INSTALL_DIR={shlex.quote(_posix(root))}",
        f". {shlex.quote(_posix(helper))}",
        "trap '_exit_install_lock' EXIT",
        body,
    )), encoding="utf-8", newline="\n")
    return script


def _environment(fallback: bool) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COPILOT_PLUGIN_", "COPILOT_EXTENSIONS_", "AGENT_MACHINES_", "AGENT_CONTAINERS_"))
           and key not in {"BASH_ENV", "ENV"}}
    env["COPILOT_EXT_NO_FLOCK"] = "1" if fallback else "0"
    env["COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC"] = "1"
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    return env


def _run(script: Path, fallback: bool) -> subprocess.CompletedProcess[str]:
    if not BASH:
        pytest.skip("Bash unavailable")
    return subprocess.run(  # noqa: S603 - fixed owned gate fixture
        [BASH, _posix(script)], env=_environment(fallback), capture_output=True,
        input="", text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.skipif(not LINUX_FLOCK, reason="Real Linux flock/fcntl interoperability")
@pytest.mark.parametrize("product", PRODUCTS)
@pytest.mark.parametrize("holder_fallback", (False, True))
def test_actual_native_and_python_backends_contend_in_both_directions(
    tmp_path: Path, product: str, holder_fallback: bool,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    lock = root / ".install.lock"
    lock.write_bytes(b"persistent lock contents")
    before = lock.stat().st_ino, lock.read_bytes()
    holder_script = _script(tmp_path, product, root, "\n".join((
        "_enter_install_lock 2",
        "printf 'held\\n'",
        "IFS= read -r answer || :",
    )))
    holder = subprocess.Popen(  # noqa: S603 - owned finite holder with explicit EOF release
        [BASH, str(holder_script)], env=_environment(holder_fallback),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert select.select([holder.stdout], [], [], 5)[0], "Holder failed to report admission"
        assert holder.stdout.readline().strip() == "held"
        contender = _script(
            tmp_path, product, root,
            "if _enter_install_lock 1; then echo incorrectly-admitted; exit 0; else exit 42; fi",
        )
        result = _run(contender, not holder_fallback)
        assert result.returncode == 42, result.stdout + result.stderr
        assert holder.poll() is None
        assert (lock.stat().st_ino, lock.read_bytes()) == before
        assert not (root / ".install.lock.pid").exists()
    finally:
        stdout, stderr = holder.communicate("\n", timeout=5)
        assert holder.returncode == 0, stdout + stderr
    released = _run(_script(tmp_path, product, root, "_enter_install_lock 1"), not holder_fallback)
    assert released.returncode == 0, released.stdout + released.stderr
    assert (lock.stat().st_ino, lock.read_bytes()) == before


@pytest.mark.parametrize("product", PRODUCTS)
@pytest.mark.parametrize("name", (".install.lock.pid", ".install.lock.pid.reap"))
@pytest.mark.parametrize("content", ("live", "unknown"))
@pytest.mark.parametrize("fallback", (False, True))
def test_legacy_receipts_are_preserved_and_refused_before_any_backend(
    tmp_path: Path, product: str, name: str, content: str, fallback: bool,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    receipt = root / name
    receipt.write_text(
        f"copilot-extensions.install-lock-pid:v1:{os.getpid()}\n" if content == "live"
        else "unverified legacy reservation\n", encoding="ascii",
    )
    before = receipt.read_bytes(), receipt.stat().st_ino, receipt.stat().st_mtime_ns
    script = _script(tmp_path, product, root, "\n".join((
        "flock() { echo backend-must-not-be-called; return 0; }",
        "_enter_install_lock 1",
    )))
    result = _run(script, fallback)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "backend-must-not-be-called" not in result.stdout
    assert (receipt.read_bytes(), receipt.stat().st_ino, receipt.stat().st_mtime_ns) == before
    assert not (root / ".install.lock").exists()


@pytest.mark.parametrize("product", PRODUCTS)
def test_sourcing_gate_is_readonly_and_does_not_create_missing_root(tmp_path: Path, product: str) -> None:
    root = tmp_path / "absent"
    result = _run(_script(tmp_path, product, root, "echo loaded-only"), True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not root.exists()


@pytest.mark.parametrize("product", PRODUCTS)
def test_missing_both_primitives_fails_closed_and_closes_fd8(tmp_path: Path, product: str) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    lock = root / ".install.lock"
    lock.write_bytes(b"persistent contents")
    before = lock.read_bytes(), lock.stat().st_ino
    result = _run(_script(tmp_path, product, root, "\n".join((
        "type() { return 1; }",
        "if _enter_install_lock 1; then echo incorrectly-admitted; exit 9; fi",
        '[[ ! -e /dev/fd/8 ]] || { echo descriptor-leaked; exit 8; }',
        "echo refused",
    ))), True)
    assert result.returncode == 0 and result.stdout.strip() == "refused", result.stdout + result.stderr
    assert "no provisioning attempted" in result.stderr
    assert (lock.read_bytes(), lock.stat().st_ino) == before
    assert not (root / ".install.lock.pid").exists()


@pytest.mark.skipif(not LINUX_FLOCK, reason="Real Linux kernel lock release")
@pytest.mark.parametrize("product", PRODUCTS)
@pytest.mark.parametrize("fallback", (False, True))
@pytest.mark.parametrize("exit_mode", ("failure", "eof"))
def test_owned_gate_releases_after_failure_or_stdin_eof(
    tmp_path: Path, product: str, fallback: bool, exit_mode: str,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    lock = root / ".install.lock"
    lock.write_bytes(b"unchanged")
    before = lock.read_bytes(), lock.stat().st_ino
    action = "exit 23" if exit_mode == "failure" else "IFS= read -r line"
    result = _run(_script(tmp_path, product, root, "_enter_install_lock 1\n" + action), fallback)
    assert result.returncode == (23 if exit_mode == "failure" else 1)
    retry = _run(_script(tmp_path, product, root, "_enter_install_lock 1"), not fallback)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert (lock.read_bytes(), lock.stat().st_ino) == before


@pytest.mark.skipif(not LINUX_FLOCK, reason="Actual Linux native publishers and kernel contention")
@pytest.mark.parametrize("product", PRODUCTS)
@pytest.mark.parametrize("publisher_fallback", (False, True))
def test_actual_init_stamp_publisher_obeys_opposite_backend_holder(
    tmp_path: Path, product: str, publisher_fallback: bool,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    holder_script = _script(tmp_path, product, root, "\n".join((
        "_enter_install_lock 2",
        "printf 'held\\n'",
        "IFS= read -r answer || :",
    )))
    holder = subprocess.Popen(  # noqa: S603 - exact fixture holder; EOF is its release
        [BASH, str(holder_script)], env=_environment(not publisher_fallback),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert select.select([holder.stdout], [], [], 5)[0]
        assert holder.stdout.readline().strip() == "held"
        env = _environment(publisher_fallback)
        env.update(HOME=str(home), USERPROFILE=str(home))
        publisher = subprocess.run(  # noqa: S603 - actual native stamp publisher in an owned root
            [BASH, str(REPO / "plugins" / product / "scripts" / "init.sh"),
             "stamp", "--install-dir", str(root)],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
        )
        assert publisher.returncode != 0, publisher.stdout + publisher.stderr
        assert "transaction lock" in publisher.stdout + publisher.stderr
        assert not (root / "payload-dir").exists()
        assert not (root / "versions").exists()
        assert holder.poll() is None
    finally:
        stdout, stderr = holder.communicate("\n", timeout=5)
        assert holder.returncode == 0, stdout + stderr
