"""Trusted Linux containers adapt existing SSH preparation to native hosting."""

from __future__ import annotations

import asyncio
import json
import math
import re
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

from ssh_manager import ConnectionManager, LocalForward, SSHConfig, TargetBusyError, TargetLock
from ssh_manager.native_channel import InputPump, emit_progress, serve
from ssh_manager.remote_command import validate_descriptor

from . import lease, native_claims


def add_arguments(sub):
    for name in ("native-transport", "native-abort", "native-retirement"):
        p = sub.add_parser(name)
        p.add_argument("name")
        p.add_argument("--owner", dest="effort", required=True)
        p.add_argument("--execution-id", required=True)
        p.add_argument("--generation", required=True)
        if name == "native-transport":
            p.add_argument("--remote-command-json", type=json.loads, required=True)
            p.add_argument("--require-relay", action="store_true", default=True)
            p.add_argument("--no-plugin-staging", action="store_true", default=True)
            p.add_argument("--resume-infrastructure", action="store_true")
            p.add_argument("--retirement-only", action="store_true")
            p.add_argument("--local-forward", action="append", default=[])
            p.add_argument("--reverse-forward", action="append", default=[])
    p = sub.add_parser("remote-exec", help="Execute an exact UTF-8 command over trusted container SSH")
    p.add_argument("name")
    p.add_argument("--command-file", required=True)
    p.add_argument("--stdin", action="store_true")
    p.add_argument("--no-plugin-staging", action="store_true", default=True)
    p.add_argument("--require-relay", action="store_true", default=True)
    p.add_argument("--timeout", type=float, default=600)


def _ports(values):
    if len(values) > 32:
        raise ValueError("too many native forwards")
    result = []
    listeners = set()
    for value in values:
        if not re.fullmatch(r"[0-9]{1,5}:[0-9]{1,5}", value):
            raise ValueError("forward requires LISTEN:CONNECT decimal ports")
        listen, connect = map(int, value.split(":"))
        if not 0 < listen <= 65535 or not 0 < connect <= 65535 or listen in listeners:
            raise ValueError("forward ports are out of range or duplicated")
        listeners.add(listen)
        result.append((listen, connect))
    return result


async def _run(args, prepared, identity):
    from . import __main__ as cli

    config = SSHConfig(**prepared["ssh"])
    source = SimpleNamespace(get_ssh_config=lambda: config, refresh=lambda: config)
    manager = ConnectionManager()
    relay = None
    try:
        await manager.ensure_connected(args.name, source, [])
        forwards = prepared["reverse_forwards"]
        retirement_only = bool(getattr(args, "retirement_only", False))
        if not forwards and not retirement_only:
            raise RuntimeError("trusted container native hosting requires a credential relay")
        port = int(forwards[0].split(":")[0]) if forwards else None
        if any(listen == port for listen, _ in getattr(args, "reverse_forward", [])):
            raise ValueError("application forward collides with the required credential relay")
        if not retirement_only:
            relay = LocalForward(config, port, reverse_forwards=forwards, extra_options={"ExitOnForwardFailure": "yes"})
            await relay.establish()
        probe = (
            "import socket\n"
            f"with socket.create_connection(('127.0.0.1',{port}),timeout=2) as s:\n"
            " s.sendall(b'ping\\n\\n');f=s.makefile('rb');assert f.read(6)==b'pong\\n\\n'\n"
        )

        async def ready():
            if retirement_only:
                return
            if not relay.is_alive:
                await relay.refresh()
            response = await manager.exec_command(args.name, shlex.join(["python3", "-c", probe]), timeout=5)
            if response.exit_code != 0:
                raise RuntimeError("trusted container credential relay is unreachable")

        await ready()
        if args.command == "remote-exec":
            command_text = Path(args.command_file).read_text(encoding="utf-8-sig")
            data = sys.stdin.buffer.read(1048577) if args.stdin else None
            if data is not None and len(data) > 1048576:
                raise ValueError("remote command stdin exceeds the bounded input")
            channel = await manager.open_stdio_channel(args.name, "bash -lc " + shlex.quote(command_text))
            try:
                out, err = await asyncio.wait_for(channel.communicate(input=data), args.timeout)
                sys.stdout.buffer.write(out)
                sys.stderr.buffer.write(err)
                return channel.returncode
            except TimeoutError:
                print("Remote command deadline expired; execution outcome is uncertain.", file=sys.stderr)
                return 124
            finally:
                await manager.close_stdio_channel(args.name, channel)

        class Commands:
            async def exec_command(self, name, command, **options):
                await ready()
                return await manager.exec_command(name, command, **options)

        async def retire(result):
            result["recovery"] = {
                "ok": True, "skipped": True,
                "detail": "session files remain in the container; no host-workspace projection",
            }
            native_claims.retire(args.name, identity, result)

        emit_progress(args, "target-auth-env", "reached")
        return await serve(
            args, Commands(), config,
            f". {shlex.quote(prepared['remote_env'])}; " if prepared.get("remote_env") else "",
            namespace="container",
            require_owner=lambda: native_claims.require_owner(args.name, identity),
            mark_launch=lambda: native_claims.mark_launch(args.name, identity), retire=retire,
        )
    finally:
        if relay is not None:
            await relay.cancel()
        await manager.disconnect_all()
        if prepared.get("remote_env"):
            await asyncio.to_thread(cli.cleanup_remote_env, args.name, prepared["user"], prepared["remote_env"])


def command(args):
    from . import __main__ as cli
    from .lifecycle import inspect_container

    identity = None
    if args.command == "remote-exec" and (not math.isfinite(args.timeout) or not 0 < args.timeout <= 3600):
        raise ValueError("remote execution timeout must be in (0,3600]")
    if args.command != "remote-exec":
        identity = (args.execution_id, args.generation, args.effort)
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", item) for item in identity[:2]):
            raise ValueError("invalid native execution identity")
        if args.command == "native-retirement":
            print(json.dumps({"receipt": native_claims.retirement(args.name, identity)}))
            return 0
    lock = TargetLock(f"container:{args.name}", op=args.command)
    try:
        lock.acquire()
    except TargetBusyError:
        print(json.dumps({"event": "rejected", "code": "venue_busy"}), flush=True)
        return 75
    try:
        if args.command == "native-abort":
            native_claims.retire(args.name, identity)
            return 0
        if identity:
            validate_descriptor(args.remote_command_json)
            args.local_forward = _ports(args.local_forward)
            args.reverse_forward = _ports(args.reverse_forward)
            if args.retirement_only and (args.local_forward or args.reverse_forward):
                raise ValueError("retirement-only transport cannot acquire application forwards")
            cli._trusted_session_host_context(args.name)
            native_claims.reserve(args.name, identity, inspect_container(args.name)["Id"])
            print(json.dumps({"event": "reserved", "executionId": identity[0], "generation": identity[1]}), flush=True)
            args.native_input = InputPump()
            args.native_progress = lambda phase, status="started": emit_progress(args, phase, status)
            args.native_identity = identity
        with lease.session_admission(args.name, native_identity=identity):
            if getattr(args, "retirement_only", False):
                from dataclasses import asdict
                _, _, user, _ = cli._trusted_session_host_context(args.name)
                prepared = {"ssh": asdict(cli.prepare_ssh_config(args.name, user)), "reverse_forwards": []}
            else:
                args.host_relay_port = cli._require_live_relay_port()
                prepared = cli._prepare_session_host(args)
            return asyncio.run(_run(args, prepared, identity))
    except lease.ProviderAdmissionError as exc:
        print(json.dumps({"event": "rejected", "code": "venue_busy", "detail": str(exc)}), flush=True)
        return 75
    finally:
        lock.release()
