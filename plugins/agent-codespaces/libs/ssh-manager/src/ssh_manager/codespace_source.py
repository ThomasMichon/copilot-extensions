"""CodespaceConfigSource -- a ``ConfigSource`` for GitHub Codespaces.

Wraps ``gh codespace ssh --config`` (a plain ``gh`` CLI call -- no
``agent-codespaces`` dependency) to produce an :class:`SSHConfig` the
ConnectionManager and the :class:`~ssh_manager.forward.LocalForward` can use.
Living in the shared ssh-manager lib lets **both** the agent-bridge daemon (which
cannot import agent-codespaces) and agent-codespaces build a codespace SSH config
from one implementation.

The generated config already carries the ``gh cs ssh --stdio`` ``ProxyCommand``;
we write it to a file for ``ssh -F`` and strip gh's ControlMaster defaults (the
manager / forward own multiplexing).
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

from .config_sources import SSHConfig

log = logging.getLogger("ssh-manager.codespace")

# Default host-side dir for generated codespace SSH config files.
_DEFAULT_CONFIG_DIR = Path.home() / ".ssh-manager" / "codespace-config"

# Successive timeouts: first is short (already-running CS), later ones tolerate a
# Shutdown CS cold-starting during ``gh codespace ssh --config`` (60-120s).
_FETCH_TIMEOUTS: tuple[int, ...] = (30, 60, 90)


def _creation_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


class CodespaceConfigSource:
    """ConfigSource that wraps ``gh codespace ssh --config`` for one CodeSpace."""

    def __init__(
        self,
        codespace_name: str,
        *,
        config_dir: Path | str | None = None,
    ) -> None:
        self._codespace_name = codespace_name
        self._config_dir = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
        self._config: SSHConfig | None = None

    @property
    def codespace_name(self) -> str:
        return self._codespace_name

    def get_ssh_config(self) -> SSHConfig:
        if self._config is not None:
            return self._config
        return self.refresh()

    def refresh(self) -> SSHConfig:
        raw = self._fetch_gh_config()
        parsed = self._parse_ssh_config(raw)
        config_path = self._write_config_file(raw)
        self._config = SSHConfig(
            host_alias=parsed["host_alias"],
            user=parsed.get("user"),
            identity_file=parsed.get("identity_file"),
            proxy_command=parsed.get("proxy_command"),
            config_file=str(config_path),
            extra_options=parsed.get("extra_options", {}),
        )
        return self._config

    def _fetch_gh_config(self) -> str:
        args = ["gh", "codespace", "ssh", "--config", "-c", self._codespace_name]
        last_error: Exception | None = None
        for attempt, timeout in enumerate(_FETCH_TIMEOUTS, 1):
            try:
                result = subprocess.run(
                    args, capture_output=True, text=True, timeout=timeout,
                    creationflags=_creation_flags(),
                )
            except FileNotFoundError:
                raise RuntimeError(
                    "gh CLI not found. Install it: https://cli.github.com/"
                ) from None
            except subprocess.TimeoutExpired:
                log.info(
                    "gh codespace ssh --config attempt %d/%d timed out (%ds) "
                    "for %s (CodeSpace may be starting)",
                    attempt, len(_FETCH_TIMEOUTS), timeout, self._codespace_name,
                )
                last_error = RuntimeError(
                    f"Timed out fetching SSH config for codespace "
                    f"{self._codespace_name} after {attempt} attempt(s)."
                )
                continue
            if result.returncode != 0:
                raise RuntimeError(
                    f"gh codespace ssh --config failed "
                    f"(rc={result.returncode}): {result.stderr.strip()}"
                )
            return result.stdout
        raise last_error  # type: ignore[misc]

    @staticmethod
    def _parse_ssh_config(raw: str) -> dict:
        result: dict = {"extra_options": {}}
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            host_match = re.match(r"^Host\s+(.+)$", line, re.IGNORECASE)
            if host_match:
                result["host_alias"] = host_match.group(1).strip()
                continue
            kv = re.match(r"^(\w+)\s+(.+)$", line) or re.match(r"^(\w+)=(.+)$", line)
            if not kv:
                continue
            key, value = kv.group(1), kv.group(2).strip()
            kl = key.lower()
            if kl == "user":
                result["user"] = value
            elif kl == "proxycommand":
                result["proxy_command"] = value
            elif kl == "identityfile":
                result["identity_file"] = value
            elif kl in ("controlmaster", "controlpath", "controlpersist"):
                pass  # the manager / forward own multiplexing
            else:
                result["extra_options"][key] = value
        if "host_alias" not in result:
            raise RuntimeError(
                f"Could not parse Host from gh codespace ssh --config:\n{raw}"
            )
        return result

    def _write_config_file(self, raw_config: str) -> Path:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\-.]", "_", self._codespace_name)
        path = self._config_dir / f"{safe}.config"
        path.write_text(raw_config)
        return path
