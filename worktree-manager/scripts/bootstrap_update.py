"""Keep fresh Manager construction and its suite update in one driver request."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import uuid


def _request_control(options, forwarded) -> bool:
    if options.request_status == "":
        raise ValueError("Request status requires a nonempty ID")
    return any((
        options.request_status is not None, options.recovery_status, options.recover_after_restart,
        options.recover_for_repair, "--auth-report" in forwarded,
    ))


def execute(args, driver, *, source_digest: str) -> int:
    from runtime_install import _bootstrap_hold_path, _source_digest, engine
    from worktree_manager.update_python_path import finish_update
    from worktree_manager.update_driver_cli import (
        _CONTEXT_KEYS, _current_authority, _report, _source_key, _update_context_directory, parse_options,
        request_context, request_source_anchor,
    )
    from worktree_manager.engine_acquisition_budget import TIMEOUT_ENV, acquisition_timeout

    observer_command = args.command[1:] if args.command[:1] == ["--"] else args.command
    options, forwarded = parse_options(observer_command[1:])
    if _request_control(options, forwarded):
        raise ValueError("Request inspection and recovery must run outside installation submission")
    budget = options.engine_acquisition_timeout
    if budget is None and options.target is None:
        budget = os.environ.get(TIMEOUT_ENV)
    if budget is not None:
        budget = str(acquisition_timeout({TIMEOUT_ENV: budget}))
    source_anchor = request_source_anchor()
    context = {**request_context(), "WORKTREE_MANAGER_ROOT": str(args.root.resolve())}
    _update_context_directory(context)
    try:
        selected = _current_authority(
            driver, context=context.get("COPILOT_EXTENSIONS_CONTEXT", ""), cwd=source_anchor,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Update driver: {error}", file=sys.stderr)
        return 1
    if selected.configured and selected.mode == "project":
        print("Update driver: bootstrap update is disabled by project authority", file=sys.stderr)
        return 1
    if selected.configured and "--no-project-controller" not in forwarded:
        forwarded = [*forwarded, "--no-project-controller"]
    command = ["update", *forwarded]
    if budget is not None:
        command.extend(["--engine-acquisition-timeout", budget])
    admitted_source = _source_key()
    hold_path = _bootstrap_hold_path()
    selected_payload = args.payload.resolve()
    from bootstrap_inputs import _preparation_assets, retain

    if args.install_source is None:
        materialized_assets = _preparation_assets(selected_payload)
        payload, shared = retain(
            selected_payload, driver.default_root(),
            expected_payload_digest=source_digest if materialized_assets else None,
        )
        selected_payload = payload
        if materialized_assets:
            source_digest = _source_digest(selected_payload)
    else:
        payload, shared = retain(
            Path(__file__).resolve().parents[1], driver.default_root(), include_sdk=False,
        )
    arguments = [
        sys.executable, "-I", "-S", "-B", "-X", "utf8",
        str(payload / "scripts" / "runtime_install.py"),
        "--root", str(args.root.resolve()), "--payload", str(selected_payload),
        "--update-participant", "--source-digest", source_digest,
    ]
    if not shared:
        # Reuse retained bytes without joining requests with unresolved later targets.
        arguments.extend(["--bootstrap-attempt", uuid.uuid4().hex])
    if args.repair_update:
        arguments.append("--repair-update")
    if args.install_source is not None:
        arguments.extend(["--install-source", str(args.install_source)])
    arguments.extend(["--", *command])
    spec = driver.RequestSpec(
        "manager", tuple(command[1:]),
        project=str(args.root.resolve()),
        cwd=source_anchor,
        source=admitted_source, context=tuple(context.items()),
        repair=not options.auto,
        killswitch_path=hold_path,
        authority="standalone" if selected.configured else "",
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
    try:
        if options.supervised_debug:
            driver.drain()
        else:
            environment = {
                **os.environ, "GIT_TERMINAL_PROMPT": "0",
                "GH_PROMPT_DISABLED": "1", "GCM_INTERACTIVE": "never",
            }
            packages = {
                engine._package_name(package): package for package in engine._package_roots(payload)
            }
            worker = driver.ensure_worker(
                (
                    sys.executable, "-I", "-S", "-B", "-X", "utf8",
                    str(payload / "scripts" / "bootstrap_update.py"),
                ),
                cwd=payload, env=environment,
                python_bootstrap_root=packages["agent-procutil"] / "src",
            )
    except Exception as error:
        if options.json:
            print(json.dumps({
                "request_id": request.id, "log_path": str(request.log_path),
                "state": request.state, "exit_code": request.exit_code, "error": str(error),
            }))
        raise
    if options.detach:
        _report(request, as_json=options.json)
        return 0
    result = driver.wait(request.id, output=sys.stderr, worker=worker)
    if result.state != "completed" or result.exit_code is None:
        raise RuntimeError(f"Bootstrap update did not complete: {result.state}")
    return finish_update(observer_command, result.exit_code)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import runtime_install

    payload = Path(__file__).resolve().parents[1]
    driver = runtime_install._execution_driver(payload)
    if sys.argv[1:]:
        raise ValueError("Unsupported bootstrap worker arguments")
    raise SystemExit(driver.drain())
