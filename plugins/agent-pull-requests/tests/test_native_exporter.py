"""Actual exporter and typed credential children; synthetic native HTTP opener."""

import json
import os
from pathlib import Path
import select
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_pull_requests.native_read import NativeReadHandler, PROFILE
from agent_pull_requests.owner_read import OwnerReadUnavailable, REQUEST_SCHEMA


@pytest.fixture
def native_exporter(tmp_path):
    root = Path(__file__).resolve().parents[2]
    exporter = tmp_path / "exporter.py"
    reads = tmp_path / "reads"
    context = tmp_path / "context.json"
    exporter.write_text(f"""
import json,os,pathlib,sys
pathlib.Path({str(context)!r}).write_text(json.dumps({{
    'pid':os.getpid(),'group':getattr(os,'getpgrp',lambda:0)()
}}))
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
    control, marker = tmp_path / "control", tmp_path / "credential.json"
    credential.write_text(f"""
import json,os,pathlib,subprocess,sys,time
with pathlib.Path({str(renewals)!r}).open('a') as calls:
    calls.write('renew\\n')
if pathlib.Path({str(control)!r}).exists():
    flags={{'creationflags':subprocess.CREATE_NO_WINDOW}} if os.name=='nt' else {{}}
    child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'],**flags)
    pathlib.Path({str(marker)!r}).write_text(json.dumps({{
        'pid':os.getpid(),'child':child.pid,'group':getattr(os,'getpgrp',lambda:0)()
    }}))
    time.sleep(60)
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
    request = {
        "schema": REQUEST_SCHEMA, "version": 1, "binding_ref": "selected",
        "number": 42, "budget_seconds": 20, "expected_principal": "actor-id",
    }
    return reader, request, renewals, reads, control, marker, context


def test_actual_exporter_and_typed_credential_process_compose_without_forge_effects(
    native_exporter,
):
    reader, request, renewals, reads, _, _, _ = native_exporter
    result = reader.read(request)
    assert result["generation"]["complete"]
    assert result["generation"]["scope"]["principal_id"] == "actor-id"
    assert result["generation"]["revision"]["target_sha"] == "c" * 40
    assert result["acquisition"]["source"] == PROFILE
    assert result["acquisition"]["credential_identity_checks"] == 1
    assert renewals.read_text().splitlines() == ["renew"] * 7
    assert reads.read_text().splitlines() == ["GET"] * 8
    assert "synthetic-native-test-material" not in json.dumps(result)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux exact descendant exit pins")
def test_actual_exporter_timeout_confirms_credential_and_descendant_before_return(native_exporter):
    reader, request, _, _, control, marker, context = native_exporter
    control.write_text("block credential acquisition")
    pins = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reader.read, dict(request, budget_seconds=2))
        try:
            deadline = time.monotonic() + 5
            while not marker.exists():
                assert time.monotonic() < deadline and not future.done()
                time.sleep(.005)
            current = json.loads(marker.read_text())
            enclosing = json.loads(context.read_text())
            assert current["group"] == enclosing["group"]
            for pid in (enclosing["pid"], current["pid"], current["child"]):
                pins.append(os.pidfd_open(pid))
                assert os.getpgid(pid) == enclosing["group"]
            with pytest.raises(OwnerReadUnavailable):
                future.result(timeout=5)
            for descriptor in pins:
                observation = select.poll()
                observation.register(descriptor, select.POLLIN)
                assert observation.poll(0), "native return preceded descendant exit"
        finally:
            for descriptor in pins:
                os.close(descriptor)
