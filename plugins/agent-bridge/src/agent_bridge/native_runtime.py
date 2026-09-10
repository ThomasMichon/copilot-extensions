"""Remote, officially installed native execution-host authority.

Uses the existing Session Host and native live registry, never an ACP session.
Launch retries inspect the recorded execution; they cannot spawn a replacement.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import time
from pathlib import Path

from .native_store import NativeError, NativeStore, identifier, signature
from .session_host import launcher
from .session_host.client import SessionHostClient

CAPABILITY = "codespace-native-host-v1"


def default_root() -> Path:
    from .config import load_config

    return Path(load_config().db_path).expanduser().parent / "native-runtime"


def process_matches(pid: int, ticks: str, boot_id: str) -> bool | None:
    if not isinstance(pid, int) or pid <= 1:
        return None
    current_boot = launcher._boot_id()
    if not current_boot or not boot_id or not ticks:
        return None
    if current_boot != boot_id:
        return False
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
        if fields[0] in {"Z", "X"}:
            return False
        current_ticks = fields[19]
    except (OSError, IndexError):
        current_ticks = ""
    if current_ticks:
        return current_ticks == ticks
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        pass
    return None


def descendant_of(pid: int, ancestor: int) -> bool:
    seen = set()
    while pid > 1 and pid not in seen and len(seen) < 128:
        if pid == ancestor:
            return True
        seen.add(pid)
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
            pid = int(fields[1])
        except (OSError, ValueError, IndexError):
            return False
    return False


class NativeRuntime:
    def __init__(self, root: Path | None = None, *, launch=launcher.launch_session_host, catalog: Path | None = None) -> None:
        self.store = NativeStore(root or default_root(), "native-runtime")
        self.launch = launch
        self.catalog = catalog or Path.home() / ".agent-bridge" / "session-hosts"

    def _state_path(self, execution_id: str) -> Path:
        return self.catalog / f"host-native-{identifier(execution_id)}.json"

    def _host_state(self, row: dict) -> dict | None:
        path = self._state_path(row["id"])
        if not path.exists():
            return None
        try:
            if path.is_symlink() or path.stat().st_size > 65536:
                raise ValueError("unsafe host record")
            state = json.loads(path.read_text(encoding="utf-8"))
            if (
                state.get("mode") != "native" or state.get("session_id") != row["id"]
                or state.get("execution_generation") != row["generation"]
                or not state.get("nonce") or state["nonce"] != row["data"].get("hostNonce")
            ):
                raise ValueError("host identity mismatch")
            if state.get("launch_pending"):
                return None
            if (
                any(type(state.get(key)) is not int or state[key] <= 1 for key in ("host_pid", "child_pid"))
                or type(state.get("port")) is not int or not 1 <= state["port"] <= 65535
            ):
                raise ValueError("invalid native process/endpoint identity")
            identity = {key: state.get(key) for key in (
                "host_pid", "child_pid", "host_start_ticks", "child_start_ticks", "boot_id", "nonce", "port",
            )}
            pinned = row["data"].get("hostIdentity")
            if pinned is not None and pinned != identity:
                raise ValueError("host incarnation changed")
            if pinned is None:
                self.store.update(row["id"], row["generation"], hostIdentity=identity)
            return state
        except (OSError, ValueError, TypeError) as exc:
            raise NativeError("identity_mismatch", "Native host authority cannot be verified") from exc

    def start(self, request: dict) -> dict:
        if not sys.platform.startswith("linux") or not launcher._boot_id():
            raise NativeError("unsupported_venue", "Native execution hosting requires Linux process identity", 400)
        execution_id = identifier(request["executionId"])
        generation = identifier(request["generation"])
        command, cwd = request["command"], request["cwd"]
        if not isinstance(command, str) or not command.strip() or "\0" in command or len(command.encode()) > 262144:
            raise NativeError("invalid_command", "Expected a bounded nonblank command", 400)
        if not isinstance(cwd, str) or not os.path.isabs(cwd) or not Path(cwd).is_dir():
            raise NativeError("invalid_cwd", "Native cwd must be an existing absolute venue directory", 400)
        from .session_host.execution_guard import check_catalog

        code, detail = check_catalog(self.catalog, "native", execution_id, generation)
        if code:
            raise NativeError("venue_busy" if code == 75 else "identity_mismatch", detail)
        expected_hash = signature({"command": command, "cwd": cwd})
        row, created = self.store.reserve(
            execution_id, generation, execution_id, request["codespace"], request["owner"],
            expected_hash, {"sessionId": None, "represented": False, "hostNonce": secrets.token_hex(32)},
            strict_identity=True,
        )
        if created:
            state_path = self._state_path(execution_id)
            if state_path.exists():
                raise NativeError("identity_mismatch", "Unrecorded host state already occupies this execution")
            from .session_host.execution_guard import admit_and_publish

            try:
                admit_and_publish(state_path, {
                    "mode": "native", "session_id": execution_id,
                    "execution_generation": generation, "nonce": row["data"]["hostNonce"],
                    "launch_pending": True, "host_pid": os.getpid(), "child_pid": 0,
                    "host_start_ticks": launcher._process_start_ticks(os.getpid()),
                    "child_start_ticks": "", "boot_id": launcher._boot_id(), "port": 0,
                }, "native", execution_id, generation)
            except Exception as exc:
                if state_path.exists():
                    raise NativeError("identity_mismatch", "Native admission authority already exists") from exc
                self.store.update(execution_id, generation, state="rejected", retired=True,
                                  noLaunch=True, error="venue_busy")
                raise NativeError("venue_busy", "Remote mode admission refused native launch") from exc
            env = {
                "AGENT_BRIDGE_NATIVE_EXECUTION_ID": execution_id,
                "AGENT_BRIDGE_NATIVE_GENERATION": generation,
                "TERM": "xterm-256color",
                "AGENT_BRIDGE_HOST_VENUE": f"codespace:{request['codespace']}",
            }
            from .config import config_dir

            env["AGENT_BRIDGE_CONFIG_DIR"] = str(config_dir())
            try:
                self.launch(
                    ["bash", "-lc", str(request.get("prelude") or "") + command],
                    cwd=cwd, env=env, state_dir=state_path.parent, ready_timeout=90,
                    state_file_name=state_path.name,
                    nonce=row["data"]["hostNonce"], session_id=execution_id,
                    terminal=True, unexpected_reap_seconds=0, active_reap_seconds=0,
                    start_paused=True,
                )
            except Exception:
                # A child may have started before a transport/launch failure.
                # Retain ownership and inspect this execution on retry.
                self.store.update(execution_id, generation, state="unreachable", error="launch_unconfirmed")
                raise
        return self.status(execution_id, generation)

    def status(self, execution_id: str, generation: str, *, db=None) -> dict:
        row = self.store.get(execution_id, generation)
        if row["data"].get("noLaunch") and row["data"].get("retired"):
            return {"executionId": execution_id, "generation": generation, "state": "stopped",
                    "represented": False, "sessionId": None, "retired": True,
                    "activated": False, "noLaunch": True, "error": row["data"].get("error")}
        state = self._host_state(row)
        if state is None:
            return {"executionId": execution_id, "generation": generation, "state": row["state"],
                    "represented": False, "sessionId": row["data"].get("sessionId"),
                    "retired": bool(row["data"].get("retired")), "activated": False}
        alive = process_matches(int(state["host_pid"]), state.get("host_start_ticks", ""), state.get("boot_id", ""))
        child_alive = process_matches(int(state["child_pid"]), state.get("child_start_ticks", ""), state.get("boot_id", ""))
        retired = False
        if alive is False and child_alive is False:
            try:
                os.killpg(int(state["child_pid"]), 0)
            except ProcessLookupError:
                retired = True
            except OSError:
                pass
        status = "stopping" if row["state"] == "stopping" else "starting"
        if retired:
            status = "stopped"
        elif state.get("state") == "child_exited" and child_alive is False:
            status = "stopping"
        elif alive is not True or child_alive is not True:
            status = "unreachable"
        session_id = row["data"].get("sessionId")
        represented = False
        if status == "starting" and session_id and db is not None:
            from .db import live_session_is_fresh

            try:
                live = db.get_live_session(session_id)
            except Exception:
                live = None
            represented = bool(live and live_session_is_fresh(live, time.time()))
            status = "ready" if represented else "unrepresented"
        row = self.store.update(
            execution_id, generation, state=status, represented=represented,
            exitCode=state.get("child_exit_code"), retired=retired,
        )
        return {
            "executionId": execution_id, "generation": generation, "state": status,
            "sessionId": session_id, "represented": represented, "host": state,
            "exitCode": row["data"].get("exitCode"),
            "retired": retired, "activated": bool(state.get("native_started")),
        }

    async def activate(self, execution_id: str, generation: str) -> dict:
        row = self.store.get(execution_id, generation)
        if row["state"] in {"stopping", "stopped", "rejected"}:
            raise NativeError("not_running", "Native execution is being retired")
        state = self._host_state(row)
        if state is None or process_matches(
            int(state["host_pid"]), state.get("host_start_ticks", ""), state.get("boot_id", ""),
        ) is not True:
            raise NativeError("identity_mismatch", "Native host identity is not live")
        client = await SessionHostClient.connect(port=int(state["port"]))
        try:
            await asyncio.wait_for(
                client.activate(child_pid=int(state["child_pid"]), nonce=state["nonce"].encode()), 5,
            )
        finally:
            await client.close()
        return self.status(execution_id, generation)

    def bind_registration(self, execution_id: str, generation: str, session_id: str, pid: int) -> None:
        row = self.store.get(execution_id, generation)
        state = self._host_state(row)
        if state is None or row["state"] in {"stopped", "rejected"}:
            raise NativeError("identity_mismatch", "Native execution has no live host")
        child_pid = int(state["child_pid"])
        if (
            process_matches(child_pid, state.get("child_start_ticks", ""), state.get("boot_id", "")) is not True
            or not isinstance(pid, int) or not descendant_of(pid, child_pid)
        ):
            raise NativeError("identity_mismatch", "Registration does not belong to the owned native process")
        previous_pid = row["data"].get("registrationPid")
        if previous_pid and previous_pid != pid:
            old_alive = process_matches(
                previous_pid, row["data"].get("registrationTicks", ""), state.get("boot_id", ""),
            )
            if old_alive is not False:
                raise NativeError("registration_conflict", "A different live extension owns this execution")
        self.store.update(
            execution_id, generation, sessionId=session_id, registrationPid=pid,
            registrationTicks=launcher._process_start_ticks(pid),
        )

    async def stop(self, execution_id: str, generation: str, *, ownership: dict | None = None) -> dict:
        try:
            row = self.store.get(execution_id, generation)
        except NativeError as exc:
            if exc.code != "not_found" or not ownership or not ownership.get("owner") or not ownership.get("codespace"):
                raise
            if self._state_path(execution_id).exists():
                raise NativeError("retirement_unconfirmed", "Unindexed native authority is present")
            row, created = self.store.tombstone(
                execution_id, generation, ownership["codespace"], ownership["owner"],
            )
            if created:
                return self.status(execution_id, generation)
        state = self._host_state(row)
        status = self.status(execution_id, generation)
        if status.get("retired"):
            return status
        self.store.update(execution_id, generation, state="stopping")
        if state is None:
            raise NativeError("retirement_unconfirmed", "No authenticated host identity is available for retirement")
        if process_matches(
            int(state["host_pid"]), state.get("host_start_ticks", ""), state.get("boot_id", ""),
        ) is not True:
            raise NativeError("retirement_unconfirmed", "Native host identity is not live; ownership is retained")
        client = await SessionHostClient.connect(port=int(state["port"]))
        try:
            await asyncio.wait_for(
                client.retire(child_pid=int(state["child_pid"]), nonce=state["nonce"].encode()), 12,
            )
        finally:
            await client.close()
        for _ in range(100):
            status = self.status(execution_id, generation)
            if status.get("retired"):
                return status
            await asyncio.sleep(.1)
        raise NativeError("retirement_unconfirmed", "Native child exit was not confirmed; ownership is retained")

    def represented_operation(self, execution_id: str, generation: str, operation: str, params: dict) -> dict:
        from .client import BridgeClient

        client = BridgeClient.from_config()
        current = self.status(execution_id, generation, db=client)
        if current["state"] != "ready" or not current.get("represented"):
            raise NativeError("unrepresented", "Native registration is unavailable; no ACP fallback is permitted", 503)
        session_id = current["sessionId"]
        if params.get("expectedSessionId") != session_id:
            raise NativeError("identity_mismatch", "Native live-session identity changed")
        if operation == "message":
            return client.send_live_message(
                session_id, sender=params["sender"], body=params["body"],
                expected_session_id=session_id, idempotency_key=params["messageId"],
                kind=params.get("kind", "prompt"), reply_to=params.get("replyTo"),
                wait=bool(params.get("wait")), wait_timeout=float(params.get("waitTimeout", 120)),
            )
        return client.get_live_result_snapshot(session_id)


def command(args) -> int:
    """Internal remote management boundary, invoked through provider transport."""
    if args.native_host_action == "capabilities":
        print(json.dumps({"capability": CAPABILITY, "version": 1,
                          "supported": sys.platform.startswith("linux") and bool(launcher._boot_id())}))
        return 0
    if args.native_host_action == "guard":
        from .session_host.execution_guard import check_catalog

        code, detail = check_catalog(None, args.requested_mode)
        print(detail)
        return code
    runtime = NativeRuntime()
    try:
        if args.native_host_action == "start":
            request = json.loads(sys.stdin.read(524289))
            result = runtime.start(request)
        else:
            from .client import BridgeClient

            if args.native_host_action == "activate":
                result = asyncio.run(runtime.activate(args.execution_id, args.expected_generation))
            elif args.native_host_action == "stop":
                ownership = json.loads(sys.stdin.read(524289)) if args.request_stdin else None
                result = asyncio.run(runtime.stop(args.execution_id, args.expected_generation, ownership=ownership))
            elif args.native_host_action in {"message", "result"}:
                params = json.loads(sys.stdin.read(524289))
                result = runtime.represented_operation(
                    args.execution_id, args.expected_generation, args.native_host_action, params,
                )
            else:
                result = runtime.status(
                    args.execution_id, args.expected_generation,
                    db=BridgeClient.from_config(),
                )
        print(json.dumps(result))
        return 0
    except NativeError as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}))
        return 75 if exc.code == "venue_busy" else 78
