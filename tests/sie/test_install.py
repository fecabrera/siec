"""Tests for 'sie install'."""

import tomllib

import pytest

from siec.sie import install_root
from tests.sie.test_sie import run_sie


@pytest.fixture
def home(tmp_path, monkeypatch):
    """
    An install root of this test's own, and a working directory to
    install from.
    """
    monkeypatch.setenv("SIE_PATH", str(tmp_path / "sie"))

    workspace = tmp_path / "work"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    return workspace


def make_package(root, name, version="1.0.0", about="", made_of="",
                 kind="library", files=()):
    """
    Lay out a package with a manifest and whatever files it should hold.

    'about' goes under [package], which says who the package is; 'made_of'
    goes under [app] or [library], which says what it is made of.
    """
    package = root / name
    package.mkdir(parents=True, exist_ok=True)

    (package / "package.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "{version}"\n{about}'
        f'\n[{kind}]\n{made_of}')

    for path, text in files:
        target = package / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    return package


def test_installs_the_manifest_and_the_declared_sources(home, monkeypatch):
    """
    An install carries the manifest and every entry of 'sources', each
    keeping the place it held inside the package.
    """
    make_package(home, "zlib", made_of='sources = ["src/"]\n',
                 files=[("src/zlib.sie", "// zlib\n"),
                        ("src/inner/more.sie", "// more\n")])

    assert run_sie(monkeypatch, "install", "zlib") == 0

    installed = install_root() / "zlib@1.0.0"
    assert (installed / "package.toml").is_file()
    assert (installed / "src" / "zlib.sie").read_text() == "// zlib\n"
    assert (installed / "src" / "inner" / "more.sie").read_text() == "// more\n"


def test_the_package_is_the_working_directory_by_default(home, monkeypatch):
    """
    With no path, the package installed is the one the command was run in.
    """
    package = make_package(home, "zlib", made_of='sources = ["src/"]\n',
                           files=[("src/zlib.sie", "")])
    monkeypatch.chdir(package)

    assert run_sie(monkeypatch, "install") == 0
    assert (install_root() / "zlib@1.0.0" / "src" / "zlib.sie").is_file()


def test_the_path_says_which_package_regardless_of_its_directory_name(
        home, monkeypatch):
    """
    A package is filed under the name and version its manifest declares,
    not under the directory it was pulled from.
    """
    package = make_package(home, "checkout", made_of='sources = ["src/"]\n',
                           files=[("src/a.sie", "")])
    (package / "package.toml").write_text(
        '[package]\nname = "zlib"\nversion = "2.1.0"\n'
        '\n[library]\nsources = ["src/"]\n')

    assert run_sie(monkeypatch, "install", "checkout") == 0

    assert (install_root() / "zlib@2.1.0" / "src" / "a.sie").is_file()
    assert not (install_root() / "checkout@1.0.0").exists()


def test_what_is_not_declared_is_left_behind(home, monkeypatch):
    """
    Only what the manifest names is copied: an examples directory beside
    the sources is not part of the package.
    """
    make_package(home, "zlib", made_of='sources = ["src/"]\n',
                 files=[("src/zlib.sie", ""),
                        ("examples/demo.sie", ""),
                        ("notes.txt", "")])

    assert run_sie(monkeypatch, "install", "zlib") == 0

    installed = install_root() / "zlib@1.0.0"
    assert not (installed / "examples").exists()
    assert not (installed / "notes.txt").exists()


def test_a_single_file_source_is_installed_as_one(home, monkeypatch):
    """
    'sources' may name files as well as directories.
    """
    make_package(home, "tiny", made_of='sources = ["tiny.sie"]\n',
                 files=[("tiny.sie", "// tiny\n")])

    assert run_sie(monkeypatch, "install", "tiny") == 0
    assert (install_root() / "tiny@1.0.0" / "tiny.sie").read_text() == "// tiny\n"


def test_the_readme_and_license_files_come_along(home, monkeypatch):
    """
    'readme' and 'license-files' name documents to carry; 'license' is
    the SPDX identifier and names no file.
    """
    make_package(home, "core",
                 about=('readme = "README.md"\n'
                        'license = "BSD-3-Clause"\n'
                        'license-files = ["LICENSE", "NOTICE"]\n'),
                 made_of='sources = ["src/"]\n',
                 files=[("README.md", "# core\n"),
                        ("LICENSE", "BSD\n"),
                        ("NOTICE", "notices\n"),
                        ("src/core.sie", "")])

    assert run_sie(monkeypatch, "install", "core") == 0

    installed = install_root() / "core@1.0.0"
    assert (installed / "README.md").read_text() == "# core\n"
    assert (installed / "LICENSE").read_text() == "BSD\n"
    assert (installed / "NOTICE").read_text() == "notices\n"

    # the SPDX identifier is not a path, so nothing by that name is looked for
    assert not (installed / "BSD-3-Clause").exists()


def test_installs_under_name_and_version(home, monkeypatch):
    """
    The directory is '<name>@<version>' under '$SIE_PATH/lib', so two
    versions of a package live side by side.
    """
    make_package(home / "old", "libc", version="1.0.0",
                 made_of='sources = ["src/"]\n', files=[("src/a.sie", "one\n")])
    make_package(home / "new", "libc", version="2.0.0",
                 made_of='sources = ["src/"]\n', files=[("src/a.sie", "two\n")])

    assert run_sie(monkeypatch, "install", "old/libc") == 0
    assert run_sie(monkeypatch, "install", "new/libc") == 0

    assert (install_root() / "libc@1.0.0" / "src" / "a.sie").read_text() == "one\n"
    assert (install_root() / "libc@2.0.0" / "src" / "a.sie").read_text() == "two\n"


def test_the_manifest_arrives_unchanged(home, monkeypatch):
    """
    The manifest is copied, not rewritten: an installed package reads
    back exactly as its source did.
    """
    package = make_package(home, "zlib",
                           made_of='sources = ["src/"]\nlibs = ["z"]\n',
                           files=[("src/zlib.sie", "")])

    assert run_sie(monkeypatch, "install", "zlib") == 0

    source = tomllib.loads((package / "package.toml").read_text())
    installed = tomllib.loads(
        (install_root() / "zlib@1.0.0" / "package.toml").read_text())
    assert installed == source


def test_reinstalling_replaces_what_was_there(home, monkeypatch, capsys):
    """
    A second install of the same name and version takes over, leaving
    nothing of the first behind.
    """
    make_package(home, "zlib", made_of='sources = ["src/"]\n',
                 files=[("src/old.sie", "old\n")])

    assert run_sie(monkeypatch, "install", "zlib") == 0
    assert capsys.readouterr().out.startswith("installed ")

    (home / "zlib" / "src" / "old.sie").unlink()
    (home / "zlib" / "src" / "new.sie").write_text("new\n")

    assert run_sie(monkeypatch, "install", "zlib") == 0
    assert capsys.readouterr().out.startswith("replaced ")

    installed = install_root() / "zlib@1.0.0"
    assert (installed / "src" / "new.sie").read_text() == "new\n"
    assert not (installed / "src" / "old.sie").exists()


def test_a_failed_install_leaves_the_previous_one_alone(home, monkeypatch):
    """
    The staged copy only takes the place of the old install once it is
    complete, so a copy that fails halfway does not destroy it.
    """
    make_package(home, "zlib", made_of='sources = ["src/"]\n',
                 files=[("src/good.sie", "good\n")])

    assert run_sie(monkeypatch, "install", "zlib") == 0

    import siec.sie

    monkeypatch.setattr(siec.sie, "copy_into",
                        lambda *a, **k: (_ for _ in ()).throw(
                            OSError(13, "Permission denied")))

    assert run_sie(monkeypatch, "install", "zlib") == 1

    installed = install_root() / "zlib@1.0.0"
    assert (installed / "src" / "good.sie").read_text() == "good\n"


def test_a_path_that_is_not_there_is_reported(home, monkeypatch, capsys):
    """
    Nothing to pull from fails, naming the path that was tried.
    """
    assert run_sie(monkeypatch, "install", "nosuch") == 1
    assert "does not exist" in capsys.readouterr().err


def test_a_directory_without_a_manifest_is_reported(home, monkeypatch, capsys):
    """
    A directory that is not a package fails, naming what was missing.
    """
    (home / "empty").mkdir()

    assert run_sie(monkeypatch, "install", "empty") == 1
    assert "package.toml" in capsys.readouterr().err


def test_a_file_is_not_a_package(home, monkeypatch, capsys):
    """
    An install pulls from a package directory: what is copied out of it is
    named relative to that directory, so a lone manifest is not enough.
    """
    make_package(home, "zlib")

    assert run_sie(monkeypatch, "install", "zlib/package.toml") == 1
    assert "not a package directory" in capsys.readouterr().err


def test_a_name_and_version_says_there_is_nothing_to_pull_from(
        home, monkeypatch, capsys):
    """
    '<name>@<version>' is what an install is filed under, so it is what a
    caller reaches for. Until there is a registry, say so.
    """
    make_package(home, "zlib")

    assert run_sie(monkeypatch, "install", "zlib@1.0.0") == 1
    assert "no registry" in capsys.readouterr().err


def test_a_manifest_without_a_name_or_version_is_reported(
        home, monkeypatch, capsys):
    """
    Both are needed to file the install under, so neither may be missing.
    """
    package = home / "nameless"
    package.mkdir()
    (package / "package.toml").write_text(
        '[package]\n\n[library]\nsources = ["src/"]\n')

    assert run_sie(monkeypatch, "install", "nameless") == 1

    err = capsys.readouterr().err
    assert "'name'" in err and "'version'" in err
    assert not install_root().exists()


@pytest.mark.parametrize("name,version", [
    ('"../outside"', '"1.0.0"'),
    ('"/absolute"', '"1.0.0"'),
    ('"bad@name"', '"1.0.0"'),
    ('"safe"', '"../outside"'),
    ("7", '"1.0.0"'),
])
def test_package_identity_is_a_safe_filename_component(
        home, monkeypatch, capsys, name, version):
    """
    Manifest identity values become install directory names, so they must
    neither escape the root nor make '<name>@<version>' ambiguous.
    """
    package = home / "unsafe"
    package.mkdir()
    (package / "package.toml").write_text(
        f"[package]\nname = {name}\nversion = {version}\n"
        "\n[library]\nsources = [\"src/\"]\n")

    assert run_sie(monkeypatch, "install", package) == 1
    assert "[package]" in capsys.readouterr().err
    assert not install_root().exists()


def test_an_entry_reaching_outside_the_package_is_refused(
        home, monkeypatch, capsys):
    """
    A manifest names what is inside its own package and nothing else.
    """
    make_package(home, "sneaky", made_of='sources = ["../elsewhere"]\n')

    assert run_sie(monkeypatch, "install", "sneaky") == 1

    assert "outside the package" in capsys.readouterr().err
    assert not (install_root() / "sneaky@1.0.0").exists()


def test_an_entry_the_package_does_not_have_is_skipped(
        home, monkeypatch, capsys):
    """
    A file the manifest names but the package lacks warns and is passed
    over; the rest of the install is still worth having.
    """
    make_package(home, "zlib",
                 about='readme = "README.md"\n',
                 made_of='sources = ["src/"]\n',
                 files=[("src/zlib.sie", "")])

    assert run_sie(monkeypatch, "install", "zlib") == 0

    err = capsys.readouterr().err
    assert "README.md" in err
    assert "skipping" in err
    assert (install_root() / "zlib@1.0.0" / "src" / "zlib.sie").is_file()


def test_a_package_declaring_no_sources_warns(home, monkeypatch, capsys):
    """
    A [library] with no 'sources' installs nothing to build against,
    which is worth saying out loud: it is usually a misspelt key.
    """
    make_package(home, "empty", made_of='source = ["src/"]\n',
                 files=[("src/empty.sie", "")])

    assert run_sie(monkeypatch, "install", "empty") == 0

    assert "no [library] 'sources'" in capsys.readouterr().err
    assert not (install_root() / "empty@1.0.0" / "src").exists()


def test_the_install_root_follows_sie_path(tmp_path, monkeypatch):
    """
    SIE_PATH says where installs go, and 'lib' is the directory under it.
    """
    monkeypatch.setenv("SIE_PATH", str(tmp_path / "elsewhere"))
    assert install_root() == tmp_path / "elsewhere" / "lib"


def test_without_sie_path_the_install_root_is_under_home(monkeypatch):
    """
    An unset SIE_PATH falls back to '~/.sie', so the command works
    before anything is configured.
    """
    monkeypatch.delenv("SIE_PATH", raising=False)

    from pathlib import Path
    assert install_root() == Path.home() / ".sie" / "lib"


def test_an_app_is_not_installed(home, monkeypatch, capsys):
    """
    An [app] is the end of the line: it is built, and nothing builds
    against it, so the install root is not where it belongs.
    """
    make_package(home, "helloworld", kind="app",
                 made_of='sources = ["src/"]\n',
                 files=[("src/main.sie", "")])

    assert run_sie(monkeypatch, "install", "helloworld") == 1

    assert "[app] is built, not installed" in capsys.readouterr().err
    assert not (install_root() / "helloworld@1.0.0").exists()


def test_a_manifest_that_is_neither_is_reported(home, monkeypatch, capsys):
    """
    '[package]' says who a package is; one of '[app]' or '[library]' says
    what it is. Without the second there is nothing to do with it.
    """
    package = home / "vague"
    package.mkdir()
    (package / "package.toml").write_text(
        '[package]\nname = "vague"\nversion = "1.0.0"\n')

    assert run_sie(monkeypatch, "install", "vague") == 1
    assert "neither [app] nor [library]" in capsys.readouterr().err


def test_a_manifest_that_is_both_is_reported(home, monkeypatch, capsys):
    """
    A package is one or the other, so declaring both says nothing.
    """
    package = home / "both"
    package.mkdir()
    (package / "package.toml").write_text(
        '[package]\nname = "both"\nversion = "1.0.0"\n'
        '\n[app]\nsources = ["src/"]\n'
        '\n[library]\nsources = ["src/"]\n')

    assert run_sie(monkeypatch, "install", "both") == 1
    assert "both [app] and [library]" in capsys.readouterr().err
