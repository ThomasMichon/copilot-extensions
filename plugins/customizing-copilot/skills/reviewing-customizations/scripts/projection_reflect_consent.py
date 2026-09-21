"""projection-reflect adopter consent -- the repo-write ownership signal.

Per `docs/patterns/install-vs-adopt-boundary.md`: granting a scheduler
repo-write authority and a review-bypass profile is a repo mutation, not a
machine-local install/update concern. Before any `projection-reflect`
automation (the scheduled sync worker, or a review gate's bypass profile) is
allowed to act, it must find this repo's own **explicit, committed, in-repo
opt-in** -- never merely "the operator asked for it in this session" or "the
repo is PR-gated" (a repo you only contribute to is often PR-gated too).

**Consent is rechecked live, not only at setup time.** Both the scheduled
worker and the bypass profile call :func:`load_consent` on every run/every
PR -- there is no separate "is this repo enrolled" cache. The moment the
committed file is missing, malformed, or explicitly disabled, this module
returns ``None`` and every caller must fail closed (the worker refuses to
open a PR; the bypass refuses to auto-merge) without requiring a second
`setup` invocation. Withdrawing consent is exactly: delete or edit the file
and let the next scheduled run/PR observe it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONSENT_SCHEMA = "copilot-extensions.projection-reflect-consent"
CONSENT_VERSION = 1

#: Where a repo's own opt-in lives -- alongside the sync lock/config this
#: same automation already reads/writes (`.github/copilot/`).
CONSENT_PATH_PARTS = (".github", "copilot", "projection-reflect.json")

MAX_TRUSTED_MARKETPLACES = 16


@dataclass(frozen=True)
class Consent:
    """A repo's validated, live-checked opt-in for `projection-reflect`."""

    reconciler_agent: str
    trusted_marketplaces: tuple[str, ...]
    dispatch_label: str


def _valid_identifier(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 128


def load_consent(repo_root: Path) -> Consent | None:
    """Read and validate this repo's committed opt-in file.

    Returns ``None`` -- never raises -- for every refusal case: the file is
    absent, unreadable, malformed, or explicitly ``"enabled": false``. Every
    caller treats ``None`` uniformly as "this repo has not consented (or has
    withdrawn consent); do nothing automated." A caller must never fall back
    to some other signal (a session request, a PR-gated policy, an operator
    instruction) when this returns ``None``.
    """
    path = repo_root.joinpath(*CONSENT_PATH_PARTS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    if not isinstance(raw, dict):
        return None
    if raw.get("schema") != CONSENT_SCHEMA:
        return None
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != CONSENT_VERSION:
        return None
    if raw.get("enabled") is not True:
        # Explicit False, missing, or any non-boolean-true value all refuse --
        # only an explicit `"enabled": true` is a live, current opt-in.
        return None

    reconciler_agent = raw.get("reconcilerAgent")
    if not _valid_identifier(reconciler_agent):
        return None

    dispatch_label = raw.get("dispatchLabel")
    if not _valid_identifier(dispatch_label):
        return None

    trusted_raw = raw.get("trustedMarketplaces")
    if not isinstance(trusted_raw, list) or not trusted_raw:
        return None
    if len(trusted_raw) > MAX_TRUSTED_MARKETPLACES:
        return None
    if not all(_valid_identifier(entry) for entry in trusted_raw):
        return None

    return Consent(
        reconciler_agent=reconciler_agent,
        trusted_marketplaces=tuple(trusted_raw),
        dispatch_label=dispatch_label,
    )
