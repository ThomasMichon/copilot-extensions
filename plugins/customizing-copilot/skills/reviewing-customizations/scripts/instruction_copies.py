"""Declaration-only lookup of disposable instruction copies, without content admission."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import stat
from typing import Callable, Iterable
import unicodedata

from instruction_delivery_io import DeliveryIO
from instruction_delivery import managed_destination, managed_identity


@dataclass(frozen=True)
class DeclaredCopy:
    plugin: str
    source_id: str
    destination: str
    local_destination: str
    template: Path
    apply_to: str


@dataclass
class Declarations:
    copies: list[DeclaredCopy] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    declared: int = 0


def load_declarations(
    sources: Iterable[object], io: DeliveryIO, *,
    workspace: Path,
    validate_path: Callable[..., PurePosixPath],
    load_json: Callable[[bytes], object],
) -> Declarations:
    """Read enabled declarations, not instruction bytes, versions or evidence."""
    result = Declarations()
    seen: set[str] = set()
    for source in sources:
        origin = str(getattr(source, "origin", ""))
        try:
            if getattr(source, "error", ""):
                raise ValueError(source.error)
            market, separator, name = origin.partition("/")
            if not separator:
                raise ValueError("plugin source must have marketplace/plugin identity")
            _name, _market = managed_identity(f"{name}@{market}")
            plugin = f"{_name}@{_market}"
            if plugin in seen:
                result.copies = [copy for copy in result.copies if copy.plugin != plugin]
                raise ValueError("enabled plugin is declared more than once")
            seen.add(plugin)
            root = getattr(source, "payload_root", None)
            if not isinstance(root, Path):
                skills = getattr(source, "skills_root", None)
                root = skills.parent if isinstance(skills, Path) else None
            if root is None:
                raise ValueError("enabled plugin has no payload root")
            output = unicodedata.normalize("NFC", (workspace / ".github/instructions/agent-worktrees-generated").as_posix()).casefold()

            def outside_output(path: Path) -> None:
                key = unicodedata.normalize("NFC", path.resolve().as_posix()).casefold()
                if key == output or key.startswith(output + "/"):
                    raise ValueError("instruction source overlaps the disposable output namespace")

            outside_output(root)
            for relative in ("plugin.json", ".claude-plugin/plugin.json"):
                manifest_path = io.safe(root, PurePosixPath(relative))
                try:
                    manifest_path.lstat()
                except FileNotFoundError:
                    continue
                manifest = load_json(io.read(manifest_path, 64 * 1024).removeprefix(b"\xef\xbb\xbf"))
                if not isinstance(manifest, dict) or manifest.get("name") != _name:
                    raise ValueError("enabled plugin manifest identity is invalid")
                break
            else:
                raise ValueError("enabled plugin manifest is missing")
            declaration = io.safe(root, PurePosixPath("instruction-projections.json"))
            try:
                declaration.lstat()
            except FileNotFoundError:
                continue
            data = load_json(io.read(declaration, 256 * 1024).removeprefix(b"\xef\xbb\xbf"))
            if (
                not isinstance(data, dict)
                or data.get("schema") != "copilot-extensions.instruction-projections"
                or type(data.get("version")) is not int or data["version"] != 1
                or not isinstance(data.get("projections"), list)
            ):
                raise ValueError("invalid instruction declarations")
            copies: list[DeclaredCopy] = []
            ids: set[str] = set()
            for entry in data["projections"]:
                if not isinstance(entry, dict):
                    raise ValueError("instruction declaration must be an object")
                source_id = entry.get("id")
                if not isinstance(source_id, str) or not source_id or source_id in ids:
                    raise ValueError("missing or duplicate instruction source id")
                ids.add(source_id)
                if entry.get("customizationKind", "instructions") != "instructions":
                    raise ValueError("unsupported instruction customization kind")
                destination = validate_path(entry.get("destination"), field_name="destination")
                if (
                    destination.parts[:2] != (".github", "instructions")
                    or len(destination.parts) < 3
                    or not destination.name.endswith(".instructions.md")
                ):
                    raise ValueError("invalid instruction destination")
                template = io.safe(
                    root, validate_path(entry.get("template"), field_name="template"),
                )
                outside_output(template)
                scope = entry.get("applyTo", "**")
                if not isinstance(scope, str) or not scope:
                    raise ValueError("invalid instruction applicability")
                copies.append(DeclaredCopy(
                    plugin, source_id, destination.as_posix(),
                    managed_destination(destination.as_posix(), plugin), template, scope,
                ))
            result.copies.extend(copies)
            result.declared += len(copies)
        except (OSError, ValueError) as exc:
            result.failures[origin] = f"{origin}: {exc}"
    conflicting: set[DeclaredCopy] = set()
    keyed = [
        (copy, unicodedata.normalize("NFC", copy.local_destination).casefold())
        for copy in result.copies
    ]
    for index, (copy, key) in enumerate(keyed):
        for other, previous in keyed[:index]:
            if key == previous or key.startswith(previous + "/") or previous.startswith(key + "/"):
                conflicting.update((copy, other))
    for copy in sorted(conflicting, key=lambda copy: (copy.local_destination, copy.plugin, copy.source_id)):
        result.failures[f"{copy.plugin}:{copy.source_id}"] = (
            f"ambiguous instruction output destination: {copy.local_destination}"
        )
    result.copies = [copy for copy in result.copies if copy not in conflicting]
    return result


def require_pair_complete(root: Path, io: DeliveryIO) -> None:
    marker = io.safe(root, PurePosixPath(".git"))
    try:
        info = marker.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode):
        git_dir = marker
    else:
        text = io.read(marker, 4096).decode("utf-8")
        if not text.startswith("gitdir:") or not text[len("gitdir:"):].strip():
            raise ValueError("invalid Git administrative pointer")
        target = Path(text[len("gitdir:"):].strip())
        git_dir = target if target.is_absolute() else root / target
    pending = io.safe(git_dir, PurePosixPath("agent-worktrees-local-guidance/pair-pending"))
    try:
        pending.lstat()
    except FileNotFoundError:
        return
    raise ValueError("paired worktree preparation is incomplete; repair the pair before publishing instructions")


def select_copy(
    root: Path, destination: str, declarations: Declarations, io: DeliveryIO,
) -> dict[str, object] | None:
    key = unicodedata.normalize("NFC", destination).casefold()
    matches = [
        copy for copy in declarations.copies
        if unicodedata.normalize("NFC", copy.destination).casefold() == key
    ]
    if len(matches) > 1:
        raise ValueError("instruction destination has ambiguous enabled declarations")
    if not matches:
        return None
    copy = matches[0]
    candidate = io.safe(root, PurePosixPath(copy.local_destination))
    try:
        candidate.lstat()
    except FileNotFoundError:
        return None
    io.read(candidate, 2 * 1024 * 1024)
    return {
        "schema": "copilot-extensions.instruction-source-selection",
        "version": 1,
        "selectedPath": copy.local_destination,
        "identity": {
            "plugin": copy.plugin, "sourceId": copy.source_id, "applyTo": copy.apply_to,
        },
        "bodySha256": None, "receiptBindingSha256": None, "receiptCapable": False,
        "modelAdmission": "unknown",
        "diagnostic": "; ".join(declarations.failures.values()) or None,
    }
