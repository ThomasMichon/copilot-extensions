"""Dependency-free binding configuration transactions and supported YAML subset."""

from __future__ import annotations

import json
import errno
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def strip_yaml_comment(value: str) -> str:
    quote = ""
    for index, char in enumerate(value):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.rstrip()


def yaml_scalar(value: str) -> str:
    value = strip_yaml_comment(value.strip())
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def render_yaml_scalar(value: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", value) and value.lower() not in {
        "yes", "no", "true", "false", "on", "off", "null",
    }:
        return value
    return json.dumps(value)


def set_top_yaml_key(text: str, key: str, value: str) -> str:
    """Replace a top-level scalar without round-tripping unrelated YAML."""
    line = f"{key}: {render_yaml_scalar(value)}"
    pat = re.compile(rf"^{re.escape(key)}\s*:.*$", re.MULTILINE)
    if pat.search(text):
        return pat.sub(lambda _match: line, text, count=1)
    lines = text.splitlines()
    insert_at = 0
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s == "" or s.startswith("#"):
            insert_at = i + 1
            continue
        break
    lines.insert(insert_at, line)
    out = "\n".join(lines)
    if text.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out


def check_document_shape(existing: str) -> None:
    significant = [
        line for line in existing.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if significant and (
        not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", significant[0])
        or any(
            not line[0].isspace() and not re.match(r"^-(?:\s|$)", line)
            and not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", line)
            for line in significant
        )
    ):
        raise RuntimeError("unsupported binding configuration shape; expected a block mapping")
    for index, line in enumerate(significant[:-1]):
        following = significant[index + 1]
        if re.match(r"^knowledge_repo\s*:", line) and (
            following[0].isspace() or re.match(r"^-(?:\s|$)", following)
        ):
            raise RuntimeError("unsupported multiline knowledge binding; configuration was preserved")


def check_existing_binding(existing: str, knowledge: str) -> bool:
    check_document_shape(existing)
    for line in existing.splitlines():
        if match := re.match(r"^knowledge_repo\s*:\s*(.*?)\s*$", line):
            raw = strip_yaml_comment(match.group(1)).strip()
            if (
                raw not in {"", "~", "null", "Null", "NULL"}
                and not raw.startswith(("'", '"'))
                and re.fullmatch(
                    r"(?:true|True|TRUE|false|False|FALSE|yes|Yes|YES|no|No|NO|"
                    r"on|On|ON|off|Off|OFF|[0-9][0-9_]*|0[bB][01_]+|"
                    r"0[xX][0-9a-fA-F_]+|[0-9][0-9_]*\.[0-9_]*(?:[eE][-+][0-9]+)?|"
                    r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2})",
                    raw,
                )
            ):
                raise RuntimeError("existing knowledge binding must be a YAML string")
    bindings = [
        (
            ""
            if strip_yaml_comment(match.group(1)).strip() in {"", "~", "null", "Null", "NULL"}
            else yaml_scalar(match.group(1))
        )
        for line in existing.splitlines()
        if (match := re.match(r"^knowledge_repo\s*:\s*(.*?)\s*$", line))
    ]
    if len(bindings) > 1:
        raise RuntimeError("existing harness configuration has duplicate knowledge bindings")
    if bindings and bindings[0] and bindings[0] != knowledge:
        raise RuntimeError(
            "existing harness knowledge binding conflicts with the declared pair; "
            "explicit re-pointing through the normal binding owner is required"
        )
    return bindings == [knowledge]


@contextmanager
def binding_lock(base: Path):
    """All explicit and preserving writers share one stable OS-locked file."""
    base.mkdir(parents=True, exist_ok=True)
    with (base / ".knowledge-binding.lock").open("a+b") as stream:
        if not stream.seek(0, os.SEEK_END):
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            acquire = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        deadline = time.monotonic() + 10
        while True:
            try:
                acquire()
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError("timed out waiting for the knowledge binding writer") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            release()


def write_binding(config: Path, harness: str, knowledge: str, *, preserve: bool) -> None:
    with binding_lock(config.parent):
        existing = config.read_text(encoding="utf-8") if config.exists() else ""
        check_document_shape(existing)
        matching = check_existing_binding(existing, knowledge) if preserve else False
        if matching:
            return
        if not existing.strip():
            existing = (
                f"# Machine-local config for {harness} (harness-knowledge managed pointer).\n"
                f"repo_name: {render_yaml_scalar(harness)}\n"
            )
        updated = set_top_yaml_key(existing, "knowledge_repo", knowledge)
        if config.is_file() and updated == existing:
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", dir=config.parent, delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(updated)
            if config.exists():
                temporary.chmod(config.stat().st_mode & 0o777)
            os.replace(temporary, config)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def read_issue_route(config: Path) -> dict[str, str] | None:
    """Read the supported ``issues`` YAML subset without requiring PyYAML."""
    if not config.is_file():
        return None
    lines = config.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^issues\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        inline = strip_yaml_comment(match.group(1))
        if inline:
            body = inline.strip()
            if body.startswith("{") and body.endswith("}"):
                body = body[1:-1]
            if not body.strip():
                return {}
            route = {}
            for item in body.split(","):
                if ":" not in item:
                    continue
                key, value = item.split(":", 1)
                key = key.strip()
                if key in {"provider", "repo"}:
                    route[key] = yaml_scalar(value)
            if not route:
                raise ValueError("issues block has an unrecognized inline shape")
            return route

        children = []
        for child in lines[index + 1 :]:
            if child.strip() == "" or child.lstrip().startswith("#"):
                continue
            indent = len(child) - len(child.lstrip())
            if indent == 0:
                break
            children.append((indent, child))
        direct_indent = min((indent for indent, _ in children), default=0)
        route = {}
        for indent, child in children:
            if indent != direct_indent:
                continue
            child_match = re.match(r"^\s+(provider|repo)\s*:\s*(.*?)\s*$", child)
            if child_match:
                route[child_match.group(1)] = yaml_scalar(child_match.group(2))
        if not children:
            return {}
        if not route:
            raise ValueError("issues block has an unrecognized nested shape")
        return route
    return None
