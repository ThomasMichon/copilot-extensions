"""Native POSIX adapters share registration with only OS observations substituted."""
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

from test_guest_environment_native import _initialized_host
from _powershell_fixture import _copy_sdk, _inject_owner_hook

LIB = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX guest adapter boundary")


def _snapshot(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): (
            path.is_dir(), path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in [root, *root.rglob("*")]
    }


def _library(root: Path, profile: Path) -> Path:
    library = _copy_sdk(root / "library")
    _inject_owner_hook(library, """
import pwd
_native_getpwuid = pwd.getpwuid
def _fixture_getpwuid(uid):
    record = _native_getpwuid(uid)
    return pwd.struct_passwd((*record[:5], os.environ['HOME'], record.pw_shell))
pwd.getpwuid = _fixture_getpwuid
_is_wsl_guest = lambda: True
_guest_machine_id = lambda: 'a' * 32
""")
    (library / "guest-python.py").write_text(
        "import importlib.util,os,pwd,sys\n"
        "from pathlib import Path\n"
        "original=pwd.getpwuid\n"
        "def account(uid):\n"
        "    record=original(uid)\n"
        "    return pwd.struct_passwd((*record[:5],os.environ['HOME'],record.pw_shell))\n"
        "pwd.getpwuid=account\n"
        "path=Path(__file__).with_name('installation_context.py')\n"
        "spec=importlib.util.spec_from_file_location('guest_cli',path)\n"
        "module=importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name]=module\n"
        "spec.loader.exec_module(module)\n"
        "module._is_wsl_guest=lambda:True\n"
        "module._guest_machine_id=lambda:'a'*32\n"
        "raise SystemExit(module.main(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    bash = (library / "installation-context.sh").read_text(encoding="utf-8")
    assert bash.count("\nACTION=") == 1
    prefix, main = bash.split("\nACTION=", 1)
    (library / "guest-bash.sh").write_text(
        prefix + "\nguest_is_wsl() { return 0; }\n"
        "guest_machine_id() { GUEST_MACHINE_ID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; }\nACTION=" + main,
        encoding="utf-8",
    )
    shutil.copyfile(library / "installation-context.ps1", library / "guest-powershell.ps1")
    return library


@pytest.mark.installation_context_smoke
@pytest.mark.parametrize("writer", ["python", "bash", "powershell"])
def test_native_guest_writers_and_readers_preserve_identity_and_refusals(
    writer: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bash, powershell = shutil.which("bash"), shutil.which("pwsh")
    if bash is None or powershell is None:
        pytest.skip("native Bash and PowerShell are required")
    profile = tmp_path / "space ' profile"
    profile.mkdir()
    library = _library(tmp_path, profile)
    distribution = "Example Distro"
    fixture, host, environment = _initialized_host(
        powershell, profile,
        {"HOME": str(profile), "USERPROFILE": str(profile), "WSL_DISTRO_NAME": distribution},
        monkeypatch,
    )
    lookup = profile / "test-bin" / "getent"
    lookup.write_text(
        "#!/bin/sh\n"
        f'[ "$#" -eq 2 ] && [ "$1" = passwd ] && [ "$2" = {os.getuid()} ] || exit 1\n'
        f"printf '%s\\n' {shlex.quote(f'fixture:x:{os.getuid()}:{os.getgid()}:fixture:{profile}:/bin/sh')}\n",
        encoding="utf-8",
    )
    lookup.chmod(0o755)
    wrappers = {}
    for entry, observation in (("direct", distribution), ("ssh", None), ("conflict", "Other Distro")):
        wrapper = profile / f"{entry}.ps1"
        command = (
            "& '" + str(library / "guest-powershell.ps1").replace("'", "''")
            + "' 'guest-status' -Distribution 'Example Distro'"
        )
        context = (
            "$env:WSL_DISTRO_NAME='" + observation.replace("'", "''") + "'"
            if observation is not None else "Remove-Item Env:WSL_DISTRO_NAME -ErrorAction SilentlyContinue"
        )
        wrapper.write_text(context + "\n" + command + "\nexit $LASTEXITCODE\n", encoding="utf-8")
        wrappers[entry] = [powershell, "-NoProfile", "-File", str(wrapper)]
    registration_wrapper = profile / "register.ps1"
    registration_wrapper.write_text(
        "$env:WSL_DISTRO_NAME='Example Distro'\n"
        "& '" + str(library / "guest-powershell.ps1").replace("'", "''")
        + "' 'guest-register' -Distribution 'Example Distro'\nexit $LASTEXITCODE\n",
        encoding="utf-8",
    )

    def run(runner: str, action: str, observation: str | None) -> subprocess.CompletedProcess:
        if runner == "powershell":
            command = (
                [powershell, "-NoProfile", "-File", str(registration_wrapper)]
                if action == "guest-register" else wrappers[
                    "ssh" if observation is None else ("direct" if observation == distribution else "conflict")
                ]
            )
            return host.run(command)
        child_environment = {**environment}
        if observation is None:
            child_environment.pop("WSL_DISTRO_NAME", None)
        else:
            child_environment["WSL_DISTRO_NAME"] = observation
        command = (
            [sys.executable, str(library / "guest-python.py")]
            if runner == "python" else [bash, str(library / "guest-bash.sh")]
        )
        return subprocess.run(
            [*command, action, "--distribution", distribution],
            env=child_environment, cwd=library, capture_output=True, text=True, timeout=20,
        )

    try:
        unregistered = _snapshot(profile)
        for runner in ("python", "bash", "powershell"):
            refused = run(runner, "guest-status", None)
            assert refused.returncode == 1, refused.stdout + refused.stderr + "".join(host.errors)
            assert "guest-identity-unregistered" in refused.stderr
            assert _snapshot(profile) == unregistered
        created = run(writer, "guest-register", distribution)
        assert created.returncode == 0, created.stdout + created.stderr + "".join(host.errors)
        registration = json.loads(created.stdout)
        assert registration["created"] is True
        path = profile / ".copilot-extensions" / "guest-environment.json"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        before = _snapshot(profile)
        for runner in ("python", "bash", "powershell"):
            for _ in range(2):
                for observation in (distribution, None):
                    result = run(runner, "guest-status", observation)
                    assert result.returncode == 0, result.stdout + result.stderr + "".join(host.errors)
                    status = json.loads(result.stdout)
                    assert status["environment"] == registration["environment"]
                    assert status["registrationState"] == "registered"
                    assert status["status"] == "ready"
                    assert _snapshot(profile) == before
            refused = run(runner, "guest-status", "Other Distro")
            assert refused.returncode == 1
            assert "guest-identity-conflict" in refused.stderr
            assert _snapshot(profile) == before

        data = json.loads(path.read_text(encoding="utf-8"))
        if writer == "python":
            for version in (True, 2, 1.0):
                malformed = {**data, "version": version}
                path.write_text(json.dumps(malformed), encoding="utf-8")
                invalid = _snapshot(profile)
                for runner in ("python", "bash", "powershell"):
                    refused = run(runner, "guest-status", None)
                    assert refused.returncode == 1
                    assert _snapshot(profile) == invalid
        data["machineId"] = "0" * 32
        path.write_text(json.dumps(data), encoding="utf-8")
        foreign = _snapshot(profile)
        for runner in ("python", "bash", "powershell"):
            refused = run(runner, "guest-status", None)
            assert refused.returncode == 1
            assert "guest-identity-foreign" in refused.stderr
            assert _snapshot(profile) == foreign
    finally:
        host.close()
    assert not host.errors
    assert _snapshot(profile) == foreign
