#!/usr/bin/env python3
"""Build-toolchain lock resolution for `tools/build_python_artifacts.py`.

Split out of that module to stay under `tools/check-module-size.py`'s
1,000-line cap for new/unbaselined files -- this is a component boundary,
not a reusability concern: every name here is still imported back into,
and re-exported by, `build_python_artifacts.py`, which remains the sole
public entry point and CLI.

Owns the Phase 2 Build hermeticity direction of the
`governed-python-artifact-promotion` effort
(`efforts/active/governed-python-artifact-promotion/README.md`): resolving
ONE pinned `setuptools`/`wheel` venv (`resolve_toolchain_lock`), reusable
unchanged across every wheel built in a run, verifying it only ever
installs from an affirmatively trusted, governed, non-public package feed
(`_governed_feed_configured`), and verifying a specific source's own
`[build-system].requires` floor is actually satisfied by the locked
versions before a `--no-build-isolation` build runs
(`_assert_toolchain_satisfies_build_requires`), since that build mode can
never substitute a different version to compensate.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

try:  # tomllib is stdlib on 3.11+; tomli backports it for this repo's
    # 3.10 support floor -- mirrors uv_editable_ref's own identical fallback.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

#: The exact packages a locked build-toolchain venv pins -- every
#: `pyproject.toml` under `plugins/` and `libs/` uses the same
#: `setuptools.build_meta` backend (confirmed by this effort's own Build
#: hermeticity survey), so pinning these two is sufficient to make every
#: wheel in a run reproducible without a per-backend scheme. ``packaging``
#: is also locked here -- not because any build backend needs it, but so
#: `_assert_toolchain_satisfies_build_requires`'s own `[build-system].
#: requires` verification can run INSIDE this governed-feed-sourced venv
#: (via its own interpreter) rather than depending on `packaging` being
#: importable in whatever process happens to be running this tool, which
#: would otherwise always fail on a genuinely clean machine with just
#: Python and `uv` installed.
_LOCKED_TOOLCHAIN_PACKAGES = ("setuptools", "wheel", "packaging")

#: Interpreter-selection environment variables that could let an ambient
#: process redirect a DIRECTLY-INVOKED interpreter (the locked toolchain's
#: own venv interpreter, or a caller-selected `--python`) onto modules or
#: a standard library other than its own -- `PYTHONPATH` can shadow a
#: locked package (e.g. the pinned `setuptools`) with an ambient copy
#: reachable on that path, and `PYTHONHOME` can redirect the interpreter's
#: own standard-library resolution entirely, even though the manifest
#: claims the locked venv was authoritative. Stripped from every
#: subprocess that invokes a specific interpreter path directly (the
#: toolchain version/marker-environment queries, and the final `uv build
#: --no-build-isolation` build itself in `build_python_artifacts.py`) --
#: this repo's own managed-runtime probes apply the same isolation (see
#: `plugins/agent-worktrees/scripts/install.sh:1025-1031`).
_PYTHON_RUNTIME_ENV_VARS = ("PYTHONPATH", "PYTHONHOME")


def sanitize_subprocess_env(env: dict | None = None) -> dict:
    """A copy of ``env`` (or the current process environment, if ``env``
    is ``None``) with every variable in `_PYTHON_RUNTIME_ENV_VARS`
    stripped -- the shared sanitization every subprocess that runs a
    SPECIFIC interpreter path directly must apply. Never used for a
    subprocess that merely invokes `uv` as a command (venv creation,
    package installation): those have their own, separate index-related
    sanitization in `resolve_toolchain_lock`, which also strips these same
    two variables as part of its broader stripped-variable set."""
    sanitized = dict(os.environ) if env is None else dict(env)
    for var in _PYTHON_RUNTIME_ENV_VARS:
        sanitized.pop(var, None)
    return sanitized


#: The file published atomically alongside a shared `--toolchain-venv`,
#: recording the governed index it was actually built from -- so a LATER
#: call sharing the same ``venv_dir`` can verify the existing venv
#: genuinely came from the currently-validated governed feed before
#: trusting its mere presence (`venv_python.is_file()`) as a completion
#: signal. Without this, a venv built before this machine's trust policy
#: existed, or from a since-revoked/different index, would be silently
#: reused and trusted forever just because an interpreter happens to
#: exist at the expected path.
_PROVENANCE_MARKER_NAME = ".governed-feed-provenance.json"


def _credential_free_index_identity(url: str) -> str:
    """``url`` with any embedded ``user:pass@`` userinfo stripped --
    `uv`/PEP 508 index URLs may legally carry credentials, but those
    credentials must never be persisted to disk (a provenance marker
    published into every shared venv) or interpolated into a diagnostic
    message, per this effort's own "never log credentials" validation
    rule. The raw, possibly-credentialed ``url`` is used ONLY for the
    actual authenticated `uv venv`/`uv pip install --index-url`
    invocations themselves in `resolve_toolchain_lock` -- never stored or
    displayed; every persisted/displayed use goes through this function
    first."""
    parts = urllib.parse.urlsplit(url)
    if not parts.username and not parts.password:
        return url
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc))


def _write_provenance_marker(venv_dir: Path, validated_index_url: str) -> None:
    """Records ``validated_index_url``'s credential-free identity into
    ``venv_dir``'s own provenance marker -- called on the staging
    directory BEFORE the atomic rename that publishes it, so the marker
    and the venv it describes always arrive together, never as two
    separate, racy writes. Never persists embedded credentials to disk."""
    marker = {
        "validated_index_url": _credential_free_index_identity(validated_index_url)
    }
    (venv_dir / _PROVENANCE_MARKER_NAME).write_text(
        json.dumps(marker), encoding="utf-8"
    )


def _provenance_matches(venv_dir: Path, validated_index_url: str) -> bool:
    """Whether ``venv_dir``'s own provenance marker records EXACTLY
    ``validated_index_url``'s credential-free identity -- a missing,
    unreadable, or mismatched marker (including a venv published before
    this check existed, which never wrote one at all) returns ``False``,
    never treated as "probably fine"."""
    marker_path = venv_dir / _PROVENANCE_MARKER_NAME
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(data, dict)
        and data.get("validated_index_url")
        == _credential_free_index_identity(validated_index_url)
    )


class ArtifactBuildError(Exception):
    """A plugin/lib wheel could not be built, or the result could not be
    understood (unparseable filename, unreadable WHEEL metadata) -- callers
    must fail closed rather than emit a manifest describing a guess."""


def _hash_fields(*fields: str) -> str:
    """Hashes an ordered sequence of string fields unambiguously: each
    field is length-prefixed before its UTF-8 bytes, so no delimiter choice
    can let two DIFFERENT field sequences serialize to the same digest
    (e.g. joining `"rel:hash"` pairs with a plain separator lets a crafted
    filename or digest absorb the separator and collide with a
    differently-split pair -- length-prefixing makes that impossible)."""
    h = hashlib.sha256()
    for field in fields:
        data = field.encode("utf-8")
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


def _venv_python_path(venv_dir: Path) -> Path:
    """The venv's own interpreter path -- Windows and POSIX venvs place it
    differently, and this must match whatever `uv venv` actually created so
    later steps query the right interpreter."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


#: A stdlib-only script (no third-party imports, since the locked
#: toolchain venv only ever has `setuptools`/`wheel` installed) that
#: reports the same keys `packaging.markers.default_environment()` would,
#: queried by running it THROUGH the locked interpreter itself -- so a
#: PEP 508 marker evaluates against the actually-locked Python, never
#: whatever process happens to be running this tool. ``implementation_version``
#: is deliberately computed from ``sys.implementation.version`` (mirroring
#: `packaging.markers`' own `format_full_version` helper), NOT
#: `platform.python_version()` -- they coincide on CPython but diverge on
#: alternative implementations (e.g. PyPy), where a marker on
#: `implementation_version` would otherwise silently take the wrong branch.
_MARKER_ENV_QUERY_SCRIPT = (
    "import json, os, platform, sys\n"
    "def _format_full_version(info):\n"
    "    version = f'{info.major}.{info.minor}.{info.micro}'\n"
    "    kind = info.releaselevel\n"
    "    if kind != 'final':\n"
    "        version += kind[0] + str(info.serial)\n"
    "    return version\n"
    "print(json.dumps({\n"
    "    'implementation_name': sys.implementation.name,\n"
    "    'implementation_version': _format_full_version(sys.implementation.version),\n"
    "    'os_name': os.name,\n"
    "    'platform_machine': platform.machine(),\n"
    "    'platform_release': platform.release(),\n"
    "    'platform_system': platform.system(),\n"
    "    'platform_version': platform.version(),\n"
    "    'python_full_version': platform.python_version(),\n"
    "    'platform_python_implementation': platform.python_implementation(),\n"
    "    'python_version': '.'.join(platform.python_version_tuple()[:2]),\n"
    "    'sys_platform': sys.platform,\n"
    "}))\n"
)


def _query_marker_environment(python_exe: Path) -> dict:
    """Queries ``python_exe`` itself for its own PEP 508 marker-environment
    values -- never assumed from the process running this script, which
    may be a different Python than ``--python`` locked into the toolchain
    venv."""
    result = subprocess.run(
        [str(python_exe), "-c", _MARKER_ENV_QUERY_SCRIPT],
        capture_output=True, text=True, env=sanitize_subprocess_env(),
    )
    if result.returncode != 0:
        raise ArtifactBuildError(
            f"could not query marker environment from {python_exe}:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ArtifactBuildError(
            f"{python_exe}: marker-environment query produced non-JSON "
            f"output: {exc}"
        ) from exc


class ToolchainLock:
    """A resolved, pinned build toolchain (exact ``setuptools``/``wheel``
    versions) installed into one dedicated venv, meant to be reused --
    unchanged -- across every wheel built in a single invocation (and,
    when a caller passes the same ``venv_dir`` to ``resolve_toolchain_lock``
    across several invocations, across a whole promotion run), so two
    wheels built moments apart never silently differ in which toolchain
    version actually produced them (the `governed-python-artifact-promotion`
    effort's own resolved Build hermeticity direction)."""

    def __init__(self, venv_python: Path, packages: dict[str, str]):
        self.venv_python = venv_python
        self.packages = packages
        self._marker_environment: dict | None = None

    @property
    def generator(self) -> str:
        """The exact ``Generator:`` string a wheel built with this locked
        toolchain must carry -- used to verify a ``--no-build-isolation``
        build actually used the pinned venv rather than silently falling
        back to some other setuptools reachable on `PATH`."""
        return f"setuptools ({self.packages['setuptools']})"

    @property
    def marker_environment(self) -> dict:
        """The PEP 508 environment-marker dict for THIS locked toolchain's
        own interpreter (``venv_python``) -- queried directly from that
        interpreter (never assumed from whatever process happens to be
        running this tool), so a constraint like `python_version < "3.0"`
        evaluates against the interpreter actually locked, even when
        ``--python`` selected a different one than this process's own.
        Queried once and cached -- the locked venv's interpreter never
        changes for the lifetime of this ``ToolchainLock``."""
        if self._marker_environment is None:
            self._marker_environment = _query_marker_environment(self.venv_python)
        return self._marker_environment

    @property
    def lock_id(self) -> str:
        """A stable identity for this exact pinned toolchain, folded into
        every artifact's own ``artifact_id`` (see ``build_plugin_artifacts``)
        so two promotion runs that happen to lock different setuptools/
        wheel versions are naturally different, auditable artifact
        identities rather than a silent same-identity mismatch."""
        pairs = [f"{name}=={version}" for name, version in sorted(self.packages.items())]
        return "sha256:" + _hash_fields(*pairs)


_GOVERNED_FEED_DEFAULT_INDEX_ENV_VARS = ("UV_DEFAULT_INDEX", "UV_INDEX_URL")
_PUBLIC_PYPI_HOSTS = {"pypi.org", "pypi.python.org", "test.pypi.org"}
#: Affirmative trust policy for `resolve_toolchain_lock`'s governed-feed
#: gate (see `_governed_feed_configured`'s own docstring): a comma-
#: separated allowlist of hostnames the MACHINE explicitly asserts are its
#: own governed feed. Never committed/hardcoded here -- this repo stays
#: feed-neutral (`tools/check-feed-neutrality.py`); only a machine's own
#: local environment ever populates this.
_TRUSTED_INDEX_HOSTS_ENV_VAR = "BUILD_PYTHON_ARTIFACTS_TRUSTED_INDEX_HOSTS"


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


def _effective_uv_toml_candidates(env: dict) -> list[Path]:
    """The uv.toml file(s) `uv` itself would actually read for index
    config in this environment, in `uv`'s own precedence order -- user-
    level first, then system-level -- matching this repo's own
    `install.ps1`/`install.sh` identical uv-config-discovery helpers
    (`Test-UvConfiguredIndex` / `_ensure_uv_index`), rather than a
    partial, user-level-only reimplementation.

    ``UV_CONFIG_FILE`` is EXCLUSIVE in `uv`'s own config resolution: when
    set, `uv` reads ONLY that file and skips its normal project/user/
    system discovery entirely -- so this must mirror that explicitly,
    never additionally (or instead) consulting any of the other paths
    below while `UV_CONFIG_FILE` is set, which would check a file `uv`
    itself is NOT reading."""
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


def _effective_default_index_url(env: dict) -> str | None:
    """The URL of the index `uv` would actually use as its DEFAULT in this
    environment, or ``None`` if nothing replaces `uv`'s own implicit
    public-PyPI default. `UV_INDEX` (plural) and a plain `[[index]]` table
    without `default = true` only add a SUPPLEMENTAL index -- `uv` still
    falls back to public PyPI for anything the supplemental index doesn't
    resolve, so neither is the effective default. Only `UV_DEFAULT_INDEX`/
    `UV_INDEX_URL`, the legacy `index-url` key, or an `[[index]]` entry
    with `default = true` actually replace it."""
    for var in _GOVERNED_FEED_DEFAULT_INDEX_ENV_VARS:
        value = env.get(var)
        if value:
            return value
    for candidate in _effective_uv_toml_candidates(env):
        if not candidate.is_file():
            continue
        try:
            data = tomllib.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        index_url = data.get("index-url")
        if isinstance(index_url, str) and index_url:
            return index_url
        indexes = data.get("index")
        if isinstance(indexes, list):
            for entry in indexes:
                if not isinstance(entry, dict) or entry.get("default") is not True:
                    continue
                url = entry.get("url")
                if isinstance(url, str) and url:
                    return url
    return None


def _validated_trusted_index_url(env: dict) -> str | None:
    """The effective default index URL, but ONLY if it is HTTPS, both
    non-public AND affirmatively trusted (see `_governed_feed_configured`'s
    own docstring for the allowlist rationale) -- returns the concrete URL
    (rather than just a bool) so a caller can pin `uv` to EXACTLY this one
    index at install time, instead of merely confirming "some index looks
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
    url = _effective_default_index_url(env)
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
    return url


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


def resolve_toolchain_lock(venv_dir: Path, *, python: str | None = None) -> ToolchainLock:
    """Resolves one pinned build-toolchain venv at ``venv_dir`` and returns
    its exact installed ``setuptools``/``wheel``/``packaging`` versions.

    If ``venv_dir`` already holds a venv from an earlier call in this same
    process or a previous invocation of this script, AND that venv's own
    provenance marker (`_provenance_matches`) confirms it was published
    from EXACTLY the index validated for THIS call, venv creation and
    package installation are skipped and only the already-installed
    versions are read back -- this is how a caller shares ONE toolchain
    lock across several plugins in one promotion run without this tool
    needing to build more than one plugin per process invocation: pass the
    same ``venv_dir`` (``--toolchain-venv`` on the CLI) to every invocation
    in that run. An existing venv whose provenance does not match (or
    carries none at all -- e.g. published before this check existed) is
    never trusted on interpreter-presence alone: it is quarantined aside
    and rebuilt fresh, exactly as if ``venv_dir`` had never existed.

    Never resolves from an untrusted index: this call itself first
    resolves the effective default index, verifying it is both non-public
    AND affirmatively trusted by this machine's own explicit policy
    (``_validated_trusted_index_url``), failing closed rather than letting
    `uv venv`/`uv pip install` silently resolve `setuptools`/`wheel` from
    an unverified index on a runner with no trust policy configured at
    all. The install itself is then pinned to EXACTLY that one validated
    URL (``--index-url`` plus ``--no-config``, with any ambient
    supplemental-index environment variable stripped) -- never merely
    "some index looked fine, trust `uv`'s own ambient config to pick the
    real one," which could still let an untrusted SUPPLEMENTAL index
    (`UV_INDEX`, or a plain `[[index]]` entry with no `default = true`)
    supply the actual packages.

    Built in a staging directory -- a genuinely unique one per call
    (`tempfile.mkdtemp`, not merely PID-qualified, since two threads in the
    same process share a PID) -- and published into ``venv_dir`` only via
    an atomic rename AFTER both `uv venv` and `uv pip install` succeed --
    never directly into ``venv_dir`` -- so an interrupted or partially
    failed setup never leaves a venv on disk that a later call's own
    `venv_python.is_file()` reuse check would mistake for a complete,
    already-installed one (which would otherwise skip straight to the
    version query and fail there forever, requiring a manual delete to
    recover)."""
    env = dict(os.environ)
    validated_index_url = _validated_trusted_index_url(env)
    if validated_index_url is None:
        raise ArtifactBuildError(
            "no affirmatively trusted governed package feed is configured "
            "on this machine (checked uv's effective default index -- "
            "UV_DEFAULT_INDEX/UV_INDEX_URL or the user-level or "
            "system-level uv.toml's (e.g. %PROGRAMDATA%, /etc/uv, "
            "/etc/xdg/uv) index-url / [[index]] default=true -- against "
            f"the explicit trust policy in {_TRUSTED_INDEX_HOSTS_ENV_VAR}) "
            "-- refusing to install the build toolchain, which would "
            "otherwise silently resolve setuptools/wheel/packaging from "
            "an unverified index"
        )
    venv_python = _venv_python_path(venv_dir)
    if venv_python.is_file() and not _provenance_matches(venv_dir, validated_index_url):
        # An interpreter existing at this shared path is not itself a
        # trust signal: it could be a venv built before this machine's
        # trust policy existed, built from a since-revoked/different
        # index, or simply never published by this function at all.
        # Never silently reuse it just because the version query below
        # would happily succeed against it -- quarantine it aside (never
        # delete outright, so a genuinely mismatched venv remains
        # available for postmortem) and fall through to a fresh build
        # from the currently-validated index, exactly as if venv_dir had
        # never existed.
        try:
            quarantine_dir = Path(
                tempfile.mkdtemp(
                    dir=venv_dir.parent, prefix=f".{venv_dir.name}.untrusted-"
                )
            )
            quarantine_dir.rmdir()  # mkdtemp pre-creates it; rename needs no destination
            venv_dir.rename(quarantine_dir)
        except OSError:
            # Lost a race with a concurrent rebuild/quarantine of this
            # same path -- re-check rather than assume: if the path now
            # genuinely matches, the race resolved itself benignly;
            # otherwise this is a real failure, not a benign lost race.
            if not (
                venv_python.is_file()
                and _provenance_matches(venv_dir, validated_index_url)
            ):
                raise ArtifactBuildError(
                    f"{venv_dir}: existing toolchain venv does not carry "
                    "provenance matching the currently validated governed "
                    "index "
                    f"({_credential_free_index_identity(validated_index_url)}), "
                    "and it could not be quarantined for a rebuild"
                )
    if not venv_python.is_file():
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_venv_dir = Path(
            tempfile.mkdtemp(dir=venv_dir.parent, prefix=f".{venv_dir.name}.staging-")
        )
        # Strip every ambient variable that could supply packages from
        # somewhere other than the one validated URL below -- `--no-config`
        # only disables config FILES; `uv` still honors these as
        # environment-based package sources (UV_EXTRA_INDEX_URL/
        # UV_FIND_LINKS are flat-file/extra-index sources independent of
        # the default-index machinery entirely, confirmed common on this
        # repo's own clean-room runners), as a way to redirect a specific
        # requirement to a direct URL regardless of index (`UV_CONSTRAINT`/
        # `UV_OVERRIDE` for the main install, `UV_BUILD_CONSTRAINT` for any
        # source-distribution build dependencies `uv pip install` itself
        # needs), or, for `UV_VENV_SEED`, as a way for `uv venv` itself to
        # pre-install setuptools/wheel/pip from whatever ambient source it
        # would otherwise use -- which the later bare `uv pip install`
        # could then leave untouched if it considers the unconstrained
        # requirement already satisfied, silently bypassing the validated
        # index entirely. Also strips `PYTHONPATH`/`PYTHONHOME` (the same
        # interpreter-redirection vectors stripped everywhere a specific
        # interpreter is invoked directly -- see `sanitize_subprocess_env`),
        # and `UV_INSECURE_HOST` -- `uv` honors that to disable TLS
        # certificate verification for a named host, which would let a
        # network attacker impersonate even an allowlisted governed-feed
        # hostname (the HTTPS-scheme requirement in
        # `_validated_trusted_index_url` is otherwise meaningless if an
        # ambient setting can disable the certificate check for that same
        # host). Used for BOTH the `uv venv` and `uv pip install` calls
        # below.
        sanitized_env = sanitize_subprocess_env(env)
        for var in (
            "UV_INDEX", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_CONFIG_FILE",
            "UV_EXTRA_INDEX_URL", "UV_FIND_LINKS", "UV_VENV_SEED",
            "UV_CONSTRAINT", "UV_BUILD_CONSTRAINT", "UV_OVERRIDE",
            "UV_INSECURE_HOST",
        ):
            sanitized_env.pop(var, None)
        try:
            venv_cmd = ["uv", "venv", "--no-config", str(staging_venv_dir)]
            if python:
                venv_cmd += ["--python", python]
            result = subprocess.run(
                venv_cmd, capture_output=True, text=True, env=sanitized_env
            )
            if result.returncode != 0:
                raise ArtifactBuildError(
                    f"uv venv failed for toolchain venv {venv_dir}:\n"
                    f"{result.stdout}\n{result.stderr}"
                )
            staging_venv_python = _venv_python_path(staging_venv_dir)
            install = subprocess.run(
                [
                    "uv", "pip", "install", "--no-config",
                    "--index-url", validated_index_url,
                    "--python", str(staging_venv_python),
                    *_LOCKED_TOOLCHAIN_PACKAGES,
                ],
                capture_output=True, text=True, env=sanitized_env,
            )
            if install.returncode != 0:
                raise ArtifactBuildError(
                    f"uv pip install failed for toolchain venv {venv_dir}:\n"
                    f"{install.stdout}\n{install.stderr}"
                )
            # Only now, with BOTH steps verified successful, publish the
            # venv into its real location via a single rename -- a retry
            # after any earlier failure never finds a partially built
            # venv_dir, since venv_dir never existed until this point.
            # The provenance marker is written into the staging directory
            # BEFORE the rename, so it is published atomically together
            # with the venv it describes -- a later call reusing this
            # exact path can verify it actually came from the currently-
            # validated governed index, never trusting mere interpreter
            # presence (see the provenance check above).
            # Never pre-delete an existing venv_dir: a concurrent caller
            # sharing this same path may have already published its OWN
            # complete venv (and may already be building against it) --
            # clobbering it here would violate the "unchanged shared lock"
            # promise and could break an in-flight build. If the rename
            # itself fails, confirm a winner's venv is genuinely present
            # (venv_python now exists) AND carries matching provenance
            # before treating it as a benign lost race -- two concurrent
            # callers validating DIFFERENT indexes can both observe an
            # absent destination; without the provenance check, whichever
            # loses the rename race would silently trust a venv sourced
            # from the OTHER caller's (different) validated index. A
            # permission/filesystem/invalid-destination error with no
            # real, matching winner must still surface as a build
            # failure, never a silently swallowed exception that leaves
            # nothing trustworthy at venv_dir for the version query below
            # to find.
            _write_provenance_marker(staging_venv_dir, validated_index_url)
            try:
                staging_venv_dir.rename(venv_dir)
            except OSError as exc:
                if not (
                    venv_python.is_file()
                    and _provenance_matches(venv_dir, validated_index_url)
                ):
                    raise ArtifactBuildError(
                        f"could not publish toolchain venv {staging_venv_dir} "
                        f"to {venv_dir}: {exc}"
                    ) from exc
        finally:
            if staging_venv_dir.exists():
                shutil.rmtree(staging_venv_dir, ignore_errors=True)

    query = subprocess.run(
        [
            str(venv_python), "-c",
            "import importlib.metadata as m, json, sys\n"
            "print(json.dumps({p: m.version(p) for p in sys.argv[1:]}))",
            *_LOCKED_TOOLCHAIN_PACKAGES,
        ],
        capture_output=True, text=True, env=sanitize_subprocess_env(),
    )
    if query.returncode != 0:
        raise ArtifactBuildError(
            f"could not read installed toolchain versions from {venv_python}:\n"
            f"{query.stdout}\n{query.stderr}"
        )
    try:
        packages = json.loads(query.stdout)
    except json.JSONDecodeError as exc:
        raise ArtifactBuildError(
            f"{venv_python}: toolchain version query produced non-JSON output: {exc}"
        ) from exc
    missing = [p for p in _LOCKED_TOOLCHAIN_PACKAGES if not packages.get(p)]
    if missing:
        raise ArtifactBuildError(
            f"{venv_dir}: locked toolchain venv is missing required package(s) "
            f"{missing} -- refusing to record an incomplete toolchain lock"
        )
    return ToolchainLock(venv_python, packages)


#: Runs entirely INSIDE the locked toolchain venv's own interpreter (see
#: `_assert_toolchain_satisfies_build_requires`), where `packaging` is
#: guaranteed importable because it is one of this toolchain's own locked
#: packages -- never imported in the calling process, which may have no
#: `packaging` at all on a genuinely clean machine. Reads a JSON payload
#: from stdin (``requires``, ``locked_packages``, ``marker_environment``)
#: and prints a single JSON result line: ``{"ok": true}`` or
#: ``{"ok": false, "error": "..."}``. Stops at the first unsatisfied/
#: unparseable/unlocked requirement, mirroring the prior in-process
#: implementation's own fail-fast behavior.
_BUILD_REQUIRES_CHECK_SCRIPT = (
    "import json, sys\n"
    "from packaging.requirements import InvalidRequirement, Requirement\n"
    "from packaging.utils import canonicalize_name\n"
    "from packaging.version import InvalidVersion, Version\n"
    "\n"
    "def _fail(error):\n"
    "    print(json.dumps({'ok': False, 'error': error}))\n"
    "    sys.exit(0)\n"
    "\n"
    "payload = json.loads(sys.stdin.read())\n"
    "locked = {\n"
    "    canonicalize_name(name): version\n"
    "    for name, version in payload['locked_packages'].items()\n"
    "}\n"
    "marker_environment = payload['marker_environment']\n"
    "for raw in payload['requires']:\n"
    "    try:\n"
    "        req = Requirement(raw)\n"
    "    except InvalidRequirement as exc:\n"
    "        _fail(f'unparseable [build-system].requires entry {raw!r}: {exc}')\n"
    "    if req.marker is not None and not req.marker.evaluate(\n"
    "        environment=marker_environment\n"
    "    ):\n"
    "        continue  # this constraint does not apply in this environment\n"
    "    canonical_name = canonicalize_name(req.name)\n"
    "    locked_version_str = locked.get(canonical_name)\n"
    "    if locked_version_str is None:\n"
    "        _fail(\n"
    "            f'declares an applicable build requirement {raw!r} for '\n"
    "            f'{req.name!r}, which this locked toolchain does not pin '\n"
    "            'at all -- --no-build-isolation means nothing installs it '\n"
    "            'automatically, so this cannot be verified as satisfied'\n"
    "        )\n"
    "    try:\n"
    "        locked_version = Version(locked_version_str)\n"
    "    except InvalidVersion as exc:\n"
    "        _fail(\n"
    "            f'locked toolchain version {locked_version_str!r} for '\n"
    "            f'{req.name!r} is not a valid version: {exc}'\n"
    "        )\n"
    "    if not req.specifier.contains(locked_version, prereleases=True):\n"
    "        _fail(\n"
    "            f'locked {req.name} {locked_version_str} does not satisfy '\n"
    "            f'its own declared build requirement {raw!r} -- refusing '\n"
    "            'to build with --no-build-isolation against an '\n"
    "            'insufficient pinned toolchain'\n"
    "        )\n"
    "print(json.dumps({'ok': True}))\n"
)


def _read_build_system_requires(source_dir: Path) -> list[str]:
    """``source_dir``'s own declared `[build-system].requires` list --
    the constraints `uv build --no-build-isolation` can never satisfy
    itself (isolation is precisely what would otherwise let it install a
    different version), so a locked toolchain that doesn't meet them must
    be caught before the build runs, not discovered as a build failure."""
    pyproject = source_dir / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ArtifactBuildError(f"{pyproject}: could not read/parse: {exc}") from exc
    build_system = data.get("build-system", {})
    if not isinstance(build_system, dict):
        raise ArtifactBuildError(f"{pyproject}: [build-system] is not a table")
    requires = build_system.get("requires", [])
    if not isinstance(requires, list) or not all(isinstance(r, str) for r in requires):
        raise ArtifactBuildError(
            f"{pyproject}: [build-system].requires is not a list of strings"
        )
    return requires


def _assert_toolchain_satisfies_build_requires(
    source_dir: Path, toolchain: ToolchainLock
) -> None:
    """Fails closed unless the locked toolchain's exact `setuptools`/
    `wheel` versions satisfy every applicable constraint ``source_dir``
    itself declares in `[build-system].requires` -- e.g. a lagging
    governed feed resolving `setuptools` 83.x against a source that
    declares `setuptools>=84.0.0`. `--no-build-isolation` means `uv build`
    can never substitute a different version to satisfy this itself, so
    this must be checked before the build runs, not discovered as a
    mysterious build failure (or, worse, a successful build against a
    toolchain the source's own manifest says is insufficient).

    Also fails closed on any OTHER applicable requirement naming a
    package this toolchain does not lock at all (anything outside
    `setuptools`/`wheel`/`packaging`) -- `--no-build-isolation` means
    nothing installs it automatically, so silently treating "not one of
    the packages we lock" as "therefore satisfied" would let a genuinely
    unmet build dependency through undetected. Package names are compared
    PEP 503-canonicalized (case-insensitive, `-`/`_`/`.` runs collapsed)
    since `Requirement.name` preserves the source's own literal spelling
    (e.g. `Setuptools` must still match the locked `setuptools` entry).

    Requires the third-party `packaging` library, but never imports it in
    THIS (calling) process -- this repo has no root `pyproject.toml`/
    `requirements.txt` declaring it as a tool-level dependency, and a
    genuinely clean machine with just Python and `uv` installed would not
    have it importable here. Instead, the actual parsing/comparison runs
    as a subprocess THROUGH ``toolchain.venv_python`` itself, where
    `packaging` is guaranteed present: it is one of the packages this
    toolchain locks (`_LOCKED_TOOLCHAIN_PACKAGES`), installed from the
    same validated governed feed as `setuptools`/`wheel`. This also means
    the comparison runs under the SAME interpreter whose
    `marker_environment` it evaluates markers against."""
    requires = _read_build_system_requires(source_dir)
    payload = json.dumps(
        {
            "requires": requires,
            "locked_packages": toolchain.packages,
            "marker_environment": toolchain.marker_environment,
        }
    )
    result = subprocess.run(
        [str(toolchain.venv_python), "-c", _BUILD_REQUIRES_CHECK_SCRIPT],
        input=payload, capture_output=True, text=True,
        env=sanitize_subprocess_env(),
    )
    if result.returncode != 0:
        raise ArtifactBuildError(
            f"{source_dir}: could not verify [build-system].requires "
            f"against the locked toolchain via {toolchain.venv_python}:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    try:
        outcome = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ArtifactBuildError(
            f"{source_dir}: build-requirement verification via "
            f"{toolchain.venv_python} produced non-JSON output: {exc}"
        ) from exc
    if not outcome.get("ok"):
        raise ArtifactBuildError(
            f"{source_dir}: "
            f"{outcome.get('error', 'unknown build-requirement verification failure')}"
        )
