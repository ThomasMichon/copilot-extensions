"""Native PR reads with synthetic forge responses and real credential acquisition."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def native_reader(root, repository, scope, *, merged=False):
    root.mkdir(parents=True, exist_ok=True)
    requests = root / "forge-requests"
    snapshot = root / "forge-snapshot.json"
    snapshot.write_text(json.dumps({
        "state": "closed" if merged else "open", "merged": merged,
        "head": {"sha": "head"}, "title": "Fixture",
    }), encoding="utf-8")
    forge_command = root / "forge.py"
    forge_command.write_text(
        "import json,os,pathlib,sys\n"
        f"path=pathlib.Path({str(requests)!r})\n"
        "records=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []\n"
        "token=os.environ['GH_TOKEN']\n"
        "assert os.environ['GITHUB_TOKEN']==token\n"
        "assert token.startswith('synthetic-forge-secret-')\n"
        "ordinal=int(token.rsplit('-',1)[1])\n"
        "assert ordinal>(records[-1]['credential'] if records else 0)\n"
        "assert not any('synthetic-forge-secret-' in part for part in sys.argv)\n"
        "url=sys.argv[-1]\n"
        f"assert url.startswith({scope.api_base + '/repos/' + scope.repository + '/'!r})\n"
        "with path.open('a') as stream:\n"
        "    stream.write(json.dumps({'credential':ordinal,'url':url})+'\\n')\n"
        "if url.endswith('/pulls/7'):\n"
        f"    value=json.loads(pathlib.Path({str(snapshot)!r}).read_text())\n"
        "elif url.endswith('/status'):\n"
        "    value={'total_count':0,'statuses':[]}\n"
        "elif '/check-runs?' in url:\n"
        "    value={'total_count':0,'check_runs':[]}\n"
        "else: value=[]\n"
        "print(json.dumps(value))\n",
        encoding="utf-8",
    )
    native = root / "native.py"
    native.write_text(
        "import json,sys\n"
        f"sys.path.insert(0,{str(Path(repository) / 'plugins' / 'agent-worktrees' / 'src')!r})\n"
        "from agent_worktrees.pr_credential_command import command_source\n"
        "from agent_worktrees.providers import github\n"
        "from agent_worktrees.providers.base import run_cli\n"
        "from agent_worktrees.pr_snapshot_payload import snapshot_payload\n"
        f"assert sys.argv[1:7]==['pr-watch','snapshot',{scope.repository!r},'7','--json',"
        "'--credential-command']\n"
        f"github.run_cli=lambda argv,**options: run_cli([sys.executable,{str(forge_command)!r},"
        "*argv[1:]],**options)\n"
        "source=command_source(sys.argv[7])\n"
        f"snapshot=github.GitHubProvider().get_snapshot({scope.repository!r},7,credential_source=source)\n"
        f"print(json.dumps(snapshot_payload(snapshot,{scope.repository!r},7,'github',"
        f"{scope.api_base!r})))\n",
        encoding="utf-8",
    )
    return (sys.executable, str(native)), requests, snapshot
