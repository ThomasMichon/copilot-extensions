"""Observe the ordinary Bridge caller without copying its invocation or envelope."""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from .procutil import no_window_kwargs
from .spawn_diagnostics import SpawnDiagnostics, diagnostic_fields, diagnostic_text


def run(
    prefix: list[str], argv: list[str], *, diagnostics: SpawnDiagnostics,
    runner: Callable[..., subprocess.CompletedProcess[str]], timeout: float,
    prompt_input: str | None = None, read_only: bool = False,
) -> subprocess.CompletedProcess[str]:
    from .launch_debug import begin

    operation = argv[1]
    if "--receipt-only" in argv:
        operation = "receipt"
    with diagnostics.stage(f"bridge_{operation}") as outcome:
        trace = None if read_only else begin(
            prefix, argv, prompt_input=prompt_input,
            input_reference=diagnostics.reservation_key, timeout=timeout,
        )
        try:
            result = runner(
                [*prefix, *argv], input=prompt_input, capture_output=True,
                text=True, encoding="utf-8", check=False, timeout=timeout,
                **no_window_kwargs(),
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            if trace:
                trace.finish(error=exc)
            if isinstance(exc, subprocess.TimeoutExpired) and exc.stderr:
                text = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
                # The stage context emits the error; this record adds captured causal text.
                diagnostics.event(f"bridge_{operation}", "timeout_diagnostics",
                                  error=diagnostic_text(text, diagnostics.secrets))
            raise
        if trace:
            trace.finish(result=result)
        outcome["returncode"] = result.returncode
        if result.returncode:
            fields = diagnostic_fields(result.stdout, diagnostics.secrets)
            if fields:
                outcome["diagnostics"] = fields
            if result.stderr:
                outcome["stderr"] = diagnostic_text(result.stderr, diagnostics.secrets)
        return result
