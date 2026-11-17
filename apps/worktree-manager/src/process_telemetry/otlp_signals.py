"""Optional closed SDK signals on the caller's existing writer, without providers."""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

if TYPE_CHECKING:
    from .diagnostic_logs import Event
    from ._otlp_transport import Reply
    from opentelemetry._logs import LogRecord, SeverityNumber
    from opentelemetry.sdk._logs import ReadableLogRecord
    from opentelemetry.sdk.metrics.export import MetricsData
    from opentelemetry.sdk.metrics import Exemplar
    from opentelemetry.proto.logs.v1.logs_pb2 import SeverityNumber as ProtoSeverityNumber
    from .otlp import LocalOtlpSink

from .signals import (
    SIGNALS, SignalBatchResult, SignalEndpoints, SignalIdentity, SignalResult,
    metric_points, validate_source_records,
)


class _PointFields(TypedDict):
    attributes: dict[str, str]
    start_time_unix_nano: int
    time_unix_nano: int
    exemplars: list[Exemplar]


class _CorrelatedLogFactory(Protocol):
    # SDK overloads omit event_name with explicit correlation; its implementation accepts both.
    def __call__(
        self, *, timestamp: int, observed_timestamp: int, trace_id: int, span_id: int,
        trace_flags: int, severity_number: SeverityNumber, severity_text: str,
        body: str, event_name: str, attributes: dict[str, str | int],
    ) -> LogRecord: ...


_RESOURCE = {"service.name": "worktree-manager-source", "service.namespace": "copilot-extensions"}
_SCOPE = "copilot-extensions.process-telemetry"
_SCHEMA = "agent.source-observation.log.v1"


def _log_payload(
    records: list[Event], identity: SignalIdentity, observed: int, resource: dict[str, str],
) -> tuple[list[ReadableLogRecord], bytes]:
    from opentelemetry._logs import LogRecord, SeverityNumber
    from opentelemetry.sdk._logs import ReadableLogRecord
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue, InstrumentationScope as ProtoScope
    from opentelemetry.proto.logs.v1.logs_pb2 import LogRecord as ProtoLog, ResourceLogs, ScopeLogs
    from opentelemetry.proto.resource.v1.resource_pb2 import Resource as ProtoResource

    def attributes(values: Mapping[str, str | int]) -> list[KeyValue]:
        return [
            KeyValue(key=k, value=AnyValue(string_value=v) if type(v) is str
                     else AnyValue(int_value=cast(int, v)))
            for k, v in sorted(values.items())
        ]

    logs = []
    for record in records:
        attrs: dict[str, str | int] = {
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
            log_record=cast(_CorrelatedLogFactory, LogRecord)(
                timestamp=record["start_time_ns"] + record["duration_ns"],
                observed_timestamp=observed, trace_id=int(record["trace_id"], 16),
                span_id=int(record["span_id"], 16), trace_flags=1,
                severity_number=severity, severity_text=severity.name,
                body="agent.source.observation", event_name="agent.source.observation",
                attributes=attrs,
            ),
            resource=Resource(resource),
            instrumentation_scope=InstrumentationScope(_SCOPE, "1"),
        ))
    encoded = []
    for readable in logs:
        log_record = readable.log_record
        encoded.append(ProtoLog(
            time_unix_nano=cast(int, log_record.timestamp),
            observed_time_unix_nano=log_record.observed_timestamp,
            trace_id=log_record.trace_id.to_bytes(16, "big"), span_id=log_record.span_id.to_bytes(8, "big"),
            flags=int(log_record.trace_flags),
            severity_number=cast("ProtoSeverityNumber.ValueType",
                                 cast(SeverityNumber, log_record.severity_number).value),
            severity_text=cast(str, log_record.severity_text), event_name=cast(str, log_record.event_name),
            body=AnyValue(string_value=cast(str, log_record.body)),
            attributes=attributes(cast(Mapping[str, str | int], log_record.attributes)),
        ))
    return logs, ExportLogsServiceRequest(resource_logs=[ResourceLogs(
        resource=ProtoResource(attributes=attributes(resource)),
        scope_logs=[ScopeLogs(scope=ProtoScope(name=_SCOPE, version="1"), log_records=encoded)],
    )]).SerializeToString()


def _metric_payload(
    points: list[Event], start: int, end: int, resource: dict[str, str],
) -> tuple[MetricsData, bytes]:
    from opentelemetry.sdk.metrics.export import (
        AggregationTemporality, Histogram, HistogramDataPoint, Metric, MetricsData,
        NumberDataPoint, ResourceMetrics, ScopeMetrics, Sum,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics

    groups: dict[tuple[str, str], list[NumberDataPoint | HistogramDataPoint]] = {}
    for point in points:
        attrs = {"ce.stage": point["stage"], "ce.outcome": point["outcome"]}
        fields: _PointFields = dict(
            attributes=attrs, start_time_unix_nano=start, time_unix_nano=end, exemplars=[],
        )
        point_data = (
            NumberDataPoint(**fields, value=point["value"]) if "value" in point
            else HistogramDataPoint(
                **fields, count=point["count"], sum=point["sum"], min=point["min"], max=point["max"],
                explicit_bounds=point["bounds"], bucket_counts=point["bucket_counts"],
            )
        )
        groups.setdefault((point["name"], point["unit"]), []).append(point_data)
    metrics = [
        Metric(
            name=name, description="", unit=unit,
            data=Sum(cast(list[NumberDataPoint], data), AggregationTemporality.DELTA, True)
            if unit == "{observation}"
            else Histogram(cast(list[HistogramDataPoint], data), AggregationTemporality.DELTA),
        )
        for (name, unit), data in sorted(groups.items())
    ]
    data = MetricsData([ResourceMetrics(
        Resource(resource), [ScopeMetrics(InstrumentationScope(_SCOPE, "1"), metrics, "")], "",
    )])
    return data, encode_metrics(data).SerializeToString()


def _ack(signal: str, reply: Reply, count: int, payload_bytes: int) -> SignalResult:
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
    classes: dict[str, tuple[
        type[ExportTraceServiceResponse] | type[ExportLogsServiceResponse]
        | type[ExportMetricsServiceResponse], str,
    ]] = {
        "traces": (ExportTraceServiceResponse, "rejected_spans"),
        "logs": (ExportLogsServiceResponse, "rejected_log_records"),
        "metrics": (ExportMetricsServiceResponse, "rejected_data_points"),
    }
    cls, field = classes[signal]
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

    def __init__(
        self, endpoints: SignalEndpoints, *, identity: SignalIdentity, timeout: float = 2.0,
    ) -> None:
        if type(endpoints) is not SignalEndpoints or type(identity) is not SignalIdentity:
            raise ValueError("closed signal configuration required")
        if type(timeout) not in (float, int) or not math.isfinite(timeout) or not 0 < timeout <= 5:
            raise ValueError("signal timeout must be positive and at most five seconds")
        self._endpoints, self._identity, self._timeout = endpoints, identity, timeout
        self._resource = {
            **_RESOURCE, "service.instance.id": str(uuid.uuid4()),
        } if endpoints.requested else None
        self._closed = False
        self._inactive: set[str] = set()
        self._trace: LocalOtlpSink | None = None
        self._epoch: int | None = None
        self._trace_id: str | None = None
        self._clock = (time.time_ns(), time.perf_counter_ns()) if endpoints.requested else None
        self.metric_expectations: tuple[Event, ...] = ()

    def export_batch(self, spans: list[Event]) -> SignalBatchResult:
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
        clock = cast(tuple[int, int], self._clock)  # Requested endpoints establish this clock.
        resource = cast(dict[str, str], self._resource)
        observed = clock[0] + time.perf_counter_ns() - clock[1]
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
                        self._trace._service_instance_id = resource["service.instance.id"]
                    self._trace._deadline = deadline
                    self._trace._last_reply = None
                    try:
                        self._trace.export_batch(records)
                    except RuntimeError:
                        if self._trace._last_reply is None:
                            raise
                    results[signal] = _ack(
                        signal, cast("Reply", self._trace._last_reply), count, self._trace._payload_bytes,
                    )
                elif signal == "logs":
                    from opentelemetry.sdk._logs.export import LogRecordExporter, LogRecordExportResult

                    sdk_records, payload = _log_payload(records, self._identity, observed, resource)
                    log_result: SignalResult | None = None

                    class Exporter(LogRecordExporter):
                        def export(self, batch: Sequence[ReadableLogRecord]) -> LogRecordExportResult:
                            nonlocal log_result
                            log_result = _ack(signal, post(endpoint, payload, deadline), len(batch), len(payload))
                            return LogRecordExportResult.SUCCESS if log_result.status == "accepted" else LogRecordExportResult.FAILURE

                        def shutdown(self) -> None:
                            pass

                        def force_flush(self, timeout_millis: int = 30000) -> bool:
                            return True

                    Exporter().export(sdk_records)
                    results[signal] = cast(SignalResult, log_result)
                else:
                    from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult

                    sdk_metrics, payload = _metric_payload(points, cast(int, start), end, resource)
                    metric_result: SignalResult | None = None

                    class MetricsExporter(MetricExporter):
                        def export(
                            self, metrics_data: MetricsData, timeout_millis: float = 10000,
                            **kwargs: object,
                        ) -> MetricExportResult:
                            nonlocal metric_result
                            metric_result = _ack(signal, post(endpoint, payload, deadline), count, len(payload))
                            return MetricExportResult.SUCCESS if metric_result.status == "accepted" else MetricExportResult.FAILURE

                        def shutdown(self, timeout_millis: float = 30000, **kwargs: object) -> None:
                            pass

                        def force_flush(self, timeout_millis: float = 10000) -> bool:
                            return True

                    MetricsExporter().export(sdk_metrics)
                    results[signal] = cast(SignalResult, metric_result)
            except (ImportError, AttributeError, TypeError, RuntimeError):
                results[signal] = SignalResult("sdk_unavailable", not_attempted_records=count)
            if results[signal].status not in ("accepted", "accepted_warning", "deadline_exhausted"):
                self._inactive.add(signal)
        if interval_known:
            self._epoch = end
        return SignalBatchResult(**results)

    def close(self) -> None:
        self._closed = True
        if self._trace is not None:
            self._trace.close()
