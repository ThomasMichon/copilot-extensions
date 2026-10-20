"""Opt-in cold-process CLI startup budgets, separate from protocol validation."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs
from zdd.routing import Endpoint, read_table

from agent_bridge.client import BridgeClient
from agent_bridge.launch_runtime import executing_executable

_SAMPLES = 5
_MEDIAN_SECONDS = 5.0
_SAMPLE_SECONDS = 15.0
_STARTUP_WATCHDOG_SECONDS = 30.0


def _performance_mode(value: str) -> str:
    if value not in {"report", "qualify"}:
        raise ValueError("AGENT_BRIDGE_STARTUP_PERF must be report or qualify")
    return value


def _summary(samples: list[dict[str, float]]) -> dict:
    if len(samples) != _SAMPLES:
        raise ValueError("Startup qualification requires every fixed sample")
    for sample in samples:
        if (
            set(sample) != {"http_seconds", "ready_seconds", "route_read_conflicts"}
            or any(type(sample[key]) not in (float, int) for key in ("http_seconds", "ready_seconds"))
            or any(not math.isfinite(value) for value in sample.values())
            or not 0 < sample["http_seconds"] <= sample["ready_seconds"]
            or type(sample["route_read_conflicts"]) is not int
            or sample["route_read_conflicts"] < 0
        ):
            raise ValueError("Invalid startup sample")
    ready = [sample["ready_seconds"] for sample in samples]
    return {
        "samples": samples,
        "median_seconds": statistics.median(ready),
        "max_seconds": max(ready),
        "median_budget_seconds": _MEDIAN_SECONDS,
        "sample_budget_seconds": _SAMPLE_SECONDS,
        "target_met": max(ready) <= _SAMPLE_SECONDS and statistics.median(ready) <= _MEDIAN_SECONDS,
    }


def _assert_budget(report: dict) -> None:
    assert report["max_seconds"] <= _SAMPLE_SECONDS, report
    assert report["median_seconds"] <= _MEDIAN_SECONDS, report


def _observe_route(root: Path) -> tuple[dict | None, int]:
    try:
        return read_table(root, strict=True), 0
    except PermissionError as exc:
        if (
            getattr(exc, "winerror", None) not in (5, 32, 33)
            and not (sys.platform == "win32" and exc.errno == errno.EACCES)
        ):
            raise
        # FileIO can expose a Windows publication conflict as CRT EACCES without winerror.
        return None, 1


def _sample(root: Path) -> dict[str, float]:
    """Launch the actual CLI in a new interpreter and observe real HTTP readiness."""
    root.mkdir()
    token = "synthetic-startup-performance"
    (root / "auth.yaml").write_text(json.dumps({"token": token}), encoding="utf-8")
    (root / "config.yaml").write_text(json.dumps({
        "port": 0, "bind": "127.0.0.1", "db_path": str(root / "sessions.db"),
        "enable_credential_relay": False, "log_level": "warning",
    }), encoding="utf-8")
    env = {
        **os.environ, "AGENT_BRIDGE_CONFIG_DIR": str(root),
        "AGENT_BRIDGE_INSTALL_DIR": str(root / "install"),
        "AGENT_BRIDGE_DYNAMIC_PORT": "1", "AGENT_BRIDGE_SELF_RETIRE": "0",
        "PYTHONPATH": os.pathsep.join(sys.path),
    }
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    client = None
    base_url = None
    first_http = None
    route_read_conflicts = 0
    with (root / "server.log").open("wb") as output:
        started = time.perf_counter()
        process = subprocess.Popen(
            [str(executing_executable()), "-m", "agent_bridge", "start", "--bind", "127.0.0.1"],
            cwd=root, env=env, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, **no_window_kwargs(),
        )
        try:
            while time.perf_counter() - started < _STARTUP_WATCHDOG_SECONDS:
                assert process.poll() is None, (
                    "Startup process exited", process.returncode,
                    (root / "server.log").read_text(encoding="utf-8"),
                )
                if client is None:
                    table, conflicts = _observe_route(root)
                    route_read_conflicts += conflicts
                    active = table.get("active") if table is not None else None
                    if active is not None:
                        endpoint = Endpoint.from_dict(active)
                        assert endpoint is not None
                        assert endpoint.pid == process.pid and endpoint.bind == "127.0.0.1"
                        base_url = endpoint.base_url
                        client = BridgeClient(base_url, token, timeout=2, connect_grace=0)
                if base_url is not None:
                    try:
                        with opener.open(base_url + "/health", timeout=0.2) as response:
                            health = json.load(response)
                    except urllib.error.URLError as exc:
                        if not isinstance(exc.reason, (ConnectionRefusedError, TimeoutError)):
                            raise
                    except TimeoutError:
                        pass
                    else:
                        observed = time.perf_counter() - started
                        if first_http is None:
                            first_http = observed
                        assert health["service"] == "agent-bridge"
                        assert not health.get("readiness_error"), health
                        if health["ready"] is True:
                            assert observed <= _STARTUP_WATCHDOG_SECONDS
                            return {
                                "http_seconds": first_http, "ready_seconds": observed,
                                "route_read_conflicts": route_read_conflicts,
                            }
                time.sleep(0.01)
            raise AssertionError(
                f"Cold startup exceeded {_STARTUP_WATCHDOG_SECONDS}s; no sample retry; "
                f"route observation conflicts={route_read_conflicts}\n"
                + (root / "server.log").read_text(encoding="utf-8")
            )
        finally:
            try:
                if client is not None:
                    if process.poll() is None:
                        client.shutdown()
                        process.wait(timeout=5)
                    assert process.returncode == 0, (root / "server.log").read_text(encoding="utf-8")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


def _source_fingerprint() -> str:
    source = Path(__file__).resolve().parents[1] / "src" / "agent_bridge"
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*.py")):
        digest.update(path.relative_to(source).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).digest())
    return digest.hexdigest()


@pytest.mark.skipif(
    not os.environ.get("AGENT_BRIDGE_STARTUP_PERF"),
    reason="explicit performance lane; not a protocol correctness gate",
)
@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.timeout(210)
def test_production_cold_start_budget(tmp_path, capsys):
    mode = _performance_mode(os.environ["AGENT_BRIDGE_STARTUP_PERF"])
    samples = []
    metadata = {
        "schema": "agent-bridge.startup-performance.v1",
        "python": platform.python_version(), "platform": sys.platform,
        "source_sha256": _source_fingerprint(),
        "boundary": "interpreter-spawn-to-cli-http-ready",
        "configuration": "fresh-empty-database-no-peers-relay-disabled",
        "os_cache": "uncontrolled",
        "mode": mode, "budget_gate_enabled": mode == "qualify",
        "baseline": "not-established",
        "startup_watchdog_seconds": _STARTUP_WATCHDOG_SECONDS,
    }
    try:
        for index in range(_SAMPLES):
            samples.append(_sample(tmp_path / f"sample-{index}"))
            with capsys.disabled():
                print("BRIDGE_STARTUP_PERF " + json.dumps({
                    **metadata, "sample": index, **samples[-1],
                }), flush=True)
    finally:
        with capsys.disabled():
            print("BRIDGE_STARTUP_PERF " + json.dumps({
                **metadata, "completed_samples": len(samples), "expected_samples": _SAMPLES,
                **(_summary(samples) if len(samples) == _SAMPLES else {"samples": samples}),
            }), flush=True)
    if mode == "qualify":
        _assert_budget(_summary(samples))


@pytest.mark.parametrize(("times", "passes"), [
    ([1, 2, 3, 4, 5], True),
    ([5, 5, 5, 15, 15], True),
    ([1, 2, 6, 7, 8], False),
    ([1, 2, 3, 4, 15.01], False),
])
def test_startup_budget_retains_samples_and_enforces_median_and_ceiling(times, passes):
    samples = [
        {"http_seconds": 0.5, "ready_seconds": value, "route_read_conflicts": 0}
        for value in times
    ]
    report = _summary(samples)
    assert report["samples"] == samples
    assert report["target_met"] is passes
    if passes:
        _assert_budget(report)
    else:
        with pytest.raises(AssertionError):
            _assert_budget(report)


@pytest.mark.parametrize("invalid", [[], [{"http_seconds": 1, "ready_seconds": 2}] * 4])
def test_missing_startup_samples_refuse_qualification(invalid):
    with pytest.raises(ValueError, match="every fixed sample"):
        _summary(invalid)


@pytest.mark.parametrize("sample", [
    {"http_seconds": 1, "ready_seconds": float("nan")},
    {"http_seconds": 1, "ready_seconds": float("inf")},
    {"http_seconds": 0, "ready_seconds": 1},
    {"http_seconds": 2, "ready_seconds": 1},
    {"http_seconds": True, "ready_seconds": 1},
])
def test_malformed_startup_samples_refuse_qualification(sample):
    with pytest.raises(ValueError, match="Invalid startup sample"):
        _summary([{**sample, "route_read_conflicts": 0}] * _SAMPLES)


@pytest.mark.parametrize("winerror", [5, 32, 33, None, 123])
def test_route_observation_retries_only_known_publication_conflicts(tmp_path, monkeypatch, winerror):
    error = PermissionError("synthetic observation refusal")
    error.winerror = winerror

    def denied(*args, **kwargs):
        raise error

    monkeypatch.setattr(sys.modules[__name__], "read_table", denied)
    if winerror in (5, 32, 33):
        assert _observe_route(tmp_path) == (None, 1)
    else:
        with pytest.raises(PermissionError):
            _observe_route(tmp_path)


@pytest.mark.parametrize("mode", ["report", "qualify"])
def test_performance_modes_are_explicit(mode):
    assert _performance_mode(mode) == mode


@pytest.mark.parametrize("mode", ["1", "true", "REPORT", ""])
def test_unknown_performance_mode_refuses(mode):
    with pytest.raises(ValueError, match="report or qualify"):
        _performance_mode(mode)


@pytest.mark.parametrize("host", ["win32", "linux"])
def test_errno_only_observation_refusal_is_bounded_only_on_windows(tmp_path, monkeypatch, host):
    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, "synthetic CRT observation refusal")

    monkeypatch.setattr(sys.modules[__name__], "read_table", denied)
    monkeypatch.setattr(sys, "platform", host)
    if host == "win32":
        assert _observe_route(tmp_path) == (None, 1)
    else:
        with pytest.raises(PermissionError):
            _observe_route(tmp_path)


def test_corrupt_route_is_not_hidden_as_a_publication_conflict(tmp_path, monkeypatch):
    def corrupt(*args, **kwargs):
        raise ValueError("synthetic corrupt route")

    monkeypatch.setattr(sys.modules[__name__], "read_table", corrupt)
    with pytest.raises(ValueError, match="corrupt route"):
        _observe_route(tmp_path)


@pytest.mark.parametrize(("mode", "seconds", "refused"), [
    ("report", 6.0, False), ("qualify", 6.0, True), ("qualify", 3.0, False),
])
def test_measurement_and_qualification_apply_distinct_gates(
    tmp_path, capsys, monkeypatch, mode, seconds, refused,
):
    observed = []

    def sample(root):
        observed.append(root)
        return {"http_seconds": 1.0, "ready_seconds": seconds, "route_read_conflicts": 0}

    module = sys.modules[__name__]
    monkeypatch.setenv("AGENT_BRIDGE_STARTUP_PERF", mode)
    monkeypatch.setattr(module, "_sample", sample)
    monkeypatch.setattr(module, "_source_fingerprint", lambda: "synthetic")
    output = SimpleNamespace(disabled=nullcontext)
    if refused:
        with pytest.raises(AssertionError):
            test_production_cold_start_budget(tmp_path, output)
    else:
        test_production_cold_start_budget(tmp_path, output)
    assert len(observed) == _SAMPLES
    report = json.loads(capsys.readouterr().out.splitlines()[-1].split(" ", 1)[1])
    assert report["completed_samples"] == _SAMPLES
    assert report["budget_gate_enabled"] is (mode == "qualify")
    assert report["target_met"] is (seconds <= _MEDIAN_SECONDS)
    assert report["baseline"] == "not-established"
