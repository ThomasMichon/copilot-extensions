"""Shared install-root helpers for agent-dispatch runtime state."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

INSTALL_DIR_ENV = "AGENT_DISPATCH_INSTALL_DIR"
LEGACY_INSTALL_DIRNAME = ".agent-dispatch"  # marketplace-isolation: allow legacy-compatibility
SCOPED_SERVICE_HASH_LENGTH = 12


def normalized_path(path: Path) -> str:
    raw = os.path.abspath(os.fspath(path.expanduser()))
    if os.name == "nt":
        return os.path.normcase(raw).replace("/", "\\")
    return os.path.normpath(raw)


def legacy_install_dir() -> Path:
    """The historic machine-global agent-dispatch install root."""
    return Path.home() / LEGACY_INSTALL_DIRNAME


def install_dir() -> Path:
    """The active agent-dispatch install root for this process."""
    override = os.environ.get(INSTALL_DIR_ENV)
    return Path(override).expanduser() if override else legacy_install_dir()


def uses_legacy_install_dir(path: Path | None = None) -> bool:
    """Whether ``path`` resolves to the historic machine-global install root."""
    candidate = install_dir() if path is None else path
    return normalized_path(candidate) == normalized_path(legacy_install_dir())


def installation_suffix(path: Path | None = None) -> str:
    """Stable short suffix for non-legacy install roots, empty for legacy."""
    candidate = install_dir() if path is None else path
    if uses_legacy_install_dir(candidate):
        return ""
    return hashlib.sha256(normalized_path(candidate).encode("utf-8")).hexdigest()[
        :SCOPED_SERVICE_HASH_LENGTH
    ]


def apply_service_env_overlay(env: dict[str, str], install_dir_path: Path) -> dict[str, str]:
    """Overlay ``service.env`` (token / host-port pins) onto ``env`` in place.

    Every detached coordinator spawn -- the first-use bootstrap in
    ``__main__.py`` AND a zero-downtime cutover's replacement in
    ``coordinator_cli.py`` -- must apply this identically, so the durable,
    installed config (most critically ``AGENT_DISPATCH_CONTROL_TOKEN`` /
    ``_COMMAND``) is guaranteed regardless of which process happened to
    trigger that particular spawn.

    A cutover can be triggered from any process context -- the
    systemd/Scheduled-Task supervisor (which loads ``service.env`` itself via
    its unit's ``EnvironmentFile``), a plain CLI invocation, or a self-update
    -- and only the supervisor's own happens to carry the durable settings
    ambiently. Without this overlay, a replacement coordinator spawned from
    any other context would come up missing the control-token command (and
    any other installed pin), and evaluator/producer-scope registrations
    would fail with ``control_authority_not_configured``.

    Returns ``env`` for convenient chaining; mutates it in place.
    """
    env_file = install_dir_path / "service.env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                env[k.strip()] = os.path.expandvars(v.strip())
        except OSError:
            pass
    return env
