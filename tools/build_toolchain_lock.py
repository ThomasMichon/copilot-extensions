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
#: wheel in a run reproducible without a per-backend scheme.
_LOCKED_TOOLCHAIN_PACKAGES = ("setuptools", "wheel")


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
        capture_output=True, text=True,
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


def _is_public_pypi_url(url: str) -> bool:
    """Whether ``url`` resolves to a public PyPI-family index (production
    or Test PyPI) -- a feed configuration that NAMES one of these hosts,
    even explicitly, is never a governed feed."""
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return False
    return (host or "").lower() in _PUBLIC_PYPI_HOSTS


def _url_host(url: str) -> str | None:
    try:
        return urllib.parse.urlsplit(url).hostname
    except ValueError:
        return None


def _trusted_index_hosts(env: dict) -> set[str]:
    raw = env.get(_TRUSTED_INDEX_HOSTS_ENV_VAR, "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _effective_uv_toml_candidates(env: dict) -> list[Path]:
    """The uv.toml file(s) `uv` itself would actually read for index
    config in this environment.

    ``UV_CONFIG_FILE`` is EXCLUSIVE in `uv`'s own config resolution: when
    set, `uv` reads ONLY that file and skips its normal project/user-level
    discovery entirely (the same exclusivity this repo's own
    `plugins/agent-worktrees/scripts/install.sh` already handles) -- so
    this must mirror that explicitly, never additionally (or instead)
    consult the user-level `uv.toml` path while `UV_CONFIG_FILE` is set,
    which would check a file `uv` itself is NOT reading."""
    config_file = env.get("UV_CONFIG_FILE")
    if config_file:
        return [Path(config_file)]
    if sys.platform == "win32":
        appdata = env.get("APPDATA")
        return [Path(appdata) / "uv" / "uv.toml"] if appdata else []
    xdg = env.get("XDG_CONFIG_HOME")
    if xdg:
        return [Path(xdg) / "uv" / "uv.toml"]
    home = env.get("HOME")
    return [Path(home) / ".config" / "uv" / "uv.toml"] if home else []


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
    """The effective default index URL, but ONLY if it is both non-public
    AND affirmatively trusted (see `_governed_feed_configured`'s own
    docstring for the allowlist rationale) -- returns the concrete URL
    (rather than just a bool) so a caller can pin `uv` to EXACTLY this one
    index at install time, instead of merely confirming "some index looks
    fine" and then trusting `uv`'s own ambient config to pick the real one
    used -- which could still consult an untrusted SUPPLEMENTAL index
    (`UV_INDEX`, or a plain `[[index]]` entry with no `default = true`)
    that this check never validated."""
    trusted_hosts = _trusted_index_hosts(env)
    if not trusted_hosts:
        return None
    url = _effective_default_index_url(env)
    if not url or _is_public_pypi_url(url):
        return None
    host = _url_host(url)
    if host is None or host.lower() not in trusted_hosts:
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
    its exact installed ``setuptools``/``wheel`` versions.

    If ``venv_dir`` already holds a venv from an earlier call in this same
    process or a previous invocation of this script (its own interpreter
    already exists on disk), venv creation and package installation are
    skipped and only the already-installed versions are read back -- this
    is how a caller shares ONE toolchain lock across several plugins in one
    promotion run without this tool needing to build more than one plugin
    per process invocation: pass the same ``venv_dir`` (``--toolchain-venv``
    on the CLI) to every invocation in that run.

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
            "UV_DEFAULT_INDEX/UV_INDEX_URL or the user-level uv.toml's "
            "index-url / [[index]] default=true -- against the explicit "
            f"trust policy in {_TRUSTED_INDEX_HOSTS_ENV_VAR}) -- refusing "
            "to install the build toolchain, which would otherwise "
            "silently resolve setuptools/wheel from an unverified index"
        )
    venv_python = _venv_python_path(venv_dir)
    if not venv_python.is_file():
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_venv_dir = Path(
            tempfile.mkdtemp(dir=venv_dir.parent, prefix=f".{venv_dir.name}.staging-")
        )
        try:
            venv_cmd = ["uv", "venv", str(staging_venv_dir)]
            if python:
                venv_cmd += ["--python", python]
            result = subprocess.run(venv_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise ArtifactBuildError(
                    f"uv venv failed for toolchain venv {venv_dir}:\n"
                    f"{result.stdout}\n{result.stderr}"
                )
            staging_venv_python = _venv_python_path(staging_venv_dir)
            # Strip any ambient SUPPLEMENTAL-index variable -- the install
            # must consult ONLY the one validated URL, passed explicitly
            # below, never whatever `uv` would otherwise additionally
            # source from the environment or a project/user config file.
            install_env = dict(env)
            for var in (
                "UV_INDEX", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_CONFIG_FILE",
            ):
                install_env.pop(var, None)
            install = subprocess.run(
                [
                    "uv", "pip", "install", "--no-config",
                    "--index-url", validated_index_url,
                    "--python", str(staging_venv_python),
                    *_LOCKED_TOOLCHAIN_PACKAGES,
                ],
                capture_output=True, text=True, env=install_env,
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
            # Never pre-delete an existing venv_dir: a concurrent caller
            # sharing this same path may have already published its OWN
            # complete venv (and may already be building against it) --
            # clobbering it here would violate the "unchanged shared lock"
            # promise and could break an in-flight build. If the rename
            # itself fails, confirm a winner's venv is genuinely present
            # (venv_python now exists) before treating it as a benign lost
            # race -- a permission/filesystem/invalid-destination error
            # with no real winner must still surface as a build failure,
            # never a silently swallowed exception that leaves nothing at
            # venv_dir for the version query below to find.
            try:
                staging_venv_dir.rename(venv_dir)
            except OSError as exc:
                if not venv_python.is_file():
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
        capture_output=True, text=True,
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
    toolchain the source's own manifest says is insufficient)."""
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.version import InvalidVersion, Version

    for raw in _read_build_system_requires(source_dir):
        try:
            req = Requirement(raw)
        except InvalidRequirement as exc:
            raise ArtifactBuildError(
                f"{source_dir}: unparseable [build-system].requires entry "
                f"{raw!r}: {exc}"
            ) from exc
        if req.marker is not None and not req.marker.evaluate(
            environment=toolchain.marker_environment
        ):
            continue  # this constraint does not apply in this environment
        locked = toolchain.packages.get(req.name)
        if locked is None:
            continue  # a requirement on a package this toolchain doesn't lock
        try:
            locked_version = Version(locked)
        except InvalidVersion as exc:
            raise ArtifactBuildError(
                f"locked toolchain version {locked!r} for {req.name!r} is "
                f"not a valid version: {exc}"
            ) from exc
        if not req.specifier.contains(locked_version, prereleases=True):
            raise ArtifactBuildError(
                f"{source_dir}: locked {req.name} {locked} does not satisfy "
                f"its own declared build requirement {raw!r} -- refusing to "
                "build with --no-build-isolation against an insufficient "
                "pinned toolchain"
            )
