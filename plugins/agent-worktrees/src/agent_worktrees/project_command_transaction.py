"""Recoverable command/receipt publication under the installer's existing lock."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import uuid
from collections.abc import Callable
from pathlib import Path

from .project_command_migration import CommandAdmission


def _digest(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _decode(value: str | None) -> bytes | None:
    return base64.b64decode(value, validate=True) if value is not None else None


def _capture(paths: list[Path]) -> dict:
    from . import installer as inst

    return {
        inst._stub_key(p.name): {
            "bytes": base64.b64encode(p.read_bytes()).decode("ascii") if p.exists() else None,
            "mode": p.stat().st_mode & 0o777 if p.exists() else 0o755,
        }
        for p in paths
    }


def _replace(
    path: Path, data: bytes | None, mode: int,
    *, expected: tuple[bytes | None, ...] | None = None,
) -> None:
    if expected is not None:
        _check_admitted(path, expected)
    if data is None:
        path.unlink(missing_ok=True)
        return
    if path.is_file() and path.read_bytes() == data:
        if path.stat().st_mode & 0o777 != mode:
            if expected is not None:
                _check_admitted(path, expected)
            path.chmod(mode)
        return
    staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with staged.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        staged.chmod(mode)
        if expected is not None:
            _check_admitted(path, expected)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def _check_admitted(path: Path, expected: tuple[bytes | None, ...]) -> None:
    from . import installer as inst

    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise inst.BinstubContentError(f"interrupted command has linked/non-file sibling: {path.name}")
    actual = inst._file_sha256(path) if path.exists() else None
    if actual not in tuple(_digest(data) for data in expected):
        raise inst.BinstubContentError(f"interrupted command has modified sibling: {path.name}; restore the recorded command before retry")


def _check_states(paths: list[Path], before: dict, after: dict) -> None:
    from . import installer as inst

    for p in paths:
        key = inst._stub_key(p.name)
        _check_admitted(p, (_decode(before[key]["bytes"]), _decode(after[key]["bytes"])))


def _normalize_states(states: object, expected: set[str]) -> dict:
    from . import installer as inst

    if not isinstance(states, dict):
        raise ValueError("invalid sibling state map")
    normalized = {}
    for name, entry in states.items():
        if not isinstance(name, str):
            raise ValueError("invalid sibling name")
        key = inst._stub_key(name)
        if key not in expected or key in normalized:
            raise ValueError("unexpected or case-colliding sibling name")
        if not isinstance(entry, dict) or set(entry) != {"bytes", "mode"}:
            raise ValueError("invalid sibling state")
        if entry["bytes"] is not None and not isinstance(entry["bytes"], str):
            raise ValueError("invalid sibling bytes")
        _decode(entry["bytes"])
        if type(entry["mode"]) is not int or not 0 <= entry["mode"] <= 0o777:
            raise ValueError("invalid file mode")
        normalized[key] = entry
    if set(normalized) != expected:
        raise ValueError("invalid sibling set")
    return normalized


def _validate_final_shape(final: object, expected: set[str]) -> None:
    from . import installer as inst

    if not isinstance(final, dict) or "transaction" in final:
        raise ValueError("invalid intended receipt object")
    for section in ("owner", "project", "runtime", "stubs"):
        fields = final.get(section)
        if not isinstance(fields, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in fields.items()
        ):
            raise ValueError(f"invalid intended receipt {section}")
    if (
        not set(inst._OWNER_IDENTITY_FIELDS + ("plugin_version",)) <= final["owner"].keys()
        or not {"name", "path", "remote"} <= final["project"].keys()
        or not {"root", "resolver"} <= final["runtime"].keys()
        or final["runtime"]["resolver"] != "payload-local"
        or type(final.get("version")) is not int
    ):
        raise ValueError("incomplete intended receipt")
    hashes = final["stubs"]
    normalized = {inst._stub_key(key) for key in hashes}
    if normalized != expected or len(normalized) != len(hashes) or any(
        re.fullmatch(r"[0-9a-f]{64}", value) is None for value in hashes.values()
    ):
        raise ValueError("invalid intended receipt hashes")


def recover(
    project: str, *, owner: dict, identity: dict | None,
    on_admitted: Callable[[], None] | None = None,
) -> bool:
    """Finish a previously admitted transition; unknown intermediate bytes block."""
    from . import installer as inst
    from . import project_command_migration as migration
    from . import project_command_templates as templates

    receipt = inst._read_receipt(project)
    if receipt is None or "transaction" not in receipt:
        return False
    paths = [p for p, _ in inst._project_binstub_specs(project)]
    try:
        tx = receipt["transaction"]
        if not isinstance(tx, dict) or type(tx.get("version")) is not int or tx["version"] != 1:
            raise ValueError("invalid transaction object/version")
        expected = {inst._stub_key(p.name) for p in paths}
        before = _normalize_states(tx["before"], expected)
        after = _normalize_states(tx["after"], expected)
        final = tx["receipt"]
        if not migration.same_lineage(receipt, owner):
            raise ValueError("installation lineage changed")
        if receipt["project"].get("name") != inst._stub_key(project):
            raise ValueError("project name changed")
        if identity is not None and not inst._same_identity(receipt["project"], identity, ("name", "path", "remote")):
            raise ValueError("registered project identity changed")
        if final is not None:
            _validate_final_shape(final, expected)
            if (
                final["schema"] != inst._BINSTUB_RECEIPT_SCHEMA
                or final["version"] != inst._BINSTUB_RECEIPT_VERSION
                or final["project"].get("name") != inst._stub_key(project)
                or (identity is not None and final["project"] != identity)
                or Path(final["runtime"]["root"]).resolve() != inst.install_dir().resolve()
            ):
                raise ValueError("invalid intended receipt")
            if not inst._same_identity(final["owner"], owner, inst._OWNER_IDENTITY_FIELDS):
                raise ValueError("intended owner changed; refuse stale publication")
            content = dict(inst._project_binstub_specs(project))
            for p in paths:
                data = _decode(after[inst._stub_key(p.name)]["bytes"])
                if data is None or not templates.matches(data, content[p]) or inst._stub_hash(final, p) != _digest(data):
                    raise ValueError("intended generated bytes/hash changed")
        elif any(entry["bytes"] is not None for entry in after.values()):
            raise ValueError("retirement contains replacement bytes")
    except (KeyError, TypeError, ValueError) as exc:
        raise inst.BinstubOwnershipError(f"invalid interrupted command transaction for {project}: {exc}") from exc
    if on_admitted is not None:
        on_admitted()
    _check_states(paths, before, after)
    for p in paths:
        key = inst._stub_key(p.name)
        entry = after[key]
        _replace(p, _decode(entry["bytes"]), entry["mode"],
                 expected=(_decode(before[key]["bytes"]), _decode(after[key]["bytes"])))
    _check_states(paths, after, after)
    if final is None:
        inst._receipt_path(project).unlink()
    else:
        inst._write_receipt(project, final)
    return True


def publish(
    project: str, owner: dict, identity: dict, specs: list[tuple[Path, str]],
    *, admitted: CommandAdmission | None = None, retire: bool = False, transfer: bool = False,
) -> int:
    """Journal prior bytes, atomically replace siblings, then publish the receipt."""
    from . import installer as inst

    paths = [p for p, _ in specs]
    if not isinstance(admitted, CommandAdmission) or set(admitted.stubs) != {
        inst._stub_key(p.name) for p in paths
    }:
        raise inst.BinstubOwnershipError(f"missing or invalid command admission evidence for {project}")
    receipt_path = inst._receipt_path(project)
    previous_bytes = receipt_path.read_bytes() if receipt_path.exists() else None
    try:
        previous = inst._read_receipt(project)
    except inst.BinstubOwnershipError:
        if not transfer:
            raise
        previous = None
    before = _capture(paths)
    if any(_decode(before[name]["bytes"]) != data for name, data in admitted.stubs.items()):
        raise inst.BinstubContentError(f"command admission changed before publication for {project}; preserving current bytes")
    after = {}
    for path, content in specs:
        key = inst._stub_key(path.name)
        old = _decode(before[key]["bytes"])
        data = None if retire else content.encode("utf-8")
        if not retire and old is not None and old.replace(b"\r\n", b"\n").replace(b"\r", b"\n") == data.replace(b"\r\n", b"\n"):
            data = old
        after[key] = {
            "bytes": base64.b64encode(data).decode("ascii") if data is not None else None,
            "mode": before[key]["mode"] if os.name == "nt" or inst.platform.system() == "Windows" else 0o755,
        }
    final = None if retire else {
        "schema": inst._BINSTUB_RECEIPT_SCHEMA, "version": inst._BINSTUB_RECEIPT_VERSION,
        "owner": owner, "project": identity,
        "runtime": {"root": str(inst.install_dir()), "resolver": "payload-local"},
        "stubs": {key: _digest(_decode(entry["bytes"])) for key, entry in after.items()},
    }
    changed = sum(before[key]["bytes"] != after[key]["bytes"] for key in before)
    if not changed and final == previous and all(before[key]["mode"] == after[key]["mode"] for key in before):
        return 0
    journal = dict((None if transfer else previous) or final or {
        "schema": inst._BINSTUB_RECEIPT_SCHEMA, "version": inst._BINSTUB_RECEIPT_VERSION,
        "owner": owner, "project": identity, "stubs": {},
        "runtime": {"root": str(inst.install_dir()), "resolver": "payload-local"},
    })
    journal["transaction"] = {"version": 1, "before": before, "after": after, "receipt": final}
    # The durable receipt is the recovery record; no separate command store.
    inst._write_receipt(project, journal)
    try:
        _check_states(paths, before, before)
        for p in paths:
            key = inst._stub_key(p.name)
            entry = after[key]
            _replace(p, _decode(entry["bytes"]), entry["mode"],
                     expected=(_decode(before[key]["bytes"]), _decode(after[key]["bytes"])))
        _check_states(paths, after, after)
        if retire:
            inst._receipt_path(project).unlink()
        else:
            inst._write_receipt(project, final)
    except Exception:
        _check_states(paths, before, after)
        for p in paths:
            key = inst._stub_key(p.name)
            entry = before[key]
            _replace(p, _decode(entry["bytes"]), entry["mode"],
                     expected=(_decode(before[key]["bytes"]), _decode(after[key]["bytes"])))
        _check_states(paths, before, before)
        if previous_bytes is None:
            inst._receipt_path(project).unlink(missing_ok=True)
        else:
            _replace(receipt_path, previous_bytes, 0o600)
        raise
    return changed
