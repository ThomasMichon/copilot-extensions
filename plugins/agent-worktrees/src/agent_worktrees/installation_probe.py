"""Bounded, strict diagnostic transport to an owning payload's native helper."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from agent_procutil import no_window_kwargs


def strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object while rejecting duplicate or case-conflicting keys."""
    result: dict[str, Any] = {}
    normalized: set[str] = set()
    for key, value in pairs:
        folded = key.casefold()
        if key in result or folded in normalized:
            raise ValueError(f"duplicate or case-conflicting JSON key: {key}")
        result[key] = value
        normalized.add(folded)
    return result


def run_installation_probe(
    command: list[str], environment: dict[str, str], plugin: str, *, action: str,
) -> dict[str, Any]:
    try:
        process = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            check=False, env=environment, timeout=15, **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError(
            f"installation-mode resolution timed out for {plugin}"
        ) from error
    except OSError as error:
        raise ValueError(
            f"installation-mode resolver could not run for {plugin}: {error}"
        ) from error
    if process.returncode not in {0, 3}:
        detail = " ".join(
            (process.stderr.strip() or process.stdout.strip()).split()
        )[:240]
        suffix = f": {detail}" if detail else ""
        raise ValueError(
            f"installation-mode resolver failed for {plugin} "
            f"(exit {process.returncode}){suffix}"
        )
    try:
        result = json.loads(process.stdout, object_pairs_hook=strict_json_object)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError(
            f"installation-mode resolver returned invalid JSON for {plugin}"
        ) from error
    if not isinstance(result, dict):
        raise ValueError(
            f"installation-mode resolver returned a non-object for {plugin}"
        )
    if action == "probe-plugin-publication":
        deployment = result.get("deployment")
        allowed = deployment.get("allowPluginPublication") if isinstance(deployment, dict) else None
    else:
        allowed = result.get("allowMutation") if action == "probe-legacy" else None
    if (
        (process.returncode == 0 and allowed is not True)
        or (process.returncode == 3 and allowed is not False)
    ):
        raise ValueError(
            f"installation-mode resolver returned inconsistent mutation status for {plugin}"
        )
    return result
