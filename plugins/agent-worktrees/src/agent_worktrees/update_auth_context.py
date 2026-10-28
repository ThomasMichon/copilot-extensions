"""Update-only auth scopes; unrelated Git identity probes retain strict isolation."""

from __future__ import annotations

import argparse
import contextlib
import contextvars
import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

from . import output, update_auth as auth, update_auth_sources as sources, update_events

_current: contextvars.ContextVar[Session | None] = contextvars.ContextVar(
    "update_auth_session", default=None
)
_repository: contextvars.ContextVar[auth.RunOverlay | None] = contextvars.ContextVar(
    "update_auth_repository", default=None
)
_managed: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "update_auth_managed", default=False
)
_repository_context: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "update_repository_context", default=None
)


def mode() -> str:
    value = os.environ.get("CE_UPDATE_AUTH", "on")
    if value not in ("on", "off", "report-only"):
        raise ValueError("CE_UPDATE_AUTH must be on, off or report-only")
    return value


def describe(report: auth.AuthReport) -> None:
    if not report.results:
        return
    output.info("Auth report (read-only):")
    for item in report.results:
        account = f" as {item.expected_account}" if item.expected_account else ""
        output.info(f"{item.source.id}{account}: {item.status} ({item.duration_ms} ms)")
        if item.fix:
            output.warn(f"fix {item.source.id}: {item.fix}")


class Session:
    def __init__(self) -> None:
        self.reports: dict[Path | None, auth.AuthReport] = {}
        self.overlays: dict[Path | None, auth.RunOverlay | None] = {}
        self.context_required: dict[Path | None, bool] = {}
        self.used: set[tuple[str, str, Path | None]] = set()
        self.anchor_reports: dict[Path, auth.AuthReport] = {}
        self.anchor_overlays: dict[Path, auth.RunOverlay | None] = {}
        self.anchors_prepared = False
        self.unresolved = False
        self.anchor_failed = False

    def prepare(
        self, context: Path | None, *, include_anchors: bool = False,
        required: bool | None = None,
    ) -> auth.AuthReport:
        was_required = self.context_required.get(context)
        required = was_required is True or (
            required if required is not None else was_required is None
        )
        if context not in self.reports or required != was_required:
            from .reconcile import MARKETPLACE

            selected = [
                item for item in sources.marketplaces(context)
                if item.id == "marketplace:" + MARKETPLACE
            ]
            report, overlay = self._prepare_report(selected, required=required)
            self.reports[context], self.overlays[context] = report, overlay
            self.context_required[context] = required
        if include_anchors:
            self.prepare_anchors()
        return self.reports[context]

    def prepare_anchors(self) -> None:
        if self.anchors_prepared:
            return
        for source in sources.anchors():
            if source.cwd is None:
                raise ValueError("anchor auth source requires a checkout context")
            report, overlay = self._prepare_report([source])
            self.anchor_reports[source.cwd], self.anchor_overlays[source.cwd] = report, overlay
        self.anchors_prepared = True

    def _prepare_report(self, selected: list[auth.Source], *, required: bool = True):
        if selected:
            update_events.enter("auth_report")
        report = auth.report(selected)
        overlay = auth.fallback(report, apply_fixes=mode() != "report-only")
        self.unresolved |= required and bool(overlay and overlay.denies)
        describe(report)
        for item in report.results:
            available = overlay is not None and item.source.id in overlay.applies_to
            summary = item.summary()
            summary["required"] = summary["required"] and required
            update_events.auth_result(summary, applied=False, available=available)
        return report, overlay

    def record_use(self, report: auth.AuthReport, overlay: auth.RunOverlay, *, required: bool):
        for item in report.results:
            key = (item.source.id, item.source.url, item.source.cwd)
            if item.source.id in overlay.applies_to and key not in self.used:
                self.used.add(key)
                summary = item.summary()
                summary["required"] = summary["required"] and required
                update_events.auth_used(summary)

    def environment(self, context: Path | None) -> dict[str, str] | None:
        self.prepare(context)
        overlay = self.overlays[context]
        return overlay.environment(os.environ) if overlay is not None else None

    def blocked(self, context: Path | None, id: str) -> bool:
        self.prepare(context)
        overlay = self.overlays[context]
        return overlay is not None and id in overlay.denies


@contextlib.contextmanager
def session():
    value = None if mode() == "off" else Session()
    token = _current.set(value)
    try:
        yield value
    finally:
        _current.reset(token)


def child_environment(context: Path | None) -> dict[str, dict[str, str]]:
    value = _current.get()
    env = value.environment(context) if value is not None else None
    return {"env": env} if env is not None else {}


def run_acquisition(runner, *args, context: Path | None = None, repository: bool = False, **kwargs):
    """Observe a launched child, not environment preparation or a failed spawn."""
    from .installer_run import InstallerCleanupError

    value = _current.get()
    if value is not None and repository:
        anchor = _repository_context.get()
        report = value.anchor_reports.get(anchor) if anchor is not None else None
        overlay = _repository.get()
        required = True
    elif value is not None:
        report = value.reports.get(context)
        overlay = value.overlays.get(context)
        required = value.context_required.get(context, True)
    else:
        report, overlay, required = None, None, False

    def used():
        if value is not None and report is not None and overlay is not None:
            value.record_use(report, overlay, required=required)

    try:
        result = runner(*args, **kwargs)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, InstallerCleanupError):
        used()
        raise
    used()
    return result


def blocked(context: Path | None, marketplace: str) -> bool:
    value = _current.get()
    return value is not None and value.blocked(context, "marketplace:" + marketplace)


def prepare_marketplace(context: Path | None, *, required: bool) -> None:
    value = _current.get()
    if value is not None:
        value.prepare(context, required=required)


class AuthRequired(RuntimeError):
    pass


def require_marketplace(context: Path | None, marketplace: str) -> None:
    if blocked(context, marketplace):
        raise AuthRequired("auth_required")


def anchor_blocked(anchor: Path) -> bool:
    value = _current.get()
    if value is None:
        return False
    id = "anchor:" + str(anchor)
    overlay = value.anchor_overlays.get(anchor)
    if overlay is not None and id in overlay.denies:
        output.warn(f"{id}: auth_required")
        return True
    return False


def anchor_failure(anchor: str, error: str) -> None:
    credential = any(
        marker in error.casefold()
        for marker in (
            "authentication failed",
            "could not read username",
            "could not read password",
            "http 401",
            "http 403",
            "repository not found",
            "auth_required",
        )
    ) or bool(re.search(r"(?:http(?:/\S+)?\s+|returned error:\s*)(?:401|403)\b", error, re.I))
    update_events.record(
        "anchor_sync",
        "anchor:" + anchor,
        "auth_required" if credential else "anchor fetch failed: " + error,
    )
    value = _current.get()
    if value is not None:
        value.unresolved |= credential
        value.anchor_failed |= not credential


@contextlib.contextmanager
def repository_scope(anchor: Path):
    """Isolate anchor operations independently of HTTPS auth management."""
    value = _current.get()
    overlay = None
    managed = False
    if value is not None:
        overlay = value.anchor_overlays.get(anchor)
        managed = anchor in value.anchor_reports
    token = _repository.set(overlay)
    managed_token = _managed.set(managed)
    scope_token = _repository_context.set(anchor)
    try:
        yield
    finally:
        _repository.reset(token)
        _managed.reset(managed_token)
        _repository_context.reset(scope_token)


def managed_repository() -> bool:
    return _managed.get() or _repository.get() is not None


def repository_environment(env: Mapping[str, str]) -> dict[str, str]:
    from . import git_ops

    overlay = _repository.get()
    if _repository_context.get() is not None or managed_repository():
        env = git_ops.repository_identity_env(dict(env))
        # An inherited snapshot is untrusted command configuration too; restoring
        # it would undo the isolation that just removed its counted entries.
        env.pop("CE_UPDATE_AUTH_BASE_CONFIG", None)
    return overlay.environment(env) if overlay is not None else dict(env)


def _marketplace_context() -> Path | None:
    from . import update_cli

    return update_cli._core_helper(
        "_invocation_update_context", update_cli._invocation_update_context
    )() or update_cli._core_helper(
        "_project_update_context", update_cli._project_update_context
    )()


def _cleanup_context(args: argparse.Namespace) -> Path:
    explicit = getattr(args, "auth_cwd", None)
    return Path(explicit) if explicit is not None else (_marketplace_context() or Path.cwd())


def run_update(args: argparse.Namespace, steps) -> int:
    with session() as value:
        if value is not None and getattr(args, "target", None) is None:
            phase = getattr(args, "phase", None)
            if phase == "anchors":
                value.prepare_anchors()
            elif phase in (None, "payloads") and not getattr(args, "plugin", None):
                value.prepare(
                    _marketplace_context(), include_anchors=(
                        phase is None and not getattr(args, "no_anchor_sync", False)
                    ),
                )
        rc = steps(args)
        results = [
            item for report in (
                *getattr(value, "reports", {}).values(),
                *getattr(value, "anchor_reports", {}).values(),
            ) for item in report.results
        ]
        if any(item.status not in {"ok", "anonymous"} for item in results):
            _cleanup_offer(_cleanup_context(args))
        if value is not None and rc == 0:
            return 1 if value.anchor_failed else 4 if value.unresolved else 0
        return rc


def _cleanup_offer(cwd: Path) -> dict:
    from . import update_auth_cleanup

    offer = update_auth_cleanup.offer(cwd)
    update_events.auth_cleanup_offer(offer)
    if offer["status"] == "offered":
        output.info(
            "Auth cleanup available (preview only): update --auth-report --auth-cleanup; "
            "apply requires the exact displayed plan digest."
        )
    return offer


def cmd_report(args: argparse.Namespace) -> int:
    cwd = _cleanup_context(args)
    selected = (
        [] if getattr(args, "auth_source", None) else sources.marketplaces(_marketplace_context())
    )
    if not getattr(args, "no_anchor_sync", False):
        selected += sources.anchors()
    for index, url in enumerate(getattr(args, "auth_source", None) or []):
        item = auth.source(f"source:{index}", url, purpose="manager", cwd=cwd)
        if item is None:
            if getattr(args, "json", False):
                print(
                    json.dumps(
                        {
                            "supported": False,
                            "reason": "unsupported_https_source",
                            "all_ok": False,
                            "results": [],
                            "environment": {},
                            "unset_environment": [],
                        }
                    )
                )
            else:
                output.err("--auth-source requires a supported HTTPS repository URL")
            return 3
        selected.append(item)
    environment = dict(os.environ)
    if getattr(args, "auth_non_repository", False):
        environment = auth.non_repository_environment(environment, cwd)
        report = auth.report(selected, env=environment)
    else:
        report = auth.report(selected)
    overlay = auth.fallback(report, apply_fixes=mode() != "report-only")
    payload = report.summary()
    cleanup_result = None
    digest = getattr(args, "apply_auth_cleanup", None)
    receipt = getattr(args, "revert_auth_cleanup", None)

    def recheck() -> auth.AuthReport:
        nonlocal report
        report = auth.report(selected, env=environment)
        return report

    if digest or receipt:
        from . import update_auth_cleanup

        if digest and mode() != "on":
            cleanup_result = {"status": "blocked", "changed": False, "reason": "auth_mode_read_only"}
        elif receipt:
            cleanup_result = update_auth_cleanup.rollback(cwd, receipt, digest)
            if cleanup_result["status"] == "reverted":
                try:
                    cleanup_result["report"] = recheck().summary()
                except Exception:
                    cleanup_result["status"] = "verification_failed"
        else:
            cleanup_result = update_auth_cleanup.apply(cwd, digest, report, recheck)
        if "report" in cleanup_result:
            payload = dict(cleanup_result["report"])
        payload["cleanup"] = cleanup_result
    elif getattr(args, "auth_cleanup", False) or any(
        item.status not in {"ok", "anonymous"} for item in report.results
    ):
        from . import update_auth_cleanup

        payload["cleanup_offer"] = update_auth_cleanup.offer(
            cwd, include_values=bool(getattr(args, "auth_cleanup", False)),
        )
    if getattr(args, "auth_environment", False):
        # Only added/replaced variables cross the Manager's process boundary.
        environment = overlay.environment(environment) if overlay else environment
        payload["environment"] = {
            key: value for key, value in environment.items() if os.environ.get(key) != value
        }
        payload["unset_environment"] = [key for key in os.environ if key not in environment]
    if getattr(args, "json", False):
        print(json.dumps(payload))
    else:
        describe(report)
        offer = payload.get("cleanup_offer", {})
        if offer.get("status") == "offered":
            output.info(offer["plan"]["diff"])
            output.info("Apply with --auth-report --apply-auth-cleanup " + offer["plan"]["plan_sha256"])
        if cleanup_result:
            output.info("Auth cleanup: " + cleanup_result["status"])
            if cleanup_result.get("backup_path"):
                output.info("Rollback receipt: " + cleanup_result["backup_path"])
            if cleanup_result.get("plan"):
                output.info(json.dumps(cleanup_result["plan"]))
    if cleanup_result and cleanup_result["status"] not in {
        "applied", "reverted", "rollback_preview",
    }:
        return 1
    if cleanup_result and "report" in cleanup_result:
        return 0 if payload["all_ok"] else 4
    return 4 if overlay and overlay.denies else 0
