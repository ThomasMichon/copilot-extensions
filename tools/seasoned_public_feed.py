#!/usr/bin/env python3
"""Seasoned public-PyPI version resolution for public build runners
without access to a consumer's private governed package feed.

Part of the `governed-python-artifact-promotion` effort's Phase 2 pipeline
wiring (`efforts/active/governed-python-artifact-promotion/README.md`):
``tools/build_toolchain_lock.py``'s ``resolve_toolchain_lock`` normally
requires an affirmatively trusted governed feed and fails closed without
one, by design -- correct for every OTHER caller of this tool (a managed
machine, which always has governed-feed access), but promotion itself runs
on a public runner with none.

This module does **not** reach for "whatever public PyPI currently calls
latest" as a substitute -- that would routinely pick a version newer than
what a real governed feed has even caught up to yet (this effort's own
Phase 1 spike measured per-package propagation lag from 0 to 65+ days), so
a toolchain built that way could silently diverge from what a managed
machine would actually resolve for the exact same declared dependency.
Instead, it resolves the newest version that has been on public PyPI for
at least a configurable minimum age (a seasoning window) -- a deliberate
approximation of a governed feed's own lag, not an exact substitute for
querying one directly (Phase 3's consumer-side admission check, by
contrast, queries a REAL governed feed directly at install time on a
managed machine, since it has one available -- see that phase's own
Validation Plan). Yanked and pre-release versions are never selected,
regardless of age, and a version's age is measured from its NEWEST
uploaded file -- not its oldest -- so every file in the metadata snapshot
clears the floor. The version-only API returns a bare pin; the release API
also returns eligible file hashes, which the toolchain installs with hash
enforcement so later same-version uploads cannot supply unseasoned bytes.
"""
from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

#: Default seasoning window, in days, applied when a caller doesn't name
#: one explicitly. Not derived from this effort's own Phase 1 lag
#: measurement (0-65+ days, which Phase 1 itself found too package-
#: specific to generalize into one constant) -- this is a deliberately
#: modest, cheap-to-satisfy floor for the build TOOLCHAIN specifically
#: (setuptools/wheel/packaging: foundational, slow-moving packaging
#: tooling, not a plugin's own runtime dependency closure), reducing the
#: odds of resolving a same-day release a governed feed could not
#: possibly have mirrored yet -- not a guarantee of exact governed-feed
#: parity.
DEFAULT_MIN_AGE_DAYS = 7

#: The PyPI JSON API endpoint template (see
#: https://warehouse.pypa.io/api-reference/json.html) -- stdlib-only
#: (`urllib.request`), matching this repo's established "no new runtime
#: dependency for a single HTTP call" pattern (e.g. the marker-environment
#: query script in `build_toolchain_lock.py`).
_PYPI_JSON_URL = "https://pypi.org/pypi/{package}/json"

#: Per-request timeout. A hung request must never stall a CI job
#: indefinitely; a timeout surfaces as the same `SeasonedVersionError` as
#: any other resolution failure, never a silent hang.
_REQUEST_TIMEOUT_SECONDS = 30

#: Matches a numeric release segment (optionally with a leading PEP 440
#: epoch, e.g. "1!2.3.4") and an optional, stable ``.postN`` suffix --
#: deliberately narrower than real PEP 440 otherwise (no pre/dev/local
#: segments at all, which stay excluded). This module never imports the
#: third-party `packaging` library (the exact constraint
#: `build_toolchain_lock.py` already applies to itself -- see its own
#: `_assert_toolchain_satisfies_build_requires` docstring -- so this tool
#: keeps working on a genuinely clean machine with no `packaging`
#: installed anywhere yet). A version string this pattern doesn't match
#: is never guessed at: `_parse_release_version` returns `None` and the
#: caller excludes it, the same conservative "can't safely classify it
#: as a stable release, so don't use it" stance this module takes for an
#: unparseable upload timestamp too. `.postN` is included because post
#: releases are stable (unlike pre/dev releases) -- excluding them could
#: return an older version than a toolchain package's own corrective
#: `.postN` republish, defeating the "newest seasoned stable release"
#: contract this resolver advertises.
_RELEASE_VERSION_RE = re.compile(r"^(?:(\d+)!)?(\d+(?:\.\d+)*)(?:\.post(\d+))?$")


def _parse_release_version(version_str: str) -> tuple[int, tuple[int, ...], int] | None:
    """``(epoch, release_segments, post)`` for a clean release version (a
    numeric release segment, with an optional PEP 440 epoch and/or stable
    ``.postN`` suffix), or ``None`` if ``version_str`` doesn't match that
    narrow shape (including every pre/dev release -- see
    `_RELEASE_VERSION_RE`'s own docstring).

    ``release_segments`` has its trailing zero elements stripped (PEP
    440's own release-segment comparison rule: "1.0" == "1.0.0" == "1", a
    trailing ``.0`` is never significant) -- comparing RAW, un-normalized
    tuples lexicographically would otherwise rank "1.0.0" as newer than
    "1.0.post1" (`(1, 0, 0) > (1, 0)` as plain tuples), even though PEP
    440 says a post release is newer than its own base release. ``post``
    is ``-1`` when the version has no ``.postN`` suffix, so a bare
    release sorts before its own later ``.post1``, matching PEP 440's own
    ordering rule that an unsuffixed release precedes its post
    releases."""
    match = _RELEASE_VERSION_RE.match(version_str.strip())
    if match is None:
        return None
    epoch = int(match.group(1)) if match.group(1) else 0
    release = [int(part) for part in match.group(2).split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    post = int(match.group(3)) if match.group(3) else -1
    return epoch, tuple(release), post


class SeasonedVersionError(RuntimeError):
    """Raised when a seasoned public version cannot be resolved for a
    package -- a network/HTTP failure, a malformed response, or no release
    old enough (and not yanked/pre-release) exists at all. Always fails
    closed: never falls back to "whatever the caller already has" or to
    an unseasoned "latest"."""


def _default_fetch(url: str) -> bytes:
    """The real network fetch -- isolated behind the injectable ``fetch``
    parameter below so every test exercises the actual selection logic
    against a fixed, synthetic response instead of real network calls to
    pypi.org (slow, flaky, and not reproducible evidence)."""
    request = urllib.request.Request(url, headers={"User-Agent": "copilot-extensions-build-toolchain-lock"})
    with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
        return response.read()


def _resolve_seasoned_release(
    package: str,
    *,
    min_age_days: int = DEFAULT_MIN_AGE_DAYS,
    now: datetime | None = None,
    fetch: Callable[[str], bytes] = _default_fetch,
) -> tuple[str, list[dict]]:
    """The newest public-PyPI release of ``package`` whose NEWEST uploaded
    file is at least ``min_age_days`` old as of ``now`` (defaults to the
    real current UTC time), excluding any release PyPI reports as yanked
    or that parses as a pre-release/dev release.

    Returns the selected version AND its snapshot file records. Measuring
    from the newest file's ``upload_time_iso_8601``, not the earliest,
    prevents a seasoned-looking version from carrying a newly uploaded
    file in that snapshot. The version-only wrapper drops the records;
    the release API binds their hashes to installation, preventing a
    subsequent same-version upload from winning live index selection.

    Raises :class:`SeasonedVersionError` -- never returns a value silently
    substituting a different guarantee -- on a negative ``min_age_days``,
    any network/HTTP failure, a response that doesn't parse as the
    expected PyPI JSON shape, or when no qualifying release exists at all
    (every release is too new, yanked, or a pre-release)."""
    if not isinstance(package, str) or re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", package
    ) is None:
        raise SeasonedVersionError("package must be a valid Python distribution name")
    package = re.sub(r"[-_.]+", "-", package).lower()
    if type(min_age_days) is not int or min_age_days < 0:
        raise SeasonedVersionError(
            f"min_age_days={min_age_days!r} must be a non-negative integer "
            "-- invalid values could disable seasoning or admit future-dated uploads"
        )
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        raise SeasonedVersionError(
            f"now={now!r} is a naive datetime -- pass a timezone-aware one "
            "(e.g. datetime.now(timezone.utc)) so the age comparison below "
            "is never silently skewed by an assumed local offset"
        )

    url = _PYPI_JSON_URL.format(package=package)
    try:
        raw = fetch(url)
    except urllib.error.HTTPError as exc:
        raise SeasonedVersionError(
            f"{package}: PyPI JSON API returned HTTP {exc.code} for {url}"
        ) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise SeasonedVersionError(
                f"{package}: PyPI JSON API request or response read timed out"
            ) from exc
        raise SeasonedVersionError(
            f"{package}: could not reach PyPI JSON API at {url}: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise SeasonedVersionError(
            f"{package}: PyPI JSON API request or response read timed out"
        ) from exc
    except (http.client.HTTPException, OSError) as exc:
        raise SeasonedVersionError(
            f"{package}: PyPI JSON API request or response read failed"
        ) from exc

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SeasonedVersionError(
            f"{package}: PyPI JSON API response is not valid JSON"
        ) from exc

    releases = data.get("releases") if isinstance(data, dict) else None
    if not isinstance(releases, dict):
        raise SeasonedVersionError(
            f"{package}: PyPI JSON API response has no 'releases' object"
        )

    candidates: list[tuple[tuple[int, tuple[int, ...], int], str]] = []
    for version_str, files in releases.items():
        if not isinstance(files, list) or not files:
            # No uploaded files at all for this version string (PyPI
            # allows a registered-but-never-uploaded release) -- nothing
            # to install, never a candidate.
            continue
        if any(not isinstance(f, dict) or f.get("yanked") is not False for f in files):
            # ANY yanked file for this version excludes the whole
            # version, even if other files for it are not marked yanked
            # -- a partially-yanked release is not a safe pin.
            continue
        parsed = _parse_release_version(version_str)
        if parsed is None:
            # Not a clean release version -- either a pre/dev release
            # (which must be excluded anyway) or a version string this
            # narrow parser doesn't recognize for some other reason (see
            # `_RELEASE_VERSION_RE`'s own docstring for why "exclude" is
            # the safe default either way).
            continue
        upload_times: list[datetime] = []
        malformed_timestamp = False
        for f in files:
            raw_time = f.get("upload_time_iso_8601")
            if not isinstance(raw_time, str):
                malformed_timestamp = True
                continue
            try:
                parsed_time = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            except ValueError:
                malformed_timestamp = True
                continue
            if parsed_time.tzinfo is None:
                # An offset-less timestamp would otherwise raise an
                # uncaught TypeError when compared against the
                # timezone-aware `now` below -- treat it the same as any
                # other malformed timestamp: never guessed at, excluded
                # via the fail-closed path.
                malformed_timestamp = True
                continue
            upload_times.append(parsed_time)
        if malformed_timestamp or not upload_times:
            # A malformed/missing timestamp on ANY file is treated the
            # same as "we cannot establish this version's age" -- never
            # silently computed from only the files that DID parse,
            # which could make a version that actually has a very recent,
            # unparseable-timestamp file look older than it is.
            continue
        # Measured from the NEWEST file, not the oldest -- see this
        # function's own docstring for why every snapshot file must age,
        # even when the release API also binds installation to its hash.
        newest = max(upload_times)
        age_days = (now - newest).total_seconds() / 86400.0
        if age_days < min_age_days:
            continue
        candidates.append((parsed, version_str))

    if not candidates:
        raise SeasonedVersionError(
            f"{package}: no release at least {min_age_days} day(s) old "
            "(excluding yanked/pre-release versions) found via PyPI's "
            f"JSON API -- refusing to fall back to an unseasoned 'latest'"
        )

    candidates.sort()
    version = candidates[-1][1]
    return version, releases[version]


def resolve_seasoned_public_version(
    package: str, *, min_age_days: int = DEFAULT_MIN_AGE_DAYS,
    now: datetime | None = None, fetch: Callable[[str], bytes] = _default_fetch,
) -> str:
    """Newest stable, non-yanked version whose newest file clears the age floor."""
    return _resolve_seasoned_release(
        package, min_age_days=min_age_days, now=now, fetch=fetch
    )[0]


@dataclass(frozen=True)
class SeasonedPublicRelease:
    version: str
    hashes: tuple[str, ...]


def resolve_seasoned_public_release(
    package: str, *, min_age_days: int = DEFAULT_MIN_AGE_DAYS,
    now: datetime | None = None, fetch: Callable[[str], bytes] = _default_fetch,
) -> SeasonedPublicRelease:
    """Bind the age decision to eligible file bytes, not a mutable live version."""
    version, files = _resolve_seasoned_release(
        package, min_age_days=min_age_days, now=now, fetch=fetch
    )
    hashes = set()
    for file in files:
        digests = file.get("digests")
        digest = digests.get("sha256") if isinstance(digests, dict) else None
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
            raise SeasonedVersionError(f"{package} {version}: missing or invalid file SHA-256")
        hashes.add(digest.lower())
    return SeasonedPublicRelease(version, tuple(sorted(hashes)))
