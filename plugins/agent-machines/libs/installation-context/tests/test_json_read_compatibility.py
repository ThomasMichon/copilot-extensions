"""Read native UTF-8 JSON without relaxing byte or duplicate-key validation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_installation_mode_governance import _load_module

@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_json_reader_preserves_native_utf8_documents(tmp_path: Path, encoding: str) -> None:
    module = _load_module()
    path = tmp_path / "deploy-manifest.json"
    document = {"schema_version": 3, "service": "agent-logger", "note": "snowman \u2603"}
    path.write_text(json.dumps(document, ensure_ascii=False), encoding=encoding)
    before = path.read_bytes()
    assert module.read_deployment_manifest(path) == document
    if encoding == "utf-8-sig":
        with pytest.raises(module.InstallationContextError):
            module.read_json(path)
    else:
        assert module.read_json(path) == document
    assert path.read_bytes() == before


def test_receipt_reader_preserves_strict_utf8_language(tmp_path: Path) -> None:
    module = _load_module()
    path = tmp_path / "install-context.json"
    path.write_text('{"value":1}', encoding="utf-8-sig")
    with pytest.raises(module.InstallationContextError):
        module.read_json(path)


@pytest.mark.parametrize("content", [
    b'\xef\xbb\xbf{"value":1,"value":2}',
    b'\xef\xbb\xbf{"value":"\xff"}',
    b'\xef\xbb\xbf\xef\xbb\xbf{}',
    b'\xef\xbb\xbf{"value":',
])
def test_json_reader_still_refuses_invalid_native_documents(
    tmp_path: Path, content: bytes,
) -> None:
    module = _load_module()
    path = tmp_path / "deploy-manifest.json"
    path.write_bytes(content)
    with pytest.raises(module.MalformedDeploymentManifest):
        module.read_deployment_manifest(path)
    assert path.read_bytes() == content


def test_deployment_storage_failure_is_not_malformed_json(tmp_path, monkeypatch) -> None:
    module = _load_module()
    path = tmp_path / "deploy-manifest.json"
    path.write_text("{}")

    def unreadable(*args, **kwargs):
        raise PermissionError("fixture storage failure")

    monkeypatch.setattr(module, "_read_regular_file", unreadable)
    with pytest.raises(module.InstallationContextError, match="storage failure") as raised:
        module.read_deployment_manifest(path)
    assert not isinstance(raised.value, module.MalformedDeploymentManifest)
