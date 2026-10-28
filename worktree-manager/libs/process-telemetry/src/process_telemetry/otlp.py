"""Explicit optional SDK export to a literal loopback OTLP/HTTP endpoint."""

from __future__ import annotations

import ipaddress
import math
import re
import time
from urllib.parse import urlsplit

from .buffer import snapshot
from .measurement import OUTCOMES, STAGES, TraceContext, _COUNTERS, _IDENTITY, _TAG

_REQUIRED = frozenset({
    "schema", "kind", "component", "stage", "trace_id", "span_id", "parent_span_id",
    "start_time_ns", "duration_ns", "outcome",
})


def validate_endpoint(endpoint: str) -> str:
    """Require an explicit local destination; no DNS, redirects or env defaults."""
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http" or not parsed.hostname
        or not ipaddress.ip_address(parsed.hostname).is_loopback
        or not parsed.port or parsed.path != "/v1/traces"
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment
    ):
        raise ValueError("OTLP requires an explicit literal loopback HTTP /v1/traces endpoint")
    return endpoint


def validate_record(event: dict) -> dict:
    """Accept only the typed span schema, never legacy lifecycle payloads/logs."""
    record = snapshot(event)
    if not _REQUIRED <= record.keys() or record.keys() - (_REQUIRED | _COUNTERS | _IDENTITY):
        raise ValueError("unsupported span fields")
    if record["schema"] != "agent.performance.v1" or record["kind"] != "span":
        raise ValueError("unsupported span schema")
    if (
        type(record["component"]) is not str or not _TAG.fullmatch(record["component"])
        or record["stage"] not in STAGES or record["outcome"] not in OUTCOMES
    ):
        raise ValueError("invalid span vocabulary")
    TraceContext(record["trace_id"], record["span_id"])
    if record["parent_span_id"] is not None:
        TraceContext(record["trace_id"], record["parent_span_id"])
    for field in ("start_time_ns", "duration_ns", *(_COUNTERS & record.keys())):
        if type(record[field]) is not int or not 0 <= record[field] < 2**63:
            raise ValueError("invalid span measurement")
    for field in _IDENTITY & record.keys():
        if type(record[field]) is not str or not re.fullmatch(r"[a-zA-Z0-9_.+-]{1,80}", record[field]):
            raise ValueError("invalid identity metadata")
    return record


class LocalOtlpSink:
    """One explicit off-path SDK exporter, with no own queue or worker thread.

    Compose ``export_batch`` with ``BufferedSink(..., batch_sink=True)``.
    Owner shutdown closes the buffer first, then closes this exporter only
    after its writer has stopped. No global provider or exit hook is installed.
    """

    def __init__(self, endpoint: str, *, timeout: float = 2.0):
        endpoint = validate_endpoint(endpoint)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 5:
            raise ValueError("export timeout must be positive and at most five seconds")
        try:
            from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
            from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
        except ImportError as exc:
            raise RuntimeError("optional OTLP dependencies are not installed") from exc

        owner = self
        self._deadline = None
        self._last_reply = None
        self._payload_bytes = 0
        self._service_instance_id = None

        class LoopbackSpanExporter(SpanExporter):
            # The SDK HTTP exporter's constructor parses/logs ambient headers
            # even with explicit arguments. Use the public exporter extension
            # point and canonical protobuf encoder, never patch SDK globals.
            def export(self, spans):
                from ._otlp_transport import post

                try:
                    body = encode_spans(spans).SerializeToString()
                    owner._payload_bytes = len(body)
                    reply = post(
                        endpoint, body, owner._deadline or time.monotonic() + timeout,
                        strict=False,
                    )
                    owner._last_reply = reply
                    if reply.error:
                        return SpanExportResult.FAILURE
                    acknowledgement = ExportTraceServiceResponse.FromString(reply.body)
                    if acknowledgement.partial_success.rejected_spans:
                        return SpanExportResult.FAILURE
                    return SpanExportResult.SUCCESS
                except Exception:
                    return SpanExportResult.FAILURE

            def shutdown(self):
                pass  # Every export closes its own finite connection.

        self._closed = False
        self._exporter = LoopbackSpanExporter()

    def __call__(self, event: dict) -> None:
        """Synchronous off-path single-record compatibility adapter."""
        self.export_batch([event])

    def export_batch(self, events: list[dict]) -> None:
        """Export at most 32 typed SDK spans; failures propagate to buffer counters."""
        if self._closed:
            raise RuntimeError("OTLP sink closed")
        if type(events) is not list or not 1 <= len(events) <= 32:
            raise ValueError("OTLP batch must contain one to 32 records")
        records = [validate_record(event) for event in events]
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import ReadableSpan
        from opentelemetry.sdk.trace.export import SpanExportResult
        from opentelemetry.sdk.util.instrumentation import InstrumentationScope
        from opentelemetry.trace import SpanContext, Status, StatusCode, TraceFlags

        spans = []
        for record in records:
            def context(span_id):
                return SpanContext(
                    int(record["trace_id"], 16), int(span_id, 16), False,
                    trace_flags=TraceFlags(TraceFlags.SAMPLED),
                )

            attributes = {
                "ce.component": record["component"], "ce.stage": record["stage"],
                "ce.outcome": record["outcome"], "ce.schema": record["schema"],
            }
            attributes.update({
                f"ce.{key}": record[key] for key in (_COUNTERS | _IDENTITY) & record.keys()
            })
            status = (
                StatusCode.OK if record["outcome"] == "ok"
                else StatusCode.ERROR if record["outcome"] == "error" else StatusCode.UNSET
            )
            spans.append(ReadableSpan(
                name=f"agent.{record['component']}.{record['stage']}",
                context=context(record["span_id"]),
                parent=context(record["parent_span_id"]) if record["parent_span_id"] else None,
                resource=Resource({
                    "service.name": record["component"],
                    "service.namespace": "copilot-extensions",
                    **({"service.instance.id": self._service_instance_id}
                       if self._service_instance_id is not None else {}),
                }),
                attributes=attributes, status=Status(status),
                start_time=record["start_time_ns"],
                end_time=record["start_time_ns"] + record["duration_ns"],
                instrumentation_scope=InstrumentationScope("copilot-extensions.process-telemetry", "1"),
            ))
        if self._exporter.export(spans) is not SpanExportResult.SUCCESS:
            raise RuntimeError("OTLP export failed")

    def close(self) -> None:
        """Close only off-path after the owning writer has stopped."""
        if not self._closed:
            self._closed = True
            self._exporter.shutdown()


from .otlp_signals import LocalOtlpSignalsSink  # noqa: E402,F401
