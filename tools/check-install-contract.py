#!/usr/bin/env python3
"""Enforce the install contract (docs/install-contract.md) across plugins.

Each plugin with a runtime installer must, per language variant:
  1. install the package via `uv pip install` (no file-copy of the package),
  2. emit no binstub that sets PYTHONPATH to a runtime lib/ dir,
  3. write a schema_version 3 deploy manifest with a `source` block,
  4. carry a source-kind resolver identical (per language) across plugins,
     and — for the update-flow robustness contract (dotfiles #935) — the
     byte-identical `install-contract:v5` self-stage prologue and smoke seam
     (per language), so a concurrent `copilot plugin update` never fights a
     wedged installer for the singleton payload and a stalled install
     self-terminates,
  5. adopt the immutable-versioned venv layout (dotfiles #581): ship a
     `scripts/versioned_runtime.py` primitive that is byte-identical to the
     canonical source (`libs/versioned-runtime/versioned_runtime.py`, vendored in
     by `tools/sync-versioned-runtime.py`) AND wire it in the installer (the
     `install-contract:v3 versioned-venv` block), so a version bump builds a
     fresh versions/<v> slot and swaps a `.venv`/`venv` link instead of ever
     mutating a live runtime's venv.
  6. declare a reconcilable ``runtimeScope`` in ``plugin.json``: every plugin's
     declared scope must be one the reconciler accepts, and a plugin that ships a
     runtime installer (``scripts/install.*`` or ``scripts/init.*``) must declare
     ``universal`` or ``machine-gated`` -- never ``none`` or nothing -- so the
     launch-time reconcile and ``update`` keep its runtime current.

The enforced entrypoint pair is the plugin's *canonical* installer: `install.*`
when present (it carries an `update` action), otherwise `init.*` for plugins
that ship only an idempotent bootstrap (agent-mcp, agent-containers). A plugin
with both has `init.*` delegate to `install.*`, so only `install.*` is checked.

Payload-runtime plugins (no pyproject.toml -- e.g. a JS extension copied to
~/.copilot/extensions/) are exempt from rule 1 (there is no Python package to
install); rules 2-4 still apply. See docs/install-contract.md
§ "Payload runtime (non-Python)".

Run manually:  python tools/check-install-contract.py
Exit code 0 = conformant, 1 = violations (suitable for a pre-push hook).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer_engine_ref as ier

from install_contract_guard import (
    PERSISTENT_ENV_END,
    PERSISTENT_ENV_START,
    is_ignored_scan_path,
    persistent_environment_violations,
)

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
INSTALLER_ENGINE_SYNC = Path(__file__).resolve().parent / "sync-installer-engine.py"

# Immutable-versioned-runtime invariant (dotfiles #581): every Python runtime
# plugin ships the byte-identical scripts/versioned_runtime.py primitive AND
# wires it in its installer (the block bearing this marker). The layout builds
# each version into versions/<v> and points a `.venv`/`venv` junction/symlink at
# the active one, so a version bump never mutates a live runtime's venv. See
# docs/patterns/README.md § "Runtime installs are immutable and versioned".
VERSIONED_MARKER = "install-contract:v3 versioned-venv"
VERSIONED_RUNTIME_FILE = "versioned_runtime.py"
VERSIONED_RUNTIME_COMPONENTS = ("versioned_runtime_leases.py", "versioned_runtime_construction.py")
# The one canonical source of the primitive. Every Python runtime plugin's
# scripts/versioned_runtime.py is vendored byte-identically from here by
# tools/sync-versioned-runtime.py; this check enforces it (drift => run sync).
VERSIONED_RUNTIME_CANONICAL = REPO / "libs" / "versioned-runtime" / VERSIONED_RUNTIME_FILE

# A binstub/install script must not point PYTHONPATH at a runtime lib/ dir.
FORBIDDEN_PYTHONPATH = re.compile(r"PYTHONPATH[^\n]*\.agent-[a-z]+[\\/]lib", re.IGNORECASE)

# A Windows install.ps1 must NOT launch the unsigned console-script trampoline
# (…\Scripts\<name>.exe) -- Smart App Control blocks it (CodeIntegrity 3077).
# Launch "<venv>\Scripts\python.exe -m <pkg>" instead. The legacy .exe may still
# be *matched* (Get-RunningProcess) but never *launched*: launching shows up as
# the trampoline followed by an argument list (`" %*`, `" start`, `" version`).
# python.exe / pythonw.exe are explicitly allowed.
FORBIDDEN_TRAMPOLINE = re.compile(
    r"Scripts[\\/](?!python\.exe)(?!pythonw\.exe)[\w.-]+\.exe[\"']?\s+(?:%\*|start\b|version\b)",
    re.IGNORECASE,
)

# Session-start runtime-reconcile invariant: every Python runtime plugin (it
# installs a ~/.local/bin binstub) MUST wire a sessionStart hook that reconciles
# that runtime at launch, so a `copilot plugin update` redeploys the binstub
# without a manual reinstall. Detected structurally: plugin.json "hooks" -> a
# hooks file with a non-empty hooks.sessionStart whose command runs a
# `bootstrap-check`. See docs/install-contract.md § "Runtime self-reconcile".
#
# The baseline is now EMPTY -- every runtime plugin complies (dotfiles#779 burned
# it down). Do NOT add plugins here to silence the check: wire the hook instead.
# This set exists only as the explicit, greppable seam for a deliberate,
# time-boxed exemption should one ever be genuinely needed.
EXEMPT_SESSION_HOOK: frozenset[str] = frozenset()

# Thread-A self-provision invariant (dotfiles#1393): every Python runtime plugin's
# canonical entrypoint (install.* if present, else init.*) MUST declare a `stamp`
# action (fast base install: snapshot/pointer + a self-provisioning binstub, NO
# inline venv) and a `provision` action (the deferred heavy build the binstub runs
# on first use). This keeps a sessionStart install fast and never wedges/pins the
# marketplace payload. See docs/install-contract.md § "Fast install + deferred
# self-provision" and the correct-install-flows effort.
#
# BASELINE_NO_STAMP is the explicit, greppable, time-boxed exemption for plugins
# whose Windows `install.ps1` lane has NOT yet been ported -- the Thread-B service
# runtimes (detached daemons + graceful cutover), tracked separately. Their POSIX
# `.sh` already declares stamp/provision, so the exemption is ps1-only. SHRINK this
# set as each is ported; do NOT add plugins here to silence the check.
# EMPTY: all Thread-B service runtimes (agent-dispatch, agent-vault, agent-index,
# agent-bridge) are ported -- their install.ps1 now declares stamp/provision.
BASELINE_NO_STAMP_PS1: frozenset[str] = frozenset()


def _declares_stamp_provision(text: str, ext: str) -> bool:
    """True if an entrypoint declares BOTH a `stamp` and a `provision` action.

    ps1: the tokens ``'stamp'`` / ``'provision'`` appear (ValidateSet + switch).
    sh:  ``stamp`` / ``provision`` appear as a case label -- immediately followed
    by ``)`` or ``|`` -- so a combined ``stamp|provision|init)`` label and the
    incidental phrase "stamp build info" are handled correctly.
    """
    if ext == "ps1":
        return ("'stamp'" in text) and ("'provision'" in text)
    return bool(re.search(r"\bstamp[)|]", text)) and bool(re.search(r"\bprovision[)|]", text))


def _session_hook_problem(plugin: Path) -> str | None:
    """Return a violation string if a runtime plugin lacks the sessionStart
    reconcile hook, else None."""
    try:
        data = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    except Exception:
        return "plugin.json unreadable (cannot verify sessionStart reconcile hook)"
    try:
        invocation = json.loads(
            (plugin / "payload-invocation.json").read_text(encoding="utf-8")
        )
    except Exception:
        invocation = {}
    if invocation.get("sessionStartBootstrap") is False:
        dispatcher = invocation.get("payloadDispatcher")
        if not isinstance(invocation.get("catalogGate"), str) or not invocation[
            "catalogGate"
        ]:
            return (
                "sessionStartBootstrap false requires a fail-closed catalogGate"
            )
        if not isinstance(dispatcher, dict) or not all(
            isinstance(dispatcher.get(platform), str)
            and dispatcher[platform]
            for platform in ("posix", "windows")
        ):
            return (
                "sessionStartBootstrap false requires gated payload dispatchers "
                "for both platforms"
            )
        return None
    hooks_ref = data.get("hooks")
    if not hooks_ref:
        return ('no sessionStart runtime-reconcile hook -- set plugin.json "hooks" to a '
                "hooks file with a sessionStart bootstrap-check (docs/install-contract.md "
                "\u00a7 'Runtime self-reconcile')")
    try:
        hooks_doc = json.loads((plugin / hooks_ref).read_text(encoding="utf-8"))
    except Exception:
        return f"hooks file '{hooks_ref}' is missing or unreadable"
    session = (hooks_doc.get("hooks") or {}).get("sessionStart")
    if not session:
        return f"hooks file '{hooks_ref}' has no sessionStart entry"
    if "bootstrap-check" not in json.dumps(session):
        return "sessionStart hook does not run a bootstrap-check runtime reconcile"
    return None


def _extract_block(text: str, start_marker: str, open_char: str, close_char: str) -> str | None:
    """Return the balanced {...} block beginning at the first start_marker line."""
    idx = text.find(start_marker)
    if idx < 0:
        return None
    brace = text.find(open_char, idx)
    if brace < 0:
        return None
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == open_char:
            depth += 1
        elif text[i] == close_char:
            depth -= 1
            if depth == 0:
                return text[idx : i + 1]
    return None


def _norm(s: str | None) -> str | None:
    if s is None:
        return None
    return re.sub(r"\s+", " ", s).strip()


def _extract_marker(text: str, start_marker: str, end_marker: str) -> str | None:
    """Return the substring from the line bearing start_marker through the line
    bearing end_marker (inclusive), or None. Used for the install-contract:v5
    self-stage / smoke-seam blocks, which are byte-identical across plugins per
    language (they are delimited by comment markers, not a single brace)."""
    i = text.find(start_marker)
    if i < 0:
        return None
    j = text.find(end_marker, i)
    if j < 0:
        return None
    return text[i : j + len(end_marker)]



def _entrypoint_base(plugin: Path) -> str | None:
    """Return the runtime entrypoint base for a plugin, or None.

    Prefers ``install`` (the canonical installer with an ``update`` action);
    falls back to ``init`` for plugins that ship only an idempotent bootstrap
    (e.g. agent-mcp, agent-containers). When a plugin has both, ``init``
    delegates to ``install`` -- the canonical pair -- so only ``install`` is
    enforced. Returns None for plugins with no runtime installer at all.
    """
    scripts = plugin / "scripts"
    if (scripts / "install.ps1").exists() or (scripts / "install.sh").exists():
        return "install"
    if (scripts / "init.ps1").exists() or (scripts / "init.sh").exists():
        return "init"
    return None


# The ``runtimeScope`` values the reconciler accepts -- kept in lockstep with
# ``agent_worktrees.reconcile.VALID_SCOPES`` by tools/test_check_install_contract.py.
VALID_RUNTIME_SCOPES = ("universal", "machine-gated", "none")
RECONCILED_RUNTIME_SCOPES = ("universal", "machine-gated")


def _runtime_scope_violations(plugins_dir: Path) -> list[str]:
    """Validate each plugin's declared ``runtimeScope`` (docs/install-contract.md).

    A declared scope must be one the reconciler accepts (an unknown value is
    silently treated as ``none``). A plugin that ships a runtime installer must
    declare a reconciled scope: with ``none`` (or no declaration) neither the
    launch-time reconcile nor ``update`` ever rebuilds its runtime, so a payload
    update leaves the deployed runtime stale.
    """
    violations: list[str] = []
    for plugin in sorted(p for p in plugins_dir.iterdir() if p.is_dir()):
        manifest = plugin / "plugin.json"
        if not manifest.is_file():
            continue
        name = plugin.name
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            violations.append(f"{name}: plugin.json is unreadable ({exc})")
            continue
        if not isinstance(data, dict):
            violations.append(f"{name}: plugin.json is not a JSON object")
            continue
        scope = data.get("runtimeScope")
        if "runtimeScope" in data and scope not in VALID_RUNTIME_SCOPES:
            violations.append(
                f"{name}: runtimeScope {scope!r} is not one of "
                f"{', '.join(VALID_RUNTIME_SCOPES)}"
            )
            continue
        base = _entrypoint_base(plugin)
        if base is not None and scope not in RECONCILED_RUNTIME_SCOPES:
            declared = "undeclared" if scope is None else repr(scope)
            violations.append(
                f"{name}: ships a runtime installer (scripts/{base}.*) but runtimeScope "
                f"is {declared} -- declare 'universal' or 'machine-gated' so the "
                "launch-time reconcile and 'update' keep its runtime current"
            )
    return violations


# `exec 8>"$f" 2>/dev/null` (no command) applies EVERY redirection to the
# current shell, so it silently discards all of the installer's later stderr --
# including its own failure messages. Group it instead:
# `{ exec 8>"$f"; } 2>/dev/null` keeps fd 8 and restores stderr.
_EXEC_STDERR_SWALLOW = re.compile(r'\bexec\s+\d+[<>]\s*(?:&-|&\d+|"[^"]*"|\S+)\s+2>\s*/dev/null')


def _exec_stderr_swallow_violations(plugins_dir: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(plugins_dir.glob("*/scripts/*.sh")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if _EXEC_STDERR_SWALLOW.search(line):
                violations.append(
                    f"{path.relative_to(plugins_dir.parent).as_posix()}:{lineno}: "
                    "`exec N>... 2>/dev/null` redirects the shell's stderr for the rest "
                    "of the script; use `{ exec N>...; } 2>/dev/null`"
                )
    return violations


# A completion marker is the published proof that a slot is complete: a refused
# or failed `mark-complete` must stop activation, never be swallowed. The helper
# must not end its writer pipeline in `|| true` (on one line or across line
# continuations), and no caller may invoke it -- with or without arguments -- as
# a statement whose result is ignored, or test it in a conditional whose
# failure branch does not stop (`if ! helper; then warn; fi`).
_MARK_COMPLETE_SWALLOW = re.compile(r"mark-complete.*\|\|\s*true|\|\s*sed\b.*\|\|\s*true")
_MARK_COMPLETE_CALL = re.compile(r"^\s*(?:_versioned_mark_complete|Invoke-VersionedMarkComplete)(?:\s|$)")
_LINE_CONTINUATION = re.compile(r"(?:\\|`)[ \t]*\n[ \t]*")


def _mark_complete_result_violations(plugins_dir: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted([*plugins_dir.glob("*/scripts/*.sh"), *plugins_dir.glob("*/scripts/*.ps1")]):
        text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        helper = re.search(
            r"^(?:_versioned_mark_complete\(\) \{|function Invoke-VersionedMarkComplete \{)\n(.*?)^\}",
            text, re.M | re.S,
        )
        rel = path.relative_to(plugins_dir.parent).as_posix()
        if helper and any(
            _MARK_COMPLETE_SWALLOW.search(line)
            for line in _LINE_CONTINUATION.sub(" ", helper.group(1)).splitlines()
            if not line.lstrip().startswith("#")
        ):
            violations.append(f"{rel}: the completion-marker helper swallows a failed mark-complete")
        elif helper and not _helper_propagates_writer_status(helper.group(1), path.suffix):
            violations.append(
                f"{rel}: the completion-marker helper does not fail when the mark-complete "
                "writer fails"
            )
        # Blank PowerShell block comments (keeping line numbers) so prose that
        # names the helper is not mistaken for a call.
        text = re.sub(r"<#.*?#>", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
        lines = text.split("\n")
        for lineno, line in enumerate(lines, 1):
            # Strings are prose, except a double-quoted `$(...)` that runs the helper.
            code = re.sub(r"'(?:[^']|'')*'", "''", line)
            code = re.sub(r'"(?:[^"`$]|`.|\$(?!\())*"', '""', code).split("#", 1)[0]
            if _MARK_COMPLETE_DEF.match(line) or not _MARK_COMPLETE_MENTION.search(code):
                continue
            if _MARK_COMPLETE_CALL.match(line):
                statement = line
                index = lineno
                while re.search(r"(?:\\|`)[ \t]*$", statement) and index < len(lines):
                    statement = statement.rstrip()[:-1] + " " + lines[index]
                    index += 1
                tail = statement.split("||", 1)[1] if "||" in statement else None
                # `|| return $?` right after the call returns the helper's own status.
                ps1 = path.suffix == ".ps1"
                stops = tail is not None and bool(
                    (not ps1 and _IMMEDIATE_STATUS.match(tail))
                    or _fails_unconditionally(tail, ps1=ps1)
                )
            elif _MARK_COMPLETE_IF_NOT.search(line):
                tail = "\n".join(lines[lineno - 1:lineno + 20])
                stops = _fails_unconditionally(
                    _braced_body(tail[tail.index("{", tail.index(_PS1_HELPER)):]), ps1=True
                )
            elif _MARK_COMPLETE_IF_PS1.search(line):
                stops = _ps1_else_fails("\n".join(lines[lineno - 1:lineno + 40]))
            elif match := _MARK_COMPLETE_SH_IF.match(line):
                stops = _sh_failure_branch_stops(lines[lineno - 1:], negated=bool(match.group(1)))
            else:
                violations.append(
                    f"{rel}:{lineno}: unrecognized completion-marker call form; call it as a "
                    "checked statement (`helper || exit 1`) or in an if/else that fails"
                )
                continue
            if not stops:
                violations.append(
                    f"{rel}:{lineno}: completion-marker publication result is ignored; "
                    "stop activation when it fails"
                )
    return violations


_PS1_HELPER = "Invoke-VersionedMarkComplete"
_MARK_COMPLETE_MENTION = re.compile(
    r"(?<![\w-])(?:_versioned_mark_complete|Invoke-VersionedMarkComplete)(?![\w-])"
)
_MARK_COMPLETE_DEF = re.compile(
    r"^\s*(?:_versioned_mark_complete\(\)\s*\{|function\s+Invoke-VersionedMarkComplete\b)"
)
_MARK_COMPLETE_IF_PS1 = re.compile(r"\b(?:if|elseif)\s*\(\s*\(?\s*Invoke-VersionedMarkComplete\b")


def _ps1_else_fails(text: str) -> bool:
    """For ``if (Invoke-VersionedMarkComplete) { ... }``: the ``else`` block is
    the failure branch, and it must fail unconditionally; no ``else`` ignores
    the failure."""
    start = text.index("{", text.index(_PS1_HELPER))
    after = text[start + len(_braced_body(text[start:])) + 2:]
    branch = re.match(r"\s*else\s*\{", after)
    return bool(branch) and _fails_unconditionally(
        _braced_body(after[branch.end() - 1:]), ps1=True
    )
_MARK_COMPLETE_IF_NOT = re.compile(r"\bif\s*\(\s*-not\s*\(\s*Invoke-VersionedMarkComplete\b")
# A failure branch must report failure in its own language -- never
# ``return 0``/``exit 0`` or a bare ``return`` (the previous command's status).
# Shell: a non-zero ``exit``/``return``. PowerShell: ``return $false``,
# ``throw`` or a non-zero ``exit``; ``return 1`` there is truthy, so a caller's
# ``if (-not (...))`` would read it as success.
_IMMEDIATE_STATUS = re.compile(r"\s*(?:return|exit)\s+\"?\$\?\"?(?![\w-])")
_FAILS = re.compile(r"\b(?:exit|return)\s+[1-9]\d*(?![\w-])")
_PS1_FAILS = re.compile(r"\bthrow\b|\breturn\s+\$false\b|\bexit\s+[1-9]\d*(?![\w-])")
_QUOTED_STATUS = re.compile(r'"(\$(?:\{?[A-Za-z_]\w*\}?|\?))"')
_PARAM_EXPANSION = re.compile(r"\$\{(\w+)[^}]*\}")


def _fails_unconditionally(
    body: str, status_vars: tuple[str, ...] = (), *, ps1: bool = False
) -> bool:
    """Whether ``body`` reports failure on every path through it.

    Only a top-level statement counts: a stop nested in a conditional
    (``if (...) { return $false }``, ``[[ ... ]] && exit 1``, an inner
    ``if ... fi`` or ``{ ... }`` block) can be skipped, letting the failure
    fall through to activation. A variable status counts only when it is one of
    ``status_vars``, a status the caller proved was captured from the failing
    writer; any other variable (``ok=0; return "$ok"``) may hold success.
    """
    fails = _PS1_FAILS if ps1 else _FAILS
    if status_vars:
        names = "|".join(map(re.escape, status_vars))
        fails = re.compile(rf"{fails.pattern}|\b(?:exit|return)\s+\$\{{?(?:{names})\}}?(?![\w-])")
    code = _PARAM_EXPANSION.sub(r"$\1", _QUOTED_STATUS.sub(r"\1", body))
    code = _strip_quoted_strings(code).strip()
    if code.startswith("{") and code.endswith("}"):
        code = code[1:-1]
    flat, depth = [], 0
    for char in code:
        depth += {"{": 1}.get(char, 0)
        if depth == 0:
            flat.append(char)
        depth -= {"}": 1}.get(char, 0) if depth else 0
    statements = re.split(r"[;\n]", "".join(flat))
    nested = 0
    for statement in (s.strip() for s in statements):
        if re.match(r"(?:if|while|until|for|case)\b", statement):
            nested += 1
        if nested == 0 and fails.match(statement):
            return True
        if re.match(r"(?:fi|done|esac)\b", statement) and nested:
            nested -= 1
    return False


# The writer invocation inside each helper, and how its status must be handled.
_SH_WRITER = re.compile(r'"\$py"\s+"\$\{args\[@\]\}"|(?<![\w])_runtime_construction_complete\b')
_PS1_WRITER = re.compile(r"&\s*\$py\b")
_PS1_STATUS_CHECK = re.compile(r"\bif\s*\(\s*\$(?:LASTEXITCODE|\w+\.ExitCode)\s+-ne\s+0\s*\)\s*\{")


def _helper_propagates_writer_status(body: str, suffix: str) -> bool:
    """Whether the helper fails when its ``mark-complete`` writer fails.

    PowerShell must branch on the writer's exit code (``$LASTEXITCODE`` or a
    captured ``.ExitCode``) and return ``$false``/throw/exit there. Shell runs
    under ``set -o pipefail``; a writer line may not end in an ``||`` fallback
    that neither keeps the status (``rc=$?``) nor stops. Behavior is exercised
    end to end by ``tools/test_check_install_contract.py``.
    """
    code = "\n".join(
        line for line in _LINE_CONTINUATION.sub(" ", body).splitlines()
        if not line.lstrip().startswith("#")
    )
    if suffix == ".ps1":
        code = re.sub(r"<#.*?#>", "", code, flags=re.S)
        attached = re.search(
            r"(?m)^[ \t]*Complete-RuntimeConstruction[ \t]+-PayloadHash[ \t]+"
            r"(?:\$\w+|\(Get-PayloadHash\))[ \t]*$",
            code,
        )
        if attached:
            before = _strip_quoted_strings(code[:attached.start()])
            return (
                before.count("{") == before.count("}")
                and not re.search(r"\b(?:try|catch|trap)\b", before)
                and code[attached.end():].strip() == "return $true"
            )
        writer = _PS1_WRITER.search(code)
        check = writer and _PS1_STATUS_CHECK.search(code, writer.end())
        return bool(check) and _fails_unconditionally(
            _braced_body(code[check.end() - 1:]), ps1=True
        )
    lines = [line for line in code.splitlines() if line.strip()]
    found = [i for i, line in enumerate(lines) if _SH_WRITER.search(line)]
    return bool(found) and all(_sh_writer_status_kept(lines, i) for i in found)


_SH_RC_CAPTURE = re.compile(r"(?:^|\|\|)\s*(?:local\s+)?rc=\$\?|PIPESTATUS", re.M)
_SH_DECLARATION = re.compile(r"^\s*(?:local|declare|typeset|export|readonly)\b")
_SH_RC_TEST = re.compile(
    r'^\s*if\s+(?:\[\[\s*"?\$\{?rc\}?"?\s+-ne\s+0\s*\]\]|\(\(\s*rc\s*(?:!=\s*0\s*)?\)\))'
)


def _sh_writer_status_kept(lines: list[str], index: int) -> bool:
    """Whether the shell writer at ``lines[index]`` decides the helper's result.

    The writer is the helper's last statement (``pipefail`` returns its status);
    tested by ``if ! writer`` with a failing branch; followed by an ``||``
    branch that fails; or its status is captured in ``rc`` and then drives a
    failing return (a top-level ``return "$rc"`` or ``if [[ "$rc" -ne 0 ]]``
    whose branch fails). Capturing the status and then returning success does
    not count.
    """
    line, rest = lines[index], lines[index + 1:]
    # `local out="$(writer)"` returns the builtin's status (0), not the writer's.
    if _SH_DECLARATION.match(line):
        return False
    following = "\n".join([line, *rest[:1]])
    if _SH_RC_CAPTURE.search(following) or any("PIPESTATUS" in r for r in rest):
        after = "\n".join(rest)
        tests = [i for i, r in enumerate(rest) if _SH_RC_TEST.match(r)]
        return _fails_unconditionally(after, ("rc",)) or any(
            _sh_failure_branch_stops(rest[i:], negated=True, status_vars=("rc",)) for i in tests
        )
    if re.match(r"\s*if\s+!", line):
        return _sh_failure_branch_stops(lines[index:], negated=True)
    _, sep, tail = line.partition("||")
    if sep:
        return _fails_unconditionally(tail)
    return not rest


_MARK_COMPLETE_SH_IF = re.compile(r"^\s*(?:el)?if\s+(!\s*)?_versioned_mark_complete\b")
_SH_KEYWORD = re.compile(r"(?<![\w$/.-])(then|if|fi|else|elif)(?![\w-])")


def _sh_failure_branch_stops(
    lines: list[str], *, negated: bool, status_vars: tuple[str, ...] = ()
) -> bool:
    """Whether the shell ``if``/``elif`` starting ``lines`` stops on helper failure.

    The failure branch is the ``then`` body of ``if ! helper`` and the ``else``
    body of ``if helper``; a positive test with no ``else`` ignores the failure.
    """
    code = "\n".join(
        _strip_quoted_strings(_QUOTED_STATUS.sub(r"\1", line))
        for line in lines if not line.lstrip().startswith("#")
    )
    keywords = list(_SH_KEYWORD.finditer(code))
    start = next((k for k in keywords if k.group(1) == "then"), None)
    # `if helper || true; then` (or `&&`) no longer tests the helper alone.
    if start is None or re.search(r"\|\||&&", code[:start.start()]):
        return False
    depth, branch, bodies, begin = 0, "then", {}, start.end()
    for keyword in (k for k in keywords if k.start() >= start.end()):
        word = keyword.group(1)
        if word == "if":
            depth += 1
        elif word == "fi" and depth:
            depth -= 1
        elif depth == 0 and word in ("else", "elif", "fi"):
            bodies.setdefault(branch, code[begin:keyword.start()])
            if word == "fi":
                break
            branch, begin = word, keyword.end()
    failure = bodies.get("then" if negated else "else")
    return failure is not None and _fails_unconditionally(failure, status_vars)


def _braced_body(text: str) -> str:
    """The ``{...}`` block that ``text`` starts with (unbalanced: the rest)."""
    depth = 0
    for index, char in enumerate(text):
        depth += {"{": 1, "}": -1}.get(char, 0)
        if depth == 0:
            return text[1:index]
    return text[1:]


def _verify_installer_engine_sync() -> list[str]:
    spec = importlib.util.spec_from_file_location("sync_installer_engine", INSTALLER_ENGINE_SYNC)
    if spec is None or spec.loader is None:
        return [f"unable to load {INSTALLER_ENGINE_SYNC.relative_to(REPO)}"]
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.verify())


def _strip_quoted_strings(line: str) -> str:
    line = re.sub(r"'(?:[^']|'')*'", "''", line)
    line = re.sub(r'"(?:[^"`]|`.)*"', '""', line)
    return line


def _non_comment_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(line)
    return lines


def _uses_installer_engine(text: str, ext: str) -> bool:
    return any(
        ier.source_match_count_text(line, ext) > 0 for line in _non_comment_lines(text)
    )


def _calls_engine_function(text: str, ext: str, *names: str) -> bool:
    code_lines = [_strip_quoted_strings(line) for line in _non_comment_lines(text)]
    if ext == "ps1":
        return any(
            re.search(
                rf"""(?<![\w-])(?:{"|".join(re.escape(name) for name in names)})(?=\s|\(|$)""",
                line,
            )
            for line in code_lines
        )
    return any(
        re.search(
            rf"""(?<![\w])(?:{"|".join(re.escape(name) for name in names)})(?=\s|\(|$)""",
            line,
        )
        for line in code_lines
    )


def _uses_engine_uv_install(text: str, ext: str) -> bool:
    ordinary_call = _calls_engine_function(
        text,
        ext,
        "Invoke-UvPipInstallResilient",
        "invoke_uv_pip_install_resilient",
    )
    captured_call = ext == "sh" and any(
        re.search(
            r'^\s*(?:if\s+)?(?:!\s*)?\w+="?\$\(\s*invoke_uv_pip_install_resilient\s',
            line,
        )
        for line in _non_comment_lines(text)
    )
    return _uses_installer_engine(text, ext) and (ordinary_call or captured_call)


def _uses_dependency_policy_install(text: str, ext: str) -> bool:
    if ext != "sh":
        return False
    code = "\n".join(_non_comment_lines(text))
    return bool(re.search(
        r'(?m)^\s*"\$VENV_PYTHON"\s+-I\s+'
        r'"\$SCRIPT_DIR/installer_dependency_policy\.py"\s+\\?\s*--uv\s+',
        code,
    ))


def _incomplete_dependency_policy_calls(text: str) -> int:
    code = "\n".join(_non_comment_lines(text))
    code = re.sub(r"(?:\\|`)[ \t]*\n[ \t]*", " ", code)
    incomplete = 0
    for line in code.splitlines():
        calls = list(re.finditer(r"installer_dependency_policy\.py", line))
        for index, call in enumerate(calls):
            if "-I" not in line[:call.start()]:
                continue
            end = calls[index + 1].start() if index + 1 < len(calls) else len(line)
            arguments = line[call.end():end]
            provider = re.search(r"(?:\s--uv(?:\s|=)|['\"]--uv['\"]\s*,)", arguments)
            separator = re.search(r"(?:\s--(?:\s|$)|['\"]--['\"]\s*,)", arguments)
            if not provider or not separator:
                incomplete += 1
    return incomplete


def _uses_engine_manifest_writer(text: str, ext: str) -> bool:
    return _uses_installer_engine(text, ext) and _calls_engine_function(
        text,
        ext,
        "Write-DeployManifest",
        "write_deploy_manifest",
    )


def _check_runtime_retention(runtime) -> None:
    """Exercise the canonical lifetime contract without an installed runtime."""
    with tempfile.TemporaryDirectory(prefix="runtime-retention-contract-") as temporary:
        root = Path(temporary)
        candidate = runtime.slot(root, "candidate")
        payload = candidate / "payload"
        payload.write_text("unchanged", encoding="utf-8")
        with runtime.construction_lease(root, "candidate", timeout=0):
            assert runtime.gc(root) == [], "GC removed a construction owner"
            assert runtime.toss_incomplete(root) == [], "toss removed a construction owner"
        with runtime.retention_lease(root, "candidate", timeout=0):
            assert runtime.gc(root) == [], "GC removed a retained candidate"
            assert runtime.toss_incomplete(root) == [], "toss removed a retained candidate"
            try:
                runtime.slot(root, "candidate", clean_incomplete=True)
            except RuntimeError:
                pass
            else:
                raise AssertionError("incomplete cleanup admitted a retained candidate")
            assert payload.read_text(encoding="utf-8") == "unchanged"
        assert runtime.toss_incomplete(root) == ["candidate"], "released candidate was not reclaimed"
        marker = runtime.mark_complete(root, "candidate", payload_hash="same", pid=11)
        before = marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns
        runtime.mark_complete(root, "candidate", payload_hash="same", pid=22)
        assert (marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns) == before, (
            "matching completion rewrote immutable evidence"
        )
        try:
            runtime.mark_complete(root, "candidate", payload_hash="different")
        except RuntimeError:
            pass
        else:
            raise AssertionError("numbered completion accepted conflicting content")
        assert marker.read_bytes() == before[0], "conflict changed immutable evidence"
        with runtime.retention_lease(root, "candidate", payload_hash="same", timeout=0):
            runtime.activate(root, "candidate")
        assert runtime.gc(root) == [], "GC removed the published candidate"


def check() -> int:
    violations: list[str] = []
    ps1_resolvers: dict[str, str | None] = {}
    sh_resolvers: dict[str, str | None] = {}
    vrt_hashes: dict[str, str] = {}
    # install-contract:v5 self-stage / smoke-seam blocks -- byte-identical across
    # plugins, per language (#935). Keyed by plugin name; value is the exact block
    # text (or None if absent). Non-payload plugins must carry both.
    v5_selfstage: dict[str, dict[str, str | None]] = {"ps1": {}, "sh": {}}
    v5_smoke: dict[str, dict[str, str | None]] = {"ps1": {}, "sh": {}}

    plugins = sorted(
        p for p in PLUGINS_DIR.iterdir()
        if p.is_dir() and _entrypoint_base(p) is not None
    )
    if not plugins:
        print("No plugins with install scripts found.", file=sys.stderr)
        return 1

    violations.extend(_runtime_scope_violations(PLUGINS_DIR))
    violations.extend(_exec_stderr_swallow_violations(PLUGINS_DIR))
    violations.extend(_mark_complete_result_violations(PLUGINS_DIR))

    persistent_environment_blocks: dict[str, str | None] = {}
    for path in sorted(PLUGINS_DIR.rglob("*.ps1")):
        text = path.read_text(encoding="utf-8", errors="replace")
        direct_access = persistent_environment_violations(text)
        uses_adapter = (
            "Get-CopilotPersistentEnvironmentVariable" in text
            or "Set-CopilotPersistentEnvironmentVariable" in text
        )
        if (
            PERSISTENT_ENV_START not in text
            and not uses_adapter
            and not direct_access
        ):
            continue
        relative = path.relative_to(REPO).as_posix()
        block = _extract_marker(text, PERSISTENT_ENV_START, PERSISTENT_ENV_END)
        persistent_environment_blocks[relative] = _norm(block)
        if block is None:
            violations.append(
                f"{relative}: direct User/Machine environment access is not "
                "test-virtualized"
            )
            continue
        if not uses_adapter:
            violations.append(
                f"{relative}: test-persistent-environment adapter is present "
                "but no persistent access routes through it"
            )
        for problem in direct_access:
            violations.append(
                f"{relative}: {problem} outside the shared "
                "test-persistent-environment adapter"
            )

    # Repo-wide backstop: the loop above enforces the full installer contract
    # (adapter present + wired) only for `plugins/**/*.ps1`. A test/helper
    # script living anywhere else in the repo (e.g. under a plugin's `tests/`,
    # a shared `libs/*/tests/`, or a future integration harness) never had to
    # carry the adapter at all, so it was free to call the real
    # `[Environment]::SetEnvironmentVariable(..., 'User'|'Machine')` (or the
    # registry paths/APIs) directly and leak into the operator's real,
    # persistent Windows User PATH -- exactly the leak this repo hit live. This
    # pass only checks the same direct-access detector against every other
    # tracked `.ps1` in the repo; it does not require the adapter marker or
    # `PLUGINS_DIR`'s stricter identical-block rules, which are installer-only.
    for path in sorted(REPO.rglob("*.ps1")):
        if PLUGINS_DIR in path.parents or is_ignored_scan_path(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for problem in persistent_environment_violations(text):
            relative = path.relative_to(REPO).as_posix()
            violations.append(
                f"{relative}: {problem} (persistent User/Machine environment "
                "write outside the shared test-persistent-environment adapter "
                "-- route through Set-CopilotPersistentEnvironmentVariable, "
                "or use an explicit 'Process' target in a test)"
            )

    for plugin in plugins:
        name = plugin.name
        # Payload-runtime plugins ship a non-Python runtime (a JS extension
        # copied to ~/.copilot/extensions/, etc.) and carry no pyproject.toml.
        # The venv / uv-pip-install / SAC-launcher rules do not apply to them;
        # they must still write a schema_version 3 manifest with a source block
        # and carry the shared source-kind resolver. See docs/install-contract.md
        # § "Payload runtime (non-Python)".
        is_payload = not (plugin / "pyproject.toml").exists()
        # Immutable-versioned-runtime invariant (#581): every Python runtime plugin
        # (pyproject + an installer) MUST ship the byte-identical
        # versioned_runtime.py primitive. Payload (non-Python) runtimes are exempt
        # -- they build no venv.
        if not is_payload:
            vrt = plugin / "scripts" / VERSIONED_RUNTIME_FILE
            if not vrt.exists():
                violations.append(
                    f"{name}: missing scripts/{VERSIONED_RUNTIME_FILE} -- every Python "
                    f"runtime must adopt the immutable-versioned venv layout (#581)"
                )
            else:
                vrt_hashes[name] = hashlib.sha256(vrt.read_bytes()).hexdigest()
            for component in VERSIONED_RUNTIME_COMPONENTS:
                canonical = VERSIONED_RUNTIME_CANONICAL.with_name(component)
                supplied = plugin / "scripts" / component
                if (not canonical.is_file() or canonical.is_symlink()
                        or not supplied.is_file() or supplied.is_symlink()
                        or supplied.read_bytes() != canonical.read_bytes()):
                    violations.append(
                        f"{name}: missing or drifted scripts/{component} "
                        "-- run 'python tools/sync-versioned-runtime.py'"
                    )
        # Session-start reconcile invariant (dotfiles#779): a Python runtime plugin
        # must self-reconcile its binstub at launch. Payload runtimes are exempt
        # (no binstub); the baseline set tracks pre-existing gaps.
        if not is_payload and name not in EXEMPT_SESSION_HOOK:
            hook_problem = _session_hook_problem(plugin)
            if hook_problem:
                violations.append(f"{name}: {hook_problem}")
        # Enforce the canonical entrypoint pair (install.* if present, else
        # init.*). Both language variants of that base must exist and conform.
        base = _entrypoint_base(plugin)
        for ext in ("ps1", "sh"):
            script = f"{base}.{ext}"
            path = plugin / "scripts" / script
            if not path.exists():
                violations.append(f"{name}: missing scripts/{script}")
                continue
            text = path.read_text(encoding="utf-8", errors="replace")

            incomplete_policy_calls = _incomplete_dependency_policy_calls(text)
            if incomplete_policy_calls:
                violations.append(
                    f"{name}/{script}: {incomplete_policy_calls} dependency policy "
                    "call(s) missing the explicit UV provider or argument separator"
                )
            uses_engine = _uses_installer_engine(text, ext)
            if (
                not is_payload
                and "uv pip install" not in text
                and not (uses_engine and _uses_engine_uv_install(text, ext))
                and not _uses_dependency_policy_install(text, ext)
            ):
                violations.append(f"{name}/{script}: no 'uv pip install' (package must not be file-copied)")
            if not is_payload and VERSIONED_MARKER not in text:
                violations.append(
                    f"{name}/{script}: missing the '{VERSIONED_MARKER}' block -- the "
                    f"installer must wire the immutable-versioned venv layout (#581)"
                )
            # Thread-A fast-install + deferred self-provision (#1393): the
            # entrypoint must declare both a `stamp` and a `provision` action.
            # ps1-lane exemptions (not yet ported) are tracked in
            # BASELINE_NO_STAMP_PS1; the POSIX .sh lane is fully ported (no
            # exemptions). Payload runtimes build no venv and are exempt.
            if not is_payload:
                exempt = ext == "ps1" and name in BASELINE_NO_STAMP_PS1
                if not exempt and not _declares_stamp_provision(text, ext):
                    violations.append(
                        f"{name}/{script}: missing a 'stamp' and/or 'provision' action "
                        "-- Thread-A requires a fast 'stamp' (self-provisioning binstub, "
                        "no inline venv) + a deferred 'provision' (#1393)"
                    )
            if FORBIDDEN_PYTHONPATH.search(text):
                violations.append(f"{name}/{script}: binstub sets PYTHONPATH to a runtime lib/ dir")
            if (
                "schema_version" not in text
                or ('"source"' not in text and "source " not in text)
            ) and not (uses_engine and _uses_engine_manifest_writer(text, ext)):
                violations.append(f"{name}/{script}: no schema_version 3 manifest with a source block")
            elif not re.search(r"schema_version[\"'=:\s]+3", text) and not (
                uses_engine and _uses_engine_manifest_writer(text, ext)
            ):
                violations.append(f"{name}/{script}: manifest is not schema_version 3")

            if ext == "ps1":
                if FORBIDDEN_TRAMPOLINE.search(text):
                    violations.append(
                        f"{name}/{script}: launches the unsigned console-script .exe "
                        "trampoline (Smart App Control blocks it -- CodeIntegrity 3077); "
                        "launch '<venv>\\Scripts\\python.exe -m <pkg>' instead"
                    )
                ps1_resolvers[name] = _norm(_extract_block(text, "function Get-SourceKind", "{", "}"))
            else:
                sh_resolvers[name] = _norm(_extract_block(text, "_source_kind()", "{", "}"))

            # install-contract:v5 self-stage + smoke seam (#935). Byte-identical
            # per language across every Python-runtime plugin, so a concurrent
            # `copilot plugin update` never fights a wedged installer for the
            # singleton payload (self-stage) and a stalled install self-terminates
            # (watchdog). Payload (non-Python) runtimes are exempt.
            if not is_payload:
                v5_selfstage[ext][name] = _extract_marker(
                    text, "# === install-contract:v5 self-stage", "# === end install-contract:v5 self-stage ==="
                )
                v5_smoke[ext][name] = _extract_marker(
                    text, "# === install-contract:v5 smoke seam", "# === end install-contract:v5 smoke seam ==="
                )

    _check_identical("Get-SourceKind (ps1)", ps1_resolvers, violations)
    _check_identical("_source_kind (sh)", sh_resolvers, violations)
    for ext in ("ps1", "sh"):
        _check_identical(f"install-contract:v5 self-stage ({ext})", v5_selfstage[ext], violations)
        _check_identical(f"install-contract:v5 smoke seam ({ext})", v5_smoke[ext], violations)
    _check_identical(
        "test-persistent-environment (ps1)",
        persistent_environment_blocks,
        violations,
    )

    # The versioned_runtime.py primitive is a self-contained per-plugin copy
    # vendored byte-identically from the canonical source
    # (libs/versioned-runtime/versioned_runtime.py) by
    # tools/sync-versioned-runtime.py -- it cannot be a shared runtime import
    # because plugins are pulled independently from the marketplace. Enforce that
    # every copy matches the canonical (drift => run the sync script).
    if not VERSIONED_RUNTIME_CANONICAL.exists():
        violations.append(
            f"missing canonical {VERSIONED_RUNTIME_CANONICAL.relative_to(REPO).as_posix()} "
            f"-- the shared source of {VERSIONED_RUNTIME_FILE}"
        )
    elif vrt_hashes:
        canonical_hash = hashlib.sha256(VERSIONED_RUNTIME_CANONICAL.read_bytes()).hexdigest()
        drifted = sorted(k for k, v in vrt_hashes.items() if v != canonical_hash)
        if drifted:
            violations.append(
                f"scripts/{VERSIONED_RUNTIME_FILE} differs from the canonical source "
                f"({VERSIONED_RUNTIME_CANONICAL.relative_to(REPO).as_posix()}) in: "
                f"{', '.join(drifted)} -- run 'python tools/sync-versioned-runtime.py'"
            )
        spec = importlib.util.spec_from_file_location("install_contract_runtime", VERSIONED_RUNTIME_CANONICAL)
        assert spec is not None and spec.loader is not None
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        try:
            _check_runtime_retention(runtime)
        except (OSError, RuntimeError, AssertionError, AttributeError, ValueError) as exc:
            violations.append(f"canonical runtime retention contract failed: {exc}")

    installer_engine_drift = _verify_installer_engine_sync()
    if installer_engine_drift:
        violations.append(
            "vendored installer-engine files are out of sync -- run "
            "'python tools/sync-installer-engine.py'"
        )
        violations.extend(f"installer-engine: {problem}" for problem in installer_engine_drift)

    if violations:
        print("Install-contract violations:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print("\nSee docs/install-contract.md.", file=sys.stderr)
        return 1
    print(f"Install contract OK ({len(plugins)} plugins).")
    return 0


def _check_identical(label: str, resolvers: dict[str, str | None], violations: list[str]) -> None:
    present = {k: v for k, v in resolvers.items() if v}
    missing = [k for k, v in resolvers.items() if not v]
    for k in missing:
        violations.append(f"{k}: missing {label} block")
    distinct = set(present.values())
    if len(distinct) > 1:
        violations.append(f"{label} block differs across plugins: {sorted(present)}")


if __name__ == "__main__":
    raise SystemExit(check())
