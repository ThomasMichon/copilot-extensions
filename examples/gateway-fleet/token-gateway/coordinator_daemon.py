"""Experimental reference implementation; see the parent README."""


from __future__ import annotations

import json
import logging
import os
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

from credential_record import generation_for_expiry, valid_credential
RUN_DIR = Path(os.environ.get("HGF_RUN_DIR", str(Path.home() / "harness-gateway-fleet" / "run")))
SOCKET_PATH = RUN_DIR / "coordinator.sock"
LOG_PATH = Path(
    os.environ.get("HGF_LOG_PATH", str(Path.home() / "harness-gateway-fleet" / "coordinator.log"))
)
CONFIG_PATH = Path(
    os.environ.get("HGF_CONFIG_PATH", str(Path(__file__).parent / "coordinator_config.json"))
)


VERSION = "0.2.1"


_lock = threading.Lock()
_state: dict = {"token": None, "expires_epoch": None, "received_at": None, "generation": 0}
_delivered = {}
_delivery_wakeup = threading.Event()
_delivery_stop = threading.Event()
RETRY_SECONDS = 5

log = logging.getLogger("hgf.coordinator")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler(sys.stderr))
    log.setLevel(logging.INFO)


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {"consumers": []}


def _status() -> dict:
    with _lock:
        if _state["token"] is None or _state["expires_epoch"] is None:
            return {"has_token": False, "remaining_seconds": None}
        remaining = _state["expires_epoch"] - time.time()
        return {"has_token": remaining > 0, "remaining_seconds": max(remaining, 0.0)}


def _ingest(token: str, expires_epoch: float) -> dict:
    if not valid_credential(token, expires_epoch):
        raise ValueError("Invalid credential payload")
    with _lock:
        current_expiry = _state.get("expires_epoch")


        if current_expiry is not None and expires_epoch <= current_expiry:
            log.info(
                "ingest: stale push ignored (incoming_exp=%.0f <= current_exp=%.0f)",
                expires_epoch,
                current_expiry,
            )
            return {"ok": True, "accepted": False, "reason": "not-newer"}
        _state["token"] = token
        _state["expires_epoch"] = expires_epoch
        _state["received_at"] = time.time()
        _state["generation"] = generation_for_expiry(expires_epoch)
        _delivered.clear()
        generation = _state["generation"]
    log.info(
        "ingest: accepted token len=%d expires_in=%.0fs generation=%d",
        len(token),
        expires_epoch - time.time(),
        generation,
    )
    _delivery_wakeup.set()
    return {"ok": True, "accepted": True, "generation": generation}


def _redistribute(token: str, expires_epoch: float, generation: int) -> None:
    config = _load_config()
    payload = json.dumps({"token": token, "expires_epoch": expires_epoch, "generation": generation})
    for consumer in config.get("consumers", []):
        if _delivery_stop.is_set():
            return
        consumer_key = json.dumps(consumer, sort_keys=True)
        with _lock:
            if generation != _state["generation"]:
                return
            if _delivered.get(consumer_key) == generation:
                continue
        host = consumer["ssh_target"]
        remote_python = consumer.get("remote_python", "python3")
        remote_script = consumer["remote_script"]
        cache_dir = consumer.get("cache_dir", "~/.agent-vault/cache")
        vendor_path = consumer.get("vendor_path", "~/harness-gateway-fleet/vendor")
        ssh_key = consumer.get("ssh_key", "~/.ssh/coordinator_ed25519")
        remote_cmd = (
            f"AGENT_VAULT_CACHE=1 AGENT_VAULT_CACHE_DIR={cache_dir} "
            f"PYTHONPATH={vendor_path} {remote_python} {remote_script}"
        )
        cmd = [
            "ssh",
            "-i",
            os.path.expanduser(ssh_key),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=accept-new",
            host,
            remote_cmd,
        ]
        try:
            result = subprocess.run(
                cmd, input=payload, capture_output=True, text=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            acknowledgement = json.loads(result.stdout) if result.returncode == 0 else {}
            if (
                isinstance(acknowledgement, dict)
                and acknowledgement.get("ok") is True
                and acknowledgement.get("generation") == generation
            ):
                with _lock:
                    if _state["generation"] == generation:
                        _delivered[consumer_key] = generation
                log.info("redistribute: %s ok generation=%d", host, generation)
            else:
                log.error(
                    "redistribute: %s failed delivery rc=%d; retry retained",
                    host,
                    result.returncode,
                )
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as exc:
            log.error("redistribute: %s %s; retry retained", host, type(exc).__name__)


def _retry_delivery():
    with _lock:
        token, expiry, generation = (
            _state["token"], _state["expires_epoch"], _state["generation"],
        )
    if token is not None and expiry is not None and expiry > time.time():
        _redistribute(token, expiry, generation)


def _delivery_loop():
    while not _delivery_stop.is_set():
        _delivery_wakeup.wait(RETRY_SECONDS)
        _delivery_wakeup.clear()
        if _delivery_stop.is_set():
            break
        try:
            _retry_delivery()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            log.error("redistribution configuration failed: %s; retry retained", type(exc).__name__)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            line = self.rfile.readline()
            if not line:
                return
            request = json.loads(line.decode("utf-8"))
            cmd = request.get("cmd")
            if cmd == "status":
                response = _status()
            elif cmd == "ingest":
                response = _ingest(request["token"], float(request["expires_epoch"]))
            else:
                response = {"ok": False, "error": f"unknown cmd {cmd!r}"}
        except Exception as exc:
            log.error("handler error: %s", exc)
            response = {"ok": False, "error": str(exc)}
        self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))


def main() -> None:
    if not hasattr(socketserver, "ThreadingUnixStreamServer"):
        raise RuntimeError("Coordinator hosting requires Unix-domain socket support")

    class Server(socketserver.ThreadingUnixStreamServer):
        allow_reuse_address = True

    _setup_logging()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    server = Server(str(SOCKET_PATH), _Handler)
    os.chmod(SOCKET_PATH, 0o600)
    _delivery_stop.clear()
    _delivery_wakeup.clear()
    delivery_worker = threading.Thread(target=_delivery_loop, daemon=True)
    delivery_worker.start()
    log.info(
        "coordinator daemon v%s listening on %s (pid=%d)", VERSION, SOCKET_PATH, os.getpid()
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("coordinator daemon stopping")
    finally:
        _delivery_stop.set()
        _delivery_wakeup.set()
        delivery_worker.join(35)
        server.server_close()
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        if delivery_worker.is_alive():
            raise RuntimeError("Coordinator delivery worker did not stop")


if __name__ == "__main__":
    main()
