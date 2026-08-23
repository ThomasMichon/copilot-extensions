"""CLI entry point for agent-dispatch.

Two modes:
  * ``agent-dispatch serve`` runs the per-host coordinator (uvicorn).
  * every other subcommand is a thin client that talks to a coordinator
    (``--url`` / ``AGENT_DISPATCH_URL``; ``--token`` / ``AGENT_DISPATCH_TOKEN``).

Output is JSON on stdout so the CLI composes with other tooling.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from . import __version__
from . import config as _config
from .client import DispatchClient, DispatchError
from .config import (
    Config,
    client_token,
    client_url,
    failover_machine,
    has_live_local_coordinator,
    shared_token,
    shared_url,
)
from .registrations import RegistrationKind

if TYPE_CHECKING:
    from .registrar import ProfileDeclaration


def _emit(value: Any) -> int:
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _federation_rendezvous(args: argparse.Namespace):
    """Resolve the rendezvous a federation command targets: an explicit ``--url``,
    else the hosted (shared) coordinator. Errors loudly when neither exists."""
    from .federation_runner import build_rendezvous, hosted_rendezvous

    url = getattr(args, "url", None)
    if url:
        return build_rendezvous(url, token=getattr(args, "token", None) or client_token())
    rv = hosted_rendezvous()
    if rv is None:
        print(
            "no hosted coordinator configured -- set AGENT_DISPATCH_SHARED_URL (the "
            "hosted-coordinator endpoint) or pass --url",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return rv


def _cmd_federation_run(args: argparse.Namespace) -> int:
    from .federation_runner import FederationRunner

    role = args.role or _config.federation_role() or "peer"
    instance = args.instance or _config.federation_instance()
    if not instance:
        print(
            "no instance id -- pass --instance or set AGENT_DISPATCH_FEDERATION_INSTANCE",
            file=sys.stderr,
        )
        return 2
    rv = _federation_rendezvous(args)
    runner = FederationRunner(
        rv, instance, role=role, machine=instance, lease_ttl=args.lease_ttl
    )
    if args.once:
        return _emit(runner.tick())
    interval = args.interval if args.interval is not None else _config.federation_interval()
    try:
        runner.run(interval=interval)
    except KeyboardInterrupt:
        pass
    finally:
        runner.resign()
    return 0


def _cmd_federation_status(args: argparse.Namespace) -> int:
    rv = _federation_rendezvous(args)
    return _emit(
        {"coordinator": rv.discover_coordinator(), "peers": rv.discover_peers()}
    )


def _resolve_client_target(args: argparse.Namespace) -> tuple[str, str | None]:
    """Resolve which coordinator (URL + token) a client command targets.

    Precedence:

    1. An explicit ``--url`` (with ``--token``/``AGENT_DISPATCH_TOKEN``) -- the
       operator's direct override, always wins.
    2. ``--shared`` -- route to the **shared/elected coordinator**
       (``AGENT_DISPATCH_SHARED_URL``; the hosted coordinator) for cross-machine
       dispatch, authenticated with its own ``AGENT_DISPATCH_SHARED_TOKEN``. If no
       shared coordinator is configured, error loudly rather than silently using
       the local queue (which would strand a cross-machine task on one host).
    3. Otherwise the **local** loopback coordinator -- same-machine work, the
       single-machine default that needs no shared service. **Failover:** if a
       shared coordinator is configured (``AGENT_DISPATCH_SHARED_URL``) *and* the
       local coordinator is not live (this environment's coordinator is down),
       transparently fall back to the shared/hosted coordinator so work is
       dispatched onto it (e.g. the standby) rather than stranded on a dead local
       queue. This is opt-in by construction: with no shared URL configured,
       nothing is probed and the local default is unchanged.
    """
    url = getattr(args, "url", None)
    token = getattr(args, "token", None)
    if url:
        return url, (token or client_token())
    if getattr(args, "shared", False):
        surl = shared_url()
        if not surl:
            print(
                "no shared coordinator configured -- set AGENT_DISPATCH_SHARED_URL "
                "(the hosted-coordinator endpoint) or pass --url",
                file=sys.stderr,
            )
            raise SystemExit(2)
        return surl, (token or shared_token())
    # Default local path, with opt-in failover to the shared coordinator: only
    # probe (and only fall back) when a shared URL is configured, so the common
    # single-machine case pays nothing and behaves exactly as before.
    surl = shared_url()
    if surl and not has_live_local_coordinator():
        return surl, (token or shared_token())
    return client_url(), (token or client_token())


def _cmd_print_endpoint(args: argparse.Namespace) -> int:
    """Print this machine's local coordinator base URL (``http://host:port``).

    A peer resolves this over SSH (``ssh <alias> agent-dispatch print-endpoint``)
    to discover the dynamic loopback port to port-forward to for SSH failover.
    Local-only: it reports *this* host's coordinator, never a failover target.
    """
    print(client_url())
    return 0


def _should_ssh_failover(args: argparse.Namespace) -> str | None:
    """The peer machine to SSH-failover to for this command, or ``None``.

    Applies only on the **default local path** (no explicit ``--url``/``--shared``)
    when ``AGENT_DISPATCH_FAILOVER_MACHINE`` names a real *peer* and the local
    coordinator is not live. Preferred over the hosted ``AGENT_DISPATCH_SHARED_URL``
    HTTP fallback (per-machine SSH identity, no shared secret). Returns the peer
    machine name, or ``None`` when failover does not apply.
    """
    if getattr(args, "url", None) or getattr(args, "shared", False):
        return None
    machine = failover_machine()
    if not machine:
        return None
    from . import remote_dispatch

    if not remote_dispatch.is_peer_machine(machine):
        return None
    if has_live_local_coordinator():
        return None
    return machine


def _client(args: argparse.Namespace, *, ensure: bool = True) -> DispatchClient:
    if ensure:
        _ensure_local_coordinator(args)
    # SSH-transport failover: local coordinator down + a peer configured -> open
    # an SSH port-forward to the peer's loopback coordinator (per-machine key =
    # identity, tokenless) and run this command against it, keeping local context.
    peer = _should_ssh_failover(args)
    if peer is not None:
        from . import ssh_tunnel

        try:
            tunnel = ssh_tunnel.open_coordinator_tunnel(peer)
        except ssh_tunnel.TunnelUnavailable as exc:
            print(
                f"agent-dispatch: local coordinator down and SSH failover to "
                f"{peer!r} unavailable ({exc})",
                file=sys.stderr,
            )
            raise SystemExit(2) from exc
        return DispatchClient(tunnel.base_url, token=None, tunnel=tunnel)
    url, token = _resolve_client_target(args)
    return DispatchClient(url, token=token)


_AUTOSTART_ENV_OPT_OUT = "AGENT_DISPATCH_NO_AUTOSTART"


def _spawn_coordinator_process() -> None:
    """Launch the local coordinator **detached** (best effort, no wait).

    Runs the coordinator directly as ``<python> -m agent_dispatch serve`` under
    ``DETACHED_PROCESS`` (Windows) / a new session (POSIX) so it outlives this CLI
    process -- a later session then finds it already up. It appends output to
    ``serve-service.log`` and honors ``service.env`` (token / host-port pins) for
    parity with the installed launcher.

    NB: this deliberately does NOT shell out to ``serve-service.ps1`` via
    ``conhost``/``powershell``. That indirection, launched detached from Python,
    proved flaky on Windows (the wrapper exited before ``serve`` bound a listener,
    so no rendezvous was written and discovery never converged). Running the
    interpreter directly is the reliable path.

    On Windows the windowless ``pythonw.exe`` sibling is required in addition to
    ``DETACHED_PROCESS``: a detached venv ``python.exe`` launcher re-execs a base
    console interpreter that allocates a fresh DefTerm console.
    """
    install_dir = Path.home() / ".agent-dispatch"
    if os.name == "nt":
        venv_py = install_dir / ".venv" / "Scripts" / "python.exe"
    else:
        venv_py = install_dir / ".venv" / "bin" / "python"
    from .procutil import detached_kwargs, windowless_python

    python = windowless_python(str(venv_py) if venv_py.is_file() else sys.executable)

    # Honor service.env (token, host/port pins) if present -- parity with the
    # installed launcher, which loads it before running `serve`.
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env_file = install_dir / "service.env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                env[k.strip()] = os.path.expandvars(v.strip())
        except OSError:
            pass

    try:
        log: Any = open(install_dir / "serve-service.log", "ab")  # noqa: SIM115
    except OSError:
        log = subprocess.DEVNULL

    kwargs: dict[str, Any] = dict(
        stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True, env=env,
        # Launch the detached coordinator from the runtime root, never the CWD we
        # inherited (a session-start hook's CWD is often the plugin payload dir,
        # which on Windows would lock it against `copilot plugin update`). The
        # daemon also relocates itself (procutil.relocate_off_payload) as a belt.
        cwd=str(install_dir),
    )
    kwargs.update(detached_kwargs())
    try:
        subprocess.Popen([python, "-m", "agent_dispatch", "serve"], **kwargs)  # noqa: S603
    finally:
        if log is not subprocess.DEVNULL:
            try:
                log.close()
            except OSError:
                pass


def _lazy_start_coordinator(*, timeout: float = 20.0) -> bool:
    """Start a local coordinator if none answers, then wait until it does.

    Serialized across concurrent CLI processes via an exclusive lock file so a
    burst of commands can't spawn a *herd* of coordinators (the SQLite queue is
    single-writer). A non-starter waits for whoever holds the lock to bring one
    up. Returns True if a live coordinator is available when we return.
    """
    from . import config

    if config.has_live_local_coordinator():
        return True
    rd = config.run_dir()
    try:
        rd.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    lock = rd / "autostart.lock"
    starter = False
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
        starter = True
    except FileExistsError:
        # Another CLI is starting one. Steal a stale lock (older than the timeout
        # with still no coordinator) so a crashed starter can't wedge autostart.
        try:
            if time.time() - lock.stat().st_mtime > timeout:
                lock.unlink()
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                starter = True
        except OSError:
            starter = False
    try:
        if starter and not config.has_live_local_coordinator():
            print(
                "agent-dispatch: no local coordinator answering; starting one...",
                file=sys.stderr,
            )
            _spawn_coordinator_process()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if config.has_live_local_coordinator():
                return True
            time.sleep(0.4)
        return config.has_live_local_coordinator()
    finally:
        if starter:
            try:
                lock.unlink()
            except OSError:
                pass


def _ensure_local_coordinator(args: argparse.Namespace) -> None:
    """Best-effort: ensure a local coordinator is reachable before a client
    command runs, lazily starting one if not.

    No-op for an explicit ``--url``/``--shared`` target (remote/operator choice),
    for a **WSL guest opted in** to Windows-client mode (``AGENT_DISPATCH_WSL_WINDOWS_CLIENT``;
    the Windows host owns that coordinator), or when opted out via
    ``AGENT_DISPATCH_NO_AUTOSTART``. Every failure is swallowed -- the command then
    fails loudly on its own if the coordinator really is unreachable, so autostart
    never converts a hard error into a silent hang.
    """
    if getattr(args, "url", None) or getattr(args, "shared", False):
        return
    if os.environ.get(_AUTOSTART_ENV_OPT_OUT):
        return
    try:
        from .config import wsl_windows_client
        from .netinfo import is_wsl

        if is_wsl() and wsl_windows_client():
            return
    except Exception:
        pass
    try:
        _lazy_start_coordinator()
    except Exception:
        pass


def _parse_affinity(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in pairs or []:
        key, _, val = item.partition("=")
        out[key.strip()] = val.strip()
    return out


def _cmd_serve(args: argparse.Namespace) -> int:
    from .config import load_config
    from .server import serve

    _reroot_serve_cwd()
    base = load_config()
    cfg = Config(
        host=_resolve_serve_host(args, base),
        port=args.port or base.port,
        db_path=args.db or base.db_path,
        token=args.token or base.token,
    )
    serve(cfg, passive=bool(getattr(args, "passive", False)))
    return 0


def _reroot_serve_cwd() -> None:
    # The coordinator must not keep the plugin payload as cwd (#621); on Windows
    # the process cwd locks that directory and blocks payload replacement.
    try:
        from .runtime_version import install_dir

        target = install_dir()
    except Exception as exc:
        print(
            f"agent-dispatch: warning: could not resolve runtime cwd; using home: {exc}",
            file=sys.stderr,
        )
        target = Path.home()
    try:
        target.mkdir(parents=True, exist_ok=True)
        os.chdir(target)
    except Exception as exc:
        print(
            f"agent-dispatch: warning: could not switch runtime cwd to {target}: {exc}",
            file=sys.stderr,
        )


def _reap_superseded_coordinators(result: Any) -> None:
    """After a successful cutover, retire every *other* live coordinator.

    The cutover itself only shuts down the single predecessor it replaced; this
    reconciles the full set so stale coordinators from earlier cutovers/restarts
    do not leak (see :mod:`agent_dispatch.reap`). Best-effort: records a step and
    never raises.

    Anchored on the cutover's own ``new_port``: the only coordinator kept is the
    routing table's ``active`` entry **when its port matches the port this
    cutover just promoted** (plus this process). If that cannot be confirmed the
    reap is skipped rather than risk terminating the freshly promoted daemon.
    """
    try:
        from zdd.routing import read_table

        from .config import routing_dir
        from .reap import reap_superseded_coordinators

        new_port = getattr(result, "new_port", None)
        table = read_table(routing_dir()) or {}
        active = table.get("active") if isinstance(table, dict) else None
        keep = {os.getpid()}
        if (
            isinstance(active, dict)
            and new_port
            and active.get("port") == new_port
            and active.get("pid")
        ):
            keep.add(int(active["pid"]))
        else:
            result.steps.append(
                "reap skipped: could not confirm the promoted coordinator "
                "against the routing table"
            )
            return
        reap = reap_superseded_coordinators(keep_pids=keep)
        if reap.reaped:
            result.steps.append(
                f"reaped {len(reap.reaped)} superseded coordinator(s): {reap.reaped}"
            )
        for err in reap.errors:
            result.steps.append(f"reap: {err}")
    except Exception as exc:  # best-effort: reap must never fail a cutover
        try:
            result.steps.append(f"reap skipped: {exc}")
        except Exception:  # noqa: S110 -- nothing actionable if even that fails
            pass


def _cmd_cutover(args: argparse.Namespace) -> int:
    """Internal graceful-cutover seam -- driven by the installer, not operators.

    Stands the freshly-installed coordinator slot up PASSIVE on a fresh port,
    health-gates it, flips the zdd routing table, drains the old coordinator at
    the safe cutover point (between task claims), and retires it -- so a version
    update never kills in-flight work. The supervisor + spawned workers outlive
    the swap and re-adopt the new coordinator via the durable queue DB + routing
    table. Rolls back before the commit point; commits forward after. See
    docs/patterns/graceful-daemon-cutover.md. NOT an operator command.
    """
    import json as _json
    import socket as _socket
    import subprocess as _subprocess
    import sys as _sys
    import urllib.request as _urllib

    from zdd import breadcrumb
    from zdd.cutover import CutoverOrchestrator

    from .config import client_token, load_config, routing_dir

    cfg = load_config()
    token = client_token()
    wildcard_v4 = ".".join(("0", "0", "0", "0"))
    host = cfg.host if cfg.host not in (wildcard_v4, "", "::") else "127.0.0.1"
    if cfg.host == "::":
        host = "::1"

    def pick_free_port() -> int:
        family = _socket.AF_INET6 if ":" in host else _socket.AF_INET
        with _socket.socket(family, _socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return int(sock.getsockname()[1])

    def spawn_passive(port: int):
        from agent_procutil import detached_kwargs, windowless_python

        cmd = [
            windowless_python(_sys.executable), "-m", "agent_dispatch", "serve",
            "--host", cfg.host, "--port", str(port), "--passive",
        ]
        # The coordinator binds AGENT_DISPATCH_PORT (Stage C: else an ephemeral
        # port); ``serve --port`` alone is only the client fallback. Pin the
        # passive to the orchestrator's pre-selected free port via the env so it
        # binds EXACTLY that port and the health-gate/flip target the right one.
        child_env = dict(os.environ)
        child_env["AGENT_DISPATCH_PORT"] = str(port)
        kwargs: dict[str, Any] = {
            "env": child_env,
            "stdin": _subprocess.DEVNULL,
            "stdout": _subprocess.DEVNULL,
            "stderr": _subprocess.DEVNULL,
        }
        kwargs.update(detached_kwargs())
        return _subprocess.Popen(cmd, **kwargs)  # noqa: S603

    def health_check(check_host: str, port: int) -> bool:
        try:
            req = _urllib.Request(f"http://{check_host}:{port}/health")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            with _urllib.urlopen(req, timeout=2) as resp:  # noqa: S310 -- loopback
                if resp.status != 200:
                    return False
                payload = _json.loads(resp.read().decode("utf-8"))
                return payload.get("status") != "draining"
        except Exception:
            return False

    def make_client(base_url: str):
        return DispatchClient(base_url, token=token, timeout=float(args.drain_timeout) + 60.0)

    def liveness_check(check_host: str, port: int) -> bool:
        # Recovery MUST use a plain liveness probe, NOT the draining-aware
        # health_check above: an aborted cutover strands the old coordinator
        # DRAINING, and the whole point of recovery is to undrain it. A
        # draining-aware probe reports a drained survivor as "unreachable", so
        # recovery would retire the breadcrumb without undraining -- leaving the
        # coordinator permanently closed to new claims. A drained daemon still
        # answers /health 200, so treat any 200 as alive here.
        try:
            req = _urllib.Request(f"http://{check_host}:{port}/health")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            with _urllib.urlopen(req, timeout=2) as resp:  # noqa: S310 -- loopback
                return resp.status == 200
        except Exception:
            return False

    recovery = breadcrumb.recover_stale_cutover(
        routing_dir(), make_client, health_check=liveness_check
    )
    if getattr(args, "recover", False):
        _emit(recovery)
        return 0
    if recovery.get("recovered"):
        print(f"[>] Recovered a prior aborted cutover: {recovery.get('reason')}", file=sys.stderr)

    orch = CutoverOrchestrator(
        routing_dir(),
        bind=cfg.host,
        version=__import__("agent_dispatch").__version__,
        spawn_passive=spawn_passive,
        health_check=health_check,
        make_client=make_client,
        pick_free_port=pick_free_port,
    )
    result = orch.run(
        health_timeout=args.health_timeout,
        drain_timeout=args.drain_timeout,
        force=args.force,
    )
    if result.ok:
        _reap_superseded_coordinators(result)
    if getattr(args, "json", False):
        _emit(result.to_dict())
    else:
        for step in result.steps:
            print(f"  - {step}", file=sys.stderr)
        if result.ok:
            print(f"Cutover complete: coordinator now on port {result.new_port}.", file=sys.stderr)
        elif result.rolled_back:
            print(f"[WARN] Cutover rolled back: {result.error}", file=sys.stderr)
        else:
            print(f"[FAIL] Cutover failed: {result.error}", file=sys.stderr)
    return 0 if result.ok else 1


def _resolve_serve_host(args: argparse.Namespace, base: Config) -> str:
    """The host the coordinator binds when ``agent-dispatch serve`` runs.

    Precedence: an explicit ``--host``; then an explicit ``AGENT_DISPATCH_HOST``
    env override (this is how ``serve-service.ps1`` passes the resolved bind
    host); then, on Windows, the topology-derived bind host
    (:func:`netinfo.resolve_bind_host` -- ``127.0.0.1`` on mirrored, the
    ``vEthernet (WSL)`` IP on NAT, never ``0.0.0.0``/LAN); otherwise the local
    default. The Windows host now **owns** the coordinator -- it no longer defers
    to a WSL peer (reverses issue #2777).
    """
    import os

    if args.host:
        return args.host
    if "AGENT_DISPATCH_HOST" in os.environ:
        return base.host
    if sys.platform == "win32":
        from .netinfo import resolve_bind_host

        return _resolve_bind_host_resilient(resolve_bind_host)
    return base.host


def _resolve_bind_host_resilient(
    resolver,
    *,
    retries: int | None = None,
    delay: float | None = None,
    sleep=None,
    log=None,
):
    """Resolve the Windows bind host, tolerating a logon-before-WSL race (#2889).

    On **NAT**, the coordinator's Scheduled Task fires ``-AtLogOn`` but WSL/HNS
    (which owns the ``vEthernet (WSL)`` adapter the coordinator must bind) starts
    slightly *after* logon. At that instant the adapter has no IPv4 yet, so
    :func:`netinfo.resolve_bind_host` **raises** ``RuntimeError`` (it refuses to
    fall back to ``0.0.0.0``/LAN). Without a retry the ``serve`` process crashes
    with no listener -- the exact #2889 symptom (a *manual* serve run later,
    once WSL is up, binds fine). Retry with a bounded wait so the coordinator
    self-heals once WSL networking comes up. **mirrored** resolves on the first
    try (no raise), so this is a no-op there.

    Tunable via ``AGENT_DISPATCH_BIND_RETRIES`` (default 20) and
    ``AGENT_DISPATCH_BIND_RETRY_DELAY`` seconds (default 3) -- ~60s of grace.
    ``sleep``/``log`` are injectable for tests. On exhaustion the last error is
    re-raised so the failure is loud and shows up in the launcher log.
    """
    import os
    import time

    if retries is None:
        retries = int(os.environ.get("AGENT_DISPATCH_BIND_RETRIES", "20"))
    if delay is None:
        delay = float(os.environ.get("AGENT_DISPATCH_BIND_RETRY_DELAY", "3"))
    if sleep is None:
        sleep = time.sleep
    if log is None:
        def log(msg: str) -> None:
            print(msg, file=sys.stderr, flush=True)

    retries = max(1, retries)
    last_err: RuntimeError | None = None
    for attempt in range(1, retries + 1):
        try:
            return resolver()
        except RuntimeError as exc:
            last_err = exc
            log(
                "agent-dispatch: bind host not resolvable yet "
                f"(attempt {attempt}/{retries}): {exc}"
            )
            if attempt < retries:
                sleep(delay)
    # Every attempt raised RuntimeError; re-raise the last so the failure is loud
    # and lands in the launcher log rather than silently binding nothing.
    raise last_err  # type: ignore[misc]  # loop ran >=1 time, so last_err is set


def _cmd_create(args: argparse.Namespace) -> int:
    repo = _scope_repo(args)
    if not repo:
        print(_REPO_UNRESOLVED, file=sys.stderr)
        return 2
    # Cross-machine dispatch (Phase 8 8a): an embody spawn targeted at *another*
    # machine runs the whole create+embody THERE over the SSH mesh, so
    # the task lives on the target's coordinator and the autopilot session runs
    # + completes on the target. agent-dispatch is per-host, so there is no local
    # task in this path.
    from . import remote_dispatch

    if remote_dispatch.is_cross_machine(args):
        return _dispatch_cross_machine(args, repo)
    payload_inline = args.payload_inline
    if args.payload_file:
        payload_inline = _read_payload_file(args.payload_file)
    claim_as = None
    if getattr(args, "claim", False):
        claim_as = _owner_from_identity(args)
        if claim_as is None:
            print(
                "agent-dispatch create --claim: could not resolve this worktree's "
                "identity to claim as; run inside a worktree or pass "
                "--machine/--worktree.",
                file=sys.stderr,
            )
            return 2
    with _client(args) as c:
        task = c.create(
            args.title,
            repo=repo,
            prompt=args.prompt,
            proposed=args.proposed,
            requires=args.require or [],
            excludes=args.exclude or [],
            affinity=_parse_affinity(args.affinity),
            labels=args.label or [],
            payload_ref=args.payload_ref,
            payload_inline=payload_inline,
            target_machine=args.target_machine,
            target_worktree=args.target_worktree,
            target_repo=args.target_repo,
            source=args.source,
            origin_ref=args.origin_ref,
            dedup_key=args.dedup_key,
            goal=args.goal,
            done_criteria=args.done_criteria,
            not_before=args.not_before,
            claim_as=claim_as,
        )
    if claim_as is not None:
        # Signal whether THIS call won the create-and-claim (mine now) or lost the
        # dedup race (the subject was already taken by someone else).
        won = task.get("owner") == claim_as and task.get("status") == "claimed"
        return _emit(_enrich({**task, "claimed_by_me": won}))
    if args.spawn and not args.proposed:
        _spawn_worker_for(args, task)
    return _emit(_enrich(task))


def _cmd_propose(args: argparse.Namespace) -> int:
    """Draft an unclaimable ``proposed`` task (the propose -> queue lifecycle).

    Identical to ``create`` but the task is always ``proposed`` (unclaimable) and is
    never claimed or spawned -- a proposal is a *plan*, committed to binding later
    with ``queue <id>``. Rejects the execution-only flags ``--claim`` / ``--spawn``
    rather than silently ignoring them.
    """
    if getattr(args, "claim", False) or getattr(args, "spawn", False):
        print(
            "agent-dispatch propose: a proposed draft is not claimed or spawned; use "
            "'create' for that, or 'queue <id>' after proposing to make it claimable",
            file=sys.stderr,
        )
        return 2
    args.proposed = True
    return _cmd_create(args)


def _read_payload_file(path: str) -> str:
    """Read a payload file, or stdin when ``path`` is ``-``."""
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _dispatch_cross_machine(args: argparse.Namespace, repo: str) -> int:
    """SSH-push the create+embody to the target machine (Phase 8 8a)."""
    from . import remote_dispatch

    payload: str | None = None
    if args.payload_file:
        payload = _read_payload_file(args.payload_file)
    elif args.payload_inline:
        payload = args.payload_inline
    try:
        result = remote_dispatch.dispatch_to_remote(
            args.target_machine, args, repo=repo, payload=payload
        )
    except remote_dispatch.RemoteDispatchUnavailable as exc:
        print(
            f"agent-dispatch: cross-machine dispatch to {args.target_machine!r} "
            f"unavailable ({exc}); nothing was queued",
            file=sys.stderr,
        )
        return 2
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0:
        diagnosis = remote_dispatch.diagnose_remote_failure(
            args.target_machine, result.returncode, result.stderr
        )
        print(
            f"agent-dispatch: remote dispatch to {args.target_machine!r} failed -- "
            f"{diagnosis}; nothing was queued on {args.target_machine!r}",
            file=sys.stderr,
        )
        return result.returncode
    return 0


def _spawn_worker_for(args: argparse.Namespace, task: dict) -> None:
    """Reserve the spawn atomically, then spawn a worker **exactly once**.

    The spawn is gated on an **atomic spawn reservation** taken from the
    coordinator before launching anything. This closes the gap between the
    queue's transactional dedup/claim and the non-transactional spawn: a dedup
    collision (``create --spawn`` on an existing ``dedup_key``), a racing second
    ``create --spawn``, or a re-poll can never double-spawn -- exactly one caller
    wins the reservation and spawns; the rest skip. If no active reservation can
    be taken (one already exists), this returns without spawning.
    """
    task_id = task["id"]
    reserved_by = f"cli:{uuid.uuid4().hex[:8]}"
    try:
        with _client(args) as c:
            resp = c.reserve_spawn(task_id, reserved_by=reserved_by)
    except DispatchError as exc:
        # Fail safe: if we cannot reserve, we do NOT spawn (better to leave the
        # task queued than risk a second autonomous worker).
        print(
            f"agent-dispatch: --spawn skipped (could not reserve spawn: {exc}); "
            f"task {task_id} left queued for any worker to claim",
            file=sys.stderr,
        )
        return
    if not resp.get("reserved"):
        res = resp.get("reservation", {})
        print(
            f"agent-dispatch: --spawn skipped -- task {task_id} already has an "
            f"active spawn ({res.get('key')} is {res.get('state')}); not spawning "
            "a second worker",
            file=sys.stderr,
        )
        return

    key = resp["reservation"]["key"]
    spawned = _do_spawn(args, task)
    try:
        with _client(args) as c:
            if spawned is None:
                c.fail_spawn(key, detail="no spawn mechanism available")
            else:
                result, via, handle = spawned
                if result.returncode != 0:
                    c.fail_spawn(key, detail=f"{via} exited {result.returncode}")
                else:
                    c.record_spawn(
                        key,
                        session_handle=handle.get("session"),
                        worktree=handle.get("worktree"),
                    )
    except DispatchError:
        # Best-effort bookkeeping -- the spawn itself already ran and was
        # reported; a coordinator hiccup here must not crash `create`.
        pass


def _embody_handle(result) -> dict[str, str | None]:
    """Best-effort extract the session/worktree handle from ``embody --json``."""
    from . import embody

    return embody.parse_handle(result)


def _do_spawn(args: argparse.Namespace, task: dict):
    """Launch a worker for a task (best effort); return ``(result, via, handle)``.

    Returns ``None`` if no spawn mechanism is available (task left queued). Two
    backends select *how* the worker is embodied:

    - ``embody`` -- a **CLI-backed autopilot** session in a fresh parallel
      worktree via ``agent-worktrees embody`` (the "dispatch an agent to do X"
      path: a durable, NF-viewable session that works the task to explicit
      completion). Falls back to the bridge backend if agent-worktrees is
      absent.
    - ``bridge`` (default) -- a **headless** agent-bridge ACP worker.
    """
    backend = getattr(args, "spawn_backend", "bridge")
    coordinator_url = _resolve_client_target(args)[0]

    if backend == "embody":
        from . import embody

        if embody.embody_available():
            worker_id = f"embody-{uuid.uuid4().hex[:8]}"
            try:
                result = embody.spawn_embodied_worker(
                    task["id"],
                    coordinator_url=coordinator_url,
                    worker_id=worker_id,
                    verify_timeout=getattr(args, "verify_timeout", 0) or 0,
                )
            except embody.EmbodyUnavailable as exc:
                print(
                    f"agent-dispatch: --spawn (embody) skipped ({exc}); task "
                    f"{task['id']} left queued for any worker to claim",
                    file=sys.stderr,
                )
                return None
            _report_spawn_result(result, task["id"], "agent-worktrees embody")
            return result, "agent-worktrees embody", _embody_handle(result)
        # Graceful degrade: no agent-worktrees -> try the headless bridge path.
        print(
            "agent-dispatch: embody backend unavailable (agent-worktrees not on "
            "PATH); falling back to the bridge backend",
            file=sys.stderr,
        )

    from . import bridge

    worker_id = f"spawn-{uuid.uuid4().hex[:8]}"
    try:
        result = bridge.spawn_worker(
            task["id"],
            agent=args.spawn_agent,
            coordinator_url=coordinator_url,
            worker_id=worker_id,
            wait=not args.run_async,
        )
    except bridge.BridgeUnavailable as exc:
        print(
            f"agent-dispatch: --spawn skipped ({exc}); task {task['id']} left queued "
            "for any worker to claim",
            file=sys.stderr,
        )
        return None
    _report_spawn_result(result, task["id"], "agent-bridge")
    return result, "agent-bridge", {"session": worker_id, "worktree": None}


def _report_spawn_result(result, task_id: str, via: str) -> None:
    """Print a warning if a best-effort spawn subprocess reported failure."""
    if result.returncode != 0:
        print(
            f"agent-dispatch: spawn via {via} failed (exit {result.returncode}); "
            f"task {task_id} remains queued. stderr: {result.stderr.strip()[:400]}",
            file=sys.stderr,
        )


def _identity(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """(machine, worktree): explicit flags override the agent-worktrees resolution."""
    machine = getattr(args, "machine", None)
    worktree = getattr(args, "worktree", None)
    if machine is None or worktree is None:
        from .identity import resolve_identity

        r_machine, r_worktree = resolve_identity()
        machine = machine or r_machine
        worktree = worktree or r_worktree
    return (machine, worktree)


_REPO_UNRESOLVED = (
    "agent-dispatch: could not resolve the calling repo (lane). Run inside a repo/"
    "worktree, or pass --repo <name|remote>. Tasks are scoped per repo, so a lane "
    "is required."
)


def _scope_repo(args: argparse.Namespace) -> str | None:
    """Resolve the lane for this command: an explicit ``--repo`` (a local repo
    name or a remote URL) wins; otherwise the calling repo, resolved from the
    CWD. Returns a canonical remote, or ``None`` if nothing resolves.
    """
    from .identity import resolve_repo, resolve_repo_selector

    selector = getattr(args, "repo", None)
    return resolve_repo_selector(selector) if selector else resolve_repo()


def _enrich(result: Any) -> Any:
    """Annotate task dict(s) with a display-only ``repo_name`` (the local name
    for the canonical ``repo`` remote, when the registry knows it), and parse the
    stored ``latest_progress`` JSON string into an object for clean at-a-glance
    output."""
    from .identity import name_for_repo

    def one(d: Any) -> Any:
        if not isinstance(d, dict):
            return d
        if "repo" in d and "repo_name" not in d:
            name = name_for_repo(d.get("repo"))
            if name:
                d = {**d, "repo_name": name}
        lp = d.get("latest_progress")
        if isinstance(lp, str) and lp:
            try:
                d = {**d, "latest_progress": json.loads(lp)}
            except (ValueError, TypeError):
                pass
        return d

    if isinstance(result, list):
        return [one(x) for x in result]
    if isinstance(result, dict) and any(k in result for k in ("assigned", "owned")):
        return {k: (_enrich(v) if isinstance(v, list) else v) for k, v in result.items()}
    return one(result)


def _cmd_claim(args: argparse.Namespace) -> int:
    # The positional is the TASK id (consistent with start/complete/yield/abandon,
    # which all take the task id first); ``--task`` is kept as a back-compat alias.
    # The owner/worker id -- rarely needed, since identity resolves from CWD -- is
    # the explicit ``--worker``/``--as`` flag. This removes the old ambiguity where
    # a bare ``claim <id>`` bound <id> to the worker slot and silently leased an
    # arbitrary task under it.
    task_id = args.task_id or args.task
    if args.task_id and args.task and args.task_id != args.task:
        print(
            f"agent-dispatch claim: conflicting task ids (positional '{args.task_id}' "
            f"vs --task '{args.task}'). Pass the task id once.",
            file=sys.stderr,
        )
        return 2
    machine, worktree = _identity(args)
    repo = _scope_repo(args)
    if not repo:
        print(_REPO_UNRESOLVED, file=sys.stderr)
        return 2
    with _client(args) as c:
        task = c.claim(
            worker_id=args.worker_id,
            capabilities=args.capability or [],
            repo=repo,
            machine=machine,
            worktree=worktree,
            task_id=task_id,
            lease_seconds=args.lease_seconds,
            evaluation=getattr(args, "evaluation", False),
        )
    if task is None:
        print("no claimable task", file=sys.stderr)
        return 3
    return _emit(_enrich(task))


def _cmd_worktree_status(args: argparse.Namespace) -> int:
    machine, worktree = _identity(args)
    if not machine or not worktree:
        print(
            "agent-dispatch: could not resolve worktree identity — pass --machine and --worktree "
            "(agent-worktrees not found, or not inside a worktree)",
            file=sys.stderr,
        )
        return 2
    repo = _scope_repo(args)
    if not repo:
        print(_REPO_UNRESOLVED, file=sys.stderr)
        return 2
    with _client(args) as c:
        inbox = c.mine(machine, worktree, repo=repo)
    return _emit(_enrich({"machine": machine, "worktree": worktree, "repo": repo, **inbox}))


def _cmd_show(args: argparse.Namespace) -> int:
    with _client(args) as c:
        task = c.get(args.task_id)
        # Surface the accumulated append-only progress log alongside the task, so
        # a (re-)embodied worker reads its goal, done-criteria, AND recorded
        # progress in one call and resumes from it rather than restarting.
        task = dict(task)
        task["progress_log"] = c.progress_log(args.task_id)
    from . import tracking

    return _emit(tracking.enrich_task(_enrich(task)))


def _split_owner(owner: str | None) -> tuple[str | None, str | None]:
    """Split a ``machine/worktree`` worker id into its parts.

    The inverse of ``queue.worker_id_for``. A malformed or missing value yields
    ``(None, None)`` (or ``(value, None)`` when it has no ``/``), never raising,
    so a reverse-lookup read can't crash on a stray owner string.
    """
    if not owner:
        return (None, None)
    machine, sep, worktree = owner.partition("/")
    if not sep:
        return (owner or None, None)
    return (machine or None, worktree or None)


def _cmd_claimant(args: argparse.Namespace) -> int:
    """task -> claiming worktree: resolve which worktree owns a task.

    The inbound-ledger reverse of ``worktree-status`` (worktree -> its tasks).
    Returns a focused record: the actual claimant (``owner`` = machine/worktree,
    once the task is claimed/started), or -- for a not-yet-claimed task -- the
    pinned ``target`` worktree, with ``claimed`` distinguishing the two.
    """
    with _client(args) as c:
        task = c.get(args.task_id)
    status = task.get("status")
    owner = task.get("owner")
    claimed = bool(owner) and status in ("claimed", "started", "completed")
    if claimed:
        machine, worktree = _split_owner(owner)
        source = "owner"
    else:
        # Not yet claimed -- surface the pin (intended claimant), if any.
        machine = task.get("target_machine")
        worktree = task.get("target_worktree")
        source = "target" if worktree else None
    result = {
        "task_id": args.task_id,
        "status": status,
        "claimed": claimed,
        "worker_id": owner if claimed else None,
        "machine": machine,
        "worktree": worktree,
        "resolved_from": source,
        "owner_session_id": task.get("owner_session_id"),
        "repo": task.get("repo"),
    }
    return _emit(_enrich(result))


def _simple(method: str, *arg_names: str):
    """Build a handler that forwards positional args to a client method."""

    def handler(args: argparse.Namespace) -> int:
        with _client(args) as c:
            result = getattr(c, method)(*[getattr(args, n) for n in arg_names])
        return _emit(result)

    return handler


def _cmd_yield(args: argparse.Namespace) -> int:
    worker_id = _resolve_owner(args, verb="yield")
    if worker_id is None:
        return 2
    exclude = args.exclude
    if not exclude and getattr(args, "exclude_self", None):
        machine, worktree = _identity(args)
        if args.exclude_self == "worktree" and worktree:
            exclude = f"worktree:{worktree}"
        elif args.exclude_self == "machine" and machine:
            exclude = f"machine:{machine}"
    with _client(args) as c:
        return _emit(c.yield_task(args.task_id, worker_id, note=args.note, exclude=exclude))


def _owner_from_identity(args: argparse.Namespace) -> str | None:
    """Compose the canonical ``machine/worktree`` owner from the CWD identity.

    Mirrors the coordinator's ``worker_id_for`` so a worker can address its own
    task without typing its owner: ``complete <id>`` (no owner) resolves the same
    ``machine/worktree`` pair it claimed under. Returns None when identity can't
    be resolved (no agent-worktrees, outside a worktree).
    """
    machine, worktree = _identity(args)
    if machine and worktree:
        return f"{machine}/{worktree}"
    return None


def _resolve_owner(args: argparse.Namespace, *, verb: str) -> str | None:
    """Resolve the acting worker's owner for a lease-holding verb.

    Prefers an explicit positional ``worker_id``; otherwise composes
    ``machine/worktree`` from the CWD identity -- the symmetry that lets an
    embodied/taken-over worker drive its whole lifecycle
    (``claim``/``start``/``complete``/``yield``) under its **worktree identity**
    without typing an owner, so the task's owner stays ``machine/worktree`` and
    live-session tracking can join it (see :mod:`tracking`). Prints guidance and
    returns None when neither is available.
    """
    worker_id = getattr(args, "worker_id", None) or _owner_from_identity(args)
    if not worker_id:
        print(
            f"agent-dispatch: could not resolve the owner for {verb}. Pass the "
            f"owner positionally (`{verb} <id> <owner>`) or run inside the "
            "owning worktree so machine/worktree resolves.",
            file=sys.stderr,
        )
    return worker_id


def _cmd_start(args: argparse.Namespace) -> int:
    worker_id = _resolve_owner(args, verb="start")
    if worker_id is None:
        return 2
    with _client(args) as c:
        return _emit(c.start(args.task_id, worker_id))


def _cmd_progress(args: argparse.Namespace) -> int:
    worker_id = _resolve_owner(args, verb="progress")
    if worker_id is None:
        return 2
    with _client(args) as c:
        return _emit(
            c.progress(
                args.task_id,
                worker_id,
                phase=args.phase or "",
                summary=args.summary,
                blocker=args.blocker,
                pr=args.pr,
            )
        )


def _cmd_card_set(args: argparse.Namespace) -> int:
    """Attach a card to a task the worker owns (the human-in-the-loop 'I need
    input' post). Parses the ``--request-input`` form spec, builds the card, and
    posts it; a card with a form marks the task awaiting-steer."""
    from . import steering

    worker_id = _resolve_owner(args, verb="card set")
    if worker_id is None:
        return 2
    try:
        fields = steering.parse_request_input(getattr(args, "request_input", None))
    except steering.SteeringError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2
    body = args.body
    if body and body.startswith("@"):
        body = Path(body[1:]).expanduser().read_text(encoding="utf-8")
    card = steering.build_card(
        title=args.title,
        status=args.status,
        link=args.link,
        body=body,
        request_input=fields,
    )
    with _client(args) as c:
        return _emit(c.set_card(args.task_id, worker_id, card=card))


def _cmd_card_show(args: argparse.Namespace) -> int:
    """Show a task's current card (plus its steer inbox)."""
    with _client(args) as c:
        task = c.get(args.task_id)
        steers = c.steer_log(args.task_id)
    return _emit(
        {
            "task_id": args.task_id,
            "card": task.get("card"),
            "awaiting_steer": task.get("awaiting_steer"),
            "steers": steers,
        }
    )


def _cmd_steer(args: argparse.Namespace) -> int:
    """Submit an answer and have the coordinator resume the owning worktree."""
    fields: dict[str, str] = {}
    for item in args.field or []:
        key, sep, value = item.partition("=")
        if not sep:
            print(
                f"agent-dispatch: --field must be key=value (got {item!r})",
                file=sys.stderr,
            )
            return 2
        fields[key.strip()] = value
    sender = args.sender or _owner_from_identity(args)
    with _client(args) as c:
        result = c.steer(
            args.task_id,
            fields=fields,
            sender=sender,
            wake=args.wake,
            message=args.message,
        )
        coordinator_reported_wake = "steer_woken" in result
        woken = result.pop("steer_woken", None)
        owner = result.get("owner")
        # Compatibility with a coordinator from before wake delivery moved
        # server-side: old Pydantic models ignore the new request fields and old
        # responses omit ``steer_woken``.
        if args.wake and owner and not coordinator_reported_wake:
            from . import bridge

            woken = bridge.resume_steered_owner(owner, args.task_id, args.message)
    return _emit({"task": result, "woken": woken})


def _cmd_steer_take(args: argparse.Namespace) -> int:
    """Consume the next pending steer for a task the worker owns (the wake-side
    read a resumed reviewer runs to learn what the operator answered)."""
    worker_id = _resolve_owner(args, verb="steer take")
    if worker_id is None:
        return 2
    with _client(args) as c:
        return _emit(c.steer_take(args.task_id, worker_id))


def _cmd_focus(args: argparse.Namespace) -> int:
    # worktree-status-core convergence: a worktree's "focus" IS its status-core
    # summary on the worktree record (the single owning layer). There is no
    # parallel focus store -- writes forward through the `agent-worktrees status`
    # verb (single-writer contract) and reads DERIVE from `agent-worktrees list
    # --json`. `progress` stays task-scoped; only this worktree-scoped focus
    # converges.
    from .identity import aw_list_records, aw_set_summary

    def _focus_row(w: dict) -> dict:
        return {
            "machine": w.get("machine"),
            "worktree": w.get("id"),
            "focus": (w.get("summary") or "").strip(),
            "updated_at": w.get("status_note_at"),
        }

    if args.list:
        rows = [
            _focus_row(w)
            for w in aw_list_records(machine=args.machine)
            if (w.get("summary") or "").strip()
        ]
        return _emit(rows)

    machine, worktree = _identity(args)
    if not machine or not worktree:
        print(
            "agent-dispatch: could not resolve this worktree's identity — run "
            "inside a worktree, or pass --machine and --worktree.",
            file=sys.stderr,
        )
        return 2

    if not args.focus_text:
        # Show this worktree's current focus (its status-core summary).
        mine = [w for w in aw_list_records(machine=machine)
                if w.get("id") == worktree]
        return _emit(_focus_row(mine[0]) if mine and (mine[0].get("summary") or "").strip()
                     else {})

    # Write-through to the status core (never a parallel store). The write
    # always targets the CWD worktree via the `agent-worktrees status` verb.
    if not aw_set_summary(args.focus_text):
        print(
            "agent-dispatch: focus write-through failed (agent-worktrees status "
            "unavailable, or not inside a worktree).",
            file=sys.stderr,
        )
        return 2
    return _emit({
        "machine": machine, "worktree": worktree,
        "focus": args.focus_text.strip(),
    })


def _cmd_complete(args: argparse.Namespace) -> int:
    # Owner is optional: a worker that claimed under its CWD identity can
    # complete with just the task id -- we resolve the same machine/worktree
    # owner. This is what lets a taken-over successor finish a handoff task with
    # one clean command (`agent-dispatch complete <id>`) once the goal is met.
    worker_id = _resolve_owner(args, verb="complete")
    if worker_id is None:
        return 2
    with _client(args) as c:
        return _emit(c.complete(args.task_id, worker_id, result_ref=args.result_ref))


def _cmd_abandon(args: argparse.Namespace) -> int:
    permitted = args.permit
    reason = args.reason
    duplicate_of = getattr(args, "duplicate_of", None)
    if duplicate_of:
        # A duplicate is self-justifying: retiring it is permitted, and the
        # dedup reference is folded into the reason so it lands in the audit
        # trail (never a silent drop).
        permitted = True
        dedup_note = f"duplicate of {duplicate_of}"
        reason = f"{reason}; {dedup_note}" if reason else dedup_note
    with _client(args) as c:
        result = c.abandon(
            args.task_id, worker_id=args.worker_id, permitted=permitted, reason=reason
        )
    if getattr(args, "resolve", False):
        # Surface the drive-the-worktree-to-resolution plan alongside the abandon
        # so the required unwind is an explicit, actionable expectation -- never a
        # silent one. It is NOT auto-run: the destructive unwind stays worker-
        # driven (`agent-dispatch resolve --execute`), on the worker's OWN tree.
        from .resolution import plan_resolution

        plan = plan_resolution(
            "abandoned",
            base=getattr(args, "base", None),
            source_ref=duplicate_of,
            reason=reason,
        )
        result = {"abandon": result, "resolution": plan.to_dict()}
    return _emit(result)


def _browse_peer(args: argparse.Namespace, subcommand: str, *, repo: str | None = None) -> int:
    """Peer-queue browse (Phase 8 Slice 8c): run the read command on the remote
    ``--machine`` over the SSH mesh and stream its JSON straight through.

    The remote CLI reads *its own* loopback coordinator (and, via 8b, enriches
    against its own local bridge), so the output is exactly what a local run on
    the peer would produce.
    """
    from . import remote_dispatch

    argv = remote_dispatch.build_remote_browse_argv(subcommand, args, repo=repo)
    try:
        result = remote_dispatch.browse_remote(args.machine, argv)
    except remote_dispatch.RemoteDispatchUnavailable as exc:
        print(
            f"agent-dispatch: peer-queue browse of {args.machine!r} unavailable "
            f"({exc})",
            file=sys.stderr,
        )
        return 2
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.returncode != 0:
        diagnosis = remote_dispatch.diagnose_remote_failure(
            args.machine, result.returncode, result.stderr
        )
        print(f"agent-dispatch: {diagnosis}", file=sys.stderr)
    return result.returncode


def _cmd_list(args: argparse.Namespace) -> int:
    repo = _scope_repo(args)
    if not repo:
        print(_REPO_UNRESOLVED, file=sys.stderr)
        return 2
    from . import remote_dispatch

    if remote_dispatch.is_peer_machine(getattr(args, "machine", None)):
        return _browse_peer(args, "list", repo=repo)
    with _client(args) as c:
        tasks = c.list(
            repo=repo,
            status=args.status,
            target_machine=args.target_machine,
            target_repo=args.target_repo,
            label=args.label,
            limit=args.limit,
        )
    from . import tracking

    return _emit(tracking.enrich_tasks(_enrich(tasks)))


#: The picker Tasks-pivot **board** groups, in the operator's priority order:
#: what needs your attention first (a task blocked awaiting your steer), then the
#: pickable/in-flight lifecycle, then recently-finished tasks. The tuple index is
#: the sort key; the string is the pivot section header. ``--board`` tags each
#: task with its group and orders by this sequence so the picker's first-seen
#: grouping renders the sections in exactly this order.
_BOARD_GROUPS = ("Blocked", "Proposed", "Queued", "Started", "Completed", "Abandoned")
_BOARD_TERMINAL = frozenset({"Completed", "Abandoned"})


def _board_group(task: dict) -> str:
    """The display group for a task on the picker board (see ``_BOARD_GROUPS``).

    A **terminal** status (completed / abandoned / dead_letter) wins first -- a
    task can carry a stale ``awaiting_steer`` flag after being abandoned while
    blocked, and a finished task is never "Blocked". Otherwise ``awaiting_steer``
    (a live task needing the operator's steer) wins over the raw lifecycle state,
    then proposed/queued, else any other owned in-flight state reads as
    *Started*."""
    st = task.get("status")
    if st == "completed":
        return "Completed"
    if st in ("abandoned", "dead_letter"):
        return "Abandoned"
    if task.get("awaiting_steer"):
        return "Blocked"
    if st == "proposed":
        return "Proposed"
    if st == "queued":
        return "Queued"
    return "Started"


def _board_sort_key(task: dict) -> tuple:
    grp = _board_group(task)
    prio = _BOARD_GROUPS.index(grp) if grp in _BOARD_GROUPS else len(_BOARD_GROUPS)
    # Within a group, surface the most recent activity first.
    ts = task.get("updated_at") or task.get("created_at") or 0
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        ts = 0.0
    return (prio, -ts)


def _board_keep(task: dict, cutoff: float) -> bool:
    """Keep an active task always; keep a terminal (completed/abandoned) task only
    when its terminal timestamp is at/after ``cutoff`` (the recency window), so the
    board shows *recently* finished work without unbounded growth."""
    if _board_group(task) not in _BOARD_TERMINAL:
        return True
    ts = task.get("completed_at") or task.get("updated_at") or 0
    try:
        return float(ts) >= cutoff
    except (TypeError, ValueError):
        return False


def _cmd_inbox(args: argparse.Namespace) -> int:
    """Machine-scoped, cross-lane view of pickable tasks.

    Unlike ``list`` (which scopes to the calling repo's lane), ``inbox`` asks
    the coordinator for tasks across *every* lane and keeps those this machine
    can pick up: a matching ``target_machine`` plus machine-agnostic tasks
    (``target_machine`` unset). Defaults to ``proposed`` -- the "available to
    start" state. Each entry carries ``target_worktree``, ``affinity``,
    ``labels`` and the display-only ``repo_name`` so a consumer (e.g. the
    worktree picker's task pivot) can group by worktree and badge handoffs.

    With ``--machine Y`` naming a *remote* peer, the inbox is read from **Y's
    own coordinator** over the SSH mesh (Phase 8 Slice 8c) -- what Y can actually
    pick up -- rather than filtering the local queue.
    """
    from . import remote_dispatch

    if remote_dispatch.is_peer_machine(args.machine):
        return _browse_peer(args, "inbox")
    machine = args.machine
    if not machine:
        from .identity import resolve_identity

        machine = resolve_identity()[0]
    if not machine:
        print(
            "agent-dispatch: could not resolve this machine — pass --machine "
            "(agent-worktrees not found, or not inside a worktree)",
            file=sys.stderr,
        )
        return 2
    # --board: the status-grouped picker board. Widens the fetch across the whole
    # visible lifecycle (proposed -> in-flight -> recently terminal), tags each
    # task with a display `group`, drops terminal tasks older than the recency
    # window, and orders by group priority so the picker renders the sections
    # Blocked -> Proposed -> Queued -> Started -> Completed -> Abandoned. Overrides
    # --awaiting-steer / --status.
    if getattr(args, "board", False):
        import time as _time

        from .queue import machine_matches

        status = "proposed,queued,claimed,started,completed,abandoned,dead_letter"
        with _client(args) as c:
            tasks = c.list(repo=None, status=status, label=args.label, limit=args.limit)
        inbox = [t for t in tasks if machine_matches(t.get("target_machine"), machine)]
        cutoff = _time.time() - max(0, getattr(args, "recent_mins", 120)) * 60
        inbox = [t for t in inbox if _board_keep(t, cutoff)]
        inbox.sort(key=_board_sort_key)
        inbox = [{**t, "group": _board_group(t)} for t in inbox]
        return _emit(_enrich(inbox))
    # --awaiting-steer widens the fetch to the owned states (a task blocked on
    # operator steering is `claimed`/`started`, not a filterable "held" -- HELD
    # is a derived category), then keeps only the *pickable* (`proposed`) rows
    # plus any *awaiting-steer* row. This is the picker steer surface's read:
    # "what I can start + what needs my answer", without the rest of the owned
    # in-progress queue.
    steer_only = getattr(args, "awaiting_steer", False)
    status = "proposed,claimed,started" if steer_only else args.status
    with _client(args) as c:
        tasks = c.list(repo=None, status=status, label=args.label, limit=args.limit)
    from .queue import machine_matches

    inbox = [t for t in tasks if machine_matches(t.get("target_machine"), machine)]
    if steer_only:
        inbox = [
            t for t in inbox
            if t.get("status") == "proposed" or t.get("awaiting_steer")
        ]
    return _emit(_enrich(inbox))


def _cmd_find(args: argparse.Namespace) -> int:
    repo = _scope_repo(args)
    if not repo:
        print(_REPO_UNRESOLVED, file=sys.stderr)
        return 2
    with _client(args) as c:
        return _emit(_enrich(c.find(args.query, repo=repo, limit=args.limit)))


def _cmd_sweep(args: argparse.Namespace) -> int:
    repo = _scope_repo(args)
    if not repo:
        print(_REPO_UNRESOLVED, file=sys.stderr)
        return 2
    with _client(args) as c:
        return _emit(_enrich(c.sweep(repo=repo, limit=args.limit)))


def _cmd_watch(args: argparse.Namespace) -> int:
    with _client(args) as c:
        try:
            for event in c.stream_events():
                json.dump(event, sys.stdout)
                sys.stdout.write("\n")
                sys.stdout.flush()
        except KeyboardInterrupt:
            return 0
    return 0


def _cmd_payload(args: argparse.Namespace) -> int:
    with _client(args) as c:
        result = c.payload(args.task_id)
    if args.raw:
        content = result.get("payload")
        if content is None:
            print(
                f"agent-dispatch: task {args.task_id} has no resolvable payload",
                file=sys.stderr,
            )
            return 4
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")
        return 0
    return _emit(result)


def _consume_already_spent(task_id: str, task: dict) -> int:
    """Refuse to replay a spent handoff baton.

    Prints a clear STOP notice (read by the successor agent in place of the
    brief) and returns exit ``3`` so programmatic callers can detect the
    already-consumed no-op. The work is done; a re-seeded successor must not
    redo it.
    """
    result_ref = task.get("result_ref")
    result_str = f" (result: {result_ref})" if result_ref else ""
    print(
        f"[agent-dispatch] Handoff task {task_id} is already COMPLETED"
        f"{result_str}.\n"
        f"This handoff was already picked up and its work finished -- NOT "
        f"replaying the brief. Do NOT redo this work; end your turn.\n"
        f"If this is unexpected, inspect with: agent-dispatch show {task_id}"
    )
    print(
        f"agent-dispatch: handoff {task_id} already consumed (completed); "
        f"not replayed",
        file=sys.stderr,
    )
    return 3


def _cmd_consume(args: argparse.Namespace) -> int:
    """Resume-and-consume a handoff and print its payload content.

    Two completion modes:

    - **Baton (default):** drive the task all the way to ``completed`` in one
      shot -- loading the brief IS consuming the baton, so a handoff is marked
      completed the *moment* it is picked up (the classic quick-baton resume:
      /resume-handoff, a hand-pasted seed). The continuation *work* is tracked
      by its effort/issue, not this task.
    - **Deferred (``--defer-complete``):** approve -> claim -> **start** the task
      (take ownership, mark it in-progress) and print the brief, but do **not**
      complete it. This is the *takeover* pickup: a dispatched/embodied successor
      loads the brief, works the task, and calls ``agent-dispatch complete
      <id>`` **explicitly** only when it reaches the handoff's goal -- so
      ``completed`` means *the work is done*, not *the baton was handed over*.

    Transitions are best-effort and idempotent: an already-advanced task just
    prints its payload (never an error), and a task the caller can't take
    ownership of is still read and printed.

    **Replay debounce (a *completed handoff* is spent).** A handoff is a baton:
    once it has been picked up and its work driven to ``completed``, re-consuming
    it must NOT re-deliver the brief as if it were fresh. A live-cutover (or any
    re-seeded successor) that re-runs ``consume <id>`` on an already-completed
    handoff would otherwise redo finished work. So a completed *handoff* is
    refused here with a clear stop notice (exit ``3``) instead of its payload --
    the single chokepoint every task-backed resume seed flows through. A
    still-in-flight handoff (``started`` -- e.g. a legitimate takeover recovery)
    is unaffected; only ``completed`` is treated as spent.
    """
    task_id = args.task_id
    defer = getattr(args, "defer_complete", False)
    machine, worktree = _identity(args)
    try:
        repo = _scope_repo(args)
    except Exception:  # lane resolution is best-effort here -- still print payload
        repo = None
    with _client(args) as c:
        try:
            task = c.get(task_id)
        except DispatchError as exc:
            print(f"agent-dispatch: {exc}", file=sys.stderr)
            return 1
        status = task.get("status")
        # Debounce a spent baton: a *completed handoff* is never replayed.
        is_handoff = ("handoff" in (task.get("labels") or [])) or (
            task.get("source") == "context-handoff"
        )
        if is_handoff and status == "completed":
            return _consume_already_spent(task_id, task)
        if status not in ("completed", "abandoned"):
            owner: str | None = None
            if status == "proposed":
                try:
                    c.approve(task_id)
                    status = "queued"
                except DispatchError:
                    pass
            if status in ("queued", "proposed"):
                try:
                    claimed = c.claim(
                        worker_id=args.worker_id,
                        repo=repo,
                        machine=machine,
                        worktree=worktree,
                        task_id=task_id,
                    )
                    owner = (claimed or {}).get("owner")
                except DispatchError:
                    owner = None
            elif status in ("claimed", "started"):
                owner = task.get("owner")
            if owner:
                try:
                    c.start(task_id, owner)
                except DispatchError:
                    pass
                # Deferred pickup stops at 'started': the successor completes
                # explicitly when the work is done. Baton mode completes now.
                if not defer:
                    result_ref = args.result_ref or f"consumed:{worktree or 'successor'}"
                    try:
                        c.complete(task_id, owner, result_ref=result_ref)
                    except DispatchError:
                        pass
        result = c.payload(task_id)
    content = result.get("payload")
    if content is None:
        print(
            f"agent-dispatch: task {task_id} has no resolvable payload",
            file=sys.stderr,
        )
        return 4
    sys.stdout.write(content)
    if not content.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import serve_stdio

    serve_stdio()
    return 0


def _cmd_schedule(args: argparse.Namespace) -> int:
    from .producers import schedule

    cmd = args.schedule_command

    if cmd == "serve":
        _url, _token = _resolve_client_target(args)
        if getattr(args, "registry", False):
            if not args.lease_scope or not args.holder:
                raise SystemExit(
                    "schedule serve --registry: --lease-scope and --holder are required"
                )
            schedule.serve_registry(
                url=_url,
                token=_token,
                interval=args.interval,
                lease_scope=args.lease_scope,
                holder=args.holder,
                holder_session=getattr(args, "holder_session", None),
                lease_ttl=getattr(args, "lease_ttl", None),
            )
        else:
            if not args.spec:
                raise SystemExit("schedule serve: pass a SPEC path or --registry")
            schedule.serve(args.spec, url=_url, token=_token, interval=args.interval)
        return 0

    if cmd == "tick":
        with _client(args) as c:
            if getattr(args, "registry", False):
                result = schedule.run_registry_tick(c)
            else:
                if not args.spec:
                    raise SystemExit("schedule tick: pass a SPEC path or --registry")
                result = schedule.run_tick(c, schedule.load_spec(args.spec))
        return _emit({
            "created": [_enrich(t) for t in result["created"]],
            "errors": result["errors"],
        })

    if cmd == "register":
        with _client(args) as c:
            result = schedule.register_from_spec(c, schedule.load_spec(args.spec))
        return _emit(result)

    if cmd == "list":
        with _client(args) as c:
            return _emit(c.list_schedules(include_paused=not args.active))

    if cmd == "inspect":
        import time as _time

        with _client(args) as c:
            rec = c.get_schedule(args.id)
            try:
                occ = schedule.due_occurrences(rec["entry"], now=_time.time())
            except schedule.ScheduleError:
                occ = []
            lease = c.get_schedule_lease(args.id)
        return _emit({"schedule": rec, "next_occurrences": occ, "lease": lease})

    if cmd == "remove":
        with _client(args) as c:
            return _emit(c.remove_schedule(args.id))

    if cmd in ("pause", "resume"):
        with _client(args) as c:
            return _emit(c.set_schedule_paused(args.id, cmd == "pause"))

    if cmd == "lease-list":
        with _client(args) as c:
            return _emit(c.list_schedule_leases())

    if cmd == "lease-show":
        with _client(args) as c:
            return _emit(c.get_schedule_lease(args.scope))

    if cmd == "lease-acquire":
        with _client(args) as c:
            return _emit(
                c.acquire_schedule_lease(
                    args.scope,
                    args.holder,
                    holder_session=args.holder_session,
                    ttl=args.ttl,
                )
            )

    if cmd == "lease-release":
        with _client(args) as c:
            return _emit(
                c.release_schedule_lease(args.scope, args.holder, force=args.force)
            )

    raise SystemExit(f"unknown schedule command: {cmd!r}")


def _cmd_emitter(args: argparse.Namespace) -> int:
    from .producers import emitter

    spec = emitter.load_spec(args.spec)
    if args.emitter_command == "serve":
        url, token = _resolve_client_target(args)
        emitter.serve(
            args.spec,
            url=url,
            token=token,
            holder=args.holder,
        )
        return 0
    if args.emitter_command == "tick":
        with _client(args) as client:
            return _emit(emitter.run_tick(client, spec, holder=args.holder))
    raise SystemExit(f"unknown emitter command: {args.emitter_command!r}")


def _cmd_webhook(args: argparse.Namespace) -> int:
    from .producers import webhook

    config = webhook.load_config(args.config) if args.config else {}
    if args.url:
        config["url"] = args.url
    if args.token:
        config["coordinator_token"] = args.token
    webhook.serve(config, host=args.host, port=args.port)
    return 0


def _parse_label_max_attempts(items: list[str] | None) -> dict[str, int]:
    """Parse repeated ``LABEL=N`` flags into a ``{label: max_attempts}`` map.

    Raises ``SystemExit`` on a malformed entry (bad shape or non-int N) so the
    supervisor fails loudly at startup rather than silently ignoring a policy.
    """
    out: dict[str, int] = {}
    for raw in items or []:
        label, sep, num = str(raw).partition("=")
        label = label.strip()
        if not sep or not label:
            raise SystemExit(
                f"--label-max-attempts expects LABEL=N, got {raw!r}"
            )
        try:
            out[label] = max(0, int(num.strip()))
        except ValueError:
            raise SystemExit(
                f"--label-max-attempts: N must be an integer, got {num!r}"
            )
    return out


def _registration_scope(args: argparse.Namespace) -> tuple[str | None, str]:
    """Resolve the (machine, env) a registration is scoped to.

    ``--machine`` / ``--env`` win; otherwise the machine is this host's resolved
    alias and the env is ``AGENT_DISPATCH_ENV`` (default ``"default"``). This is
    the *one supervisor per machine-and-environment* the registration binds to.
    """
    import os

    from . import remote_dispatch

    machine = getattr(args, "machine", None) or remote_dispatch.local_machine()
    env = (
        getattr(args, "env", None)
        or os.environ.get("AGENT_DISPATCH_ENV")
        or "default"
    )
    return machine, env


def _build_registration_spec(args: argparse.Namespace) -> dict:
    """Assemble the ``spec`` dict a registration stores from the register args.

    An explicit ``--spec`` (inline JSON or ``@path``) is used verbatim for any
    kind; otherwise a ``supervised-lane`` spec is built from the convenience lane
    flags (repo/labels/limits/evaluator) so the singleton daemon can later
    reconstruct the supervise invocation from the stored row.
    """
    raw = getattr(args, "spec", None)
    if raw:
        text = raw
        if raw.startswith("@"):
            try:
                text = Path(raw[1:]).expanduser().read_text(encoding="utf-8")
            except OSError as exc:
                raise SystemExit(
                    f"supervise register: could not read --spec file "
                    f"{raw[1:]!r}: {exc}"
                ) from exc
        try:
            spec = json.loads(text)
        except ValueError as exc:
            raise SystemExit(f"supervise register: bad --spec JSON: {exc}") from exc
        if not isinstance(spec, dict):
            raise SystemExit("supervise register: --spec must be a JSON object")
        return spec

    kind = getattr(args, "kind", None) or "supervised-lane"
    if kind not in ("supervised-lane", "evaluator"):
        raise SystemExit(
            f"supervise register: --spec is required for kind {kind!r}"
        )

    all_repos = bool(getattr(args, "all_repos", False))
    spec: dict = {}
    if all_repos:
        spec["all_repos"] = True
    else:
        repo = _scope_repo(args)
        if not repo:
            raise SystemExit(
                "supervise register: could not resolve a lane; pass --repo or "
                "--all-repos"
            )
        spec["repo"] = repo
    labels = [label for label in (getattr(args, "label", None) or []) if label]
    if labels:
        spec["labels"] = labels
    spec["max_concurrent"] = getattr(args, "max_concurrent", 1)
    spec["max_attempts"] = getattr(args, "max_attempts", 3)
    lma = _parse_label_max_attempts(getattr(args, "label_max_attempts", None))
    if lma:
        spec["label_max_attempts"] = lma
    # Embody backend default is headless; record it (+ any per-label overrides) so
    # the daemon rebuilds the same lane. Omit the default to keep older specs stable.
    backend = getattr(args, "embody_backend", None) or "headless"
    if backend != "headless":
        spec["embody_backend"] = backend
    headless = [
        label for label in (getattr(args, "headless_label", None) or []) if label
    ]
    if headless:
        spec["headless_labels"] = headless
    cli = [label for label in (getattr(args, "cli_label", None) or []) if label]
    if cli:
        spec["cli_labels"] = cli
    if getattr(args, "headless_agent", None):
        spec["headless_agent"] = args.headless_agent
    if getattr(args, "evaluator", None):
        if kind != "evaluator":
            raise SystemExit(
                "supervise register: --evaluator is only valid with "
                "--kind evaluator (a supervised-lane does not run an evaluator)"
            )
        spec["evaluator"] = args.evaluator
    spec["interval"] = getattr(args, "interval", 30.0)
    if kind == "evaluator" and not spec.get("evaluator"):
        raise SystemExit(
            "supervise register --kind evaluator: pass --evaluator <spec-path> "
            "(or --spec with an inline 'evaluator_spec')"
        )
    return spec


def _cmd_supervise_register(args: argparse.Namespace) -> int:
    """``supervise register`` -- add a durable registration and RETURN its handle.

    This is the *supervise-registers-and-returns* behavior: registering supervised
    work writes a registration row and completes, emitting the registration info
    back to the caller, instead of becoming the foreground loop. The singleton
    supervisor daemon (a later increment) is what runs the registered unit.
    """
    kind = getattr(args, "kind", None) or "supervised-lane"
    spec = _build_registration_spec(args)
    machine, env = _registration_scope(args)
    with _client(args) as c:
        rec = c.register_registration(
            kind,
            spec,
            reg_id=getattr(args, "id", None),
            machine=machine,
            env=env,
        )
    if getattr(args, "ensure", False):
        rec = {**rec, "daemon": _ensure_supervisor_daemon(args, machine, env)}
    return _emit(rec)


def _cmd_supervise_status(args: argparse.Namespace) -> int:
    """``supervise status <id>`` -- query a registration by its handle."""
    with _client(args) as c:
        return _emit(c.get_registration(args.id))


def _cmd_supervise_list(args: argparse.Namespace) -> int:
    """``supervise list`` -- list registrations on this (or a filtered) scope."""
    machine = getattr(args, "machine", None)
    env = getattr(args, "env", None)
    with _client(args) as c:
        return _emit(
            c.list_registrations(
                kind=getattr(args, "kind", None),
                machine=machine,
                env=env,
                include_paused=not getattr(args, "active", False),
            )
        )


def _cmd_supervise_remove(args: argparse.Namespace) -> int:
    """``supervise remove <id>`` -- drop a registration by its handle."""
    with _client(args) as c:
        return _emit(c.remove_registration(args.id))


def _spawn_supervisor_daemon_detached(machine: str | None, env: str) -> bool:
    """Best-effort: launch the singleton supervisor daemon as a detached child.

    Runs ``agent-dispatch supervise serve`` for this (machine, env) fully detached
    so it outlives this CLI process. If a daemon is already running the new child's
    single-instance election stands it down cleanly (pin-not-failover), so a double
    launch is self-correcting. Returns whether the spawn was issued.
    """
    from .procutil import detached_kwargs, runtime_root, windowless_python

    argv = [windowless_python(sys.executable), "-m", "agent_dispatch", "supervise", "serve"]
    if machine:
        argv += ["--machine", machine]
    if env:
        argv += ["--env", env]
    try:
        subprocess.Popen(  # noqa: S603
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Launch from the runtime root, never an inherited (possibly payload) CWD.
            cwd=str(runtime_root()),
            **detached_kwargs(),
        )
        return True
    except OSError as exc:  # pragma: no cover -- launch failure is environmental
        print(f"supervise register: could not ensure daemon: {exc}", file=sys.stderr)
        return False


def _ensure_supervisor_daemon(args: argparse.Namespace, machine: str | None, env: str) -> dict:
    """Ensure a singleton supervisor daemon is running for this (machine, env).

    Checks the supervisor lease first; if a daemon already holds it, this is a
    no-op. Otherwise it launches one detached. Best-effort and fail-soft -- a
    failure to ensure never fails the register call.
    """
    from .config import run_dir
    from .single_instance import is_locked, lock_path_for
    from .supervisor_daemon import supervisor_lease_scope

    scope = supervisor_lease_scope(machine, env)
    try:
        if is_locked(lock_path_for(run_dir(), scope)):
            return {"ensured": False, "reason": "already running"}
    except OSError:  # noqa: BLE001 -- fail-soft; fall through to a spawn attempt
        pass
    spawned = _spawn_supervisor_daemon_detached(machine, env)
    return {"ensured": spawned, "reason": "spawned" if spawned else "spawn failed"}


def _cmd_supervise_serve(args: argparse.Namespace) -> int:
    """``supervise serve`` -- run the singleton supervisor daemon (foreground).

    One master per (machine, env): reads the registration registry and runs each
    active registration in its own subprocess, reconciling on every tick.
    Single-instance-guarded -- a second daemon for the same scope stands down.
    """
    from .supervisor_daemon import SupervisorDaemon

    # Never hold the Copilot plugin payload dir as CWD (Windows locks it against
    # `copilot plugin update`); this daemon is lazy-started and inherits the
    # launching session's CWD. Relocate before the long-lived loop.
    from . import procutil
    procutil.relocate_off_payload()

    machine, env = _registration_scope(args)

    if machine is None and not getattr(args, "no_declared", False):
        # Fail-loud companion to the fail-closed reconcile: an unidentified host
        # will SKIP every machine-pinned declaration (it cannot confirm membership),
        # so a discovered machine-scoped pool would silently never run. Surface it
        # so the operator can pass --machine (or fix host identity) -- aperture-labs
        # #5001.
        print(
            "agent-dispatch supervise serve: WARNING -- could not resolve this "
            "host's machine name; machine-scoped declarations will be SKIPPED "
            "(machine-agnostic ones still run). Pass --machine <alias> to scope "
            "this daemon.",
            file=sys.stderr,
        )

    declared_source = None
    if not getattr(args, "no_declared", False):
        from . import registrar_discovery

        if getattr(args, "legacy_env", False):
            # Back-compat bridge: pointer-discovered declarations PLUS the host's
            # legacy supervisor.env / supervisors/*.env profiles, deduped by name
            # (a first-class declaration wins). Lets a host switch its supervisor
            # unit to `serve` without dropping its existing profiles mid-migration.
            declared_source = registrar_discovery.discover_with_legacy
        else:
            declared_source = registrar_discovery.discover
    elif getattr(args, "legacy_env", False):
        # --no-declared drops pointer discovery but --legacy-env still bridges the
        # legacy env profiles.
        from . import registrar_discovery

        declared_source = registrar_discovery.read_legacy_env_profiles

    with _client(args) as c:
        daemon = SupervisorDaemon(
            c, machine, env, poll_interval=getattr(args, "interval", 5.0),
            declared_source=declared_source,
            # Rebuild the client by re-resolving the coordinator endpoint after a
            # connection failure -- the coordinator's ephemeral port moves on
            # restart, so a cached one would wedge the daemon (#3825).
            client_factory=lambda: _client(args, ensure=False),
        )

        def _on_cycle(summary) -> None:
            changed = (
                summary.started or summary.stopped or summary.restarted
                or summary.revived
            )
            if changed:
                print(
                    f"supervise serve: started={summary.started} "
                    f"stopped={summary.stopped} restarted={summary.restarted} "
                    f"revived={summary.revived} running={summary.running} "
                    f"backing_off={summary.backing_off}",
                    file=sys.stderr,
                )

        return daemon.serve(
            once=getattr(args, "once", False),
            single_instance=not getattr(args, "no_single_instance", False),
            on_cycle=_on_cycle,
        )


def _cmd_supervise_daemon_status(args: argparse.Namespace) -> int:
    """``supervise daemon-status`` -- is a daemon running here, and what would it
    run."""
    from .config import overrides_path, run_dir
    from .overrides import load_overrides, overridden_off_ids
    from .single_instance import is_locked, lock_path_for
    from .supervisor_daemon import supervisor_lease_scope

    machine, env = _registration_scope(args)
    scope = supervisor_lease_scope(machine, env)
    running = is_locked(lock_path_for(run_dir(), scope))
    with _client(args) as c:
        regs = c.list_registrations(machine=machine, env=env, include_paused=True)
    overrides = load_overrides(overrides_path())
    off = overridden_off_ids(overrides)
    # Annotate each registration with its override state so the overridden-off set
    # is legible right beside what is declared/registered (vision: legibility).
    for reg in regs:
        rid = reg.get("id")
        if rid in off:
            reg["overridden_off"] = True
            rec = overrides.get(rid) or {}
            reg["override_reason"] = rec.get("reason")
    return _emit(
        {
            "scope": scope,
            "machine": machine,
            "env": env,
            "running": running,
            "registrations": regs,
            "overrides": overrides,
        }
    )


def _cmd_supervise_override(args: argparse.Namespace) -> int:
    """``supervise override {disable,enable,list}`` -- the operator kill-switch.

    A fast, local, reversible enable/disable veto on a supervised unit (addressed
    by its registration id), applied out of band via the local override store. The
    daemon subtracts overridden-off ids from its desired set on the next reconcile,
    so a disabled unit winds down and stays down until re-enabled -- even across a
    repo re-sync of its declaration.
    """
    from .config import overrides_path
    from .overrides import (
        clear_override,
        load_overrides,
        overridden_off_ids,
        set_override,
    )

    action = getattr(args, "override_command", None)
    path = overrides_path()
    if action == "disable":
        record = set_override(
            path, args.id, disabled=True, reason=getattr(args, "reason", None)
        )
        return _emit({"id": args.id, "overridden_off": True, **record})
    if action == "enable":
        cleared = clear_override(path, args.id)
        return _emit({"id": args.id, "overridden_off": False, "cleared": cleared})
    # list (default)
    overrides = load_overrides(path)
    off = sorted(overridden_off_ids(overrides))
    return _emit(
        {
            "path": str(path),
            "overridden_off": off,
            "overrides": overrides,
        }
    )


def _cmd_supervise(args: argparse.Namespace) -> int:
    """Run the embody spawn supervisor over the lane (once, or as a loop).

    Turns queued (optionally label-gated) tasks into host embody autopilots,
    exactly once each, via the atomic spawn reservation. See the ``supervisor``
    module for the spawn-at-most-once safety model.

    A ``supervise <register|status|list|remove>`` subcommand instead manages
    durable **registrations** (the *registered-supervision* surface): registering
    adds a unit and returns its handle rather than becoming this loop. The bare
    ``supervise`` (no subcommand) remains the transitional foreground loop until
    the singleton daemon subsumes it.
    """
    sub = getattr(args, "supervise_command", None)
    if sub == "register":
        return _cmd_supervise_register(args)
    if sub == "status":
        return _cmd_supervise_status(args)
    if sub == "list":
        return _cmd_supervise_list(args)
    if sub == "remove":
        return _cmd_supervise_remove(args)
    if sub == "serve":
        return _cmd_supervise_serve(args)
    if sub == "daemon-status":
        return _cmd_supervise_daemon_status(args)
    if sub == "override":
        return _cmd_supervise_override(args)

    from .supervisor import (
        Supervisor,
        make_embody_spawn,
        make_headless_spawn,
        make_label_routed_spawn,
    )

    repo = None if getattr(args, "all_repos", False) else _scope_repo(args)
    coordinator_url = _resolve_client_target(args)[0]
    pool = [h for h in (getattr(args, "pool", "") or "").split(",") if h.strip()]
    # Embody backend default is HEADLESS: a dispatched/supervised task is a
    # self-contained, autonomous body that needs no human attach, and headless
    # sidesteps the CLI-start-prompt path entirely. `--embody-backend cli` opts the
    # whole lane back to CLI/mux (attachable); per-label overrides fine-tune either
    # way (`--cli-label` forces CLI when the default is headless; `--headless-label`
    # forces headless when the default is cli).
    backend = getattr(args, "embody_backend", None) or "headless"
    headless_labels = [
        label for label in (getattr(args, "headless_label", None) or []) if label
    ]
    cli_labels = [
        label for label in (getattr(args, "cli_label", None) or []) if label
    ]
    capacity_gate = None
    if pool:
        from . import remote_dispatch
        from .fleet import FleetSpawner

        origin = getattr(args, "origin", None) or remote_dispatch.local_machine()
        if not origin:
            print(
                "agent-dispatch supervise --pool: could not resolve this machine's "
                "alias for fleet bodies to report back to; pass --origin <alias>.",
                file=sys.stderr,
            )
            return 2
        # Fleet bodies are headless by default too (the `--headless` flag remains an
        # explicit force); only `--embody-backend cli` makes fleet bodies CLI.
        fleet_headless = bool(getattr(args, "headless", False)) or backend != "cli"
        fleet = FleetSpawner(
            pool,
            origin=origin,
            headless=fleet_headless,
            agent=getattr(args, "headless_agent", None) or "task-worker",
            verify_timeout=getattr(args, "verify_timeout", 0) or 0,
        )
        spawn_fn = fleet
        capacity_gate = fleet.can_spawn
        if headless_labels or cli_labels:
            print(
                "agent-dispatch supervise: per-label --headless-label/--cli-label "
                "are ignored in fleet (--pool) mode; the whole pool is "
                f"{'headless' if fleet_headless else 'CLI'} "
                "(set --embody-backend to change).",
                file=sys.stderr,
            )
        body = "headless agent-bridge ACP" if fleet_headless else "CLI-embodied"
        print(
            f"agent-dispatch supervise: fleet mode -- pool={','.join(fleet.pool)} "
            f"origin={origin} body={body}",
            file=sys.stderr,
        )
        # Preflight only a headless fleet lane: a CLI-embodied fleet body is a
        # worktree autopilot, not an agent-bridge agent.
        _preflight_agent = (
            (getattr(args, "headless_agent", None) or "task-worker")
            if fleet_headless else None
        )
        _preflight_pool = list(fleet.pool)
    else:
        headless_spawn = make_headless_spawn(
            coordinator_url,
            agent=getattr(args, "headless_agent", None) or "task-worker",
        )
        embody_spawn = make_embody_spawn(
            coordinator_url, verify_timeout=getattr(args, "verify_timeout", 0) or 0
        )
        if backend == "cli":
            # CLI-default lane: headless is the per-label opt-in.
            default_spawn, overrides = embody_spawn, {
                label: headless_spawn for label in headless_labels
            }
            routed_note = (
                f"CLI embody; headless-ACP for label(s): {', '.join(headless_labels)}"
                if headless_labels else "CLI embody (all watched labels)"
            )
        else:
            # Headless-default lane (the default): CLI is the per-label opt-out.
            default_spawn, overrides = headless_spawn, {
                label: embody_spawn for label in cli_labels
            }
            routed_note = (
                f"headless-ACP embody; CLI for label(s): {', '.join(cli_labels)}"
                if cli_labels else "headless-ACP embody (all watched labels)"
            )
        spawn_fn = (
            make_label_routed_spawn(default_spawn, overrides=overrides)
            if overrides else default_spawn
        )
        print(f"agent-dispatch supervise: {routed_note}", file=sys.stderr)
        # A local lane is headless when the default backend is headless, or when a
        # CLI-default lane routes a subset of labels to a headless body.
        _headless_active = backend != "cli" or bool(headless_labels)
        _preflight_agent = (
            (getattr(args, "headless_agent", None) or "task-worker")
            if _headless_active else None
        )
        _preflight_pool = None
    # Best-effort, fail-loud preflight: warn (never block) when the headless
    # embody agent isn't registered with agent-bridge on the host(s) where a body
    # will actually spawn -- turning a silent dead-letter (the classic bogus
    # `task-worker` default) into a diagnosable startup warning. Skipped for
    # `--once` so hot one-shot/cron polls stay cheap and side-effect-free.
    if not args.once and _preflight_agent:
        from . import bridge

        for _warning in bridge.preflight_headless_agent(
            _preflight_agent, pool=_preflight_pool
        ):
            print(_warning, file=sys.stderr)
    with _client(args, ensure=False) as c:
        evaluator = None
        spec_path = getattr(args, "evaluator", None)
        if spec_path:
            from .producers.evaluator import EvaluatorError, SpecEvaluator

            try:
                spec = json.loads(Path(spec_path).expanduser().read_text(encoding="utf-8"))
                evaluator = SpecEvaluator(spec)
            except (OSError, ValueError, EvaluatorError) as exc:
                print(f"agent-dispatch supervise: bad --evaluator spec: {exc}", file=sys.stderr)
                return 2
            print(
                "agent-dispatch supervise: evaluator pass enabled -- advancing "
                "terminal tasks across the loop",
                file=sys.stderr,
            )
        sup = Supervisor(
            c,
            spawn_fn=spawn_fn,
            repo=repo,
            labels=args.label or None,
            max_concurrent=args.max_concurrent,
            max_attempts=args.max_attempts,
            label_max_attempts=_parse_label_max_attempts(
                getattr(args, "label_max_attempts", None)
            ),
            heartbeat=not args.no_heartbeat,
            reactive=not getattr(args, "no_reactive", False),
            reactive_interval=getattr(args, "reactive_interval", 2.0) or 2.0,
            capacity_gate=capacity_gate,
            evaluator=evaluator,
        )
        if args.once:
            return _emit({"spawned": sup.poll_once()})

        def _on_cycle(spawned: list[str]) -> None:
            if spawned:
                print(
                    f"agent-dispatch supervise: spawned {len(spawned)} task(s): "
                    f"{', '.join(spawned)}",
                    file=sys.stderr,
                )

        sup.serve(interval=args.interval, on_cycle=_on_cycle)
    return 0


def _cmd_reservations(args: argparse.Namespace) -> int:
    """Operator visibility + manual control over spawn reservations."""
    with _client(args) as c:
        if args.reservations_command == "list":
            rows = c.list_reservations(
                task_id=args.task, state=args.state, limit=args.limit
            )
            return _emit(rows)
        if args.reservations_command == "fail":
            return _emit(c.fail_spawn(args.key, detail=args.detail))
        if args.reservations_command == "settle":
            return _emit(c.settle_spawn(args.key, detail=args.detail))
    return 2


# ---- Loop recipes -----------------------------------------------------------
#
# A recipe is a packaged loop archetype (reviewer / conflict-resolution /
# goal-driven). `recipes list|describe|render` are pure introspection; `recipes
# kick` renders a recipe into an ordinary task and reuses `_cmd_create` (so the
# same dedup / spawn / lane resolution applies) -- the ad-hoc "recipes run without
# a wrapper service" path. See visions/plugins/agent-dispatch (§Concepts/*The
# recipe*, §Features/*loop-recipes* + *recipes-run-ad-hoc*).


def _parse_recipe_params(pairs: list[str] | None) -> dict[str, str]:
    """Parse repeated ``--param KEY=VALUE`` into a dict."""
    out: dict[str, str] = {}
    for item in pairs or []:
        if "=" not in item:
            raise ValueError(f"--param must be KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--param has an empty key: {item!r}")
        out[key] = value
    return out


def _recipe_param_dicts(recipe: Any) -> list[dict]:
    return [
        {
            "name": p.name,
            "required": p.required,
            "default": p.default,
            "description": p.description,
        }
        for p in recipe.params
    ]


def _run_resolution_step(step: Any, *, cwd: str | None = None) -> dict:
    """Execute one non-advisory :class:`ResolutionStep` in the caller's worktree.

    Runs the step's fixed ``argv`` (git only) and returns a bounded result. An
    advisory step is never run here -- the caller reports it as an instruction.
    """
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed git argv from a ResolutionStep
            list(step.argv), cwd=cwd, check=False, capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"kind": step.kind, "ran": True, "ok": False, "error": str(exc)}
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    return {
        "kind": step.kind,
        "ran": True,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "output": out[:2000],
        "error": err[:2000] or None,
    }


def _cmd_resolve(args: argparse.Namespace) -> int:
    """Drive THIS worktree to a clean, resolved final state (the enforced
    *drive-the-worktree-to-resolution* invariant). Plans by default; ``--execute``
    performs the (destructive) unwind on the caller's own workspace."""
    from .resolution import ResolutionError, plan_resolution

    try:
        plan = plan_resolution(
            args.outcome, base=args.base, source_ref=args.source, reason=args.reason
        )
    except ResolutionError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2

    if not args.execute:
        payload = plan.to_dict()
        payload["executed"] = False
        payload["note"] = (
            "plan only -- re-run with --execute to perform the unwind "
            "(destructive steps discard working-tree state)"
        )
        return _emit(payload)

    results: list[dict] = []
    instructions: list[str] = []
    failed = False
    for step in plan.steps:
        if step.advisory:
            instructions.append(step.description)
            results.append({"kind": step.kind, "ran": False, "advisory": True})
            continue
        res = _run_resolution_step(step)
        results.append(res)
        if not res["ok"]:
            failed = True
            # A failed destructive unwind must not be papered over -- stop so the
            # worker/operator can look, rather than pressing on into a dirtier
            # state.
            if step.destructive:
                break

    payload = plan.to_dict()
    payload.update({"executed": True, "results": results, "instructions": instructions})
    _emit(payload)
    return 1 if failed else 0


def _spawn_detached_waiter(spec: Any) -> dict:
    """Re-exec ``agent-dispatch run`` (without ``--detach``) as a fully detached
    waiter that outlives this process, so the kicking worker can be torn down
    while a cheap OS-level process owns the wait and fires the resume."""
    from . import hibernation
    from .procutil import detached_kwargs, windowless_python

    argv = hibernation.detached_run_argv(spec, python=windowless_python(sys.executable))
    proc = subprocess.Popen(  # noqa: S603 -- fixed argv (interpreter + our own module)
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detached_kwargs(),
    )
    return {"pid": proc.pid, "argv": argv}


def _cmd_run(args: argparse.Namespace) -> int:
    """Hand a blocking wait to the layer (*hibernate-the-wait*): run ``-- <cmd>``
    to completion, then resume the worktree-affinitied worker via agent-bridge.
    With ``--detach`` the wait runs in a detached process so the worker can be
    torn down (costing nothing) while it waits."""
    from . import bridge
    from .hibernation import RunSpec, run_and_resume

    command = getattr(args, "_dashdash_tail", None)
    if command is None:
        command = list(args.command or [])
        if command and command[0] == "--":
            command = command[1:]
    if not command:
        print(
            "agent-dispatch: run needs a command after '--', e.g. "
            "`agent-dispatch run --resume <worktree> -- <blocking-cmd>`",
            file=sys.stderr,
        )
        return 2

    spec = RunSpec(
        command=tuple(command),
        resume_worktree=args.resume,
        task_id=args.task,
        message=args.message,
    )

    if args.detach:
        handle = _spawn_detached_waiter(spec)
        return _emit(
            {
                "detached": True,
                "resume_worktree": spec.resume_worktree,
                "command": list(spec.command),
                **handle,
            }
        )

    def runner(cmd: tuple[str, ...]) -> int:
        try:
            proc = subprocess.run(list(cmd), check=False)  # noqa: S603 -- operator-supplied wait
            return proc.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"agent-dispatch: run: could not execute the wait: {exc}", file=sys.stderr)
            return 127

    report = run_and_resume(spec, runner=runner, resumer=bridge.send_nudge)
    return _emit(report)


def _cmd_evaluate(args: argparse.Namespace) -> int:
    """Feed one task **lifecycle event** through a declarative evaluator and apply
    its decisions (the *evaluator* half of emitters-and-evaluators). The event
    JSON is read from ``--event-file`` or stdin; the coordinator shape is
    ``{"type": "task.completed", "task": {...}}``."""
    from .producers.evaluator import EvaluatorError, SpecEvaluator, evaluate_and_apply

    try:
        spec = json.loads(Path(args.spec).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"agent-dispatch: cannot read evaluator spec: {exc}", file=sys.stderr)
        return 2
    raw = (
        Path(args.event_file).expanduser().read_text(encoding="utf-8")
        if args.event_file
        else sys.stdin.read()
    )
    try:
        event = json.loads(raw)
    except ValueError as exc:
        print(f"agent-dispatch: event is not valid JSON: {exc}", file=sys.stderr)
        return 2
    try:
        evaluator = SpecEvaluator(spec)
    except EvaluatorError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        report = evaluate_and_apply(
            evaluator, event, creator=lambda *a, **k: {}, repo=args.repo, apply=False
        )
        return _emit(report)

    with _client(args) as c:
        try:
            report = evaluate_and_apply(
                evaluator, event, creator=c.create, repo=args.repo, apply=True
            )
        except EvaluatorError as exc:
            print(f"agent-dispatch: {exc}", file=sys.stderr)
            return 2
    return _emit(report)


def _cmd_recipes_list(args: argparse.Namespace) -> int:
    from .recipes import list_recipes

    return _emit(
        [
            {
                "name": r.name,
                "summary": r.summary,
                "params": _recipe_param_dicts(r),
                "suspend_on": list(r.suspend_on),
                "resolution": r.resolution,
            }
            for r in list_recipes()
        ]
    )


def _cmd_recipes_describe(args: argparse.Namespace) -> int:
    from .recipes import UnknownRecipe, get_recipe

    try:
        r = get_recipe(args.name)
    except UnknownRecipe as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2
    return _emit(
        {
            "name": r.name,
            "summary": r.summary,
            "params": _recipe_param_dicts(r),
            "title_template": r.title_template,
            "goal_template": r.goal_template,
            "done_criteria": r.done_criteria,
            "charter_template": r.charter_template,
            "suspend_on": list(r.suspend_on),
            "resolution": r.resolution,
            "requires": list(r.requires),
            "labels": list(r.labels),
        }
    )


def _cmd_recipes_render(args: argparse.Namespace) -> int:
    from .recipes import RecipeError, render_recipe

    try:
        rendered = render_recipe(args.name, _parse_recipe_params(args.param))
    except (RecipeError, ValueError) as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2
    return _emit(rendered.to_dict())


def _recipe_dedup_key(rendered: Any) -> str:
    """A reserved-work dedup key so re-kicking the same recipe+params collides
    rather than forking the work (the *no-overlapping-live-workers* invariant's
    dedup-before-create half). Delegates to the shared registry helper so the CLI
    and MCP kick paths derive the same key."""
    from .recipes import dedup_key_for

    return dedup_key_for(rendered)


def _recipe_create_namespace(
    args: argparse.Namespace, rendered: Any
) -> argparse.Namespace:
    """Build a ``create``-shaped namespace from a rendered recipe so ``kick`` can
    reuse ``_cmd_create`` verbatim (dedup, spawn, lane resolution)."""
    return argparse.Namespace(
        # recipe-derived
        title=rendered.title,
        prompt=rendered.prompt,
        goal=rendered.goal,
        done_criteria=rendered.done_criteria,
        require=list(rendered.requires) or None,
        label=list(dict.fromkeys([*rendered.labels, *(getattr(args, "label", None) or [])])),
        dedup_key=getattr(args, "dedup_key", None) or _recipe_dedup_key(rendered),
        source="recipe",
        origin_ref=rendered.recipe,
        # spawn passthrough (a recipe worker wants a full checkout -> embody body)
        spawn=getattr(args, "spawn", False),
        spawn_backend=getattr(args, "spawn_backend", "embody"),
        spawn_agent=getattr(args, "spawn_agent", "task-worker"),
        run_async=getattr(args, "run_async", False),
        verify_timeout=getattr(args, "verify_timeout", 0),
        # lane / client passthrough
        repo=getattr(args, "repo", None),
        url=getattr(args, "url", None),
        token=getattr(args, "token", None),
        # create knobs left at their defaults (a recipe kick uses none of these)
        proposed=False,
        claim=False,
        exclude=None,
        affinity=None,
        payload_ref=None,
        payload_inline=None,
        payload_file=None,
        target_machine=None,
        target_worktree=None,
        target_repo=None,
        not_before=0.0,
        machine=getattr(args, "machine", None),
        worktree=getattr(args, "worktree", None),
    )


def _cmd_recipes_kick(args: argparse.Namespace) -> int:
    from .recipes import RecipeError, render_recipe

    try:
        rendered = render_recipe(args.name, _parse_recipe_params(args.param))
    except (RecipeError, ValueError) as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2

    create_ns = _recipe_create_namespace(args, rendered)
    if getattr(args, "dry_run", False):
        preview = rendered.to_dict()
        preview.update(
            {
                "dry_run": True,
                "dedup_key": create_ns.dedup_key,
                "spawn": create_ns.spawn,
                "spawn_backend": create_ns.spawn_backend,
            }
        )
        return _emit(preview)
    return _cmd_create(create_ns)


def _cmd_recipes_drive(args: argparse.Namespace) -> int:
    """Decide the next loop step for a recipe given a ``--signal`` (the driver's
    executable rhythm). Prints the action; ``--execute`` performs the SUSPEND
    (detached hibernation wait) and RESOLVE (drive-to-resolution) legs -- WORK is
    the agent's own to do."""
    from .recipes import UnknownRecipe, decide, get_recipe
    from .recipes.driver import RESOLVE, SUSPEND

    try:
        recipe = get_recipe(args.name)
    except UnknownRecipe as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2

    action = decide(recipe, args.signal)
    report: dict[str, Any] = {"recipe": recipe.name, "signal": args.signal, "action": action.to_dict()}

    if not args.execute:
        return _emit(report)

    if action.kind == SUSPEND:
        wait_cmd = getattr(args, "_dashdash_tail", None)
        if wait_cmd is None:
            wait_cmd = list(args.wait_cmd or [])
            if wait_cmd and wait_cmd[0] == "--":
                wait_cmd = wait_cmd[1:]
        if not wait_cmd or not args.resume:
            report["executed"] = False
            report["note"] = (
                "SUSPEND needs --resume <worktree> and a wait command after '--' "
                "to hand off; nothing executed"
            )
            return _emit(report)
        from .hibernation import RunSpec

        spec = RunSpec(command=tuple(wait_cmd), resume_worktree=args.resume, task_id=args.task)
        report["executed"] = True
        report["waiter"] = _spawn_detached_waiter(spec)
        return _emit(report)

    if action.kind == RESOLVE:
        from .resolution import plan_resolution

        plan = plan_resolution(action.outcome, base=args.base, source_ref=args.source)
        results: list[dict] = []
        instructions: list[str] = []
        failed = False
        for step in plan.steps:
            if step.advisory:
                instructions.append(step.description)
                results.append({"kind": step.kind, "ran": False, "advisory": True})
                continue
            res = _run_resolution_step(step)
            results.append(res)
            if not res["ok"]:
                failed = True
                if step.destructive:
                    break
        report["executed"] = True
        report["resolution"] = {**plan.to_dict(), "results": results, "instructions": instructions}
        _emit(report)
        return 1 if failed else 0

    # WORK: nothing for the layer to execute -- the agent does the pass.
    report["executed"] = False
    report["note"] = "WORK is the agent's to perform; re-run drive with the next signal"
    return _emit(report)


class _DashDashParser(argparse.ArgumentParser):
    """Top-level parser that captures a verbatim ``-- <command...>`` tail robustly.

    argparse's handling of a ``--`` separator before a ``nargs='*'`` positional
    differs across CPython versions (3.11 raises "unrecognized arguments"; 3.12+
    consumes it), which broke ``recipes drive --execute -- <cmd>`` on 3.11
    runtime slots (#383). Rather than depend on that, split everything after the
    FIRST ``--`` off ourselves, parse the head normally, and expose the verbatim
    tail via ``_dashdash_tail`` for the ``run`` / ``recipes drive`` handlers.

    Scoped to those two commands (the only ones that take a ``-- <command>``
    tail) so every other subcommand keeps argparse's native ``--`` "end of
    options" escape hatch.
    """

    def parse_known_args(self, args=None, namespace=None):  # type: ignore[override]
        args = list(sys.argv[1:] if args is None else args)
        if "--" in args:
            idx = args.index("--")
            head, tail = args[:idx], args[idx + 1:]
            # Resolve the ACTUAL subcommand from the head (peek parse) rather than
            # a token-membership test -- a positional VALUE equal to 'run'/'drive'
            # (e.g. `create run -- ...`) must not trigger interception (#383).
            try:
                peek, _ = super().parse_known_args(head, None)
            except SystemExit:
                peek = None
            if peek is not None and getattr(peek, "func", None) in (
                _cmd_run, _cmd_recipes_drive
            ):
                ns, extras = super().parse_known_args(head, namespace)
                ns._dashdash_tail = tail
                return ns, extras
        return super().parse_known_args(args, namespace)


def _declaration_summary(decl: ProfileDeclaration) -> dict[str, Any]:
    """A JSON-friendly summary of a discovered declaration (for ``registrar discover``)."""
    ef = decl.effective_filters()
    return {
        "name": decl.name,
        "owner": decl.owner,
        "labels": list(decl.labels),
        "repos": decl.repos,
        "concurrency": decl.concurrency,
        "body": {"type": decl.body.type, "agent": decl.body.agent},
        "filters": {
            "permit": {dim: sorted(vals) for dim, vals in ef.permit.items()},
            "reject": {dim: sorted(vals) for dim, vals in ef.reject.items()},
        },
    }


def _cmd_registrar(args: argparse.Namespace) -> int:
    """The declarative-supervision registrar: manage discovery pointers + read the
    declared profile set. A thin writer/reader over the declared documents (the one
    source of truth) -- no coordinator round-trip."""
    from . import registrar_discovery as rd
    from .registrar import RegistrarError

    try:
        if args.registrar_command == "add-pointer":
            pointer = rd.add_pointer(
                args.name, args.location, kind=args.kind, owner=args.owner
            )
            return _emit(pointer.to_dict())
        if args.registrar_command == "list":
            return _emit([p.to_dict() for p in rd.load_pointers()])
        if args.registrar_command == "remove":
            return _emit({"removed": rd.remove_pointer(args.name)})
        if args.registrar_command == "discover":
            decls = rd.discover()
            return _emit([_declaration_summary(d) for d in decls])
        if args.registrar_command == "discover-repo":
            decls = rd.discover_repo(args.repo_root, owner=args.owner)
            return _emit([_declaration_summary(d) for d in decls])
    except RegistrarError as exc:
        print(f"agent-dispatch registrar: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled registrar command {args.registrar_command!r}")


def _create_args_parent() -> argparse.ArgumentParser:
    """The shared argument surface for ``create`` and ``propose`` (an argparse parent).

    Both verbs enqueue a task from the identical inputs; ``propose`` just forces the
    ``proposed`` (unclaimable) state. Defining the args once keeps the two verbs from
    drifting.
    """
    cp = argparse.ArgumentParser(add_help=False)
    cp.add_argument("title", help="short, specific, self-contained summary of the work")
    cp.add_argument(
        "--prompt", default="",
        help="the task instruction -- describe the work fully enough to dedup "
             "against and to execute without extra context",
    )
    cp.add_argument(
        "--repo",
        help="lane (repo) this task belongs to: a local repo name or a remote "
             "URL. Default: the calling repo resolved from the CWD. Tasks stay "
             "in their producing repo's lane -- for a cross-repo *code* target "
             "use --target-repo and let the lane agent do it via working-cross-repo.",
    )
    cp.add_argument("--proposed", action="store_true", help="create as an unclaimable draft")
    cp.add_argument(
        "--claim", action="store_true",
        help="atomically create-AND-claim as this worktree (no queued gap). With "
             "--dedup-key <subject>, this is the lazy open-ended-pickup primitive: "
             "either mint the subject as mine, or (on a dedup collision) get back "
             "the row someone else already took -- see 'claimed_by_me' in the "
             "output to tell which.",
    )
    cp.add_argument(
        "--require", action="append", help="hard capability/identity token (repeatable)"
    )
    cp.add_argument(
        "--exclude", action="append",
        help="hard EXCLUSION token -- a worker whose capabilities/identity match "
             "any exclude is ineligible (anti-affinity; repeatable). E.g. "
             "'machine:host-a', 'worktree:foo', 'agent:reviewer'.",
    )
    cp.add_argument("--affinity", action="append", help="soft preference key=value (repeatable)")
    cp.add_argument("--label", action="append", help="free-form label (repeatable)")
    cp.add_argument("--payload-ref")
    cp.add_argument("--payload-inline")
    cp.add_argument(
        "--payload-file",
        help="read the payload from a file (large payloads spill to a blob "
             "automatically); '-' reads from stdin",
    )
    cp.add_argument(
        "--target-machine",
        help="route the task to this machine. With `--spawn --spawn-backend "
             "embody` for another machine, dispatch runs there over the SSH "
             "mesh (Phase 8: create+embody land on the target's coordinator).",
    )
    cp.add_argument("--target-worktree")
    cp.add_argument("--target-repo")
    cp.add_argument("--source")
    cp.add_argument("--origin-ref")
    cp.add_argument("--dedup-key")
    cp.add_argument(
        "--goal",
        help="durable objective the worker loops toward across turns/embodiments "
             "(the resumable-goal feature); a worker resumes it from recorded "
             "progress rather than restarting. Omit for a plain one-shot task.",
    )
    cp.add_argument(
        "--done-criteria",
        help="explicit criteria for when --goal is met; the worker completes only "
             "once it judges these satisfied (deferred completion).",
    )
    cp.add_argument("--not-before", type=float, default=0.0)
    cp.add_argument(
        "--spawn", action="store_true",
        help="after creating, spawn a worker to execute it (best effort)",
    )
    cp.add_argument(
        "--spawn-backend", choices=["bridge", "embody"], default="bridge",
        help="how to embody the spawned worker: 'embody' = a CLI-backed "
             "autopilot session in a fresh parallel worktree (agent-worktrees "
             "embody -- the 'dispatch an agent to do X' path); 'bridge' "
             "(default) = a headless agent-bridge ACP worker",
    )
    cp.add_argument(
        "--spawn-agent", default="task-worker",
        help="agent-bridge agent name to spawn (bridge backend only; "
             "default: task-worker)",
    )
    cp.add_argument(
        "--verify-timeout", type=int, default=0,
        help="embody backend: wait up to N seconds for the spawned mux session "
             "to come up before returning (default 0: don't wait)",
    )
    cp.add_argument(
        "--async", dest="run_async", action="store_true",
        help="with --spawn, don't wait for the worker (fire-and-forget)",
    )
    return cp


def build_parser() -> argparse.ArgumentParser:
    parser = _DashDashParser(
        prog="agent-dispatch", description="Agent task queue + coordinator"
    )
    parser.add_argument("--version", action="version", version=f"agent-dispatch {__version__}")
    parser.add_argument(
        "--url", help="coordinator base URL (default: AGENT_DISPATCH_URL or config)"
    )
    parser.add_argument("--token", help="bearer token (default: AGENT_DISPATCH_TOKEN)")
    parser.add_argument(
        "--shared", action="store_true",
        help="target the SHARED/elected coordinator (AGENT_DISPATCH_SHARED_URL; "
             "the hosted coordinator) for cross-machine dispatch, instead of this "
             "host's local coordinator. Authenticated with AGENT_DISPATCH_SHARED_TOKEN.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the per-host coordinator")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--db")
    p.add_argument(
        "--passive",
        action="store_true",
        help=argparse.SUPPRESS,  # internal: a graceful-cutover passive instance
    )
    p.set_defaults(func=_cmd_serve)

    # Internal graceful-cutover seam (installer-driven, not an operator command).
    p = sub.add_parser("_cutover", help=argparse.SUPPRESS)
    p.add_argument("--health-timeout", type=float, default=60.0)
    p.add_argument("--drain-timeout", type=float, default=300.0)
    p.add_argument("--force", action="store_true")
    p.add_argument("--recover", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_cutover)

    create_parent = _create_args_parent()
    p = sub.add_parser(
        "create",
        parents=[create_parent],
        help="enqueue a task (write a self-contained title + --prompt so a "
             "producer sweeping existing tasks can judge duplication)",
    )
    p.set_defaults(func=_cmd_create)

    p = sub.add_parser(
        "propose",
        parents=[create_parent],
        help="draft an unclaimable 'proposed' task (the propose -> queue "
             "lifecycle): like create but always proposed, never claimed or "
             "spawned; run 'queue <id>' to make it claimable",
    )
    p.set_defaults(func=_cmd_propose)

    p = sub.add_parser(
        "approve", aliases=["queue"], help="move a proposed task to queued (commit it to binding)"
    )
    p.add_argument("task_id")
    p.set_defaults(func=_simple("approve", "task_id"))

    p = sub.add_parser(
        "registrar",
        help="declarative-supervision registrar: manage discovery pointers and read "
             "the declared profile set (declarations are the one source of truth; "
             "the CLI is a thin writer/reader over them)",
    )
    reg_sub = p.add_subparsers(dest="registrar_command", required=True)
    rp = reg_sub.add_parser(
        "add-pointer",
        help="record (or replace) a pointer to a location of declaration documents",
    )
    rp.add_argument("name", help="unique pointer name (letters, digits, '-', '_')")
    rp.add_argument(
        "location",
        help="directory of declaration docs, or (with --kind repo) a repo root "
             "whose .agent-dispatch/registrar/ is read",
    )
    rp.add_argument(
        "--kind", choices=["dir", "repo"], default="dir",
        help="'dir' (default) reads the location directly; 'repo' reads its "
             ".agent-dispatch/registrar/ subdir",
    )
    rp.add_argument("--owner", help="provenance stamped on declarations read here")
    rp.set_defaults(func=_cmd_registrar)
    rp = reg_sub.add_parser("list", help="list the recorded discovery pointers")
    rp.set_defaults(func=_cmd_registrar)
    rp = reg_sub.add_parser("remove", help="remove a discovery pointer by name")
    rp.add_argument("name", help="the pointer name to remove")
    rp.set_defaults(func=_cmd_registrar)
    rp = reg_sub.add_parser(
        "discover",
        help="read + aggregate the declared profile set across all pointers "
             "(rejects duplicate profile names across sources)",
    )
    rp.set_defaults(func=_cmd_registrar)
    rp = reg_sub.add_parser(
        "discover-repo",
        help="read a single synced repo's in-repo .agent-dispatch/registrar/ "
             "declarations (the repo-sync discovery unit)",
    )
    rp.add_argument("repo_root", help="path to the repo root to read declarations from")
    rp.add_argument("--owner", help="provenance override (default: repo:<name>)")
    rp.set_defaults(func=_cmd_registrar)

    p = sub.add_parser(
        "claim", help="atomically lease one eligible task (identity auto-resolved from CWD)"
    )
    p.add_argument(
        "task_id", nargs="?",
        help="claim THIS specific task id (optional; default: any eligible task). "
             "First-positional task id, consistent with start/complete/yield/abandon.",
    )
    p.add_argument("--machine", help="override the resolved machine (targeting identity)")
    p.add_argument("--worktree", help="override the resolved worktree id (targeting identity)")
    p.add_argument(
        "--worker", "--as", dest="worker_id",
        help="explicit owner/worker id to claim as (rarely needed; default: "
             "composed from machine/worktree). Was the bare positional, now a flag "
             "so it can't be confused with the task id.",
    )
    p.add_argument("--capability", action="append", help="advertised capability (repeatable)")
    p.add_argument(
        "--task", help="alias for the positional task id (back-compat)",
    )
    p.add_argument(
        "--repo",
        help="lane to claim from (local name or remote URL). Default: the calling "
             "repo. A worker only claims tasks in its own repo's lane.",
    )
    p.add_argument("--lease-seconds", type=int)
    p.add_argument(
        "--evaluation", action="store_true",
        help="claim under the tight EVALUATION lease (a quick accept/reject "
             "window): a stuck evaluator auto-releases fast, and 'start' then "
             "extends to the full work lease on commit. Decline with "
             "'yield --exclude-self' or 'abandon --duplicate-of'.",
    )
    p.set_defaults(func=_cmd_claim)

    p = sub.add_parser(
        "worktree-status",
        help="this worktree's inbox: tasks assigned to + owned by it (identity auto-resolved)",
    )
    p.add_argument("--machine", help="override the resolved machine")
    p.add_argument("--worktree", help="override the resolved worktree id")
    p.add_argument(
        "--repo",
        help="lane to scope the inbox to (local name or remote URL). Default: the calling repo.",
    )
    p.set_defaults(func=_cmd_worktree_status)

    p = sub.add_parser(
        "start", help="mark a claimed task started (identity auto-resolved from CWD)"
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id", nargs="?", help="owner id (default: composed from machine/worktree)"
    )
    p.add_argument("--machine", help="override the resolved machine (targeting identity)")
    p.add_argument("--worktree", help="override the resolved worktree id (targeting identity)")
    p.set_defaults(func=_cmd_start)

    p = sub.add_parser(
        "yield",
        help="return a held task to queued (with a note; identity auto-resolved)",
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id", nargs="?", help="owner id (default: composed from machine/worktree)"
    )
    p.add_argument("--note")
    p.add_argument(
        "--exclude-self", "--not-me", choices=("worktree", "machine"), dest="exclude_self",
        help="append a scoped self-EXCLUSION when yielding, so this same "
             "candidate isn't re-offered the task: 'worktree' (narrowest -- this "
             "worktree only) or 'machine' (this whole machine). Prefer the "
             "narrowest scope that is true. (`--not-me` is a deprecated alias.)",
    )
    p.add_argument(
        "--exclude",
        help="append an explicit exclusion token when yielding (e.g. "
             "'agent:reviewer'); overrides --exclude-self.",
    )
    p.add_argument("--machine", help="override the resolved machine (targeting identity)")
    p.add_argument("--worktree", help="override the resolved worktree id (targeting identity)")
    p.set_defaults(func=_cmd_yield)

    p = sub.add_parser("complete", help="mark a started task completed")
    p.add_argument("task_id")
    p.add_argument(
        "worker_id", nargs="?",
        help="owner id (default: the machine/worktree resolved from CWD, so a "
             "worker can `complete <id>` without typing its own owner)",
    )
    p.add_argument("--machine", help="override the resolved machine identity")
    p.add_argument("--worktree", help="override the resolved worktree identity")
    p.add_argument("--result-ref")
    p.set_defaults(func=_cmd_complete)

    p = sub.add_parser(
        "abandon",
        help="terminally abandon a task (requires --permit or --duplicate-of)",
    )
    p.add_argument("task_id")
    p.add_argument("--worker-id")
    p.add_argument("--permit", action="store_true", help="assert abandonment is permitted")
    p.add_argument("--reason")
    p.add_argument(
        "--duplicate-of", dest="duplicate_of", metavar="REF",
        help="retire the task as a DUPLICATE of REF (an existing task id, PR, or "
             "issue). Self-justifying: implies --permit and records the dedup "
             "reference in the reason, so the decision is never a silent drop.",
    )
    p.add_argument(
        "--resolve", action="store_true",
        help="also emit the drive-the-worktree-to-resolution plan (the unwind the "
             "worker must run on its own worktree). Advisory -- runs nothing.",
    )
    p.add_argument(
        "--base", metavar="BRANCH",
        help="with --resolve, the base branch the worktree unwinds onto "
             "(default: the branch's tracked upstream)",
    )
    p.set_defaults(func=_cmd_abandon)

    p = sub.add_parser("heartbeat", help="extend the lease on a held task")
    p.add_argument("task_id")
    p.add_argument("worker_id")
    p.set_defaults(func=_simple("heartbeat", "task_id", "worker_id"))

    p = sub.add_parser(
        "progress",
        help="record a brief progress beat toward the goal (also heartbeats the "
             "lease; identity auto-resolved from CWD)",
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id", nargs="?", help="owner id (default: composed from machine/worktree)"
    )
    p.add_argument(
        "--phase", default="",
        help="short phase label (e.g. 'planning', 'implementing', 'PR open')",
    )
    p.add_argument(
        "--summary", required=True,
        help="one-line status toward the goal (hard-capped; keep it a line, not a "
             "transcript)",
    )
    p.add_argument("--blocker", help="a real blocker holding progress, if any")
    p.add_argument("--pr", help="the PR/ref this beat corresponds to, if any")
    p.add_argument("--machine", help="override the resolved machine (targeting identity)")
    p.add_argument("--worktree", help="override the resolved worktree id (targeting identity)")
    p.set_defaults(func=_cmd_progress)

    p = sub.add_parser(
        "focus",
        help="set/show this worktree's current focus (its status-core summary "
             "on the worktree record); identity auto-resolved from CWD",
    )
    p.add_argument(
        "focus_text", nargs="?",
        help="one-line focus for this worktree; omit to show the current focus",
    )
    p.add_argument("--list", action="store_true", help="list every worktree's focus")
    p.add_argument("--machine", help="filter --list to a machine / override resolved machine")
    p.add_argument("--worktree", help="override the resolved worktree id")
    p.set_defaults(func=_cmd_focus)

    p = sub.add_parser("detach", help="demote a hard worktree pin to a soft affinity")
    p.add_argument("task_id")
    p.set_defaults(func=_simple("detach", "task_id"))

    # -- Steering: card + steer (human-in-the-loop) --------------------------
    cp = sub.add_parser(
        "card",
        help="attach/show a task's card -- the glanceable brief a worker posts "
             "when it needs operator input",
    )
    csub = cp.add_subparsers(dest="card_cmd", required=True)
    cs = csub.add_parser(
        "set",
        help="attach a card to a held task you own (a form via --request-input "
             "marks it awaiting-steer); identity auto-resolved from CWD",
    )
    cs.add_argument("task_id")
    cs.add_argument(
        "worker_id", nargs="?",
        help="owner id (default: composed from machine/worktree)",
    )
    cs.add_argument("--title", help="short card title")
    cs.add_argument("--status", help="one-line status overview")
    cs.add_argument("--link", help="a link to the rich artifact (OneDrive draft / PR)")
    cs.add_argument(
        "--body",
        help="the scrollable card body (markdown); '@path' reads a file",
    )
    cs.add_argument(
        "--request-input", dest="request_input",
        help="form spec the operator should fill, e.g. "
             "'feedback:textarea,decision:choice[revise,post-approved,hold-all]'",
    )
    cs.add_argument("--machine", help="override the resolved machine")
    cs.add_argument("--worktree", help="override the resolved worktree id")
    cs.set_defaults(func=_cmd_card_set)
    ch = csub.add_parser("show", help="show a task's current card + its steer inbox")
    ch.add_argument("task_id")
    ch.set_defaults(func=_cmd_card_show)

    sp = sub.add_parser(
        "steer",
        help="submit an operator's answer to a task's card, or (steer take) "
             "consume the next answer as the worker",
    )
    ssub = sp.add_subparsers(dest="steer_cmd", required=True)
    ssm = ssub.add_parser(
        "submit",
        help="submit an operator answer (--field k=v ...) and wake the worker",
    )
    ssm.add_argument("task_id")
    ssm.add_argument(
        "--field", action="append", metavar="KEY=VALUE",
        help="one answer field (repeatable), e.g. --field decision=post-approved",
    )
    ssm.add_argument("--sender", help="who is answering (default: resolved identity)")
    ssm.add_argument("--message", help="override the wake nudge text")
    ssm.add_argument(
        "--no-wake", dest="wake", action="store_false",
        help="do not send an agent-bridge wake nudge to the owning worktree",
    )
    ssm.set_defaults(func=_cmd_steer, wake=True)
    stk = ssub.add_parser(
        "take",
        help="consume the next pending steer for a task you own (the wake-side "
             "read); identity auto-resolved from CWD",
    )
    stk.add_argument("task_id")
    stk.add_argument(
        "worker_id", nargs="?",
        help="owner id (default: composed from machine/worktree)",
    )
    stk.add_argument("--machine", help="override the resolved machine")
    stk.add_argument("--worktree", help="override the resolved worktree id")
    stk.set_defaults(func=_cmd_steer_take)

    p = sub.add_parser("list", help="list tasks (scoped to the calling repo by default)")
    p.add_argument("--repo", help="lane to list (local name or remote URL); default: calling repo")
    p.add_argument(
        "--status",
        help="filter by status; comma-separate for several (e.g. queued,started)",
    )
    p.add_argument("--target-machine")
    p.add_argument("--target-repo")
    p.add_argument("--label")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument(
        "--machine",
        help="read another machine's queue over the SSH mesh (peer browse); "
             "default: this machine's local coordinator",
    )
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser(
        "inbox",
        help="machine-scoped, cross-lane pickable tasks (default: proposed) -- "
             "what this machine can start, across every repo lane",
    )
    p.add_argument(
        "--machine",
        help="machine to scope to; a *remote* machine reads that peer's queue "
             "over the SSH mesh (default: this machine, resolved via agent-worktrees)",
    )
    p.add_argument(
        "--status",
        default="proposed",
        help="status filter; comma-separate for several (default: proposed). "
             "Ignored when --awaiting-steer is set.",
    )
    p.add_argument(
        "--awaiting-steer", dest="awaiting_steer", action="store_true",
        help="show the picker steer surface: pickable (proposed) tasks PLUS any "
             "task blocked on operator steering (a posted card's request_input, "
             "in claimed/started), and nothing else of the owned queue. Overrides "
             "--status.",
    )
    p.add_argument(
        "--board", action="store_true",
        help="status-grouped board for the picker Tasks pivot: tasks across "
             "proposed/queued/claimed/started PLUS recently completed/abandoned, "
             "each tagged with a display `group` (Blocked/Proposed/Queued/Started/"
             "Completed/Abandoned) and ordered by that priority. Overrides "
             "--status and --awaiting-steer.",
    )
    p.add_argument(
        "--recent-mins", dest="recent_mins", type=int, default=120,
        help="with --board: include completed/abandoned tasks whose terminal time "
             "is within this many minutes (default: 120).",
    )
    p.add_argument("--label")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=_cmd_inbox)

    p = sub.add_parser(
        "find", help="substring search over title/prompt (a quick dedup probe; calling repo)"
    )
    p.add_argument("query")
    p.add_argument(
        "--repo", help="lane to search (local name or remote URL); default: calling repo"
    )
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=_cmd_find)

    p = sub.add_parser(
        "sweep",
        help="the dedup corpus for the calling repo: every non-abandoned task, "
             "newest first -- read these before creating a task to verify the "
             "work doesn't already exist",
    )
    p.add_argument(
        "--repo", help="lane to sweep (local name or remote URL); default: calling repo"
    )
    p.add_argument("--limit", type=int, default=500)
    p.set_defaults(func=_cmd_sweep)

    p = sub.add_parser("show", help="show one task")
    p.add_argument("task_id")
    p.set_defaults(func=_cmd_show)

    p = sub.add_parser(
        "claimant",
        help="task -> claiming worktree: which worktree owns a task (the inbound "
             "reverse of worktree-status). Reports the actual owner once claimed, "
             "else the pinned target worktree.",
    )
    p.add_argument("task_id")
    p.set_defaults(func=_cmd_claimant)

    p = sub.add_parser("events", help="show a task's audit trail")
    p.add_argument("task_id")
    p.set_defaults(func=_simple("events", "task_id"))

    p = sub.add_parser("payload", help="show a task's resolved payload (inline or blob)")
    p.add_argument("task_id")
    p.add_argument(
        "--raw", action="store_true", help="print the payload content only (not JSON)"
    )
    p.set_defaults(func=_cmd_payload)

    p = sub.add_parser(
        "consume",
        help="resume-and-consume a handoff: drive it to completed (idempotent; "
        "a spent completed handoff is refused, exit 3, never replayed) "
        "and print its payload -- the successor's one-command pickup",
    )
    p.add_argument("task_id")
    p.add_argument(
        "--worker-id", dest="worker_id",
        help="owner id (default: from machine/worktree)",
    )
    p.add_argument("--machine", help="override the resolved machine identity")
    p.add_argument("--worktree", help="override the resolved worktree identity")
    p.add_argument(
        "--repo",
        help="lane to consume from (local name or remote URL). Default: the calling repo.",
    )
    p.add_argument("--result-ref", help="result ref recorded on completion")
    p.add_argument(
        "--defer-complete", action="store_true",
        help="takeover pickup: approve->claim->start + print the brief, but do "
             "NOT complete -- the successor completes explicitly when the goal "
             "is reached (deferred completion)",
    )
    p.set_defaults(func=_cmd_consume)

    p = sub.add_parser("recover", help="requeue expired-lease tasks")
    p.set_defaults(func=lambda args: _emit(_client(args).recover()))

    p = sub.add_parser("watch", help="stream task events (SSE) as JSON lines")
    p.set_defaults(func=_cmd_watch)

    p = sub.add_parser(
        "mcp", help="run the local stdio MCP server (per-agent interaction layer)"
    )
    p.set_defaults(func=_cmd_mcp)

    p = sub.add_parser(
        "schedule",
        help="scheduler/timer producer: turn a JSON schedule spec into deferred "
             "tasks (idempotent per occurrence via not_before + dedup_key), and "
             "manage a persisted registry of recurring jobs + single-producer leases",
    )
    sched_sub = p.add_subparsers(dest="schedule_command", required=True)
    sp = sched_sub.add_parser(
        "tick",
        help="create every currently-due occurrence once, then exit (drive from "
             "cron / a systemd timer / manage_schedule)",
    )
    sp.add_argument(
        "spec", nargs="?",
        help="path to the JSON schedule spec (omit with --registry to tick the "
             "coordinator's registered schedules)",
    )
    sp.add_argument(
        "--registry", action="store_true",
        help="tick the coordinator's registered schedules instead of a spec file",
    )
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser(
        "serve", help="built-in timer: reload the spec and tick every --interval seconds"
    )
    sp.add_argument(
        "spec", nargs="?",
        help="path to the JSON schedule spec (omit with --registry)",
    )
    sp.add_argument(
        "--interval", type=float, default=60.0, help="seconds between ticks (default: 60)"
    )
    sp.add_argument(
        "--registry", action="store_true",
        help="lease-gated registry mode: tick the coordinator's registered "
             "schedules only while this host holds the job-lease",
    )
    sp.add_argument(
        "--lease-scope", help="job-lease scope to hold in --registry mode (required)"
    )
    sp.add_argument(
        "--holder", help="this producer's identity (the machine) in --registry mode"
    )
    sp.add_argument("--holder-session", help="optional live-session handle of the holder")
    sp.add_argument(
        "--lease-ttl", type=float,
        help="observability-only lease expiry seconds (never auto-steals)",
    )
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser(
        "register",
        help="register (upsert) every schedule in a spec file into the "
             "coordinator's persisted registry",
    )
    sp.add_argument("spec", help="path to the JSON schedule spec to register")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser("list", help="list registered schedules")
    sp.add_argument(
        "--active", action="store_true", help="only non-paused schedules"
    )
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser(
        "inspect", help="show one registered schedule + its next occurrences + lease"
    )
    sp.add_argument("id", help="the schedule id")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser("remove", help="delete a registered schedule")
    sp.add_argument("id", help="the schedule id")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser("pause", help="pause a registered schedule (keep its definition)")
    sp.add_argument("id", help="the schedule id")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser("resume", help="resume a paused schedule")
    sp.add_argument("id", help="the schedule id")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser("lease-list", help="list held schedule job-leases")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser("lease-show", help="show the job-lease for a scope")
    sp.add_argument("scope", help="the lease scope")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser(
        "lease-acquire",
        help="acquire/renew a job-lease (pin-not-failover: never steals a lease "
             "held by another holder)",
    )
    sp.add_argument("scope", help="the lease scope")
    sp.add_argument("--holder", required=True, help="this holder's identity (the machine)")
    sp.add_argument("--holder-session", help="optional live-session handle")
    sp.add_argument("--ttl", type=float, help="observability-only expiry seconds")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser(
        "lease-release", help="release a job-lease (use --force to reassign a stuck one)"
    )
    sp.add_argument("scope", help="the lease scope")
    sp.add_argument("--holder", required=True, help="the releasing holder's identity")
    sp.add_argument(
        "--force", action="store_true", help="reassign a lease held by another holder"
    )
    sp.set_defaults(func=_cmd_schedule)

    p = sub.add_parser(
        "emitter",
        help="lease-gated periodic command emitter managed by the singleton supervisor",
    )
    emitter_sub = p.add_subparsers(dest="emitter_command", required=True)
    ep = emitter_sub.add_parser("tick", help="run one lease-gated emitter tick")
    ep.add_argument("spec", help="path to the JSON command-emitter spec")
    ep.add_argument("--holder", required=True, help="this producer's machine identity")
    ep.set_defaults(func=_cmd_emitter)
    ep = emitter_sub.add_parser(
        "serve", help="run a command emitter on its declared interval"
    )
    ep.add_argument("spec", help="path to the JSON command-emitter spec")
    ep.add_argument("--holder", required=True, help="this producer's machine identity")
    ep.set_defaults(func=_cmd_emitter)

    p = sub.add_parser(
        "webhook",
        help="reactive producer: serve an HTTP app mapping git-forge PR-merge "
             "and telemetry events onto tasks",
    )
    p.add_argument("--config", help="path to the JSON webhook config (optional)")
    p.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=9331, help="bind port (default: 9331)")
    p.set_defaults(func=_cmd_webhook)

    p = sub.add_parser(
        "supervise",
        help="embody spawn supervisor: turn queued (label-gated) tasks into host "
             "embody autopilots, exactly once each, via atomic spawn reservations",
    )
    p.add_argument("--repo", help="lane to supervise (default: the calling repo)")
    p.add_argument(
        "--all-repos", action="store_true", help="supervise every lane (no repo scope)"
    )
    p.add_argument(
        "--label", action="append",
        help="only spawn queued tasks carrying this label (repeatable; opt-in gate)",
    )
    p.add_argument(
        "--max-concurrent", type=int, default=1,
        help="cap on in-flight spawns (default: 1)",
    )
    p.add_argument(
        "--max-attempts", type=int, default=3,
        help="dead-letter a task after this many failed spawn attempts "
             "(default: 3; 0 = retry forever)",
    )
    p.add_argument(
        "--label-max-attempts", action="append", metavar="LABEL=N",
        help="per-label override of --max-attempts (repeatable), e.g. "
             "--label-max-attempts code-review=3 so raising one "
             "label's bound doesn't revive another label's stale tasks "
             "(N=0 = retry forever for that label)",
    )
    p.add_argument(
        "--no-heartbeat", action="store_true",
        help="don't hold the lease of confirmed-alive embodied workers "
             "(default: heartbeat live workers so a quiet-but-alive session's "
             "lease doesn't expire)",
    )
    p.add_argument(
        "--embody-backend", choices=["headless", "cli"], default="headless",
        help="how the supervisor embodies a claimed task by default: 'headless' "
             "(default) -- a headless agent-bridge ACP session (no mux, no "
             "CLI-start-prompt), the right body for self-contained autonomous "
             "sweeps; 'cli' -- a CLI-backed autopilot worktree session (mux, "
             "attachable). Per-label overrides: --cli-label (force CLI when the "
             "default is headless) / --headless-label (force headless when the "
             "default is cli).",
    )
    p.add_argument(
        "--headless-label", action="append", metavar="LABEL",
        help="force queued tasks carrying this label to a HEADLESS agent-bridge "
             "ACP session (repeatable). Only meaningful with --embody-backend cli "
             "(headless is already the default); local (non-pool) mode only.",
    )
    p.add_argument(
        "--cli-label", action="append", metavar="LABEL",
        help="force queued tasks carrying this label to a CLI-backed autopilot "
             "(mux, attachable) instead of the default headless body (repeatable). "
             "The opt-out for a lane that is headless-by-default; local "
             "(non-pool) mode only.",
    )
    p.add_argument(
        "--headless-agent", default="task-worker", metavar="AGENT",
        help="agent-bridge agent name used for headless embody bodies "
             "(default: task-worker)",
    )
    p.add_argument(
        "--verify-timeout", type=int, default=0,
        help="embody: wait up to N seconds for the spawned session (0 = don't wait)",
    )
    p.add_argument(
        "--interval", type=float, default=30.0,
        help="serve loop poll interval in seconds (default: 30)",
    )
    p.add_argument(
        "--no-reactive", action="store_true",
        help="don't wake the serve loop on a worker turn-end; wait the full "
             "--interval each cycle (default: react to an embodied worker settling "
             "a turn so a completed goal is reconciled and the next task embodied "
             "promptly; the poll interval stays the floor)",
    )
    p.add_argument(
        "--reactive-interval", type=float, default=2.0,
        help="how often (seconds) the reactive wait re-samples embodied workers' "
             "turn state within one --interval (default: 2)",
    )
    p.add_argument(
        "--once", action="store_true", help="run a single supervision cycle and exit"
    )
    p.add_argument(
        "--pool",
        help="fleet mode: comma-separated host aliases to dispatch embody bodies "
             "to (first live host wins). Omit for local spawn on this machine.",
    )
    p.add_argument(
        "--origin",
        help="fleet mode: this coordinator's own SSH alias, which dispatched "
             "bodies report their lease back to (default: the resolved local "
             "machine). Required when the local machine can't be resolved.",
    )
    p.add_argument(
        "--headless", action="store_true",
        help="fleet (--pool) mode: embody fleet bodies as HEADLESS agent-bridge "
             "ACP sessions on the pool host (via --headless-agent) instead of "
             "CLI/mux embody -- sidesteps the CLI startup-seed 'Loading...' hang, "
             "so bounded sweeps embody reliably on a remote pool host with no "
             "human attach. Ignored outside --pool mode.",
    )
    p.add_argument(
        "--evaluator", metavar="SPEC",
        help="path to an evaluator spec (JSON). When set, each cycle feeds every "
             "newly-terminal task's lifecycle event to the evaluator and applies "
             "its decisions (emit a follow-up task) -- the service-driven loop-"
             "advancement pass (emitters-and-evaluators). See 'evaluate'.",
    )
    p.set_defaults(func=_cmd_supervise)
    # Registration management subcommands (registered-supervision). Optional: the
    # bare `supervise` (no subcommand) remains the transitional foreground loop,
    # while `supervise register|status|list|remove` manage durable registrations
    # that the singleton supervisor daemon runs. See the vision Behavior
    # *supervise-registers-and-returns*.
    sup_sub = p.add_subparsers(dest="supervise_command")
    rp = sup_sub.add_parser(
        "register",
        help="add a durable supervision registration and RETURN its handle "
             "(does not run the loop; the singleton supervisor runs it)",
    )
    rp.add_argument(
        "--kind", choices=sorted(RegistrationKind.ALL), default="supervised-lane",
        help="the unit kind to register (default: supervised-lane)",
    )
    rp.add_argument(
        "--id", help="explicit registration id (default: derived deterministically "
                     "from kind+scope+spec, so re-registering upserts)",
    )
    rp.add_argument(
        "--spec", metavar="JSON|@FILE",
        help="the unit's spec as inline JSON or @path; required for non-lane "
             "kinds. For supervised-lane, omit it to build the spec from the lane "
             "convenience flags below.",
    )
    rp.add_argument("--machine", help="scope the registration to this machine "
                    "(default: this host's resolved alias)")
    rp.add_argument("--env", help="scope the registration to this environment "
                    "(default: $AGENT_DISPATCH_ENV or 'default')")
    # supervised-lane convenience flags (used when --spec is omitted)
    rp.add_argument("--repo", help="lane to supervise (default: the calling repo)")
    rp.add_argument("--all-repos", action="store_true",
                    help="supervise every lane (no repo scope)")
    rp.add_argument("--label", action="append",
                    help="only spawn queued tasks carrying this label (repeatable)")
    rp.add_argument("--max-concurrent", type=int, default=1,
                    help="cap on in-flight spawns (default: 1)")
    rp.add_argument("--max-attempts", type=int, default=3,
                    help="dead-letter a task after this many failed spawn attempts "
                         "(default: 3; 0 = retry forever)")
    rp.add_argument("--label-max-attempts", action="append", metavar="LABEL=N",
                    help="per-label override of --max-attempts (repeatable)")
    rp.add_argument("--embody-backend", choices=["headless", "cli"], default="headless",
                    help="default embody body for the lane: 'headless' (default) "
                         "agent-bridge ACP, or 'cli' autopilot worktree session")
    rp.add_argument("--headless-label", action="append", metavar="LABEL",
                    help="force tasks carrying this label to a headless agent-bridge "
                         "ACP session (repeatable; use when --embody-backend cli)")
    rp.add_argument("--cli-label", action="append", metavar="LABEL",
                    help="force tasks carrying this label to a CLI autopilot instead "
                         "of the default headless body (repeatable)")
    rp.add_argument("--headless-agent", metavar="AGENT",
                    help="agent-bridge agent name for headless embody bodies")
    rp.add_argument("--evaluator", metavar="SPEC",
                    help="path to an evaluator spec (JSON) folded into the lane spec")
    rp.add_argument("--interval", type=float, default=30.0,
                    help="serve loop poll interval in seconds (default: 30)")
    rp.add_argument("--ensure", action="store_true",
                    help="after registering, ensure the singleton supervisor daemon "
                         "is running for this (machine, env) -- start it detached if "
                         "not (best-effort; a running daemon is a no-op)")
    rp.set_defaults(func=_cmd_supervise)
    rp = sup_sub.add_parser("status", help="query a registration by its handle")
    rp.add_argument("id", help="registration id")
    rp.set_defaults(func=_cmd_supervise)
    rp = sup_sub.add_parser("list", help="list registrations")
    rp.add_argument("--kind", choices=sorted(RegistrationKind.ALL),
                    help="filter by kind")
    rp.add_argument("--machine", help="filter by machine")
    rp.add_argument("--env", help="filter by environment")
    rp.add_argument("--active", action="store_true",
                    help="only active (non-paused) registrations")
    rp.set_defaults(func=_cmd_supervise)
    rp = sup_sub.add_parser("remove", help="remove a registration by its handle")
    rp.add_argument("id", help="registration id")
    rp.set_defaults(func=_cmd_supervise)
    rp = sup_sub.add_parser(
        "serve",
        help="run the singleton supervisor daemon (foreground): reconcile the "
             "registration registry into per-unit subprocesses, one master per "
             "(machine, env), single-instance-guarded",
    )
    rp.add_argument("--machine", help="scope: this machine (default: resolved alias)")
    rp.add_argument("--env", help="scope: this environment "
                    "(default: $AGENT_DISPATCH_ENV or 'default')")
    rp.add_argument("--interval", type=float, default=5.0,
                    help="reconcile poll interval in seconds (default: 5)")
    rp.add_argument("--once", action="store_true",
                    help="reconcile a single time and exit (still lease-guarded)")
    rp.add_argument("--no-single-instance", action="store_true",
                    help="skip the singleton election (deliberately unguarded; "
                         "for tests / diagnostics only)")
    rp.add_argument("--no-declared", action="store_true",
                    help="do not supervise the registrar's DECLARED profile set "
                         "(discovered pointers); run only store-backed registrations")
    rp.add_argument("--legacy-env", action="store_true",
                    help="ALSO supervise legacy AGENT_DISPATCH_SUPERVISE_* env "
                         "profiles (supervisor.env + supervisors/*.env) as declarations "
                         "-- the Phase 4 migration back-compat bridge, so switching a "
                         "host's supervisor unit to `serve` keeps its existing profiles "
                         "running until each is migrated to a first-class declaration. A "
                         "declaration of the same name wins over a legacy profile.")
    rp.set_defaults(func=_cmd_supervise)
    rp = sup_sub.add_parser(
        "daemon-status",
        help="show whether a supervisor daemon holds this (machine, env) scope "
             "and the registrations it would run",
    )
    rp.add_argument("--machine", help="scope: this machine (default: resolved alias)")
    rp.add_argument("--env", help="scope: this environment "
                    "(default: $AGENT_DISPATCH_ENV or 'default')")
    rp.set_defaults(func=_cmd_supervise)
    op = sup_sub.add_parser(
        "override",
        help="operator kill-switch: locally disable/enable one supervised unit "
             "(by registration id), out of band and taking precedence over its "
             "declaration + the discovery layer (a re-sync does not undo it)",
    )
    op_sub = op.add_subparsers(dest="override_command")
    od = op_sub.add_parser(
        "disable",
        help="disable a supervised unit now: the daemon winds it down on the next "
             "reconcile and keeps it down until re-enabled",
    )
    od.add_argument("id", help="registration id of the unit to disable "
                              "(see `supervise daemon-status` / `list`)")
    od.add_argument("--reason", help="why it is disabled (recorded for legibility)")
    od.set_defaults(func=_cmd_supervise)
    oe = op_sub.add_parser(
        "enable",
        help="clear a unit's override, returning it to its declared/registered state",
    )
    oe.add_argument("id", help="registration id of the unit to re-enable")
    oe.set_defaults(func=_cmd_supervise)
    ol = op_sub.add_parser("list", help="list the current operator overrides")
    ol.set_defaults(func=_cmd_supervise)
    op.set_defaults(func=_cmd_supervise)
    p = sub.add_parser(
        "reservations", help="inspect / manually control spawn reservations"
    )
    res_sub = p.add_subparsers(dest="reservations_command", required=True)
    rp = res_sub.add_parser("list", help="list spawn reservations")
    rp.add_argument("--task", help="filter by task id")
    rp.add_argument("--state", help="filter by state (comma-list ok)")
    rp.add_argument("--limit", type=int, default=200)
    rp.set_defaults(func=_cmd_reservations)
    rp = res_sub.add_parser(
        "fail", help="mark a reservation failed (releases the task for a fresh attempt)"
    )
    rp.add_argument("key")
    rp.add_argument("--detail")
    rp.set_defaults(func=_cmd_reservations)
    rp = res_sub.add_parser("settle", help="mark a reservation settled (attempt over)")
    rp.add_argument("key")
    rp.add_argument("--detail")
    rp.set_defaults(func=_cmd_reservations)

    p = sub.add_parser(
        "federation",
        help="federation runtime: register presence + drive the fenced-epoch "
             "coordinator lease over the rendezvous directory (hosted backend)",
    )
    fed_sub = p.add_subparsers(dest="federation_command", required=True)
    sp = fed_sub.add_parser(
        "run",
        help="run the federation loop (presence + lease) until stopped",
    )
    sp.add_argument(
        "--role", choices=sorted(_config.FEDERATION_ROLES),
        help="this node's role (default: AGENT_DISPATCH_FEDERATION_ROLE or peer)",
    )
    sp.add_argument(
        "--instance",
        help="stable directory id (default: AGENT_DISPATCH_FEDERATION_INSTANCE or "
             "the machine id)",
    )
    sp.add_argument(
        "--url", help="rendezvous coordinator URL (default: the hosted coordinator / "
                      "AGENT_DISPATCH_SHARED_URL)",
    )
    sp.add_argument("--token", help="bearer token for --url")
    sp.add_argument(
        "--interval", type=float,
        help="seconds between ticks (default: AGENT_DISPATCH_FEDERATION_INTERVAL)",
    )
    sp.add_argument(
        "--lease-ttl", type=float, dest="lease_ttl",
        help="staleness threshold before a standby fails over (lease-eligible roles)",
    )
    sp.add_argument(
        "--once", action="store_true",
        help="run a single tick and exit (print the resulting state)",
    )
    sp.set_defaults(func=_cmd_federation_run)
    sp = fed_sub.add_parser(
        "status", help="print the discovered coordinator + live peers"
    )
    sp.add_argument("--url", help="rendezvous coordinator URL (default: the hosted coordinator)")
    sp.add_argument("--token", help="bearer token for --url")
    sp.set_defaults(func=_cmd_federation_status)

    p = sub.add_parser("health", help="check coordinator health")
    p.set_defaults(func=lambda args: _emit(_client(args, ensure=False).health()))

    pe = sub.add_parser(
        "print-endpoint",
        help="print this machine's local coordinator base URL (for SSH-failover "
             "peer discovery)",
    )
    pe.set_defaults(func=_cmd_print_endpoint)

    # -- Loop recipes: list / describe / render / kick --------------------
    rp = sub.add_parser(
        "recipes",
        help="loop recipes -- the packaged shapes of long-running agentic work "
             "(reviewer / conflict-resolution / goal-driven), kickable ad-hoc",
    )
    rsub = rp.add_subparsers(dest="recipes_command", required=True)

    lp = rsub.add_parser("list", help="list the available recipes")
    lp.set_defaults(func=_cmd_recipes_list)

    dp = rsub.add_parser("describe", help="show a recipe's full descriptor")
    dp.add_argument("name", help="recipe name (see 'recipes list')")
    dp.set_defaults(func=_cmd_recipes_describe)

    rr = rsub.add_parser(
        "render",
        help="render a recipe with parameters (prints the fields; creates nothing)",
    )
    rr.add_argument("name")
    rr.add_argument(
        "--param", action="append", metavar="KEY=VALUE",
        help="a recipe parameter (repeatable), e.g. --param repo=owner/name --param pr=42",
    )
    rr.set_defaults(func=_cmd_recipes_render)

    kp = rsub.add_parser(
        "kick",
        help="carve an ad-hoc task from a recipe (optionally spawn a worker to "
             "drive it) -- the no-wrapper-service path",
    )
    kp.add_argument("name")
    kp.add_argument(
        "--param", action="append", metavar="KEY=VALUE",
        help="a recipe parameter (repeatable)",
    )
    kp.add_argument(
        "--repo",
        help="lane (repo) for the task: a local repo name or remote URL "
             "(default: the calling repo)",
    )
    kp.add_argument("--dedup-key", help="override the derived reserved-work dedup key")
    kp.add_argument(
        "--label", action="append", metavar="LABEL",
        help="extra label(s) to stamp on the kicked task (repeatable), merged "
             "with the recipe's own labels -- e.g. route the task onto a "
             "supervisor pool with '--label general'",
    )
    kp.add_argument(
        "--spawn", action="store_true",
        help="after creating, spawn a worker to drive the loop (best effort)",
    )
    kp.add_argument(
        "--spawn-backend", choices=["bridge", "embody"], default="embody",
        help="how to embody the worker: 'embody' (default) = a CLI autopilot in a "
             "fresh worktree with a full checkout (the right body for a recipe); "
             "'bridge' = a headless ACP worker",
    )
    kp.add_argument("--spawn-agent", default="task-worker")
    kp.add_argument(
        "--async", dest="run_async", action="store_true",
        help="with --spawn, don't wait for the worker (fire-and-forget)",
    )
    kp.add_argument("--verify-timeout", type=int, default=0)
    kp.add_argument(
        "--dry-run", action="store_true",
        help="print the create call the kick would make, without enqueuing it",
    )
    kp.set_defaults(func=_cmd_recipes_kick)

    # -- Drive-the-worktree-to-resolution: the enforced clean-up verb ------
    rvp = sub.add_parser(
        "resolve",
        help="drive THIS worktree to a clean, resolved final state after a loop "
             "(landed -> verify clean; abandoned -> unwind to base + reconcile source)",
    )
    rvp.add_argument(
        "--outcome", required=True, choices=["landed", "abandoned"],
        help="how the work ended: 'landed' (merged) or 'abandoned' (unwind to base)",
    )
    rvp.add_argument(
        "--base", metavar="BRANCH",
        help="base branch to unwind onto for --outcome abandoned "
             "(default: the branch's tracked upstream)",
    )
    rvp.add_argument(
        "--source", metavar="REF",
        help="the change/issue this worker was driving, folded into the "
             "source-reconcile instruction",
    )
    rvp.add_argument("--reason", help="abandonment reason (recorded on the plan)")
    rvp.add_argument(
        "--execute", action="store_true",
        help="perform the plan (destructive steps discard working-tree state); "
             "without it, the plan is printed and nothing runs",
    )
    rvp.set_defaults(func=_cmd_resolve)

    # -- Hibernate-the-wait: hand a blocking wait to the layer -------------
    rnp = sub.add_parser(
        "run",
        help="hand a blocking wait to the layer (hibernate-the-wait): run "
             "'-- <cmd>' to completion, then resume the worktree-affinitied "
             "worker via agent-bridge",
    )
    rnp.add_argument(
        "--resume", metavar="WORKTREE",
        help="the worker (worktree handle) to resume when the wait resolves "
             "(agent-bridge routes to whichever session is live then)",
    )
    rnp.add_argument("--task", metavar="ID", help="task id, folded into the resume nudge")
    rnp.add_argument("--message", help="override the resume nudge text")
    rnp.add_argument(
        "--detach", action="store_true",
        help="run the wait in a detached process that outlives this one, so the "
             "kicking worker can be torn down while it waits (true hibernation)",
    )
    rnp.add_argument(
        "command", nargs=argparse.REMAINDER,
        help="the blocking wait command, after '--' (e.g. -- agent-worktrees pr-watch 42)",
    )
    rnp.set_defaults(func=_cmd_run)

    # -- Evaluator: a producer's lifecycle handler ------------------------
    evp = sub.add_parser(
        "evaluate",
        help="feed one task lifecycle event through a declarative evaluator and "
             "apply its decisions (emit a follow-up task, or nothing)",
    )
    evp.add_argument("--spec", required=True, metavar="FILE", help="evaluator spec (JSON)")
    evp.add_argument(
        "--event-file", metavar="FILE",
        help="lifecycle event JSON (default: read from stdin)",
    )
    evp.add_argument(
        "--repo", help="lane for any emitted follow-up task (a local name or remote URL)"
    )
    evp.add_argument(
        "--dry-run", action="store_true",
        help="print the decisions without creating any follow-up task",
    )
    evp.set_defaults(func=_cmd_evaluate)

    dr = rsub.add_parser(
        "drive",
        help="decide the next loop step for a recipe given a --signal (the "
             "executable work/suspend/resolve rhythm); --execute performs the "
             "suspend + resolve legs",
    )
    dr.add_argument("name")
    dr.add_argument(
        "--signal", required=True,
        help="what just happened: 'start', a suspend-on event (e.g. change-updated), "
             "'work-done'/'idle', or a terminal signal (merged/landed/abandoned/closed)",
    )
    dr.add_argument("--resume", metavar="WORKTREE", help="worker to resume on a SUSPEND leg")
    dr.add_argument("--task", metavar="ID", help="task id, folded into a SUSPEND resume")
    dr.add_argument("--base", metavar="BRANCH", help="base branch for a RESOLVE unwind")
    dr.add_argument("--source", metavar="REF", help="change/issue for a RESOLVE reconcile")
    dr.add_argument(
        "--execute", action="store_true",
        help="perform the prescribed action (SUSPEND: spawn the detached waiter; "
             "RESOLVE: run the unwind). Needs --resume and a '--' wait command for SUSPEND.",
    )
    dr.add_argument(
        "wait_cmd", nargs="*",
        help="for --execute on a SUSPEND, the blocking wait command after '--'",
    )
    dr.set_defaults(func=_cmd_recipes_drive)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except DispatchError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 1
    except (ConnectionError, OSError) as exc:
        print(f"agent-dispatch: cannot reach coordinator: {exc}", file=sys.stderr)
        return 1
    except httpx.TransportError as exc:
        print(f"agent-dispatch: cannot reach coordinator: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
