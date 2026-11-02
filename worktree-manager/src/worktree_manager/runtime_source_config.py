"""Independent, default-off source policy for installed service runtimes."""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import source_config

_CHANNELS = {"marketplace", "main", "stable", "exact", "dev"}
_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


@dataclass(frozen=True)
class RuntimeSource:
    channel: str = "marketplace"
    repo: str = source_config.DEFAULT_REPO
    ref: str = ""
    allow_dev: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.channel, str) or self.channel not in _CHANNELS:
            raise ValueError("runtime source channel must be marketplace, main, stable, exact or dev")
        if not isinstance(self.repo, str) or not self.repo or any(
            ord(character) < 32 for character in self.repo
        ):
            raise ValueError("runtime source repo must be a nonempty repository locator")
        url = urlsplit(self.repo) if "://" in self.repo else None
        if (
            re.match(r"^[A-Za-z][A-Za-z0-9+.-]*::", self.repo)
            or (url is not None and (
                url.scheme not in {"https", "http", "ssh", "git", "file"}
                or url.password is not None or url.query or url.fragment
                or (url.scheme in {"http", "https"} and url.username is not None)
            ))
        ):
            raise ValueError("runtime source requires a credential-free Git repository locator")
        if (
            (os.name == "nt" and Path(self.repo).drive and not Path(self.repo).is_absolute())
            or (url is None and not Path(self.repo).is_absolute() and not re.match(
                r"^[^/\\:]+:", self.repo,
            ))
        ):
            raise ValueError("local runtime source repositories must use an absolute path")
        if not isinstance(self.ref, str) or type(self.allow_dev) is not bool:
            raise ValueError("runtime source ref must be a string and allow_dev must be a boolean")
        if self.channel == "dev" and not self.allow_dev:
            raise ValueError("merged-dev runtime source requires explicit --allow-dev consent")
        if self.allow_dev and self.channel != "dev":
            raise ValueError("allow_dev applies only to the dev runtime source")
        if self.channel == "exact":
            if not _OBJECT_ID.fullmatch(self.ref):
                raise ValueError("exact runtime source requires a full lowercase commit object ID")
        elif self.channel == "stable":
            if (
                not _TAG.fullmatch(self.ref) or ".." in self.ref or "//" in self.ref
                or self.ref.endswith(("/", ".", ".lock"))
                or any(part.startswith(".") or part.endswith(".lock") for part in self.ref.split("/"))
            ):
                raise ValueError("stable runtime source requires an explicit release tag")
        elif self.ref:
            raise ValueError("only stable and exact runtime sources accept a ref")

    @property
    def branch(self) -> str:
        return "dev" if self.channel == "dev" else "main"

    def document(self) -> dict[str, str | bool]:
        return {
            "channel": self.channel, "repo": self.repo,
            "ref": self.ref, "allow_dev": self.allow_dev,
        }


def configured_source(root: Path | None = None) -> RuntimeSource:
    path = source_config.config_path(root)
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return RuntimeSource()
    try:
        data = source_config.tomllib.loads(content)
    except source_config.tomllib.TOMLDecodeError as error:
        raise ValueError(f"Cannot read runtime source policy from {path}: {error}") from error
    table = data.get("runtime_source", {})
    if not isinstance(table, dict) or table.keys() - {"channel", "repo", "ref", "allow_dev"}:
        raise ValueError(f"Invalid [runtime_source] policy in {path}")
    return RuntimeSource(**table)


def require_marketplace(root: Path | None = None) -> None:
    selection = configured_source(root)
    if selection.channel != "marketplace":
        raise ValueError(
            f"Runtime source {selection.channel!r} is configured, but source-backed "
            "execution is unavailable through this targeted/bootstrap/repair entry point. "
            "No update was run. "
            "Use runtime-source resolve to inspect acquisition, or runtime-source reset "
            "to explicitly restore marketplace runtime delivery."
        )


def set_source(selection: RuntimeSource, *, root: Path | None = None) -> None:
    """Replace only this policy table; retain Manager self-source and other config."""
    path = source_config.config_path(root)
    configured_source(root)  # Invalid existing policy must not silently become a default.
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        content = ""
    kept: list[str] = []
    skipping = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "[runtime_source]":
            skipping = True
        elif stripped.startswith("["):
            skipping = False
        if not skipping:
            kept.append(line)
    remainder = "\n".join(kept).strip()
    table = "[runtime_source]\n" + "\n".join(
        f"{key} = {json.dumps(value, ensure_ascii=False)}"
        for key, value in selection.document().items()
    )
    replacement = (remainder + "\n\n" if remainder else "") + table + "\n"
    # Reject alternate TOML table spellings instead of appending a duplicate policy.
    if source_config.tomllib.loads(replacement).get("runtime_source") != selection.document():
        raise ValueError("Cannot replace noncanonical [runtime_source] table")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=".runtime-source-", suffix=".toml", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(replacement)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
