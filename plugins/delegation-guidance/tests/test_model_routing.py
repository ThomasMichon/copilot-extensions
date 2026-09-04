"""Tests for inert model-routing configuration resolution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
RESOLVER = PLUGIN / "scripts" / "resolve-model-routing.py"
SCHEMA = PLUGIN / "schemas" / "model-routing.schema.json"
EXAMPLE = PLUGIN / "examples" / "model-routing.json"


def _config(
    purposes: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    return {
        "schema": "copilot-extensions.model-routing",
        "version": 1,
        "purposes": purposes,
    }


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _run(repo: Path, home: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    return subprocess.run(
        [
            sys.executable,
            str(RESOLVER),
            "--repo",
            str(repo),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def _repo(tmp_path: Path, *, trusted: bool = True) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    (repo / ".git").mkdir(parents=True)
    if trusted:
        _write(
            home / ".copilot" / "config.json",
            {"trustedFolders": [str(repo.resolve())]},
        )
    return repo, home


def test_schema_and_example_use_portable_placeholder_models() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    assert schema["properties"]["schema"]["const"] == (
        "copilot-extensions.model-routing"
    )
    assert schema["properties"]["version"]["const"] == 1
    models = {
        entry["model"]
        for entries in example["purposes"].values()
        for entry in entries
    }
    assert models == {"example-code-model", "example-fast-model"}


def test_missing_configs_resolve_to_empty_registry(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    result = _run(repo, home)
    payload = json.loads(result.stdout)

    assert payload["sources"] == {
        "operator": "missing",
        "repository": "missing",
    }
    assert payload["purposes"] == {}
    assert payload["diagnostics"] == []
    assert result.stderr == ""


def test_untrusted_repository_config_is_ignored(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path, trusted=False)
    _write(
        repo / ".github" / "copilot" / "model-routing.json",
        _config({
            "evidence": [{
                "model": "repo-model",
                "state": "candidate",
                "surfaces": ["task"],
            }],
        }),
    )

    result = _run(repo, home)
    payload = json.loads(result.stdout)

    assert payload["sources"]["repository"] == "untrusted"
    assert payload["purposes"] == {}
    assert "repository config ignored because the repository is untrusted" in (
        payload["diagnostics"]
    )


def test_operator_layer_overrides_repository_entry(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    _write(
        repo / ".github" / "copilot" / "model-routing.json",
        _config({
            "evidence": [{
                "model": "shared-model",
                "state": "candidate",
                "surfaces": ["task"],
                "costRank": 30,
            }],
        }),
    )
    _write(
        home / ".copilot" / "model-routing.json",
        _config({
            "evidence": [
                {
                    "model": "shared-model",
                    "state": "held",
                    "surfaces": ["task"],
                    "costRank": 30,
                },
                {
                    "model": "demonstrated-model",
                    "state": "demonstrated",
                    "surfaces": ["task"],
                    "costRank": 10,
                    "evidence": [{
                        "ref": "https://example.com/evidence",
                        "observedAt": "2026-01-01",
                        "sampleCount": 3,
                    }],
                },
            ],
        }),
    )

    payload = json.loads(_run(repo, home).stdout)
    entries = payload["purposes"]["evidence"]

    assert [entry["model"] for entry in entries] == [
        "demonstrated-model",
        "shared-model",
    ]
    assert entries[1]["state"] == "held"
    assert payload["sources"] == {
        "operator": "loaded",
        "repository": "loaded",
    }


def test_invalid_layer_fails_open_with_diagnostic(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    _write(
        repo / ".github" / "copilot" / "model-routing.json",
        {
            **_config({}),
            "execute": "never",
        },
    )

    result = _run(repo, home)
    payload = json.loads(result.stdout)

    assert payload["sources"]["repository"] == "invalid"
    assert payload["purposes"] == {}
    assert payload["diagnostics"]
    assert "[delegation-guidance]" in result.stderr


def test_invalid_operator_suppresses_repository_choices(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    _write(
        repo / ".github" / "copilot" / "model-routing.json",
        _config({
            "coding": [{
                "model": "repository-model",
                "state": "demonstrated",
                "surfaces": ["task"],
                "evidence": [{
                    "ref": "https://example.com/repository-evidence",
                    "observedAt": "2026-01-01",
                    "sampleCount": 3,
                }],
            }],
        }),
    )
    _write(
        home / ".copilot" / "model-routing.json",
        {
            **_config({}),
            "unsupported": True,
        },
    )

    payload = json.loads(_run(repo, home).stdout)

    assert payload["sources"] == {
        "operator": "invalid",
        "repository": "loaded",
    }
    assert payload["purposes"] == {}
    assert "repository config suppressed" in payload["diagnostics"][-1]


def test_schema_invalid_null_operator_field_suppresses_repository(
    tmp_path: Path,
) -> None:
    repo, home = _repo(tmp_path)
    _write(
        repo / ".github" / "copilot" / "model-routing.json",
        _config({
            "coding": [{
                "model": "repository-model",
                "state": "demonstrated",
                "surfaces": ["task"],
                "evidence": [{
                    "ref": "https://example.com/repository-evidence",
                    "observedAt": "2026-01-01",
                    "sampleCount": 3,
                }],
            }],
        }),
    )
    _write(
        home / ".copilot" / "model-routing.json",
        _config({
            "coding": [{
                "model": "operator-model",
                "state": "candidate",
                "surfaces": ["task"],
                "constraints": None,
            }],
        }),
    )

    payload = json.loads(_run(repo, home).stdout)

    assert payload["sources"] == {
        "operator": "invalid",
        "repository": "loaded",
    }
    assert payload["purposes"] == {}
    assert "constraints must be an array" in payload["diagnostics"][0]


def test_duplicate_object_keys_make_layer_invalid(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    config = home / ".copilot" / "model-routing.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        '{"schema":"copilot-extensions.model-routing","version":1,'
        '"purposes":{"coding":[{"model":"duplicate-state",'
        '"state":"held","state":"demonstrated","surfaces":["task"],'
        '"evidence":[{"ref":"https://example.com/evidence",'
        '"observedAt":"2026-01-01","sampleCount":3}]}]}}',
        encoding="utf-8",
    )

    payload = json.loads(_run(repo, home).stdout)

    assert payload["sources"]["operator"] == "invalid"
    assert payload["purposes"] == {}
    assert "duplicate object key: state" in payload["diagnostics"][0]


def test_loader_matches_surface_date_and_notes_schema(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    operator = home / ".copilot" / "model-routing.json"
    _write(
        operator,
        _config({
            "evidence": [{
                "model": "valid-notes",
                "state": "demonstrated",
                "surfaces": ["task"],
                "evidence": [{
                    "ref": "https://example.com/evidence",
                    "observedAt": "2026-01-01",
                    "sampleCount": 1,
                    "notes": "",
                }],
            }],
        }),
    )
    loaded = json.loads(_run(repo, home).stdout)
    assert loaded["purposes"]["evidence"][0]["evidence"][0]["notes"] == ""

    _write(
        operator,
        _config({
            "evidence": [{
                "model": "invalid-surface",
                "state": "candidate",
                "surfaces": ["TASK SPACE"],
            }],
        }),
    )
    invalid_surface = json.loads(_run(repo, home).stdout)
    assert invalid_surface["sources"]["operator"] == "invalid"

    _write(
        operator,
        _config({
            "evidence": [{
                "model": "invalid-date",
                "state": "demonstrated",
                "surfaces": ["task"],
                "evidence": [{
                    "ref": "https://example.com/evidence",
                    "observedAt": "20260101",
                    "sampleCount": 1,
                }],
            }],
        }),
    )
    invalid_date = json.loads(_run(repo, home).stdout)
    assert invalid_date["sources"]["operator"] == "invalid"


def test_demonstrated_model_requires_evidence(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    _write(
        home / ".copilot" / "model-routing.json",
        _config({
            "coding": [{
                "model": "unsupported-demonstration",
                "state": "demonstrated",
                "surfaces": ["task"],
            }],
        }),
    )

    payload = json.loads(_run(repo, home).stdout)

    assert payload["sources"]["operator"] == "invalid"
    assert payload["purposes"] == {}
    assert "evidence is required" in payload["diagnostics"][0]


def test_resolver_is_inert_and_uses_no_process_execution() -> None:
    source = RESOLVER.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "eval(" not in source
    assert "exec(" not in source
    assert '"--home"' not in source
