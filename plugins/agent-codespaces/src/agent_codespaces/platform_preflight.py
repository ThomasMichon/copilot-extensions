"""Preflight: ensure the CodeSpace's Copilot CLI has its platform package (#111).

A freshly-created odsp-web CodeSpace ships the ``@github/copilot`` npm-loader
stub but is missing its platform optional-dependency (e.g.
``@github/copilot-linux-x64``) -- the devcontainer's global npm registry
defaults to the ODSP **private feed**, so the optional dep was skipped/401'd at
image build. Without the platform binary, ``copilot --acp`` cannot launch and an
agent-bridge dispatch dies at ``stage 7/LAUNCH_ACP`` with a bare "Connection
closed", masking the real cause (``copilot --version`` -> "no platform package
found").

This module verifies ``copilot --version`` up front and, if the platform
package is missing, repairs it by reinstalling from **public** npm (bypassing
the private-feed 401) before the ACP launch. It is degrade-safe: any probe error
leaves the launch to proceed and surface its own error, no worse than today.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger("agent-codespaces.platform-preflight")

# ``copilot --version`` prints this (to stderr) when the loader stub is present
# but the platform optional-dependency was never fetched.
PLATFORM_MISSING_MARKER = "no platform package found"

# Verify copilot can load its platform package. A login shell puts the
# nvm-managed ``copilot`` on PATH; ``2>&1`` folds the diagnostic (which prints to
# stderr) into stdout so a single stream carries the marker.
VERIFY_COMMAND = "bash -l -c 'copilot --version 2>&1'"

# Repair by fetching the platform binary from PUBLIC npm. A naive
# ``npm install -g @github/copilot`` hits the CodeSpace's private-feed default
# and 401s; ``--registry`` overrides the default just for this install.
REPAIR_COMMAND = (
    "bash -l -c 'npm install -g @github/copilot "
    "--registry=https://registry.npmjs.org 2>&1'"
)

# The marker is usually a NOT-READY / timing state, not a permanently-broken
# install: a CodeSpace's copilot ultimately always finishes installing, so a
# "no platform package found" seen at ACP-launch time is most often post-create
# provisioning that hasn't converged yet (or a wrong cwd/user/auth issue that a
# global reinstall would NOT fix and could mask). So we WAIT-and-retry first and
# only fall back to the public-npm reinstall as a genuine last resort -- the
# reinstall exists for the real #111 case (the platform optional-dep was never
# fetched at image build because the CodeSpace's private-feed npm default 401'd
# it), which waiting alone never heals.
_VERIFY_RETRIES = 3
_VERIFY_RETRY_DELAY_S = 8.0

# ``run_remote(cmd)`` runs a shell command on the CodeSpace, returning
# ``(exit_code, combined_output)``.
RunRemote = Callable[[str], Awaitable[tuple[int, str]]]


def needs_platform_repair(version_output: str) -> bool:
    """Whether ``copilot --version`` output signals the platform package is missing."""
    return PLATFORM_MISSING_MARKER in (version_output or "").lower()


async def ensure_copilot_platform(
    run_remote: RunRemote,
    *,
    retries: int = _VERIFY_RETRIES,
    retry_delay: float = _VERIFY_RETRY_DELAY_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[bool, str]:
    """Ensure ``copilot`` can load its platform package before an ACP launch (#111).

    Polls ``copilot --version`` up to ``retries`` times (waiting ``retry_delay``
    seconds between attempts) because the platform binary "ultimately always
    gets installed" -- a transient ``no platform package found`` during
    post-create convergence heals on its own, so waiting is preferred over a
    drastic global reinstall. Only if the marker persists after the wait do we
    fall back to reinstalling ``@github/copilot`` from public npm (the genuine
    private-feed-401 case that waiting cannot heal).

    Returns ``(ok, detail)`` where ``ok`` is whether copilot is (now)
    launchable and ``detail`` is a short status tag. Never raises: a probe that
    cannot run returns ``(True, ...)`` so the preflight never blocks a launch it
    could not diagnose.
    """
    attempts = max(1, retries)
    last_out = ""
    for attempt in range(attempts):
        try:
            rc, out = await run_remote(VERIFY_COMMAND)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("copilot platform verify probe failed to run: %s", exc)
            return True, "verify-skipped"
        last_out = out
        if not needs_platform_repair(out):
            # Either healthy (rc==0) or a non-zero for some *other* reason
            # (copilot absent entirely, wrong cwd/user, etc.) -- in the latter
            # case the npm repair wouldn't help, so let the launch surface the
            # real error rather than masking it.
            return True, ("already-present" if rc == 0 else "no-repair-signal")
        if attempt < attempts - 1:
            log.info(
                "copilot platform package not ready on attempt %d/%d "
                "(post-create may still be converging); waiting %.0fs",
                attempt + 1, attempts, retry_delay,
            )
            await sleep(retry_delay)

    # Marker persisted through the wait -> treat as the genuine #111 case.
    log.warning(
        "CodeSpace copilot still missing its platform package after %d checks "
        "(#111); reinstalling @github/copilot from public npm before ACP launch",
        attempts,
    )
    try:
        r_rc, r_out = await run_remote(REPAIR_COMMAND)
    except Exception as exc:  # pragma: no cover - defensive
        log.error("copilot platform repair failed to run: %s", exc)
        return False, "repair-error"
    if r_rc != 0:
        log.error(
            "copilot platform repair (npm install) failed (rc=%s): %s",
            r_rc, r_out.strip(),
        )
        return False, "repair-failed"

    try:
        v_rc, v_out = await run_remote(VERIFY_COMMAND)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("post-repair verify probe failed to run: %s", exc)
        return True, "repaired-unverified"
    ok = v_rc == 0 and not needs_platform_repair(v_out)
    if ok:
        log.info("copilot platform package restored (#111): %s", v_out.strip())
    else:
        log.error(
            "copilot still cannot load its platform package after repair: %s",
            v_out.strip(),
        )
    return ok, ("repaired" if ok else "repair-verify-failed")
