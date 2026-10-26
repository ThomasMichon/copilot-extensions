"""Actual finite Fleet CLI processes against the real synthetic TLS receiver."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import shlex
import subprocess
from urllib.parse import urlsplit

from agent_procutil import no_window_kwargs
import pytest

from fleet_manager import planning
from fleet_manager.continuity_bootstrap import _state, bootstrap
from fleet_manager import private_state
from fleet_manager.gateway_service_server import STATE, start
from fleet_manager.ledger import OperationLedger
from gateway_service_transport_fixtures import Rig
from test_gateway_enrollment import EnrollmentHarness, GRANT


@pytest.mark.parametrize("consumer", ["fleet-cli", "manager-engine"])
def test_real_cli_first_touch_check_restart_and_same_private_key(
    tmp_path: Path, consumer: str,
    sdk_runtime: tuple[Path, list[str]] | None = None,
) -> None:
    async def scenario() -> None:
        rig = Rig(tmp_path)
        enrollment = EnrollmentHarness(tmp_path)
        enrollment.h.clock.value = rig.h.clock.value
        enrollment.scope["expires_at"] = rig.h.clock.value + 600
        enrollment.delegation["grant_expires_at"] = rig.h.clock.value + 300
        enrollment.document["policy"]["manifest"]["revision"] += 1
        rig.h.document = enrollment.document
        projection_directory = tmp_path / "projection"
        projection_directory.mkdir(mode=0o700)
        source = projection_directory / "selected.json"
        state = tmp_path / "machine-state"
        try:
            await rig.start(enrollment_journal=enrollment.ledger)
            enrollment.scope["destination"] = rig.origin
            enrollment.document["policy"]["manifest"]["revision"] += 1
            rig.save()
            await rig.state.reload()
            projection = {
                "schema": "fleet-manager.continuity-projection", "version": 1,
                "provider": "local-projection-v1", "authority": "authority-a",
                "audience": "example-gateway", "delegation": enrollment.delegation,
                "grant": GRANT, "gateway_ca": rig.ca}
            source.write_bytes(planning.canonical(projection))
            source.chmod(0o600)
            assert await bootstrap(
                source, harness="harness-a", state_root=state, apply=False,
            ) == {"status": "needed", "stage": "enrollment"}
            assert not state.exists()

            async def process(*arguments: str) -> tuple[int, bytes, bytes]:
                package = Path(__file__).parents[1] / "src"
                environment = dict(os.environ)
                environment["PYTHONPATH"] = str(package) + os.pathsep + environment.get("PYTHONPATH", "")
                command = (
                    sdk_runtime[1] if sdk_runtime else
                    [sys.executable, "-m", "fleet_manager"])
                child = await asyncio.create_subprocess_exec(
                    *command, "bootstrap",
                    "--source", str(source), "--harness", "harness-a",
                    "--state-root", str(state), *arguments,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    env=environment, **no_window_kwargs())
                stdout, stderr = await asyncio.wait_for(child.communicate(), 20)
                return child.returncode, stdout, stderr

            if consumer == "manager-engine":
                repository = Path(__file__).parents[2]
                engine_python = repository / ".test-venvs/linux/agent-worktrees/bin/python"
                if os.name == "nt" or not engine_python.is_file():
                    if sdk_runtime:
                        pytest.fail("installed SDK qualification requires the prepared POSIX engine test environment")
                    pytest.skip("source qualification requires the prepared POSIX engine test environment")
                if sdk_runtime is None:
                    installed = await asyncio.create_subprocess_exec(
                        str(engine_python), "-I", "-c",
                        "import importlib.util;raise SystemExit(importlib.util.find_spec('fleet_manager') is None)",
                        **no_window_kwargs())
                    if await installed.wait():
                        pytest.skip("selected engine test environment needs its Fleet service-Gateway SDK")
                support = await asyncio.create_subprocess_exec(
                    str(engine_python), "-I", "-c",
                    "import json,sysconfig;print(json.dumps(sorted({"
                    "sysconfig.get_path('purelib'),sysconfig.get_path('platlib')})))",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    **no_window_kwargs())
                support_stdout, support_stderr = await asyncio.wait_for(support.communicate(), 15)
                assert support.returncode == 0, support_stderr
                if sdk_runtime:
                    engine_python = sdk_runtime[0]
                home = tmp_path / "home"
                home.mkdir(mode=0o700)
                harness = tmp_path / "harness"
                harness.mkdir()
                (harness / ".agent-worktrees").mkdir()
                (harness / ".agent-worktrees/repository-setup.json").write_text(json.dumps({
                    "version": 2, "entrypoints": {
                        "posix": {"runtime": "bash", "script": "setup.sh"}},
                    "contexts": {"harness-a": {
                        "provider": "local-projection-v1", "source": str(source)}},
                }))
                writer = (
                    "import json,pathlib,sys;"
                    "r=json.loads(pathlib.Path(sys.argv[1]).read_text());"
                    "pathlib.Path(r['result_file']).write_text(json.dumps("
                    "{'version':1,'status':'ready','message':'Synthetic core ready'}))")
                (harness / "setup.sh").write_text(
                    "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -c "
                    + shlex.quote(writer) + ' "$2"\n')
                def git(*arguments: str) -> None:
                    subprocess.run(
                        ["git", "-C", str(harness), *arguments], check=True,
                        capture_output=True, **no_window_kwargs())
                await asyncio.to_thread(git, "init", "-q")
                await asyncio.to_thread(git, "remote", "add", "origin",
                                        "https://example.invalid/team/harness.git")
                await asyncio.to_thread(git, "add", ".")
                await asyncio.to_thread(
                    git, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "Synthetic harness", "--no-verify")
                executables = tmp_path / "bin"
                executables.mkdir()
                launcher = executables / "fleet-manager"
                launcher.write_text(
                    "#!/bin/sh\nexit 99\n")
                launcher.chmod(0o700)
                engine_driver = (
                    "import sys;sys.path[:0]=" + repr([
                        str(repository / "plugins/agent-worktrees/src"),
                        str(repository / "libs/agent-procutil/src")])
                    + ";import site;"
                    + "[site.addsitedir(path) for path in " + repr(json.loads(support_stdout))
                    + "];from pathlib import Path;"
                    "import fleet_manager,hashlib;"
                    "package=Path(fleet_manager.__file__).parent;"
                    "expected=" + repr({
                        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in (repository / "fleet-manager/src/fleet_manager").glob("*.py")})
                    + ";assert all(hashlib.sha256((package/name).read_bytes()).hexdigest()==value "
                    "for name,value in expected.items()),'selected test SDK differs from current source';"
                    "from agent_worktrees import repos,repository_bootstrap;"
                    + (
                        "from agent_worktrees import repository_continuity;"
                        "original=repository_continuity.run_caller_owned;"
                        "exec(" + repr(
                            "def guarded_sdk(command, **options):\n"
                            "    assert command[:4] == [sys.executable, '-I', '-m', 'fleet_manager']\n"
                            f"    return original({sdk_runtime[1]!r} + command[4:], **options)\n"
                            "repository_continuity.run_caller_owned = guarded_sdk\n"
                        ) + ");" if sdk_runtime else ""
                    )
                    +
                    "repos._repos_yaml_path=lambda:Path(sys.argv[1]);"
                    "assert sys.argv[2:4]==['repos','setup'];"
                    "raise SystemExit(repository_bootstrap.command(sys.argv[4:]))")
                manager_driver = (
                    "import sys,site;"
                    "[site.addsitedir(path) for path in " + repr(json.loads(support_stdout))
                    + "];sys.path.insert(0," + repr(str(repository / "worktree-manager/src"))
                    + ");from worktree_manager import engine_client,repository_bootstrap;"
                    "engine_client.set_engine_command([sys.argv[1],'-c',sys.argv[2],sys.argv[3]]);"
                    "raise SystemExit(repository_bootstrap.run(repository_bootstrap.parse(sys.argv[4:])))")
                environment = dict(os.environ)
                environment.update({
                    "HOME": str(home), "USERPROFILE": str(home),
                    "PATH": str(executables) + os.pathsep + environment.get("PATH", ""),
                    "PYTHONPATH": os.pathsep.join(str(repository / path) for path in (
                        "fleet-manager/src", "worktree-manager/src", "plugins/agent-worktrees/src",
                        "libs/agent-procutil/src"))})
                child = await asyncio.create_subprocess_exec(
                    str(engine_python), "-c", manager_driver, str(engine_python), engine_driver,
                    str(home / "repos.yaml"), "--apply",
                    "--repository", "https://example.invalid/team/harness.git",
                    "--repository-target", str(harness), "--trust-repository-setup",
                    "--context", "harness-a",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    env=environment, **no_window_kwargs())
                stdout, stderr = await asyncio.wait_for(child.communicate(), 60)
                status = child.returncode
            else:
                status, stdout, stderr = await process("--apply")
            assert status == 0, (stdout, stderr)
            if consumer == "fleet-cli":
                assert json.loads(stdout) == {"status": "ready", "stage": "connected"}
            else:
                assert b"authenticated outbound Gateway access" in stdout
                states = list(home.glob(".fleet-continuity-*"))
                assert len(states) == 1
                state = states[0]
            retained = (state / "machine.json").read_bytes()
            assert GRANT.encode() not in retained
            if os.name != "nt":
                assert (state / "machine.json").stat().st_mode & 0o077 == 0
            if sdk_runtime:
                old_nonce = rig.state.authority.challenge()
                await rig.serving.close()
                enrollment.ledger = OperationLedger(
                    enrollment.ledger.path, authority="authority-a",
                    installation="installation-a", enable_enrollment=True)
                rig.serving = await start(
                    replace(rig.listener, port=urlsplit(rig.origin).port, origin=rig.origin),
                    rig.policy, reload_interval=0.05, clock=rig.h.clock,
                    enrollment_journal=enrollment.ledger)
                rig.state = rig.serving.app[STATE]
                assert rig.serving.origin == rig.origin
                assert rig.state.authority.challenge() != old_nonce
            assert (await process())[0] == 0
            assert (await process("--apply"))[0] == 0
            assert (state / "machine.json").read_bytes() == retained
            with sqlite3.connect(enrollment.ledger.path) as connection:
                assert connection.execute("SELECT count(*) FROM machine_enrollments").fetchone() == (1,)
                assert connection.execute("SELECT count(*) FROM operations").fetchone() == (1,)
            projection["delegation"] = copy.deepcopy(projection["delegation"])
            projection["delegation"]["scope"]["container"] = "changed-container"
            source.write_bytes(planning.canonical(projection))
            assert (await process("--apply"))[0] != 0
            assert (state / "machine.json").read_bytes() == retained
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode-bit custody; native ACL lane is separate")
def test_replacement_after_path_validation_cannot_bypass_opened_file_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, replacement = tmp_path / "source.json", tmp_path / "replacement.json"
    source.write_bytes(b"private-original")
    source.chmod(0o600)
    replacement.write_bytes(b"shared-replacement")
    replacement.chmod(0o644)
    validate = private_state.validate_private_file

    def replace_after_validation(path: Path) -> None:
        validate(path)
        replacement.replace(path)

    monkeypatch.setattr(private_state, "validate_private_file", replace_after_validation)
    with pytest.raises(planning.PlanError, match="private to its owner"):
        private_state.read_private_file(source, 16384)


@pytest.mark.parametrize("change", [
    {"generation": 0}, {"generation": -1}, {"generation": "1"},
    {"principal": "invalid principal"}, {"account": ""},
])
def test_invalid_first_touch_intent_leaves_no_immutable_state(
    tmp_path: Path, change: dict,
) -> None:
    enrollment = EnrollmentHarness(tmp_path)
    scope = copy.deepcopy(enrollment.scope)
    scope.update(change)
    projection = {
        "provider": "local-projection-v1", "authority": "authority-a", "audience": "example-gateway",
        "delegation": dict(enrollment.delegation, scope=scope)}
    projection["delegation"]["grant_expires_at"] = 2**52
    root = tmp_path / "invalid-machine"
    with pytest.raises(planning.PlanError):
        _state(root, projection, apply=True)
    assert not (root / "machine.json").exists()
    assert not root.exists()
