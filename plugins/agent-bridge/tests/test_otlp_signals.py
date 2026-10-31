"""Closed source signals: pure contracts and opt-in genuine SDK/protobuf wire."""

from __future__ import annotations

import atexit
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from process_telemetry.otlp import LocalOtlpSignalsSink
from process_telemetry.signals import (
    BOUNDS, SignalEndpoints, SignalIdentity, SignalResult, metric_points, validate_source_records,
    validate_metric_points,
)


def source(stage="source_entry", *, sequence=1, **extra):
    return dict(
        schema="agent.performance.v1", kind="span", component="worktree-manager-source",
        stage=stage, outcome="ok", trace_id="a" * 32, span_id=f"{sequence:016x}",
        parent_span_id=None, start_time_ns=1_000_000_000, duration_ns=0,
        queue_wait_ns=1000, **extra,
    )


def test_closed_configuration_and_partition():
    endpoints = SignalEndpoints(logs="http://127.0.0.1:1234/v1/logs")
    assert endpoints.requested == ("logs",)
    with pytest.raises(AttributeError):
        endpoints.logs = None
    for url in ("http://localhost:12/v1/logs", "http://127.0.0.1:1/v1/metrics",
                "http://192.0.2.1:12/v1/logs", "http://127.0.0.1/v1/logs",
                "http://127.0.0.1:12/v1/logs?x=y", "http://127.0.0.1:12/v1/logs\n"):
        with pytest.raises(ValueError):
            SignalEndpoints(logs=url)
    with pytest.raises(ValueError):
        SignalIdentity(source_revision="private path")
    with pytest.raises(ValueError):
        SignalResult("accepted", attempted_records=2, accepted_records=1)
    value = SignalResult("partial", attempted_records=3, accepted_records=1, rejected_records=1,
                         unknown_records=1, not_attempted_records=2)
    assert value.generated_records == 5


def test_unconfigured_sink_returns_before_clock_import_or_record_validation(monkeypatch):
    from process_telemetry import otlp_signals
    import builtins
    from types import SimpleNamespace
    original = builtins.__import__

    def forbidden(*args, **kwargs):
        raise AssertionError("unconfigured sink did work")

    def imports(name, *args, **kwargs):
        if name.startswith("opentelemetry") or name.endswith("_otlp_transport"):
            forbidden()
        return original(name, *args, **kwargs)

    monkeypatch.setattr(otlp_signals, "time", SimpleNamespace(
        time_ns=forbidden, monotonic=forbidden, perf_counter_ns=forbidden,
    ))
    monkeypatch.setattr(otlp_signals.uuid, "uuid4", forbidden)
    monkeypatch.setattr(builtins, "__import__", imports)
    client = LocalOtlpSignalsSink(SignalEndpoints(), identity=SignalIdentity())
    result = client.export_batch(object())
    assert result.logs.status == result.metrics.status == result.traces.status == "not_requested"
    client.close()


def test_fixed_metric_cardinality_units_and_unavailable():
    events = []
    for index in range(32):
        events.append({**source("input_ack", sequence=index + 1),
                       "duration_ns": index * 1_000_000, "outcome": "ok" if index % 2 else "unavailable"})
    points = metric_points(validate_source_records(events))
    assert len(points) <= 56
    durations = [p for p in points if p["name"] == "ce.source.duration"]
    assert len(durations) == 1
    assert durations[0]["count"] == 16
    assert durations[0]["unit"] == "s"
    assert sum(durations[0]["bucket_counts"]) == 16
    assert durations[0]["bounds"] == list(BOUNDS)
    assert durations[0]["max"] == .031
    for extra in ({"message": "private"}, {"cpu_ns": 10}, {"duration_ns": True},
                  {"roster_count": 1_000_001}, {"stage": "freeform"}):
        with pytest.raises(ValueError):
            validate_source_records([{**source(), **extra}])
    with pytest.raises(ValueError):
        validate_source_records([source()] * 33)


def test_sender_metric_metadata_has_closed_shapes_and_consistent_counts():
    from copy import deepcopy

    points = [{**p, "start_time_ns": 100, "time_ns": 200}
              for p in metric_points([source("input_ack")])]
    assert validate_metric_points(points) == points
    for value in (None, {}, [None], ["text"], [{"name": "unknown"}]):
        with pytest.raises(ValueError):
            validate_metric_points(value)
    for field, value in (
        ("bounds", [True] * len(BOUNDS)), ("bucket_counts", [0] * (len(BOUNDS) + 1)),
        ("sum", float("nan")), ("start_time_ns", 200), ("count", True),
    ):
        corrupted = deepcopy(points)
        histogram = next(p for p in corrupted if p["name"] == "ce.source.duration")
        histogram[field] = value
        with pytest.raises(ValueError):
            validate_metric_points(corrupted)


@pytest.fixture
def wire(tmp_path):
    pytest.importorskip("opentelemetry.sdk._logs.export")
    captured, config = [], {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers["Content-Length"])
            payload = self.rfile.read(size)
            file = tmp_path / f"{len(captured)}.protobuf"
            file.write_bytes(payload)
            captured.append((self.path, dict(self.headers), file))
            settings = config.get(self.path, {})
            if settings.get("delay"):
                time.sleep(settings["delay"])
            try:
                if settings.get("slow_headers"):
                    self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                    self.wfile.flush()
                    for _ in range(100):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(.05)
                    return
                self.send_response(settings.get("status", 200))
                self.send_header("Content-Type", settings.get("mime", "application/x-protobuf"))
                body = settings.get("body", b"")
                self.send_header("Content-Length", str(settings.get("length", len(body))))
                self.end_headers()
                if settings.get("drip"):
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(settings["drip"])
                else:
                    self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = .025
    stopped = threading.Event()

    def serve():
        while not stopped.is_set():
            server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", captured, config
    finally:
        stopped.set()
        thread.join(3)
        server.server_close()
        assert not thread.is_alive()


def sink(base, **kwargs):
    return LocalOtlpSignalsSink(
        SignalEndpoints(**{signal: f"{base}/v1/{signal}" for signal in ("traces", "logs", "metrics")}),
        identity=SignalIdentity(source_revision="b" * 40, selected_version="1.2.3"),
        **kwargs,
    )


def decode_logs(path):
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
    return ExportLogsServiceRequest.FromString(path.read_bytes())


def decode_metrics(path):
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
    return ExportMetricsServiceRequest.FromString(path.read_bytes())


def test_genuine_sdk_records_export_and_persist_closed_protobuf(wire, monkeypatch):
    base, captured, _ = wire
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "private=forbidden")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "private=forbidden;invalid")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "http://192.0.2.1:1/v1/logs")
    monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.1:1")
    monkeypatch.setenv("OTEL_METRIC_EXPORT_INTERVAL", "1")
    from opentelemetry import _logs, metrics, trace

    def forbidden(*args, **kwargs):
        raise AssertionError("export registered a process-global side effect")

    monkeypatch.setattr(atexit, "register", forbidden)
    monkeypatch.setattr(_logs, "set_logger_provider", forbidden)
    monkeypatch.setattr(metrics, "set_meter_provider", forbidden)
    monkeypatch.setattr(trace, "set_tracer_provider", forbidden)
    # The owned receiver already exists. SDK export must not make another worker.
    monkeypatch.setattr(threading.Thread, "start", forbidden)
    client = sink(base)
    events = [source(), {**source("input_ack", sequence=2), "duration_ns": 5_000_000},
              {**source("fixture_rows", sequence=3), "outcome": "unavailable", "roster_count": 3}]
    first = client.export_batch(events)
    assert all(getattr(first, s).status == "accepted" for s in ("traces", "logs", "metrics"))
    assert first.logs.accepted_records == 3
    assert first.metrics.accepted_records == 7
    assert sum(getattr(first, s).requests for s in ("traces", "logs", "metrics")) == 3
    log_request = decode_logs(captured[1][2])
    resource = log_request.resource_logs[0]
    assert {a.key for a in resource.resource.attributes} == {
        "service.name", "service.namespace", "service.instance.id",
    }
    scope = resource.scope_logs[0]
    assert scope.scope.name == "copilot-extensions.process-telemetry" and scope.scope.version == "1"
    assert not scope.scope.attributes
    for log in scope.log_records:
        assert log.body.string_value == log.event_name == "agent.source.observation"
        assert log.trace_id.hex() == "a" * 32
        attrs = {a.key: a.value for a in log.attributes}
        assert attrs["ce.schema"].string_value == "agent.source-observation.log.v1"
        assert attrs["ce.selected_version"].string_value == "1.2.3"
        assert "ce.payload_version" not in attrs
    assert scope.log_records[-1].severity_number == 13
    assert "ce.duration_ns" not in {a.key for a in scope.log_records[-1].attributes}
    metric_request = decode_metrics(captured[2][2])
    metrics = metric_request.resource_metrics[0].scope_metrics[0].metrics
    for metric in metrics:
        data = metric.sum if metric.HasField("sum") else metric.histogram
        assert data.aggregation_temporality == 1
        for point in data.data_points:
            assert {a.key for a in point.attributes} == {"ce.stage", "ce.outcome"}
            assert not point.exemplars
    assert not any(b"forbidden" in file.read_bytes() for _, _, file in captured)
    previous_end = client.metric_expectations[0]["time_ns"]
    client.export_batch([{**source("input_ack", sequence=4), "duration_ns": 1}])
    assert client.metric_expectations[0]["start_time_ns"] == previous_end
    client.close()
    before = len(captured)
    assert client.export_batch([source(sequence=5)]).logs.status == "inactive"
    assert len(captured) == before


def test_resource_instance_is_stable_per_sink_and_distinct_across_sink_lifetimes(wire):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    base, captured, _ = wire
    clients = [sink(base), sink(base)]

    def export(client, events):
        first = len(captured)
        result = client.export_batch(events)
        assert all(getattr(result, s).status == "accepted" for s in ("traces", "logs", "metrics"))
        return captured[first:]

    def instance_ids(requests):
        instances = set()
        for path, _, file in requests:
            if path == "/v1/traces":
                resource = ExportTraceServiceRequest.FromString(file.read_bytes()).resource_spans[0]
                records = resource.scope_spans[0].spans
            elif path == "/v1/logs":
                resource = decode_logs(file).resource_logs[0]
                records = resource.scope_logs[0].log_records
            else:
                resource = decode_metrics(file).resource_metrics[0]
                records = [
                    point
                    for metric in resource.scope_metrics[0].metrics
                    for point in (metric.sum if metric.HasField("sum") else metric.histogram).data_points
                ]
                assert all({a.key for a in point.attributes} == {"ce.stage", "ce.outcome"}
                           for point in records)
            attrs = {a.key: a.value.string_value for a in resource.resource.attributes}
            assert attrs.keys() == {"service.name", "service.namespace", "service.instance.id"}
            assert attrs["service.name"] == "worktree-manager-source"
            assert attrs["service.namespace"] == "copilot-extensions"
            instance = attrs["service.instance.id"]
            parsed = uuid.UUID(instance)
            assert str(parsed) == instance and parsed.version == 4
            assert parsed.variant == uuid.RFC_4122
            assert instance.replace("-", "") != "a" * 32
            assert all("service.instance.id" not in {a.key for a in record.attributes}
                       for record in records)
            instances.add(instance)
        return instances

    try:
        initial = [instance_ids(export(client, [source()])) for client in clients]
        assert all(len(ids) == 1 for ids in initial)
        assert initial[0].isdisjoint(initial[1])
        before = len(captured)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda client: client.export_batch([source("input_ack", sequence=2)]), clients,
            ))
        assert all(getattr(result, s).status == "accepted"
                   for result in results for s in ("traces", "logs", "metrics"))
        assert instance_ids(captured[before:]) == initial[0] | initial[1]
        assert len(captured[before:]) == 6
        assert instance_ids(export(clients[0], [source("ui_stop", sequence=3)])) == initial[0]
    finally:
        for client in clients:
            client.close()

    replacement = sink(base)
    try:
        replacement_ids = instance_ids(export(replacement, [source()]))
        assert len(replacement_ids) == 1
        assert replacement_ids.isdisjoint(initial[0] | initial[1])
    finally:
        replacement.close()


@pytest.mark.parametrize("kind", ["partial", "warning", "negative", "excess", "malformed", "mime", "oversize", "truncated", "unsupported", "redirect"])
def test_acknowledgement_partition_and_no_retry(wire, kind):
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse

    base, captured, config = wire
    ack = ExportLogsServiceResponse()
    settings = {}
    if kind in ("partial", "warning", "negative", "excess"):
        ack.partial_success.rejected_log_records = {"partial": 1, "warning": 0, "negative": -1, "excess": 3}[kind]
        ack.partial_success.error_message = "private receiver text"
        settings["body"] = ack.SerializeToString()
    if kind == "malformed":
        settings["body"] = b"not-protobuf"
    if kind == "mime":
        settings["mime"] = "application/json"
    if kind == "oversize":
        settings["length"] = 65537
    if kind == "truncated":
        settings["length"] = 12
    if kind == "unsupported":
        settings["status"] = 404
    if kind == "redirect":
        settings["status"] = 302
    config["/v1/logs"] = settings
    client = LocalOtlpSignalsSink(
        SignalEndpoints(logs=f"{base}/v1/logs"), identity=SignalIdentity(), timeout=.2,
    )
    first = client.export_batch([source(), source("ui_stop", sequence=2)])
    assert first.logs.attempted_records == 2 and first.logs.generated_records == 2
    if kind == "partial":
        assert (first.logs.status, first.logs.accepted_records, first.logs.rejected_records) == ("partial", 1, 1)
    elif kind == "warning":
        assert first.logs.status == "accepted_warning" and first.logs.accepted_records == 2
    else:
        assert first.logs.unknown_records == 2 and first.logs.accepted_records == 0
    assert "private" not in json.dumps(asdict(first))
    client.export_batch([source(sequence=3)])
    assert len(captured) == (2 if kind == "warning" else 1)
    client.close()


def test_shared_deadline_slow_drip_and_independent_sibling(wire, monkeypatch):
    from process_telemetry import _otlp_transport

    # Measure transport exhaustion, not first-use optional SDK import latency.
    pytest.importorskip("opentelemetry.exporter.otlp.proto.common.trace_encoder")
    pytest.importorskip("opentelemetry.exporter.otlp.proto.common.metrics_encoder")
    real_post = _otlp_transport.post

    def consume_trace_budget(endpoint, payload, deadline, **kwargs):
        reply = real_post(endpoint, payload, deadline, **kwargs)
        if endpoint.endswith("/v1/traces"):
            assert reply.attempted
            # A socket timeout can precede a coarse monotonic-clock deadline.
            # This scenario requires the trace result to arrive after that boundary.
            while (remaining := deadline - time.monotonic()) > 0:
                time.sleep(remaining)
        return reply

    monkeypatch.setattr(_otlp_transport, "post", consume_trace_budget)
    base, captured, config = wire
    config["/v1/traces"] = {"body": b"x" * 20, "drip": .025}
    client = sink(base, timeout=.1)
    started = time.monotonic()
    result = client.export_batch([source()])
    assert time.monotonic() - started < .4
    assert result.traces.unknown_records == 1
    assert result.logs.status == result.metrics.status == "deadline_exhausted"
    assert result.logs.not_attempted_records == 1
    time.sleep(.2)
    second = client.export_batch([source("input_ack", sequence=2)])
    assert second.traces.status == "inactive"
    assert second.logs.status == second.metrics.status == "accepted"
    assert sum(path == "/v1/traces" for path, _, _ in captured) == 1
    client.close()


def test_trace_failure_before_deadline_preserves_sibling_budget(wire):
    base, captured, config = wire
    config["/v1/traces"] = {"status": 503}
    client = sink(base)
    try:
        first = client.export_batch([source()])
        assert first.traces.status == "transport_unknown"
        assert first.traces.requests == first.traces.unknown_records == 1
        assert first.logs.status == first.metrics.status == "accepted"
        second = client.export_batch([source("input_ack", sequence=2)])
        assert second.traces.status == "inactive"
        assert second.logs.status == second.metrics.status == "accepted"
        assert sum(path == "/v1/traces" for path, _, _ in captured) == 1
    finally:
        client.close()


def test_missing_origin_and_unsupported_metrics_are_not_zero_success(wire):
    base, captured, config = wire
    client = sink(base)
    result = client.export_batch([source("input_ack")])
    assert result.metrics.status == "inactive" and result.metrics.not_attempted_records == 3
    assert len(captured) == 2
    config["/v1/metrics"] = {"status": 404}
    result = client.export_batch([source(sequence=2)])
    assert result.metrics.status == "unsupported" and result.metrics.unknown_records == 2
    assert result.logs.status == "accepted"
    client.close()


@pytest.mark.parametrize("signal,field", [
    ("metrics", "rejected_data_points"), ("traces", "rejected_spans"),
])
def test_partial_ack_uses_signal_specific_point_units(wire, signal, field):
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

    base, _, config = wire
    response = ExportMetricsServiceResponse() if signal == "metrics" else ExportTraceServiceResponse()
    setattr(response.partial_success, field, 1)
    config[f"/v1/{signal}"] = {"body": response.SerializeToString()}
    client = sink(base)
    result = client.export_batch([source(), source("input_ack", sequence=2)])
    row = getattr(result, signal)
    assert row.status == "partial"
    assert row.rejected_records == 1
    assert row.accepted_records == (4 if signal == "metrics" else 1)
    assert row.generated_records == row.accepted_records + row.rejected_records
    client.close()


def test_sdk_absent_only_disables_requested_signal_and_keeps_counts(wire, monkeypatch):
    import builtins
    base, captured, _ = wire
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            raise ImportError("synthetic missing optional SDK")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    client = LocalOtlpSignalsSink(SignalEndpoints(logs=f"{base}/v1/logs"), identity=SignalIdentity())
    result = client.export_batch([source()])
    assert result.logs.status == "sdk_unavailable"
    assert result.logs.not_attempted_records == 1 and result.logs.requests == 0
    assert result.traces.status == result.metrics.status == "not_requested"
    assert client.export_batch([source(sequence=2)]).logs.status == "inactive"
    assert captured == []
    client.close()


def test_source_identity_and_single_epoch_are_not_silently_relabelled(wire):
    client = sink(wire[0])
    with pytest.raises(ValueError, match="identity"):
        client.export_batch([{**source(), "source_revision": "c" * 40}])
    client.export_batch([source()])
    with pytest.raises(ValueError, match="one source trace"):
        client.export_batch([{**source(), "trace_id": "c" * 32}])
    client.close()


def test_trace_only_keeps_legacy_success_ack_without_new_mime_rule(wire):
    base, _, config = wire
    config["/v1/traces"] = {"status": 202, "mime": "text/plain"}
    client = LocalOtlpSignalsSink(SignalEndpoints(traces=f"{base}/v1/traces"), identity=SignalIdentity())
    assert client.export_batch([source()]).traces.status == "accepted"
    client.close()


def test_exhausted_budget_does_not_attempt_a_request(wire):
    client = sink(wire[0], timeout=1e-12)
    result = client.export_batch([source()])
    for signal, count in (("traces", 1), ("logs", 1), ("metrics", 2)):
        row = getattr(result, signal)
        assert row.status == "deadline_exhausted"
        assert row.generated_records == row.not_attempted_records == count
        assert row.requests == row.unknown_records == 0
    assert wire[1] == []
    client.close()


def test_unknown_epoch_does_not_fabricate_a_zero_length_interval(wire):
    client = sink(wire[0])
    result = client.export_batch([{**source(), "start_time_ns": 2**63 - 1}])
    assert result.logs.status == "accepted"
    assert result.metrics.status == "inactive"
    assert result.metrics.not_attempted_records == 2
    assert client.metric_expectations == ()
    client.close()


def test_clock_tie_cannot_reuse_a_delta_interval_or_sleep(wire, monkeypatch):
    from process_telemetry import otlp_signals
    clock = iter((0, 10, 10, 11))
    monkeypatch.setattr(otlp_signals.time, "time_ns", lambda: 1_000_000_000)
    monkeypatch.setattr(otlp_signals.time, "perf_counter_ns", lambda: next(clock))
    client = LocalOtlpSignalsSink(
        SignalEndpoints(metrics=f"{wire[0]}/v1/metrics"), identity=SignalIdentity(),
    )
    assert client.export_batch([source()]).metrics.status == "accepted"
    tied = client.export_batch([source("input_ack", sequence=2)])
    assert tied.metrics.status == "inactive" and tied.metrics.not_attempted_records == 3
    assert client.metric_expectations == ()
    assert client.export_batch([source("input_ack", sequence=3)]).metrics.status == "accepted"
    assert client.metric_expectations[0]["start_time_ns"] == 1_000_000_010
    assert client.metric_expectations[0]["time_ns"] == 1_000_000_011
    assert len(wire[1]) == 2
    client.close()


def test_slow_headers_share_the_same_absolute_deadline(wire):
    base, captured, config = wire
    config["/v1/logs"] = {"slow_headers": True}
    client = LocalOtlpSignalsSink(
        SignalEndpoints(logs=f"{base}/v1/logs"), identity=SignalIdentity(), timeout=.3,
    )
    started = time.monotonic()
    row = client.export_batch([source()]).logs
    assert time.monotonic() - started < 1
    assert row.status == "transport_unknown" and row.unknown_records == 1
    assert client.export_batch([source(sequence=2)]).logs.status == "inactive"
    assert len(captured) == 1
    client.close()
