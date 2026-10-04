"""Script-backed backlog-provider adapter (the ``script`` forge provider).

Implements ``repository_issue_loops.ForgeProvider`` by invoking a declared
script via subprocess for each of the four backlog operations
(``list_open_issues``/``reserve``/``claim``/``release``) -- the first
concrete realization of the vision's *extend-any-declaration* script-path-
hook model (``visions/plugins/agent-dispatch/README.md``):
``repository_issue_loop``'s own scheduling/lease/quiet-period/dedup
machinery (``repository_issue_loops.py``) is reused completely unchanged;
the script supplies only the domain-specific backlog source, never the loop
shape. Split into its own module purely to stay under this repo's
module-size cap, mirroring ``gitea_provider_stub.py``'s own precedent.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import posixpath
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from .issue_loop_markers import validate_marker_payload
from .registrar import RegistrarError

if TYPE_CHECKING:
    from .repository_issue_loops import Issue

#: ``forge`` keys meaningful only to ``forge.provider: script``.
SCRIPT_FORGE_KEYS = frozenset({"command", "cwd", "timeout_seconds", "namespace", "backlog"})

#: Matches ``ScriptEvaluator``'s own default (``producers/evaluator.py``) --
#: the established local precedent for a trusted script invocation's bound.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Matches ``ScriptEvaluator``'s own ``MAX_SCRIPT_EVALUATOR_TIMEOUT``: an
#: excessively large but technically-finite value (e.g. ``1e308``) still
#: reaches ``Popen.communicate()`` and raises ``OverflowError`` before
#: waiting at all -- a practical upper bound is required, not just finiteness.
MAX_TIMEOUT_SECONDS = 1800.0


class ScriptProviderError(RegistrarError):
    """A script-backed backlog provider failed, timed out, or returned a
    malformed response."""


def _portable_script_prefix(script_path: str) -> list[str]:
    """Prefix a resolved script path with the interpreter/shell its suffix
    requires so it actually executes on Windows (where `CreateProcess`
    cannot run a `.py`/`.sh`/`.ps1` file directly, unlike POSIX's shebang
    support) -- mirrors ``companion.py``'s own established script-path
    resolver (`_resolve_companion_argv`) for the same three suffixes."""
    path = Path(script_path)
    suffix = path.suffix.casefold()
    if suffix == ".py":
        return [sys.executable, str(path)]
    if suffix == ".sh":
        shell = shutil.which("bash")
        if not shell:
            raise RegistrarError(
                "repository-issue-loop forge.command: a '.sh' script requires bash"
            )
        return [shell, str(path)]
    if suffix == ".ps1":
        shell = shutil.which("pwsh")
        if shell is None and os.name == "nt":
            fallback = (
                Path(os.environ.get("SystemRoot", r"C:\Windows"))
                / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            )
            shell = str(fallback) if fallback.is_file() else None
        if not shell:
            raise RegistrarError(
                "repository-issue-loop forge.command: a '.ps1' script requires PowerShell"
            )
        return [shell, "-NoProfile", "-NonInteractive", "-File", str(path)]
    return [str(path)]


def _normalize_declared_path(path: str) -> str:
    """Collapse spelling variants of the same relative/absolute path
    (``./scripts/poller.py`` vs ``scripts/poller.py``) to one canonical
    form, so the same backlog declared with cosmetically different path
    spelling still dedups -- forward-slash-normalized first so a
    declaration authored with Windows-style separators canonicalizes the
    same way everywhere."""
    return posixpath.normpath(path.replace("\\", "/"))


def _safe_resolve_path(path: Path, *, field: str) -> Path:
    """``Path.resolve()`` wrapped so a resolution failure (e.g.
    ``RuntimeError`` for a symlink loop, or an ``OSError`` from the
    underlying filesystem call) surfaces as the promised configuration
    ``RegistrarError`` instead of an uncaught runtime exception -- not
    every caller catches those broader exception types."""
    try:
        return path.resolve()
    except (OSError, RuntimeError) as exc:
        raise RegistrarError(
            f"repository-issue-loop forge.{field}: failed to resolve path "
            f"{str(path)!r}: {exc}"
        ) from exc


def _declaring_repo_identity(repo_root: str | Path | None) -> str:
    """A stable, cross-host identity for the declaring repository, used by
    ``script_resource_namespace`` so two *unrelated* repositories that
    happen to declare the same relative script path/producer_login/repo
    label do not collide on the coordinator's resource key. Delegates to
    the established, scrubbed-environment git-remote probe
    (``registrar_lane_aliases._derive_git_remote_alias`` -- clears ambient
    `GIT_DIR`/`GIT_CONFIG_KEY_*`/etc. that could otherwise redirect the
    probe to an unrelated repository despite an explicit ``-C``, and
    refuses to trust an *ancestor* repo's remote when ``repo_root`` isn't
    itself a git toplevel) for the canonicalized `origin` remote --
    device- and protocol-independent, stable across machines/checkouts.
    Falls back to the repo root's own absolute path when no git remote is
    resolvable (same-host collision avoidance only -- a non-git-remote
    checkout has no stronger cross-host identity available here). Never
    raises -- a resolution failure here just degrades the fallback
    identity to the unresolved path, matching this helper's own
    best-effort contract (unlike the hard validation failures the rest of
    `validate_script_forge_config` raises for the same class of error)."""
    if repo_root is None:
        return ""
    expanded = Path(repo_root).expanduser()
    try:
        root = expanded.resolve()
    except (OSError, RuntimeError):
        return str(expanded)
    cached = _REPO_IDENTITY_CACHE.get(root)
    if cached is not None:
        return cached
    from .registrar_lane_aliases import _derive_git_remote_alias

    remote = _derive_git_remote_alias(root)
    identity = remote or str(root)
    _REPO_IDENTITY_CACHE[root] = identity
    return identity


#: Memoizes `_declaring_repo_identity`'s git-remote probe by resolved repo
#: root. `_derive_git_remote_alias` is documented as a registration-time-only
#: probe (two subprocess calls, each up to a 5-second timeout) -- without
#: this cache, every scheduled `repository_issue_loop` tick re-validates its
#: `script` forge config (`run_tick` -> `validate_config` ->
#: `validate_script_forge_config`), re-running that probe on what is
#: otherwise a hot path. A repo's own remote essentially never changes for
#: the life of one daemon process, so caching by root for the process
#: lifetime is a safe, simple fix; a process restart naturally refreshes it.
_REPO_IDENTITY_CACHE: dict[Path, str] = {}


def validate_script_forge_config(
    forge: Mapping[str, Any],
    *,
    provider: Any,
    repo_root: str | Path | None = None,
    inherited_script_fields: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    """Validate the ``script``-only ``forge.command``/``forge.cwd``/
    ``forge.timeout_seconds`` fields, mirroring
    ``ado_discovery_scope.validate_discovery_scope``'s own provider-gating
    pattern: present but the wrong provider is a hard error (never a silent
    no-op); required but absent for ``provider == "script"`` is equally a
    hard error. Returns ``None`` when none of these fields apply (any
    non-``script`` provider with none of them set).

    ``command`` (its first element, the script path) and ``cwd``, when
    relative, resolve against ``repo_root`` -- the declaring repo,
    threaded the same way ``repository_issue_loop``'s own
    ``worker_identity`` already is (``validate_config``'s own ``cwd``
    parameter) -- never the daemon process's own incidental working
    directory, since the subprocess call runs synchronously inside each
    scheduled occurrence. The resolved script path is then prefixed with
    whatever interpreter/shell its suffix requires
    (``_portable_script_prefix``), so a `.py`/`.sh`/`.ps1` script declared
    without its own executable bit still runs on a platform (Windows) that
    cannot exec it directly.

    ``inherited_script_fields`` names which of ``command``/``cwd`` this
    declaration inherited from an ``extends:`` base rather than declaring
    itself. A *relative* inherited value cannot be resolved correctly here:
    ``repo_root`` is always the outer leaf declaration's own repo (see
    ``expand_repository_issue_loop``), never the directory of whichever hop
    in the chain actually supplied the field, so a cross-repo base's
    relative script path would silently execute (or fail to execute) at
    the wrong location -- generalizing per-hop origin tracking to cover
    this is a known, tracked limitation (`kind: emitter`'s own
    ``spec.cwd`` already has it; this field does not). Rather than ship
    that silent misresolution, refuse it outright: require an absolute
    path for either field whenever it was inherited.
    """
    present = {key: forge[key] for key in SCRIPT_FORGE_KEYS if key in forge}
    if provider != "script":
        if present:
            raise RegistrarError(
                f"repository-issue-loop forge.{'/'.join(sorted(present))}: "
                "only supported for forge.provider 'script'"
            )
        return None
    command = forge.get("command")
    if (
        not isinstance(command, (list, tuple))
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        raise RegistrarError(
            "repository-issue-loop forge.command: required for "
            "forge.provider 'script' -- expected a non-empty list of "
            "non-empty strings"
        )
    if "command" in inherited_script_fields and not Path(command[0]).is_absolute():
        raise RegistrarError(
            "repository-issue-loop forge.command: inherited via `extends:` "
            "from a different declaration -- a relative script path cannot "
            "be resolved against this declaration's own repo root, since "
            "it may differ from the base's (unsupported cross-repo "
            "inheritance shape). Use an absolute path, or declare "
            "`forge.command` directly in this file instead of inheriting it."
        )
    base = (
        _safe_resolve_path(Path(repo_root).expanduser(), field="repo_root")
        if repo_root is not None
        else None
    )
    resolved_command = [str(part) for part in command]
    # Normalize Windows-style separators before constructing `Path`: POSIX
    # treats a literal backslash as an ordinary filename character, not a
    # separator, so `scripts\poller.py` authored on Windows would
    # otherwise resolve as one single (nonexistent) literal filename on a
    # POSIX host -- while `_normalize_declared_path` (used for the
    # resource-key namespace) already treats it as equivalent to
    # `scripts/poller.py`, letting resolution and namespace identity
    # disagree about what path is actually being executed.
    resolved_command[0] = resolved_command[0].replace("\\", "/")
    script_path = Path(resolved_command[0])
    if not script_path.is_absolute():
        if base is None:
            raise RegistrarError(
                "repository-issue-loop forge.command: a relative script path "
                "requires a known declaring repo root (unreachable from a "
                "direct declaration read with no resolvable registrar "
                "context) -- use an absolute path instead of depending on "
                "the daemon process's own incidental working directory"
            )
        resolved_command[0] = str(_safe_resolve_path(base / script_path, field="command"))
    script_path_resolved = resolved_command[0]
    resolved_command = _portable_script_prefix(resolved_command[0]) + resolved_command[1:]

    cwd = forge.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not cwd):
        raise RegistrarError(
            "repository-issue-loop forge.cwd: expected a non-empty string"
        )
    if cwd is not None:
        # Same normalization as `command[0]` above -- POSIX treats a
        # literal backslash as an ordinary filename character, so an
        # unnormalized Windows-style `cwd` would otherwise resolve
        # differently than its own namespace-identity spelling.
        cwd = cwd.replace("\\", "/")
    if (
        "cwd" in inherited_script_fields
        and cwd is not None
        and not Path(cwd).is_absolute()
    ):
        raise RegistrarError(
            "repository-issue-loop forge.cwd: inherited via `extends:` "
            "from a different declaration -- a relative cwd cannot be "
            "resolved against this declaration's own repo root, since it "
            "may differ from the base's (unsupported cross-repo "
            "inheritance shape). Use an absolute path, or declare "
            "`forge.cwd` directly in this file instead of inheriting it."
        )
    if base is None:
        if cwd is None or not Path(cwd).is_absolute():
            raise RegistrarError(
                "repository-issue-loop forge.cwd: requires an absolute path "
                "when no declaring repo root is known -- an unset or "
                "relative value would otherwise depend on the daemon "
                "process's own incidental working directory"
            )
        resolved_cwd = str(_safe_resolve_path(Path(cwd), field="cwd"))
    else:
        resolved_cwd = cwd
        if cwd is not None:
            cwd_path = Path(cwd)
            if not cwd_path.is_absolute():
                resolved_cwd = str(_safe_resolve_path(base / cwd_path, field="cwd"))
            else:
                resolved_cwd = str(_safe_resolve_path(cwd_path, field="cwd"))
        else:
            # Unset `forge.cwd` still anchors to the declaring repo root,
            # never the daemon process's own incidental cwd -- the same
            # rule as `command`'s own default resolution.
            resolved_cwd = str(base)

    timeout = forge.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise RegistrarError(
            "repository-issue-loop forge.timeout_seconds: expected a number"
        )
    timeout_error = RegistrarError(
        "repository-issue-loop forge.timeout_seconds: expected a finite "
        f"number > 0 and <= {MAX_TIMEOUT_SECONDS:g}"
    )
    try:
        timeout = float(timeout)
    except OverflowError:
        # An arbitrarily large JSON/YAML integer (e.g. far beyond any
        # float's range) raises OverflowError on conversion rather than
        # producing a non-finite float -- treat it the same as any other
        # out-of-range timeout instead of letting the conversion itself
        # crash validation.
        raise timeout_error from None
    if timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS or not math.isfinite(timeout):
        raise timeout_error
    namespace = forge.get("namespace")
    if namespace is not None and (not isinstance(namespace, str) or not namespace):
        raise RegistrarError(
            "repository-issue-loop forge.namespace: expected a non-empty string"
        )
    backlog = forge.get("backlog")
    if backlog is not None and (not isinstance(backlog, str) or not backlog):
        raise RegistrarError(
            "repository-issue-loop forge.backlog: expected a non-empty string"
        )
    declared_command = [str(part) for part in command]
    declared_command[0] = _normalize_declared_path(declared_command[0])
    declared_cwd = _normalize_declared_path(cwd) if cwd else cwd
    return {
        "command": resolved_command,
        "cwd": resolved_cwd,
        "timeout_seconds": timeout,
        "namespace": namespace,
        "backlog": backlog,
        # Internal-only markers for `script_resource_namespace` (never a
        # recognized schema key, ignored by anything else that reads
        # `forge`): `_script_path` is the plain resolved script path,
        # pre-interpreter-wrap -- `command[0]` after wrapping is the
        # interpreter/shell (e.g. `sys.executable`), not a usable
        # namespace. `_declared_command`/`_declared_cwd` are the
        # as-authored (pre-resolution), *path-normalized* values -- the
        # namespace must be a stable declaration-level identity
        # independent of both the machine-local absolute paths
        # `command`/`cwd` resolve to (a different checkout root or Python
        # installation on another host must not change it) and of
        # cosmetic spelling (`./poller.py` vs `poller.py` is the same
        # backlog). `_declared_repo_identity` additionally distinguishes
        # two *unrelated* repositories that happen to declare the same
        # relative path/producer_login/repo label -- but it is only
        # best-effort (a git-remote probe cached per resolved repo root
        # for the process lifetime, not re-derived on later ticks -- see
        # `_REPO_IDENTITY_CACHE`), so an explicit `forge.namespace` always
        # takes precedence when set.
        "_script_path": script_path_resolved,
        "_declared_command": declared_command,
        "_declared_cwd": declared_cwd,
        "_declared_repo_identity": _declaring_repo_identity(repo_root),
    }


def _issue_to_dict(issue: "Issue") -> dict[str, Any]:
    return {
        "number": issue.number,
        "title": issue.title,
        "url": issue.url,
        "labels": list(issue.labels),
        "created_at": issue.created_at,
        "updated_at": issue.updated_at,
        "reservations": list(issue.reservations),
    }


def _validate_issue_payload(raw: Any) -> "Issue":
    """Strictly type-check a script response's issue object before
    constructing ``Issue`` -- `int(1.5)`/`str(None)` would otherwise
    silently coerce a wrong-typed field instead of raising, a string
    `labels` would iterate per-character, and a non-mapping `reservations`
    entry would crash downstream (`_latest_reservations`'s own `.get()`
    calls) instead of failing here with a clear error."""
    from .repository_issue_loops import Issue

    def _fail(reason: str) -> NoReturn:
        raise ScriptProviderError(
            f"script provider list_open_issues: malformed issue entry: {reason}"
        )

    if not isinstance(raw, Mapping):
        _fail("each issue must be a JSON object")
    number = raw.get("number")
    if not isinstance(number, int) or isinstance(number, bool):
        _fail("'number' must be an integer")
    title = raw.get("title")
    if not isinstance(title, str):
        _fail("'title' must be a string")
    url = raw.get("url")
    if not isinstance(url, str):
        _fail("'url' must be a string")
    labels = raw.get("labels", ())
    if not isinstance(labels, (list, tuple)) or not all(
        isinstance(label, str) for label in labels
    ):
        _fail("'labels' must be a list of strings")
    reservations = raw.get("reservations", ())
    if not isinstance(reservations, (list, tuple)) or not all(
        isinstance(reservation, Mapping) for reservation in reservations
    ):
        _fail("'reservations' must be a list of objects")
    validated_reservations = []
    for reservation in reservations:
        validated = validate_marker_payload(dict(reservation), issue_number=number)
        if validated is None:
            _fail(
                "each 'reservations' entry must match the "
                "loop/occurrence/state/at/label/issue/task_id/reason marker schema"
            )
        validated_reservations.append(validated)
    timestamps = {}
    for key in ("created_at", "updated_at"):
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail(f"{key!r} must be a finite number")
        if isinstance(value, float) and not math.isfinite(value):
            _fail(f"{key!r} must be a finite number")
        try:
            timestamps[key] = float(value)
        except OverflowError:
            # An arbitrarily large JSON integer is itself a "finite" int
            # but overflows on conversion to float -- treat it the same
            # as any other malformed timestamp instead of letting the
            # conversion itself crash discovery.
            _fail(f"{key!r} must be a finite number")
    return Issue(
        number=number,
        title=title,
        url=url,
        labels=tuple(labels),
        created_at=timestamps["created_at"],
        updated_at=timestamps["updated_at"],
        reservations=tuple(validated_reservations),
    )


class ScriptProvider:
    """A backlog provider backed by a declared, repo-packaged script.

    The script is invoked once per operation, named via a trailing
    ``--op <name>`` argument, with a structured JSON request object on
    stdin and expected to print a structured JSON response object on
    stdout. A non-zero exit is always a real error (the script's stderr is
    surfaced verbatim, truncated); a timeout or a malformed/non-JSON
    stdout are equally real errors, never silently treated as an empty
    success.

    Uses ``Popen`` + :func:`terminate_process_tree`
    (``agent_dispatch.procutil``) rather than a bare
    ``subprocess.run(timeout=)``: the latter's ``TimeoutExpired`` handling
    kills only the immediate child, but a venv ``python.exe`` launcher
    re-execs the base interpreter as a NEW child process on Windows (no
    true ``exec`` there) -- so a bare timeout kill leaks that re-exec'd
    grandchild running indefinitely, the same leak class
    ``run_background_capture`` (``procutil.py``) already mitigates the
    same way. On POSIX this reliably reaps the whole group. **On Windows
    it is not a complete fix**: if the launcher has already exited by the
    time a timeout is detected, only its now-dead PID remains to address,
    and ``taskkill /PID <dead-pid> /T`` cannot reliably reach a surviving
    orphan descendant through it -- a fully leader-independent guarantee
    there needs a Windows Job Object (``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``),
    not implemented here; tracked as a known residual gap, not silently
    claimed solved.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        producer_login: str | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
    ):
        if (
            isinstance(command, (str, bytes))
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise ValueError("command must be a non-empty list of non-empty strings")
        self.command = [str(part) for part in command]
        self.cwd = cwd
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds must be a number")
        timeout_error = ValueError(
            f"timeout_seconds must be a finite number > 0 and <= {MAX_TIMEOUT_SECONDS:g}"
        )
        try:
            timeout_seconds = float(timeout_seconds)
        except OverflowError:
            raise timeout_error from None
        if (
            timeout_seconds <= 0
            or timeout_seconds > MAX_TIMEOUT_SECONDS
            or not math.isfinite(timeout_seconds)
        ):
            raise timeout_error
        self.timeout_seconds = timeout_seconds
        self.producer_login = producer_login
        self._popen = popen

    def _invoke(self, op: str, request: Mapping[str, Any]) -> dict[str, Any]:
        from .procutil import _process_tree_kwargs, terminate_process_tree

        body: dict[str, Any] = dict(request)
        if self.producer_login is not None:
            body.setdefault("producer_login", self.producer_login)
        # `ensure_ascii=True` (never `False`): a lone UTF-16 surrogate can
        # legally end up in a Python string decoded from an escaped JSON
        # declaration or a script-reported issue field. With
        # `ensure_ascii=False`, json.dumps would pass it through verbatim,
        # and stdin's own UTF-8 `communicate()` encode then raises
        # `UnicodeEncodeError` *after* the subprocess has already started --
        # a spawn-time leak the generic `UnicodeError` handler below
        # mis-describes as a bad script response rather than reaping the
        # process it never got to run. Escaping to `\uXXXX` keeps the same
        # decoded JSON values while avoiding the raw surrogate in the wire
        # payload entirely.
        payload = json.dumps(body, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        argv = [*self.command, "--op", op]
        try:
            spawn_time = time.monotonic()
            proc = self._popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                shell=False,
                cwd=self.cwd,
                **_process_tree_kwargs(),
            )
        except (OSError, ValueError) as exc:
            # `Popen` raises `ValueError` (not just `OSError`) for certain
            # malformed argv values -- e.g. an embedded NUL byte in an
            # absolute command or a later argument -- that bypass
            # `_safe_resolve_path`'s own validation entirely. Catch it the
            # same way so every start failure is a consistent
            # `ScriptProviderError`, never a raw exception aborting the
            # tick.
            raise ScriptProviderError(
                f"script provider op {op!r} failed to start: {exc}"
            ) from exc
        from .companion import process_start_token

        proc_pid = getattr(proc, "pid", None)
        start_token = None
        if isinstance(proc_pid, int):
            try:
                start_token = process_start_token(proc_pid)
            except Exception:
                # Best-effort only (matches spawn_factories.py's own
                # precedent) -- an indeterminate probe must never abort
                # this call before it even reaches communicate()/cleanup,
                # leaving the just-spawned script running with nothing
                # left to reap it.
                start_token = None
        # `process_start_token`'s own `ps` fallback can block up to 5
        # seconds (its own fixed timeout) when `/proc` is unavailable
        # (e.g. macOS) -- without accounting for that against one
        # wall-clock deadline, `communicate(timeout=self.timeout_seconds)`
        # below would start its own *fresh* countdown afterward, letting a
        # short-timeout invocation run several times longer than declared.
        # Charge the probe's own elapsed time against the same budget.
        remaining = max(0.0, self.timeout_seconds - (time.monotonic() - spawn_time))
        try:
            stdout, stderr = proc.communicate(input=payload, timeout=remaining)
        except subprocess.TimeoutExpired:
            terminate_process_tree(proc, expected_start_token=start_token)
            raise ScriptProviderError(
                f"script provider op {op!r} timed out after "
                f"{self.timeout_seconds:.1f}s"
            ) from None
        except OverflowError as exc:
            # An out-of-range timeout_seconds reaches the platform wait
            # call before any waiting happens -- the constructor's own
            # MAX_TIMEOUT_SECONDS bound should already prevent this, but
            # the process is still running and must still be reaped.
            terminate_process_tree(proc, expected_start_token=start_token)
            raise ScriptProviderError(
                f"script provider op {op!r}: timeout_seconds "
                f"{self.timeout_seconds!r} is out of range: {exc}"
            ) from exc
        except UnicodeError as exc:
            raise ScriptProviderError(
                f"script provider op {op!r} produced output that is not "
                f"valid UTF-8: {exc}"
            ) from exc
        if proc.returncode != 0:
            stderr = str(stderr or "").strip()[:400]
            raise ScriptProviderError(
                f"script provider op {op!r} exited {proc.returncode}: {stderr}"
            )
        stdout = str(stdout or "").strip()
        if not stdout:
            return {}
        try:
            response = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ScriptProviderError(
                f"script provider op {op!r} produced invalid JSON on stdout: {exc}"
            ) from exc
        if not isinstance(response, dict):
            raise ScriptProviderError(
                f"script provider op {op!r} response must be a JSON object, "
                f"got {type(response).__name__}"
            )
        return response

    def list_open_issues(self, repo: str) -> list["Issue"]:
        response = self._invoke("list_open_issues", {"repo": repo})
        raw_issues = response.get("issues")
        if not isinstance(raw_issues, list):
            raise ScriptProviderError(
                "script provider list_open_issues: response must have an "
                "'issues' list"
            )
        issues = [_validate_issue_payload(raw) for raw in raw_issues]
        seen: set[int] = set()
        for issue in issues:
            if issue.number in seen:
                raise ScriptProviderError(
                    "script provider list_open_issues: duplicate issue "
                    f"number {issue.number} in response -- the loop treats "
                    "'number' as the unique resource-owner identity"
                )
            seen.add(issue.number)
        return issues

    def reserve(self, repo: str, issue: "Issue", reservation: dict[str, Any]) -> None:
        self._invoke(
            "reserve",
            {"repo": repo, "issue": _issue_to_dict(issue), "reservation": reservation},
        )

    def claim(
        self, repo: str, issue: "Issue", reservation: dict[str, Any], task_id: str
    ) -> None:
        self._invoke(
            "claim",
            {
                "repo": repo,
                "issue": _issue_to_dict(issue),
                "reservation": reservation,
                "task_id": task_id,
            },
        )

    def release(
        self,
        repo: str,
        issue: "Issue",
        reservation: dict[str, Any],
        reason: str,
    ) -> None:
        self._invoke(
            "release",
            {
                "repo": repo,
                "issue": _issue_to_dict(issue),
                "reservation": reservation,
                "reason": reason,
            },
        )


def validate_repo_field(repo: Any, *, provider: Any) -> None:
    """Validate ``repository-issue-loop``'s own ``repo`` field shape --
    ``'owner/name'`` for a forge-shaped provider, any non-empty string for
    ``script`` (the script interprets ``repo`` itself; the motivating
    consumer's own case has no forge-shaped identifier at all). Kept
    here, not in ``repository_issue_loops.py``, purely to stay under that
    module's size cap."""
    import re

    if provider == "script":
        if not isinstance(repo, str) or not repo.strip():
            raise RegistrarError(
                "repository-issue-loop repo: expected a non-empty string"
            )
        return
    if not isinstance(repo, str) or not re.fullmatch(r"[^/\s]+/[^/\s]+", repo):
        raise RegistrarError(
            "repository-issue-loop repo: expected 'owner/name' (GitHub) or "
            "'organization/project' (Azure DevOps)"
        )


def _backlog_identifier(config: Mapping[str, Any]) -> str:
    """Return the identifier passed to a ``script`` provider's own four
    backlog operations (``list_open_issues``/``reserve``/``claim``/
    ``release``), used by ``repository_issue_loops.py``. ``repo``
    otherwise doubles as both that backlog identity *and* the task's
    routing lane (``client.list``/``client.create``,
    ``embody.project_for_task``) -- for the forge providers that is fine
    (``owner/name``/``organization/project`` already names a real,
    embody-routable project), but a ``script`` declaration's own ``repo``
    can be an arbitrary, non-forge-shaped label with no corresponding
    project at all. Letting that label silently double as the task's
    routing lane misroutes (or coincidentally collides with) an unrelated
    real project. ``forge.backlog``, when set, decouples the two: the
    task still routes on ``repo`` unchanged, while the script sees
    ``backlog``. Falls back to ``repo`` when unset, so every existing
    declaration (and every non-``script`` provider, which never sets
    ``forge.backlog``) keeps its prior behavior exactly. Kept here, not in
    ``repository_issue_loops.py``, purely to stay under that module's size
    cap."""
    backlog = config["forge"].get("backlog")
    return str(backlog) if backlog else str(config["repo"])


def script_resource_namespace(forge: Mapping[str, Any]) -> str:
    """The `script` provider's explicit per-declaration coordinator
    namespace, used by `_resource_key` (``repository_issue_loops.py``).
    Unlike a real `owner/name` or `organization/project`, a script
    backlog's own `repo` is an arbitrary provider-local label with no
    inherent global-uniqueness guarantee -- two unrelated script
    declarations could innocently pick the same `repo` string and collide
    on the coordinator's resource key.

    An explicit `forge.namespace` (adopter-chosen, stable by
    construction) always wins when set -- the only guarantee strong
    enough for a correctness-critical, redundant/failover deployment:
    everything else here is inherently **best-effort**, since
    `_declared_repo_identity` is a git-remote probe cached by resolved
    repo root for the life of the daemon process (`_REPO_IDENTITY_CACHE`)
    rather than re-derived on every tick. A *transient* probe failure on
    the very first validation is therefore cached too, until the process
    restarts -- so the namespace can still differ across a restart, or
    across two redundant hosts whose first probe happened to fail
    differently, even though it stays stable between ticks on one host.
    Adopters with a genuine cross-host/failover deployment should set
    `forge.namespace` explicitly rather than relying on auto-derivation.

    Absent an explicit namespace, hashes the *as-declared,
    path-normalized* command/cwd (``_declared_command``/``_declared_cwd``,
    ``validate_script_forge_config``'s own markers -- already collapsed to
    one canonical spelling, e.g. `./poller.py` and `poller.py` hash
    identically) together with `producer_login` and
    `_declared_repo_identity` (the declaring repository's own
    canonicalized git remote, or its absolute path when no remote is
    resolvable) -- never the machine-resolved absolute `command`/`cwd` or
    `_script_path`: the same logical declaration (the same repo checkout,
    run on a second host for redundancy/failover, or under a different
    local Python installation) should still dedup against itself through
    the coordinator in the common case, which a machine-local absolute
    path would break by producing a different namespace per host.
    `producer_login` is included because it is itself forwarded to the
    script and can select a distinct backlog even when `command`/`cwd`
    are otherwise identical; `_declared_repo_identity` keeps two
    *unrelated* repositories that happen to declare the same relative
    path/producer_login/repo label from colliding. Preserves filesystem
    case semantics throughout (never casefolds) since case-sensitive
    filesystems distinguish paths differing only by case. Falls back to
    the resolved `command`/`cwd` (and an empty repo identity) when the
    declared markers are absent (direct construction bypassing
    ``validate_script_forge_config``)."""
    namespace = forge.get("namespace")
    if isinstance(namespace, str) and namespace:
        return hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:16]
    declared_command = forge.get("_declared_command")
    if declared_command is None:
        declared_command = forge.get("command") or ()
    command = [str(part) for part in declared_command]
    cwd = forge.get("_declared_cwd", forge.get("cwd"))
    identity = "\x1e".join(
        [
            *command,
            str(cwd or ""),
            str(forge.get("producer_login") or ""),
            str(forge.get("_declared_repo_identity") or ""),
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def build_provider(
    forge: Mapping[str, Any],
    *,
    producer_login: str | None,
    repo_root: str | Path | None = None,
) -> ScriptProvider:
    """Construct the ``script`` forge provider from its validated/resolved
    config -- kept here (not in ``repository_issue_loops.py``) so that
    module needs no direct import of :class:`ScriptProvider` itself.

    Re-normalizes ``command``/``cwd`` through
    ``validate_script_forge_config`` first whenever ``forge`` hasn't
    already been through it (its own ``_script_path`` marker is the
    tell) -- regardless of whether ``repo_root`` is known:
    ``validate_script_forge_config`` itself requires a fully absolute
    `command`/`cwd` when ``repo_root`` is ``None``, so even a rootless
    direct declaration still needs this pass to apply the Windows
    interpreter/shell portability wrap (``.py``/``.sh``/``.ps1``) and
    attach the internal markers `script_resource_namespace` reads -- a
    caller that hands this the *raw* declaration straight out of
    ``spec.repository_issue_loop`` (the CLI's own `status`/`discover`
    commands, which never ran `run_tick`'s own `validate_config(config,
    cwd=cwd)` first) would otherwise reach the subprocess with a bare
    `.py`/`.sh`/`.ps1` argv and no interpreter prefix. Re-running it on an
    *already*-resolved ``forge`` is not merely redundant but actively
    wrong: `command[0]` by then is the prefixed interpreter/shell (e.g.
    `sys.executable`), not the original script path, so a second pass
    would misresolve the namespace and corrupt the portability wrap. The
    *full* `validate_config` is separately unsafe to re-run here
    regardless: its own output carries derived keys (e.g.
    `worker_filters`) that are not valid re-input.
    """
    if "_script_path" not in forge:
        forge = {**forge, **validate_script_forge_config(
            forge, provider="script", repo_root=repo_root
        )}
    return ScriptProvider(
        forge["command"],
        cwd=forge.get("cwd"),
        timeout_seconds=forge.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
        producer_login=producer_login,
    )
