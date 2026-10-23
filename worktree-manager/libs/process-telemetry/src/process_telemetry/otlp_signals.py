"""Optional closed SDK signals on the caller's existing writer, without providers."""

from __future__ import annotations

import math
import time
from dataclasses import asdict

from .signals import (
    SIGNALS, SignalBatchResult, SignalEndpoints, SignalIdentity, SignalResult,
    metric_points, validate_source_records,
)

_RESOURCE = {"service.name": "worktree-manager-source", "service.namespace": "copilot-extensions"}
_SCOPE = "copilot-extensions.process-telemetry"
_SCHEMA = "agent.source-observation.log.v1"


def _log_payload(records, identity, observed):
    from opentelemetry._logs import LogRecord, SeverityNumber
    from opentelemetry.sdk._logs import ReadableLogRecord
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue, InstrumentationScope as ProtoScope
    from opentelemetry.proto.logs.v1.logs_pb2 import LogRecord as ProtoLog, ResourceLogs, ScopeLogs
    from opentelemetry.proto.resource.v1.resource_pb2 import Resource as ProtoResource

    def attributes(values):
        return [
            KeyValue(key=k, value=AnyValue(string_value=v) if type(v) is str else AnyValue(int_value=v))
            for k, v in sorted(values.items())
        ]

    logs = []
    for record in records:
        attrs = {
            "ce.schema": _SCHEMA, "ce.component": record["component"],
            "ce.stage": record["stage"], "ce.outcome": record["outcome"],
        }
        for key in ("duration_ns", "queue_wait_ns", "roster_count"):
            if key in record and not (key == "duration_ns" and record["outcome"] == "unavailable"):
                attrs[f"ce.{key}"] = record[key]
        attrs.update({f"ce.{k}": v for k, v in asdict(identity).items() if v is not None})
        severity = {"error": SeverityNumber.ERROR, "unavailable": SeverityNumber.WARN}.get(
            record["outcome"], SeverityNumber.INFO,
        )
        logs.append(ReadableLogRecord(
            log_record=LogRecord(
                timestamp=record["start_time_ns"] + record["duration_ns"],
                observed_timestamp=observed, trace_id=int(record["trace_id"], 16),
                span_id=int(record["span_id"], 16), trace_flags=1,
                severity_number=severity, severity_text=severity.name,
                body="agent.source.observation", event_name="agent.source.observation",
                attributes=attrs,
            ),
            resource=Resource(_RESOURCE),
            instrumentation_scope=InstrumentationScope(_SCOPE, "1"),
        ))
    encoded = []
    for readable in logs:
        record = readable.log_record
        encoded.append(ProtoLog(
            time_unix_nano=record.timestamp, observed_time_unix_nano=record.observed_timestamp,
            trace_id=record.trace_id.to_bytes(16, "big"), span_id=record.span_id.to_bytes(8, "big"),
            flags=int(record.trace_flags), severity_number=record.severity_number.value,
            severity_text=record.severity_text, event_name=record.event_name,
            body=AnyValue(string_value=record.body), attributes=attributes(record.attributes),
        ))
    return logs, ExportLogsServiceRequest(resource_logs=[ResourceLogs(
        resource=ProtoResource(attributes=attributes(_RESOURCE)),
        scope_logs=[ScopeLogs(scope=ProtoScope(name=_SCOPE, version="1"), log_records=encoded)],
    )]).SerializeToString()


def _metric_payload(points, start, end):
    from opentelemetry.sdk.metrics.export import (
        AggregationTemporality, Histogram, HistogramDataPoint, Metric, MetricsData,
        NumberDataPoint, ResourceMetrics, ScopeMetrics, Sum,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics

    groups = {}
    for point in points:
        attrs = {"ce.stage": point["stage"], "ce.outcome": point["outcome"]}
        fields = dict(attributes=attrs, start_time_unix_nano=start, time_unix_nano=end, exemplars=[])
        data = (
            NumberDataPoint(**fields, value=point["value"]) if "value" in point
            else HistogramDataPoint(
                **fields, count=point["count"], sum=point["sum"], min=point["min"], max=point["max"],
                explicit_bounds=point["bounds"], bucket_counts=point["bucket_counts"],
            )
        )
        groups.setdefault((point["name"], point["unit"]), []).append(data)
    metrics = [
        Metric(
            name=name, description="", unit=unit,
            data=Sum(data, AggregationTemporality.DELTA, True) if unit == "{observation}"
            else Histogram(data, AggregationTemporality.DELTA),
        )
        for (name, unit), data in sorted(groups.items())
    ]
    data = MetricsData([ResourceMetrics(
        Resource(_RESOURCE), [ScopeMetrics(InstrumentationScope(_SCOPE, "1"), metrics, "")], "",
    )])
    return data, encode_metrics(data).SerializeToString()


def _ack(signal, reply, count, payload_bytes):
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

    base = dict(requests=int(reply.attempted), attempted_payload_bytes=payload_bytes if reply.attempted else 0)
    if reply.error:
        return SignalResult(
            reply.error, attempted_records=count if reply.attempted else 0,
            unknown_records=count if reply.attempted else 0,
            not_attempted_records=0 if reply.attempted else count, **base,
        )
    cls, field = {
        "traces": (ExportTraceServiceResponse, "rejected_spans"),
        "logs": (ExportLogsServiceResponse, "rejected_log_records"),
        "metrics": (ExportMetricsServiceResponse, "rejected_data_points"),
    }[signal]
    try:
        ack = cls.FromString(reply.body).partial_success
        rejected = getattr(ack, field)
        if not 0 <= rejected <= count:
            raise ValueError("invalid rejection count")
        return SignalResult(
            "partial" if rejected else "accepted_warning" if ack.error_message else "accepted",
            attempted_records=count, accepted_records=count - rejected, rejected_records=rejected, **base,
        )
    except Exception:
        return SignalResult("invalid_ack", attempted_records=count, unknown_records=count, **base)


class LocalOtlpSignalsSink:
    """No queue, provider, periodic reader, retry, exit hook or worker of its own."""

    def __init__(self, endpoints: SignalEndpoints, *, identity: SignalIdentity, timeout: float = 2.0):
        if type(endpoints) is not SignalEndpoints or type(identity) is not SignalIdentity:
            raise ValueError("closed signal configuration required")
        if type(timeout) not in (float, int) or not math.isfinite(timeout) or not 0 < timeout <= 5:
            raise ValueError("signal timeout must be positive and at most five seconds")
        self._endpoints, self._identity, self._timeout = endpoints, identity, timeout
        self._closed = False
        self._inactive = set()
        self._trace = None
        self._epoch = None
        self._trace_id = None
        self._clock = (time.time_ns(), time.perf_counter_ns()) if endpoints.requested else None
        self.metric_expectations = ()

    def export_batch(self, spans: list[dict]) -> SignalBatchResult:
        if not self._endpoints.requested:
            return SignalBatchResult(**{name: SignalResult("not_requested") for name in SIGNALS})
        from ._otlp_transport import post

        deadline = time.monotonic() + self._timeout
        records = validate_source_records(spans)
        for record in records:
            for key, field in (
                ("source_revision", "source_revision"), ("payload_version", "payload_version"),
                ("runtime_version", "serving_version"),
            ):
                if key in record and record[key] != getattr(self._identity, field):
                    raise ValueError("source identity contradicts admitted metadata")
        if self._trace_id is not None and self._trace_id != records[0]["trace_id"]:
            raise ValueError("one source trace per signal sink")
        self._trace_id = records[0]["trace_id"]
        points = metric_points(records) if self._endpoints.metrics else []
        observed = self._clock[0] + time.perf_counter_ns() - self._clock[1]
        if self._epoch is None:
            self._epoch = next((r["start_time_ns"] for r in records if r["stage"] == "source_entry"), None)
        start = self._epoch
        end = observed
        interval_known = start is not None and end > start
        self.metric_expectations = tuple(
            {**point, "start_time_ns": start, "time_ns": end} for point in points
        ) if interval_known else ()
        results = {}
        for signal in SIGNALS:
            count = len(points) if signal == "metrics" else len(records)
            endpoint = getattr(self._endpoints, signal)
            if endpoint is None:
                results[signal] = SignalResult("not_requested")
                continue
            if self._closed or signal in self._inactive or (signal == "metrics" and not interval_known):
                results[signal] = SignalResult("inactive", not_attempted_records=count)
                continue
            if time.monotonic() >= deadline:
                results[signal] = SignalResult("deadline_exhausted", not_attempted_records=count)
                continue
            try:
                if signal == "traces":
                    from .otlp import LocalOtlpSink

                    if self._trace is None:
                        self._trace = LocalOtlpSink(endpoint, timeout=self._timeout)
                    self._trace._deadline = deadline
                    self._trace._last_reply = None
                    try:
                        self._trace.export_batch(records)
                    except RuntimeError:
                        if self._trace._last_reply is None:
                            raise
                    results[signal] = _ack(signal, self._trace._last_reply, count, self._trace._payload_bytes)
                elif signal == "logs":
                    from opentelemetry.sdk._logs.export import LogRecordExporter, LogRecordExportResult

                    sdk_records, payload = _log_payload(records, self._identity, observed)
                    result = None

                    class Exporter(LogRecordExporter):
                        def export(self, batch):
                            nonlocal result
                            result = _ack(signal, post(endpoint, payload, deadline), len(batch), len(payload))
                            return LogRecordExportResult.SUCCESS if result.status == "accepted" else LogRecordExportResult.FAILURE

                        def shutdown(self):
                            pass

                        def force_flush(self, timeout_millis=30000):
                            return True

                    Exporter().export(sdk_records)
                    results[signal] = result
                else:
                    from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult

                    sdk_metrics, payload = _metric_payload(points, start, end)
                    result = None

                    class Exporter(MetricExporter):
                        def export(self, metrics_data, timeout_millis=10000, **kwargs):
                            nonlocal result
                            result = _ack(signal, post(endpoint, payload, deadline), count, len(payload))
                            return MetricExportResult.SUCCESS if result.status == "accepted" else MetricExportResult.FAILURE

                        def shutdown(self, timeout_millis=30000, **kwargs):
                            pass

                        def force_flush(self, timeout_millis=10000):
                            return True

                    Exporter().export(sdk_metrics)
                    results[signal] = result
            except (ImportError, AttributeError, TypeError, RuntimeError):
                results[signal] = SignalResult("sdk_unavailable", not_attempted_records=count)
            if results[signal].status not in ("accepted", "accepted_warning", "deadline_exhausted"):
                self._inactive.add(signal)
        if interval_known:
            self._epoch = end
        return SignalBatchResult(**results)

    def close(self):
        self._closed = True
        if self._trace is not None:
            self._trace.close()
