"""A self-contained, data-only encoding of one complete promotion source tree."""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections import deque

from promotion_unit_contract import MAX_PACKET_FILE_BYTES

SCHEMA = "copilot-extensions.complete-promotion-source"
MAX_SOURCE_BYTES = 512 << 20
MAX_SOURCE_FILES = 100_000
MAX_LINK_BYTES = 4096
MAX_LINK_HOPS = 40
IDENTITY_FIELDS = {
    "dev_sha", "candidate_sha", "main_before", "control_sha", "content_tree", "tree",
    "source_index_sha256", "qualification_digest",
}
_MODES = {"100644", "100755", "120000"}
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
             *(f"lpt{i}" for i in range(10))}
_STAMP = (1980, 1, 1, 0, 0, 0)


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def validate_identity(identity: dict) -> None:
    if not isinstance(identity, dict) or set(identity) != IDENTITY_FIELDS or any(
        not isinstance(value, str) or not re.fullmatch(
            r"[0-9a-f]{64}" if key.endswith(("sha256", "digest")) else r"[0-9a-f]{40}", value,
        ) for key, value in identity.items()
    ):
        raise ValueError("invalid complete-source generation identity")


def source_asset_name(kind: str, identity: dict, digest: str) -> str:
    validate_identity(identity)
    if kind not in ("complete-source", "source-manifest") or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid complete-source asset identity")
    extension = "zip" if kind == "complete-source" else "json"
    return (
        f"promotion-{kind}-{identity['dev_sha']}-{identity['candidate_sha']}"
        f"-sha256-{digest}.{extension}"
    )


def _path(name: str) -> list[str]:
    if not isinstance(name, str) or not name or len(name.encode("utf-8")) > 65535:
        raise ValueError("invalid complete-source path")
    parts = name.split("/")
    if any(
        part in ("", ".", "..") or part.casefold() == ".git"
        or part.endswith((" ", ".")) or re.search(r'[<>:"\\|?*\x00-\x1f\x7f]', part)
        or part.split(".", 1)[0].casefold() in _RESERVED
        for part in parts
    ):
        raise ValueError("unsafe complete-source path")
    return parts


def _object(kind: str, raw: bytes) -> bytes:
    return hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).digest()


def _validate_links(links: dict[str, str]) -> None:
    targets = {name: tuple(target.split("/")) for name, target in links.items()}
    canonical = {name.casefold(): name for name in links}
    for name in links:
        pending = deque([*name.split("/")[:-1], *targets[name]])
        resolved, hops = [], 1
        while pending:
            part = pending.popleft()
            if part in ("", "."):
                continue
            if part == "..":
                if not resolved:
                    raise ValueError("complete-source link escapes the source root")
                resolved.pop()
                continue
            resolved.append(part)
            path = "/".join(resolved)
            link = canonical.get(path.casefold())
            if link is not None:
                if link != path:
                    raise ValueError("case-equivalent complete-source link target")
                hops += 1
                if hops > MAX_LINK_HOPS:
                    raise ValueError("cyclic or excessive complete-source link traversal")
                resolved.pop()
                pending.extendleft(reversed(targets[link]))


def _inventory(files: dict[str, tuple[str, bytes]]) -> tuple[list[dict], str]:
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_SOURCE_FILES:
        raise ValueError("complete-source inventory is empty or oversized")
    root, aliases, inventory, links, total = {}, {}, [], {}, 0
    for name, entry in sorted(files.items()):
        parts = _path(name)
        if (
            not isinstance(entry, tuple) or len(entry) != 2 or entry[0] not in _MODES
            or not isinstance(entry[1], bytes) or len(entry[1]) > MAX_PACKET_FILE_BYTES
        ):
            raise ValueError("unsupported complete-source file type or size")
        mode, raw = entry
        total += len(raw)
        if total > MAX_SOURCE_BYTES:
            raise ValueError("complete-source tree exceeds its byte bound")
        node = root
        for number, part in enumerate(parts):
            prefix = "/".join(parts[:number + 1])
            if aliases.setdefault(prefix.casefold(), prefix) != prefix:
                raise ValueError("case-equivalent complete-source path")
            if number == len(parts) - 1:
                if part in node:
                    raise ValueError("conflicting complete-source file and directory")
                node[part] = (mode, _object("blob", raw))
            else:
                node = node.setdefault(part, {})
                if not isinstance(node, dict):
                    raise ValueError("complete-source file is an ancestor")
        if mode == "120000":
            if not 0 < len(raw) <= MAX_LINK_BYTES:
                raise ValueError("complete-source link exceeds its byte bound")
            try:
                target = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("invalid complete-source link") from exc
            if not target or target.startswith("/") or re.search(r'[:\\\x00-\x1f\x7f]', target):
                raise ValueError("unsafe complete-source link")
            links[name] = target
        inventory.append({
            "path": name, "mode": mode, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        })

    def tree(node: dict) -> bytes:
        records = []
        for name, value in node.items():
            directory = isinstance(value, dict)
            mode, digest = ("40000", tree(value)) if directory else value
            encoded = name.encode("utf-8")
            records.append((encoded + (b"/" if directory else b""), mode.encode() + b" " + encoded + b"\0" + digest))
        return _object("tree", b"".join(record for _key, record in sorted(records)))

    _validate_links(links)
    return inventory, tree(root).hex()


def source_archive(files: dict[str, tuple[str, bytes]], *, identity: dict) -> tuple[bytes, dict]:
    """Ignore export attributes: every admitted Git blob is retained without substitution."""
    validate_identity(identity)
    inventory, tree = _inventory(files)
    if tree != identity["tree"]:
        raise ValueError("complete-source inventory does not reconstruct the candidate tree")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, (mode, raw) in sorted(files.items()):
            member = zipfile.ZipInfo(name, date_time=_STAMP)
            member.create_system = 3
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = int(mode, 8) << 16
            archive.writestr(member, raw, compresslevel=9)
    raw = output.getvalue()
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("complete-source archive exceeds its byte bound")
    manifest = {
        "schema": SCHEMA, "version": 1, **identity,
        "archive_sha256": hashlib.sha256(raw).hexdigest(), "files": inventory,
    }
    if len(canonical_bytes(manifest)) > MAX_PACKET_FILE_BYTES:
        raise ValueError("complete-source manifest exceeds its byte bound")
    return raw, manifest


def verify_source_archive(raw: bytes, manifest: dict, *, identity: dict) -> dict[str, tuple[str, bytes]]:
    """Prove complete reconstruction without extracting files or following source links."""
    validate_identity(identity)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != IDENTITY_FIELDS | {"schema", "version", "archive_sha256", "files"}
        or manifest["schema"] != SCHEMA or type(manifest["version"]) is not int or manifest["version"] != 1
        or any(manifest[key] != value for key, value in identity.items())
        or not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE_BYTES
        or hashlib.sha256(raw).hexdigest() != manifest["archive_sha256"]
        or not isinstance(manifest["files"], list) or not 1 <= len(manifest["files"]) <= MAX_SOURCE_FILES
        or len(canonical_bytes(manifest)) > MAX_PACKET_FILE_BYTES
    ):
        raise ValueError("complete-source manifest differs from the admitted generation")
    files, total = {}, 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) != len(manifest["files"]) or archive.comment:
                raise ValueError("complete-source archive inventory differs")
            for member, expected in zip(members, manifest["files"], strict=True):
                if (
                    not isinstance(expected, dict) or set(expected) != {"path", "mode", "size", "sha256"}
                    or type(expected["size"]) is not int
                    or not 0 <= member.file_size <= MAX_PACKET_FILE_BYTES
                    or member.filename != expected["path"] or member.filename in files
                    or member.create_system != 3 or member.external_attr >> 16 != int(expected["mode"], 8)
                    or member.date_time != _STAMP or member.compress_type != zipfile.ZIP_DEFLATED
                    or member.flag_bits & 1 or member.extra or member.comment
                ):
                    raise ValueError("invalid complete-source archive member")
                total += member.file_size
                if total > MAX_SOURCE_BYTES:
                    raise ValueError("complete-source archive expands beyond its byte bound")
                content = archive.read(member)
                files[member.filename] = (expected["mode"], content)
    except (zipfile.BadZipFile, KeyError, TypeError, OverflowError) as exc:
        raise ValueError("invalid complete-source archive") from exc
    inventory, tree = _inventory(files)
    if inventory != manifest["files"] or tree != identity["tree"]:
        raise ValueError("complete-source bytes do not reconstruct the admitted full tree")
    return files
