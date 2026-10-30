"""New-session mode policy; never changes existing session identity or transport."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import yaml
from pydantic import TypeAdapter

from . import config
from .preference_models import SessionMode, SessionModes, SessionRepo, SessionVenue


def _scope(repo: str | None, venue: str | None) -> list[str]:
    if repo is not None:
        TypeAdapter(SessionRepo).validate_python(repo)
    if venue is not None:
        TypeAdapter(SessionVenue).validate_python(venue)
    return (["repos", repo] if repo is not None else []) + (
        ["venues", venue] if venue is not None else ["default"]
    )


def _select(policy: SessionModes, repo: str | None, venue: str | None):
    data = policy.model_dump(exclude_none=True)
    scopes = []
    if repo is not None:
        if venue is not None:
            scopes.append(["repos", repo, "venues", venue])
        scopes.append(["repos", repo, "default"])
    if venue is not None:
        scopes.append(["venues", venue])
    scopes.append(["default"])
    for scope in scopes:
        value = data
        for key in scope:
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None:
            return {"mode": value, "source": "session_modes." + ".".join(scope)}
    return None


def _repo_policy(root: Path):
    policy = config.load_repo_bridge_config(root)
    if policy is None:
        return None
    # Attribute composed repository policy to all authoritative layers, including
    # the explicit marketplace overlay, rather than inventing a single owner.
    paths = ", ".join(str(path) for path in config._repo_config_layers(root))
    return policy.session_modes, paths


def _absolute_root(value: Path) -> Path:
    if not value.is_absolute() or not value.is_dir():
        raise ValueError("configuration repository must be an existing absolute directory")
    return value


def resolve_session_mode(
    *,
    operator_repo: Path | None = None,
    repo: str | None = None,
    venue: str | None = None,
    default: str = "acp",
    repo_root: Path | None = None,
) -> dict[str, str]:
    """Operator > current repository > service > adopter fallback; scope within each."""
    _scope(repo, venue)
    TypeAdapter(SessionMode).validate_python(default)
    # Validate every present owner even if a higher-priority policy wins.
    service = config.load_config(strict=True)
    layers = []
    for root in (operator_repo, repo_root):
        if root is not None:
            layer = _repo_policy(_absolute_root(root))
            if layer is not None:
                layers.append(layer)
    layers.append((service.session_modes, str(config.config_dir() / "config.yaml")))
    for policy, path in layers:
        selected = _select(policy, repo, venue)
        if selected is not None:
            return {"mode": selected["mode"], "source": f"{path}#{selected['source']}"}
    return {"mode": default, "source": "default"}


def write_session_mode(
    *,
    operator_repo: Path | None = None,
    machine_local: bool = False,
    repo: str | None = None,
    venue: str | None = None,
    mode: str | None = None,
    unset: bool = False,
) -> dict[str, str | None]:
    """Atomically update only an explicitly selected destination and scope."""
    scope = _scope(repo, venue)
    if (operator_repo is not None) == machine_local:
        raise ValueError("writes require exactly one of --operator-repo or --machine-local")
    if unset == (mode is not None):
        raise ValueError("choose exactly one of --set or --unset")
    if mode is not None:
        TypeAdapter(SessionMode).validate_python(mode)
    if operator_repo is not None:
        root = _absolute_root(operator_repo)
        # Preserve the existing canonical/legacy authority, never migrate it here.
        config.load_repo_bridge_config(root)
        path = config._repo_base_config_path(root)
        path = path or root / config.CANONICAL_REPO_CONFIG_DIR / config.CONFIG_FILENAME
    else:
        config.load_config(strict=True)
        path = config.config_dir() / config.CONFIG_FILENAME
    before = path.read_bytes() if path.exists() else None
    data = yaml.safe_load(before) if before is not None else {}
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise ValueError("configuration must be a mapping")
    SessionModes.model_validate(data.get("session_modes", {}))
    policy = data.setdefault("session_modes", {})
    target = policy
    for key in scope[:-1]:
        target = target.setdefault(key, {})
    if unset:
        target.pop(scope[-1], None)
    else:
        target[scope[-1]] = mode
    SessionModes.model_validate(policy)
    encoded = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{uuid4().hex}.pending")
    try:
        with staged.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if (path.read_bytes() if path.exists() else None) != before:
            raise ValueError("configuration changed during update; retry from current policy")
        os.replace(staged, path)
        actual = yaml.safe_load(path.read_text(encoding="utf-8"))
        if actual != data:
            raise ValueError("session-mode configuration readback failed")
    finally:
        staged.unlink(missing_ok=True)
    return {"mode": mode, "source": f"{path}#session_modes.{'.'.join(scope)}"}


def current_repo_root() -> Path:
    """Use the nearest checkout, not a guessed operator configuration repository."""
    cwd = Path.cwd()
    return next((path for path in (cwd, *cwd.parents) if (path / ".git").exists()), cwd)
