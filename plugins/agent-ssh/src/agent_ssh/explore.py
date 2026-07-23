"""agent-ssh :: core :: live machine introspection (``explore``).

Invoked locally against a **reachable** SSH target, ``explore`` shells in over
the provisioned transport and reports, *by convention*, what the machine offers
the agent fabric:

- its checked-out repos and **where** they live -- read from the machine's own
  per-machine repo registry (``agent-worktrees repos list --json``), the source
  of truth for its locations -- which of those **back an agent**, and each repo's
  declared **purpose** (``role`` + ``summary``) read from the in-repo
  ``.agent-worktrees/related.yaml`` catalog(s) checked out on the machine;
- whether the fabric's worktree / coordination / dispatch runtimes are installed
  (``agent-worktrees`` / ``agent-bridge`` / ``agent-dispatch`` binstubs + version);
- the **derived agents** that fall out of "the machine is reachable AND it has an
  agent-backing repo checked out at path P" -- addressable as ``<repo>@<target>``.

It is **read-only**: it runs one SSH probe and prints a report (or ``--json``).
Persisting a finding into a registry is a separate, explicit step (``--adopt``,
a follow-on capability) -- exploration itself never mutates local or remote state.
Locations are read **live** from the machine at query time; nothing is cached
here (derive-don't-duplicate).

The probe is a single POSIX ``sh`` script streamed to the target on stdin, so it
needs no remote deployment and survives a minimal ``$PATH`` (it resolves the
fabric binstubs from ``$HOME/.local/bin`` when they are not already on ``PATH``).
It emits delimited sections this module parses back into a structured result.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

# Fabric runtimes we probe for on the target. Ordered for stable reporting.
FABRIC_TOOLS = ("agent-worktrees", "agent-bridge", "agent-dispatch")

_MARK = "===AGENT_SSH_PROBE"

# POSIX-sh probe. Machine-facing output -> ASCII only. Resolves each binstub via
# PATH, then a $HOME/.local/bin | $HOME/bin fallback (a non-login SSH command may
# not inherit the login PATH that puts the binstubs on it). Emits delimited
# sections; see parse_probe().
PROBE_SCRIPT = r"""
_find_tool() {
  if command -v "$1" >/dev/null 2>&1; then command -v "$1"; return 0; fi
  for _d in "$HOME/.local/bin" "$HOME/bin"; do
    if [ -x "$_d/$1" ]; then printf '%s\n' "$_d/$1"; return 0; fi
  done
  return 1
}
printf '%s:os===\n' "===AGENT_SSH_PROBE"
uname -a 2>/dev/null || echo unknown
printf '%s:tools===\n' "===AGENT_SSH_PROBE"
for _t in agent-worktrees agent-bridge agent-dispatch; do
  _p=`_find_tool "$_t" 2>/dev/null || true`
  if [ -n "$_p" ]; then
    _v=`"$_p" --version 2>/dev/null | head -n 1 || true`
    printf '%s\t%s\t%s\n' "$_t" "$_p" "$_v"
  else
    printf '%s\t\t\n' "$_t"
  fi
done
printf '%s:repos===\n' "===AGENT_SSH_PROBE"
_awt=`_find_tool agent-worktrees 2>/dev/null || true`
_repos_json='{}'
if [ -n "$_awt" ]; then
  _repos_json=`"$_awt" repos list --json 2>/dev/null || echo '{}'`
fi
printf '%s\n' "$_repos_json"
printf '%s:related===\n' "===AGENT_SSH_PROBE"
printf '%s\n' "$_repos_json" \
  | grep -oE '"(windows|wsl|linux)"[[:space:]]*:[[:space:]]*"[^"]+"' 2>/dev/null \
  | sed -E 's/.*:[[:space:]]*"([^"]*)"$/\1/' \
  | while IFS= read -r _p; do
      [ -n "$_p" ] || continue
      _rf="$_p/.agent-worktrees/related.yaml"
      if [ -f "$_rf" ]; then
        printf '>>>RELFILE:%s\n' "$_p"
        cat "$_rf" 2>/dev/null || true
        printf '\n>>>RELEND\n'
      fi
    done
printf '%s:end===\n' "===AGENT_SSH_PROBE"
"""


@dataclass
class RuntimeInfo:
    """Whether a fabric runtime is installed on the target, and its version."""

    name: str
    installed: bool = False
    path: str = ""
    version: str = ""


@dataclass
class DerivedAgent:
    """An addressable agent that falls out of reachability x an agent-backing
    repo checkout on the target."""

    name: str          # <repo>@<target>
    repo: str
    repo_class: str
    path: str          # the checkout path on the target (its platform)
    role: str = ""     # repo purpose (role), from an in-repo related.yaml catalog
    summary: str = ""  # repo purpose (summary), from an in-repo related.yaml catalog


@dataclass
class ExploreResult:
    """Structured result of introspecting one SSH target."""

    target: str
    reachable: bool = False
    error: str = ""
    os: str = ""
    runtimes: list[RuntimeInfo] = field(default_factory=list)
    repos: list[dict] = field(default_factory=list)
    derived_agents: list[DerivedAgent] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _ProbeProc:
    """Minimal decoded result of the SSH probe (returncode + text streams)."""

    returncode: int
    stdout: str
    stderr: str


def _ssh_probe(target: str, timeout: int) -> _ProbeProc:
    """Run the probe on *target* over SSH, streaming the script on stdin.

    ``sh -s`` reads the script from stdin, avoiding any remote-quoting of the
    (multi-line) probe. BatchMode keeps it non-interactive; a failed connection
    returns a non-zero rc rather than prompting.

    The script is sent as **LF-terminated bytes** (``text=False``): a text-mode
    stdin pipe on Windows would rewrite ``\\n`` to ``\\r\\n``, and the stray
    ``\\r`` breaks a POSIX ``sh``/``dash`` on the far side (``for ...; do\\r`` ->
    "word unexpected (expecting do)").
    """
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.run(
        [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={timeout}",
            "-o", "StrictHostKeyChecking=accept-new",
            target,
            "sh", "-s",
        ],
        input=PROBE_SCRIPT.replace("\r\n", "\n").encode("utf-8"),
        capture_output=True,
        creationflags=creationflags,
        check=False,
    )
    return _ProbeProc(
        returncode=proc.returncode,
        stdout=proc.stdout.decode("utf-8", "replace"),
        stderr=proc.stderr.decode("utf-8", "replace"),
    )


def _section(raw: str, name: str) -> str:
    """Return the body of a ``===AGENT_SSH_PROBE:<name>===`` section."""
    start = f"{_MARK}:{name}==="
    lines = raw.splitlines()
    out: list[str] = []
    capture = False
    for line in lines:
        if line.strip() == start:
            capture = True
            continue
        if capture and line.startswith(f"{_MARK}:") and line.strip().endswith("==="):
            break
        if capture:
            out.append(line)
    return "\n".join(out).strip()


def parse_related(raw: str) -> dict[str, dict[str, str]]:
    """Parse the ``related`` probe section into a ``name -> {role, summary}`` map.

    The section carries the concatenated ``.agent-worktrees/related.yaml`` files
    found in the machine's checked-out repos, each framed by ``>>>RELFILE:<path>``
    / ``>>>RELEND``. ``related.yaml`` is directional (each repo's POV of the
    OTHERS): its ``related:`` entries carry a per-repo ``role`` + ``summary`` --
    the declared **purpose**. We union all catalogs into one map keyed by the
    global-registry repo name; the first non-empty purpose for a name wins.

    Fail-safe: any unparseable block is skipped and yields no purposes.
    """
    purposes: dict[str, dict[str, str]] = {}
    if not raw.strip():
        return purposes
    try:
        import yaml
    except ImportError:  # pragma: no cover - pyyaml is a declared dependency
        return purposes

    blocks: list[str] = []
    current: list[str] | None = None
    for line in raw.splitlines():
        if line.startswith(">>>RELFILE:"):
            current = []
            continue
        if line.strip() == ">>>RELEND":
            if current is not None:
                blocks.append("\n".join(current))
            current = None
            continue
        if current is not None:
            current.append(line)

    for body in blocks:
        try:
            doc = yaml.safe_load(body) or {}
        except Exception:  # noqa: S112 -- a malformed remote related.yaml is skipped, not fatal
            continue
        if not isinstance(doc, dict):
            continue
        related = doc.get("related")
        if not isinstance(related, dict):
            continue
        for name, entry in related.items():
            if not isinstance(entry, dict):
                continue
            key = str(name).strip()
            if not key:
                continue
            role = str(entry.get("role", "") or "").strip()
            summary = str(entry.get("summary", "") or "").strip()
            if not role and not summary:
                continue
            existing = purposes.get(key)
            if existing and (existing.get("role") or existing.get("summary")):
                continue  # first non-empty purpose wins
            purposes[key] = {"role": role, "summary": summary}
    return purposes


def parse_probe(raw: str) -> dict:
    """Parse the delimited probe output into {os, runtimes, repos, purposes}."""
    os_line = _section(raw, "os") or "unknown"

    runtimes: list[RuntimeInfo] = []
    for line in _section(raw, "tools").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        name = parts[0].strip() if parts else ""
        path = parts[1].strip() if len(parts) > 1 else ""
        version = parts[2].strip() if len(parts) > 2 else ""
        if name:
            runtimes.append(
                RuntimeInfo(name=name, installed=bool(path), path=path, version=version)
            )

    repos: list[dict] = []
    repos_raw = _section(raw, "repos")
    if repos_raw:
        try:
            doc = json.loads(repos_raw)
            if isinstance(doc, dict):
                repos = list(doc.get("repos", []))
        except (ValueError, TypeError):
            repos = []

    purposes = parse_related(_section(raw, "related"))

    return {"os": os_line, "runtimes": runtimes, "repos": repos, "purposes": purposes}


def derive_agents(
    target: str,
    repos: list[dict],
    purposes: dict[str, dict[str, str]] | None = None,
) -> list[DerivedAgent]:
    """Derive the addressable agents on *target*: its agent-backing repos.

    A machine's own checked-out, ``agent: true`` repos ARE the agents reachable
    on it -- ``<repo>@<target>``. This is the roster falling out of two facts
    already true (the machine is reachable; it has repo X checked out that backs
    an agent), read live from the machine's own registry. Each agent carries its
    declared **purpose** (``role`` + ``summary``) when the repo appears in an
    in-repo ``related.yaml`` catalog on the machine (*purposes*).
    """
    purposes = purposes or {}
    agents: list[DerivedAgent] = []
    for entry in repos:
        if not isinstance(entry, dict) or not entry.get("agent"):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        paths = entry.get("paths") or {}
        # Show any known checkout path; the machine's platform key wins, but we
        # don't presume which platform the alias lands on -- report the first.
        path = ""
        if isinstance(paths, dict) and paths:
            path = str(next(iter(paths.values())))
        purpose = purposes.get(name) or {}
        agents.append(
            DerivedAgent(
                name=f"{name}@{target}",
                repo=name,
                repo_class=str(entry.get("class", "")),
                path=path,
                role=str(purpose.get("role", "")),
                summary=str(purpose.get("summary", "")),
            )
        )
    return agents


def explore(target: str, timeout: int = 10) -> ExploreResult:
    """Introspect one reachable SSH target (read-only)."""
    result = ExploreResult(target=target)
    try:
        proc = _ssh_probe(target, timeout)
    except FileNotFoundError:
        result.error = "ssh not found on PATH"
        return result
    if proc.returncode != 0:
        result.reachable = False
        err = (proc.stderr or "").strip().splitlines()
        result.error = err[-1] if err else f"ssh exited {proc.returncode}"
        return result

    result.reachable = True
    parsed = parse_probe(proc.stdout)
    result.os = parsed["os"]
    result.runtimes = parsed["runtimes"]
    result.repos = parsed["repos"]
    purposes = parsed.get("purposes") or {}
    # Annotate each repo with its declared purpose (role + summary), when the
    # machine's in-repo related.yaml catalog(s) describe it.
    for entry in result.repos:
        if isinstance(entry, dict):
            p = purposes.get(str(entry.get("name", "")).strip())
            if p:
                entry["purpose"] = p
    result.derived_agents = derive_agents(target, result.repos, purposes)
    return result


def format_report(result: ExploreResult) -> str:
    """Human-readable report for one target."""
    lines: list[str] = []
    lines.append(f"agent-ssh explore: {result.target}")
    if not result.reachable:
        lines.append(f"  [FAIL] unreachable: {result.error}")
        return "\n".join(lines)

    lines.append("  reachable: yes")
    lines.append(f"  os: {result.os}")

    lines.append("  fabric runtimes:")
    for rt in result.runtimes:
        if rt.installed:
            ver = f" {rt.version}" if rt.version else ""
            lines.append(f"    [OK]   {rt.name}{ver}  ({rt.path})")
        else:
            lines.append(f"    [--]   {rt.name}  (not installed)")

    lines.append(f"  repos ({len(result.repos)}):")
    for entry in result.repos:
        name = entry.get("name", "?")
        cls = entry.get("class", "?")
        agent = "agent" if entry.get("agent") else "no-agent"
        paths = entry.get("paths") or {}
        path = next(iter(paths.values()), "") if isinstance(paths, dict) else ""
        lines.append(f"    - {name} [{cls}, {agent}]  {path}")
        purpose = entry.get("purpose") or {}
        if isinstance(purpose, dict) and (purpose.get("role") or purpose.get("summary")):
            role = purpose.get("role", "")
            summary = purpose.get("summary", "")
            tag = f"[{role}] " if role else ""
            lines.append(f"        purpose: {tag}{summary}".rstrip())

    lines.append(f"  derived agents ({len(result.derived_agents)}):")
    for ag in result.derived_agents:
        lines.append(f"    - {ag.name}  ({ag.repo_class})  {ag.path}")
        if ag.role or ag.summary:
            tag = f"[{ag.role}] " if ag.role else ""
            lines.append(f"        purpose: {tag}{ag.summary}".rstrip())

    return "\n".join(lines)
