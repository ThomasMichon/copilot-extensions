import os
from pathlib import Path

import pytest

from agent_ssh import gateway_authorization as authorization


@pytest.mark.skipif(os.name != "nt", reason="Windows inherited long working-directory contract")
def test_native_authorization_from_long_source_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path
    while len(str(root)) < 300:
        root /= "numbered-source-application-slot"
    root.mkdir(parents=True)
    authorization._windows_sid.cache_clear()
    expected = authorization._windows_sid()
    authorization._windows_sid.cache_clear()
    try:
        with monkeypatch.context() as context:
            context.chdir("\\\\?\\" + str(root))
            private = tmp_path / "source-private-authorization"
            authorization._windows_create_directory(private)
            authorization._windows_private(private, directory=True)
            assert authorization._windows_sid() == expected
    finally:
        authorization._windows_sid.cache_clear()
