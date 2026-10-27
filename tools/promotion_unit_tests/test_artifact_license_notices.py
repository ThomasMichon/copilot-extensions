"""Source-notice preservation, not a license classification or approval service."""

from email.parser import BytesParser
import hashlib
import io
import zipfile

import pytest

import promotion_python_artifacts as artifacts


def _case(source, kind):
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
    return data.getvalue(), hashes, prefix, metadata


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
    data, hashes, prefix, metadata = _case(source, kind)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        options = dict(
            source_dir=source, prefix=prefix, metadata=metadata, candidate_root=tmp_path
        )
        if kind in ("missing", "changed", "empty-source", "directory-source"):
            with pytest.raises(artifacts.PromotionArtifactError, match="LICENSE"):
                artifacts.validate_wheel_sources(archive, hashes, **options)
        else:
            artifacts.validate_wheel_sources(archive, hashes, **options)


@pytest.mark.parametrize("kind", ["modern", "changed", "missing"])
def test_selected_vendored_notice_is_not_replaced_by_canonical_source(
    tmp_path, monkeypatch, kind
):
    import build_python_artifacts as builder

    canonical = tmp_path / "libs" / "fixture"
    _case(canonical, "modern")
    (canonical / "LICENSE").write_bytes(b"Changed notice.\n")
    consumer = tmp_path / "plugins" / "consumer"
    selected = consumer / "libs" / "fixture"
    data, hashes, prefix, metadata = _case(selected, kind)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname="consumer"\nversion="1.0"\nrequires-python=">=3.10"\n'
        'dependencies=["fixture"]\n[tool.uv.sources]\nfixture={path="libs/fixture"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(builder, "REPO", tmp_path)
    monkeypatch.setattr(builder, "LIBS_DIR", tmp_path / "libs")
    assert builder.resolve_vendored_libs(consumer) == [("fixture", selected)]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        options = dict(
            source_dir=selected,
            prefix=prefix,
            metadata=metadata,
            candidate_root=tmp_path,
        )
        if kind == "modern":
            artifacts.validate_wheel_sources(archive, hashes, **options)
        else:
            with pytest.raises(artifacts.PromotionArtifactError, match="LICENSE"):
                artifacts.validate_wheel_sources(archive, hashes, **options)
