"""Plan native evidence assets for explicitly declared non-Invocation consumers."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

DECLARATION = "native-command-errors.json"
ASSETS = frozenset({
    "command-errors.sh", "command-errors.ps1", "command-causes.tsv",
    "private-paths.sh", "private-paths.ps1",
})
SCHEMA = "copilot-extensions.native-command-errors"
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


@dataclass(frozen=True)
class NativeAssetPlan:
    """A complete immutable bundle followed by its mutable selection descriptor."""

    outputs: dict[Path, bytes]
    index: Path


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Native command evidence metadata repeats {key!r}")
        result[key] = value
    return result


def _object(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        data = stream.read(16_385)
    if len(data) > 16_384:
        raise ValueError(f"Native command evidence metadata is too large: {path}")
    value: object = json.loads(data, object_pairs_hook=_unique_pairs)
    if not isinstance(value, dict):
        raise TypeError(f"Native command evidence metadata must be an object: {path}")
    return cast(dict[str, object], value)


def _relative(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._/-]+", value):
        raise ValueError("Native command evidence outputDir must be a relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute() or value != path.as_posix() or not path.parts
        or any(part in {".", ".."} or part.endswith(".") for part in path.parts)
        or any(part.split(".")[0].upper() in _RESERVED for part in path.parts)
    ):
        raise ValueError("Native command evidence outputDir is not a normalized owned path")
    return path


def _assets(root: Path) -> dict[str, bytes]:
    spec = importlib.util.spec_from_file_location(
        "_native_consumer_evidence", Path(__file__).with_name("command_evidence.py"),
    )
    assert spec and spec.loader
    provider = importlib.util.module_from_spec(spec)
    previous_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(provider)
    finally:
        sys.dont_write_bytecode = previous_bytecode
    for relative in provider.NATIVE_INPUTS.values():
        path = root / relative
        if not path.is_file() or path.stat().st_size > 262_144:
            raise ValueError(f"Native evidence input is missing, nonregular or oversized: {path}")
    supplied: object = provider.native_files(root)
    if not isinstance(supplied, dict) or set(supplied) != ASSETS:
        raise ValueError("Native evidence provider returned an incomplete resource closure")
    result: dict[str, bytes] = {}
    for name in sorted(ASSETS):
        content = supplied[name]
        if not isinstance(content, bytes):
            raise TypeError("Native evidence provider returned a non-byte resource")
        result[name] = content
    return result


def _extend_causes(assets: dict[str, bytes], value: object) -> None:
    if not isinstance(value, list) or len(value) > 16:
        raise ValueError("Native command evidence causes must be a bounded static list")
    catalog = assets["command-causes.tsv"].decode("ascii")
    rows = catalog.splitlines()
    known = {row.split("\t", 1)[0] for row in rows}
    if len(known) != len(rows) or len(rows) + len(value) > 32:
        raise ValueError("Native command evidence cause catalog exceeds its contract")
    for item in value:
        if not isinstance(item, dict) or set(item) != {"code", "message"}:
            raise ValueError("Native command evidence cause declarations are invalid")
        code, message = item["code"], item["message"]
        if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z-]{0,63}", code):
            raise ValueError("Native command evidence cause identity is invalid")
        if (
            not isinstance(message, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ,.:;()/_-]{0,255}", message)
            or code in known
        ):
            raise ValueError("Native command evidence cause text is unsafe or redefined")
        known.add(code)
        rows.append(f"{code}\t{message}")
    assets["command-causes.tsv"] = ("\n".join(rows) + "\n").encode("ascii")


def plan_outputs(
    declaration: Path, *, canonical_declaration: Path, canonical_root: Path,
) -> NativeAssetPlan:
    """Plan a source-bound, digest-named five-file closure without writing it."""
    data = _object(declaration)
    required = {"version", "consumer", "outputDir"}
    if (
        not required.issubset(data) or set(data) - required - {"causes"}
        or type(data["version"]) is not int
    ):
        raise ValueError("Unsupported native command evidence declaration")
    if data["version"] != 1:
        raise ValueError("Unsupported native command evidence declaration version")
    consumer = data["consumer"]
    if not isinstance(consumer, str) or not consumer or len(consumer) > 128:
        raise ValueError("Native command evidence consumer identity is invalid")
    for plugin in (declaration.parent, canonical_declaration.parent):
        if _object(plugin / "plugin.json").get("name") != consumer:
            raise ValueError("Native command evidence declaration names a foreign consumer")
    output = declaration.parent / _relative(data["outputDir"])
    assets = _assets(canonical_root)
    if "causes" in data:
        _extend_causes(assets, data["causes"])
    digests = {name: hashlib.sha256(content).hexdigest() for name, content in assets.items()}
    bundle = hashlib.sha256(
        json.dumps(digests, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    outputs = {output / bundle / name: content for name, content in assets.items()}
    index = output / "index.json"
    outputs[index] = (json.dumps({
        "schema": SCHEMA, "version": 1, "consumer": consumer,
        "bundle": bundle, "files": digests,
    }, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return NativeAssetPlan(outputs, index)
