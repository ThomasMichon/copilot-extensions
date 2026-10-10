"""Pure local-first rendering, provenance comparison and body coverage."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Callable, Mapping, Protocol

MARKER_PREFIX = "<!-- copilot-extension-instruction-projection "
RECEIPT_PREFIX = "<!-- copilot-guidance-body-end:v1 "
IDENTITY_FIELDS = ("plugin", "sourceId", "pluginVersion", "templateSha256", "applyTo")
OWNER_FIELDS = ("plugin", "sourceId", "destination", "customizationKind", "applyTo")
VERSION = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)(?:-dev([0-9]+))?$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
MAX_FALLBACK_BYTES = 6144
BASE_KEYS = {
    "sourceId", "plugin", "pluginVersion", "template", "templateSha256",
    "templateBytes", "destination", "customizationKind", "applyTo", "renderedBytes",
}
SELECTOR_KEYS = {
    "schema", "version", "sourceId", "plugin", "pluginVersion", "templateSha256",
    "destination", "applyTo", "renderedBytes", "deliveryKind",
}


class Projection(Protocol):
    source_id: str
    plugin: str
    plugin_version: str
    template: str
    template_sha256: str
    template_bytes: int
    template_content: bytes
    destination: str
    customization_kind: str
    apply_to: str
    delivery_mode: str
    delivery_declared: bool


def render_projection(
    spec: Projection, include_prefer_local: bool
) -> tuple[bytes, dict, bytes | None]:
    marker = {
        "schema": "copilot-extensions.instruction-projection", "version": 1,
        "sourceId": spec.source_id, "plugin": spec.plugin,
        "pluginVersion": spec.plugin_version, "template": spec.template,
        "templateSha256": spec.template_sha256, "templateBytes": spec.template_bytes,
        "destination": spec.destination, "customizationKind": spec.customization_kind,
        "applyTo": spec.apply_to, "renderedBytes": 0,
    }
    if spec.delivery_mode == "selector":
        body, body_marker = render_body(spec.template_content, marker)
        if include_prefer_local:
            selector, selector_marker = render_selector(marker)
            return selector, selector_marker, body
        return body, body_marker, None
    lines = spec.template_content.decode("utf-8").splitlines(keepends=True)
    closing = lines.index("---\n", 1)
    header = "".join(lines[: closing + 1])
    body = "".join(lines[closing + 1 :])
    preamble = ""
    if include_prefer_local:
        local = PurePosixPath(spec.destination).name.removesuffix(".instructions.md")
        preamble = (
            f"\n> If `{local}.local.instructions.md` exists here, compare\n"
            "> `pluginVersion` and prefer whichever is newer. On a tie,\n"
            "> compare `templateSha256`: matching means prefer local;\n"
            "> differing means prefer this checked-in file.\n"
        )
    for _ in range(4):
        content = (
            header + MARKER_PREFIX + _json(marker) + " -->\n" + preamble + body
        ).encode()
        if marker["renderedBytes"] == len(content):
            if spec.delivery_declared:
                return (*render_body(spec.template_content, marker, preamble), None)
            return content, marker, None
        marker["renderedBytes"] = len(content)
    raise ValueError("rendered byte count did not stabilize")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate provenance key")
        value[key] = item
    return value


def parse_marker(raw: bytes) -> dict[str, object]:
    text = raw.decode("utf-8", errors="strict")
    matches = [line for line in text.splitlines() if line.startswith(MARKER_PREFIX)]
    if len(matches) != 1 or not matches[0].endswith(" -->"):
        raise ValueError("projection must contain exactly one provenance marker")
    marker = json.loads(
        matches[0][len(MARKER_PREFIX):-len(" -->")], object_pairs_hook=_unique_pairs
    )
    keys = BASE_KEYS | {"schema", "version"}
    if (
        not isinstance(marker, dict)
        or marker.get("schema") != "copilot-extensions.instruction-projection"
        or type(marker.get("version")) is not int
    ):
        raise ValueError("projection provenance marker schema is unsupported")
    if marker.get("version") == 2:
        if set(marker) != SELECTOR_KEYS or marker.get("deliveryKind") != "selector":
            raise ValueError("invalid compact selector provenance")
        return marker
    if marker.get("version") != 1 or set(marker) not in (
        keys, keys | {"deliveryKind"}, keys | {"deliveryKind", "bodySha256"}
    ):
        raise ValueError("projection provenance marker schema is unsupported")
    if "deliveryKind" in marker:
        kind = marker["deliveryKind"]
        if kind not in {"body", "selector"} or (
            ("bodySha256" in marker) != (kind == "body")
        ):
            raise ValueError("invalid delivery envelope")
        if kind == "body" and (
            not isinstance(marker["bodySha256"], str)
            or not DIGEST.fullmatch(marker["bodySha256"])
        ):
            raise ValueError("invalid body hash")
    return marker


def fallback_destination(destination: str) -> str:
    path = PurePosixPath(destination)
    if (
        path.parts[:2] != (".github", "instructions")
        or len(path.parts) < 4
        or not path.name.endswith(".instructions.md")
    ):
        raise ValueError("invalid selector destination")
    return str(
        PurePosixPath(".github/copilot/context-fallbacks")
        / PurePosixPath(*path.parts[2:]).with_name(
            path.name.removesuffix(".instructions.md") + ".md"
        )
    )


def _json(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def identity(marker: Mapping[str, object]) -> dict[str, object]:
    return {field: marker[field] for field in IDENTITY_FIELDS}


def receipt_binding(marker: Mapping[str, object]) -> str:
    value = identity(marker) | {"bodySha256": marker["bodySha256"]}
    return hashlib.sha256(_json(value).encode()).hexdigest()


def version_key(value: object) -> tuple[int, int, int, int, int]:
    match = VERSION.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError("invalid plugin version")
    major, minor, patch, dev = match.groups()
    return int(major), int(minor), int(patch), int(dev is None), int(dev or 0)


def validate_candidate(marker: Mapping[str, object], owner: Mapping[str, object]) -> None:
    if any(marker.get(field) != owner.get(field) for field in OWNER_FIELDS):
        raise ValueError("candidate source, marketplace, destination or scope mismatch")
    version_key(marker.get("pluginVersion"))
    if not isinstance(marker.get("templateSha256"), str) or not DIGEST.fullmatch(
        marker["templateSha256"]
    ):
        raise ValueError("invalid candidate template hash")


def local_wins(local: Mapping[str, object], reviewed: Mapping[str, object]) -> bool:
    validate_candidate(local, reviewed)
    local_version = version_key(local["pluginVersion"])
    reviewed_version = version_key(reviewed["pluginVersion"])
    return local_version > reviewed_version or (
        local_version == reviewed_version
        and local["templateSha256"] == reviewed["templateSha256"]
    )


def _render(header: str, body: str, marker: dict[str, object], suffix: str = "") -> bytes:
    for _ in range(5):
        content = (
            header + MARKER_PREFIX + _json(marker) + " -->\n\n" + body + suffix
        ).encode("utf-8")
        if marker["renderedBytes"] == len(content):
            return content
        marker["renderedBytes"] = len(content)
    raise ValueError("delivery byte count did not stabilize")


def render_body(
    template: bytes, provenance: Mapping[str, object], preamble: str = ""
) -> tuple[bytes, dict]:
    lines = template.decode("utf-8").splitlines(keepends=True)
    closing = lines.index("---\n", 1)
    header = "".join(lines[: closing + 1])
    body = preamble + "".join(lines[closing + 1 :])
    if MARKER_PREFIX in body or RECEIPT_PREFIX in body:
        raise ValueError("template contains reserved delivery boundaries")
    marker = dict(provenance)
    marker["deliveryKind"] = "body"
    marker["bodySha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    receipt = {"bindingSha256": receipt_binding(marker)}
    content = _render(
        header, body, marker, "\n" + RECEIPT_PREFIX + _json(receipt) + " -->\n"
    )
    return content, marker


def render_selector(provenance: Mapping[str, object]) -> tuple[bytes, dict]:
    marker = {key: value for key, value in provenance.items() if key in SELECTOR_KEYS}
    marker["version"] = 2
    marker["deliveryKind"] = "selector"
    destination = str(marker["destination"])
    local = destination.removesuffix(".instructions.md") + ".local.instructions.md"
    fallback = fallback_destination(destination)
    body = (
        "Acquire authoritative content before dependent/consequential action; "
        "resolution failure is a visible blocker, never authorization.\n\n"
        f"Local: `{local}`. Reviewed fallback: `{fallback}`.\n"
        "Apply the inline cache-recovery/coverage protocol. Use `resolve-source --json`, "
        "or exact filename-labeled owner/scope/version/hash checks: newer wins; equal "
        "version/hash favors local; equal version/unequal hash favors fallback. Read "
        "fully unless the entire selected body with matching provenance/receipt is "
        "directly visible now. This selector asserts no body delivery.\n"
    )
    header = "---\napplyTo: " + json.dumps(marker["applyTo"]) + "\n---\n"
    return _render(header, body, marker), marker


def complete_body(raw: bytes, marker: Mapping[str, object]) -> bool:
    """Verify a complete byte envelope, not that a host admitted it to a model."""
    try:
        text = raw.decode("utf-8")
        if marker.get("deliveryKind") != "body":
            return False
        opening = MARKER_PREFIX + _json(marker) + " -->\n\n"
        if text.count(opening) != 1 or text.count(RECEIPT_PREFIX) != 1:
            return False
        before, remainder = text.split(opening, 1)
        if not before.startswith("---\n") or before.count("---\n") != 2:
            return False
        body, receipt_text = remainder.rsplit("\n" + RECEIPT_PREFIX, 1)
        if not receipt_text.endswith(" -->\n"):
            return False
        receipt_json = receipt_text.removesuffix(" -->\n")
        receipt = json.loads(receipt_json, object_pairs_hook=_unique_pairs)
        expected = {"bindingSha256": receipt_binding(marker)}
        return (
            receipt == expected
            and receipt_json == _json(expected)
            and hashlib.sha256(body.encode()).hexdigest() == marker["bodySha256"]
            and marker["renderedBytes"] == len(raw)
        )
    except (ValueError, KeyError, UnicodeError, TypeError):
        return False


def validate_local_template(raw: bytes, marker: Mapping[str, object]) -> None:
    text = raw.decode("utf-8")
    marker_line = MARKER_PREFIX + _json(marker) + " -->\n"
    if text.count(marker_line) != 1:
        raise ValueError("noncanonical local provenance")
    header, body = text.split(marker_line, 1)
    lines = header.splitlines()
    if (
        len(lines) != 3 or lines[0] != "---" or lines[2] != "---"
        or not lines[1].startswith("applyTo:")
        or lines[1].split(":", 1)[1].strip().strip("\"'") != marker["applyTo"]
    ):
        raise ValueError("local frontmatter scope mismatch")
    if marker.get("deliveryKind") == "body":
        if not complete_body(raw, marker) or not body.startswith("\n"):
            raise ValueError("local body receipt/integrity mismatch")
        body = body[1:].rsplit("\n" + RECEIPT_PREFIX, 1)[0]
    template = (header + body).encode()
    if (
        len(template) != marker["templateBytes"]
        or hashlib.sha256(template).hexdigest() != marker["templateSha256"]
    ):
        raise ValueError("local content does not match its canonical template hash")


def validate_lock_delivery(entry: Mapping[str, object]) -> None:
    if entry.get("deliveryMode") != "selector":
        raise ValueError("unsupported lock delivery mode")
    fallback = entry.get("fallback")
    if (
        not isinstance(fallback, dict)
        or set(fallback) != {"destination", "renderedSha256", "renderedBytes"}
        or fallback["destination"] != fallback_destination(str(entry["destination"]))
        or not isinstance(fallback["renderedSha256"], str)
        or not DIGEST.fullmatch(fallback["renderedSha256"])
        or not isinstance(fallback["renderedBytes"], int)
        or isinstance(fallback["renderedBytes"], bool)
        or not 1 <= fallback["renderedBytes"] <= MAX_FALLBACK_BYTES
    ):
        raise ValueError("invalid owned fallback path, digest or size")


def validate_lock_entry(
    entry: object, validate_path: Callable[..., PurePosixPath], template_budget: int
) -> dict[str, object]:
    keys = BASE_KEYS | {"renderedSha256"}
    if not isinstance(entry, dict) or set(entry) not in (
        keys, keys | {"deliveryMode", "fallback"}
    ):
        raise ValueError("lock entry has unknown or missing keys")
    if "deliveryMode" in entry:
        validate_lock_delivery(entry)
    for key in keys - {"templateBytes", "renderedBytes"}:
        if not isinstance(entry[key], str) or not entry[key]:
            raise ValueError(f"lock entry {key} is invalid")
    identifier = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
    plugin, separator, marketplace = entry["plugin"].partition("@")
    if (
        not separator or not identifier.fullmatch(plugin)
        or not identifier.fullmatch(marketplace)
        or not identifier.fullmatch(entry["sourceId"])
    ):
        raise ValueError("lock plugin/source identity is invalid")
    template = validate_path(entry["template"], field_name="lock template")
    destination = validate_path(entry["destination"], field_name="lock destination")
    if (
        destination.parts[:3] != (".github", "instructions", plugin)
        or template.suffixes[-2:] != [".instructions", ".md"]
        or destination.suffixes[-2:] != [".instructions", ".md"]
        or destination.name.endswith(".local.instructions.md")
    ):
        raise ValueError("lock path is outside its owned namespace")
    if entry["customizationKind"] != "instructions":
        raise ValueError("lock customizationKind is unsupported")
    version_key(entry["pluginVersion"])
    if not 1 <= len(entry["applyTo"]) <= 256 or any(
        ord(character) < 32 for character in entry["applyTo"]
    ):
        raise ValueError("lock applyTo is invalid")
    if not DIGEST.fullmatch(entry["templateSha256"]) or not DIGEST.fullmatch(
        entry["renderedSha256"]
    ):
        raise ValueError("lock digest is invalid")
    for key in ("templateBytes", "renderedBytes"):
        if (
            not isinstance(entry[key], int) or isinstance(entry[key], bool)
            or entry[key] < 1
        ):
            raise ValueError(f"lock {key} is invalid")
    if entry["templateBytes"] > template_budget:
        raise ValueError("lock templateBytes exceeds the template budget")
    return dict(entry)
