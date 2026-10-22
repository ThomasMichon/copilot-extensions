"""Experimental reference implementation; see the parent README."""


from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import random
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

GATEWAY_APP_RESOURCE = os.environ.get("HGF_GATEWAY_RESOURCE", "api://00000001-0000-4000-8000-000000000000")


SSH_TUNNEL_LOCAL_PORT = 10022
SSH_COORDINATOR_USER = os.environ.get("HGF_SSH_USER", "operator")
SSH_IDENTITY_FILE = Path(os.environ.get("HGF_SSH_IDENTITY_FILE", str(Path.home() / ".ssh" / "gateway_ed25519")))


SSH_KNOWN_HOSTS_FILE = Path.home() / "harness-gateway-fleet" / "ssh-tunnel-known-hosts"
REFRESH_THRESHOLD_SECONDS = 15 * 60


MIN_ACCEPTABLE_REMAINING_SECONDS = 20 * 60


MAX_RETRIES = 6
BASE_BACKOFF_SECONDS = 5.0

LOG_PATH = Path.home() / "harness-gateway-fleet-producer.log"
HEALTH_LOG_PATH = Path.home() / "harness-gateway-fleet" / "health.jsonl"
TUNNEL_PORTS = {"relay-a": SSH_TUNNEL_LOCAL_PORT, "worker-a": 10023, "worker-b": 10024}

log = logging.getLogger("hgf.producer")
health_log = logging.getLogger("hgf.health")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler(sys.stderr))
    log.setLevel(logging.INFO)
    HEALTH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    health_handler = TimedRotatingFileHandler(
        HEALTH_LOG_PATH, when="midnight", backupCount=30, utc=True, encoding="utf-8"
    )
    health_handler.setFormatter(logging.Formatter("%(message)s"))
    health_log.addHandler(health_handler)
    health_log.setLevel(logging.INFO)
    health_log.propagate = False


def _tunnel_health(port: int) -> dict[str, str]:
    try:
        with (
            socket.create_connection(("127.0.0.1", port), timeout=3) as connection,
            connection.makefile("rb") as stream,
        ):
            banner = stream.readline(256)
        if banner.startswith(b"SSH-2.0-") and banner.endswith(b"\n"):
            return {"status": "healthy", "reason": "ssh_banner"}
        return {"status": "unhealthy", "reason": "missing_ssh_banner"}
    except TimeoutError:
        return {"status": "unhealthy", "reason": "timeout"}
    except OSError:
        return {"status": "unhealthy", "reason": "connection_failed"}


def _record_health(result: int, attempt: int) -> None:
    sample = {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine": platform.node(),
        "pid": os.getpid(),
        "producer": {"status": "healthy" if result == 0 else "unhealthy", "attempt": attempt},
        "tunnels": {name: _tunnel_health(port) for name, port in TUNNEL_PORTS.items()},
    }
    health_log.info(json.dumps(sample, separators=(",", ":")))


def _ssh_coordinator(remote_args: list[str], stdin_payload: str | None = None) -> dict:
    SSH_KNOWN_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={SSH_KNOWN_HOSTS_FILE}",
        "-p",
        str(SSH_TUNNEL_LOCAL_PORT),
        "-i",
        str(SSH_IDENTITY_FILE),
        f"{SSH_COORDINATOR_USER}@127.0.0.1",
        "python3",
        "~/harness-gateway-fleet/coordinator_client.py",
        *remote_args,
    ]
    result = subprocess.run(
        cmd, input=stdin_payload, capture_output=True, text=True, timeout=30, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"ssh to coordinator failed rc={result.returncode} stderr={result.stderr.strip()}"
        )
    return json.loads(result.stdout.strip().splitlines()[-1])


def _coordinator_status() -> dict:
    return _ssh_coordinator(["status"])


def _mint_token() -> tuple[str, float]:
    pass


    result = subprocess.run(
        f'az account get-access-token --resource {GATEWAY_APP_RESOURCE} -o json',
        shell=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    payload = json.loads(result.stdout)
    token = payload["accessToken"]


    if "expires_on" in payload:
        expires_epoch = float(payload["expires_on"])
    else:
        expires_epoch = time.time() + 3600
    return token, expires_epoch


def _push_token(token: str, expires_epoch: float) -> dict:
    payload = json.dumps({"token": token, "expires_epoch": expires_epoch})
    return _ssh_coordinator(["ingest"], stdin_payload=payload)


def run_once(force: bool = False) -> int:
    try:
        status = _coordinator_status()
    except Exception as exc:
        log.error("status check failed: %s", exc)
        return 1

    remaining = status.get("remaining_seconds")
    if not force and status.get("has_token") and remaining is not None and remaining > REFRESH_THRESHOLD_SECONDS:
        log.info("coordinator token healthy (remaining=%.0fs); no push needed", remaining)
        return 0

    log.info(
        "coordinator token needs refresh (has_token=%s remaining=%s); minting",
        status.get("has_token"),
        remaining,
    )
    try:
        token, expires_epoch = _mint_token()
    except Exception as exc:
        log.error("mint failed: %s", exc)
        return 1

    minted_remaining = expires_epoch - time.time()
    if minted_remaining < MIN_ACCEPTABLE_REMAINING_SECONDS:
        log.warning(
            "minted token too stale to push (remaining=%.0fs < floor=%.0fs) -- "
            "az's own token cache likely hasn't rolled over yet; treating as a "
            "transient mint failure so the retry/backoff loop gives it time",
            minted_remaining,
            MIN_ACCEPTABLE_REMAINING_SECONDS,
        )
        return 1

    try:
        result = _push_token(token, expires_epoch)
    except Exception as exc:
        log.error("push failed: %s", exc)
        return 1

    if result.get("ok"):
        log.info(
            "push accepted=%s generation=%s expires_in=%.0fs",
            result.get("accepted", True),
            result.get("generation"),
            expires_epoch - time.time(),
        )
        return 0
    log.error("push rejected: %s", result)
    return 1


LOOP_POLL_SECONDS = 120


def run_cycle(force: bool = False) -> int:
    pass

    for attempt in range(1, MAX_RETRIES + 1):
        rc = run_once(force=force)
        _record_health(rc, attempt)
        if rc == 0:
            return 0
        if attempt == MAX_RETRIES:
            log.error("giving up after %d attempts", attempt)
            return 1
        backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 2)
        log.info("retrying in %.1fs (attempt %d/%d)", backoff, attempt + 1, MAX_RETRIES)
        time.sleep(backoff)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="mint+push even if coordinator's cache is fresh")
    parser.add_argument(
        "--loop",
        action="store_true",
        help=(
            "run forever (one process, started once via autorun/Run-key -- "
            "no external scheduler re-invokes it). Each cycle runs the normal "
            "retry/backoff sequence, then sleeps LOOP_POLL_SECONDS and repeats. "
            "A single cycle failing out after MAX_RETRIES is logged and the "
            "loop continues to the next cycle rather than exiting."
        ),
    )
    args = parser.parse_args()

    _setup_logging()
    if args.loop:
        log.info("starting in --loop mode (poll every %ds)", LOOP_POLL_SECONDS)
        while True:
            run_cycle(force=args.force)
            time.sleep(LOOP_POLL_SECONDS)
    return run_cycle(force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
