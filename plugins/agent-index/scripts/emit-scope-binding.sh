#!/usr/bin/env bash
# emit-scope-binding -- agent-index sessionStart hook (POSIX parity).
#
# Emits a succinct "what agent-index covers + prefer @agent-index for retrieval
# within these scopes" guidance fragment as {"additionalContext": "..."}, so
# every agent learns the configured index scopes at session start without the MCP
# tool schemas entering the main context (the tools live in the @agent-index
# sub-agent). Generic emitter (ships in the plugin); it reads the LOCAL scope
# config -- <repo>/.agent-index/config.yaml corpus.sources -- so the operator's
# scope values stay in the repo, not baked into the plugin.
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
    "## agent-index retrieval is available for these scopes\n\n"
    "The **agent-index** semantic + lexical index covers the following configured\n"
    "scopes (delegate retrieval to the **`@agent-index`** sub-agent):\n\n"
    + rows + "\n\n"
    "**Prefer `@agent-index`** (`agent_index_search` / `agent_index_find_similar`)\n"
    "over a broad `grep`/`glob` sweep when searching **within these scopes** by\n"
    "meaning/behavior, when you want the most-relevant few results across a large\n"
    "corpus, or to pivot 'more like this' from a hit. Pass the `source`/`repo`\n"
    "filter to scope to one corpus (and to respect trust-domain boundaries, which\n"
    "are not yet enforced at query time). Fall back to direct `grep`/`glob` for\n"
    "exact-string hunts, files outside these scopes, or if the index is unavailable."
)

print(json.dumps({"additionalContext": md}))
PY
exit 0
