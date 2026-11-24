"""Strict, data-only decoding of a PR's frozen attribution decision."""

from __future__ import annotations

from typing import Any, TypedDict


# Unknown persisted modes never authorize publication.
_VALID_ATTRIBUTION_MODES = frozenset({"", "false", "true", "codename"})


class _AttributionPair(TypedDict):
    attribution_mode: str
    attribution_explicit: bool


def _parse_frozen_attribution_pair(raw: dict[str, Any]) -> _AttributionPair:
    """Strictly validate the persisted ``attribution_mode``/
    ``attribution_explicit`` pair (round-30 finding, corrected round-33)
    -- the same never-treat-unknown-as-safe discipline round-12
    established for ``codename_source``, applied here to a PAIR of fields
    that must migrate together:

    * ``attribution_explicit`` is read via a STRICT boolean check -- a
      value that is not literally a Python ``bool`` (a hand-edited
      ``attribution_explicit: "false"``, a truthy STRING) invalidates the
      WHOLE pair back to the empty legacy sentinel, not just itself
      (fix-PR-#3037-review finding): a naive "coerce non-True to False"
      would leave `attribution_mode` VALID while only neutralizing
      `attribution_explicit` -- but the raw-marker (`"true"`) mode
      publishes unconditionally on mode alone, never consulting
      `attribution_explicit` first, so that shape would still let a
      malformed record authorize a privacy-sensitive marker.
    * ``attribution_mode`` is validated against the closed
      ``_VALID_ATTRIBUTION_MODES`` set -- an unrecognized value is
      migrated exactly like a missing one (never read as one of the
      three known modes, never perpetually re-derived from live config
      on every load).
    * A PARTIAL pair (only one of the two fields present) is ALSO treated
      as the empty legacy sentinel -- never let a half-written record
      produce a mode without its matching explicitness, or an
      explicitness without its matching mode.

    Returns a dict of the two fields' constructor kwargs (empty-string
    mode + ``False`` explicitness for every "not a clean, complete,
    known-valid pair" case), for the empty-legacy-sentinel migration path
    ``refresh_source_attribution``/``_open_via_provider`` lazily freezes
    on first touch.
    """
    mode_present = "attribution_mode" in raw
    explicit_present = "attribution_explicit" in raw
    if not (mode_present and explicit_present):
        return {"attribution_mode": "", "attribution_explicit": False}
    mode_raw = raw.get("attribution_mode")
    mode = mode_raw if isinstance(mode_raw, str) else ""
    if mode not in _VALID_ATTRIBUTION_MODES:
        return {"attribution_mode": "", "attribution_explicit": False}
    explicit_raw = raw.get("attribution_explicit")
    if not isinstance(explicit_raw, bool):
        return {"attribution_mode": "", "attribution_explicit": False}
    return {"attribution_mode": mode, "attribution_explicit": explicit_raw}
