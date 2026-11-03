"""Optional historical cleanup; saved observations never grant disposal authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPORT_SCHEMA = "copilot-extensions.historical-cleanup-report"
CONTRACT_SCHEMA = "copilot-extensions.historical-cleanup-contract"
DETECTOR = "manager-prototype-commands-v1"
MAX_REPORT_BYTES = 1024 * 1024
MAX_FINDINGS = 1024
DETECTORS = {
    "manager": {"commands": {DETECTOR}, "runtime-residue": {
        "manager-sealed-payload-v1", "manager-sealed-application-v1", "manager-runtime-unknown",
    }},
    "agent-worktrees": {"daemons": {"worktrees-monitor-control-v1"}},
    "agent-index": {"registrations": {"index-adopted-legacy-tasks-v1"}},
}


def _families(scope: dict) -> dict:
    component = scope["component"]
    if component == "manager":
        from . import historical_runtimes

        return {"commands": _owner(), "runtime-residue": historical_runtimes}
    from . import historical_providers

    if component == "agent-worktrees":
        return {"daemons": historical_providers.WorktreesDaemons()}
    return {"registrations": historical_providers.IndexRegistrations(scope["selection"])}


def _family(scope: dict, finding: dict) -> str:
    for family, detectors in DETECTORS.get(scope["component"], {}).items():
        if finding.get("detector") in detectors:
            return family
    raise ValueError("cleanup detector does not belong to this component")


def _owner():
    from . import historical_commands

    return historical_commands


def capabilities() -> dict:
    return {
        "schema": CONTRACT_SCHEMA,
        "version": 1,
        "operations": ["capabilities", "inspect", "apply"],
        "selection": "explicit-report-ids",
        "components": {
            "manager": {
                "commands": {"detectors": [{"id": DETECTOR, "revision": 1}]},
                "daemons": {"supported": False},
                "registrations": {"supported": False},
                "runtime-residue": {"detectors": [{"id": name, "revision": 1} for name in sorted(
                    DETECTORS["manager"]["runtime-residue"] - {"manager-runtime-unknown"},
                )]},
            },
            "agent-worktrees": {"daemons": {
                "detectors": [{"id": "worktrees-monitor-control-v1", "revision": 1}],
                "provider": "selected-installed-runtime-capability-required",
            }},
            "agent-index": {"registrations": {
                "detectors": [{"id": "index-adopted-legacy-tasks-v1", "revision": 1}],
                "provider": "explicit-selected-cell-and-adopted-retained-inert-root",
                "platforms": ["windows"],
            }},
        },
        "apply": "resource-owner-revalidation-required",
        "update_prerequisite": False,
    }


def _scope(root: str | None) -> dict:
    from .self_install import default_root

    selected = Path(root) if root is not None else default_root()
    if not selected.is_absolute():
        raise ValueError("cleanup root must be absolute")
    return {
        "component": "manager",
        "root": str(selected),
        "bin_dir": str(Path.home() / ".local" / "bin"),
    }


def _finding_id(finding: dict) -> str:
    encoded = json.dumps(finding, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inspect(scope: dict) -> dict:
    errors, findings = [], []
    coverage = {name: {"supported": False} for name in (
        "commands", "daemons", "registrations", "runtime-residue",
    )}
    try:
        adapters = _families(scope)
    except (OSError, ValueError, RuntimeError) as error:
        adapters = {}
        errors.append({"family": "provider", "reason": str(error)})
    for family, adapter in adapters.items():
        coverage[family] = {"detectors": sorted(DETECTORS[scope["component"]][family]), "complete": False}
        try:
            observed = adapter.inspect(Path(scope["root"]) if "root" in scope else None,
                                     Path(scope["bin_dir"]) if "bin_dir" in scope else None)
            if not isinstance(observed, list) or len(observed) > 256:
                raise ValueError("historical owner returned an invalid inventory")
            admitted = []
            for finding in observed:
                if not isinstance(finding, dict) or "id" in finding:
                    raise ValueError("historical owner returned an invalid finding")
                _validate_observation(finding)
                if _family(scope, finding) != family:
                    raise ValueError("historical owner returned a foreign detector")
                admitted.append({**finding, "id": _finding_id(finding)})
            findings.extend(admitted)
            coverage[family]["complete"] = True
        except (OSError, ValueError, RuntimeError) as error:
            errors.append({"family": family, "reason": str(error)})
    return {
        "schema": REPORT_SCHEMA, "version": 1, "scope": scope, "coverage": coverage,
        "coverage_complete": all(item.get("complete") is True for item in coverage.values()),
        "findings": findings, "errors": errors,
    }


def _validate_observation(finding: dict) -> None:
    if (
        not isinstance(finding.get("detector"), str)
        or finding["detector"] not in {name for families in DETECTORS.values()
                                      for names in families.values() for name in names}
        or type(finding.get("revision")) is not int
        or finding["revision"] != 1 or not isinstance(finding.get("status"), str)
        or finding["status"] not in {"candidate", "preserved"}
        or not isinstance(finding.get("resource"), str) or not finding["resource"]
        or not isinstance(finding.get("reason"), str)
    ):
        raise ValueError("cleanup finding has an invalid owner observation")
    if finding["status"] == "candidate":
        digest = finding.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError("cleanup candidate has invalid content evidence")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("cleanup report contains duplicate JSON fields")
        result[key] = value
    return result


def _bounded_json(path: Path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("cleanup report exceeds the supported size")
    def reject_constant(value: str):
        raise ValueError(f"cleanup report contains an invalid JSON constant: {value}")

    return json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=reject_constant)


def _read_report(path: Path, scope: dict, selected: list[str]) -> list[dict]:
    report = _bounded_json(path)
    if not isinstance(report, dict) or (
        report.get("schema") != REPORT_SCHEMA or type(report.get("version")) is not int
        or report["version"] != 1 or report.get("scope") != scope
    ):
        raise ValueError("cleanup report contract or current installation scope differs")
    findings = report.get("findings")
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        raise ValueError("cleanup report has an invalid finding inventory")
    admitted = {}
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("cleanup report contains an invalid finding")
        observed = {key: value for key, value in finding.items() if key != "id"}
        identity = finding.get("id")
        if not isinstance(identity, str) or len(identity) != 64 or (
            identity != _finding_id(observed) or identity in admitted
        ):
            raise ValueError("cleanup report finding identity changed or is duplicated")
        _validate_observation(observed)
        _family(scope, observed)
        admitted[identity] = observed
    if len(set(selected)) != len(selected) or any(key not in admitted for key in selected):
        raise ValueError("select each reported candidate exactly once")
    result = [admitted[key] for key in selected]
    if any(type(item.get("revision")) is not int
           or item["revision"] != 1 or item.get("status") != "candidate" for item in result):
        raise ValueError("selection contains a preserved or unsupported finding")
    return result


def apply_selected(scope: dict, findings: list[dict]) -> dict:
    actions = []
    adapters = _families(scope)
    for family, adapter in adapters.items():
        selected = [finding for finding in findings if _family(scope, finding) == family]
        if not selected:
            continue
        try:
            if callable(getattr(adapter, "apply_many", None)):
                outcomes = adapter.apply_many(Path(scope["root"]) if "root" in scope else None,
                                     Path(scope["bin_dir"]) if "bin_dir" in scope else None, selected)
            else:
                outcomes = []
                for finding in selected:
                    try:
                        outcome = adapter.apply(
                            Path(scope["root"]) if "root" in scope else None,
                            Path(scope["bin_dir"]) if "bin_dir" in scope else None, finding,
                        )
                    except (OSError, ValueError, RuntimeError) as error:
                        outcome = {"resource": finding["resource"], "status": "failed", "reason": str(error)}
                    outcomes.append(outcome)
        except (OSError, ValueError, RuntimeError) as error:
            outcomes = [{"resource": item["resource"], "status": "failed", "reason": str(error)}
                        for item in selected]
        if not isinstance(outcomes, list) or len(outcomes) != len(selected):
            outcomes = [{} for _ in selected]
        for finding, outcome in zip(selected, outcomes):
            if not isinstance(outcome, dict) or (
                outcome.get("resource") != finding.get("resource")
                or not isinstance(outcome.get("reason"), str)
                or outcome.get("status") not in {
                    "applied", "confirmed", "already-absent", "preserved", "pending", "failed",
                }
            ):
                outcome = {"resource": finding["resource"], "status": "failed",
                           "reason": "resource owner returned an unconfirmed cleanup outcome"}
            if outcome["status"] == "confirmed":
                outcome = {**outcome, "status": "applied"}
            actions.append(outcome)
    return {
        "schema": "copilot-extensions.historical-cleanup-result", "version": 1,
        "scope": scope, "actions": actions,
        "settled": len(actions) == len(findings) and all(
            item["status"] in {"applied", "already-absent"} for item in actions
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="worktree-manager cleanup")
    commands = parser.add_subparsers(dest="operation", required=True)
    support = commands.add_parser("capabilities", help="describe this executable's optional support")
    support.add_argument("--json", action="store_true")
    for operation in ("inspect", "apply"):
        command = commands.add_parser(operation)
        command.add_argument("--component", choices=("manager", "agent-worktrees", "agent-index"), default="manager")
        command.add_argument("--root")
        command.add_argument("--selection", type=Path, help="Current Index cell selection; never taken from a saved report")
        command.add_argument("--json", action="store_true")
        if operation == "apply":
            command.add_argument("--report", type=Path, required=True)
            command.add_argument("--select", action="append", required=True)
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    if args.operation == "capabilities":
        report = capabilities()
        code = 0
    else:
        try:
            scope = _scope(args.root)
            if args.component != "manager":
                scope = {**scope, "component": args.component}
                if args.component == "agent-index":
                    if args.selection is None or args.root is None:
                        raise ValueError("Index cleanup requires --root and --selection for its adopted legacy installation")
                    scope["selection"] = str(args.selection.absolute())
                elif args.root is not None or args.selection is not None:
                    raise ValueError("Worktrees cleanup resolves its own native runtime scope")
                else:
                    scope = {"component": "agent-worktrees"}
            elif args.selection is not None:
                raise ValueError("Manager cleanup does not accept an Index selection")
            if args.operation == "inspect":
                report = inspect(scope)
                code = 0 if report["coverage_complete"] and not report["errors"] else 1
            else:
                findings = _read_report(args.report, scope, args.select)
                report = apply_selected(scope, findings)
                code = 0 if report["settled"] else 1
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"historical cleanup refused: {exc}", file=sys.stderr)
            return 2
    if args.json:
        print(json.dumps(report, indent=2))
    elif args.operation == "capabilities":
        print("Historical cleanup contract v1: inspect and explicit apply-selected requests.")
        print("Finite detectors: Manager commands/runtime residue, Worktrees monitor peers, selected Index tasks.")
    elif args.operation == "inspect":
        print("Historical cleanup is finite and owner-scoped; unsupported families remain explicit.")
        for finding in report["findings"]:
            print(f"{finding['id']}  {finding.get('status')}: {finding.get('resource')}")
            print(f"  {finding.get('reason')}")
        for error in report["errors"]:
            print(f"error: {error['family']}: {error['reason']}", file=sys.stderr)
        print("Save inspect --json output for an explicit apply --report FILE --select ID.")
    else:
        for action in report["actions"]:
            print(f"{action['status']}: {action.get('resource')}: {action.get('reason')}")
    return code
