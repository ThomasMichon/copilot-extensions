"""File-backed machine-only provenance for process-log retirement policy."""

from __future__ import annotations

import copy
import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_logger.sync.log_archives import _fingerprint
from agent_logger.sync.publication_directory import publication_directory

if TYPE_CHECKING:
    from agent_logger.config import Config

_MAX_CONFIG_BYTES = 1024 * 1024


def _machine_data(home: Path) -> dict[str, Any]:
    from agent_logger.config import DEFAULTS, _deep_merge, _load_user_config

    data = _deep_merge(DEFAULTS, _load_user_config(home / "config.yaml"))
    if os.environ.get("AGENT_LOGGER_SYNC_TARGET"):
        data["sync"]["target"] = os.environ["AGENT_LOGGER_SYNC_TARGET"]
    if os.environ.get("AGENT_LOGGER_VOICE_PACK"):
        data["log"]["voice_pack"] = os.environ["AGENT_LOGGER_VOICE_PACK"]
    return data


@contextmanager
def _pinned_config(home: Path) -> Iterator[tuple[tuple[int, ...], str]]:
    with publication_directory(home, create=False) as root:
        with root.open("config.yaml", protected=True) as stream:
            before = os.fstat(stream.fileno())
            if before.st_size > _MAX_CONFIG_BYTES:
                raise ValueError("machine-local reclamation configuration exceeds size limit")
            content = stream.read(_MAX_CONFIG_BYTES + 1)
            if len(content) != before.st_size or len(content) > _MAX_CONFIG_BYTES:
                raise ValueError("machine-local reclamation configuration changed while reading")
            revision = _fingerprint(before)
            if (
                _fingerprint(os.fstat(stream.fileno())) != revision
                or _fingerprint(root.stat("config.yaml")) != revision
            ):
                raise ValueError("machine-local reclamation configuration changed while opening")
            yield revision, hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class MachineConfigOrigin:
    home: Path
    revision: tuple[int, ...]
    sha256: str
    expected: dict[str, Any] = field(repr=False)

    @contextmanager
    def guard(self, cfg: Config) -> Iterator[None]:
        """Hold the original policy file/ancestry unwriteable through disposition."""
        with _pinned_config(self.home) as (revision, digest):
            if revision != self.revision or digest != self.sha256:
                raise ValueError("machine-local reclamation policy file changed")
            if (
                cfg.home != self.home or cfg.repo_config_path is not None
                or cfg.repo_root is not None or cfg.as_dict() != self.expected
                or _machine_data(self.home) != self.expected
            ):
                raise ValueError("machine-local reclamation policy or configuration changed")
            yield


def machine_config_origin(
    data: dict[str, Any], home: Path, *, include_repo: bool,
) -> MachineConfigOrigin | None:
    """Admit only an explicit machine-only loader, never a caller's Config dict."""
    raw = data.get("sync", {}).get("process_logs", {}).get("reclamation")
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return None
    if include_repo:
        return None  # Ordinary config consumers receive no retirement authority.
    absolute = home.expanduser().absolute()
    with _pinned_config(absolute) as (revision, digest):
        if _machine_data(absolute) != data:
            raise ValueError("machine-local reclamation configuration changed during load")
        return MachineConfigOrigin(absolute, revision, digest, copy.deepcopy(data))
