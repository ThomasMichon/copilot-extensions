"""Selected CLI-store acquisition and authenticated reads, never ambient retry."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError, OutputLimitExceeded, run_caller_owned

from .github_credentials import CredentialObservationError
from .repos_configuration_sources import identity


class SelectedGitHubAccount:
    """Keep one verified credential inside the original finite observation.

    The caller retains cleanup custody on OwnedCleanupError. No credential,
    identity response or child diagnostic is included in this object's repr.
    """

    def __init__(self, host, login, repository, *, budget, checkpoint):
        if host != "github.com" or identity(login) != login or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository,
        ):
            raise CredentialObservationError("selection", "unsupported-target")
        self.login, self.repository = login, repository
        self.budget: OwnedExecutionBudget = budget
        self.checkpoint = checkpoint
        self.executable = shutil.which("gh")
        if self.executable is None:
            raise CredentialObservationError("credential", "missing-command")
        self._environment = {
            key: value for key, value in os.environ.items()
            if key.upper() not in {
                "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
                "GH_HOST", "GH_REPO", "GH_DEBUG", "GH_FORCE_TTY", "GH_HTTP_UNIX_SOCKET",
            }
        }
        self._environment["GH_PROMPT_DISABLED"] = "1"
        self._environment["GH_PAGER"] = "cat"
        self._verified = False

    def capture(
        self, stage, arguments, *, binary=False, limit=256 * 1024,
        allow_graphql_errors=False, input_text=None,
    ):
        """Bound each child by the same deadline and live caller checkpoint."""
        self.checkpoint()
        timeout = self.budget.execution_deadline - time.monotonic()
        if timeout <= 0:
            raise CredentialObservationError(stage, "timeout")
        try:
            result = run_caller_owned(
                [self.executable, *arguments], timeout=timeout, budget=self.budget,
                env=dict(self._environment), binary=binary,
                max_stdout_bytes=limit, max_stderr_bytes=64 * 1024,
                **({"input_text": input_text} if input_text is not None else {}),
            )
        except OwnedCleanupError:
            raise
        except subprocess.TimeoutExpired:
            raise CredentialObservationError(stage, "timeout") from None
        except OutputLimitExceeded:
            raise CredentialObservationError(stage, "output-limit") from None
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            raise CredentialObservationError(stage, "capture-failed") from None
        self.checkpoint()
        if result.returncode:
            output = result.stdout.decode("ascii", errors="ignore") if binary else result.stdout
            status = re.match(r"HTTP/\S+ ([1-5][0-9]{2})(?:\s|$)", output)
            if (
                allow_graphql_errors and not binary and arguments[:2] == ["api", "graphql"]
                and status is not None and status[1] == "200"
            ):
                try:
                    value = json.loads(re.split(r"\r?\n\r?\n", output, maxsplit=1)[1])
                    if isinstance(value, dict) and isinstance(value.get("errors"), list) and value["errors"]:
                        return output
                except (ValueError, TypeError, IndexError, RecursionError):
                    pass
            raise CredentialObservationError(
                stage, "credential-unavailable" if stage == "credential" else "access-unconfirmed",
                exit_code=result.returncode,
                http_status=int(status[1]) if status is not None and stage != "credential" else None,
            )
        return result.stdout

    def json(self, stage, path):
        """Read a pinned identity/metadata route without returning its raw bytes."""
        raw = self.capture(
            stage, ["api", "--hostname", "github.com", "--include", "https://api.github.com" + path],
        )
        try:
            header, body = re.split(r"\r?\n\r?\n", raw, maxsplit=1)
            status = re.match(r"HTTP/\S+ ([0-9]{3})(?:\s|$)", header)
            value = json.loads(body)
            if status is None or status[1] != "200" or not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, TypeError, RecursionError):
            raise CredentialObservationError(stage, "invalid-response") from None

    def verify(self):
        """Acquire the explicitly selected store entry once, even if active."""
        if self._verified:
            self.checkpoint()
            return
        token = self.capture(
            "credential", ["auth", "token", "--hostname", "github.com", "--user", self.login],
        ).strip()
        if (
            not token or len(token) > 16384 or not token.isascii()
            or any(character.isspace() or not character.isprintable() for character in token)
        ):
            raise CredentialObservationError("credential", "invalid-response")
        self._environment["GH_TOKEN"] = token
        observed_login = self.json("identity", "/user").get("login")
        try:
            valid_login = identity(observed_login) == observed_login
        except ValueError:
            valid_login = False
        if not valid_login:
            raise CredentialObservationError("identity", "invalid-response")
        if observed_login.casefold() != self.login.casefold():
            raise CredentialObservationError("identity", "account-mismatch")
        self._verified = True
