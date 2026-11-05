"""Exact package identity and complete-version observations for compatibility."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .resources import RunOutcome


def _name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def parse_pip(out: RunOutcome, package_id: str) -> dict[str, Any]:
    if out.returncode != 0:
        absent = out.returncode == 1 and not out.stdout.strip() and re.fullmatch(
            rf"WARNING: Package\(s\) not found: {re.escape(package_id)}", out.stderr.strip(),
        ) is not None
        return {"known": absent, "present": False, "version": None}
    fields = {}
    for line in out.stdout.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip().casefold()] = value.strip()
    present = _name(fields.get("name", "")) == _name(package_id)
    return {"known": present, "present": present, "version": fields.get("version") if present else None}


def parse_tool_list(out: RunOutcome, package_id: str) -> dict[str, Any]:
    if out.returncode != 0:
        return {"known": False, "present": False, "version": None}
    for line in out.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] == "package":
            fields = fields[1:]
        if fields and _name(fields[0]) == _name(package_id):
            version = fields[1].removesuffix(",") if len(fields) > 1 else None
            if version and version.startswith("v"):
                version = version[1:]
            return {"known": True, "present": True, "version": version}
    return {"known": True, "present": False, "version": None}


def parse_dpkg(out: RunOutcome, package_id: str) -> dict[str, Any]:
    present = out.returncode == 0 and bool(out.stdout.strip())
    absent = out.returncode == 1 and not out.stdout.strip() and re.fullmatch(
        rf"dpkg-query: no packages found matching {re.escape(package_id)}", out.stderr.strip(),
    ) is not None
    return {
        "known": present or absent, "present": present,
        "version": out.stdout.strip() if present else None,
    }
