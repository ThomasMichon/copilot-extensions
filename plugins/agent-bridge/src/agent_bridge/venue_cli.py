"""Venue/parity and zero-downtime deploy CLI for ``agent-bridge``."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import socket
import sys
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zdd.conditional import ConditionalCutover
    from .serving_reconcile import ServingRuntime

from .parity_harness import (
    CONTAINER_RECREATE_FAULT,
    FAILED_ACP_HANDSHAKE_FAULT,
    FRONTEND_RESTART_HOSTINDEX_LOSS,
    RELAY_INTERRUPTION,
)


def _core():
    from . import __main__ as core

    return core


def _cmd_parity(args: argparse.Namespace) -> None:
    from .client import BridgeClientError
    from .parity_harness import ParityFailure, run

    core = _core()
    if (args.ado_url or args.azure_scope) and not args.auth:
        message = "--ado-url/--azure-scope require --auth"
        if args.json:
            core._json_out({"ok": False, "target": args.target, "error": message})
        else:
            print(f"[FAIL] venue parity: {message}", file=sys.stderr)
        sys.exit(2)
    if args.fault == RELAY_INTERRUPTION and not args.auth:
        message = "--fault relay-interruption requires --auth"
        if args.json:
            core._json_out({"ok": False, "target": args.target, "error": message})
        else:
            print(f"[FAIL] venue parity: {message}", file=sys.stderr)
        sys.exit(2)
    if args.fault == CONTAINER_RECREATE_FAULT and not args.target.startswith("container:"):
        message = "--fault container-recreate requires a container: target"
        if args.json:
            core._json_out({"ok": False, "target": args.target, "error": message})
        else:
            print(f"[FAIL] venue parity: {message}", file=sys.stderr)
        sys.exit(2)
    client = core._get_client()
    fault_handler = (
        _fault_frontend_restart_hostindex_loss
        if args.fault == FRONTEND_RESTART_HOSTINDEX_LOSS
        else None
    )
    try:
        evidence = run(
            client,
            args.target,
            expected_workspace=args.expect_workspace,
            expected_capability=args.expect_capability,
            auth=args.auth,
            ado_url=args.ado_url,
            azure_scope=args.azure_scope,
            startup_timeout=args.startup_timeout,
            turn_timeout=args.turn_timeout,
            keep_session=args.keep_session,
            fault=args.fault,
            fault_handler=fault_handler,
        )
    except ParityFailure as exc:
        result = exc.evidence.to_dict() if exc.evidence is not None else {"ok": False, "target": args.target}
        result["ok"] = False
        result["error"] = str(exc)
        if args.json:
            core._json_out(result)
        else:
            print(f"[FAIL] venue parity: {exc}", file=sys.stderr)
        sys.exit(1)
    except BridgeClientError as exc:
        result = {"ok": False, "target": args.target, "error": exc.detail, "status": exc.status}
        if args.json:
            core._json_out(result)
        else:
            print(f"[FAIL] venue parity: HTTP {exc.status}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    result = evidence.to_dict()
    if args.json:
        core._json_out(result)
        return
    print(f"[OK] venue parity: {args.target}")
    summary_pid = result["resumed_child_pid"] if args.fault == CONTAINER_RECREATE_FAULT else result["initial_child_pid"]
    summary_acp = result["resumed_acp_session_id"] if args.fault == CONTAINER_RECREATE_FAULT else result["initial_acp_session_id"]
    print(f"  session={result['session_id']} pid={summary_pid} acp={summary_acp}")
    for name, ok in result["checks"].items():
        print(f"  {'PASS' if ok else 'FAIL'} {name}")


def _fault_frontend_restart_hostindex_loss(
    session_id: str,
    startup_timeout: float,
) -> dict[str, Any]:
    from .config import config_dir
    from .session_host.host_index import HostIndex

    core = _core()
    index_path = config_dir() / "hosts" / "index.json"
    initial_record = HostIndex(index_path).get(session_id)
    if initial_record is None:
        raise RuntimeError("target session has no local HostIndex record")
    if initial_record.boundary == "local":
        raise RuntimeError("fault requires a remote Session Host boundary")
    if not (initial_record.extra or {}).get("remote_authority_v2"):
        raise RuntimeError("target session lacks far-side authority v2")

    frontend_pid_before = core._read_pid_file() or core._pid_on_port(core._service_port())
    if not frontend_pid_before:
        raise RuntimeError("could not identify the running frontend")

    def quiet_service_call(action) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            action()

    removed = False
    try:
        quiet_service_call(core._service_stop)
        if core._service_is_running():
            raise RuntimeError("frontend did not stop; HostIndex was not modified")
        removed = HostIndex(index_path).remove(session_id)
        if not removed:
            raise RuntimeError("target HostIndex record disappeared before fault injection")
    finally:
        if not core._service_is_running():
            quiet_service_call(core._service_start)

    if not core._service_is_running():
        raise RuntimeError("frontend did not recover after restart")
    frontend_pid_after = core._read_pid_file() or core._pid_on_port(core._service_port())
    if not frontend_pid_after:
        raise RuntimeError("could not identify the restarted frontend")

    deadline = time.monotonic() + startup_timeout
    recovered = None
    while time.monotonic() < deadline:
        recovered = HostIndex(index_path).get(session_id)
        if recovered is not None:
            break
        time.sleep(0.25)
    if recovered is None:
        raise RuntimeError("HostIndex record was not recovered from far-side authority")

    return {
        "frontend_pid_before": frontend_pid_before,
        "frontend_pid_after": frontend_pid_after,
        "host_index_target_removed": removed,
        "initial_host_pid": initial_record.host_pid,
        "recovered_host_pid": recovered.host_pid,
        "initial_child_pid": initial_record.child_pid,
        "recovered_child_pid": recovered.child_pid,
        "recovered_from_remote_authority": bool(
            (recovered.extra or {}).get("recovered_from_remote")
            and (recovered.extra or {}).get("remote_authority_v2")
        ),
    }


def _cmd_deploy(
    args: argparse.Namespace, *, conditional: ConditionalCutover | None = None,
    routing_dir: Path | None = None,
    serving: ServingRuntime | None = None,
) -> None:
    from zdd import breadcrumb, routing
    from zdd.cutover import CutoverOrchestrator

    from . import __version__
    from .client import BridgeClient
    from .config import config_dir, load_config, load_or_create_auth_token
    from .routing_state import active_route_is_forward

    if getattr(args, "probe_serving", False) and not getattr(args, "reconcile_serving", False):
        raise ValueError("serving probe requires --reconcile-serving")
    if getattr(args, "reconcile_serving", False) and serving is None:
        if conditional is not None:
            raise ValueError("serving reconcile cannot replace a conditional repair")
        from .serving_reconcile import reconcile_serving

        reconcile_serving(args)
        return

    forwarded_skip = (
        "this machine reaches a host bridge through a forward (active.json); "
        "there is no local daemon to deploy"
    )
    core = _core()
    if serving is not None and (
        getattr(args, "force", False) or getattr(args, "recover", False) or conditional is not None
    ):
        raise ValueError("serving reconcile cannot force retirement or replace conditional recovery")
    if conditional is not None:
        from zdd.conditional import run_conditional_cutover

        if routing_dir is None:
            raise ValueError("conditional deployment requires the observed routing directory")
        if getattr(args, "force", False) or getattr(args, "recover", False):
            raise ValueError("conditional deployment cannot force retirement or run legacy recovery")
        result = run_conditional_cutover(
            routing_dir, version=__version__, request=conditional,
            health_timeout=args.health_timeout, drain_timeout=args.drain_timeout,
            lock_timeout=getattr(args, "lock_timeout", None),
            refuse_old=lambda active: forwarded_skip if active_route_is_forward(active) else None,
        )
        if getattr(args, "json", False):
            core._json_out(result.to_dict())
        else:
            for step in result.steps:
                print(f"  - {step}")
            if not result.ok:
                print(f"[FAIL] Conditional cutover refused or failed: {result.error}")
        sys.exit(0 if result.ok else 1)
    if core._active_endpoint_is_forward() and not getattr(args, "recover", False):
        if serving is not None:
            from .serving_reconcile import ServingReconcileError

            raise ServingReconcileError("serving ownership changed to a forwarded route")
        # The routed "old daemon" is the host's bridge, through the forward:
        # a cutover would take the route over, then drain and shut it down.
        # Skip before any recovery or reap, the same way in both output modes
        # (an output flag never selects lifecycle mutations); ``--recover``
        # alone continues, to clean up an aborted cutover's breadcrumb.
        if getattr(args, "json", False):
            core._json_out({"ok": False, "error": forwarded_skip, "skipped": True,
                            "steps": [f"refused: {forwarded_skip}"]})
        else:
            print(f"[SKIP] agent-bridge deploy: {forwarded_skip}")
        return
    cfg = load_config()
    predecessor = routing.Endpoint.from_dict(serving.route) if serving is not None else None
    if serving is not None:
        from .serving_reconcile import existing_auth_token

        token = existing_auth_token(serving.root)
    else:
        token = load_or_create_auth_token()
    host = "::1" if cfg.bind == "::" else cfg.bind if cfg.bind not in ("0.0.0.0", "") else "127.0.0.1"

    def pick_free_port() -> int:
        s = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
        s.bind((host, 0))
        port = s.getsockname()[1]
        s.close()
        return port

    candidate = None

    def spawn_passive(port: int):
        nonlocal candidate
        cmd = [sys.executable, "-m", "agent_bridge", "start", "--port", str(port), "--passive"]
        if serving is not None:
            from .serving_candidate import ServingCandidate

            candidate = ServingCandidate(
                Path(config_dir()), port, __version__,
                lambda: core._service_health_on_port(port, host=host, require_ready=False),
                lambda: BridgeClient(
                    f"http://{routing.format_authority(host, port)}", token,
                    timeout=2, connect_grace=0,
                ),
                drain_timeout=args.drain_timeout,
                before_shutdown=verify_predecessor_for_rollback,
            )
            cmd.extend(["--serving-launch-id", candidate.launch_id])
        # A passive generation inside the deploy's Job would die with it once
        # the cutover retires the active daemon: never start it contained.
        launcher = core._launch_bridge_daemon(
            cmd, display="agent-bridge passive generation", contained_last_resort=False
        )
        if candidate is not None:
            candidate.launcher = launcher
            return candidate
        return launcher

    def health_check(h: str, port: int) -> bool:
        try:
            with urllib.request.urlopen(
                f"http://{routing.format_authority(h, port)}/health", timeout=2,
            ) as resp:
                if resp.status != 200:
                    return False
                body = json.loads(resp.read().decode("utf-8"))
                if serving is not None:
                    if predecessor is not None and (h, port) == (
                        predecessor.client_host, predecessor.port,
                    ):
                        serving.verify_health(body)
                        return True
                    if candidate is None:
                        return False
                    candidate.verify_health(body)
                    return True
                return body.get("status") == "ok" and body.get("ready", True) is True
        except Exception:
            return False

    def make_client(base_url: str) -> BridgeClient:
        if serving is not None:
            from .serving_reconcile import guarded_client

            return guarded_client(serving, base_url, token, int(args.drain_timeout) + 60)
        return BridgeClient(base_url, token, timeout=int(args.drain_timeout) + 60)

    # Serving convergence is not stale-cutover repair: retain any existing
    # recovery evidence and let the orchestrator's normal admission refuse it.
    _pre_recovery_breadcrumb = breadcrumb.read_breadcrumb(config_dir()) if serving is None else None
    recovery = (
        breadcrumb.recover_stale_cutover(config_dir(), make_client, health_check=health_check)
        if serving is None else {}
    )
    if recovery.get("refused"):
        reason = breadcrumb.refusal_reason(recovery)
        if getattr(args, "json", False):
            core._json_out({"ok": False, "error": reason, "recovery": recovery})
        else:
            print(f"[FAIL] {reason}", file=sys.stderr)
        sys.exit(1)
    passive_reap = (
        core._reap_abandoned_passive(config_dir(), _pre_recovery_breadcrumb)
        if serving is None else {}
    )
    if passive_reap.get("refused"):
        reason = breadcrumb.refusal_reason(passive_reap)
        if getattr(args, "json", False):
            core._json_out({"ok": False, "error": reason, "passive_reap": passive_reap})
        else:
            print(f"[FAIL] {reason}", file=sys.stderr)
        sys.exit(1)
    if getattr(args, "recover", False):
        recovery["passive_reap"] = passive_reap
        if args.json:
            core._json_out(recovery)
        elif recovery.get("recovered"):
            print(f"[OK] {recovery.get('reason')}")
        else:
            print(f"[>] {recovery.get('reason')}")
        sys.exit(0)
    prelude_steps: list[str] = []
    if recovery.get("recovered"):
        prelude_steps.append(f"recovered a prior aborted cutover: {recovery.get('reason')}")
    if passive_reap.get("reaped"):
        prelude_steps.append(f"reaped an abandoned never-promoted passive (pid={passive_reap.get('pid')})")

    def refuse_serving(active: dict | None) -> str | None:
        refusal = serving.refuse(active)
        if refusal or candidate is None:
            return refusal
        try:
            candidate.verify_health(core._service_health_on_port(candidate.port, host=host))
        except Exception as exc:
            return f"successor identity changed before publication ({type(exc).__name__})"
        return None

    def verify_predecessor_for_rollback() -> None:
        if serving is None or predecessor is None:
            from .serving_reconcile import ServingReconcileError

            raise ServingReconcileError("rollback has no captured predecessor")
        serving.verify_health(core._service_health_on_port(
            predecessor.port, host=predecessor.client_host,
        ))

    def record_successor_publication(endpoint: routing.Endpoint) -> None:
        if candidate is None:
            from .serving_reconcile import ServingReconcileError

            raise ServingReconcileError("successor publication has no launch owner")
        candidate.mark_published(endpoint)

    orch = CutoverOrchestrator(
        config_dir(),
        bind=cfg.bind,
        version=__version__,
        spawn_passive=spawn_passive,
        health_check=health_check,
        make_client=make_client,
        pick_free_port=pick_free_port,
        refuse_old=(
            refuse_serving if serving is not None else
            lambda active: forwarded_skip if active_route_is_forward(active) else None
        ),
        expected_old=routing.Endpoint.from_dict(serving.route) if serving is not None else None,
        on_published=record_successor_publication if serving is not None else None,
    )
    res = None
    try:
        res = orch.run(health_timeout=args.health_timeout, drain_timeout=args.drain_timeout, force=args.force)
    finally:
        if candidate is not None:
            candidate.close(completed=res is not None and res.ok)
    # Prepend rather than print eagerly: an eager print corrupts `--json`
    # output, since it lands on stdout ahead of the single JSON payload
    # `core._json_out(res.to_dict())` emits below.
    res.steps = prelude_steps + res.steps
    if res.error == forwarded_skip:
        if args.json:
            data = res.to_dict()
            data["skipped"] = True
            core._json_out(data)
        else:
            for step in res.steps:
                print(f"  - {step}")
            print(f"[SKIP] agent-bridge deploy: {forwarded_skip}")
        sys.exit(0)

    if res.ok:
        active = routing.read_active_endpoint(config_dir(), verify_listener=False)
        if active is not None and active.pid:
            core._reconcile_service_marker(active.pid, active.version)
            res.steps.append(f"service marker reconciled -> pid {active.pid} (port {active.port})")
        old = res.old_endpoint
        # Confirmed-gone means exactly that -- confirmed, not merely
        # "we don't have contrary evidence" (PR #4543 review: treating an
        # unknown pid as confirmed retirement could let the post-cutover
        # reattach retry below run while a legacy/partial routing record's
        # old frontend is still actually attached).
        if old is None:
            old_confirmed_gone = True  # cold start -- no predecessor to wait for
        elif not old.pid:
            old_confirmed_gone = False  # pid unknown -- cannot confirm either way
        elif active is not None and old.pid == active.pid:
            old_confirmed_gone = True  # "old" IS the new active -- nothing to retire
        else:
            old_confirmed_gone = False  # confirmed below, or left False
        if old is not None and old.pid and (active is None or old.pid != active.pid):
            if serving is not None:
                exited, forced = serving.process.wait(0), False
            else:
                exited, forced = core._ensure_retired_daemon_exited(old.pid)
            if exited:
                res.steps.append(f"old daemon exited pid={old.pid}" + (" (forced tree reap)" if forced else ""))
                old_confirmed_gone = True
            else:
                res.steps.append(f"FAILED: retired daemon pid={old.pid} is still alive")
                res.ok = False
                retirement_kind = (
                    "graceful retirement" if serving is not None else
                    "graceful and forced process-tree retirement"
                )
                res.error = (
                    f"retired daemon pid={old.pid} survived {retirement_kind}; "
                    "split-brain is possible"
                )
        if res.ok and old_confirmed_gone and active is not None:
            # Phase 3's "the next generation earns the handoff, never assumes
            # it": the new daemon's own startup reattach pass ran while the
            # old generation still held its session-host claims (it was still
            # passive at that point), so it likely skipped every live record
            # as contended. Now that the old generation is *confirmed* exited
            # (its own /shutdown handler already released every claim it
            # held), retry the scan so surviving session-hosts are actually
            # adopted rather than left stranded until some later opportunity.
            # Best-effort, mirroring the existing relay-adopt post-cutover step
            # (which similarly calls its endpoint unconditionally rather than
            # gating on a protocol-version capability check).
            try:
                active_url = f"http://{routing.format_authority(host, active.port)}"
                reattach = make_client(active_url)._request(
                    "POST", "/api/v1/session-hosts/reattach"
                ) or {}
                res.steps.append(
                    f"post-cutover reattach: {reattach.get('reattached', 0)} session(s)"
                )
            except Exception as exc:  # noqa: BLE001 -- best-effort, never fails the cutover
                res.steps.append(f"post-cutover reattach failed (non-fatal): {exc}")

    if res.ok and serving is not None:
        from .serving_reconcile import (
            ServingReconcileError, compare_serving_version, observe_serving,
        )

        try:
            with observe_serving(Path(config_dir())) as current:
                if (
                    current is None
                    or compare_serving_version(current.version, __version__) != "current"
                ):
                    raise ServingReconcileError("cutover did not converge the serving runtime")
        except ServingReconcileError as exc:
            res.ok = False
            res.error = str(exc)

    if args.json:
        core._json_out(res.to_dict())
    else:
        for step in res.steps:
            print(f"  - {step}")
        if res.ok:
            print(f"Cutover complete: active daemon now on port {res.new_port}.")
        elif res.rolled_back:
            print(f"[WARN] Cutover rolled back: {res.error}", file=sys.stderr)
        else:
            print(f"[FAIL] Cutover failed: {res.error}", file=sys.stderr)
    sys.exit(0 if res.ok else 1)


def add_deploy_cutover_flags(
    parser: argparse.ArgumentParser, *, include_recover: bool = True
) -> None:
    """Register the flags ``_cmd_deploy`` reads off its args Namespace.

    Shared by the ``deploy`` verb and ``service restart`` (which calls
    ``_cmd_deploy`` directly) so the two never drift out of sync.
    ``--recover`` is deploy-only maintenance mode (heal a prior aborted
    cutover and exit *without* starting a new one) -- exposing it on
    ``restart`` would let ``service restart --recover`` return success
    without ever actually restarting anything, so callers that don't want
    it pass ``include_recover=False``. ``_cmd_deploy`` reads it via
    ``getattr(args, "recover", False)``, so omitting the flag entirely is
    equivalent to it always being unset.
    """
    parser.add_argument("--health-timeout", type=float, default=60.0, metavar="SECONDS", help="Max seconds to wait for the new daemon to become healthy.")
    parser.add_argument("--drain-timeout", type=float, default=300.0, metavar="SECONDS", help="Max seconds to wait for the old daemon's in-flight work to settle.")
    parser.add_argument("--force", action="store_true", help="Proceed with cutover even if the old daemon does not fully drain.")
    if include_recover:
        parser.add_argument("--recover", action="store_true", help="Only heal a prior aborted cutover: undrain a survivor left drained by a cutover that never completed, then exit. Does not start a new cutover.")
        parser.add_argument(
            "--reconcile-serving", action="store_true",
            help="Installer reconcile: cut over only an adopted healthy older daemon; never cold-start, force retirement or repair stale cutovers.",
        )
        parser.add_argument(
            "--probe-serving", action="store_true",
            help="Installer-only read-only serving check; exits 10 when an older daemon needs a transition.",
        )
    # `default=argparse.SUPPRESS`, not `False`: the top-level `--json` flag
    # (`build_parser()`) already sets `args.json` in the shared Namespace
    # before this subparser is applied. A `False` default here would
    # silently overwrite a `agent-bridge --json deploy`/`--json service
    # restart` invocation back to `False` (the exact regression documented
    # in `tests/test_session_selection.py`'s
    # `test_global_json_flag_survives_into_resume_namespace`). SUPPRESS lets
    # this local `--json` only ever *add* the flag, never blank it.
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit JSON.")


def register_venue_commands(sub: argparse._SubParsersAction) -> None:
    parity_p = sub.add_parser("parity", help="Run redacted launch/auth/reattach acceptance for a remote venue")
    parity_p.add_argument("target", help="Remote agent target (container: or codespace:)")
    parity_p.add_argument("--expect-workspace")
    parity_p.add_argument("--expect-capability")
    parity_p.add_argument("--auth", action="store_true", help="Require redacted GitHub credential and gh API checks")
    parity_p.add_argument("--ado-url", help="ADO Git URL to check with credential fill + ls-remote (values redacted)")
    parity_p.add_argument("--azure-scope", help="Azure scope to mint through azure-auth-helper (value redacted)")
    parity_p.add_argument("--startup-timeout", type=float, default=600.0)
    parity_p.add_argument("--turn-timeout", type=float, default=600.0)
    parity_p.add_argument("--keep-session", action="store_true")
    parity_p.add_argument(
        "--fault",
        choices=[
            FRONTEND_RESTART_HOSTINDEX_LOSS,
            RELAY_INTERRUPTION,
            FAILED_ACP_HANDSHAKE_FAULT,
            CONTAINER_RECREATE_FAULT,
        ],
        help="Run an explicit destructive fault scenario. Faults refuse other active managed sessions and affect only the harness-created launch transaction, HostIndex, or supervised credential relay.",
    )
    parity_p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit structured redacted evidence")
    parity_p.set_defaults(func=_cmd_parity)

    deploy_p = sub.add_parser(
        "deploy",
        help="(internal) installer-driven ZDD cutover seam -- activation runs it automatically on update; operators do not invoke it directly",
    )
    add_deploy_cutover_flags(deploy_p)
    deploy_p.set_defaults(func=_cmd_deploy)
