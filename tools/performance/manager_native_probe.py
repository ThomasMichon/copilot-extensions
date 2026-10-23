"""Explicit source-native headless Manager measurement; no installed enrollment."""

from __future__ import annotations

import time

ENTRY_NS = time.perf_counter_ns()
ENTRY_WALL_NS = time.time_ns()
ENTRY_CPU_NS = time.process_time_ns()

import argparse
import asyncio
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import types

ROOT = Path(__file__).resolve().parents[2]


def captured_child(argv, *, cwd, env, timeout):
    """Reuse Manager/process-library custody, including timeout descendant cleanup."""
    if sys.platform != "win32":
        from worktree_manager.engine_posix_process import run as run_owned
        with tempfile.TemporaryFile(dir=cwd) as errors:
            try:
                result = run_owned(argv, cwd=cwd, env=env, timeout=timeout, text=True, stderr=errors)
            except subprocess.TimeoutExpired as exc:
                errors.seek(0)
                exc.stderr = errors.read().decode("utf-8", errors="replace")
                raise
            errors.seek(0)
            stderr = errors.read().decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(argv, result.returncode, result.stdout, stderr)
    from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job
    process, job = spawn_sync_in_kill_on_close_job(
        argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, require_job=True, allow_breakaway=False,
        **no_window_kwargs(),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    finally:
        if job is not None:
            job.close()
        process.communicate(timeout=5)


def key_receipt_observer(original, event_type, pending, *, clock=time.perf_counter_ns):
    """Time receipt, not the later completion of handler/Pilot settling."""
    async def observed(app, event):
        matching = (
            isinstance(event, event_type) and event.key == "x"
            and pending["sent"] is not None and pending["received"] is None
        )
        if matching:
            pending["received"] = clock()
        try:
            await original(app, event)
        finally:
            if matching:
                pending["handled"] = True
    return observed


def source_identity(root):
    """Read one selected source, unaffected by ambient repository-selection overrides."""
    from agent_procutil import no_window_kwargs
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    revision = subprocess.check_output(
        ["git", "-c", "core.fsmonitor=false", "-C", str(root), "rev-parse", "HEAD"],
        text=True, timeout=15, env=env, **no_window_kwargs(),
    ).strip()
    clean = not subprocess.check_output(
        ["git", "-c", "core.fsmonitor=false", "-C", str(root),
         "status", "--porcelain", "--untracked-files=normal"],
        text=True, timeout=15, env=env, **no_window_kwargs(),
    ).strip()
    return revision, clean


def child(config: dict) -> dict:
    """Exercise real source Picker callbacks, with declared fixture substitutions."""
    attempted = []

    def block_children(event, args):
        if event in ("subprocess.Popen", "os.system", "os.posix_spawn"):
            attempted.append(event)
            raise RuntimeError("source probe blocks unrelated child execution")

    sys.addaudithook(block_children)
    import urllib.request
    def block_network(*args, **kwargs):
        raise RuntimeError("source probe blocks unrelated network access")
    urllib.request.urlopen = block_network
    import websocket
    websocket.create_connection = block_network
    from worktree_manager.agent_worktrees_runtime import ensure_engine_runtime
    ensure_engine_runtime()
    from worktree_manager.performance_observation import SourceObservation
    from worktree_manager.production_picker.picker_tui import derive, engine, frame_health
    from textual import events
    substitutions = [
        "advisory update/orphan polls disabled", "isolated fixture source",
        "refresh timing wrapper; original callback executes",
        "key-receipt timing wrapper; original event handler executes",
    ]
    for name in ("_poll_update_state", "_poll_manager_update_state", "_poll_orphan_state"):
        setattr(engine.PickerScreen, name, lambda self, *a, **kw: None)
    points = {"source_entry": 0}
    original_refresh = engine.PickerScreen._after_first_refresh

    def refresh(screen):
        points.setdefault("first_refresh", time.perf_counter_ns() - ENTRY_NS)
        return original_refresh(screen)

    engine.PickerScreen._after_first_refresh = refresh
    pending_input = {"sent": None, "received": None, "handled": False}
    engine.PickerApp.on_event = key_receipt_observer(
        engine.PickerApp.on_event, events.Key, pending_input,
    )
    local = ("sample", "Win" if sys.platform == "win32" else "Linux")
    rows = [derive.norm({
        "id": f"sample-{index}", "title": f"Sample {index}", "status": "active",
        "started_at": "2026-01-01T00:00:00", "state": "wip", "turn_count": 0,
    }, *local) for index in range(3)]
    source = types.SimpleNamespace(
        LOCAL=local, LOCAL_LABEL="sample",
        machines=lambda: [("sample", *local, True)],
        bucket=derive.bucket, for_machine=derive.for_machine, load=lambda: rows,
    )
    identity = {
        **config["identity"],
        "serving_version": importlib.metadata.version("copilot-extensions-worktree-manager"),
    }
    from process_telemetry.signals import SignalEndpoints
    endpoints = SignalEndpoints(**config["signal_endpoints"]) if config.get("signal_endpoints") else None
    observation = SourceObservation(
        identity, endpoint=config["endpoint"] if endpoints is None else None,
        signal_endpoints=endpoints, origin_ns=ENTRY_NS, wall_ns=ENTRY_WALL_NS,
        source_clean=config["source_clean"],
    ) if config["mode"] == "enabled" else None
    reporter = frame_health.FrameHealthReporter(
        Path(config["events"]), report_gaps=False, observation=observation,
    ) if observation is not None else None
    if reporter is not None:
        os.environ["AGENT_WORKTREES_LAUNCH_TRACE"] = config["events"]
    result = {
        "mode": config["mode"], "identity": identity, "source_clean": config["source_clean"],
        "identity_method": "source Git/manifest, isolated distribution metadata, selected module-root check",
        "points_ns": points, "substitutions": substitutions,
        "entry_boundary": "script entry before source Picker imports",
        "refresh_boundary": "real source Textual callback, not headed paint",
        "input_boundary": "post_message to matching Key receipt, before handler settling; not paint",
        "roster_count": len(rows), "outcome": "ok",
        "cache_mode": "fresh-isolated-state; os-cache-uncontrolled",
        "python_version": platform.python_version(), "platform": sys.platform,
        "serving_source_matches": Path(engine.__file__).resolve().is_relative_to(
            ROOT / "worktree-manager/src"
        ),
    }
    if not result["serving_source_matches"]:
        result.update(outcome="error", error_code="source_module_mismatch")
        return result

    async def exercise():
        app = engine.PickerApp(source, live=False, mock_mode=True)
        async with app.run_test(size=(100, 35)) as pilot:
            screen = app.query_one(engine.PickerScreen)
            for _ in range(100):
                if (
                    "first_refresh" in points and len(screen.data) == len(rows)
                    and screen._setup_applied_epoch == screen._setup_epoch != 0
                ):
                    break
                await pilot.pause(.02)
            if "first_refresh" not in points or len(screen.data) != len(rows):
                raise RuntimeError("fixture roster was not observed")
            app.screen._compositor.render_full_update()
            if screen.query_one("#nf-body-data").option_count < len(rows) * 2:
                raise RuntimeError("fixture viewport was not observed")
            points["fixture_rows"] = time.perf_counter_ns() - ENTRY_NS
            if reporter is not None:
                reporter.observe("fixture_rows", roster_count=len(screen.data))
            before = app._last_input_activity
            pending_input["sent"] = time.perf_counter_ns()
            app.post_message(events.Key("x", "x"))
            deadline = time.perf_counter() + 3
            while not pending_input["handled"] and time.perf_counter() < deadline:
                await pilot.pause(.001)
            if app._last_input_activity <= before:
                raise RuntimeError("input was not acknowledged")
            if pending_input["received"] is None or not pending_input["handled"]:
                raise RuntimeError("input receipt was not observed")
            points["input_ack"] = pending_input["received"] - pending_input["sent"]
            if reporter is not None:
                reporter.observe("input_ack", duration_ns=points["input_ack"],
                                 observed_ns=pending_input["received"])

    from contextlib import nullcontext
    try:
        with frame_health.source_observation_scope(reporter) if reporter is not None else nullcontext():
            if reporter is not None:
                reporter.observe("source_entry", observed_ns=ENTRY_NS, duration_ns=0)
            asyncio.run(exercise())
    except Exception as exc:
        result.update(outcome="error", error_code=type(exc).__name__)
    result["blocked_child_attempts"] = len(attempted)
    result["source_process_cpu_ns"] = max(0, time.process_time_ns() - ENTRY_CPU_NS)
    result["cpu_boundary"] = "process_time_ns from script entry to report; excludes descendants"
    result["telemetry"] = reporter.observation_summary() if reporter is not None else None
    result["owner_drain_ns"] = result["telemetry"]["owner_drain_ns"] if reporter is not None else None
    return result


def child_environment(home: Path) -> dict:
    """Selected child-only isolated roots; never alter user-global configuration."""
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "COPILOT_", "WORKTREE_", "OTEL_", "GITHUB_", "GH_", "GIT_"))
    }
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home / "appdata"),
        "LOCALAPPDATA": str(home / "local"), "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"), "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_RUNTIME_DIR": str(home / "run"), "COPILOT_HOME": str(home / "copilot"),
        "TEMP": str(home), "TMP": str(home), "WORKTREE_NO_UPDATE": "1",
        "WORKTREE_MANAGER_ROOT": str(home / "manager"),
        "AGENT_WORKTREES_STATUS_MONITOR": "0",
        "AGENT_WORKTREES_PLUGINS_DIR": str(home / "empty-plugins"),
        "AGENT_WORKTREES_PIVOTS_DIR": str(home / "empty-pivots"),
        "WORKTREE_MANAGER_AGENT_WORKTREES_SRC": str(ROOT / "plugins/agent-worktrees/src"),
        "PYTHONPATH": os.pathsep.join(map(str, (
            ROOT / "worktree-manager/src", ROOT / "libs/process-telemetry/src",
        ))),
    })
    return env


def run(args) -> dict:
    from dataclasses import fields
    from worktree_manager.performance_report import child_result, source_report, trace_readback, signal_readback
    from process_telemetry.otlp import validate_endpoint, validate_record
    from process_telemetry.signals import SignalEndpoints, SignalResult, validate_metric_points

    signal_config = None
    signal_result_fields = {field.name for field in fields(SignalResult)}
    if getattr(args, "logs_endpoint", None) or getattr(args, "metrics_endpoint", None):
        from dataclasses import asdict
        signal_config = asdict(SignalEndpoints(
            traces=args.endpoint, logs=getattr(args, "logs_endpoint", None),
            metrics=getattr(args, "metrics_endpoint", None),
        ))
    for signal in ("logs", "metrics"):
        if getattr(args, f"{signal}_receiver_jsonl", None) and not (signal_config or {}).get(signal):
            raise ValueError("signal readback requires its explicitly configured sender endpoint")

    if args.endpoint is not None:
        validate_endpoint(args.endpoint)
    destination = Path(args.run_dir).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    revision, clean = source_identity(ROOT)
    version = tomllib.loads((ROOT / "worktree-manager/pyproject.toml").read_text(encoding="utf-8"))[
        "project"]["version"]
    runs = []
    identities = set()
    expected_labels = {}
    expected_logs, expected_metrics = {}, []
    for index in range(args.samples):
        order = ("disabled", "enabled") if index % 2 == 0 else ("enabled", "disabled")
        for mode in order:
            directory = destination / f"{index}-{mode}"
            directory.mkdir()
            events = directory / "observations.jsonl"
            config = {
                "mode": mode, "events": str(events), "endpoint": args.endpoint,
                "signal_endpoints": signal_config,
                "source_clean": clean,
                "identity": {"source_revision": revision, "payload_version": None, "selected_version": version},
            }
            config_path = directory / "input.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            started = time.perf_counter_ns()
            try:
                process = captured_child(
                    [sys.executable, "-s", str(Path(__file__).resolve()), "--child", str(config_path)],
                    cwd=directory, env=child_environment(directory), timeout=args.timeout,
                )
                result = child_result(process.stdout, mode=mode, returncode=process.returncode)
                (directory / "stderr.txt").write_text(process.stderr, encoding="utf-8")
            except subprocess.TimeoutExpired as exc:
                result = {"mode": mode, "outcome": "error", "error_code": "process_timeout", "exit_code": None}
                error = exc.stderr or ""
                if isinstance(error, bytes):
                    error = error.decode("utf-8", errors="replace")
                (directory / "stderr.txt").write_text(error, encoding="utf-8")
            except (OSError, RuntimeError, UnicodeError) as exc:
                result = {"mode": mode, "outcome": "error", "error_code": type(exc).__name__, "exit_code": None}
            result["process_duration_ns"] = time.perf_counter_ns() - started
            result["process_clock_domain"] = "parent-perf-counter"
            result["sample_index"] = index
            result["pair_order"] = list(order)
            result["owned_root_processes"] = 1
            result["owned_control_processes"] = 0 if sys.platform == "win32" else 1
            sender_count = 0
            sender_ids = set()
            sender_stages = {}
            signal_batches = []
            try:
                lines = events.read_text(encoding="utf-8").splitlines() if events.exists() else []
            except (OSError, UnicodeError):
                lines = []
                result["event_file_invalid"] = True
            for line in lines:
                try:
                    value = json.loads(line)
                    if type(value) is not dict:
                        raise ValueError("sender row is not an object")
                    if value.get("schema") == "manager.signal-batch.v1":
                        if value.get("trace_id") != (result.get("telemetry") or {}).get("trace_id"):
                            raise ValueError("unexpected signal-batch identity")
                        if type(value["signals"]) is not dict or set(value["signals"]) != {"traces", "logs", "metrics"}:
                            raise ValueError("unexpected signal-batch keys")
                        for row in value["signals"].values():
                            if type(row) is not dict or row.keys() != signal_result_fields:
                                raise ValueError("incomplete sender signal accounting")
                            SignalResult(**row)
                        points = validate_metric_points(value["metrics"])
                        signal_batches.append(value)
                        expected_metrics.extend(points)
                        continue
                    record = validate_record(value["span"])
                    key = (record["trace_id"], record["span_id"])
                    if (
                        record["trace_id"] != (result.get("telemetry") or {}).get("trace_id")
                        or key in sender_ids
                    ):
                        raise ValueError("unexpected sender identity")
                    sender_ids.add(key)
                    sender_stages[record["stage"]] = sender_stages.get(record["stage"], 0) + 1
                    expected_labels[key] = {
                        f"ce.{name}": record[name]
                        for name in ("component", "stage", "schema", "outcome")
                    }
                    log_attrs = {
                        **{f"ce.{name}": record[name] for name in ("component", "stage", "outcome")},
                        "ce.schema": "agent.source-observation.log.v1",
                    }
                    for field in ("duration_ns", "queue_wait_ns", "roster_count"):
                        if field in record and not (field == "duration_ns" and record["outcome"] == "unavailable"):
                            log_attrs[f"ce.{field}"] = record[field]
                    log_attrs.update({
                        f"ce.{name}": value for name, value in (result.get("telemetry") or {}).get("identity", {}).items()
                        if value is not None
                    })
                    severity, text = {"error": (17, "ERROR"), "unavailable": (13, "WARN")}.get(record["outcome"], (9, "INFO"))
                    expected_logs[key] = {
                        "attributes": log_attrs, "severity_number": severity, "severity_text": text,
                        "time_ns": record["start_time_ns"] + record["duration_ns"],
                    }
                    sender_count += 1
                except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
                    result["event_file_invalid"] = True
            identities.update(sender_ids)
            result["sender_records"] = sender_count
            result["signal_batches"] = signal_batches
            result["sender_stage_counts"] = sender_stages
            if mode == "enabled":
                result["sender_missing_stages"] = [
                    stage for stage in ("source_entry", "first_refresh", "input_ack", "fixture_rows", "ui_stop")
                    if not sender_stages.get(stage)
                ]
            if sender_count != ((result.get("telemetry") or {}).get("counts") or {}).get("written", 0):
                result["event_file_invalid"] = True
            runs.append(result)
    report = source_report(runs, expected_per_mode=args.samples)
    report["sender_spans"] = [{"trace_id": trace, "span_id": span} for trace, span in sorted(identities)]
    report["readback"] = {"complete": False, "reason": "not-requested"}
    report["signal_readback"] = {}
    if args.receiver_jsonl:
        deadline = time.monotonic() + args.readback_timeout
        started = time.perf_counter_ns()
        while True:
            try:
                with Path(args.receiver_jsonl).open(encoding="utf-8") as stream:
                    report["readback"] = trace_readback(stream, identities, expected_labels=expected_labels)
            except (OSError, UnicodeError, ValueError, RecursionError) as exc:
                report["readback"] = {"complete": False, "reason": type(exc).__name__}
            if report["readback"]["complete"] or time.monotonic() >= deadline:
                break
            time.sleep(.1)
        report["readback"]["wait_ns"] = time.perf_counter_ns() - started
        report["readback"]["sender_complete"] = report["modes"]["enabled"]["telemetry_incomplete"] == 0
        report["readback"]["complete"] = report["readback"]["complete"] and report["readback"]["sender_complete"]
    for signal in ("logs", "metrics"):
        path = getattr(args, f"{signal}_receiver_jsonl", None)
        if path is None:
            report["signal_readback"][signal] = {"complete": False, "reason": "not-requested"}
            continue
        deadline = time.monotonic() + args.readback_timeout
        while True:
            try:
                with Path(path).open(encoding="utf-8") as stream:
                    value = signal_readback(
                        stream, signal=signal, expected_logs=expected_logs, expected_metrics=expected_metrics,
                    )
            except (OSError, UnicodeError, ValueError, RecursionError) as exc:
                value = {"complete": False, "reason": type(exc).__name__}
            if value["complete"] or time.monotonic() >= deadline:
                break
            time.sleep(.1)
        value["sender_complete"] = report["modes"]["enabled"]["telemetry_incomplete"] == 0
        value["complete"] = value["complete"] and value["sender_complete"]
        report["signal_readback"][signal] = value
    (destination / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child")
    parser.add_argument("--run-dir")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--endpoint")
    parser.add_argument("--receiver-jsonl")
    parser.add_argument("--logs-endpoint")
    parser.add_argument("--metrics-endpoint")
    parser.add_argument("--logs-receiver-jsonl")
    parser.add_argument("--metrics-receiver-jsonl")
    parser.add_argument("--readback-timeout", type=float, default=5)
    args = parser.parse_args()
    if args.child:
        result = child(json.loads(Path(args.child).read_text(encoding="utf-8")))
        print("MANAGER_OBSERVATION " + json.dumps(result))
        return 0 if result["outcome"] == "ok" else 1
    if (
        not args.run_dir or not 1 <= args.samples <= 20 or not 1 <= args.timeout <= 60
        or not 0 <= args.readback_timeout <= 30
    ):
        parser.error("a fresh --run-dir, 1..20 samples and 1..60 second timeout are required")
    report = run(args)
    print(json.dumps({
        "schema": report["schema"],
        "modes": {mode: {key: value[key] for key in (
            "expected", "completed", "failed", "drops", "telemetry_incomplete",
        )}
                  for mode, value in report["modes"].items()},
        "readback": report["readback"], "qualification": report["qualification"],
        "signal_readback": report["signal_readback"],
    }))
    valid = all(
        mode["failed"] == 0 and mode["telemetry_incomplete"] == 0
        and all(point["complete"] for point in mode["points"].values())
        for mode in report["modes"].values()
    )
    if args.receiver_jsonl:
        valid = valid and report["readback"]["complete"]
    for signal in ("logs", "metrics"):
        if getattr(args, f"{signal}_receiver_jsonl"):
            valid = valid and report["signal_readback"][signal]["complete"]
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
