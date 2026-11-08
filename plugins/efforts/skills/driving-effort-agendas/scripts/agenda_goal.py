"""Read canonical effort intent without creating another mutable goal store."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any


def section(text: str, heading: str) -> str | None:
    lines: list[str] = []
    active = False
    fence: str | None = None
    for line in text.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        if fence is None and not marker:
            if active and re.match(r"^-\s+\*\*(?:Slug|Repo|Status|Created|Branch\(es\)):\*\*", line):
                break
            title = re.match(r"^##\s+(.+?)\s*#*\s*$", line)
            if title:
                if active:
                    break
                active = title[1].casefold() == heading.casefold()
                continue
        if active:
            lines.append(line)
    return "\n".join(lines).strip() or None


def root_goal(source: dict[str, Any], home: Path | None) -> str:
    label = f"{source['kind']}:{source['ref']}"
    if source["kind"] != "effort":
        return label
    reference = source["ref"].split("#", 1)[0]
    path = Path(reference)
    if "://" in reference or not reference:
        return f"{label} [Goal unavailable: not a local effort reference]"
    if not path.is_absolute():
        if home is None:
            return f"{label} [Goal unavailable: source home not supplied]"
        base = home.resolve()
        # A detached managed lane must use an absolute source reference.
        for parent in (base, *base.parents):
            if (parent / ".git").exists():
                base = parent
                break
        path = base / path
    if path.is_dir():
        path /= "README.md"
    try:
        with path.open("rb") as stream:
            raw = stream.read(1048577)
        if len(raw) > 1048576:
            return f"{label} [Goal unavailable: effort exceeds 1 MiB]"
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeError, ValueError):
        return f"{label} [Goal unavailable: cannot read canonical effort]"
    goal = section(text, "Goal")
    if goal:
        display = f"{goal}\n(Source: {source['kind']}:{reference}#goal)"
        if len(display.encode("utf8")) > 4096 or "\x00" in display:
            return f"{label} [Goal exceeds display budget; read canonical {path}#goal in bounded ranges]"
        return display
    legacy = section(text, "Guiding Intent")
    if legacy:
        display = f"{legacy}\n(Source: {label}; legacy Guiding Intent, no Goal)"
        if len(display.encode("utf8")) > 4096 or "\x00" in display:
            return f"{label} [Legacy Guiding Intent exceeds display budget; read canonical {path}]"
        return display
    return f"{label} [Goal unavailable: no Goal or Guiding Intent]"
