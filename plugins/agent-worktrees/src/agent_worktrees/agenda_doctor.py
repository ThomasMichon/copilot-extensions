"""Targeted composition of lifecycle admission and the selected agenda owner."""

from __future__ import annotations

import json
import argparse
from dataclasses import asdict
from pathlib import Path
import subprocess
import sys
from typing import Any

from agent_procutil.owned_process import run_caller_owned
from machine_transport import detect_platform

from . import config as cfg, execution_spaces, finalize_lock as fin, launch_admission
from . import output, sessions, tracking, worktree_data_store as data
from .worktree_data_contract import segment


def _helper(root: Path) -> Path:
    from plugin_activation import resolve_active_plugins

    report = resolve_active_plugins(plugin_names=("efforts",), project_root=root)
    if report.authority.value != "complete" or len(report.active) != 1:
        raise ValueError("one unambiguous enabled efforts payload is required")
    plugin = next(iter(report.active.values()))
    payload = plugin.root_for_scope("project:current") or plugin.root_for_scope("global")
    if payload is None:
        raise ValueError("selected efforts payload has no activation root")
    script = payload / "skills" / "driving-effort-agendas" / "scripts" / "agenda_doctor.py"
    if not script.is_file() or script.resolve() != script:
        raise ValueError("selected efforts payload has no ordinary agenda doctor")
    return script


def _invoke(script: Path, arguments: list[str]) -> dict[str, Any]:
    bootstrap = (
        "import sys; from pathlib import Path; "
        "sys.path.insert(0,str(Path(sys.argv[1]).parent)); "
        "import agenda_doctor; raise SystemExit(agenda_doctor.main(sys.argv[2:]))"
    )
    result = run_caller_owned(
        [sys.executable, "-I", "-B", "-c", bootstrap, str(script), *arguments],
        timeout=300, max_stdout_bytes=65536, max_stderr_bytes=2048,
    )
    if result.returncode:
        raise ValueError(result.stderr[:2048] or "agenda owner refused")
    if len(result.stdout) > 65536:
        raise ValueError("agenda owner response exceeded its bound")
    reply: dict[str, Any] = json.loads(result.stdout)
    if reply.get("schema") != "efforts.agenda-placement-doctor" or reply.get("version") != 1:
        raise ValueError("unsupported agenda doctor response")
    return reply


def _record(
    config: cfg.Config, path: Path, previous: tracking.WorktreeRecord | None = None,
) -> tracking.WorktreeRecord:
    execution_spaces.require_current_execution_space(config)
    segment(path.stem, "worktree_id")
    root, _ = data._project_root(config.repo_name)
    record = data._canonical_record(root, path.stem, config.repo_name)
    if record is None or record.platform != detect_platform():
        raise ValueError("agenda doctor requires an exact local canonical worktree")
    execution_spaces.require_record_mutation(record, config, recover_receiving=False)
    if previous is not None and any(
        getattr(record, field) != getattr(previous, field)
        for field in ("creation_nonce", "worktree_path", "repo", "worktree_id")
    ):
        raise ValueError("worktree identity changed during agenda admission")
    verdict = sessions.verify_worktree_active(record, strict=True)
    if not verdict.probes_ok or not verdict.mux_probe_ok or verdict.active:
        raise ValueError(
            "agenda migration requires a confirmed stopped session; "
            "live or unknown liveness retains the source"
        )
    return record


def targeted_doctor(args: argparse.Namespace) -> int:
    """Compose the selected agenda owner without running general doctor passes."""
    report: dict[str, Any] = {"version": 1, "scope": "agenda", "worktree_id": args.agenda,
              "mode": "fix" if args.fix else "report", "error": None}
    try:
        if (
            getattr(args, "repair_orphaned_owner", None) is not None
            or any(getattr(args, field, None) for field in (
                "repair_record", "repair_creation", "repair_machine_record", "seed_id",
                "recovery_report", "guest_distribution", "gc_sessions", "prune_pivots",
                "apply_daemon_health", "bound_agent_end_field", "from_machine", "to_machine",
            ))
        ):
            raise ValueError("agenda doctor cannot be combined with another repair scope")
        segment(args.agenda, "worktree_id")
        config = cfg.load_config()
        path = cfg.tracking_dir() / f"{args.agenda}.yaml"
        record = _record(config, path)
        checkout = Path(record.worktree_path)
        source = Path(args.agenda_source_home or checkout / ".effort-agenda").absolute()
        legacy = checkout.absolute() / ".effort-agenda"
        if source != legacy and source.parent != legacy:
            raise ValueError("source must be the exact checkout-local agenda home or one direct lane")
        if source.resolve() != source:
            raise ValueError("indirect agenda source refused")
        script = _helper(Path(config.default_repo.anchor))
        support = _invoke(script, ["info"])
        if support.get("capabilities") != ["agenda-placement-doctor-v1"]:
            raise ValueError("selected efforts owner lacks agenda-placement-doctor-v1")
        report["placement"] = data.read_worktree_data(config.repo_name, args.agenda)
        if report["placement"] is not None:
            report["placement"] = asdict(report["placement"])
        if not args.fix:
            report["agenda"] = _invoke(script, [
                "inspect", "--source-home", str(source), "--worktree", args.agenda,
            ])
            if report["placement"] is not None:
                external = [
                    _invoke(script, [
                        "inspect-external", "--source-home", placement["state_home"],
                        "--worktree", args.agenda, "--store-id", placement["store_id"],
                    ])
                    for placement in report["placement"]["placements"]
                ]
                report["external"] = external
                if report["agenda"]["state"] == "absent":
                    states = {entry["state"] for entry in external}
                    report["agenda"] = {
                        "state": ("external-corrupt" if "external-corrupt" in states
                                  else "external-missing" if "external-missing" in states
                                  else "external-correct"),
                        "repairable": False,
                    }
        else:
            values = {
                "export-home": args.agenda_export_home, "lane": args.agenda_lane,
                "store-id": args.agenda_store_id, "operation-id": args.agenda_operation_id,
                "expected-revision": args.agenda_expected_revision,
            }
            if any(value is None for value in values.values()):
                raise ValueError("agenda --fix requires export home, lane, store ID, revision and operation ID")
            saved = Path(args.agenda_export_home).absolute()
            if saved.resolve() != saved or saved == checkout or checkout in saved.parents:
                raise ValueError("recovery export must be ordinary and outside the checkout")
            arguments = [
                "repair", "--source-home", str(source), "--provider-python", sys.executable,
                "--project", config.repo_name, "--worktree", args.agenda,
                "--creation-nonce", record.creation_nonce,
            ]
            for name, value in values.items():
                arguments += ["--" + name, str(value)]
            with fin.FinalizeLock(
                Path(config.default_repo.worktree_root) / ".finalize.lock",
                timeout=300, stale_after=3600,
            ), launch_admission.guard(path):
                _record(config, path, record)
                report["agenda"] = _invoke(script, arguments)
                if source != legacy and legacy.is_dir() and not any(legacy.iterdir()):
                    legacy.rmdir()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        report["error"] = str(exc)
    if args.json:
        output._json_output(report)
    elif report["error"]:
        output.err(report["error"])
    else:
        output.ok(f"Agenda {args.agenda}: {report['agenda']['state']}")
    return 1 if report["error"] else 0
