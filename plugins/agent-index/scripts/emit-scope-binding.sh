#!/usr/bin/env bash
# emit-scope-binding -- agent-index sessionStart hook (POSIX parity).
#
# Emits a succinct "what agent-index covers + how to search it" guidance fragment
# as {"additionalContext": "..."}, so EVERY agent learns, at session start, that a
# semantic index is available and how to query it -- by calling the `agent-index`
# CLI DIRECTLY. agent-index is a uniform retrieval capability every agent may use,
# so it is deliberately NOT wrapped in a sub-agent or an MCP tool; the
# how-to-search instructions ride in this hook's additionalContext instead.
# Generic emitter (ships in the plugin); it reads the LOCAL scope config --
# <repo>/.agent-index/config.yaml corpus.sources -- so the operator's scope values
# stay in the repo, not baked into the plugin.
#
# cwd-gated: emits {} outside a repo that declares corpus.sources. The config
# parse + JSON encode is delegated to python3 (present wherever agent-index runs);
# with no python3, or no config, it emits {} so nothing leaks into unrelated repos.
set -eu

emit_empty() { printf '%s\n' '{}'; exit 0; }

root="$(git rev-parse --show-toplevel 2>/dev/null | head -n1 || true)"
[ -n "${root:-}" ] || emit_empty
cfg="$root/.agent-index/config.yaml"
[ -f "$cfg" ] || emit_empty
command -v python3 >/dev/null 2>&1 || emit_empty

CFG="$cfg" python3 <<'PY'
import json, os, re, sys

cfg = os.environ["CFG"]
try:
    lines = open(cfg, encoding="utf-8").read().splitlines()
except OSError:
    print("{}"); sys.exit(0)

# Dependency-light scan of the corpus.sources list (no YAML module): within the
# top-level `corpus:` block, each `- name:` item + its following `repo:` /
# `trust_domain:`.
in_corpus = False
sources = []
cur = None
for raw in lines:
    line = re.sub(r"\s+#.*$", "", raw)
    if re.match(r"^\s*#", line):
        continue
    if re.match(r"^\S.*:", line):
        in_corpus = bool(re.match(r"^\s*corpus\s*:", line))
        continue
    if not in_corpus:
        continue
    m = re.match(r"^\s*-\s*name\s*:\s*['\"]?([^'\"]+?)['\"]?\s*$", line)
    if m:
        if cur:
            sources.append(cur)
        cur = {"name": m.group(1).strip(), "repo": "", "trust": ""}
        continue
    if cur:
        mr = re.match(r"^\s*repo\s*:\s*['\"]?([^'\"]+?)['\"]?\s*$", line)
        if mr:
            cur["repo"] = mr.group(1).strip(); continue
        mt = re.match(r"^\s*trust_domain\s*:\s*['\"]?([^'\"]+?)['\"]?\s*$", line)
        if mt:
            cur["trust"] = mt.group(1).strip(); continue
if cur:
    sources.append(cur)

if not sources:
    print("{}"); sys.exit(0)

rows = "\n".join(
    "  - {label} (source `{name}`){td}".format(
        label=s["repo"] or s["name"],
        name=s["name"],
        td=(" [%s]" % s["trust"]) if s["trust"] else "",
    )
    for s in sources
)

md = (
    "## agent-index retrieval \u2014 use the session command catalog\n\n"
    "A semantic + lexical index of this harness is available to **every agent**.\n"
    "Take `commands[id=agent-index].argv` from the injected command catalog and\n"
    "append the arguments below (no sub-agent, no MCP tool, no PATH lookup). It covers:\n\n"
    + rows + "\n\n"
    "**How to search:**\n"
    "- `<catalog argv prefix> search \"<natural-language or code query>\" [--source <name>] "
    "[--language <lang>] [--repo <repo>] [--limit N] --json` \u2014 ranked hits; each has "
    "`chunk_id`, `source`, `file_path`, `line_start`/`line_end`, `content`.\n"
    "- `<catalog argv prefix> similar <chunk_id> [--source <name>] [--limit N]` \u2014 pivot "
    "'more like this' from a hit.\n"
    "- `<catalog argv prefix> clusters [--source <name>] [--exact-dupes-only] [--limit N]` \u2014 "
    "near-duplicate groups.\n"
    "- `<catalog argv prefix> status` \u2014 index health + per-source coverage; probe once if "
    "results look sparse.\n\n"
    "**Prefer the catalog command's `search` subcommand** over a broad `grep`/`glob` sweep when searching\n"
    "**within these scopes** by meaning/behavior, for the most-relevant few results\n"
    "across a large corpus, or to pivot from a hit. Pass `--source` to scope to one\n"
    "corpus (and to respect trust-domain boundaries, not yet enforced at query time).\n"
    "Fall back to `grep`/`glob` for exact-string hunts, files outside these scopes,\n"
    "or if the index is unavailable. Read-only: never reindex from an agent \u2014 that\n"
    "is the operator flow (`<catalog argv prefix> index`)."
)

print(json.dumps({"additionalContext": md}))
PY
exit 0
