"""Exercise the host wrappers without Docker, credentials or a native agent."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RIG = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
CONTAINER_ID = "a" * 64


@pytest.fixture(params=["bash", "powershell"])
def wrapper(request, tmp_path):
    executable = BASH if request.param == "bash" else POWERSHELL
    if not executable or (request.param == "bash" and os.name == "nt"):
        pytest.skip(f"{request.param} unavailable")
    if os.name == "nt":
        pytest.skip("executable Docker fixture requires POSIX")
    scenario = tmp_path / "scenario"
    scenario.mkdir()
    (scenario / "manifest.json").write_text(
        json.dumps({"tier": "P", "auth": {"copilot": "none"}}), encoding="utf-8",
    )
    (scenario / "scenario.sh").write_text("exit 0\n", encoding="utf-8")
    results = tmp_path / "results"
    results.mkdir()  # Avoid the legacy mount-permissions helper containers.
    commands = tmp_path / "commands.jsonl"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        + """
import json, os, pathlib, re, sys
a = sys.argv[1:]
with open(os.environ["COMMANDS"], "a") as stream:
    stream.write(json.dumps(a) + "\\n")
mode = os.environ.get("DOCKER_FIXTURE", "")
identity = "a" * 64
if a[0] == "ps":
    if mode.startswith("existing"):
        print(identity)
    elif mode == "similar-name":
        name_filter = next(part[5:] for part in a if part.startswith("name="))
        if re.search(name_filter, "/cr-base-proofx1"):
            print(identity)
elif a[0] == "images":
    print("cached-image")
elif a[0] == "run":
    if mode == "create-failed":
        print("name conflict at create", file=sys.stderr)
        sys.exit(125)
    if mode != "missing-id":
        print(identity)
elif a[0] == "inspect":
    if mode == "changed":
        identity = "b" * 64
    elif mode.startswith("registration-") and a[-1] != identity:
        identity = "b" * 64  # The admitted name now belongs to a replacement.
    elif mode == "registration-missing-original":
        print("Error: No such object", file=sys.stderr)
        sys.exit(1)
    print(identity + (" false" if mode == "expired" else " true"))
elif a[0] == "exec":
    if mode == "probe-failed" and "/bin/sh" in a:
        sys.exit(1)
elif a[0] in ("build", "rm"):
    # Legacy rm is expected only in explicitly unbounded tests.
    if a[0] == "build":
        sys.exit(97)
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    gh = fake_bin / "gh"
    gh.write_text("#!/bin/sh\necho unexpected-auth >&2\nexit 98\n", encoding="utf-8")
    gh.chmod(0o755)
    env = {
        **os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
        "COMMANDS": str(commands), "CR_RESULTS_DIR": str(results),
        "AGENT_BRIDGE_PROVIDERS_DIR": str(tmp_path / "providers.d"),
        "CLEAN_ROOM_DOCKER_COMMAND_JSON": json.dumps([sys.executable, str(docker)]),
    }

    def invoke(options=(), *, mode="run", then="none", fixture="", suffix="bounded-fixture"):
        env["DOCKER_FIXTURE"] = fixture
        if request.param == "bash":
            args = [executable, str(RIG / "run.sh"), "--scenario", str(scenario),
                    "--name-suffix", suffix, "--then", then]
            args += list(options) + [mode]
        else:
            names = {
                "--cpus": "-Cpus", "--memory": "-Memory",
                "--pids-limit": "-PidsLimit", "--lifetime-seconds": "-LifetimeSeconds",
                "--os": "-Os",
            }
            args = [executable, "-NoProfile", "-File", str(RIG / "run.ps1"),
                    "-Scenario", str(scenario), "-NameSuffix", suffix,
                    "-Then", then, "-Mode", mode]
            args += [names.get(option, option) for option in options]
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)
        calls = [json.loads(line) for line in commands.read_text().splitlines()] if commands.exists() else []
        return result, calls

    def unregister():
        def quote(value):
            return str(value).replace("'", "''")
        if request.param == "bash":
            source = (RIG / "run.sh").read_text(encoding="utf-8")
            start = source.index("do_bridge_unregister() {")
            body = source[start:source.index("\n}\n", start) + 3]
            script = tmp_path / "unregister.sh"
            script.write_text(
                "set -eu\n_py() { command -v python3 || command -v python; }\n"
                f"HERE='{quote(RIG)}'\nCREATED_CONTAINER_ID={CONTAINER_ID}\n"
                f"BRIDGE_CONTAINER_ID={CONTAINER_ID}\nCONTAINER=cr-base-bounded-fixture\n"
                "AGENT_NAME=cleanroom-base-bounded-fixture\n"
                + body + "\ndo_bridge_unregister\n",
                encoding="utf-8",
            )
            command = [executable, str(script)]
        else:
            source = (RIG / "run.ps1").read_text(encoding="utf-8")
            start = source.index("function Invoke-BridgeUnregister(")
            body = source[start:source.index("\n}\n", start) + 3]
            target_start = source.index("function Get-ContainerTarget {")
            target = source[target_start:source.index("\n}\n", target_start) + 3]
            script = tmp_path / "unregister.ps1"
            script.write_text(
                "$ErrorActionPreference='Stop'\n"
                f"$Here='{quote(RIG)}'; $script:CreatedContainerId='{CONTAINER_ID}'\n"
                f"$script:BridgeContainerId='{CONTAINER_ID}'\n"
                "$Container='cr-base-bounded-fixture'; $AgentName='cleanroom-base-bounded-fixture'\n"
                + target + "\n" + body + "\nInvoke-BridgeUnregister\n",
                encoding="utf-8",
            )
            command = [executable, "-NoProfile", "-File", str(script)]
        return subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)

    invoke.environment = env
    invoke.registrations = tmp_path / "cleanroom.d"
    invoke.unregister = unregister
    return invoke


LIMITS = ["--cpus", "0.5", "--memory", "128m", "--pids-limit", "64", "--lifetime-seconds", "20"]


def test_limits_reach_creation_and_same_invocation_reentry_does_not_renew(wrapper):
    result, calls = wrapper(LIMITS, then="shell")
    assert result.returncode == 0, result.stderr
    creates = [call for call in calls if call[0] == "run"]
    assert len(creates) == 1
    create = creates[0]
    for flag, value in [("--cpus", "0.5"), ("--memory", "128m"),
                        ("--memory-swap", "128m"), ("--pids-limit", "64")]:
        assert create[create.index(flag) + 1] == value
    assert create[-4:] == ["--entrypoint", "sleep", "copilot-cleanroom:base", "20"]
    assert "--init" in create
    assert not any(call[0] in ("rm", "build") for call in calls)
    for call in calls:
        if call[0] == "exec":
            assert CONTAINER_ID in call  # Never exec by a replaceable name.
    assert "not renewed" in result.stdout
    assert "unexpected-auth" not in result.stderr


@pytest.mark.parametrize("fixture", ["existing-running", "existing-stopped"])
@pytest.mark.parametrize("mode", ["run", "shell"])
def test_bounded_request_refuses_foreign_running_or_stopped_container_before_auth(wrapper, fixture, mode):
    result, calls = wrapper(LIMITS, fixture=fixture, mode=mode)
    assert result.returncode != 0
    assert "name already exists" in result.stderr.lower()
    assert len(calls) == 1 and calls[0][0] == "ps"


def test_dotted_name_does_not_match_an_unrelated_container(wrapper):
    result, calls = wrapper(LIMITS, fixture="similar-name", suffix="proof.1")
    assert result.returncode == 0, result.stderr
    creates = [call for call in calls if call[0] == "run"]
    assert len(creates) == 1
    assert creates[0][creates[0].index("--name") + 1] == "cr-base-proof.1"


@pytest.mark.parametrize("fixture", ["changed", "expired"])
def test_reentry_refuses_changed_identity_or_expiry_without_recreation(wrapper, fixture):
    result, calls = wrapper(LIMITS, then="shell", fixture=fixture)
    assert result.returncode != 0
    assert "identity changed or lifetime ended" in result.stderr.lower()
    assert sum(call[0] == "run" for call in calls) == 1
    assert not any(call[0] == "rm" for call in calls)


@pytest.mark.parametrize("fixture", ["create-failed", "missing-id"])
def test_failed_creation_never_advances_to_exec_or_removal(wrapper, fixture):
    result, calls = wrapper(LIMITS, fixture=fixture)
    assert result.returncode != 0
    assert not any(call[0] in ("exec", "rm") for call in calls)
    assert sum(call[0] == "run" for call in calls) == 1


def test_bridge_registration_resolution_and_cleanup_use_created_id_despite_name_replacement(wrapper):
    result, calls = wrapper(LIMITS, mode="bridge-register", fixture="registration-replaced-name")
    assert result.returncode == 0, result.stderr
    record_path = wrapper.registrations / f"{CONTAINER_ID}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["container"] == record["container_id"] == CONTAINER_ID
    assert "cleanroom:" + CONTAINER_ID in result.stdout
    assert not (wrapper.registrations / "cr-base-bounded-fixture.json").exists()
    assert all(call[-1] == CONTAINER_ID for call in calls if call[0] == "inspect")
    resolved = subprocess.run(
        [sys.executable, str(RIG / "bridge_register.py"), "namespace-resolve", CONTAINER_ID],
        env=wrapper.environment, capture_output=True, text=True, timeout=30,
    )
    assert resolved.returncode == 0, resolved.stderr
    assert json.loads(resolved.stdout)["spawn_command"][3] == CONTAINER_ID
    unregistered = wrapper.unregister()
    assert unregistered.returncode == 0, unregistered.stderr
    assert not record_path.exists()


def test_bridge_registration_refuses_missing_original_instead_of_adopting_replacement(wrapper):
    result, _ = wrapper(LIMITS, mode="bridge-register", fixture="registration-missing-original")
    assert result.returncode != 0
    assert "not running" in result.stderr.lower()
    assert not wrapper.registrations.exists() or not list(wrapper.registrations.glob("*.json"))


@pytest.mark.parametrize("options", [
    ["--cpus", "NaN"], ["--cpus", "0"], ["--cpus", "-1"],
    ["--memory", "5m"], ["--memory", "0"], ["--memory", "1.5g"],
    ["--pids-limit", "-1"], ["--pids-limit", "2147483648"],
    ["--lifetime-seconds", "0"], ["--lifetime-seconds", "1.5"],
    ["--memory", ""],
])
def test_invalid_limits_fail_before_docker(wrapper, options):
    result, calls = wrapper(options)
    assert result.returncode != 0
    assert calls == []


def test_bounded_destructive_then_is_refused_before_effects(wrapper):
    result, calls = wrapper(LIMITS, then="down")
    assert result.returncode != 0
    assert calls == []


def test_limits_on_non_creation_mode_are_not_silently_ignored(wrapper):
    result, calls = wrapper(LIMITS, mode="auth")
    assert result.returncode != 0
    assert calls == []


def test_unbounded_default_preserves_infinite_sleep_and_legacy_replacement(wrapper):
    result, calls = wrapper(fixture="existing-running")
    assert result.returncode == 0, result.stderr
    create = next(call for call in calls if call[0] == "run")
    assert create[-1] == "infinity"
    assert not any(flag in create for flag in ("--cpus", "--memory", "--memory-swap", "--pids-limit"))
    assert any(call[0] == "rm" for call in calls)


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_windows_container_limits_are_rejected_before_scenario_or_docker():
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-File", str(RIG / "run.ps1"),
         "-Os", "windows", "-LifetimeSeconds", "20", "-Scenario", "does-not-exist"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "not supported with -Os windows" in result.stderr
