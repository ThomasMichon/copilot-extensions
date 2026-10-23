"""Opt-in real SDK/wire tests; no shared collector or production configuration."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("opentelemetry.exporter.otlp.proto.common.trace_encoder")
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest, ExportTraceServiceResponse,
)

from process_telemetry import SinkSlot
from process_telemetry.buffer import BufferedSink
from process_telemetry.measurement import begin
from process_telemetry.otlp import LocalOtlpSink, validate_endpoint, validate_record


def record():
    slot = SinkSlot()
    records = []
    slot.set(records.append)
    span = begin(slot, "telemetry-canary", "ipc", identity={"runtime_version": "1.2.3"})
    span.finish(spawn_count=0)
    return records[0]


@pytest.fixture
def receiver(tmp_path):
    captured = []
    config = {"status": 200, "location": "", "response": b""}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers["Content-Length"])
            if size > 262144:
                self.send_error(413)
                return
            data = self.rfile.read(size)
            path = tmp_path / f"batch-{len(captured)}.protobuf"
            path.write_bytes(data)
            captured.append((self.path, dict(self.headers), path))
            self.send_response(config["status"])
            if config["location"]:
                self.send_header("Location", config["location"])
            self.send_header("Content-Length", str(config.get("declared_length", len(config["response"]))))
            self.end_headers()
            self.wfile.write(config["response"])

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = .05
    stopped = threading.Event()

    def serve():
        while not stopped.is_set():
            server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/traces", captured, config
    finally:
        stopped.set()
        thread.join(3)
        server.server_close()
        assert not thread.is_alive()


def test_real_sdk_exports_protobuf_and_receiver_persists_without_private_environment(receiver, monkeypatch):
    endpoint, captured, _ = receiver
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "private.source=must-not-export")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_HEADERS",
        "x-private=must-not-export,content-type=application/json,content-encoding=gzip",
    )
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
        "content-type=application/json,content-encoding=gzip,content-length=99999",
    )
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://192.0.2.1:1/v1/traces")
    monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.1:1")
    first, second = record(), record()
    sink = LocalOtlpSink(endpoint)
    try:
        sink.export_batch([first, second])
    finally:
        sink.close()
    assert len(captured) == 1
    path, headers, file = captured[0]
    assert path == "/v1/traces"
    assert {key.lower(): value for key, value in headers.items()}["content-type"] == "application/x-protobuf"
    assert not any(key.lower() == "x-private" for key in headers)
    assert not any(key.lower() == "content-encoding" for key in headers)
    assert int(headers["Content-Length"]) == len(file.read_bytes())
    decoded = ExportTraceServiceRequest.FromString(file.read_bytes())
    spans = [s for r in decoded.resource_spans for scope in r.scope_spans for s in scope.spans]
    assert len(spans) == 2
    assert {span.trace_id.hex() for span in spans} == {first["trace_id"], second["trace_id"]}
    for resource in decoded.resource_spans:
        assert {attr.key for attr in resource.resource.attributes} == {"service.name", "service.namespace"}
    for span in spans:
        assert span.name == "agent.telemetry-canary.ipc"
        assert span.end_time_unix_nano >= span.start_time_unix_nano
        assert {attr.key for attr in span.attributes} == {
            "ce.schema", "ce.component", "ce.stage", "ce.outcome",
            "ce.cpu_ns", "ce.spawn_count", "ce.runtime_version",
        }
    assert b"must-not-export" not in file.read_bytes()


@pytest.mark.parametrize("variable", ["OTEL_EXPORTER_OTLP_HEADERS", "OTEL_EXPORTER_OTLP_TRACES_HEADERS"])
def test_malformed_ambient_header_never_reaches_logs(receiver, monkeypatch, caplog, variable):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", raising=False)
    monkeypatch.setenv(variable, "x-private=must-not-log;metadata")
    with caplog.at_level("WARNING"):
        sink = LocalOtlpSink(receiver[0])
        try:
            sink(record())
        finally:
            sink.close()
    assert "must-not-log" not in caplog.text
    assert "must-not-log" not in str(receiver[1][0][1])


@pytest.mark.parametrize("endpoint", [
    "http://example.com:4318/v1/traces", "http://192.0.2.1:4318/v1/traces",
    "http://localhost:4318/v1/traces", "https://127.0.0.1:4318/v1/traces",
    "http://127.0.0.1:4318/", "http://127.0.0.1:4318/v1/traces?secret=value",
    "http://user:password@127.0.0.1:4318/v1/traces",
    "http://127.0.0.1/v1/traces", "",
])
def test_nonlocal_implicit_or_ambiguous_endpoints_refused(endpoint):
    with pytest.raises(ValueError):
        validate_endpoint(endpoint)


@pytest.mark.parametrize(("status", "location"), [
    (302, "http://192.0.2.1:1/v1/traces"), (302, ""), (304, ""),
    (307, "http://192.0.2.1:1/v1/traces"),
])
def test_redirect_cannot_export_elsewhere(receiver, status, location):
    endpoint, captured, config = receiver
    config.update(status=status, location=location)
    sink = LocalOtlpSink(endpoint, timeout=.2)
    try:
        with pytest.raises(RuntimeError, match="OTLP export failed"):
            sink(record())
    finally:
        sink.close()
    assert len(captured) == 1


@pytest.mark.parametrize("kind", ["partial", "malformed", "oversize"])
def test_unsuccessful_acknowledgement_is_not_export_success(receiver, kind):
    endpoint, _, config = receiver
    acknowledgement = ExportTraceServiceResponse()
    acknowledgement.partial_success.rejected_spans = 1
    config["response"] = {
        "partial": acknowledgement.SerializeToString(),
        "malformed": b"not-protobuf",
        "oversize": b"a" * 65537,
    }[kind]
    sink = LocalOtlpSink(endpoint, timeout=.2)
    try:
        with pytest.raises(RuntimeError, match="OTLP export failed"):
            sink(record())
    finally:
        sink.close()


@pytest.mark.parametrize("length", [12, "invalid", "-1", "0,0", 70000])
def test_truncated_or_invalid_length_is_not_export_success(receiver, length):
    endpoint, _, config = receiver
    config["declared_length"] = length
    sink = LocalOtlpSink(endpoint, timeout=.2)
    try:
        with pytest.raises(RuntimeError, match="OTLP export failed"):
            sink(record())
    finally:
        sink.close()


def test_receiver_outage_isolated_from_bounded_producers(receiver):
    endpoint, captured, config = receiver
    config["status"] = 503
    sink = LocalOtlpSink(endpoint, timeout=.2)
    writer = BufferedSink(sink.export_batch, capacity=4, batch_size=4, batch_sink=True).start()
    try:
        event = record()
        for _ in range(20):
            writer(event)
            if writer.stats()["accepted"]:
                break
            threading.Event().wait(.01)
        assert writer.stats()["accepted"] > 0
        for _ in range(100):
            writer(event)
        deadline = time.monotonic() + 3
        while not writer.stats()["sink_failed"] and time.monotonic() < deadline:
            threading.Event().wait(.025)
        stats = writer.stats()
        assert stats["full"] + stats["contended"] > 0
        assert stats["sink_failed"] > 0
        assert captured
    finally:
        writer.close()
        assert writer.join(3)
        sink.close()


def test_owner_buffer_delivers_real_sdk_batch(receiver):
    sink = LocalOtlpSink(receiver[0])
    writer = BufferedSink(sink.export_batch, batch_sink=True).start()
    event = record()
    try:
        for _ in range(20):
            writer(event)
            if writer.stats()["accepted"]:
                break
            threading.Event().wait(.01)
        deadline = time.monotonic() + 3
        while not writer.stats()["delivered"] and time.monotonic() < deadline:
            threading.Event().wait(.025)
        assert writer.stats()["delivered"] == 1
        decoded = ExportTraceServiceRequest.FromString(receiver[1][0][2].read_bytes())
        assert decoded.resource_spans[0].scope_spans[0].spans[0].trace_id.hex() == event["trace_id"]
    finally:
        writer.close()
        assert writer.join(3)
        sink.close()


def test_unknown_fields_are_refused_not_exported(receiver):
    sink = LocalOtlpSink(receiver[0])
    try:
        for key in ("prompt", "argv", "path", "message"):
            with pytest.raises(ValueError):
                sink({**record(), key: "private"})
        invalid = record()
        invalid["cpu_ns"] = True
        with pytest.raises(ValueError):
            validate_record(invalid)
        with pytest.raises(ValueError):
            sink.export_batch([])
        assert not receiver[1]
    finally:
        sink.close()


def test_canary_uses_real_export_and_disclaims_persistence(receiver, monkeypatch, capsys):
    from process_telemetry.canary import main

    monkeypatch.setattr("sys.argv", ["canary", "--endpoint", receiver[0]])
    assert main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["result"] == "exported"
    assert output["persistence"] == "not-verified"
    decoded = ExportTraceServiceRequest.FromString(receiver[1][0][2].read_bytes())
    assert decoded.resource_spans[0].scope_spans[0].spans[0].trace_id.hex() == output["trace_id"]
