"""Bind guarded wheel filtering to the resolver's native interpreter."""
from __future__ import annotations

import os
import platform
import hashlib
import json
import re
import sys
from urllib.parse import urlsplit

import installer_dependency_policy as dependencies
import installer_native_feed as native_feed
import installer_wheel_inputs as inspection
from core_native_feed_errors import GuardedSelectionError
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

_TAG_PART = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")


def require_native_target(transaction, projection: str, target_tags: list[str]) -> None:
    try:
        pairs = list(dependencies._argument_pairs(transaction.argv[3:]))
        pythons = [value for option, value in pairs if option in ("--python", "-p")]
        versions = [value for option, value in pairs if option == "--python-version"]
        outer = native_feed._projection(projection, "PipCompileSettings")
        settings = native_feed._fields(native_feed._body(outer["settings"], "PipSettings"))
        reported = native_feed._optional(settings["python"])
        current = os.path.normcase(os.path.realpath(sys.executable))
        if (
            target_tags != [str(tag) for tag in sys_tags()]
            or len(pythons) != 1 or os.path.normcase(os.path.realpath(pythons[0])) != current
            or versions != [platform.python_version()]
            or reported is None or os.path.normcase(os.path.realpath(reported)) != current
            or settings.get("python_platform") != "None" or settings.get("universal") != "false"
        ):
            raise ValueError
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError("guarded resolver target differs from the native interpreter") from None


def project_for_target(project, name, compatible_tags, wheel_limits, deadline):
    if project is None:
        return None
    entries, base, origin, transport = project
    candidates, seen = [], set()
    for entry in entries:
        inspection._check_deadline(deadline)
        filename = entry.get("filename")
        if not isinstance(filename, str):
            raise GuardedSelectionError("guarded index contains an invalid filename")
        if not filename.endswith(".whl"):
            continue
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,250}\.whl", filename) is None
            or inspection._unsafe_windows_component(filename)
        ):
            raise GuardedSelectionError("guarded index contains an invalid wheel name")
        tokens = filename[:-4].split("-")
        if len(tokens) < 5:
            raise GuardedSelectionError("guarded index contains an invalid wheel name")
        if any(_TAG_PART.fullmatch(part) is None for part in tokens[-3:]):
            # Preserve prefix/project validation before omitting an unselectable tag.
            prefix_probe = "-".join((*tokens[:-3], "py3", "none", "any")) + ".whl"
            wheel_name, _version, _build, _tags = parse_wheel_filename(prefix_probe)
            if canonicalize_name(wheel_name) != name:
                raise GuardedSelectionError("guarded index contains another project")
            continue
        inspection.bounded_tag(
            "-".join(tokens[-3:]), limit=wheel_limits.max_tags, deadline=deadline,
        )
        wheel_name, _version, _build, tags = parse_wheel_filename(filename)
        if canonicalize_name(wheel_name) != name:
            raise GuardedSelectionError("guarded index contains another project")
        location, hashes = entry.get("url"), entry.get("hashes", {})
        if not isinstance(location, str) or not location or not isinstance(hashes, dict):
            raise GuardedSelectionError("guarded index contains an invalid wheel entry")
        fragment = urlsplit(location).fragment
        fragment_digest = fragment.removeprefix("sha256=") if fragment.startswith("sha256=") else None
        digest = hashes.get("sha256", fragment_digest)
        if digest is not None and (
            not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise GuardedSelectionError("guarded index contains an invalid digest")
        yanked, withdrawn = entry.get("yanked", False), entry.get("withdrawn", False)
        if not isinstance(yanked, (bool, str)) or type(withdrawn) is not bool:
            raise GuardedSelectionError("guarded index contains invalid admission evidence")
        if not withdrawn:
            hint, uploaded = entry.get("requires-python"), entry.get("upload-time")
            if hint not in (None, ""):
                if not isinstance(hint, str) or len(hint) > 4096:
                    raise GuardedSelectionError("guarded index contains an invalid Python hint")
                try:
                    SpecifierSet(hint)
                except InvalidSpecifier:
                    raise GuardedSelectionError("guarded index contains an invalid Python hint") from None
            if uploaded is not None and (
                not isinstance(uploaded, str) or len(uploaded) > 128
                or not uploaded.isascii() or any(ord(char) < 32 for char in uploaded)
            ):
                raise GuardedSelectionError("guarded index contains an invalid upload time")
            folded = filename.casefold()
            if folded in seen:
                raise GuardedSelectionError("guarded index contains duplicate wheel names")
            seen.add(folded)
        if not tags.isdisjoint(compatible_tags):
            candidates.append(entry)
    identity = hashlib.sha256(json.dumps(
        [transport, candidates], sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return candidates, base, origin, identity
