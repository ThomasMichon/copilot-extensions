"""Render transport-only cloudflared ingress; no account or identity authority."""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
from pathlib import Path
from urllib.parse import urlsplit

from .gateway_service_channel import TransportError, check
from .gateway_service_context import ServicePolicyError
from .gateway_service_listener import Listener, external_origin


def ingress(listener: Listener, *, ca_pool: str | None = None) -> dict:
    """Render a JSON-compatible YAML fragment for an owner-managed tunnel."""
    listener.tls()
    public = urlsplit(external_origin(listener.external_origin))
    private = urlsplit(listener.origin)
    check(listener.port != 0 and public.port is None, "cloudflare-port-unsupported")
    assert public.hostname is not None and private.hostname is not None
    try:
        ipaddress.ip_address(public.hostname)
    except ValueError:
        check("." in public.hostname, "cloudflare-hostname-required")
    else:
        raise TransportError("cloudflare-hostname-required")
    request: dict[str, str | bool] = {
        "originServerName": private.hostname,
        "httpHostHeader": public.netloc,
        "noTLSVerify": False,
        "http2Origin": False,
        "disableChunkedEncoding": False,
    }
    if ca_pool is not None:
        check(isinstance(ca_pool, str) and 0 < len(ca_pool) <= 4096
              and all(32 <= ord(c) < 127 for c in ca_pool), "invalid-ca-pool")
        request["caPool"] = ca_pool
    return {"ingress": [
        {"hostname": public.hostname, "service": listener.origin, "originRequest": request},
        {"service": "http_status:404"},
    ]}


def main(argv: list[str] | None = None) -> int:
    """Print a fragment; never install cloudflared or modify its configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tls-config", type=Path, required=True)
    parser.add_argument("--ca-pool")
    args = parser.parse_args(argv)
    try:
        document = ingress(Listener.load(args.tls_config), ca_pool=args.ca_pool)
    except (TransportError, ServicePolicyError, OSError) as exc:
        code = exc.code if isinstance(exc, (TransportError, ServicePolicyError)) else (
            "source-unavailable")
        logging.getLogger(__name__).error("cloudflare-ingress-refused code=%s", code)
        return 1
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
