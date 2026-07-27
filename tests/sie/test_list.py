"""Tests for 'sie list'."""

from siec.sie import install_root
from tests.sie.test_install import home, make_package  # noqa: F401
from tests.sie.test_sie import run_sie


def put(root, name, version, description=None):
    """
    Put a package in the install root by hand, the way an install leaves it.
    """
    package = root / f"{name}@{version}"
    package.mkdir(parents=True)

    said = "" if description is None else f'description = "{description}"\n'
    (package / "package.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "{version}"\n{said}')

    return package


def test_lists_what_was_installed(home, monkeypatch, capsys):  # noqa: F811
    """
    A package shows up under the name and version it was filed as.
    """
    make_package(home, "zlib", extra='sources = ["src/"]\n',
                 files=[("src/zlib.sie", "")])

    assert run_sie(monkeypatch, "install", "zlib") == 0
    capsys.readouterr()

    assert run_sie(monkeypatch, "list") == 0
    assert capsys.readouterr().out == "zlib@1.0.0\n"


def test_the_description_comes_from_the_installed_manifest(
        home, monkeypatch, capsys):  # noqa: F811
    """
    What a package says it is is read back out of the copy that was
    installed, not out of wherever it came from.
    """
    put(install_root(), "zlib", "1.0.0", "Bindings for zlib")

    assert run_sie(monkeypatch, "list") == 0
    assert capsys.readouterr().out == "zlib@1.0.0  Bindings for zlib\n"


def test_specs_are_padded_so_descriptions_line_up(
        home, monkeypatch, capsys):  # noqa: F811
    """
    The descriptions start at one column, whatever the names are.
    """
    put(install_root(), "libc", "1.0.0", "the short one")
    put(install_root(), "mpdecimal", "1.0.0", "the long one")

    assert run_sie(monkeypatch, "list") == 0

    width = len("mpdecimal@1.0.0")
    assert capsys.readouterr().out.splitlines() == [
        f"{'libc@1.0.0':<{width}}  the short one",
        f"{'mpdecimal@1.0.0':<{width}}  the long one",
    ]


def test_a_package_without_a_description_lists_as_its_spec(
        home, monkeypatch, capsys):  # noqa: F811
    """
    A manifest that does not say what it is leaves no trailing padding.
    """
    put(install_root(), "zlib", "1.0.0")

    assert run_sie(monkeypatch, "list") == 0
    assert capsys.readouterr().out == "zlib@1.0.0\n"


def test_versions_of_one_package_are_listed_in_order(
        home, monkeypatch, capsys):  # noqa: F811
    """
    Versions sort as numbers, so 10 comes after 9 rather than before 2.
    """
    for version in ("10.0.0", "2.0.0", "9.0.0"):
        put(install_root(), "thing", version)

    assert run_sie(monkeypatch, "list") == 0

    assert capsys.readouterr().out.splitlines() == [
        "thing@2.0.0", "thing@9.0.0", "thing@10.0.0"]


def test_packages_are_listed_by_name(home, monkeypatch, capsys):  # noqa: F811
    """
    Names sort together, whatever order they were installed in.
    """
    for name in ("zlib", "core", "libc"):
        put(install_root(), name, "1.0.0")

    assert run_sie(monkeypatch, "list") == 0

    assert capsys.readouterr().out.splitlines() == [
        "core@1.0.0", "libc@1.0.0", "zlib@1.0.0"]


def test_an_empty_install_root_says_so(home, monkeypatch, capsys):  # noqa: F811
    """
    Nothing installed is not a failure, and the note goes to stderr so
    the listing itself stays empty.
    """
    assert run_sie(monkeypatch, "list") == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "nothing installed" in captured.err


def test_a_missing_install_root_says_the_same(home, monkeypatch, capsys):  # noqa: F811
    """
    An install root that was never created reads as empty rather than
    as an error.
    """
    assert not install_root().exists()
    assert run_sie(monkeypatch, "list") == 0
    assert "nothing installed" in capsys.readouterr().err


def test_a_staged_copy_is_not_listed(home, monkeypatch, capsys):  # noqa: F811
    """
    An install that was interrupted leaves a hidden staging directory;
    it is not something that got installed.
    """
    put(install_root(), "zlib", "1.0.0")
    (install_root() / ".zlib@2.0.0.partial").mkdir()

    assert run_sie(monkeypatch, "list") == 0
    assert capsys.readouterr().out.splitlines() == ["zlib@1.0.0"]


def test_a_directory_that_is_not_a_spec_is_not_listed(
        home, monkeypatch, capsys):  # noqa: F811
    """
    The directory name is the identity, so one that is not a
    '<name>@<version>' names no install.
    """
    put(install_root(), "zlib", "1.0.0")
    (install_root() / "stray").mkdir()
    (install_root() / "notes.txt").write_text("")

    assert run_sie(monkeypatch, "list") == 0
    assert capsys.readouterr().out.splitlines() == ["zlib@1.0.0"]


def test_an_unreadable_manifest_still_lists_its_package(
        home, monkeypatch, capsys):  # noqa: F811
    """
    A package whose manifest is gone or broken is still installed, and
    listing it is how anyone finds out it is there.
    """
    package = put(install_root(), "broken", "1.0.0", "was fine")
    (package / "package.toml").write_text("[package\n")

    assert run_sie(monkeypatch, "list") == 0
    assert capsys.readouterr().out.splitlines() == ["broken@1.0.0"]


def test_installs_show_up_as_they_are_made(home, monkeypatch, capsys):  # noqa: F811
    """
    The listing reads the install root each time, so what was just
    installed is in it.
    """
    make_package(home, "zlib")
    make_package(home, "libc")

    assert run_sie(monkeypatch, "install", "zlib") == 0
    capsys.readouterr()

    assert run_sie(monkeypatch, "list") == 0
    assert capsys.readouterr().out.splitlines() == ["zlib@1.0.0"]

    assert run_sie(monkeypatch, "install", "libc") == 0
    capsys.readouterr()

    assert run_sie(monkeypatch, "list") == 0
    assert capsys.readouterr().out.splitlines() == ["libc@1.0.0", "zlib@1.0.0"]
