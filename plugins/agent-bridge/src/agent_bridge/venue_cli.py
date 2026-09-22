"""Venue/parity and zero-downtime deploy CLI for ``agent-bridge``."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import socket
import subprocess
import sys
import time
import urllib.request
from typing import Any

from . import __version__
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


def _passive_daemon_creationflags() -> int:
    core = _core()
    if sys.platform == "win32":
        return int(core.windowless_daemon_kwargs(breakaway=True).get("creationflags", 0))
    return 0


def _passive_daemon_stdio_kwargs() -> tuple[dict, list]:
    from .config import config_dir

    log_out = open(config_dir() / "agent-bridge.log", "ab")
    try:
        log_err = open(config_dir() / "agent-bridge-err.log", "ab")
    except OSError:
        log_out.close()
        raise
    kwargs = {"stdout": log_out, "stderr": log_err, "stdin": subprocess.DEVNULL}
    return kwargs, [log_out, log_err]


def _cmd_deploy(args: argparse.Namespace) -> None:
    from .client import BridgeClient
    from .config import config_dir, load_config, load_or_create_auth_token
    from zdd import breadcrumb, routing
    from zdd.cutover import CutoverOrchestrator

    core = _core()
    cfg = load_config()
    token = load_or_create_auth_token()
    host = cfg.bind if cfg.bind not in ("0.0.0.0", "") else "127.0.0.1"

    def pick_free_port() -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((host, 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def spawn_passive(port: int):
        cmd = [sys.executable, "-m", "agent_bridge", "start", "--port", str(port), "--passive"]
        kwargs, opened_streams = _passive_daemon_stdio_kwargs()
        if sys.platform == "win32":
            kwargs["creationflags"] = _passive_daemon_creationflags()
        else:
            kwargs["start_new_session"] = True
        try:
            try:
                return subprocess.Popen(cmd, **kwargs)
            finally:
                for stream in opened_streams:
                    stream.close()
        except OSError:
            if sys.platform != "win32":
                raise
            pid = core._spawn_via_wmi_broker_pid(cmd)
            if pid is None:
                raise
            return core._BrokeredProcessHandle(pid)

    def health_check(h: str, port: int) -> bool:
        try:
            with urllib.request.urlopen(f"http://{h}:{port}/health", timeout=2) as resp:
                if resp.status != 200:
                    return False
                body = json.loads(resp.read().decode("utf-8"))
                return body.get("status") == "ok" and body.get("ready", True) is True
        except Exception:
            return False

    def make_client(base_url: str) -> BridgeClient:
        return BridgeClient(base_url, token, timeout=int(args.drain_timeout) + 60)

    _pre_recovery_breadcrumb = breadcrumb.read_breadcrumb(config_dir())
    recovery = breadcrumb.recover_stale_cutover(config_dir(), make_client, health_check=health_check)
    passive_reap = core._reap_abandoned_passive(config_dir(), _pre_recovery_breadcrumb)
    if getattr(args, "recover", False):
        recovery["passive_reap"] = passive_reap
        if args.json:
            core._json_out(recovery)
        elif recovery.get("recovered"):
            print(f"[OK] {recovery.get('reason')}")
        else:
            print(f"[>] {recovery.get('reason')}")
        sys.exit(0)
    if recovery.get("recovered"):
        print(f"[>] Recovered a prior aborted cutover: {recovery.get('reason')}")
    if passive_reap.get("reaped"):
        print(f"[>] Reaped an abandoned never-promoted passive (pid={passive_reap.get('pid')})")

    orch = CutoverOrchestrator(
        config_dir(),
        bind=cfg.bind,
        version=__version__,
        spawn_passive=spawn_passive,
        health_check=health_check,
        make_client=make_client,
        pick_free_port=pick_free_port,
    )
    res = orch.run(health_timeout=args.health_timeout, drain_timeout=args.drain_timeout, force=args.force)

    if res.ok:
        active = routing.read_active_endpoint(config_dir(), verify_listener=False)
        if active is not None and active.pid:
            core._reconcile_service_marker(active.pid, active.version)
            res.steps.append(f"service marker reconciled -> pid {active.pid} (port {active.port})")
        old = res.old_endpoint
        if old is not None and old.pid and (active is None or old.pid != active.pid):
            exited, forced = core._ensure_retired_daemon_exited(old.pid)
            if exited:
                res.steps.append(f"old daemon exited pid={old.pid}" + (" (forced tree reap)" if forced else ""))
            else:
                res.steps.append(f"FAILED: retired daemon pid={old.pid} is still alive")
                res.ok = False
                res.error = (
                    f"retired daemon pid={old.pid} survived graceful and forced process-tree retirement; "
                    "split-brain is possible"
                )

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
    deploy_p.add_argument("--health-timeout", type=float, default=60.0, metavar="SECONDS", help="Max seconds to wait for the new daemon to become healthy.")
    deploy_p.add_argument("--drain-timeout", type=float, default=300.0, metavar="SECONDS", help="Max seconds to wait for the old daemon's in-flight work to settle.")
    deploy_p.add_argument("--force", action="store_true", help="Proceed with cutover even if the old daemon does not fully drain.")
    deploy_p.add_argument("--recover", action="store_true", help="Only heal a prior aborted cutover: undrain a survivor left drained by a cutover that never completed, then exit. Does not start a new cutover.")
    deploy_p.add_argument("--json", action="store_true", help="Emit JSON.")
    deploy_p.set_defaults(func=_cmd_deploy)
