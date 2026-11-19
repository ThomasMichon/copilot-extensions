"""Pure controls for the opt-in native fixture's finite execution lease."""

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from agent_machines import health_jobs
from agent_procutil.owned_process import run_caller_owned

_PATH = Path(__file__).parent / "native" / "health_linux_fixture.py"
_SPEC = importlib.util.spec_from_file_location("_health_linux_fixture_controls", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
fixture = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fixture)

OWNER = "urn:copilot-extensions:health-job:" + "c" * 64


def _installed() -> Any:
    return SimpleNamespace(
        python=Path("/example/runtime/versions/1.0/.venv/bin/python"),
        definition=lambda desired, owner, *, enabled: health_jobs.installed_linux_definition(
            slot="/example/runtime/versions/1.0",
            python="/example/runtime/versions/1.0/.venv/bin/python",
            identity="1" * 32 + ":" + "2" * 64, store=desired["store"],
            policy_token=desired["policy_token"], owner=owner, account_uid=1000, enabled=enabled,
        ),
    )


def test_fixture_uses_two_absolute_cycles_and_fails_closed_after_lease() -> None:
    now = datetime(2026, 1, 1, 23, 59, 50, tzinfo=timezone.utc)
    times, expires = fixture.calendar(now)
    installed = fixture.FiniteInstallation(_installed(), times, expires)
    value = installed.definition(
        {"store": "/example/store", "policy_token": "a" * 64}, OWNER, enabled=False,
    )
    service = next(text for name, text in value["artifacts"].items() if name.endswith(".service"))
    timer = next(text for name, text in value["artifacts"].items() if name.endswith(".timer"))
    assert "OnCalendar=2026-01-02 00:00:10 UTC\n" in timer
    assert "OnCalendar=2026-01-02 00:00:40 UTC\n" in timer
    assert timer.count("OnCalendar=") == 2 and "*" not in timer
    assert "Persistent=true\n" in timer
    assert "TimeoutStartSec=55s\nTimeoutStopSec=5s\nKillMode=control-group\nRestart=no\n" in service
    condition = next(line for line in service.splitlines() if line.startswith("ExecCondition="))
    assert str(int(expires.timestamp())) in condition
    assert '"-I" "-S" "-c"' in condition and "else 1)" in condition
    assert service.count("ExecStart=") == 1 and "-m" in service and "agent_machines.health_job" in service
    assert installed.definition(
        {"store": "/example/store", "policy_token": "a" * 64}, OWNER, enabled=False,
    ) == value
    assert installed.installed.definition(
        {"store": "/example/store", "policy_token": "a" * 64}, OWNER, enabled=False,
    )["artifacts"] != value["artifacts"]


@pytest.mark.parametrize("kind", ["naive", "offset", "reversed", "unbounded"])
def test_fixture_refuses_unbounded_or_ambiguous_clocks(kind: str) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    times, expires = fixture.calendar(now)
    if kind == "naive":
        times = tuple(value.replace(tzinfo=None) for value in times)
    elif kind == "offset":
        times = tuple(value.astimezone(timezone(timedelta(hours=1))) for value in times)
    elif kind == "reversed":
        times = tuple(reversed(times))
    else:
        expires += timedelta(hours=1)
    with pytest.raises(ValueError, match="UTC fixture calendar"):
        fixture.FiniteInstallation(_installed(), times, expires)


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.parametrize("expired", [False, True])
def test_fixture_expiry_command_qualifies_and_refuses_in_real_interpreter(expired: bool) -> None:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=-30 if expired else 30)
    source = _installed()
    source.python = Path(sys.executable)
    installed = fixture.FiniteInstallation(
        source, (expires - timedelta(seconds=20), expires - timedelta(seconds=10)), expires,
    )
    definition = installed.definition(
        {"store": "/example/store", "policy_token": "a" * 64}, OWNER, enabled=False,
    )
    service = next(text for name, text in definition["artifacts"].items() if name.endswith(".service"))
    command = next(line for line in service.splitlines() if line.startswith("ExecCondition="))
    result = run_caller_owned(
        shlex.split(command.split("=", 1)[1]), timeout=5,
        max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == (1 if expired else 0), result.stderr
