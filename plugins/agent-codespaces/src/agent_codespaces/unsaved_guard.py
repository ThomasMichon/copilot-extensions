"""Second opinion on GitHub's "codespace has unsaved changes" delete refusal.

``gh codespace delete`` (without ``--force``) refuses non-interactively when
GitHub's *cached* git status for the codespace reports uncommitted/unpushed
work. That flag is frequently stale, so a box that is actually clean cannot be
deleted without ``--force`` -- which skips any real check.

When (and only when) gh refuses for that reason, this module audits **every**
git checkout on the box over SSH -- every repo found by a full traversal of
``/workspaces`` (``node_modules`` skipped), the dotfiles checkout, and every linked ``git worktree`` those
repos know about -- for uncommitted changes, commits on no remote, and local
branches carrying unpushed commits. All clean -> the caller retries with gh's
``--force`` for that specific refusal. Anything dirty, or an audit that cannot
complete, raises :class:`UnsavedWorkError` naming the exact checkouts.
"""

from __future__ import annotations

import asyncio
import re
import shlex
import threading
from dataclasses import dataclass, field

from remote_login_shell import wrap_login_shell

from . import config

# gh's exact non-interactive refusal (pkg/cmd/codespace/delete.go confirmDeletion):
#   unable to confirm: codespace <name> has unsaved changes (use --force to override)
# Matched as the whole (last non-empty) stderr line, so an incidental mention
# inside some other failure never triggers the audit-then-force path.
_UNSAVED_RE = re.compile(
    r"^(?:(?:error|x)\s*:?\s*)?unable to confirm: codespace (\S+) has unsaved changes "
    r"\(use --force to override\)\s*$",
    re.IGNORECASE,
)
_MARK_DONE = "CHECKOUT_AUDIT=1"
_SAMPLE_LINES = 5


def is_unsaved_changes_refusal(stderr: str | None, name: str | None = None) -> bool:
    """True when gh refused the delete only because of the unsaved-changes flag.

    The refusal must be gh's exact message and the only (non-empty) stderr
    line; with ``name`` it must also name that codespace.
    """
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    if len(lines) != 1:
        return False
    m = _UNSAVED_RE.match(lines[0])
    return bool(m) and (name is None or m.group(1) == name)


@dataclass
class CheckoutState:
    """Git safety signals for one checkout on the box."""

    path: str
    dirty: bool = False
    ahead: int = 0
    unpushed_branches: int = 0
    error: bool = False
    sample: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.error or self.dirty or self.ahead > 0 or self.unpushed_branches > 0)

    def describe(self) -> str:
        if self.error:
            return f"{self.path}: could not read git status"
        parts = []
        if self.dirty:
            parts.append("uncommitted changes")
        if self.ahead > 0:
            parts.append(f"{self.ahead} commit(s) on no remote")
        if self.unpushed_branches > 0:
            parts.append(f"{self.unpushed_branches} branch(es) with unpushed commits")
        text = f"{self.path}: " + ", ".join(parts)
        if self.sample:
            text += "".join(f"\n      {line}" for line in self.sample)
        return text


@dataclass
class CheckoutAudit:
    """Result of auditing every checkout on a box."""

    known: bool
    checkouts: list[CheckoutState] = field(default_factory=list)
    error: str = ""

    @property
    def dirty_checkouts(self) -> list[CheckoutState]:
        return [c for c in self.checkouts if not c.clean]

    @property
    def all_clean(self) -> bool:
        return self.known and bool(self.checkouts) and not self.dirty_checkouts


class UnsavedWorkError(RuntimeError):
    """The box really holds unsaved work (or could not be audited)."""

    def __init__(self, name: str, audit: CheckoutAudit) -> None:
        self.name = name
        self.audit = audit
        if not audit.known:
            detail = (f"could not audit its checkouts ({audit.error or 'no result'}); "
                      "refusing to override")
        elif not audit.checkouts:
            detail = "no git checkout was found to audit; refusing to override"
        else:
            detail = "these checkouts hold unsaved work:\n" + "\n".join(
                f"    - {c.describe()}" for c in audit.dirty_checkouts)
        super().__init__(
            f"GitHub reports CodeSpace '{name}' has unsaved changes and {detail}\n"
            "  Push/commit or discard that work, or re-run with --force to delete anyway.")


# Bash audit script (see audit_command). printf, never echo -e, so backslashes
# in paths/porcelain lines are emitted verbatim; tab/newline paths are refused.
_AUDIT_SCRIPT = r"""
shopt -s nullglob dotglob 2>/dev/null
declare -A seen common
list=()
wl=$(mktemp) && cf=$(mktemp) && fe=$(mktemp) || exit 1
trap 'rm -f "$wl" "$cf" "$fe"' EXIT
err() { printf 'CHECKOUT_ERR\t%s\n' "$1"; }
add() {
  case "$1" in *$'\n'*|*$'\t'*|*$'\r'*) err unsafe-path; return 0;; esac
  # A listed checkout that is missing or inaccessible cannot be audited:
  # fail closed (a stale entry blocks until `git worktree prune`).
  [ -d "$1" ] && [ -r "$1" ] && [ -x "$1" ] || { err "$1"; return 0; }
  [ -n "${seen[$1]}" ] && return 0
  seen[$1]=1; list+=("$1")
}
# Full traversal (dot-directories included; node_modules skipped as dependency
# trees). Any traversal error (e.g. an unreadable directory that could hide a
# checkout) fails closed.
if [ -d @ROOT@ ]; then
  if ! find @ROOT@ -name node_modules -type d -prune -o -name .git -print0 -prune \
      >"$cf" 2>"$fe"; then
    err "workspace traversal incomplete"
    head -n 5 "$fe" | tr '\t' ' ' | while IFS= read -r l; do err "traversal: $l"; done
  fi
fi
cands=()
while IFS= read -r -d '' g; do cands+=("$g"); done <"$cf"
for g in "${cands[@]}" @EXTRAS@; do
  [ -e "$g" ] || continue
  top=$(git -C "$(dirname "$g")" rev-parse --show-toplevel 2>/dev/null) \
    || { err "$(dirname "$g")"; continue; }
  # One checked -z listing (raw, unquoted paths), parsed from the same output;
  # an enumeration failure fails closed and $top is then never reported clean.
  git -C "$top" worktree list --porcelain -z >"$wl" 2>/dev/null || { err "$top"; continue; }
  add "$top"
  while IFS= read -r -d '' rec; do
    case "$rec" in "worktree "*) add "${rec#worktree }";; esac
  done <"$wl"
done
for p in "${list[@]}"; do
  # Explicit flags so repo config (status.showUntrackedFiles, submodule
  # ignore) cannot hide work from the audit.
  st=$(git -C "$p" status --porcelain --untracked-files=all --ignore-submodules=none \
    2>/dev/null) || { err "$p"; continue; }
  d=0; [ -n "$st" ] && d=1
  a=0
  if git -C "$p" rev-parse -q --verify HEAD >/dev/null 2>&1; then
    a=$(git -C "$p" rev-list --count HEAD --not --remotes 2>/dev/null) \
      || { err "$p"; continue; }
  fi
  cd_=$(cd "$p" && cd "$(git rev-parse --git-common-dir)" 2>/dev/null && pwd -P)
  cd_=${cd_:-$p}; nb=0; berr=0
  if [ -z "${common[$cd_]}" ]; then
    common[$cd_]=1
    refs=$(git -C "$p" for-each-ref --format='%(refname)' refs/heads 2>/dev/null) \
      || berr=1
    for b in $refs; do
      c=$(git -C "$p" rev-list --count "$b" --not --remotes 2>/dev/null) \
        || { berr=1; break; }
      [ "${c:-0}" -gt 0 ] && nb=$((nb+1))
    done
  fi
  [ "$berr" = 1 ] && { err "$p"; continue; }
  printf 'CHECKOUT\t%s\t%s\t%s\t%s\n' "$d" "${a:-0}" "$nb" "$p"
  if [ "$d" = 1 ]; then
    printf '%s\n' "$st" | head -n @SAMPLES@ | while IFS= read -r l; do
      printf 'SAMPLE\t%s\t%s\n' "$p" "$l"
    done
  fi
done
echo @DONE@
"""


def audit_command(
    *,
    workspace_root: str = "/workspaces",
    extra_dirs: tuple[str, ...] = (config.DOTFILES_DIR,),
) -> str:
    """Bash (run inside the box) emitting one ``CHECKOUT`` line per checkout.

    Line format (tab-separated): ``CHECKOUT dirty ahead unpushed_branches path``,
    ``CHECKOUT_ERR path``, or ``SAMPLE path porcelain-line``; ends with
    ``CHECKOUT_AUDIT=1`` once the scan completed. Read-only.
    """
    extras = " ".join(shlex.quote(f"{d}/.git") for d in extra_dirs)
    inner = (
        _AUDIT_SCRIPT.replace("@ROOT@", shlex.quote(workspace_root))
        .replace("@EXTRAS@", extras)
        .replace("@SAMPLES@", str(_SAMPLE_LINES))
        .replace("@DONE@", _MARK_DONE)
    )
    return wrap_login_shell(inner)


def _int(value: str) -> int:
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return 0


def parse_audit(output: str | None) -> CheckoutAudit:
    """Parse :func:`audit_command` output. Never raises; incomplete -> unknown.

    Records are split only on the protocol's ``\\n`` delimiter (never
    ``str.splitlines``, which also splits on ``\\r`` and other characters a
    path may contain), and merging is conservative: a later record never
    clears an earlier error or dirty state for the same checkout.
    """
    lines = (output or "").split("\n")
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty or nonempty[-1].strip() != _MARK_DONE:
        return CheckoutAudit(known=False, error="audit did not complete")
    checkouts: dict[str, CheckoutState] = {}
    for line in lines:
        parts = line.split("\t")
        if parts[0] == "CHECKOUT" and len(parts) >= 5:
            path = "\t".join(parts[4:])
            new = CheckoutState(
                path=path, dirty=parts[1].strip() != "0", ahead=_int(parts[2]),
                unpushed_branches=_int(parts[3]),
            )
            existing = checkouts.get(path)
            if existing is None or existing.clean:
                checkouts[path] = new
        elif parts[0] == "CHECKOUT_ERR" and len(parts) >= 2:
            path = "\t".join(parts[1:])
            checkouts.setdefault(path, CheckoutState(path=path)).error = True
        elif parts[0] == "SAMPLE" and len(parts) >= 3 and parts[1] in checkouts:
            checkouts[parts[1]].sample.append("\t".join(parts[2:]))
    return CheckoutAudit(known=True, checkouts=list(checkouts.values()))


async def _probe(
    name: str, account: str | None, timeout: float, token: str | None = None,
) -> CheckoutAudit:
    from ssh_manager import ConnectionManager

    from ._ssh_retry import exec_with_retry
    from .codespace_config import CodespaceSource

    manager = ConnectionManager()
    try:
        source = CodespaceSource(name, account=account, token=token)
        await manager.ensure_connected(name, source, [])
        result = await exec_with_retry(manager, name, audit_command(), timeout=timeout)
    finally:
        await manager.disconnect(name)
    if getattr(result, "exit_code", 1) != 0:
        return CheckoutAudit(known=False, error=f"audit exited {result.exit_code}")
    return parse_audit(result.stdout)


def audit_checkouts(
    name: str, account: str | None = None, timeout: float = 90.0, *, token: str | None = None,
) -> CheckoutAudit:
    """Audit every checkout on ``name`` over SSH. Never raises (unknown on error).

    Runs on a private thread/event loop so it is safe to call from sync code
    that may itself be running under an event loop.
    """
    box: dict[str, CheckoutAudit] = {}

    def _run() -> None:
        try:
            box["audit"] = asyncio.run(_probe(name, account, timeout, token))
        except Exception as exc:  # noqa: BLE001 -- degrade to "unknown"
            box["audit"] = CheckoutAudit(known=False, error=str(exc) or type(exc).__name__)

    worker = threading.Thread(target=_run, name=f"audit-{name}", daemon=True)
    worker.start()
    worker.join(timeout + 120)
    return box.get("audit") or CheckoutAudit(known=False, error="audit timed out")


def force_args_if_checkouts_clean(
    name: str, args: list[str], stderr: str | None, *,
    account: str | None = None, token: str | None = None,
) -> list[str] | None:
    """Decide how to proceed after a failed non-forced ``gh codespace delete``.

    Returns ``None`` when the refusal was NOT the unsaved-changes flag (the
    caller reports the original error). Returns ``args + ["--force"]`` when every
    checkout on the box is verified clean. Raises :class:`UnsavedWorkError`
    listing the dirty checkouts (or the audit failure) otherwise. ``token``
    (the caller's exact pre-minted token) is reused for the SSH audit.
    """
    if not is_unsaved_changes_refusal(stderr, name):
        return None
    if account is None and token is None:
        from .lifecycle import account_for_codespace

        account = account_for_codespace(name)
    audit = audit_checkouts(name, account, token=token)
    if not audit.all_clean:
        raise UnsavedWorkError(name, audit)
    return [*args, "--force"]
