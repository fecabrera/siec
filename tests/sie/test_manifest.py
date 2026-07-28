"""Tests for centralized package-manifest validation."""

import re
import tomllib

import pytest

from siec.sie import load_package, satisfies, validate_manifest


def manifest(tmp_path, text):
    """Write and parse one package manifest."""
    path = tmp_path / "package.toml"
    path.write_text(text)
    return path, tomllib.loads(text)


def test_manifest_validation_builds_one_normalized_package_model(tmp_path):
    """
    Identity, kind, paths, libraries, and requirements are interpreted once
    into the values every manifest consumer shares.
    """
    path, data = manifest(tmp_path, """\
[package]
name = "demo"
version = "1.2.3"
description = "A package"
readme = "docs/README.md"
license-files = ["LICENSE", "LICENSE"]
include = ["vendor/", "vendor/"]

[library]
sources = ["src/", "single.sie", "src/"]
libs = ["z", "m", "z"]

[dependencies]
core = "^1.2"
""")

    package = validate_manifest(path, data)

    assert package.manifest == path.resolve()
    assert package.name == "demo"
    assert package.version == "1.2.3"
    assert package.kind == "library"
    assert package.description == "A package"
    assert package.sources == ("src", "single.sie")
    assert package.documents == ("docs/README.md", "LICENSE")
    assert package.includes == ("vendor",)
    assert package.libraries == ("z", "m")

    requirement = package.requirements["core"]
    assert requirement.text == "^1.2"
    assert requirement.operator == "^"
    assert requirement.version == (1, 2)


@pytest.mark.parametrize("text,expected", [
    (
        '[package]\nname = "app"\n\n[app]\nsources = ["src", 1]\n',
        "[app] 'sources'",
    ),
    (
        '[package]\nname = "app"\nreadme = 1\n\n[app]\nsources = ["src"]\n',
        "[package] 'readme'",
    ),
    (
        '[package]\nname = "app"\ninclude = [1]\n\n[app]\nsources = ["src"]\n',
        "[package] 'include'",
    ),
    (
        '[package]\nname = "app"\n\n[app]\nlibs = ["m", 1]\n',
        "[app] 'libs'",
    ),
    (
        'dependencies = 1\n\n[package]\nname = "app"\n\n[app]\n',
        "[dependencies] must be a table",
    ),
])
def test_manifest_lists_and_tables_reject_values_previously_discarded(
        tmp_path, text, expected):
    """Malformed field values receive one consistent manifest diagnostic."""
    path, data = manifest(tmp_path, text)

    with pytest.raises(ValueError, match=re.escape(expected)):
        validate_manifest(path, data)


def test_load_package_reads_the_manifest_once(tmp_path, monkeypatch):
    """Using normalized fields does not reinterpret or reread raw TOML."""
    path, _ = manifest(tmp_path, """\
[package]
name = "app"

[app]
sources = ["src/"]
libs = ["m"]
""")

    import siec.sie as sie

    original = sie.read_manifest
    calls = 0

    def counted_read(manifest):
        nonlocal calls
        calls += 1
        return original(manifest)

    monkeypatch.setattr(sie, "read_manifest", counted_read)
    package = load_package(path)

    assert package.spec == "app"
    assert package.dependencies() == {}
    assert package.libs() == ("m",)
    assert calls == 1


def test_validated_requirements_are_not_reparsed(tmp_path, monkeypatch):
    """Resolution predicates consume the parsed requirement value directly."""
    path, data = manifest(tmp_path, """\
[package]
name = "app"

[app]
sources = ["src"]

[dependencies]
core = "~1.2"
""")
    requirement = validate_manifest(path, data).requirements["core"]

    import siec.sie as sie

    def reparsed(_text):
        raise AssertionError("requirement was parsed again")

    monkeypatch.setattr(sie, "parse_requirement", reparsed)

    assert satisfies("1.2.7", requirement)
    assert not satisfies("1.3.0", requirement)


def test_package_relative_paths_are_normalized_and_cannot_escape(tmp_path):
    """Every path-bearing field shares the same containment validation."""
    path, data = manifest(tmp_path, """\
[package]
name = "app"
include = ["../outside"]

[app]
sources = ["src"]
""")

    with pytest.raises(ValueError, match="outside the package"):
        validate_manifest(path, data)
