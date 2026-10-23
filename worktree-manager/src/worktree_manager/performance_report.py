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
            from process_telemetry.signals import SIGNALS, STATUSES, SignalResult, SignalIdentity
            identity = telemetry.get("identity", {})
            if type(identity) is not dict:
                raise ValueError("invalid child identity")
            SignalIdentity(**identity)
            requested = telemetry.get("signals_requested", [])
            rows = telemetry.get("signals", {})
            if (
                type(requested) is not list or any(type(s) is not str or s not in SIGNALS for s in requested)
                or len(set(requested)) != len(requested) or type(rows) is not dict
                or rows.keys() - set(SIGNALS)
            ):
                raise ValueError("invalid requested signals")
            from dataclasses import fields
            required_signal_fields = {field.name for field in fields(SignalResult)} | {"generated_records"}
            for row in rows.values():
                if (
                    type(row) is not dict or not required_signal_fields <= row.keys()
                    or row.keys() - (required_signal_fields | {"first_failure"})
                ):
                    raise ValueError("incomplete child signal accounting")
                generated = row["generated_records"]
                failure = row.get("first_failure")
                if failure is not None and failure not in STATUSES:
                    raise ValueError("invalid signal failure")
                signal = SignalResult(**{k: v for k, v in row.items() if k not in ("generated_records", "first_failure")})
                if type(generated) is not int or generated != signal.generated_records:
                    raise ValueError("invalid signal generated count")
    except (ValueError, IndexError, TypeError, KeyError, RecursionError):
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
                    or any(
                        not telemetry.get("signals", {}).get(signal)
                        or telemetry["signals"][signal]["accepted_records"] != telemetry["signals"][signal]["generated_records"]
                        or (signal in ("traces", "logs")
                            and telemetry["signals"][signal]["generated_records"] != counts.get("written"))
                        or (signal == "metrics" and counts.get("written", 0) > 0
                            and telemetry["signals"][signal]["generated_records"] == 0)
                        for signal in telemetry.get("signals_requested", [])
                    )
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


def signal_readback(lines, *, signal, expected_logs=None, expected_metrics=()) -> dict:
    """Exact owned log correlations or metric intervals; no raw receiver content."""
    import json
    import base64
    import math
    from collections import Counter

    resources = {"service.name": "worktree-manager-source", "service.namespace": "copilot-extensions"}

    def unsigned(value):
        if type(value) is str:
            if not 1 <= len(value) <= 20 or not value.isascii() or not value.isdecimal():
                raise ValueError("invalid unsigned integer")
            value = int(value)
        if type(value) is not int or not 0 <= value < 2**64:
            raise ValueError("invalid unsigned integer")
        return value

    def real(value):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("invalid measured scalar")
        return value

    def correlation(value, width):
        try:
            raw = bytes.fromhex(value)
        except ValueError:
            raw = b""
        if len(raw) != width:
            raw = base64.b64decode(value, validate=True)
        if len(raw) != width:
            raise ValueError("invalid correlation ID")
        return raw.hex()

    def attrs(entries):
        values = {}
        for entry in entries:
            key, wrapped = entry["key"], entry["value"]
            if key in values or len(wrapped) != 1:
                raise ValueError("duplicate or malformed attribute")
            if "stringValue" in wrapped:
                value = wrapped["stringValue"]
                if type(value) is not str:
                    raise ValueError("invalid string")
            elif "intValue" in wrapped:
                value = wrapped["intValue"]
                if type(value) not in (int, str) or not str(value).isdigit():
                    raise ValueError("invalid integer")
                value = int(value)
            else:
                raise ValueError("unexpected attribute type")
            values[key] = value
        return values

    def point_key(point):
        return (
            point["name"], point["unit"], point["stage"], point["outcome"],
            int(point["start_time_ns"]), int(point["time_ns"]),
        )

    if signal == "logs":
        expected = {}
        for (trace, span), wanted in (expected_logs or {}).items():
            key = (correlation(trace, 16), correlation(span, 8))
            if key in expected:
                return {"complete": False, "reason": "duplicate_sender_log_identity"}
            expected[key] = wanted
        owned_traces = {trace for trace, _ in expected}
        selectors = {trace for trace, _ in expected}
        selectors.update(base64.b64encode(bytes.fromhex(trace)).decode("ascii") for trace, _ in expected)
    elif signal == "metrics":
        expected = {point_key(p): p for p in expected_metrics}
        if len(expected) != len(expected_metrics):
            return {"complete": False, "reason": "duplicate_sender_metric_interval"}
        selectors = {str(key[-1]) for key in expected}
        owned_ends = {key[-1] for key in expected}
    else:
        raise ValueError("unsupported readback signal")
    seen, matched, mismatched, extra = Counter(), set(), set(), 0
    invalid = 0
    candidate_selectors = {value.lower() for value in selectors} if signal == "logs" else selectors
    for line in lines:
        candidate = line.lower() if signal == "logs" else line
        if not any(selector in candidate for selector in candidate_selectors):
            continue
        try:
            value = json.loads(line)
            for resource in value.get("resourceLogs" if signal == "logs" else "resourceMetrics", []):
                for scope in resource.get("scopeLogs" if signal == "logs" else "scopeMetrics", []):
                    def common():
                        scope_data = scope.get("scope", {})
                        return (
                            attrs(resource.get("resource", {}).get("attributes", [])) == resources
                            and scope_data.get("name") == "copilot-extensions.process-telemetry"
                            and scope_data.get("version") == "1" and not scope_data.get("attributes")
                        )
                    if signal == "logs":
                        for log in scope.get("logRecords", []):
                            try:
                                trace = correlation(log.get("traceId", ""), 16)
                            except (ValueError, TypeError):
                                continue
                            if trace not in owned_traces:
                                continue
                            key = (trace, correlation(log.get("spanId", ""), 8))
                            if key not in expected:
                                extra += key[0] in selectors
                                continue
                            seen[key] += 1
                            wanted = expected[key]
                            good = (
                                common() and attrs(log.get("attributes", [])) == wanted["attributes"]
                                and log.get("body") == {"stringValue": "agent.source.observation"}
                                and log.get("eventName") == "agent.source.observation"
                                and log.get("severityNumber") == wanted["severity_number"]
                                and log.get("severityText") == wanted["severity_text"]
                                and unsigned(log.get("timeUnixNano", -1)) == wanted["time_ns"]
                            )
                            (matched if good else mismatched).add(key)
                    else:
                        for metric in scope.get("metrics", []):
                            kinds = [kind for kind in ("sum", "histogram", "gauge", "exponentialHistogram", "summary")
                                     if kind in metric]
                            entries = [(kind, metric[kind], point) for kind in kinds
                                       for point in metric[kind].get("dataPoints", [])]
                            for kind, data, point in entries:
                                try:
                                    end = unsigned(point.get("timeUnixNano", -1))
                                except (ValueError, TypeError):
                                    continue
                                if end not in owned_ends:
                                    continue
                                interval = (unsigned(point.get("startTimeUnixNano", -1)), end)
                                labels = attrs(point.get("attributes", []))
                                key = (
                                    metric.get("name"), metric.get("unit"), labels.get("ce.stage"),
                                    labels.get("ce.outcome"), *interval,
                                )
                                if key not in expected:
                                    extra += str(key[-1]) in selectors
                                    continue
                                seen[key] += 1
                                wanted = expected[key]
                                temporality = data.get("aggregationTemporality")
                                good = (
                                    common() and len(kinds) == 1
                                    and kind == ("sum" if "value" in wanted else "histogram")
                                    and set(labels) == {"ce.stage", "ce.outcome"}
                                    and (type(temporality) is int and temporality == 1
                                         or temporality == "AGGREGATION_TEMPORALITY_DELTA")
                                    and unsigned(point.get("flags", 0)) == 0
                                    and not point.get("exemplars")
                                )
                                if kind == "sum":
                                    good = (
                                        good and data.get("isMonotonic") is True and "asDouble" not in point
                                        and unsigned(point.get("asInt", -1)) == wanted["value"]
                                    )
                                elif kind == "histogram":
                                    good = (
                                        good and unsigned(point.get("count", -1)) == wanted["count"]
                                        and all(real(point.get(field)) == wanted[field] for field in ("sum", "min", "max"))
                                        and [unsigned(v) for v in point.get("bucketCounts", [])] == wanted["bucket_counts"]
                                        and [real(v) for v in point.get("explicitBounds", [])] == wanted["bounds"]
                                    )
                                (matched if good else mismatched).add(key)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            invalid += 1
    duplicates = sum(count - 1 for count in seen.values())
    return {
        "expected": len(expected), "matched": len(matched), "missing": len(expected.keys() - matched),
        "mismatched": len(mismatched), "duplicates": duplicates, "extra": extra,
        "invalid_matching_lines": invalid,
        "complete": bool(expected) and matched == expected.keys() and not (
            mismatched or duplicates or extra or invalid
        ),
    }
