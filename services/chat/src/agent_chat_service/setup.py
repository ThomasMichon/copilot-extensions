"""Explicit standalone manual-client selection; no host or enrollment effects."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

from .client import ClientError, emit
from .construction import _engine, client_candidate
from .store import ChatError, json_bytes, strict_json

_TARGETS = ("current-version", "last-known-good", "selected.json")
_JOURNAL = "installation.json"
_MAX_BYTES = 1024 * 1024


def _refuse(message: str) -> None:
    raise ClientError("setup_refused", message)


def _slot_name(value: str) -> str:
    if (not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}", value)
            or value == "dev"):
        _refuse("Provide a numbered slot name of 1-128 ordinary ASCII characters.")
    return value


def _publication():
    _engine()
    return importlib.import_module("agent_chat_service.versioned_runtime_publication")


def _absolute(value: Path) -> Path:
    path = Path(os.path.abspath(value))
    if not value.is_absolute() or str(path) != str(value):
        _refuse("Setup paths must be explicit absolute paths without aliases.")
    return path


def _read(path: Path, publication):
    before = publication.state_path(path, private=True)
    if before is None or before.size > _MAX_BYTES:
        _refuse("Required setup metadata is absent or oversized.")
    data = path.read_bytes()
    if publication.state_path(path, private=True) != before:
        _refuse("Setup metadata changed during its read.")
    return strict_json(data, max_bytes=_MAX_BYTES)


def _write(path: Path, data: dict, publication) -> None:
    contents = json_bytes(data) + b"\n"
    if len(contents) > _MAX_BYTES:
        _refuse("Installation journal reached its supported byte bound.")
    before = publication.state_path(path, private=True)
    fd, name = tempfile.mkstemp(prefix=".installation-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        if publication.state_path(path, private=True) != before:
            _refuse("Setup journal changed before commit.")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _identity(root: Path, installation_id: str, publication, *, create: bool = False):
    root = _absolute(root)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", installation_id):
        _refuse("Provide an ordinary nonsecret installation identity.")
    journal = root / _JOURNAL
    created = False
    if not os.path.lexists(root):
        if not create:
            return None
        publication.state_path(root)
        root.mkdir(mode=0o700)
        created = True
    # Probe parents before trusting this root, including on an unselected install.
    state = publication.state_path(journal, private=True)
    if state is None:
        if not created:
            _refuse("Root has no attributable Chat journal; legacy roots are not adopted.")
        data = {
            "schema": "agent-chat.installation", "version": 1,
            "root": str(root), "installation_id": installation_id,
            "selected": None, "history": [], "pending": None,
        }
        _write(journal, data, publication)
        return data
    data = _read(journal, publication)
    if (not isinstance(data, dict) or set(data) != {
            "schema", "version", "root", "installation_id", "selected", "history", "pending",
        } or data["schema"] != "agent-chat.installation" or type(data["version"]) is not int
            or data["version"] != 1 or data["root"] != str(root)
            or data["installation_id"] != installation_id or not isinstance(data["history"], list)):
        _refuse("Installation journal does not match this explicit installation.")
    return data


def _settled(data):
    if data["pending"] is not None:
        _refuse("An installation operation is unresolved; invoke explicit recover first.")


def _operation(root: Path, value: dict, publication):
    if not isinstance(value, dict) or set(value) != {
        "id", "baseline", "context", "expected", "receipts", "state",
    } or not isinstance(value["state"], str) or value["state"] not in {
        "pending", "published", "reversed",
    }:
        _refuse("Invalid installation operation.")
    if not isinstance(value["id"], str) or not re.fullmatch("[0-9a-f]{32}", value["id"]):
        _refuse("Invalid installation operation identity.")
    workspace = root / "operations" / value["id"]
    targets = {root / name for name in _TARGETS}
    if not isinstance(value["baseline"], dict) or set(value["baseline"]) != set(_TARGETS):
        _refuse("Invalid operation baseline.")
    baseline = {
        root / name: publication.PhysicalState.parse(state) if state is not None else None
        for name, state in value["baseline"].items()
    }
    if (not isinstance(value["expected"], list) or len(value["expected"]) != len(_TARGETS)
            or any(not isinstance(item, list) or len(item) != 2
                   or not isinstance(item[0], str) or Path(item[0]) not in targets
                   or not isinstance(item[1], str) or not re.fullmatch("[0-9a-f]{64}", item[1])
                   for item in value["expected"])
            or {Path(item[0]) for item in value["expected"]} != targets):
        _refuse("Invalid operation output bindings.")
    if (not isinstance(value["receipts"], list) or not value["receipts"]
            or any(not isinstance(name, str)
                   or not re.fullmatch(
                       r"copy-forward\.jsonl|copy-recovery\.1\.jsonl|recovery-[0-9a-f]{32}\.jsonl",
                       name,
                   )
                   for name in value["receipts"])
            or len(set(value["receipts"])) != len(value["receipts"])):
        _refuse("Invalid operation receipt bindings.")
    context = workspace / "copy-context.json"
    if publication.state_path(context, private=True) != publication.PhysicalState.parse(
        value["context"],
    ):
        _refuse("Operation context changed its captured physical binding.")
    context_root, entries = publication.read_context(context)
    if context_root != root or set(entries) != targets:
        _refuse("Operation context does not match the installation.")
    for index, name in enumerate(_TARGETS):
        before = baseline[root / name]
        backup, captured = entries[root / name]
        if before is None:
            if backup is not None or captured is not None:
                _refuse("Operation context changed its captured absent baseline.")
        elif (backup != workspace / f"copy-before.{index}" or captured is None
              or any(getattr(before, field) != getattr(captured, field)
                     for field in ("digest", "size", "mode"))):
            _refuse("Operation context changed its captured backup binding.")
    receipts = []
    for name in value["receipts"]:
        receipt = workspace / name
        if name == "copy-forward.jsonl" or publication.state_path(receipt) is not None:
            receipts.append(receipt)
    return workspace, context, receipts, baseline


def _proofs(root: Path, data: dict, publication):
    proofs = {root / name: [] for name in _TARGETS}
    allowed = {path: set() for path in proofs}
    for operation in data["history"]:
        _operation(root, operation, publication)
        if operation["state"] not in {"published", "reversed"}:
            _refuse("Unsettled operation cannot prove selected ownership.")
        for path, digest in operation["expected"]:
            allowed[Path(path)].add(digest)
    for operation in data["history"]:
        _, _, receipts, _ = _operation(root, operation, publication)
        for receipt in receipts:
            values = publication.read_receipt(
                receipt, root, set(proofs), recovery_only=operation["state"] == "reversed",
            )
            # Recovery restores a prior selection, whose bytes are declared in history.
            for path, states in values.items():
                if any(state.digest not in allowed[path] for state in states):
                    _refuse("Receipt bytes are outside the installation history.")
                proofs[path].extend(states)
    return proofs


def _owned(root: Path, data: dict, publication):
    proofs = _proofs(root, data, publication)
    for path, states in proofs.items():
        current = publication.state_path(path)
        if current is None or current not in states:
            _refuse("A selected object is absent or physically replaced; preserving it.")
    selected = _read(root / "selected.json", publication)
    if selected != data["selected"]:
        _refuse("Selected metadata differs from its operation journal.")
    if (not isinstance(selected, dict) or set(selected) != {
            "schema", "version", "installation_id", "role", "slot", "payload_hash",
            "source", "config", "config_sha256",
        } or selected["schema"] != "agent-chat.selection" or type(selected["version"]) is not int
            or selected["version"] != 1 or selected["installation_id"] != data["installation_id"]
            or selected["role"] != "manual-client"
            or any(not isinstance(selected[key], str)
                   or not re.fullmatch("[0-9a-f]{64}", selected[key])
                   for key in ("payload_hash", "config_sha256"))
            or any(not isinstance(selected[key], str) for key in ("source", "config"))):
        _refuse("Invalid selected manual-client identity.")
    _slot_name(selected["slot"])
    for name in _TARGETS[:2]:
        if (root / name).read_bytes() != (selected["slot"] + "\n").encode():
            _refuse("Runtime selectors do not match the selected manual client.")
    return selected


def _config(path: Path, root: Path, publication) -> str:
    path = _absolute(path)
    if path.is_relative_to(root):
        _refuse("Client configuration must be outside the runtime root.")
    from .cli import configured
    before = publication.state_path(path, private=True)
    if before is None:
        _refuse("Explicit client configuration is missing.")
    client = configured(str(path))
    if client.directory.is_relative_to(root):
        _refuse("Client state must be outside the runtime root.")
    if publication.state_path(path, private=True) != before:
        _refuse("Client configuration changed while validated.")
    return before.digest


def _argv(root: Path, selected: dict):
    runtime = _engine().runtime
    version = selected["slot"]
    slot = root / "versions" / version
    if not runtime.is_complete(root, version, expect_hash=selected["payload_hash"]):
        _refuse("Selected completed runtime is unavailable.")
    python = runtime.slot_python(root, version)
    if python is None:
        _refuse("Selected interpreter is unavailable.")
    return [
        str(python), "-I", "-S", "-B", str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
        "-m", "agent_chat_service.cli", "--config", selected["config"],
    ]


def status(root: Path, installation_id: str) -> dict:
    """Read local ownership only; no locks, construction, subprocess or repair."""
    publication = _publication()
    data = _identity(root, installation_id, publication)
    result = {
        "schema": "agent-chat.setup-status", "version": 1, "installation_id": installation_id,
        "role": "manual-client", "state": "absent", "commands": [],
        "mutations_performed": False, "remote_verified": False,
        "release_selection_verified": False,
    }
    if data is None:
        return result
    if data["pending"] is not None:
        result["state"] = "recovery-required"
        return result
    if data["selected"] is None:
        if any(publication.state_path(root / name) is not None for name in _TARGETS):
            _refuse("Unexpected selectors exist in an unselected installation.")
        result["state"] = "unselected"
        return result
    selected = _owned(root, data, publication)
    _argv(root, selected)
    if _config(Path(selected["config"]), root, publication) != selected["config_sha256"]:
        _refuse("Selected client configuration drifted; no implicit adoption.")
    result.update(state="selected", selection=selected)
    return result


def catalog(root: Path, installation_id: str) -> dict:
    """Derive invocation from the selected slot's independently verified catalog."""
    publication = _publication()
    data = _identity(root, installation_id, publication)
    if data is None:
        _refuse("No installation is selected.")
    _settled(data)
    if data["selected"] is None:
        _refuse("No installation is selected.")
    selected = _owned(root, data, publication)
    if _config(Path(selected["config"]), root, publication) != selected["config_sha256"]:
        _refuse("Selected client configuration drifted.")
    from .transport import run_command
    argv = _argv(root, selected)
    installed = strict_json(
        run_command([*argv, "catalog"], timeout=30, max_capture=_MAX_BYTES),
        max_bytes=_MAX_BYTES,
    )
    if (not isinstance(installed, dict)
            or installed.get("schema") != "agent-chat.client-catalog"
            or installed.get("evidence") != "local-installed-resource-record"):
        _refuse("Selected runtime returned an invalid installed catalog.")
    _owned(root, data, publication)
    if _config(Path(selected["config"]), root, publication) != selected["config_sha256"]:
        _refuse("Selected client configuration changed during catalog validation.")
    return {
        "schema": "agent-chat.selected-catalog", "version": 1,
        "installation_id": installation_id, "role": "manual-client",
        "selection": selected, "installed": installed,
        "commands": [{"id": "agent-chat", "argv": argv, "shell": "direct",
                      "availability": "ready"}],
        "release_selection_verified": False, "remote_verified": False,
    }


def install(root: Path, installation_id: str, *, config: Path, version: str,
            project: Path, uv: Path, python: Path) -> dict:
    version = _slot_name(version)
    publication = _publication()
    root = _absolute(root)
    project, uv, python = (_absolute(path) for path in (project, uv, python))
    config_digest = _config(config, root, publication)
    # Validate an existing root before the operation lease can create anything.
    data = _identity(root, installation_id, publication, create=True)
    with publication.target_leases(root, [root / _JOURNAL]):
        data = _identity(root, installation_id, publication)
        _settled(data)
        if data["selected"] is not None:
            old = _owned(root, data, publication)
            if old["config"] != str(config) or old["config_sha256"] != config_digest:
                _refuse("Install/update does not implicitly migrate or rewrite configuration.")
        elif any(publication.state_path(root / name) is not None for name in _TARGETS):
            _refuse("Unattributable selectors exist; preserving them.")
        with client_candidate(
            root, version, project=project, uv=uv, python=python,
        ) as candidate:
            selection = {
                "schema": "agent-chat.selection", "version": 1,
                "installation_id": installation_id, "role": "manual-client",
                "slot": candidate.slot.name, "payload_hash": candidate.payload_hash,
                "source": str(_absolute(project)),
                "config": str(config), "config_sha256": config_digest,
            }
            if data["selected"] == selection:
                _owned(root, data, publication)
                if _config(config, root, publication) != config_digest:
                    _refuse("Client configuration changed during candidate validation.")
                return {"state": "unchanged", "selection": selection}
            operation_id = uuid.uuid4().hex
            workspace = root / "operations" / operation_id
            workspace.mkdir(parents=True, mode=0o700)
            contents = [(version + "\n").encode()] * 2 + [json_bytes(selection) + b"\n"]
            bindings = []
            for index, (name, value) in enumerate(zip(_TARGETS, contents)):
                source = workspace / f"input-{index}"
                fd = os.open(source, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(value)
                bindings.append(publication.CopyBinding(
                    source, root / name, hashlib.sha256(value).hexdigest(),
                ))

            def validate():
                if _config(config, root, publication) != config_digest:
                    _refuse("Client configuration changed before publication.")
                if data["selected"] is not None:
                    # Only prepublication validation may use the old proofs.
                    if data["pending"] is None:
                        _owned(root, data, publication)

            def pending():
                if data["selected"] is not None:
                    _owned(root, data, publication)
                elif any(state is not None for state in publisher.baseline.values()):
                    _refuse("Foreign selectors appeared before first publication.")
                data["pending"] = {
                    "kind": "publish", "operation": {
                        "id": operation_id,
                        "state": "pending",
                        "baseline": {path.name: asdict(state) if state else None
                                     for path, state in publisher.baseline.items()},
                        "context": asdict(publication.state_path(publisher.context, private=True)),
                        "expected": publisher.expected,
                        "receipts": ["copy-forward.jsonl", "copy-recovery.1.jsonl"],
                    },
                }
                _write(root / _JOURNAL, data, publication)

            publisher = publication.CopyPublication(
                root, workspace, bindings, validate_inputs=validate, before_effects=pending,
            )
            publisher.publish()
            try:
                publisher.retain()
            except BaseException:
                publisher.rollback()
                raise
            data["pending"]["operation"]["state"] = "published"
            data["history"].append(data["pending"]["operation"])
            data["selected"], data["pending"] = selection, None
            _write(root / _JOURNAL, data, publication)
            return {"state": "selected", "selection": selection}


def _recover(root: Path, data: dict, publication) -> None:
    pending = data["pending"]
    if (not isinstance(pending, dict) or set(pending) != {"kind", "operation"}
            or pending["kind"] not in {"publish", "remove"}):
        _refuse("Invalid unresolved installation operation.")
    operation = pending["operation"]
    workspace, context, receipts, baseline = _operation(root, operation, publication)
    name = "recovery-" + uuid.uuid4().hex + ".jsonl"
    # Record the output locator before reversal, including on caller death.
    previous = list(receipts)
    operation["receipts"].append(name)
    _write(root / _JOURNAL, data, publication)
    if pending["kind"] == "remove":
        receipts = [path for op in data["history"]
                    for path in _operation(root, op, publication)[2]]
        expected = [binding for op in data["history"] for binding in op["expected"]]
    else:
        receipts, expected = previous, operation["expected"]
    receipts = [path for path in receipts if publication.state_path(path) is not None]
    foreign = publication.recover_uncontested(
        context, receipts, workspace / name, expected, baseline, recovery_only=True,
    )
    if foreign:
        _refuse("Foreign replacements preserved; operation remains recovery-required.")
    if pending["kind"] == "publish":
        operation["state"] = "reversed"
        data["history"].append(operation)
    else:
        data["selected"] = None
    data["pending"] = None
    _write(root / _JOURNAL, data, publication)


def recover(root: Path, installation_id: str) -> dict:
    publication = _publication()
    data = _identity(root, installation_id, publication)
    if data is None:
        _refuse("No installation operation exists.")
    with publication.target_leases(root, [root / _JOURNAL]):
        data = _identity(root, installation_id, publication)
        if data["pending"] is not None:
            _recover(root, data, publication)
    return {"state": "recovered", "selection": data["selected"]}


def uninstall(root: Path, installation_id: str) -> dict:
    """Withdraw only attributable selectors; preserve slots, proofs, config and data."""
    publication = _publication()
    data = _identity(root, installation_id, publication)
    if data is None:
        return {"state": "absent"}
    with publication.target_leases(root, [root / _JOURNAL]):
        data = _identity(root, installation_id, publication)
        _settled(data)
        if data["selected"] is None:
            return {"state": "unselected"}
        _owned(root, data, publication)
        # The first publication captured absence. Later receipts prove the current
        # physical generation; reversing this context removes rather than reselects.
        operation = dict(data["history"][0])
        operation["receipts"] = list(operation["receipts"])
        data["pending"] = {"kind": "remove", "operation": operation}
        _write(root / _JOURNAL, data, publication)
        _recover(root, data, publication)
    return {"state": "unselected", "preserved": ["runtime-slots", "configuration", "data", "proofs"]}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict", newline="\n")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--installation-id", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "update"):
        child = commands.add_parser(name)
        for option in ("config", "project", "uv", "python"):
            child.add_argument("--" + option, type=Path, required=True)
        child.add_argument("--version", required=True)
    for name in ("status", "catalog", "recover", "uninstall"):
        commands.add_parser(name)
    args = parser.parse_args(argv)
    try:
        if args.command in {"install", "update"}:
            result = install(
                args.root, args.installation_id, config=args.config, version=args.version,
                project=args.project, uv=args.uv, python=args.python,
            )
        else:
            result = globals()[args.command](args.root, args.installation_id)
        emit(result, sys.stdout)
        return 0
    except (ClientError, ChatError) as error:
        emit(error.as_dict(), sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        emit({"error": "setup_failed", "message": str(error)}, sys.stderr)
        return 1
    except KeyboardInterrupt:
        emit({"error": "interrupted", "message": "Inspect status and recover before retrying."},
             sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
