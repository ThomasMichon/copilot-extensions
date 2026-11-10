"""Optional report composition; no native policy or credential work at startup."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import signature
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_pull_requests.watch_replay import ConsumerScope

if TYPE_CHECKING:
    from agent_pull_requests.report_read import ReportReader


def core(response_bytes: int) -> tuple[Any, Any]:
    try:
        from agent_pull_requests.native_report import NativeGitHubReport
        from agent_pull_requests.report_read import ReportReader, response_budget
        from agent_pull_requests.watch_service import WatchService
        from agent_pull_requests.watch_gateway import WatchGateway
        from fleet_manager.gateway_service_handler import (
            BoundedServiceResponse, HandlerBinding, ServiceRequest,
        )

        response_budget(response_bytes)
        NativeGitHubReport.check_support()
        signature(ReportReader).bind(
            None, {}, None, max_work_seconds=1, response_limit=response_bytes,
        )
        signature(NativeGitHubReport.resolve).bind(None, None, response_limit=response_bytes)
        signature(WatchService).bind_partial(reports={}, response_limit=response_bytes)
        signature(WatchGateway.handler_binding).bind(None, response_limit=response_bytes)
        signature(HandlerBinding).bind("fleet", "gateway", None, response_limit=response_bytes)
        signature(ServiceRequest).bind(None, "POST", "/", (), b"", response_limit=response_bytes)
        if not callable(BoundedServiceResponse):
            raise ValueError("bounded response unavailable")
        return NativeGitHubReport, ReportReader
    except (ImportError, AttributeError, TypeError, ValueError):
        raise ValueError("selected report hosting APIs unsupported") from None


@dataclass(frozen=True)
class ReportConfig:
    response_bytes: int
    bindings: tuple[tuple[str, str, ConsumerScope, Path, dict[str, str], str], ...]

    def readers(self, seconds: int) -> dict[tuple[str, str], ReportReader]:
        native, reader = core(self.response_bytes)
        return {
            (principal, target): reader(
                admitted, scope,
                native(config_path=path, scope=scope, context_id=context_id).resolve,
                max_work_seconds=seconds, response_limit=self.response_bytes,
            )
            for principal, target, admitted, path, scope, context_id in self.bindings
        }
