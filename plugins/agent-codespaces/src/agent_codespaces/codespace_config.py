"""CodespaceSource -- SSH ConfigSource for GitHub Codespaces.

Wraps ``gh codespace ssh --config`` to produce SSH configuration that
the ssh-manager ConnectionManager can use. This is the CodeSpace-specific
implementation of the ConfigSource protocol -- it lives here in
agent-codespaces, not in ssh-manager.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

from ssh_manager import SSHConfig

from .config import RUNTIME_DIR

log = logging.getLogger("agent-codespaces")

SSH_CONFIG_DIR = RUNTIME_DIR / "ssh"


class CodespaceSource:
    """ConfigSource that wraps ``gh codespace ssh --config``.

    Calls the gh CLI to generate SSH config for a specific CodeSpace,
    then parses the output into an SSHConfig that ssh-manager can use.
    The generated config already includes ``ControlMaster auto`` and a
    ``ProxyCommand`` using ``gh cs ssh --stdio`` -- we just need to
    write it to a file and add ``ControlPath``/``ControlPersist``.
    """

    def __init__(self, codespace_name: str) -> None:
        self._codespace_name = codespace_name
        self._config: SSHConfig | None = None
        self._config_file: Path | None = None

    @property
    def codespace_name(self) -> str:
        return self._codespace_name

    def get_ssh_config(self) -> SSHConfig:
        if self._config is not None:
            return self._config
        return self.refresh()

    def refresh(self) -> SSHConfig:
        """Re-generate SSH config by calling ``gh codespace ssh --config``."""
        raw_config = self._fetch_gh_config()
        parsed = self._parse_ssh_config(raw_config)
        config_path = self._write_config_file(raw_config, parsed["host_alias"])

        self._config = SSHConfig(
            host_alias=parsed["host_alias"],
            user=parsed.get("user"),
            config_file=str(config_path),
            proxy_command=parsed.get("proxy_command"),
            extra_options=parsed.get("extra_options", {}),
        )
        self._config_file = config_path
        return self._config

    def _fetch_gh_config(self) -> str:
        """Call ``gh codespace ssh --config -c <name>`` and return output."""
        args = [
            "gh", "codespace", "ssh", "--config",
            "-c", self._codespace_name,
        ]

        log.debug("Fetching SSH config: %s", " ".join(args))

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=self._creation_flags(),
            )
        except FileNotFoundError:
            raise RuntimeError(
                "gh CLI not found. Install it: https://cli.github.com/"
            ) from None
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Timed out fetching SSH config for codespace {self._codespace_name}"
            ) from None

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise RuntimeError(
                f"gh codespace ssh --config failed (rc={result.returncode}): {stderr}"
            )

        return result.stdout

    def _parse_ssh_config(self, raw: str) -> dict:
        """Parse the SSH config output from gh into structured data.

        Expected format (from gh CLI source ``pkg/cmd/codespace/ssh.go``):

            Host cs.<codespace-name>.<escaped-repo-ref>
                User <ssh-user>
                ProxyCommand gh cs ssh -c <name> --stdio -- -i <key>
                UserKnownHostsFile=/dev/null
                StrictHostKeyChecking no
                LogLevel quiet
                ControlMaster auto
                IdentityFile <key-path>
        """
        result: dict = {"extra_options": {}}

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue

            # Host line
            host_match = re.match(r"^Host\s+(.+)$", line, re.IGNORECASE)
            if host_match:
                result["host_alias"] = host_match.group(1).strip()
                continue

            # Key-value options
            kv_match = re.match(r"^(\w+)\s+(.+)$", line)
            if not kv_match:
                # Handle Key=Value format
                kv_match = re.match(r"^(\w+)=(.+)$", line)

            if kv_match:
                key, value = kv_match.group(1), kv_match.group(2).strip()
                key_lower = key.lower()

                if key_lower == "user":
                    result["user"] = value
                elif key_lower == "proxycommand":
                    result["proxy_command"] = value
                elif key_lower == "identityfile":
                    result["identity_file"] = value
                elif key_lower in ("controlmaster", "controlpath", "controlpersist"):
                    # We manage these ourselves -- skip gh's defaults
                    pass
                else:
                    result["extra_options"][key] = value

        if "host_alias" not in result:
            raise RuntimeError(
                f"Could not parse Host from gh codespace ssh --config output:\n{raw}"
            )

        return result

    def _write_config_file(self, raw_config: str, host_alias: str) -> Path:
        """Write the SSH config to a file for use with ``ssh -F``."""
        SSH_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        # Use codespace name as filename (sanitized)
        safe_name = re.sub(r"[^\w\-.]", "_", self._codespace_name)
        config_path = SSH_CONFIG_DIR / f"{safe_name}.config"

        config_path.write_text(raw_config)
        log.debug("Wrote SSH config to %s", config_path)

        return config_path

    @staticmethod
    def _creation_flags() -> int:
        if sys.platform == "win32":
            return subprocess.CREATE_NO_WINDOW
        return 0
