"""Keep fresh Manager construction and its suite update in one driver request."""

from __future__ import annotations

import os
from pathlib import Path
import sys


def execute(args, driver, *, source_digest: str) -> int:
    from worktree_manager.update_driver_cli import (
        _CONTEXT_KEYS, _report, _source_key, parse_options, request_context,
    )
    from worktree_manager.engine_acquisition_budget import TIMEOUT_ENV, acquisition_timeout

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    options, forwarded = parse_options(command[1:])
    budget = options.engine_acquisition_timeout
    inspection = any((
        options.request_status, options.recovery_status, options.recover_after_restart,
        options.recover_for_repair, "--auth-report" in forwarded,
    ))
    if budget is None and options.target is None and not inspection:
        budget = os.environ.get(TIMEOUT_ENV)
    if budget is not None:
        selected_budget = str(acquisition_timeout({TIMEOUT_ENV: budget}))
        if options.engine_acquisition_timeout is None:
            command = [*command, "--engine-acquisition-timeout", selected_budget]
    selected_payload = args.payload.resolve()
    from bootstrap_inputs import retain

    if args.install_source is None:
        payload, _shared = retain(selected_payload, driver.default_root())
        selected_payload = payload
    else:
        payload, _shared = retain(
            Path(__file__).resolve().parents[1], driver.default_root(), include_sdk=False,
        )
    arguments = [
        sys.executable, "-I", "-S", "-B", "-X", "utf8",
        str(payload / "scripts" / "runtime_install.py"),
        "--root", str(args.root.resolve()), "--payload", str(selected_payload),
        "--update-participant", "--source-digest", source_digest,
    ]
    if args.repair_update:
        arguments.append("--repair-update")
    if args.install_source is not None:
        arguments.extend(["--install-source", str(args.install_source)])
    arguments.extend(["--", *command])
    spec = driver.RequestSpec(
        "manager", tuple(command[1:]),
        project=str(args.root.resolve()), cwd=str(payload),
        source=_source_key(),
        context=tuple({
            **request_context(), "WORKTREE_MANAGER_ROOT": str(args.root.resolve()),
        }.items()),
        repair=not options.auto,
    )
    runner = driver.TrustedRunner(
        tuple(arguments), str(payload), context_keys=_CONTEXT_KEYS,
    )
    # Frozen control code does not pin the suite's later marketplace targets.
    try:
        request = driver.enqueue(spec, runner=runner, coalesce_running=False)
    except driver.InterruptedError:
        if options.auto:
            raise
        from update_driver.recovery import recover_for_repair

        released = recover_for_repair(authorized=True, root=driver.default_root())
        print(
            f"Manual update authorized repair of {len(released)} prior queue fence(s); "
            "their failed outcomes remain recorded; cleanup is not certified.",
            file=sys.stderr,
        )
        request = driver.enqueue(spec, runner=runner, coalesce_running=False)
    print(f"Update request {request.id}; log: {request.log_path}", file=sys.stderr)
    worker = None
    if options.supervised_debug:
        driver.drain()
    else:
        worker = driver.ensure_worker(
            (
                sys.executable, "-I", "-S", "-B", "-X", "utf8",
                str(payload / "scripts" / "bootstrap_update.py"),
            ),
            cwd=payload, env={
                **os.environ, "GIT_TERMINAL_PROMPT": "0",
                "GH_PROMPT_DISABLED": "1", "GCM_INTERACTIVE": "never",
            },
        )
    if options.detach:
        _report(request, as_json=options.json)
        return 0
    result = driver.wait(request.id, output=sys.stderr, worker=worker)
    if result.state != "completed" or result.exit_code is None:
        raise RuntimeError(f"Bootstrap update did not complete: {result.state}")
    return result.exit_code


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import runtime_install

    raise SystemExit(runtime_install._execution_driver(Path(__file__).resolve().parents[1]).drain())
