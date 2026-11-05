"""Explicit hosted native MCP caller-loss lane; never a contained-test escape."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


REPOSITORY = Path(__file__).resolve().parents[1]
CONTAINMENT = "COPILOT_EXTENSIONS_TEST_CONTAINED"
ROOT = "COPILOT_EXTENSIONS_MCP_LIFETIME_ROOT"
ACKNOWLEDGEMENT = "[agent-mcp] driver launch acknowledgement: "


def admit(consent: bool, environment=None) -> Path:
    environment = os.environ if environment is None else environment
    temporary = environment.get("RUNNER_TEMP")
    if (
        not consent or environment.get(CONTAINMENT)
        or environment.get("GITHUB_ACTIONS") != "true"
        or environment.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or not temporary or (os.name != "nt" and sys.platform != "linux")
    ):
        raise RuntimeError("MCP lifetime qualification requires uncontained ephemeral hosted CI consent")
    return Path(temporary).resolve()


def source_imports() -> None:
    sys.path[:0] = [
        str(REPOSITORY / "libs" / name / "src")
        for name in ("update-driver", "agent-procutil", "single-instance-lease", "zdd")
    ]


def consumer_environment(environment, lifecycle):
    admit(True, environment)
    from windows_broker_fixture import fixture_environment

    if os.name == "nt":
        environment = {key.upper(): value for key, value in environment.items()}
    return {
        **fixture_environment(environment, control=True), **lifecycle, **{
            key: value for key, value in environment.items()
            if key.upper() in {
                "HOME", "COPILOT_HOME", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR",
                "UV_PYTHON_PREFERENCE", "UV_PYTHON_DOWNLOADS", "UV_NO_PROGRESS",
                "BOOTSTRAP_KILLSWITCH_STATE_FILE", "COPILOT_EXTENSIONS_DAEMON_PYTHON",
                "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "TMPDIR", "PSMODULEANALYSISCACHEPATH",
            }
        },
    }


def caller(root: Path) -> None:
    temporary = admit(True)
    if not root.resolve().is_relative_to(temporary) or not root.parent.name.startswith(
        "ce-broker-probe-mcp-"
    ):
        raise RuntimeError("MCP caller is outside its admitted fixture")
    source_imports()
    import agent_procutil as pu

    ordinary = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(120)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **pu.no_window_kwargs(),
    )
    try:
        command = json.loads((root / "hook-command.json").read_text(encoding="utf-8"))
        started = time.monotonic()
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, timeout=15,
            **pu.no_window_kwargs(),
        )
        elapsed = time.monotonic() - started
        (root / "hook.stdout").write_bytes(result.stdout)
        (root / "hook.stderr").write_bytes(result.stderr)
        frames = [
            line.partition(ACKNOWLEDGEMENT)[2]
            for line in result.stderr.decode("utf-8-sig").splitlines()
            if line.startswith(ACKNOWLEDGEMENT)
        ]
        outcome = {
            "caller": os.getpid(), "ordinary": ordinary.pid,
            "code": result.returncode, "seconds": elapsed,
            "frames": frames,
        }
        if os.name == "nt":
            from agent_procutil import _job_scopes
            from agent_procutil._daemon_instance_identity import _windows_birth

            outcome.update(
                ordinary_birth=_windows_birth(ordinary._handle),
                scopes=os.environ.get(_job_scopes.ENVIRONMENT_KEY),
            )
        temporary_result = root / "outcome.tmp"
        temporary_result.write_text(json.dumps(outcome), encoding="utf-8")
        temporary_result.replace(root / "outcome.json")
        time.sleep(120)
    finally:
        if ordinary.poll() is None:
            ordinary.kill()
        ordinary.wait(timeout=5)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ephemeral-ci-consent", action="store_true")
    args = parser.parse_args(argv)
    temporary = admit(args.ephemeral_ci_consent)
    source_imports()
    import pytest

    root = Path(tempfile.mkdtemp(prefix="ce-broker-probe-mcp-", dir=temporary))
    os.environ[ROOT] = str(root)
    print(f"MCP lifetime evidence: {root}", flush=True)
    return pytest.main([
        str(REPOSITORY / "libs/update-driver/tests/test_first_use_mcp_lifetime.py"),
        "-q", "-s", "--basetemp", str(root / "pytest"),
        "--timeout=900", "--timeout-method=" + ("signal" if sys.platform == "linux" else "thread"),
        "--junitxml", str(root / "lifetime.xml"), "-o", "junit_family=legacy",
    ])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--caller":
        caller(Path(sys.argv[2]))
    else:
        raise SystemExit(main())
