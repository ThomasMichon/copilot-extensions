"""Manager's read-only installation workspace and target-scoped driver adapter."""

from __future__ import annotations

import argparse
import json
import os
import sys

from update_telemetry import redact

from . import __version__, engine_client

PIVOT_NAME = "manager-installation-updates"
ENGINE_INVENTORY_TIMEOUT = 8


def _stored_target(arguments):
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False, exit_on_error=False)
    parser.add_argument("--target")
    try:
        parsed, _unknown = parser.parse_known_args(arguments)
    except argparse.ArgumentError as error:
        raise ValueError(f"Invalid durable update target: {error}") from error
    return parsed.target


def _restore_engine_context(pointer):
    from . import agent_plugin_runtime

    environment = {**os.environ, "COPILOT_EXTENSIONS_CONTEXT": pointer}
    resolved = agent_plugin_runtime._namespaced_resolution("agent-worktrees", environment=environment)
    if not resolved or resolved.get("actualMode") != "namespaced":
        raise ValueError("Selected engine installation context is invalid or inactive; refresh from its owning engine")
    os.environ["COPILOT_EXTENSIONS_CONTEXT"] = pointer


def _engine_rows(payload):
    fields = {"id", "title", "kind", "enabled", "installed", "payload", "selected", "running", "source", "updatable", "limitation"}
    if not isinstance(payload, dict) or type(payload.get("contract")) is not int or payload["contract"] != 1:
        raise ValueError("Selected engine lacks the targeted-update inventory contract; update the suite first")
    rows = payload.get("entries")
    if not isinstance(rows, list):
        raise ValueError("Selected engine returned malformed installation inventory; update its owner and refresh")
    seen = {"worktree-manager"}
    for row in rows:
        if (
            not isinstance(row, dict) or not fields.issubset(row)
            or any(not isinstance(row[key], str) for key in fields - {"installed", "updatable"})
            or type(row["installed"]) is not bool or type(row["updatable"]) is not bool
            or not row["id"] or row["id"] in seen
        ):
            raise ValueError("Selected engine returned invalid or duplicate component facts; refresh after repairing its inventory")
        seen.add(row["id"])
    return rows


def run_targeted_update(rest: list[str]) -> int:
    from .update_driver_cli import parse_options
    from .update_events import UpdateRun

    _options, forwarded = parse_options(rest)
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--target", required=True)
    parser.add_argument("--project")
    parser.add_argument("--no-manager", action="store_true")
    args = parser.parse_args(forwarded)
    run = UpdateRun()
    try:
        if args.target == "worktree-manager":
            from .self_install import self_update

            with run.self_update_phase():
                result = self_update(dry_run=False)
                cutover = result.cutover if isinstance(result.cutover, dict) else {}
                failed = cutover.get("action") == "error" or (
                    isinstance(cutover.get("result"), dict) and cutover["result"].get("ok") is False
                )
                run.say(f"Manager: {result.action}; {result.reason or result.version}")
                if failed:
                    run.say(f"Manager mux cutover failed: {cutover}", state="failed")
                run.record_self_update(result.action, failed, str(cutover) if failed else result.reason)
            return run.finish(0 if result.action in {"updated", "already-current"} and not failed else 1)
        from .update_orchestration import preflight_runtime_source

        if not preflight_runtime_source(run):
            return run.finish(1)
        # Strictly parsed by the selected engine; an older provider rejects it.
        rc = engine_client.run_engine_passthrough(
            args.project, ["update", "--no-manager", "--target", args.target],
        )
        return run.finish(rc)
    except (engine_client.EngineError, OSError, ValueError) as error:
        run.say(str(error), state="failed")
        return run.finish(1)
    except BaseException as error:
        run.abort(error)
        raise


def _operations(*, engine_source=None):
    import update_driver
    from .update_driver_cli import _source_key

    operations = {}
    source = _source_key()
    for request in update_driver.status():
        argv = request.spec.argv
        target = _stored_target(argv)
        if target is None or (request.spec.backend == "manager" and target != "worktree-manager"):
            continue
        expected_source = source if request.spec.backend == "manager" else (
            engine_source if request.spec.backend == "engine" else None
        )
        if expected_source is None or request.spec.source != expected_source:
            continue
        state = request.state
        if state == "completed":
            state = "succeeded" if request.exit_code == 0 else "failed"
        elif state == "pending":
            state = "queued"
        operations[target] = {
            "state": state, "request_id": request.id, "error": request.error,
            "exit_code": request.exit_code, "log": str(request.log_path),
        }
        if request.log_path.is_file():
            with request.log_path.open("rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 4096))
                operations[target]["progress"] = redact(stream.read().decode("utf-8", errors="replace"))
    return operations


def inventory():
    from .catalog import load_catalog
    from .discovery import DiscoverySource, discover
    from .self_install import current_version, default_root, manager_ref, manager_repo

    root = default_root()
    rows = [{
        "id": "worktree-manager", "title": "Worktree Manager", "kind": "application",
        "enabled": "not applicable", "installed": current_version(root) is not None,
        "payload": __version__, "selected": current_version(root) or "missing",
        "running": f"{__version__} (this process; daemon unknown)",
        "source": f"{manager_repo(root)} @ {manager_ref(root)}", "updatable": True,
        "limitation": "",
    }]
    findings = []
    authored = {}
    try:
        authored = {item.name: item for item in load_catalog().plugins}
    except (OSError, ValueError, KeyError, TypeError) as error:
        findings.append(f"Manager catalog unavailable: {error}; repair the Manager payload")
    known = {name: item.summary for name, item in authored.items()}
    discovery = DiscoverySource("none", "", ())
    try:
        discovery = discover(allow_remote=False)
        known.update({item.name: item.description for item in discovery.plugins})
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        findings.append(f"Local catalog unavailable: {error}; refresh or repair its metadata")
    engine_error = None
    engine_source = None
    try:
        from pathlib import Path
        from plugin_resolve import load_marketplace
        from plugin_resolve.inventory import installed_item, inventory_paths

        home, cache = inventory_paths(Path.cwd(), None)
        item = installed_item("agent-worktrees@copilot-extensions", home, cache)
        catalog_root = (
            item.get("reference", {}).get("marketplace_root")
            or item.get("inventory", {}).get("catalog_root")
        )
        installed_catalog = load_marketplace(catalog_root) if catalog_root else None
        if installed_catalog is not None:
            known.update({name: "" for name in installed_catalog.plugins})
        elif discovery.kind == "none":
            findings.append("Known catalog unavailable: refresh the marketplace through the existing suite updater; installed inventory remains inspectable.")
    except (OSError, ValueError) as error:
        findings.append(f"Known catalog unavailable: {error}; retry after repairing marketplace metadata")
    try:
        payload = engine_client.run_json(None, ["update", "--inventory", "--json"], timeout=ENGINE_INVENTORY_TIMEOUT)
        owner_rows = _engine_rows(payload)
        engine_source = payload.get("execution_source")
        if engine_source is not None and not isinstance(engine_source, str):
            raise ValueError("Selected engine returned invalid execution-source identity")
        rows.extend(owner_rows)
    except (engine_client.EngineError, OSError, ValueError) as error:
        engine_error = f"Engine inventory unavailable: {error}. Retry after updating the suite through its existing command."
        findings.append(engine_error)
    found = {row["id"] for row in rows}
    for name in sorted(known.keys() - found):
        rows.append({
            "id": name, "title": name, "kind": "known component", "installed": "unknown" if engine_error else False,
            "enabled": "unknown", "payload": "unknown / missing", "selected": "unknown",
            "running": "unknown", "source": discovery.detail or "Manager catalog",
            "updatable": False, "limitation": engine_error or "Not installed; use setup/enablement before update",
        })
    history_error = None
    try:
        operations = _operations(engine_source=engine_source)
    except (OSError, ValueError, RuntimeError) as error:
        operations = {}
        history_error = f"Durable update history unavailable: {error}; retry refresh"
        findings.append(history_error)
    for row in rows:
        if row["id"] in authored:
            item = authored[row["id"]]
            row["kind"] = f"{getattr(item, 'kind', 'component')} / {row['kind']}"
        if row["id"] != "worktree-manager":
            from .runtime_source_config import configured_source, require_marketplace

            try:
                selection = configured_source(root)
                row["source"] += "; runtime policy: " + json.dumps(selection.document())
                require_marketplace(root)
            except (OSError, ValueError) as error:
                row.update(updatable=False, limitation=str(error))
        row["source"] = redact(row["source"])
        row["limitation"] = redact(row.get("limitation") or "")
        operation = operations.get(row["id"])
        unavailable = history_error or (engine_error and row["id"] != "worktree-manager")
        row["activity"] = operation["state"] if operation else "unknown" if unavailable else "idle"
        detail = [
            f"{key}: {row[key]}" for key in (
                "kind", "installed", "enabled", "payload", "selected", "running", "source",
            )
        ]
        if operation:
            detail.append("Durable operation: " + json.dumps(operation, ensure_ascii=True))
        if history_error:
            detail.append(history_error)
        if row.get("limitation"):
            detail.append("Update unavailable: " + row["limitation"])
        detail.append("Refresh reads actual state. Dismissal does not cancel durable installation. Background maintenance is not enabled by this page.")
        row["card"] = {"title": row["title"], "status": row["activity"], "body": redact("\n\n".join(detail))}
    warnings = [redact(finding) for finding in findings]
    return {"entries": rows, "summary": {
        "errors": warnings, "banner_text": "\n".join(warnings), "banner_level": "warn",
    }}


def request_result_screen(label):
    from .production_picker.picker_tui.configuration_form import ConfigurationResultScreen

    class InstallationRequestScreen(ConfigurationResultScreen):
        pending_message = "Submitting to the durable installation owner..."
        success_message = "Request accepted. Installation is not yet complete; return and refresh for owner progress."
        failure_message = "Submission failed. Inspect the owner receipt below before retrying."

    return InstallationRequestScreen(label)


def submit(target: str) -> int:
    from .update_driver_cli import cmd_update_request

    # Re-read readiness at invocation, not just when the row was rendered.
    row = next((row for row in inventory()["entries"] if row["id"] == target), None)
    if row is None or not row["updatable"]:
        print(f"Update blocked for {target}: {row['limitation'] if row else 'unknown component'}", file=sys.stderr)
        return 1
    return cmd_update_request(["--target", target, "--detach", "--json"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--engine-argv", help=argparse.SUPPRESS)
    parser.add_argument("--engine-context", help=argparse.SUPPRESS)
    parser.add_argument("command", choices=("list", "update"))
    parser.add_argument("target", nargs="?")
    args = parser.parse_args(argv)
    if args.engine_argv:
        os.environ[engine_client.ENGINE_ARGV_ENV] = args.engine_argv
    warning = engine_client.accept_inherited_engine_command()
    if warning:
        parser.error(warning)
    if args.engine_context:
        try:
            _restore_engine_context(args.engine_context)
        except ValueError as error:
            parser.error(str(error))
    if args.command == "list":
        if args.target:
            parser.error("list does not accept a target")
        print(json.dumps(inventory()))
        return 0
    if not args.target:
        parser.error("update requires a component target")
    return submit(args.target)


def configuration_pivot():
    from .production_picker.picker_tui.pivots import parse_manifest

    command = [sys.executable, "-B", "-m", "worktree_manager.installation_updates"]
    inherited = engine_client.inherited_engine_command()
    if inherited:
        command += ["--engine-argv", json.dumps(inherited)]
    pointer = os.environ.get("COPILOT_EXTENSIONS_CONTEXT")
    if pointer:
        command += ["--engine-context", pointer]
    return parse_manifest({
        "label": "Installation & Updates", "placement": "config", "scope": "global",
        "list": [*command, "list"], "id_field": "id", "title_field": "title",
        "columns": [
            {"key": key, "header": label, "width": width}
            for key, label, width in (
                ("title", "Component", 27), ("payload", "Payload", 16),
                ("selected", "Selected runtime", 18), ("running", "Observed running", 23),
                ("activity", "Update", 12), ("enabled", "Enabled", 10),
            )
        ],
        "actions": [
            {"key": "details", "label": "Installation details / limitations", "kind": "card"},
            {"key": "update", "label": "Update selected component only", "confirm": True,
             "run": [*command, "update", "{id}"], "when": {"updatable": True}},
        ],
    }, name=PIVOT_NAME, source_path=__file__)


if __name__ == "__main__":
    raise SystemExit(main())
