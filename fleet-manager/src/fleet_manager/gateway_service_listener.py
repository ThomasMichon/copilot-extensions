"""Explicit TLS listener bootstrap, independent of the live role policy."""

from __future__ import annotations

import ipaddress
import re
import ssl
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509

from . import planning
from .gateway_service_channel import TransportError, check, origin, shape
from .gateway_service_context import identifier


def external_origin(value: object) -> str:
    """Require one canonical, non-ephemeral HTTPS authority for public PoP."""
    selected = origin(value)
    parsed = urlsplit(selected)
    host = parsed.hostname
    assert host is not None
    try:
        address = ipaddress.ip_address(host)
        authority = f"[{address}]" if address.version == 6 else str(address)
    except ValueError:
        check(len(host) <= 253 and all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.split(".")), "invalid-external-origin")
        authority = host
    check(parsed.port != 0, "invalid-external-origin")
    if parsed.port not in (None, 443):
        authority += f":{parsed.port}"
    check(selected == "https://" + authority, "invalid-external-origin")
    return selected


def read_bounded(path: Path) -> bytes:
    with path.open("rb") as source:
        value = source.read(planning.MAX_BYTES + 1)
    check(len(value) <= planning.MAX_BYTES, "source-limit")
    return value


@dataclass(frozen=True)
class Listener:
    fleet: str
    gateway: str
    host: str
    port: int
    origin: str
    certificate: str
    key: str
    remote: bool = False
    external_origin: str | None = None

    @classmethod
    def load(cls, path: Path) -> Listener:
        """Read strict bootstrap; paths refer to externally provisioned files."""
        try:
            value = planning.decode(read_bounded(path))
        except planning.PlanError:
            raise TransportError("invalid-listener") from None
        check(isinstance(value, dict), "invalid-listener")
        shape(value, {"schema", "version", "fleet", "gateway", "host", "port",
                      "origin", "certificate", "key", "remote"}
              | ({"external_origin"} if "external_origin" in value else set()))
        check(value["schema"] == "fleet-manager.gateway-service-listener"
              and type(value["version"]) is int and value["version"] == 1,
              "invalid-listener")
        public = external_origin(value["external_origin"]) if "external_origin" in value else None
        return cls(identifier(value["fleet"]), identifier(value["gateway"]),
                   value["host"], value["port"], value["origin"],
                   value["certificate"], value["key"], value["remote"], public)

    def check_host(self, values: list[str]) -> None:
        """Explicit proxy mode accepts only its fixed authority, never forwarded identity."""
        if self.external_origin is not None:
            check(values == [urlsplit(self.external_origin).netloc], "origin-host-mismatch")

    def bound_origin(self, port: int) -> str:
        """Resolve the SDK's private ephemeral port, not the public proof target."""
        if self.port != 0:
            return self.origin
        parsed = urlsplit(self.origin)
        assert parsed.hostname is not None
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        return f"https://{host}:{port}"

    def tls(self) -> ssl.SSLContext:
        origin(self.origin)
        if self.external_origin is not None:
            external_origin(self.external_origin)
        check(type(self.port) is int and 0 <= self.port <= 65535
              and type(self.remote) is bool and isinstance(self.host, str),
              "invalid-listener")
        try:
            loopback = ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            loopback = self.host == "localhost"
        check(loopback or self.remote, "remote-bind-not-authorized")
        parsed = urlsplit(self.origin)
        check((parsed.port if parsed.port is not None else 443) == self.port
              and (self.port != 0 or loopback), "origin-port-mismatch")
        check(all(isinstance(p, str) and 0 < len(p) <= 4096
                  for p in (self.certificate, self.key)), "invalid-listener")
        try:
            certificate = x509.load_pem_x509_certificate(
                read_bounded(Path(self.certificate)))
            names = certificate.extensions.get_extension_for_class(
                x509.SubjectAlternativeName).value
            try:
                selected = ipaddress.ip_address(parsed.hostname)
                matches = selected in names.get_values_for_type(x509.IPAddress)
            except ValueError:
                matches = parsed.hostname in names.get_values_for_type(x509.DNSName)
        except (ValueError, x509.ExtensionNotFound):
            raise TransportError("invalid-certificate") from None
        check(matches, "origin-certificate-mismatch")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.certificate, self.key)
        return context
