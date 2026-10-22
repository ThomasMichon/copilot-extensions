"""Experimental reference implementation; see the parent README."""


from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

GATEWAY_APP_RESOURCE = os.environ.get("HGF_GATEWAY_RESOURCE", "api://00000001-0000-4000-8000-000000000000")
GATEWAY_LOCAL_BASE = "http://127.0.0.1:18787"
MIN_ACCEPTABLE_REMAINING_SECONDS = 20 * 60

LOG_PATH = Path.home() / "harness-gateway-fleet" / "credential-push.log"

log = logging.getLogger("hgf.credential-push")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler(sys.stderr))
    log.setLevel(logging.INFO)


def _az_get_access_token(resource: str, *, tenant: str = "") -> tuple[str, float]:
    pass


    tenant_arg = f" --tenant {tenant}" if tenant else ""
    result = subprocess.run(
        f'az account get-access-token --resource {resource}{tenant_arg} -o json',
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


def _push(resource: str, tenant: str, token: str, expires_epoch: float, gateway_token: str) -> dict:
    resp = requests.post(
        f"{GATEWAY_LOCAL_BASE}/api/v1/credentials/push",
        headers={"Authorization": f"Bearer {gateway_token}"},
        json={
            "resource": resource,
            "tenant": tenant,
            "token": token,
            "expires_epoch": expires_epoch,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _verify(resource: str, tenant: str, expected_token: str, gateway_token: str) -> bool:
    resp = requests.post(
        f"{GATEWAY_LOCAL_BASE}/api/v1/relay",
        headers={"Authorization": f"Bearer {gateway_token}"},
        json={"action": "get-azure-token", "fields": {"resource": resource, "tenant": tenant}},
        timeout=15,
    )
    if resp.status_code != 200:
        log.error("verify: relay returned %d: %s", resp.status_code, resp.text)
        return False
    served = resp.json().get("token")
    ok = served == expected_token
    log.info("verify: relay served %s token", "the same" if ok else "A DIFFERENT")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", required=True, help="AAD resource/scope to mint, e.g. https://management.azure.com/")
    parser.add_argument("--tenant", default="", help="optional tenant override")
    parser.add_argument("--verify", action="store_true", help="read the token back via /api/v1/relay and confirm it matches")
    args = parser.parse_args()

    _setup_logging()

    try:
        gateway_token, _ = _az_get_access_token(GATEWAY_APP_RESOURCE)
    except Exception as exc:
        log.error("mint gateway bootstrap token failed: %s", exc)
        return 1

    try:
        resource_token, expires_epoch = _az_get_access_token(args.resource, tenant=args.tenant)
    except Exception as exc:
        log.error("mint resource token for %s failed: %s", args.resource, exc)
        return 1

    minted_remaining = expires_epoch - time.time()
    if minted_remaining < MIN_ACCEPTABLE_REMAINING_SECONDS:


        log.error(
            "minted resource token too stale to push (remaining=%.0fs < floor=%.0fs)",
            minted_remaining,
            MIN_ACCEPTABLE_REMAINING_SECONDS,
        )
        return 1

    try:
        result = _push(args.resource, args.tenant, resource_token, expires_epoch, gateway_token)
    except Exception as exc:
        log.error("push failed: %s", exc)
        return 1

    if not result.get("ok"):
        log.error("push rejected: %s", result)
        return 1
    log.info(
        "push accepted resource=%s expires_in=%.0fs", result.get("resource"), expires_epoch - time.time()
    )

    if args.verify:
        if not _verify(args.resource, args.tenant, resource_token, gateway_token):
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
