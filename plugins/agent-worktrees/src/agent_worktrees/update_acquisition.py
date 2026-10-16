"""Bounded payload acquisition recovery, never an installer retry scheduler."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from time import monotonic, sleep

from . import output, update_events


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


def _numeric_refusal(detail: str) -> str | None:
    low = detail.lower()
    if update_events._marker(low, update_events._ERRNO, 5) or update_events._os_error(
        low, posix=(5,),
    ):
        return "I/O failure"
    if (
        update_events._marker(low, update_events._WINERROR, 5)
        or any(update_events._marker(low, update_events._ERRNO, code) for code in (1, 13))
        or update_events._os_error(low, windows=(5,), posix=(1, 13))
    ):
        return "permission denied"
    return None


def _numeric_contention(detail: str) -> bool:
    low = detail.lower()
    return (
        any(update_events._marker(low, update_events._WINERROR, code) for code in (32, 33))
        or update_events._os_error(low, windows=(32, 33))
    )


def acquire(
    name: str, marketplace: str, copilot: str, *,
    cwd: Path | None = None, engine: bool = False,
) -> str:
    """Attempt at most twice within one target budget, re-inspecting each time.

    A completed command's explicit transport/contention failure may retry after
    backoff. Missing Copilot ledger entries may use activation-preserving
    install. Unknown failures and authority/integrity refusals stop. A timeout
    cannot retry: subprocess.run does not prove descendants have been reaped.
    """
    from . import activation_preservation, reconcile
    from .update_runtime import describe_copilot_spawn_error

    ref = f"{name}@{marketplace}"
    missing_ledger_pattern = re.compile(
        rf"\bplugin\s+['\"`]?{re.escape(name)}(?:@{re.escape(marketplace)})?"
        r"['\"`]?\s+(?:is\s+)?not installed\b",
        re.IGNORECASE,
    )
    started = monotonic()
    repair_ledger = False
    status = "payload acquisition budget exhausted"
    for attempt in range(2):
        remaining = _TOTAL_BUDGET_S - (monotonic() - started)
        if remaining <= 0:
            return status
        installed = reconcile.core_installed_payload_dir(name) is not None
        verb = "update" if engine or (installed and not repair_ledger) else "install"
        argv = [copilot, "plugin", verb, ref]
        kwargs = {
            "capture_output": True, "text": True,
            "timeout": min(_ATTEMPT_TIMEOUT_S, remaining), "cwd": cwd,
        }
        try:
            result = (
                activation_preservation.run_install_preserving_activation(argv, ref, **kwargs)
                if verb == "install" else subprocess.run(argv, **kwargs)
            )
        except activation_preservation.PluginStateError as error:
            status = f"plugin state error: {update_events.bounded(str(error), 200)}"
            output.warn(f"{name}: {status}")
            return status
        except subprocess.TimeoutExpired:
            output.warn(
                f"Plugin {verb} for {name} timed out; automatic retry is blocked "
                "because payload subprocess descendant cleanup is not proven"
            )
            return "timed out"
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
            refusal = _REFUSAL.search(detail)
            refusal_reason = refusal.group(0) if refusal else _numeric_refusal(detail)
            refused = refusal_reason is not None
            reason = f"refused ({refusal_reason}, exit {result.returncode})" if refused else (
                f"failed (exit {result.returncode})"
            )
            status = f"plugin {verb} {reason}: {update_events.bounded(detail, 200, tail=True)}"
            missing_ledger = (
                not engine and verb == "update" and not refused
                and bool(missing_ledger_pattern.search(detail))
            )
            retryable = not refused and (
                bool(_TRANSIENT.search(detail)) or _numeric_contention(detail)
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
