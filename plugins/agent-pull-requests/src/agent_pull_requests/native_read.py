"""Optional finite native exporter, distinct from a protected-owner callback."""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
import subprocess
import threading
import time
from types import MappingProxyType

from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned

from .owner_read import (
    OwnerReadBudgetExceeded, OwnerReadUnavailable, PRINCIPAL_SEMANTICS,
    _label, _seconds, parse_request,
)
from .review_workspace import MAX_BYTES, _scope, build_generation
from .watch_provider import command_path
from .watch_replay_store import json_object

PROFILE = "configured_native_provider"
_CAPABILITIES = {
    "finite_acquisition", "renewable_observe_credentials",
    "principal_binding", "credential_budget",
    "enclosing_scope_credentials",
}


def command_prefix(value, *, require_available=True):
    if (
        not isinstance(value, tuple) or not 1 <= len(value) <= 64
        or any(not isinstance(item, str) or not item or "\0" in item for item in value)
        or sum(len(item.encode("utf-8")) for item in value) > 16384
    ):
        raise ValueError("native read requires an immutable bounded command prefix")
    if type(require_available) is not bool or not Path(value[0]).is_absolute():
        raise ValueError("native read requires an absolute command prefix")
    if require_available:
        command_path(value[0])
    if Path(value[0]).suffix.lower() in (".cmd", ".bat", ".ps1"):
        raise ValueError("native read requires an explicit executable/interpreter")
    return value


class NativeReadHandler:
    """Trusted configuration selects one target and its existing credential owner.

    The enclosing Gateway retains its request thread through finite execution
    and actual cleanup. Withdrawal prevents later work, not an early drain
    receipt while the synchronous native primitive still owns a child.
    """

    profile = PROFILE

    def __init__(self, *, command, credential_command, repo, scope, binding_ref,
                 max_work_seconds) -> None:
        self.command = command_prefix(command)
        self.credential_command = command_prefix(credential_command)
        if not isinstance(repo, str) or not repo or repo.startswith("-") or any(
            ord(char) < 32 for char in repo
        ):
            raise ValueError("explicit native repository required")
        self.repo = repo
        selected = _scope({**scope, "number": 1})
        selected.pop("number")
        self.scope = MappingProxyType(selected)
        self.binding_ref = _label(binding_ref)
        self.max_work_seconds = _seconds(max_work_seconds)
        self._discovery_lock = threading.Lock()
        self._discovered = False

    @staticmethod
    def _run(argv, remaining, limit):
        timeout = remaining()
        try:
            return run_caller_owned(
                argv, timeout=timeout, max_stdout_bytes=limit, max_stderr_bytes=8192,
            )
        except OwnedCleanupError as error:
            # Keep the original request/drain thread until native custody settles.
            logging.getLogger(__name__).warning(
                "native review cleanup unconfirmed; original custody retained",
            )
            while True:
                try:
                    error.retry_cleanup(deadline=time.monotonic() + 2)
                    break
                except Exception:
                    time.sleep(1)
            raise OwnerReadUnavailable("native review acquisition cleanup was delayed") from None
        except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeError):
            raise OwnerReadUnavailable("selected native review exporter unavailable") from None

    def _discover(self, remaining):
        if not self._discovery_lock.acquire(timeout=remaining()):
            raise OwnerReadBudgetExceeded("native capability discovery budget exhausted")
        try:
            remaining()
            if self._discovered:
                return
            capabilities = self._run([*self.command, "capabilities"], remaining, 65536)
            remaining()
            try:
                contract = json_object(capabilities.stdout)
                if (
                    capabilities.returncode != 0
                    or contract.get("schema") != "agent-worktrees.review-evidence-capabilities"
                    or type(contract.get("version")) is not int or contract["version"] != 1
                    or contract.get("evidence_versions") != [1]
                    or any(type(version) is not int for version in contract["evidence_versions"])
                    or not isinstance(contract.get("providers"), list)
                    or "azure-devops" not in contract["providers"]
                    or not isinstance(contract.get("capabilities"), list)
                    or not _CAPABILITIES <= set(contract["capabilities"])
                    or contract.get("effects") != "none"
                    or contract.get("submission") is not False
                    or contract.get("source_attachment") is not False
                ):
                    raise ValueError
            except (TypeError, ValueError, UnicodeError, RecursionError):
                raise OwnerReadUnavailable("selected native review acquisition unsupported") from None
            self._discovered = True
        finally:
            self._discovery_lock.release()

    def read(self, request, *, request_checkpoint=None):
        parsed = parse_request(request)
        if parsed["binding_ref"] != self.binding_ref or (
            parsed.get("expected_principal", self.scope["principal_id"]) != self.scope["principal_id"]
        ):
            raise OwnerReadUnavailable("native read binding differs")
        deadline = time.monotonic() + min(parsed["budget_seconds"], self.max_work_seconds)

        def remaining():
            if request_checkpoint is not None:
                request_checkpoint()
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise OwnerReadBudgetExceeded("native read work budget exhausted")
            return seconds

        self._discover(remaining)
        result = self._run([
            *self.command, "read", "--repo", self.repo, "--number", str(parsed["number"]),
            "--endpoint", self.scope["endpoint"],
            "--expected-principal", self.scope["principal_id"],
            "--credential-command", json.dumps(self.credential_command),
            "--contained-worker",
            "--budget", str(remaining()),
        ], remaining, MAX_BYTES)
        remaining()
        try:
            evidence = json_object(result.stdout)
            acquisition = evidence.get("acquisition")
            if (
                result.returncode != 0
                or _scope(evidence.get("scope")) != {**self.scope, "number": parsed["number"]}
                or not isinstance(acquisition, dict)
                or acquisition.get("source") != PROFILE or acquisition.get("effects") != "none"
                or acquisition.get("principal_semantics") != PRINCIPAL_SEMANTICS
                or acquisition.get("principal_source") != "provider_connection_data"
                or acquisition.get("snapshot_atomic") is not False
            ):
                raise ValueError
            generation = build_generation(evidence)["generation"]
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise OwnerReadUnavailable("native review evidence unavailable or mismatched") from None
        remaining()
        return {
            "generation": generation,
            "acquisition": {
                key: copy.deepcopy(acquisition[key]) for key in (
                    "source", "effects", "observed_from", "observed_to", "duration_ms",
                    "principal_source", "principal_semantics", "credential_identity_checks",
                    "omissions", "snapshot_atomic",
                ) if key in acquisition
            },
        }
