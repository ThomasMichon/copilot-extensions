#!/usr/bin/env python3
"""Governed-feed index discovery and trust for `tools/build_toolchain_lock.py`.

Split out of that module (which was itself split out of
`tools/build_python_artifacts.py`) to stay under
`tools/check-module-size.py`'s 1,000-line cap for new/unbaselined files --
this is a component boundary, not a reusability concern: every name here is
still imported back into, and re-exported by, `build_toolchain_lock.py`,
which remains importable exactly as before for every existing caller.

Owns "what index does this machine trust" -- discovering `uv`'s own
effective default index across project/user/system config and environment
variables (mirroring `uv`'s own precedence), and validating that index
against an affirmative, machine-local trust policy
(`BUILD_PYTHON_ARTIFACTS_TRUSTED_INDEX_HOSTS`) before anything is allowed to
call it "governed". `build_toolchain_lock.py` owns a DIFFERENT, downstream
concern: resolving one locked venv from whatever index this module
validates.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sys
import urllib.parse
from pathlib import Path

try:  # tomllib is stdlib on 3.11+; tomli backports it for this repo's
    # 3.10 support floor -- mirrors uv_editable_ref's own identical fallback.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

_GOVERNED_FEED_DEFAULT_INDEX_ENV_VARS = ("UV_DEFAULT_INDEX", "UV_INDEX_URL")
_PUBLIC_PYPI_HOSTS = {"pypi.org", "pypi.python.org", "test.pypi.org"}
#: Affirmative trust policy for `resolve_toolchain_lock`'s governed-feed
#: gate (see `_governed_feed_configured`'s own docstring): a comma-
#: separated allowlist of hostnames the MACHINE explicitly asserts are its
#: own governed feed. Never committed/hardcoded here -- this repo stays
#: feed-neutral (`tools/check-feed-neutrality.py`); only a machine's own
#: local environment ever populates this.
_TRUSTED_INDEX_HOSTS_ENV_VAR = "BUILD_PYTHON_ARTIFACTS_TRUSTED_INDEX_HOSTS"


def _credential_free_index_identity(url: str) -> str:
    """``url`` with any embedded ``user:pass@`` userinfo, query string, AND
    fragment stripped -- for HUMAN-READABLE DISPLAY only (a diagnostic
    message naming the index a caller validated). `uv`/PEP 508 index URLs
    may legally carry credentials in any of these three places, and none
    of them must ever be interpolated into a message shown to a human,
    per this effort's own "never log credentials" rule.

    NEVER used for persistence or identity comparison: a query parameter
    can legitimately carry a tenant/feed selector, not just a credential
    -- two URLs differing only by query would collapse to the same
    redacted string here and be wrongly treated as the same index.
    `_opaque_index_identity` is the persisted/compared identity; this
    function is for display only."""
    parts = urllib.parse.urlsplit(url)
    if not parts.username and not parts.password and not parts.query and not parts.fragment:
        return url
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc, query="", fragment=""))


def _provenance_key_dir() -> Path:
    """Per-user, host-wide directory for `_opaque_index_identity`'s keyed-
    hash material -- mirrors `tools/_admission_protocol.py`'s own
    `admission_dir()` convention (the established per-machine, per-user
    state location for this repo's tooling) rather than inventing a new
    one."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "copilot-extensions" / "build-python-artifacts"


def _provenance_key() -> bytes:
    """A stable, per-machine random key used to compute
    `_opaque_index_identity` as a KEYED digest (HMAC) rather than a bare
    hash of the raw URL. A bare `sha256(url)` is not credential-safe: most
    of a governed-feed URL is predictable (scheme, host, path), so the
    digest becomes an OFFLINE verifier an attacker can use to brute-force
    a low-entropy embedded password/token without ever needing network
    access to the real feed. Keying the hash with machine-local secret
    material (never committed, never derived from the URL itself) closes
    that offline-verification path -- an attacker without this file can no
    longer test guesses against the persisted digest at all.

    Generated once per machine and reused -- not per call -- so the same
    (index, python) identity keeps comparing equal across separate
    invocations, which `resolve_toolchain_lock`'s reuse/provenance-match
    contract depends on. Created with restrictive, owner-only permissions
    (mirroring the index-config temp file in `build_toolchain_lock.py`);
    a race with another process creating it first is resolved by reading
    back whatever that process wrote, never by two processes using two
    different keys."""
    path = _provenance_key_dir() / "provenance-key"
    try:
        existing = path.read_bytes()
    except OSError:
        existing = b""
    if len(existing) == 32:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process won the race to create it first -- trust
        # whatever key it wrote rather than risk two processes disagreeing
        # on the key (which would make their persisted identities
        # incomparable).
        return path.read_bytes()
    with os.fdopen(fd, "wb") as key_file:
        key_file.write(key)
    return key


def _opaque_index_identity(url: str) -> str:
    """A secret-safe, KEYED digest of the FULL validated index URL --
    including any embedded userinfo, query string, and fragment -- used
    to persist to disk (a provenance marker) and to compare whether two
    calls share EXACTLY the same effective index. A query parameter can
    select a tenant/feed, not just carry a credential, so redacting
    (stripping) it before comparing -- as `_credential_free_index_identity`
    does for display -- would wrongly treat two distinct feeds sharing a
    host/path as identical and silently reuse a venv across them. Hashing
    the full, unredacted value instead preserves its full distinguishing
    power.

    Uses `hmac`, keyed with a per-machine secret (`_provenance_key`),
    rather than a bare `hashlib.sha256(url)`: most of a governed-feed URL
    is predictable, so a bare hash of it would be an offline-crackable
    verifier for a low-entropy embedded password/token, even though the
    digest itself cannot be reversed. Keying with machine-local secret
    material closes that offline-verification path while preserving every
    other property a bare hash had (one-way, stable, and still sensitive
    to a credential rotation -- routing it to a fresh alternate slot, the
    correct, more conservative direction for a fail-closed contract)."""
    return hmac.new(_provenance_key(), url.encode("utf-8"), hashlib.sha256).hexdigest()


def _normalize_hostname(host: str) -> str:
    """Lowercases ``host`` and strips a single trailing root dot -- a
    fully-qualified hostname may legally end in `.` (e.g. `pypi.org.`),
    which DNS treats as identical to `pypi.org`, but a raw string/set
    comparison would not -- letting that spelling bypass both the public-
    PyPI denylist and the trust allowlist."""
    host = host.lower()
    return host[:-1] if host.endswith(".") else host


def _is_public_pypi_url(url: str) -> bool:
    """Whether ``url`` resolves to a public PyPI-family index (production
    or Test PyPI) -- a feed configuration that NAMES one of these hosts,
    even explicitly, is never a governed feed."""
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return False
    return _normalize_hostname(host or "") in _PUBLIC_PYPI_HOSTS


def _url_host(url: str) -> str | None:
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return None
    return _normalize_hostname(host) if host else None


def _trusted_index_hosts(env: dict) -> set[str]:
    raw = env.get(_TRUSTED_INDEX_HOSTS_ENV_VAR, "")
    return {_normalize_hostname(h.strip()) for h in raw.split(",") if h.strip()}


def _project_uv_toml_candidates() -> list[tuple[Path, bool]]:
    """Project-level uv config, in uv's own HIGHEST precedence tier --
    walks upward from the CURRENT WORKING DIRECTORY (never a hardcoded
    root) looking for a `uv.toml` (preferred over a sibling
    `pyproject.toml`) or a `pyproject.toml` carrying a `[tool.uv]` table,
    stopping at the FIRST directory where either is found. Returns
    ``(path, is_pyproject)`` pairs so the caller parses each according to
    its own shape."""
    cwd = Path.cwd()
    for directory in (cwd, *cwd.parents):
        uv_toml = directory / "uv.toml"
        if uv_toml.is_file():
            return [(uv_toml, False)]
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            return [(pyproject, True)]
    return []


def _effective_uv_toml_candidates(env: dict) -> list[Path]:
    """The uv.toml file(s) `uv` itself would actually read for index
    config in this environment, in `uv`'s own precedence order -- user-
    level first, then system-level -- matching this repo's own
    `install.ps1`/`install.sh` uv-config-discovery helpers
    (`Test-UvConfiguredIndex` / `_ensure_uv_index`). Project-level
    discovery (higher precedence than both) is separate -- see
    `_project_uv_toml_candidates` -- since it is cwd-based with its own
    `pyproject.toml` shape to parse.

    ``UV_CONFIG_FILE`` is EXCLUSIVE in `uv`'s own config resolution: when
    set, `uv` reads ONLY that file and skips project/user/system
    discovery entirely -- this must mirror that, never additionally
    consulting the other paths below while it is set."""
    config_file = env.get("UV_CONFIG_FILE")
    if config_file:
        return [Path(config_file)]
    if sys.platform == "win32":
        candidates = []
        appdata = env.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "uv" / "uv.toml")
        programdata = env.get("PROGRAMDATA")
        if programdata:
            candidates.append(Path(programdata) / "uv" / "uv.toml")
        return candidates
    candidates = []
    xdg = env.get("XDG_CONFIG_HOME")
    home = env.get("HOME")
    if xdg:
        candidates.append(Path(xdg) / "uv" / "uv.toml")
    elif home:
        candidates.append(Path(home) / ".config" / "uv" / "uv.toml")
    candidates.append(Path("/etc/uv/uv.toml"))
    candidates.append(Path("/etc/xdg/uv/uv.toml"))
    return candidates


def _effective_default_index_url(env: dict) -> tuple[str, str | None] | None:
    """The ``(url, name)`` of the index `uv` would actually use as its
    DEFAULT in this environment, or ``None`` if nothing replaces `uv`'s
    own implicit public-PyPI default. ``name`` is the `[[index]]` entry's
    own ``name`` field when the validated default came from a named entry
    with ``default = true``, else ``None`` -- an env-var source or the
    legacy bare ``index-url`` key never has a name. Callers that
    authenticate a NAMED index (`UV_INDEX_<NAME>_USERNAME`/``PASSWORD``)
    need this name preserved; a bare URL alone cannot recover it.
    `UV_INDEX` (plural) and a plain `[[index]]` table
    without `default = true` only add a SUPPLEMENTAL index -- `uv` still
    falls back to public PyPI for anything it doesn't resolve, so neither
    is the effective default. Only `UV_DEFAULT_INDEX`/`UV_INDEX_URL`, the
    legacy `index-url` key, or an `[[index]]` entry with `default = true`
    actually replace it.

    Checks PROJECT-level config (`_project_uv_toml_candidates`) before
    user-/system-level config, mirroring uv's own precedence -- a check
    that only ever consulted user/system config would both miss a
    project-only governed default and could select the wrong (lower-
    precedence) URL when a project-level one should win. Project
    discovery is skipped when `UV_CONFIG_FILE` is set (see `_effective_uv_toml_
    candidates`'s docstring). An EXISTING higher-precedence candidate
    that fails to parse is never skipped in favor of a lower-precedence
    one -- it fails closed (returns ``None``) instead, since `uv` itself
    would not silently fall through to a different file either."""
    for var in _GOVERNED_FEED_DEFAULT_INDEX_ENV_VARS:
        value = env.get(var)
        if value:
            return (value, None)
    candidates: list[tuple[Path, bool]] = []
    if not env.get("UV_CONFIG_FILE"):
        candidates.extend(_project_uv_toml_candidates())
    candidates.extend((path, False) for path in _effective_uv_toml_candidates(env))
    for candidate, is_pyproject in candidates:
        if not candidate.is_file():
            continue
        try:
            data = tomllib.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            # This candidate EXISTS at a real precedence tier `uv` itself
            # would read -- a parse failure here means `uv` would also
            # fail (or behave unpredictably) on it, never silently fall
            # through to a LOWER-precedence file instead. Continuing past
            # it could select a lower-precedence URL `uv` itself would
            # never actually use, violating this gate's fail-closed
            # contract. Fail closed: report no validated index at all.
            return None
        if not isinstance(data, dict):
            return None
        if is_pyproject:
            tool = data.get("tool")
            data = tool.get("uv") if isinstance(tool, dict) else None
            if not isinstance(data, dict):
                # Not malformed -- a `pyproject.toml` simply carrying no
                # `[tool.uv]` table at all is a valid file; it just has
                # no uv config in it, so lower-precedence tiers are still
                # consulted normally.
                continue
        index_url = data.get("index-url")
        if isinstance(index_url, str) and index_url:
            return (index_url, None)
        indexes = data.get("index")
        if isinstance(indexes, list):
            for entry in indexes:
                if not isinstance(entry, dict) or entry.get("default") is not True:
                    continue
                url = entry.get("url")
                if isinstance(url, str) and url:
                    name = entry.get("name")
                    return (url, name if isinstance(name, str) and name else None)
    return None


def _validated_trusted_index_url(env: dict) -> tuple[str, str | None] | None:
    """The effective default index's ``(url, name)``, but ONLY if the URL
    is HTTPS, both non-public AND affirmatively trusted (see
    `_governed_feed_configured`'s own docstring for the allowlist
    rationale) -- returns the concrete ``(url, name)`` pair (rather than
    just a bool) so a caller can pin `uv` to EXACTLY this one index at
    install time -- INCLUDING its name, when it has one, so a
    `UV_INDEX_<NAME>_USERNAME`/``PASSWORD``-authenticated named index can
    still authenticate -- instead of merely confirming "some index looks
    fine" and then trusting `uv`'s own ambient config to pick the real one
    used -- which could still consult an untrusted SUPPLEMENTAL index
    (`UV_INDEX`, or a plain `[[index]]` entry with no `default = true`)
    that this check never validated. The HTTPS requirement means an
    allowlisted hostname alone is never sufficient: a plaintext
    `http://<trusted-host>/...` URL is rejected even though its hostname
    passes the allowlist, since a network attacker could otherwise
    impersonate that host without ever needing to be trusted themselves."""
    trusted_hosts = _trusted_index_hosts(env)
    if not trusted_hosts:
        return None
    effective = _effective_default_index_url(env)
    if effective is None:
        return None
    url, name = effective
    if not url or _is_public_pypi_url(url):
        return None
    # Require HTTPS: an allowlisted HOSTNAME is not itself proof of
    # identity over a plaintext (or TLS-downgraded) connection -- a
    # network attacker able to intercept `http://<trusted-host>/...`, or
    # exploit an ambient TLS-disable setting, could impersonate the real
    # governed feed while this still reports it as validated/trusted.
    if urllib.parse.urlsplit(url).scheme != "https":
        return None
    host = _url_host(url)
    if host is None or host not in trusted_hosts:
        return None
    return (url, name)


def _governed_feed_configured(*, env: dict | None = None) -> bool:
    """Whether `uv`'s EFFECTIVE DEFAULT index in this environment is both
    (a) not a public PyPI-family host, AND (b) affirmatively trusted by
    this machine's own explicit policy (`_TRUSTED_INDEX_HOSTS_ENV_VAR`).

    This is an ALLOWLIST, deliberately -- a prior revision inferred
    governance from "not one of a few known public hostnames", which would
    silently accept an arbitrary untrusted index (e.g. a public mirror
    under a different hostname) as "governed" just because it isn't named
    `pypi.org`. Nothing is trusted unless this machine's own environment
    affirmatively lists it: with no trust policy configured at all, this
    always fails closed, even if SOME non-public-looking index happens to
    be configured as the default. Deliberately reads configuration, never
    a hardcoded URL or hostname -- this repository stays feed-neutral (see
    `tools/check-feed-neutrality.py`); only each machine's own local
    configuration ever names its real, trusted feed."""
    env = dict(os.environ) if env is None else env
    return _validated_trusted_index_url(env) is not None
