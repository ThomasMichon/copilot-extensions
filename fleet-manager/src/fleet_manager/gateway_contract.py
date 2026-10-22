"""Bounded NEW Gateway wire contracts. Parsing is never authentication."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from .planning import PlanError, canonical, decode, digest

MAX_WIRE = 65536
INDEX_CONTRACT = "fleet-index-scoped-v1"
CREDENTIAL_ACTIONS = frozenset({"get-azure-token", "get-github-token", "get-access-token"})
CREDENTIAL_WORKERS = frozenset({"index-worker", "pr-worker"})


class GatewayError(ValueError):
    """Only static, non-secret reason codes cross the error boundary."""


def require(condition: bool, code: str = "invalid-contract") -> None:
    if not condition:
        raise GatewayError(code)


def text(value: Any, maximum: int = 256) -> str:
    require(isinstance(value, str))
    try:
        length = len(value.encode("utf-8"))
    except UnicodeError:
        raise GatewayError("invalid-contract") from None
    require(0 < length <= maximum)
    require(not any(ord(c) < 32 for c in value))
    return value


def number(value: Any, low: int = 1, high: int = 2**53) -> int:
    require(type(value) is int and low <= value <= high)
    return value


def strings(value: Any, maximum: int = 32, *, minimum: int = 1) -> tuple[str, ...]:
    require(type(value) is list and minimum <= len(value) <= maximum)
    result = tuple(text(item) for item in value)
    require(len(set(result)) == len(result))
    return result


def fields(value: Any, required: set[str], optional: set[str] = frozenset()) -> dict:
    require(type(value) is dict and required <= value.keys() and not value.keys() - required - optional)
    return value


def encode(value: Any) -> bytes:
    try:
        raw = canonical(value)
    except PlanError:
        raise GatewayError("invalid-contract") from None
    require(len(raw) <= MAX_WIRE, "wire-limit")
    return raw


def parse(raw: bytes) -> Any:
    require(type(raw) is bytes and len(raw) <= MAX_WIRE, "wire-limit")
    try:
        return decode(raw)
    except PlanError:
        raise GatewayError("invalid-contract") from None


def endpoint(value: Any, *, remote: bool = False) -> str:
    value = text(value, 2048)
    parts = urlsplit(value)
    require(parts.scheme == "https" and parts.hostname is not None, "encrypted-endpoint-required")
    require(not parts.username and not parts.password and not parts.query and not parts.fragment)
    require(parts.path in ("", "/"), "endpoint-must-be-origin")
    require(remote or parts.hostname in ("127.0.0.1", "::1", "localhost"), "remote-not-enabled")
    try:
        require(parts.port is not None, "explicit-port-required")
        number(parts.port, 1, 65535)
    except ValueError:
        raise GatewayError("invalid-endpoint") from None
    return value.rstrip("/")


@dataclass(frozen=True)
class ForgeScope:
    """Exact observation target; the provider must independently verify its permissions."""

    provider: str
    api_base: str
    repository: str
    permission: str
    authentication: str

    def __post_init__(self) -> None:
        require(self.provider in ("github", "azure-devops"), "unsupported-forge")
        base = text(self.api_base, 2048)
        try:
            parsed = urlsplit(base)
            port = parsed.port
        except ValueError:
            raise GatewayError("invalid-forge-endpoint") from None
        require(parsed.scheme == "https" and parsed.hostname is not None
                and parsed.username is None and parsed.password is None
                and not parsed.query and not parsed.fragment and not base.endswith("/")
                and "\\" not in base and not any(c.isspace() for c in base)
                and (port is None or 0 < port <= 65535), "invalid-forge-endpoint")
        repository = text(self.repository)
        parts = repository.split("/")
        require(len(parts) == 2 and all(part and part == part.strip() and part not in (".", "..")
                                       for part in parts)
                and not any(c in repository for c in "\\?#%"), "invalid-forge-repository")
        require(self.permission == "observe", "unsupported-forge-permission")
        require(self.authentication in ("basic", "bearer")
                and (self.provider != "github" or self.authentication == "bearer"),
                "unsupported-forge-authentication")

    @classmethod
    def load(cls, value: Any) -> ForgeScope:
        fields(value, set(cls.__dataclass_fields__))
        return cls(**value)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Enrollment:
    principal: str
    role: str
    service: str
    installation: str
    generation: int
    expires_at: int
    sources: tuple[str, ...]
    corpora: tuple[str, ...]
    accounts: tuple[str, ...]
    audiences: tuple[str, ...]
    scopes: tuple[str, ...]
    jobs: tuple[str, ...]
    proof_jwk: dict
    recipient_jwk: dict | None
    receipt_jwk: dict | None

    @classmethod
    def load(cls, value: Any) -> Enrollment:
        fields(value, {"principal", "role", "service", "installation", "generation", "expires_at",
                       "sources", "corpora", "accounts", "audiences", "scopes", "jobs", "proof_jwk"},
               {"recipient_jwk", "receipt_jwk"})
        for key in ("principal", "role", "service", "installation"):
            text(value[key])
        require(value["role"] in ("query", *CREDENTIAL_WORKERS, "credential-controller"))
        if value["role"] == "pr-worker":
            require(value["corpora"] == [], "pr-worker-corpus-denied")
        public_key(value["proof_jwk"], "EC")
        recipient = value.get("recipient_jwk")
        if recipient is not None:
            public_key(recipient, "RSA")
        receipt = value.get("receipt_jwk")
        if receipt is not None:
            validate_jwks({"keys": [receipt]})
        require(value["role"] != "credential-controller" or receipt is not None,
                "controller-signing-key-required")
        return cls(*(value[k] for k in ("principal", "role", "service", "installation")),
                   number(value["generation"]), number(value["expires_at"]),
                   strings(value["sources"]),
                   strings(value["corpora"], minimum=0 if value["role"] in
                           ("pr-worker", "credential-controller") else 1),
                   *(strings(value[k]) for k in ("accounts", "audiences", "scopes", "jobs")),
                   value["proof_jwk"], recipient, receipt)


def public_key(value: Any, kind: str = "OKP") -> None:
    require(type(value) is dict and value.get("kty") == kind, "invalid-public-key")
    require(not set(value) & {"d", "p", "q", "dp", "dq", "qi", "oth", "k"}, "private-key-in-config")
    if kind == "OKP":
        require(value.get("crv") == "Ed25519" and isinstance(value.get("x"), str))
    elif kind == "RSA":
        require(isinstance(value.get("n"), str) and isinstance(value.get("e"), str))
    else:
        require(value.get("crv") == "P-256" and isinstance(value.get("x"), str)
                and isinstance(value.get("y"), str), "invalid-public-key")


@dataclass(frozen=True)
class Backend:
    identity: str
    service: str
    installation: str
    generation: int
    expires_at: int
    corpus: str
    sources: tuple[str, ...]
    endpoint: str
    ca: str
    issuer: str
    jwks: dict
    contract: str

    @classmethod
    def load(cls, value: Any, remote: bool) -> Backend:
        fields(value, set(cls.__dataclass_fields__))
        for key in ("identity", "service", "installation", "corpus", "ca", "issuer", "contract"):
            text(value[key])
        validate_jwks(value["jwks"])
        return cls(*(value[k] for k in ("identity", "service", "installation")),
                   number(value["generation"]), number(value["expires_at"]), text(value["corpus"]),
                   strings(value["sources"]), endpoint(value["endpoint"], remote=remote),
                   value["ca"], value["issuer"], value["jwks"], value["contract"])


def validate_jwks(value: Any) -> None:
    fields(value, {"keys"})
    require(type(value["keys"]) is list and 0 < len(value["keys"]) <= 16, "trust-required")
    kids = []
    for key in value["keys"]:
        public_key(key)
        kids.append(text(key.get("kid")))
        require(key.get("alg") == "EdDSA" and key.get("use") == "sig", "invalid-signing-key")
    require(len(set(kids)) == len(kids), "duplicate-key")


@dataclass(frozen=True)
class GatewayConfig:
    fleet: str
    installation: str
    issuer: str
    audience: str
    jwks: dict
    enrollments: tuple[Enrollment, ...]
    revoked: tuple[str, ...]
    backends: tuple[Backend, ...]
    credential_rules: tuple[dict, ...]
    pending_limit: int
    lease_seconds: int
    tls: dict
    signing_key: str
    signing_kid: str
    identity: dict

    @classmethod
    def load(cls, value: Any) -> GatewayConfig:
        fields(value, {"schema", "version", "role", *cls.__dataclass_fields__, "remote"})
        require(value["schema"] == "fleet-manager.gateway" and type(value["version"]) is int
                and value["version"] == 1 and value["role"] == "gateway", "gateway-role-required")
        require(type(value["remote"]) is bool)
        for key in ("fleet", "installation", "issuer", "audience", "signing_key", "signing_kid"):
            text(value[key])
        validate_jwks(value["jwks"])
        auth_identity = fields(value["identity"], {"profile", "issuer", "jwks", "trust_valid_until"})
        require(auth_identity["profile"] == "jwt-pop-v1", "unsupported-profile")
        text(auth_identity["issuer"], 2048)
        fields(auth_identity["jwks"], {"keys"})
        require(type(auth_identity["jwks"]["keys"]) is list
                and 0 < len(auth_identity["jwks"]["keys"]) <= 16, "trust-required")
        for key in auth_identity["jwks"]["keys"]:
            public_key(key, "EC")
            text(key.get("kid"))
        number(auth_identity["trust_valid_until"])
        require(type(value["enrollments"]) is list and 0 < len(value["enrollments"]) <= 128)
        enrolled = tuple(Enrollment.load(item) for item in value["enrollments"])
        require(len({item.principal for item in enrolled}) == len(enrolled))
        revoked = value["revoked"]
        require(type(revoked) is list and len(revoked) <= 256)
        for item in revoked:
            text(item)
        require(type(value["backends"]) is list and len(value["backends"]) <= 16)
        backends = tuple(Backend.load(item, value["remote"]) for item in value["backends"])
        require(len({item.service for item in backends}) == len(backends))
        rules = value["credential_rules"]
        require(type(rules) is list and len(rules) <= 128)
        identities = set()
        for rule in rules:
            fields(rule, {"worker", "controller", "source", "account", "audience", "scopes", "action"},
                   {"forge"})
            for key in ("worker", "controller", "source", "account", "audience", "action"):
                text(rule[key])
            strings(rule["scopes"])
            require(rule["action"] in CREDENTIAL_ACTIONS, "unsupported-action")
            forge = ForgeScope.load(rule["forge"]) if "forge" in rule else None
            identity = (rule["worker"], rule["source"], rule["account"], rule["audience"], rule["action"],
                        tuple(rule["scopes"]), forge)
            require(identity not in identities, "ambiguous-provider-policy")
            identities.add(identity)
            worker = next((e for e in enrolled if e.principal == rule["worker"]), None)
            controller = next((e for e in enrolled if e.principal == rule["controller"]), None)
            require(worker is not None and worker.role in CREDENTIAL_WORKERS
                    and worker.recipient_jwk is not None)
            require((worker.role == "pr-worker") == (forge is not None), "forge-binding-required")
            require(controller is not None and controller.role == "credential-controller")
            for peer in (worker, controller):
                require(rule["source"] in peer.sources and rule["account"] in peer.accounts
                        and rule["audience"] in peer.audiences and set(rule["scopes"]) <= set(peer.scopes))
        tls = fields(value["tls"], {"host", "port", "certificate", "key"}, {"origin"})
        text(tls["host"])
        require(value["remote"] or tls["host"] in ("127.0.0.1", "::1", "localhost"), "remote-not-enabled")
        number(tls["port"], 0, 65535)
        text(tls["certificate"])
        text(tls["key"])
        require(not value["remote"] or "origin" in tls, "external-origin-required")
        if "origin" in tls:
            endpoint(tls["origin"], remote=value["remote"])
        return cls(*(value[k] for k in ("fleet", "installation", "issuer", "audience")), value["jwks"],
                   enrolled, tuple(revoked), backends, tuple(rules),
                   number(value["pending_limit"], 1, 256), number(value["lease_seconds"], 2, 60),
                   tls, value["signing_key"], value["signing_kid"], auth_identity)


def request(value: Any) -> dict:
    fields(value, {"request_id", "service_id", "installation_id", "generation", "operation", "parameters"})
    for key in ("request_id", "service_id", "installation_id", "operation"):
        text(value[key])
    number(value["generation"])
    require(type(value["parameters"]) is dict)
    encode(value)
    return value


def search(value: Any) -> dict:
    fields(value, {"q", "limit", "corpus"}, {"source", "language", "repo"})
    text(value["q"], 4096)
    number(value["limit"], 1, 100)
    text(value["corpus"])
    for key in ("source", "language", "repo"):
        if key in value:
            text(value[key])
    return value


def credential(value: Any) -> dict:
    fields(value, {"job", "source", "account", "audience", "scopes", "action", "recipient"}, {"forge"})
    for key in ("job", "source", "account", "audience", "action", "recipient"):
        text(value[key])
    strings(value["scopes"])
    require(value["action"] in CREDENTIAL_ACTIONS, "unsupported-action")
    if "forge" in value:
        ForgeScope.load(value["forge"])
    return value


def correlation(value: dict) -> dict:
    """Reuse operation/receipt content-digest conventions, never persist secret bytes."""
    return {key: value[key] for key in
            ("request_id", "service_id", "installation_id", "generation", "operation")} | {
                "request_digest": digest(value)}
