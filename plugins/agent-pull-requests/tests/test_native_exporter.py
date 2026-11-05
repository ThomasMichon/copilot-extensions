"""Actual exporter and typed credential children; synthetic native HTTP opener."""

import json
from pathlib import Path
import sys

from agent_pull_requests.native_read import NativeReadHandler, PROFILE
from agent_pull_requests.owner_read import REQUEST_SCHEMA


def test_actual_exporter_and_typed_credential_process_compose_without_forge_effects(tmp_path):
    root = Path(__file__).resolve().parents[2]
    exporter = tmp_path / "exporter.py"
    reads = tmp_path / "reads"
    exporter.write_text(f"""
import json,pathlib,sys
sys.path[:0]=[{str(root / 'agent-worktrees' / 'src')!r},
              {str(root / 'agent-worktrees' / 'tests')!r}]
from test_azure_review_evidence import Forge
from agent_worktrees.providers import azure_observation_reads
class SelectedForge(Forge):
    def open(self, request, timeout):
        assert request.get_method()=='GET'
        result=super().open(request,timeout)
        if request.full_url.split('?')[0].endswith('/pullRequests/42'):
            result.value.update(status='active',createdBy={{'id':'author-id'}},
                lastMergeTargetCommit={{'commitId':'c'*40}})
        with pathlib.Path({str(reads)!r}).open('a') as calls:
            calls.write(request.get_method()+'\\n')
        return result
forge=SelectedForge()
azure_observation_reads.build_opener=lambda *handlers:forge
from agent_worktrees.pr_review_evidence_cli import main
raise SystemExit(main())
""")
    credential = tmp_path / "credential.py"
    renewals = tmp_path / "renewals"
    credential.write_text(f"""
import json,pathlib,time
with pathlib.Path({str(renewals)!r}).open('a') as calls:
    calls.write('renew\\n')
print(json.dumps({{
    'schema':'fleet-manager.forge-credential','version':1,
    'token':'synthetic-native-test-material','expires_at':int(time.time())+300,
    'forge':{{
        'provider':'azure-devops','api_base':'https://dev.azure.com/example',
        'repository':'project/repository','permission':'observe','authentication':'bearer'
    }}
}}))
""")
    reader = NativeReadHandler(
        command=(sys.executable, "-I", str(exporter)),
        credential_command=(sys.executable, "-I", str(credential)),
        repo="project/repository", binding_ref="selected", max_work_seconds=20,
        scope={
            "provider": "azure-devops", "endpoint": "https://dev.azure.com/example",
            "project_id": "project-id", "repository_id": "repository-id",
            "principal_id": "actor-id",
        },
    )
    result = reader.read({
        "schema": REQUEST_SCHEMA, "version": 1, "binding_ref": "selected",
        "number": 42, "budget_seconds": 20, "expected_principal": "actor-id",
    })
    assert result["generation"]["complete"]
    assert result["generation"]["scope"]["principal_id"] == "actor-id"
    assert result["generation"]["revision"]["target_sha"] == "c" * 40
    assert result["acquisition"]["source"] == PROFILE
    assert result["acquisition"]["credential_identity_checks"] == 1
    assert renewals.read_text().splitlines() == ["renew"] * 7
    assert reads.read_text().splitlines() == ["GET"] * 8
    assert "synthetic-native-test-material" not in json.dumps(result)
