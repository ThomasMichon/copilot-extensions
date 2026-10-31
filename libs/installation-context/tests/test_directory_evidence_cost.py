"""Directory source admission must parse a catalog once without weakening it."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil

import pytest

from test_installation_context_posix import (
    BASH, POSIX_SCRIPT, PYTHON_COMMAND, _run, _write_json,
)

pytestmark = [
    pytest.mark.installation_context_smoke,
    pytest.mark.skipif(BASH is None, reason="Native POSIX Bash is required"),
]


def _arguments(root: Path, payload: Path, plugin: str = "agent-example") -> tuple:
    return (
        "resolve", "--copilot-home", root / "copilot",
        "--durable-home", root / "durable", "--payload-root", payload,
        "--plugin-id", plugin,
    )


@pytest.mark.parametrize("count", [1, 200])
def test_directory_lookup_parses_the_manifest_once(tmp_path: Path, count: int) -> None:
    assert BASH is not None
    marketplace = tmp_path / "marketplace"
    payload = marketplace / "payloads/plugins/agent-example"
    payload.mkdir(parents=True)
    manifest = marketplace / ".plugin/marketplace.json"
    plugins = [
        {"name": f"unrelated-{index}", "source": f"plugins/unrelated-{index}"}
        for index in range(count - 1)
    ]
    plugins.append({"name": "agent-example", "source": "plugins/agent-example"})
    _write_json(manifest, {
        "name": "Directory:\n\u96ea",
        "metadata": {"pluginRoot": "payloads"},
        "plugins": plugins,
    })
    real_awk = shutil.which("awk")
    assert real_awk is not None
    commands = tmp_path / "commands"
    commands.mkdir()
    calls = tmp_path / "manifest-parses"
    wrapper = commands / "awk"
    wrapper.write_text(
        "#!/bin/sh\n"
        "for argument do\n"
        f"  if [ \"$argument\" = {shlex.quote(str(manifest))} ]; then\n"
        f"    printf 'parse\\n' >> {shlex.quote(str(calls))}\n"
        "  fi\n"
        "done\n"
        f"exec {shlex.quote(real_awk)} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    arguments = _arguments(tmp_path, payload)
    native = _run(
        (BASH, str(POSIX_SCRIPT)), *arguments,
        env={"PATH": str(commands) + os.pathsep + os.environ["PATH"]},
    )
    expected = _run(PYTHON_COMMAND, *arguments)
    assert json.loads(native.stdout) == json.loads(expected.stdout)
    assert calls.read_text().splitlines() == ["parse"]
    assert not (tmp_path / "durable").exists()


@pytest.mark.parametrize("fault", [
    "duplicate-entry", "duplicate-json-key", "wrong-name-case", "wrong-plugins-case",
    "wrong-name-type", "wrong-source-type", "metadata-escape", "source-escape",
    "nul-name", "nul-source", "reserved-key", "requested-backslash",
])
def test_directory_batch_keeps_native_refusals(tmp_path: Path, fault: str) -> None:
    assert BASH is not None
    marketplace = tmp_path / "marketplace"
    payload = marketplace / "plugins/agent-example"
    payload.mkdir(parents=True)
    entry = {"name": "agent-example", "source": "plugins/agent-example"}
    document = {"name": "example", "plugins": [entry]}
    plugin = "agent-example"
    if fault == "duplicate-entry":
        document["plugins"].append(entry.copy())
    elif fault == "wrong-name-case":
        entry["Name"] = entry.pop("name")
    elif fault == "wrong-plugins-case":
        document["Plugins"] = document.pop("plugins")
    elif fault == "wrong-name-type":
        document["plugins"].append({"name": 42})
    elif fault == "wrong-source-type":
        entry["source"] = []
    elif fault == "metadata-escape":
        document["metadata"] = {"pluginRoot": "../outside"}
    elif fault == "source-escape":
        entry["source"] = "../outside"
    elif fault == "nul-name":
        document["plugins"].append({"name": "unrelated\0"})
    elif fault == "nul-source":
        entry["source"] += "\0"
    elif fault == "reserved-key":
        document["metadata\x1cpluginRoot"] = "forged"
    elif fault == "requested-backslash":
        plugin = r"agent-\x65xample"
    manifest = marketplace / "marketplace.json"
    _write_json(manifest, document)
    if fault == "duplicate-json-key":
        manifest.write_text(
            manifest.read_text().replace('"name": "example"', '"name": "example", "name": "duplicate"'),
        )
    result = _run(
        (BASH, str(POSIX_SCRIPT)), *_arguments(tmp_path, payload, plugin),
        check=False,
    )
    assert result.returncode != 0
    assert result.stderr.strip()
    assert not result.stdout.strip()
    assert not (tmp_path / "durable").exists()


def test_unselected_source_fields_keep_the_existing_scope(tmp_path: Path) -> None:
    assert BASH is not None
    marketplace = tmp_path / "marketplace"
    payload = marketplace / "plugins/agent-example"
    payload.mkdir(parents=True)
    _write_json(marketplace / "marketplace.json", {
        "name": None,
        "metadata": {"pluginRoot": None},
        "plugins": [
            {"name": "unrelated", "source": {"not": "selected"}},
            {"name": None, "source": None},
            {"name": "agent-example", "source": "plugins/agent-example"},
        ],
    })
    arguments = _arguments(tmp_path, payload)
    result = _run((BASH, str(POSIX_SCRIPT)), *arguments)
    expected = _run(PYTHON_COMMAND, *arguments)
    assert json.loads(result.stdout) == json.loads(expected.stdout)
