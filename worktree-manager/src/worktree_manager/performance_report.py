"""Pure reports for explicit source-native observations, never headed qualification."""

from __future__ import annotations

import statistics

from process_telemetry.report import summarize

POINTS = ("source_entry", "first_refresh", "input_ack", "fixture_rows")


def _duration(value):
    return value is None or (type(value) is int and 0 <= value < 2**63)


def child_result(stdout: str, *, mode: str, returncode: int) -> dict:
    """Keep a malformed/missing child report as a failed sample, not a lost run."""
    import json

    try:
        lines = [line for line in stdout.splitlines() if line.startswith("MANAGER_OBSERVATION ")]
        value = json.loads(lines[-1].split(" ", 1)[1])
        if (
            type(value) is not dict or value.get("mode") != mode
            or value.get("outcome") not in ("ok", "error")
            or type(value.get("points_ns")) is not dict
        ):
            raise ValueError("invalid child report")
        if any(not _duration(item) for item in value["points_ns"].values()):
            raise ValueError("invalid child duration")
        if not _duration(value.get("source_process_cpu_ns")) or not _duration(value.get("owner_drain_ns")):
            raise ValueError("invalid child resource duration")
        telemetry = value.get("telemetry")
        if telemetry is not None:
            if type(telemetry) is not dict or type(telemetry.get("counts")) is not dict:
                raise ValueError("invalid child telemetry")
            if any(type(item) is not int or not 0 <= item < 2**63 for item in telemetry["counts"].values()):
                raise ValueError("invalid child counter")
            for key in ("queued_remaining", "abandoned", "unwritten", "owner_drain_ns"):
                if key in telemetry and (
                    type(telemetry[key]) is not int or not 0 <= telemetry[key] < 2**63
                ):
                    raise ValueError("invalid child telemetry duration")
            for key in ("sampled", "writer_started", "writer_alive", "export_requested"):
                if key in telemetry and type(telemetry[key]) is not bool:
                    raise ValueError("invalid child telemetry flag")
    except (ValueError, IndexError, TypeError):
        value = {"mode": mode, "outcome": "error", "error_code": "invalid_child_report"}
    value["exit_code"] = returncode
    if returncode != 0:
        value["outcome"] = "error"
    return value


def source_report(runs: list[dict], *, expected_per_mode: int) -> dict:
    """Retain failures, incomplete telemetry and the precise observation boundary."""
    if type(expected_per_mode) is not int or not 1 <= expected_per_mode <= 20:
        raise ValueError("invalid expected sample count")
    if any(run.get("mode") not in ("disabled", "enabled") for run in runs):
        raise ValueError("unknown measurement mode")
    modes = {}
    for mode in ("disabled", "enabled"):
        selected = [run for run in runs if run["mode"] == mode]
        if len(selected) > expected_per_mode:
            raise ValueError("too many samples")
        points = {}
        drops = 0
        failures = 0
        telemetry_incomplete = 0
        for run in selected:
            telemetry = run.get("telemetry") or {}
            counts = telemetry.get("counts", {})
            drops += sum(counts.get(key, 0) for key in ("queue_full", "after_close")) + telemetry.get("abandoned", 0)
            failures += run.get("outcome") != "ok"
            if mode == "enabled":
                telemetry_incomplete += bool(
                    not telemetry.get("sampled") or not telemetry.get("writer_started")
                    or run.get("event_file_invalid")
                    or run.get("sender_missing_stages")
                    or telemetry.get("writer_alive") or telemetry.get("queued_remaining")
                    or any(counts.get(key, 0) for key in (
                        "queue_full", "after_close", "writer_failed", "export_failed", "sampled_out",
                    ))
                    or counts.get("queued") != counts.get("written")
                    or (telemetry.get("export_requested") and counts.get("exported") != counts.get("written"))
                )
        for point in POINTS:
            samples = []
            for run in selected:
                duration = run.get("points_ns", {}).get(point)
                counts = (run.get("telemetry") or {}).get("counts", {})
                lost = sum(counts.get(key, 0) for key in ("queue_full", "after_close"))
                lost += (run.get("telemetry") or {}).get("abandoned", 0)
                samples.append({
                    "outcome": "ok" if run.get("outcome") == "ok" and duration is not None else "unavailable",
                    "duration_ns": duration, "dropped": lost,
                })
            summary = summarize(samples, expected=expected_per_mode)
            if summary["successful"] < 100:
                summary["p99_ns"] = None
            summary["p95_rank_resolution"] = summary["successful"] >= 20
            points[point] = summary
        modes[mode] = {
            "expected": expected_per_mode, "completed": len(selected),
            "failed": failures, "drops": drops, "telemetry_incomplete": telemetry_incomplete,
            "points": points, "samples": selected,
        }
        for field in ("process_duration_ns", "source_process_cpu_ns"):
            summary = summarize([{
                "outcome": "ok" if run.get("outcome") == "ok" and run.get(field) is not None else "unavailable",
                "duration_ns": run.get(field), "dropped": 0,
            } for run in selected], expected=expected_per_mode)
            if summary["successful"] < 100:
                summary["p99_ns"] = None
            modes[mode][field] = summary
    overhead = {}
    for point in POINTS:
        if point == "source_entry":
            overhead[point] = {"reason": "zero-offset marker, not an interval"}
            continue
        baseline, enabled = [modes[mode]["points"][point] for mode in ("disabled", "enabled")]
        valid = baseline["complete"] and enabled["complete"]
        baseline_median = statistics.median(sample["duration_ns"] for sample in baseline["samples"]) if valid else None
        enabled_median = statistics.median(sample["duration_ns"] for sample in enabled["samples"]) if valid else None
        delta = enabled_median - baseline_median if valid else None
        pairs = []
        for index in range(expected_per_mode):
            matching = [
                next((run for run in modes[mode]["samples"] if run.get("sample_index") == index), None)
                for mode in ("disabled", "enabled")
            ]
            measured = [
                run.get("points_ns", {}).get(point) if run and run.get("outcome") == "ok" else None
                for run in matching
            ]
            pairs.append({
                "sample_index": index,
                "delta_ns": measured[1] - measured[0] if all(value is not None for value in measured) else None,
            })
        paired_values = [pair["delta_ns"] for pair in pairs if pair["delta_ns"] is not None]
        overhead[point] = {
            "difference_of_medians_ns": delta, "paired_deltas": pairs,
            "paired_median_delta_ns": statistics.median(paired_values)
            if len(paired_values) == expected_per_mode else None,
            "ratio_of_medians": enabled_median / baseline_median
            if valid and baseline_median else None,
            "interpretation": "observed differences, not causal confidence or a budget pass",
        }
    return {
        "schema": "manager.source-native-report.v1", "modes": modes, "overhead": overhead,
        "observation_method": "owned Python executable, source PickerApp, headless fixture",
        "point_semantics": {
            "source_entry": "zero-offset script-entry marker, not executable startup duration",
            "first_refresh": "script-entry to real source Textual refresh callback",
            "input_ack": "injected Key dispatch to source acknowledgement",
            "fixture_rows": "script-entry to observed headless fixture viewport",
        },
        "qualification": "not-qualified-for-headed-or-installed-budgets",
        "unavailable": {
            "headed_paint": "headless Textual driver",
            "authoritative_rows": "fixture roster is not production authority",
            "descendant_cpu": "not measured",
            "descendant_processes": "not measured; root/control invocation counts only",
            "peak_memory": "not measured",
            "lock_wait": "not measured",
            "installed_generation_latency": "source-only probe",
            "os_first_instruction": "source entry is a script marker, not OS instrumentation",
            "installed_launcher": "source driver, not the installed shell/binstub launch",
        },
    }


def trace_readback(lines, identities: set[tuple[str, str]], *, expected_labels=None) -> dict:
    """Read only owned IDs from explicit receiver JSONL; never return raw records."""
    import json

    matched = set()
    mismatched = set()
    seen = {}
    invalid = 0
    for line in lines:
        if not any(trace in line for trace, _span in identities):
            continue
        try:
            value = json.loads(line)
            for resource in value.get("resourceSpans", []):
                for scope in resource.get("scopeSpans", []):
                    for span in scope.get("spans", []):
                        key = (span.get("traceId"), span.get("spanId"))
                        if key in identities:
                            seen[key] = seen.get(key, 0) + 1
                            labels = {entry["key"]: entry.get("value", {}).get("stringValue")
                                      for entry in span.get("attributes", [])}
                            expected = (expected_labels or {}).get(key, {})
                            if all(labels.get(name) == value for name, value in expected.items()):
                                matched.add(key)
                            else:
                                mismatched.add(key)
        except (ValueError, TypeError, AttributeError, KeyError):
            invalid += 1
    return {
        "expected": len(identities), "matched": len(matched),
        "identity_matches": len(seen), "label_mismatches": len(mismatched),
        "duplicates": sum(value - 1 for value in seen.values()),
        "missing": [{"trace_id": trace, "span_id": span}
                    for trace, span in sorted(identities - matched)],
        "invalid_matching_lines": invalid,
        "complete": bool(identities) and matched == identities and invalid == 0 and not mismatched,
    }
