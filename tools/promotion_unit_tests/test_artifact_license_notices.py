"""Source-notice preservation, not a license classification or approval service."""

from email.parser import BytesParser
import hashlib
import io
import zipfile

import pytest

import promotion_python_artifacts as artifacts


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "changed",
        "modern",
        "legacy",
        "historical",
        "empty-source",
        "directory-source",
    ],
)
def test_source_license_notice_is_preserved_in_first_party_wheels(tmp_path, kind):
    source = tmp_path / "source"
    package = source / "src" / "fixture"
    package.mkdir(parents=True)
    code = b"VALUE = 1\n"
    notice = b"Existing source copyright and permission notice.\n"
    (package / "__init__.py").write_bytes(code)
    (source / "pyproject.toml").write_text(
        '[build-system]\nrequires=["setuptools>=68"]\nbuild-backend="setuptools.build_meta"\n'
        '[project]\nname="fixture"\nversion="1.0"\nrequires-python=">=3.10"\nlicense="MIT"\n'
        '[tool.setuptools.packages.find]\nwhere=["src"]\n',
        encoding="utf-8",
    )
    if kind == "directory-source":
        (source / "LICENSE").mkdir()
    elif kind != "historical":
        (source / "LICENSE").write_bytes(b"" if kind == "empty-source" else notice)
    prefix = "fixture-1.0.dist-info/"
    metadata_bytes = (
        b"Metadata-Version: 2.4\nName: fixture\nVersion: 1.0\n"
        b"Requires-Python: >=3.10\nLicense-Expression: MIT\n"
    )
    members = {"fixture/__init__.py": code, prefix + "METADATA": metadata_bytes}
    if kind in ("changed", "modern", "legacy"):
        relative = "LICENSE" if kind == "legacy" else "licenses/LICENSE"
        members[prefix + relative] = (
            b"Changed notice.\n" if kind == "changed" else notice
        )
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    hashes = {
        name: hashlib.sha256(value).hexdigest() for name, value in members.items()
    }
    metadata = BytesParser().parsebytes(metadata_bytes)
    with zipfile.ZipFile(io.BytesIO(data.getvalue())) as archive:
        options = dict(
            source_dir=source, prefix=prefix, metadata=metadata, candidate_root=tmp_path
        )
        if kind in ("missing", "changed", "empty-source", "directory-source"):
            with pytest.raises(artifacts.PromotionArtifactError, match="LICENSE"):
                artifacts.validate_wheel_sources(archive, hashes, **options)
        else:
            artifacts.validate_wheel_sources(archive, hashes, **options)
