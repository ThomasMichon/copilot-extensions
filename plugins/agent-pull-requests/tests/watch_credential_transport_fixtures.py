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


def ado_native_reader(root, repository, scope):
    root.mkdir(parents=True, exist_ok=True)
    requests = root / "forge-requests"
    snapshot = root / "forge-snapshot.json"
    project, name = scope.repository.split("/", 1)
    snapshot.write_text(json.dumps({
        "pullRequestId": 7, "status": "active", "mergeStatus": "succeeded",
        "lastMergeSourceCommit": {"commitId": "head"},
        "sourceRefName": "refs/heads/topic", "targetRefName": "refs/heads/main",
        "createdBy": {"id": "author"}, "reviewers": [], "title": "Fixture",
        "repository": {
            "id": "repository-id", "name": name,
            "project": {"id": "project-id", "name": project},
        },
    }), encoding="utf-8")
    native = root / "native.py"
    native.write_text(
        "import io,json,pathlib,sys\n"
        "from urllib.parse import urlsplit\n"
        f"sys.path.insert(0,{str(Path(repository) / 'plugins' / 'agent-worktrees' / 'src')!r})\n"
        "from agent_worktrees.pr_credential_command import command_source\n"
        "from agent_worktrees.providers.azure_devops import AzureDevOpsProvider\n"
        "from agent_worktrees.providers import azure_observation_reads\n"
        "from agent_worktrees.pr_snapshot_payload import snapshot_payload\n"
        f"assert sys.argv[1:7]==['pr-watch','snapshot',{scope.repository!r},'7','--json',"
        "'--credential-command']\n"
        "class Response(io.BytesIO):\n"
        "    status=200\n"
        "    headers={}\n"
        "    def __init__(self,value): super().__init__(json.dumps(value).encode())\n"
        "class Forge:\n"
        "    def open(self,request,**options):\n"
        "        assert request.get_method()=='GET' and 0<options['timeout']<=20\n"
        "        url=request.full_url\n"
        f"        assert url.startswith({scope.api_base.rstrip('/') + '/'!r})\n"
        "        scheme,token=request.get_header('Authorization').split(' ',1)\n"
        "        assert scheme=='Bear'+'er' and token.startswith('synthetic-forge-secret-')\n"
        "        assert not any('synthetic-forge-secret-' in part for part in sys.argv)\n"
        "        ordinal=int(token.rsplit('-',1)[1])\n"
        f"        path=pathlib.Path({str(requests)!r})\n"
        "        records=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []\n"
        "        assert ordinal>(records[-1]['credential'] if records else 0)\n"
        "        with path.open('a') as stream:\n"
        "            stream.write(json.dumps({'credential':ordinal,'url':url})+'\\n')\n"
        "        target=urlsplit(url).path\n"
        "        if target.endswith('/pullRequests/7'):\n"
        f"            value=json.loads(pathlib.Path({str(snapshot)!r}).read_text())\n"
        "        elif target.endswith('/iterations'):\n"
        "            value={'count':1,'value':[{'id':1,'sourceRefCommit':{'commitId':'head'},"
        "'createdDate':'2026-01-01T00:00:00Z'}]}\n"
        "        else: value={'count':0,'value':[]}\n"
        "        return Response(value)\n"
        "azure_observation_reads.build_opener=lambda *handlers: Forge()\n"
        "source=command_source(sys.argv[7])\n"
        f"snapshot=AzureDevOpsProvider().get_configured_snapshot({scope.repository!r},7,"
        f"api_base={scope.api_base!r},credential_source=source)\n"
        f"print(json.dumps(snapshot_payload(snapshot,{scope.repository!r},7,'azure-devops',"
        f"{scope.api_base!r})))\n",
        encoding="utf-8",
    )
    return (sys.executable, str(native)), requests, snapshot
