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
import os
import stat
from dataclasses import dataclass
from pathlib import Path

CONSENT_SCHEMA = "copilot-extensions.projection-reflect-consent"
CONSENT_VERSION = 1

#: Where a repo's own opt-in lives -- alongside the sync lock/config this
#: same automation already reads/writes (`.github/copilot/`).
CONSENT_PATH_PARTS = (".github", "copilot", "projection-reflect.json")

MAX_TRUSTED_MARKETPLACES = 16

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _is_indirection(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or _is_reparse(info)


def _has_indirected_component(repo_root: Path, path: Path) -> bool:
    """Reject a consent path reached through a symlink/reparse point at any
    level -- the file itself, or any parent directory up to ``repo_root``
    (a symlinked parent is exactly as much of an escape as a symlinked
    file). This gate controls repo-write and auto-merge consent, so it must
    never resolve indirection that could source an opt-in from outside the
    repo the committed file appears to live in.
    """
    current = path
    while current != repo_root and current.parent != current:
        if _is_indirection(current):
            return True
        current = current.parent
    return False


@dataclass(frozen=True)
class Consent:
    """A repo's validated, live-checked opt-in for `projection-reflect`.

    ``require_immutable_pin`` defaults to ``False`` (absent in the committed
    file): most adopters sync externally-installed marketplace plugins,
    which today's resolver (``scan_plugin_sources.resolve_pinned_commits``)
    cannot pin at all -- requiring a pin unconditionally would silently
    disable the bypass path entirely for that common case. A repo opts in
    explicitly only once it understands the tradeoff (today: only
    self-hosted/directory-marketplace sources can ever be pinned).
    """

    reconciler_agent: str
    trusted_marketplaces: tuple[str, ...]
    dispatch_label: str
    require_immutable_pin: bool = False


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
    if _has_indirected_component(repo_root, path):
        return None
    try:
        info = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        # Refuse anything but a genuine regular file (a symlink, junction,
        # or other reparse point is rejected by `_has_indirected_component`
        # above; this also rejects the rare case of a non-regular file --
        # e.g. a FIFO or device node -- at the exact consent path).
        return None
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

    require_pin = raw.get("requireImmutablePin", False)
    if not isinstance(require_pin, bool):
        # Present but not a genuine boolean -- malformed, fail closed on
        # the whole consent file rather than guessing an interpretation.
        return None

    return Consent(
        reconciler_agent=reconciler_agent,
        trusted_marketplaces=tuple(trusted_raw),
        dispatch_label=dispatch_label,
        require_immutable_pin=require_pin,
    )
