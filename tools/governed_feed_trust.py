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
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

try:  # tomllib is stdlib on 3.11+; tomli backports it for this repo's
    # 3.10 support floor -- mirrors uv_editable_ref's own identical fallback.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib


class ArtifactBuildError(Exception):
    """A plugin/lib wheel could not be built, or the result could not be
    understood (unparseable filename, unreadable WHEEL metadata) -- callers
    must fail closed rather than emit a manifest describing a guess.

    Defined HERE (the lowest layer) rather than in `build_toolchain_lock.py`
    (which originally defined it) because `_restrict_file_to_owner` below
    -- shared by that module's own index-config file AND this module's own
    provenance key file -- needs to raise it; `build_toolchain_lock.py`
    re-imports/re-exports it exactly like every other name moved here."""


def _restrict_file_to_owner(path: Path) -> None:
    """Best-effort hardens ``path``'s ACCESS CONTROL to the owning user
    only, beyond the POSIX mode bits already applied at creation. `0o600`
    does NOT establish an owner-only ACL on Windows (and may not be
    authoritative on any ACL-backed filesystem) -- a credential-bearing
    file created under a caller-selected directory can still inherit a
    broader ACL from that directory, leaving it readable by other local
    principals despite the mode bits (the same reasoning this repo
    already applies to the Windows named-pipe transport in
    `plugins/agent-vault/src/agent_vault/cutover.py`'s own
    `OWNER_GATED_TRANSPORTS`: a Windows default DACL is never treated as
    sufficient for a secret). Shared by `build_toolchain_lock.py`'s own
    sanitized index-config file AND this module's own provenance key file
    (`_provenance_key`) -- both are credential-bearing.

    On Windows: strips inherited permissions and grants Full Control to
    only the current user plus `SYSTEM` (required for normal OS
    housekeeping, e.g. antivirus scanning) via `icacls` -- a standard
    Windows tool, no new dependency. On POSIX: a no-op: the `0o600` mode
    bits already applied at creation are authoritative there.

    Raises `ArtifactBuildError` on any failure (the current user cannot be
    determined, or `icacls` itself fails) -- a credential-bearing file
    whose ACL could not be VERIFIED restrictive (via this command's own
    exit code) must never be silently trusted as protected."""
    if sys.platform != "win32":
        return
    owner = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip("\\")
    if not owner or not os.environ.get("USERNAME"):
        raise ArtifactBuildError(
            f"{path}: could not determine the current user to restrict "
            "this credential-bearing file's ACL to -- refusing to "
            "proceed with an unverified, possibly-inherited ACL"
        )
    result = subprocess.run(
        [
            "icacls", str(path),
            "/inheritance:r",
            "/grant:r", f"{owner}:F", "SYSTEM:F",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ArtifactBuildError(
            f"{path}: could not restrict this credential-bearing file's "
            f"ACL to the current user:\n{result.stdout}\n{result.stderr}"
        )
    # `/inheritance:r` alone does NOT remove already-inherited ACEs on
    # this repo's own test machines -- empirically, it converts them to
    # EXPLICIT entries instead (Authenticated Users/BUILTIN\Users/
    # BUILTIN\Administrators all survived `/inheritance:r /grant:r
    # <owner>:F SYSTEM:F` in isolation), since `/grant:r` only replaces
    # the GRANT for the principals it names, leaving every other
    # principal's own (now-explicit) ACE untouched. Explicitly strip
    # every broad, well-known principal by SID (locale-independent,
    # unlike group display names) so only the owner and SYSTEM remain.
    remove_result = subprocess.run(
        [
            "icacls", str(path), "/remove:g",
            *_BROAD_WINDOWS_PRINCIPAL_SIDS,
        ],
        capture_output=True, text=True,
    )
    if remove_result.returncode != 0:
        raise ArtifactBuildError(
            f"{path}: could not strip broad principals from this "
            f"credential-bearing file's ACL:\n"
            f"{remove_result.stdout}\n{remove_result.stderr}"
        )
    # VERIFY the final result rather than trusting exit codes alone: a
    # credential-bearing file whose ACL cannot be confirmed restrictive
    # must never be silently trusted as protected. Parses the exact
    # PRINCIPAL name from each ACE line (everything before `:(`) and
    # compares it EXACTLY (normalized lowercase) against the owner and
    # the literal "NT AUTHORITY\SYSTEM" -- a prior SUBSTRING check here
    # wrongly accepted e.g. `DOMAIN\svc-backup` when the owner was
    # `DOMAIN\svc`, or any principal merely containing the word "system",
    # since the preceding `/remove:g` step intentionally leaves any
    # OTHER, non-broad explicit ACE in place (one could legitimately
    # exist from a prior, more targeted grant) rather than wiping the ACL
    # down to nothing first.
    query = subprocess.run(
        ["icacls", str(path)], capture_output=True, text=True
    )
    if query.returncode != 0:
        raise ArtifactBuildError(
            f"{path}: could not verify this credential-bearing file's "
            f"final ACL:\n{query.stdout}\n{query.stderr}"
        )
    path_str = str(path)
    principals: list[str] = []
    for raw_line in query.stdout.splitlines():
        line = raw_line.strip()
        if ":(" not in line:
            continue
        if line.startswith(path_str):
            line = line[len(path_str):].strip()
        principal = line.split(":(", 1)[0].strip()
        if principal:
            principals.append(principal)
    expected = {owner.lower(), "nt authority\\system"}
    unexpected = [p for p in principals if p.lower() not in expected]
    if unexpected:
        raise ArtifactBuildError(
            f"{path}: this credential-bearing file's ACL still grants "
            f"unexpected principal(s) after hardening: {unexpected!r} -- "
            "refusing to trust it as owner-only"
        )


#: Well-known, locale-independent Windows SIDs for broad built-in
#: principals that `icacls /grant:r` does not implicitly strip when
#: granting a DIFFERENT principal -- see `_restrict_file_to_owner`.
_BROAD_WINDOWS_PRINCIPAL_SIDS = (
    "*S-1-1-0",       # Everyone
    "*S-1-5-11",      # NT AUTHORITY\Authenticated Users
    "*S-1-5-32-545",  # BUILTIN\Users
    "*S-1-5-32-544",  # BUILTIN\Administrators
)

_GOVERNED_FEED_DEFAULT_INDEX_ENV_VARS = ("UV_DEFAULT_INDEX", "UV_INDEX_URL")
_PUBLIC_PYPI_HOSTS = {"pypi.org", "pypi.python.org", "test.pypi.org"}
#: Affirmative trust policy for `resolve_toolchain_lock`'s governed-feed
#: gate (see `_governed_feed_configured`'s own docstring): a comma-
#: separated allowlist of hostnames the MACHINE explicitly asserts are its
#: own governed feed. Never committed/hardcoded here -- this repo stays
#: feed-neutral (`tools/check-feed-neutrality.py`); only a machine's own
#: local environment ever populates this.
_TRUSTED_INDEX_HOSTS_ENV_VAR = "BUILD_PYTHON_ARTIFACTS_TRUSTED_INDEX_HOSTS"
#: Every ambient uv variable that could supply packages from somewhere
#: other than one explicitly validated index -- shared by
#: `build_toolchain_lock.py`'s own install-call sanitization and
#: `build_python_artifacts.py`'s final `--no-build-isolation` build
#: subprocess (see `strip_package_source_env_vars`), so the two can never
#: silently drift apart.
_UV_PACKAGE_SOURCE_ENV_VARS = (
    "UV_INDEX", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_CONFIG_FILE",
    "UV_EXTRA_INDEX_URL", "UV_FIND_LINKS", "UV_VENV_SEED",
    "UV_CONSTRAINT", "UV_BUILD_CONSTRAINT", "UV_OVERRIDE",
    "UV_INSECURE_HOST", "UV_NO_CONFIG",
)
#: uv's own named-index credential env-var convention
#: (`UV_INDEX_<NAME>_USERNAME`/`PASSWORD`), regardless of `<NAME>`.
_UV_INDEX_CREDENTIAL_ENV_RE = re.compile(r"^UV_INDEX_[A-Za-z0-9_]+_(USERNAME|PASSWORD)$")


def strip_package_source_env_vars(env: dict, *, strip_credentials: bool = False) -> None:
    """In-place strips every `_UV_PACKAGE_SOURCE_ENV_VARS` entry from
    ``env``. `UV_INDEX_<NAME>_USERNAME`/`PASSWORD`-shaped named-index
    credential variables are left untouched UNLESS ``strip_credentials``
    is true: `resolve_toolchain_lock`'s own install call needs them (to
    authenticate a named governed index); `build_python_artifacts.py`'s
    final `--no-build-isolation` build subprocess needs no index access
    at all, so it strips credentials too -- the build BACKEND executes
    arbitrary code from the source tree and has no legitimate need to
    observe them."""
    for var in _UV_PACKAGE_SOURCE_ENV_VARS:
        env.pop(var, None)
    if strip_credentials:
        for key in [k for k in env if _UV_INDEX_CREDENTIAL_ENV_RE.match(k)]:
            env.pop(key, None)


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


#: Bounded wait for `_provenance_key`'s own first-run creation lock --
#: module-level so a test can shrink it for a fast, deterministic timeout
#: check rather than actually waiting out the real default.
_PROVENANCE_KEY_LOCK_TIMEOUT_SECONDS = 10.0


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
    contract depends on. A sibling LOCKFILE (``<path>.lock``, created via
    `O_CREAT | O_EXCL`) serializes first-run creation across concurrent
    callers -- a reread-after-publish alone (this function's own earlier
    revision) still let two racing callers EACH publish a genuinely
    different key, with whichever `os.replace` landed last silently
    becoming the real one while the other had already returned (and
    might already be persisting provenance keyed on) a value no longer
    on disk. Holding the lock, a caller re-checks for an existing key
    (another caller may have published while this one waited) before
    generating its own -- so AT MOST ONE caller per machine ever
    actually creates the key; every other caller, racing or not, reads
    back that exact same one. Bounded by a generous timeout so a
    crashed lock-holder cannot wedge every future caller forever (still
    better than failing outright against this effort's own fail-closed
    contract preferring loud failure over broken builds)."""
    path = _provenance_key_dir() / "provenance-key"
    try:
        existing = path.read_bytes()
    except OSError:
        existing = b""
    if len(existing) == 32:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    deadline = time.monotonic() + _PROVENANCE_KEY_LOCK_TIMEOUT_SECONDS
    lock_fd = None
    while lock_fd is None:
        try:
            lock_fd = os.open(
                str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError:
            if time.monotonic() > deadline:
                raise ArtifactBuildError(
                    f"{lock_path}: timed out waiting for another process "
                    "to finish publishing the provenance key -- refusing "
                    "to proceed without a verified, agreed-upon key"
                )
            time.sleep(0.05)
    try:
        os.close(lock_fd)
        # Re-check NOW, under the lock: another caller may have already
        # published while this one was waiting to acquire it.
        try:
            existing = path.read_bytes()
        except OSError:
            existing = b""
        if len(existing) == 32:
            return existing
        key = secrets.token_bytes(32)
        # Published via an atomic rename from a uniquely-named temp
        # file, never a direct `O_CREAT | O_EXCL` open on the final path
        # -- that would make the file visible (0 bytes) to a concurrent
        # reader BEFORE the 32 key bytes are actually written, and a
        # crash in that exact window would leave a permanently-malformed
        # file every later call keeps reading back via the exists-and-
        # right-length check above. `_restrict_file_to_owner` (0o600
        # mode bits alone are not an owner-only ACL on Windows -- this
        # key is just as credential-bearing as the index-config temp
        # file it protects) is applied to the temp file BEFORE the
        # rename, so the final path never exists at a broader ACL even
        # momentarily. Holding the lock means this is now the ONLY
        # writer -- no reread-the-final-value step is needed afterward.
        tmp_path = path.with_name(
            f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}"
        )
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as key_file:
                key_file.write(key)
            _restrict_file_to_owner(tmp_path)
            os.replace(str(tmp_path), str(path))
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return key
    finally:
        lock_path.unlink(missing_ok=True)


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
