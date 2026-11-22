import importlib.util
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/binding-knowledge/scripts/azure_project_visibility.py"
spec = importlib.util.spec_from_file_location("azure_project_visibility", SCRIPT)
assert spec is not None and spec.loader is not None
visibility = importlib.util.module_from_spec(spec)
spec.loader.exec_module(visibility)
PROJECT = "11111111-2222-3333-4444-555555555555"


@pytest.mark.parametrize("kind", ["private", "public", "unknown", "missing", "foreign", "other-org", "unreadable"])
def test_only_selected_private_project_is_accepted(kind: str) -> None:
    record = {"id": PROJECT, "name": "sample", "visibility": kind,
              "url": f"https://dev.azure.com/example/_apis/projects/{PROJECT}"}
    if kind == "missing":
        del record["visibility"]
    if kind == "foreign":
        record.update(name="another", visibility="private")
    if kind == "other-org":
        record.update(url=f"https://dev.azure.com/another/_apis/projects/{PROJECT}", visibility="private")
    with patch.object(visibility.subprocess, "run") as run:
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps(record))
        if kind == "unreadable":
            run.side_effect = subprocess.CalledProcessError(1, ["az"])
        url = "https://dev.azure.com/example/sample/_git/notes"
        if kind == "private":
            visibility.require_private_project(url)
        else:
            with pytest.raises(ValueError, match="verified private"):
                visibility.require_private_project(url)
        assert run.call_args.args[0][:8] == [
            "az", "devops", "project", "show", "--organization", "https://dev.azure.com/example",
            "--project", "sample",
        ]
