"""Parent-only diagnostic retention; never admission or native-completeness authority."""

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
import time

import pytest

LIMIT = 4096


def identity(value):
    return hashlib.sha256(str(value).encode("utf-8", "surrogatepass")).hexdigest()


def snapshot(registry, verified=()):
    from tools.coverage_guided_selection.measurement_launch import signed_read

    rows = []
    evidence = {row["request"]["nonce"]: row.get("lines", {}) for row in verified}
    for root, issued in sorted(registry._issued.items())[:LIMIT]:
        row = {
            "request_id": identity(root.name),
            "kind": issued["kind"] if issued["kind"] in {"python", "uv-wheel-install", "uv-slot"} else "unknown",
            "records": {},
        }
        if root.is_symlink() or not root.is_relative_to(registry.root):
            row["records"] = {"directory": "unowned"}
            rows.append(row)
            continue
        for name in ("request", "audit", "spawn", "birth"):
            path = root / (name + ".json")
            if not path.exists():
                row["records"][name] = "missing"
                continue
            try:
                value = signed_read(path, registry.key)
                row["records"][name] = "authenticated"
                if name == "request":
                    row["context_digest"] = identity(value.get("context", ""))
                for field in ("pid", "parent", "entry_pid", "created_ns", "born_ns"):
                    number = value.get(field)
                    if type(number) is int:
                        row[name + "_" + field] = number
            except (OSError, ValueError, KeyError, TypeError):
                row["records"][name] = "invalid"
        row["launch_phases"] = registry._diagnostic_launches.get(root)
        row["exit_observed"] = registry._diagnostic_exits.get(root)
        # Missing exits remain unknown, particularly for opaque native descendants.
        row["actual_kernel_exit_ns"] = None
        measured = evidence.get(root.name, {})
        row["verified_prelude_seconds"] = measured.get("prelude_seconds")
        row["verified_audit_complete"] = measured.get("audit_complete")
        rows.append(row)
    return {
        "schema": "CGS-MEASUREMENT-DIAGNOSTICS/1",
        "diagnostic_only": True, "authorizes_admission": False,
        "native_completeness_proven": False,
        "clock": "monotonic_ns; exit timestamps mean first existing wait/poll observation",
        "issued_count": len(registry._issued), "failure_count": len(registry.failures),
        "truncated": len(registry._issued) > LIMIT, "issued": rows,
    }


class Recording:
    def __init__(self, registry, *, lifecycle=None):
        self.registry = registry
        self.phases = []
        self.verified = ()
        self.lifecycle = lifecycle
        self.failed_interval = None

    @contextmanager
    def failure_boundary(self, started):
        try:
            yield
        except BaseException as error:
            self.failed_interval = {
                "started": started, "ended": time.monotonic(),
                "error_type": type(error).__name__, "admission": False,
                "performance_acceptance": False,
            }
            raise

    def plugin(self, label):
        phases = self.phases

        class PhaseReports:
            @pytest.hookimpl(wrapper=True, trylast=True)
            def pytest_runtest_makereport(self, item, call):
                report = yield
                if len(phases) < LIMIT:
                    phases.append({
                        "run": label, "node_digest": identity(item.nodeid),
                        "file": Path(item.nodeid.split("::", 1)[0]).name,
                        "phase": report.when, "outcome": report.outcome,
                        "duration_s": report.duration,
                        "wall_start_s": report.start, "wall_stop_s": report.stop,
                    })
                return report

        return PhaseReports()

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            report = snapshot(self.registry, self.verified)
        except Exception as exc:
            report = {"diagnostic_only": True, "authorizes_admission": False,
                      "native_completeness_proven": False, "snapshot_error": type(exc).__name__}
        report["pytest_phases"] = self.phases
        report["phase_limit_reached"] = len(self.phases) >= LIMIT
        report["exception_type"] = kind.__name__ if kind else None
        report["failed_interval"] = self.failed_interval
        print("MEASUREMENT_DIAGNOSTICS " + json.dumps(report, sort_keys=True, allow_nan=False))
        return False
