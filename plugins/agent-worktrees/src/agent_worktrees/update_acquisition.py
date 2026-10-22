"""Bounded payload acquisition recovery, never an installer retry scheduler."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from time import monotonic, sleep
from typing import TYPE_CHECKING

from . import output, update_events, update_auth_context as _auth

if TYPE_CHECKING:
    from .activation_preservation import ActivationCustody


_REFUSAL = re.compile(
    r"\b(?:401|403|EACCES|EPERM|EINTEGRITY|unauthori[sz]ed|authentication|"
    r"policy|checksum|signature|certificate|hash)\b|permission denied|access is denied|"
    r"\b(?:CERT|ERR_TLS|ERR_SSL)_",
    re.IGNORECASE,
)
_TRANSIENT = re.compile(
    r"\b(?:ECONNRESET|ECONNREFUSED|EAI_AGAIN|ETIMEDOUT)\b|"
    r"\bHTTP(?:/\d(?:\.\d)?)?\s+(?:502|503|504)\b",
    re.IGNORECASE,
)
_TOTAL_BUDGET_S = 240.0
_ATTEMPT_TIMEOUT_S = 120.0
_BACKOFF_S = 1.0
_WINDOWS = os.name == "nt"


class _QuiescentPayloadTimeout(subprocess.TimeoutExpired):
    """A payload update timed out after its Windows Job was confirmed empty."""


def _run_update(
    argv: list[str], *, cwd: Path | None, timeout: float,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if not _WINDOWS:
        environment = {"env": env} if env is not None else {}
        return subprocess.run(
            argv, cwd=cwd, timeout=timeout, capture_output=True, text=True, **environment,
        )
    from . import installer_run

    lines: list[str] = []
    result = installer_run.run_installer(
        argv, cwd=cwd, env=os.environ if env is None else env, deadline_s=timeout,
        log=lines.append, require_tree_exit=True,
    )
    text = "\n".join(lines)
    if result.rc is None and result.status.startswith("timed out"):
        raise _QuiescentPayloadTimeout(argv, timeout, output=text)
    return subprocess.CompletedProcess(
        argv, result.rc if result.rc is not None else 1, text,
        result.status if result.rc is None else "",
    )


def _run_install(
    argv: list[str], identity: str, *, cwd: Path | None, timeout: float,
    env: Mapping[str, str] | None = None, custody: ActivationCustody | None = None,
) -> subprocess.CompletedProcess[str]:
    from . import activation_preservation, installer_run

    environment = {"env": env} if env is not None else {}
    if not _WINDOWS:
        return activation_preservation.run_install_preserving_activation(
            argv, identity, runner=partial(_auth.run_acquisition, subprocess.run, context=cwd),
            capture_output=True, text=True, timeout=timeout, cwd=cwd, **environment,
        )
    started = monotonic()
    guard = (
        custody.preserve(identity) if custody is not None
        else activation_preservation.preserve_activation_durably(identity)
    )
    with guard as preservation:
        remaining = timeout - (monotonic() - started)
        if remaining <= 0:
            preservation.confirm_not_launched()
            raise installer_run.InstallerLaunchError(
                "payload command budget exhausted during activation recovery; no child launched"
            )
        try:
            result = _auth.run_acquisition(
                _run_update, argv, context=cwd, cwd=cwd, timeout=remaining, **environment,
            )
        except _QuiescentPayloadTimeout:
            preservation.confirm_quiescent()
            raise
        except (installer_run.InstallerLaunchError, OSError):
            preservation.confirm_not_launched()
            raise
        preservation.confirm_quiescent()
        return result


def _refusal_reason(detail: str) -> str | None:
    refusal = _REFUSAL.search(detail)
    return refusal.group(0) if refusal else _numeric_refusal(detail)


def _numeric_refusal(detail: str) -> str | None:
    low = detail.lower()
    if update_events._marker(low, update_events._ERRNO, 5) or update_events._os_error(
        low, posix=(5,),
    ):
        return "I/O error"
    if (
        update_events._marker(low, update_events._WINERROR, 5)
        or any(update_events._marker(low, update_events._ERRNO, code) for code in (1, 13))
        or update_events._os_error(low, windows=(5,), posix=(1, 13))
    ):
        return "permission denied"
    return None


def _contention_marker(detail: str) -> str | None:
    low = detail.lower()
    for code in (33, 32):
        if (
            update_events._marker(low, update_events._WINERROR, code)
            or update_events._os_error(low, windows=(code,))
        ):
            return f"WinError {code}"
    return None


def acquire(
    name: str, marketplace: str, copilot: str, *,
    cwd: Path | None = None, engine: bool = False,
) -> str:
    """Recover original activation before selecting or running a Windows acquisition."""
    from . import activation_preservation

    started = monotonic()
    if not _WINDOWS:
        return _acquire(name, marketplace, copilot, cwd=cwd, engine=engine, started=started)
    try:
        with activation_preservation.activation_custody() as custody:
            return _acquire(
                name, marketplace, copilot, cwd=cwd, engine=engine,
                started=started, custody=custody,
            )
    except (activation_preservation.PluginStateError, OSError) as error:
        status = f"plugin state error: {update_events.bounded(str(error), 200)}"
        output.warn(f"{name}: {status}")
        return status


def _acquire(
    name: str, marketplace: str, copilot: str, *,
    cwd: Path | None, engine: bool, started: float,
    custody: ActivationCustody | None = None,
) -> str:
    """Attempt at most twice within one target budget, re-inspecting each time.

    A completed command's explicit transport/contention failure may retry after
    backoff. Missing Copilot ledger entries may use activation-preserving
    install. Unknown failures and authority/integrity refusals stop. A timeout
    may retry only after a Windows update's owned Job is confirmed empty.
    Install and POSIX timeouts cannot license another writer.
    """
    from . import activation_preservation, installer_run, reconcile
    from .update_runtime import describe_copilot_spawn_error

    if _auth.blocked(cwd, marketplace):
        return "auth_required"
    ref = f"{name}@{marketplace}"
    missing_ledger_pattern = re.compile(
        rf"\bplugin\s+['\"`]?{re.escape(name)}(?:@{re.escape(marketplace)})?"
        r"['\"`]?\s+(?:is\s+)?not installed\b",
        re.IGNORECASE,
    )
    repair_ledger = False
    status = "payload acquisition budget exhausted"
    for attempt in range(2):
        remaining = _TOTAL_BUDGET_S - (monotonic() - started)
        if remaining <= 0:
            return status
        installed = reconcile.core_installed_payload_dir(name) is not None
        verb = "update" if engine or (installed and not repair_ledger) else "install"
        if _WINDOWS:
            remaining -= installer_run._REAP_GRACE_S
            if verb == "install":
                remaining -= activation_preservation.SETTLEMENT_RETRY_RESERVE_S
            if remaining <= 0:
                return status
        argv = [copilot, "plugin", verb, ref]
        timeout = min(_ATTEMPT_TIMEOUT_S, remaining)
        environment = _auth.child_environment(cwd)
        try:
            result = (
                _run_install(argv, ref, cwd=cwd, timeout=timeout, custody=custody, **environment)
                if verb == "install" else _auth.run_acquisition(
                    _run_update, argv, context=cwd, cwd=cwd, timeout=timeout, **environment,
                )
            )
        except activation_preservation.PluginStateError as error:
            status = f"plugin state error: {update_events.bounded(str(error), 200)}"
            output.warn(f"{name}: {status}")
            return status
        except installer_run.InstallerCleanupError as error:
            status = f"payload cleanup failed: {update_events.bounded(str(error), 200)}"
            output.warn(f"{name}: {status}")
            return status
        except installer_run.InstallerLaunchError as error:
            status = f"payload launch failed: {update_events.bounded(str(error), 200)}"
            output.warn(f"{name}: {status}")
            return status
        except subprocess.TimeoutExpired as error:
            refusal = (
                _refusal_reason(error.output or "")
                if isinstance(error, _QuiescentPayloadTimeout) else None
            )
            if refusal:
                status = f"plugin {verb} timed out after refusal ({refusal})"
                output.warn(f"{name}: {status}")
                return status
            if verb == "install" and isinstance(error, _QuiescentPayloadTimeout):
                output.warn(
                    f"Plugin install for {name} timed out after confirmed cleanup and "
                    "activation restoration; automatic install replay remains disabled"
                )
                return "timed out"
            if verb != "update" or not isinstance(error, _QuiescentPayloadTimeout):
                output.warn(
                    f"Plugin {verb} for {name} timed out; automatic retry is blocked "
                    "because payload subprocess descendant cleanup is not proven"
                )
                return "timed out"
            status, retryable, missing_ledger = "timed out", True, False
        except OSError as error:
            if verb == "install":
                status = (
                    "plugin install or activation restoration failed: "
                    f"{update_events.bounded(str(error), 200)}"
                )
                output.warn(f"{name}: {status}")
                return status
            detail = describe_copilot_spawn_error(error, cwd=cwd)
            status = update_events.bounded(detail, 200)
            retryable = getattr(error, "winerror", None) in (32, 33)
            missing_ledger = False
        else:
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    output.ok(update_events.redact(line))
                return "OK (installed)" if verb == "install" else "OK"
            detail = "\n".join(
                value for value in (result.stdout.strip(), result.stderr.strip()) if value
            )
            refusal_reason = _refusal_reason(detail)
            refused = refusal_reason is not None
            contention = _contention_marker(detail)
            failure = f"failed ({contention}, exit {result.returncode})" if contention else (
                f"failed (exit {result.returncode})"
            )
            reason = f"refused ({refusal_reason}, exit {result.returncode})" if refused else (
                failure
            )
            status = f"plugin {verb} {reason}: {update_events.bounded(detail, 200, tail=True)}"
            if verb == "install" and result.returncode == installer_run._WATCHDOG_EXIT:
                output.warn(f"{name}: {status}; automatic install-timeout replay remains disabled")
                return status
            missing_ledger = (
                not engine and verb == "update" and not refused
                and bool(missing_ledger_pattern.search(detail))
            )
            retryable = not refused and (
                bool(_TRANSIENT.search(detail)) or contention is not None
            )

        if attempt == 1 or not (retryable or missing_ledger):
            output.warn(f"{name}: {status}")
            return status
        # Retain the failed attempt as advisory history. The outer acquisition
        # owner records the final required outcome, including a recovered one.
        update_events.record("payload_refresh", ref, status, required=False, changed=False)
        repair_ledger = missing_ledger
        if missing_ledger:
            output.info(f"{name}: missing plugin ledger entry; retrying activation-preserving install")
        else:
            output.warn(f"{name}: {status}; retrying after {_BACKOFF_S:g}s")
            sleep(_BACKOFF_S)
    return status
