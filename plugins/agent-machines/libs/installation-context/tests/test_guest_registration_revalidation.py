"""Inject publication at real lock admission, retaining native readers/writers."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from test_guest_environment_posix import _initialized_host, _library
from _powershell_fixture import _inject_owner_hook

pytestmark = [
    pytest.mark.skipif(os.name != "posix", reason="native POSIX registration boundary"),
    pytest.mark.installation_context_smoke,
]


def _publish_at_lock_entry(library: Path) -> None:
    _inject_owner_hook(library, """
native_enter = _DirectoryLock.__enter__
def enter(lock):
    result = native_enter(lock)
    registration = json.loads(Path(os.environ['GUEST_TEST_REGISTRATION']).read_text())
    _atomic_write_json(
        Path(os.environ['HOME']) / '.copilot-extensions' / 'guest-environment.json',
        registration, lock=lock,
    )
    return result
_DirectoryLock.__enter__ = enter
""")

    bash = library / "guest-bash.sh"
    source = bash.read_text(encoding="utf-8")
    marker = "\nacquire_lock() {"
    assert source.count(marker) == 1
    source = source.replace(marker, "\nnative_acquire_lock() {", 1)
    marker = "\nACTION="
    assert source.count(marker) == 1
    bash.write_text(source.replace(marker, """
acquire_lock() {
    native_acquire_lock "$@"
    atomic_write_json "$HOME/.copilot-extensions/guest-environment.json" "$(cat "$GUEST_TEST_REGISTRATION")"
}
""" + marker, 1), encoding="utf-8")


@pytest.mark.parametrize("writer", ["python", "bash", "powershell"])
@pytest.mark.parametrize("competing", ["Other Distro", "EXAMPLE DISTRO"])
def test_registration_revalidates_publication_after_initial_read(
    writer: str, competing: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bash, powershell = shutil.which("bash"), shutil.which("pwsh")
    if bash is None or powershell is None:
        pytest.skip("native Bash and PowerShell are required")
    profile = tmp_path / "isolated profile"
    profile.mkdir()
    library = _library(tmp_path, profile)
    _publish_at_lock_entry(library)
    registration = {
        "schema": "copilot-extensions.guest-environment", "version": 1,
        "distribution": competing, "machineId": "a" * 32,
        "homeRealPath": str(profile),
    }
    publication = tmp_path / "publication.json"
    publication.write_text(json.dumps(registration), encoding="utf-8")
    _fixture, host, environment = _initialized_host(
        powershell, profile, {
            "HOME": str(profile), "USERPROFILE": str(profile),
            "WSL_DISTRO_NAME": "Example Distro",
            "GUEST_TEST_REGISTRATION": str(publication),
        }, monkeypatch,
    )
    lookup = profile / "test-bin" / "getent"
    lookup.write_text(
        "#!/bin/sh\n"
        f'[ "$#" -eq 2 ] && [ "$1" = passwd ] && [ "$2" = {os.getuid()} ] || exit 1\n'
        f"printf '%s\\n' {shlex.quote(f'fixture:x:{os.getuid()}:{os.getgid()}:fixture:{profile}:/bin/sh')}\n",
        encoding="utf-8",
    )
    lookup.chmod(0o755)
    try:
        if writer == "powershell":
            wrapper = profile / "register.ps1"
            wrapper.write_text(
                "& '" + str(library / "guest-powershell.ps1").replace("'", "''")
                + "' 'guest-register' -Distribution 'Example Distro'\nexit $LASTEXITCODE\n",
                encoding="utf-8",
            )
            result = host.run([powershell, "-NoProfile", "-File", str(wrapper)])
        else:
            command = (
                [sys.executable, str(library / "guest-python.py")]
                if writer == "python" else [bash, str(library / "guest-bash.sh")]
            )
            result = subprocess.run(
                [*command, "guest-register", "--distribution", "Example Distro"],
                env=environment, cwd=library, capture_output=True, text=True, timeout=20,
            )
        path = profile / ".copilot-extensions" / "guest-environment.json"
        assert json.loads(path.read_text(encoding="utf-8")) == registration
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        if competing == "Other Distro":
            assert result.returncode == 1, result.stdout + result.stderr
            assert "guest-identity-conflict" in result.stderr + "".join(host.errors)
        else:
            assert result.returncode == 0, result.stdout + result.stderr + "".join(host.errors)
            response = json.loads(result.stdout)
            assert response["created"] is False
            assert response["environment"]["wslDistro"] == competing
        assert not (path.parent / ".guest-environment.lock").exists()
    finally:
        host.close()
