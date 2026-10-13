"""Read-only Workers resource table and CLI entry points for local configuration."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import registrar_discovery as rd
from .config import overrides_path
from .overrides import overridden_off_ids
from .registrar import RegistrarError
from .registrar_reconcile import declared_registration_id, declaration_to_registration
from .supervisor_registration import registration_override_ids
from .worker_instances import (
    ACTIVATION_CONSENT, OWNER, active_dir, digest, read_records,
    save_instance, templates, validate_configuration, _validate_dependencies,
)

SAVE_EFFECTS = (
    "Save only: persists a distinct user-local configuration on this CLI's local machine. "
    "Does not edit repository/plugin/template source, publish changes, start a service, "
    "create tasks, or launch agents. An already activated version continues unchanged."
)
ACTIVATE_EFFECTS = (
    "Save and Activate: saves locally and publishes this version to the existing supervisor's "
    "registrar directory pointer, clearing this instance's disable overrides. "
    "Reconciliation may start/restart worker processes, bind queued tasks, and, for "
    "loop templates, create agentic tasks. No second supervisor is started. "
    "Without a running supervisor activation remains pending; publication is not proof of running."
)
TEMPLATE_DESCRIPTIONS = {
    "worker-pool": "Bounded pool that binds eligible queued tasks",
    "repository-issue-loop": "Discover and reserve repository issues for one worker",
    "goal-driven": "Drive repository issue goals through their completion gate",
    "backlog-triager": "Classify repository backlog issues using a consumer evaluator",
    "issue-reproducer": "Reproduce and classify issues using a consumer evaluator",
    "effort-builder": "Shape tracked efforts from repository issues",
    "effort-driver": "Drive tracked efforts through their completion gate",
    "reviewer": "Discover, review and evaluate repository pull requests",
    "conflict-resolution": "Reconcile conflicted pull requests under their landing policy",
}


def _fields(name: str, document: dict, repo_root: str = "") -> list[dict]:
    return [
        {"name": "name", "type": "text", "label": "Unique local instance name (immutable)", "default": name},
        {"name": "declaration", "type": "textarea", "label": "Complete declaration JSON", "default": json.dumps(document, indent=2)},
        {"name": "repo_root", "type": "text", "label": "Absolute repository root (required by issue/effort loops)", "default": repo_root},
    ]


def _template_defaults(fragment: dict) -> dict:
    from .registrar_recipes import deep_merge
    if fragment["kind"] == "repository-issue-loop":
        return deep_merge(fragment, {
            "repo": "", "source": "", "task_label": "", "cadence_seconds": 3600,
            "forge": {"provider": "github", "producer_login": ""},
            "reservation": {"label": "", "comment": True},
        })
    if fragment["kind"] == "reviewer-loop":
        return deep_merge(fragment, {
            "repo": "", "task_label": "",
            "emitter": {"command": [], "interval_seconds": 60},
            "evaluator": {"evaluator_spec": {"rules": []}},
        })
    return fragment


def _observation() -> dict:
    from .remote_dispatch import local_machine
    from .supervisor_registration import supervisor_lease_scope
    from .supervise_cli import _read_supervisor_runtime_status
    scope = supervisor_lease_scope(local_machine(), os.environ.get("AGENT_DISPATCH_ENV", "default"))
    payload, error = _read_supervisor_runtime_status(scope)
    if error or not payload:
        return {"known": False, "reason": error or "no local supervisor receipt"}
    updated = payload.get("updated_at")
    if not isinstance(updated, (int, float)) or not 0 <= time.time() - updated <= 60:
        return {"known": False, "reason": "stale local supervisor receipt"}
    return {"known": True, **payload}


def _states(declarations: tuple, overrides: set, observed: dict) -> dict:
    ids = [declared_registration_id(d) for d in declarations]
    disabled = [
        d for d in declarations
        if registration_override_ids(declaration_to_registration(d, machine=None)) & overrides
    ]
    enabled = "disabled" if len(disabled) == len(declarations) else (
        "partly-disabled" if disabled else "enabled"
    )
    running = set(observed.get("running", ())).intersection(ids)
    activity = "unknown"
    if observed["known"]:
        activity = "observed-running" if running else "not-observed-running"
    return {
        "enabled": enabled, "activity": activity, "registration_ids": ids,
        "statistics": {
            "declarations": len(declarations),
            "lanes": sum(d.concurrency for d in declarations if d.kind == "supervised-lane"),
            "observed_running": len(running) if observed["known"] else None,
        },
        "lanes": sum(d.concurrency for d in declarations if d.kind == "supervised-lane"),
    }


def _cards(row: dict, body: str) -> dict:
    row["card"] = {"title": row["title"], "body": body}
    row["save_card"] = {"title": f"Save {row['title']}", "body": f"{body}\n\n{SAVE_EFFECTS}"}
    row["activate_card"] = {"title": f"Save and Activate {row['title']}", "body": f"{body}\n\n{ACTIVATE_EFFECTS}"}
    return row


def catalog() -> dict:
    """No mkdir, network clients, mutation, service ensure, or task access."""
    from .overrides import load_overrides
    errors = []
    records = read_records(errors=errors)
    report = rd.RegistrarSources().refresh(emit_warnings=False)
    errors.extend(str(finding) for finding in report.combined.findings)
    if report.trusted_error:
        errors.append(report.trusted_error)
    overrides = overridden_off_ids(load_overrides(overrides_path()))
    observed = _observation()
    rows = []
    for index, error in enumerate(errors):
        rows.append(_cards({
            "id": f"discovery-error:{index}", "title": "Discovery requires repair",
            "group": "Discovery errors", "description": error,
            "validity": "error", "enabled": "unknown", "activity": "unknown",
            "editable": "false", "instantiable": "false",
            "provenance": "registrar discovery",
        }, error + "\n\nRun registrar doctor; unsafe mutations are refused."))
    local_names = set()
    for record in records:
        conf = record["configuration"]
        draft_errors = []
        declarations = ()
        try:
            _, declarations = validate_configuration(record["name"], conf["declaration"], conf["repo_root"])
            _validate_dependencies(
                declarations, conf["repo_root"],
                available_declarations=report.combined.declarations,
            )
        except RegistrarError as exc:
            draft_errors.append(str(exc))
        publication = active_dir() / f"{record['name']}.json"
        active_conf = None
        active_declarations = ()
        publication_errors = []
        try:
            from .worker_instances import _regular
            _regular(publication)
            if publication.exists():
                payload = rd._decode(publication.read_text(encoding="utf-8"), ".json", where=str(publication))
                loaded_conf = (
                    {"declaration": payload.get("configuration"), "repo_root": payload.get("repo_root", "")}
                    if payload.get("kind") == "user-worker-instance"
                    else {"declaration": payload, "repo_root": ""}
                )
                _, active_declarations = validate_configuration(
                    record["name"], loaded_conf["declaration"], loaded_conf["repo_root"],
                )
                active_conf = loaded_conf
        except (RegistrarError, OSError, UnicodeError) as exc:
            publication_errors.append(f"{publication}: cannot read published configuration: {exc}")
            errors.extend(publication_errors)
        local_names.update(d.name for d in (*declarations, *active_declarations))
        runtime_declarations = active_declarations if active_conf is not None else declarations
        state = _states(runtime_declarations, overrides, observed)
        discovered_names = {
            d.name for d in report.combined.declarations if d.owner == OWNER
        }
        discovered = bool(active_declarations) and all(
            d.name in discovered_names for d in active_declarations
        )
        published_revision = digest(active_conf) if active_conf is not None else None
        row = {
            "id": record["id"], "title": record["name"], "group": "Configured",
            "description": conf["declaration"].get("description") or "User-local worker instance",
            "validity": "invalid" if draft_errors or publication_errors else "valid",
            "editable": "true", "instantiable": "false",
            "revision": record["revision"], "fields": _fields(record["name"], conf["declaration"], conf["repo_root"]),
            "provenance": record["source"]["id"], "source": record["source"],
            "publication": "published" if discovered else (
                "not-discovered" if active_conf is not None else "draft"
            ),
            "published_revision": published_revision,
            "draft_state": "matches-published" if published_revision == record["revision"]
            else "modified" if active_conf is not None else "unpublished",
            "draft_statistics": {
                "declarations": len(declarations),
                "lanes": sum(d.concurrency for d in declarations if d.kind == "supervised-lane"),
            },
            "pool_toggleable": str(
                not errors and discovered and len(runtime_declarations) == 1
                and runtime_declarations[0].kind == "supervised-lane"
            ).lower(),
            "pool_name": runtime_declarations[0].name if len(runtime_declarations) == 1 else "",
            "owner": OWNER,
            "dependency_status": "invalid-or-missing" if draft_errors else "resolved",
            "dependency_errors": draft_errors,
            "publication_errors": publication_errors,
            **state,
        }
        if publication_errors:
            row.update(
                publication="error", draft_state="publication-error",
                enabled="unknown", activity="unknown", pool_toggleable="false",
                pool_name="", lanes=None,
                statistics={"declarations": None, "lanes": None, "observed_running": None},
            )
        elif active_conf is None:
            row.update(enabled="not-published", activity="not-published")
        body = (
            f"{row['description']}\n\nSource: {row['provenance']} ({record['source']['revision']}). "
            f"Local publication: {row['publication']}; effective state: {row['enabled']}; "
            f"activity: {row['activity']}. Statistics: {json.dumps(row['statistics'])}."
            f"\n\nDraft: {row['draft_state']} ({record['revision']}); "
            f"draft capacity: {json.dumps(row['draft_statistics'])}. "
            f"Published revision: {published_revision or 'none'}."
        )
        if draft_errors:
            body += "\n\nUnmet draft prerequisites: " + "; ".join(draft_errors)
        if publication_errors:
            body += "\n\nPublished configuration requires repair: " + "; ".join(publication_errors)
        fleets = [d.fleet for d in runtime_declarations if d.fleet.enabled]
        if fleets:
            body += (
                "\n\nFleet transport requires SSH. Host reachability and remote "
                "runtime readiness are unknown here; the existing FleetSpawner probes "
                "them at dispatch time. An all-asleep fleet defers without reserving tasks."
            )
        rows.append(_cards(row, body))
    for declaration in report.combined.declarations:
        if declaration.owner == OWNER and declaration.name in local_names:
            continue
        row = {
            "id": f"source:{declared_registration_id(declaration)}",
            "title": declaration.name, "group": "Source declarations",
            "description": declaration.description or f"{declaration.kind} declaration",
            "provenance": declaration.owner or "local",
            "source_path": declaration.source_path,
            "validity": "valid", "editable": "false", "instantiable": "false",
            "pool_toggleable": str(not errors and declaration.kind == "supervised-lane").lower(),
            "pool_name": declaration.name, "owner": declaration.owner or "local",
            **_states((declaration,), overrides, observed),
        }
        body = (f"{row['description']}\n\nOwned by {row['provenance']}. "
                "Read-only here: edit through the owning repository/plugin configuration flow.")
        try:
            _validate_dependencies(
                (declaration,), "", available_declarations=report.combined.declarations,
            )
        except RegistrarError as exc:
            row.update(dependency_status="invalid-or-missing", dependency_errors=[str(exc)])
            body += f"\n\nUnmet prerequisite: {exc}"
        else:
            row.update(dependency_status="resolved", dependency_errors=[])
        if declaration.fleet.enabled:
            body += "\n\nFleet hosts are probed by FleetSpawner at dispatch time; readiness is unknown here."
        rows.append(_cards(row, body))
    for name, fragment in templates().items():
        kind = fragment["kind"]
        prerequisite = (
            "Supply a unique instance name and complete JSON declaration. "
            + ("Requires repository root, repo, source, task_label, cadence_seconds, forge "
               "identity, reservation and any referenced evaluator/worker identity dependencies."
               if kind == "repository-issue-loop" else
               "Requires complete emitter discovery/evaluator policy and pool inputs."
               if kind == "reviewer-loop" else
               "Choose watched labels, placement/filter, capacity and worker body.")
        )
        rows.append(_cards({
            "id": f"template:{name}", "title": name, "group": "Available templates",
            "description": TEMPLATE_DESCRIPTIONS[name],
            "provenance": "agent-dispatch built-in recipe",
            "validity": "fragment" if name != "worker-pool" else "template",
            "enabled": "not-instantiated", "activity": "not-applicable",
            "editable": "false", "instantiable": "true", "prerequisites": prerequisite,
            "dependency_status": "inputs-required", "dependency_errors": [],
            "revision": digest(fragment), "fields": _fields("", _template_defaults(fragment)),
        }, f"Template: {name}\n\n{prerequisite}\n\nValidation refuses missing inputs/dependencies "
           "before saving or publishing. The template/source remains unchanged."))
    return {
        "entries": rows,
        "summary": {
            "configured": len(records), "source_declarations": sum(r["group"] == "Source declarations" for r in rows),
            "templates": len(templates()), "errors": errors,
            "activity_known": observed["known"], "activity_reason": observed.get("reason"),
            "target": "CLI-local machine; independent of operational machine selection",
        },
    }


def command(args: argparse.Namespace) -> int:
    try:
        if args.workers_command == "catalog":
            result = catalog()
        else:
            fields = {}
            for value in args.field:
                name, separator, content = value.partition("=")
                if not separator or name in fields:
                    raise RegistrarError("fields must be unique name=value pairs")
                fields[name] = content
            result = save_instance(
                args.id, args.revision, fields,
                activate=args.activate, consent=args.consent,
            )
        print(json.dumps(result, indent=2))
        return 1 if result.get("activation") == "failed" else 0
    except (RegistrarError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"agent-dispatch workers: {exc}", file=sys.stderr)
        return 2


def register_commands(sub) -> None:
    listing = sub.add_parser("catalog", help="read-only local worker instances, source declarations and templates")
    listing.set_defaults(func=command)
    save = sub.add_parser("save", help="save user-local configuration; activation requires explicit consent")
    save.add_argument("id")
    save.add_argument("--revision", required=True)
    save.add_argument("--field", action="append", default=[])
    save.add_argument("--activate", action="store_true")
    save.add_argument("--consent", choices=[ACTIVATION_CONSENT])
    save.set_defaults(func=command)
