"""Bounded useful diagnostics, redacted before persistence or truncation."""

from __future__ import annotations

import json
from pathlib import Path
import re
import stat

SENSITIVE = frozenset({
    "secret", "password", "client_secret", "hmac_secret", "encryption_key", "jwt_secret",
    "key", "d", "access_token", "refresh_token", "id_token", "authorization",
})
FIXTURE_JSON = ("provider/configuration.yml", "provider/users.yml", "client/inputs.json")
FIXTURE_KEYS = ("provider/key.pem", "client/key.pem")
MAX_FILE = 131072
MAX_RECORDS = 20
MAX_TEXT = 3000
PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?(?:-----END (?:[A-Z]+ )?PRIVATE KEY-----|$)",
    re.DOTALL,
)
COMPACT_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])",
)
PASSWORD_HASH = re.compile(r"\$(?:argon2(?:id|i|d)|pbkdf2[-a-z0-9]*)\$[^\s\"'\\]+")
AUTH_VALUE = re.compile(
    r"(?i)\b(?:" + "Bear" + "er|DP" + "oP|Ba" + "sic" + r")\s+[A-Za-z0-9+/_.=-]+",
)
LOGFMT_FIELD = re.compile(r'([A-Za-z_][A-Za-z0-9_.-]*)=("(?:\\.|[^"\\])*"|[^\s"]+)')
ASSIGNED_SECRET = re.compile(
    r"""(?ix)\b(access_token|refresh_token|id_token|client_secret|password|"""
    r"""hmac_secret|encryption_key|jwt_secret)\b(["']?\s*[:=]\s*)"""
    r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;]+)""",
)


class CaptureError(ValueError):
    """Fixture redaction inputs unavailable; no raw diagnostic may be retained."""


def text_record(line: bytes) -> dict | None:
    """Pinned Authelia validates configuration before selecting JSON log format."""
    try:
        text = line.decode("utf-8")
    except UnicodeError:
        return None
    values: dict[str, str] = {}
    end = 0
    for field in LOGFMT_FIELD.finditer(text):
        if text[end:field.start()].strip():
            return None
        name, value = field.groups()
        if name in values:
            return None
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                return None
        values[name] = value
        end = field.end()
    return values if values and not text[end:].strip() else None


def sensitive_values(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and (key.lower() in SENSITIVE or key.lower().endswith("_secret")):
                if isinstance(item, str) and item:
                    found.add(item)
            found.update(sensitive_values(item))
    elif isinstance(value, list):
        for item in value:
            found.update(sensitive_values(item))
    return found


def read_fixture(path: Path) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
        raise CaptureError("diagnostic-redaction-input-refused")
    return path.read_bytes()


def fixture_secrets(fixtures: Path) -> tuple[str, ...]:
    values: set[str] = set()
    try:
        for name in FIXTURE_JSON:
            raw = read_fixture(fixtures / name)
            if raw is not None:
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise CaptureError("diagnostic-redaction-input-refused")
                values.update(sensitive_values(value))
        for name in FIXTURE_KEYS:
            raw = read_fixture(fixtures / name)
            if raw is not None:
                text = raw.decode("ascii").strip()
                if not text:
                    raise CaptureError("diagnostic-redaction-input-refused")
                values.add(text)
                values.update(line for line in text.splitlines()
                              if len(line) >= 16 and not line.startswith("-----"))
    except (OSError, ValueError, UnicodeError):
        raise CaptureError("diagnostic-redaction-input-unavailable") from None
    # Redact JSON-escaped and multiline renderings as well as literal values.
    variants = values | {json.dumps(value)[1:-1] for value in values}
    return tuple(sorted(variants, key=len, reverse=True))


def redact(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        text = text.replace(secret, "[redacted]")
    text = PRIVATE_KEY.sub("[redacted-private-key]", text)
    text = COMPACT_TOKEN.sub("[redacted-token]", text)
    text = PASSWORD_HASH.sub("[redacted-password-hash]", text)
    text = AUTH_VALUE.sub("[redacted-authorization]", text)
    text = ASSIGNED_SECRET.sub(r"\1\2[redacted]", text)
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    return text[:MAX_TEXT] + (" [truncated]" if len(text) > MAX_TEXT else "")


def diagnostic_records(data: bytes, fixtures: Path) -> dict:
    """Keep error, nested cause and stack, but never arbitrary JSON fields."""
    secrets = fixture_secrets(fixtures)
    records: list[dict[str, str]] = []
    omitted = 0
    for line in data.splitlines():
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError):
            if line.startswith(b"Error response from daemon:"):
                value = {"level": "error", "msg": line.decode("utf-8", errors="replace")}
            else:
                value = text_record(line)
        if not isinstance(value, dict) or value.get("level") not in ("error", "fatal", "warning"):
            continue
        if len(records) == MAX_RECORDS:
            omitted += 1
            continue
        record = {"level": value["level"]}
        for field in ("msg", "message", "error", "stack", "provider", "providers"):
            item = value.get(field)
            if isinstance(item, str):
                record[field] = redact(item, secrets)
            elif field in ("error", "stack", "providers") and isinstance(item, (dict, list)):
                record[field] = redact(json.dumps(item, sort_keys=True), secrets)
        records.append(record)
    return {"diagnostic_capture": "redacted", "diagnostics": records,
            "omitted_diagnostic_records": omitted}
