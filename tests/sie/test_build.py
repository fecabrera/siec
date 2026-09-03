"""Tests for 'sie build' and the resolving behind it."""

import pytest

from siec.sie import install_root, satisfies
from tests.sie.test_install import home, make_package  # noqa: F401
from tests.sie.test_sie import run_sie


HELLO = """\
fn main() -> i32 { return 0; }
"""


def package(root, name, version=None, deps=None, libs=None, sources=("src/",),
            kind=None, files=(("src/main.sie", HELLO),)):
    """
    Lay out a package with a manifest, sources, and dependencies.

    A package with a version is one meant to be installed, so it is a
    [library] unless told otherwise; one without is an [app] to build.
    """
    if kind is None:
        kind = "library" if version else "app"

    about = [f'name = "{name}"']
    if version is not None:
        about.append(f'version = "{version}"')

    made_of = []
    if sources is not None:
        made_of.append("sources = [" + ", ".join(f'"{s}"' for s in sources) + "]")
    if libs:
        made_of.append("libs = [" + ", ".join(f'"{lib}"' for lib in libs) + "]")

    manifest = ("[package]\n" + "\n".join(about) + "\n"
                + f"\n[{kind}]\n" + "".join(line + "\n" for line in made_of))
    if deps:
        manifest += "\n[dependencies]\n" + "".join(
            f'{n} = "{r}"\n' for n, r in deps.items())

    directory = root / (f"{name}-{version}" if version else name)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.toml").write_text(manifest)

    for path, text in files:
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    return directory


def install(monkeypatch, path):
    """
    Put a package in the install root, where a build can resolve it.
    """
    assert run_sie(monkeypatch, "install", str(path)) == 0


#
# Requirements
#

@pytest.mark.parametrize("version,requirement,expected", [
    ("1.0.0", "*", True),
    ("9.9.9", "*", True),
    ("1.0.0", "~1", True),
    ("1.9.9", "~1", True),
    ("2.0.0", "~1", False),
    ("1.2.0", "~1.2", True),
    ("1.3.0", "~1.2", False),
    ("1.2.9", "~1.2.3", True),
    ("1.2.2", "~1.2.3", False),
    ("1.9.0", "^1.2.3", True),
    ("2.0.0", "^1.2.3", False),
    ("0.2.9", "^0.2.3", True),
    ("0.3.0", "^0.2.3", False),
    ("1.2.3", "1.2.3", True),
    ("1.2.4", "1.2.3", False),
    ("1.2.3", "1.2", True),
    ("2.0.0", ">=1.5", True),
    ("1.4.0", ">=1.5", False),
    ("1.4.0", "<1.5", True),
    ("1.5.0", "<=1.5", True),
    ("1.5.0", "!=1.5", False),
    ("10.0.0", "~1", False),
])
def test_a_requirement_says_which_versions_answer_it(
        version, requirement, expected):
    """
    '*' takes anything, '~' and '^' take later releases that keep what was
    written down, and a comparison compares as numbers.
    """
    assert satisfies(version, requirement) is expected


@pytest.mark.parametrize("requirement", [
    "",
    "banana",
    "~",
    ">=",
    "1..2",
    "1.2beta",
])
def test_a_malformed_requirement_is_rejected(requirement):
    """
    Only '*' is a wildcard; typos must not match an empty version prefix.
    """
    with pytest.raises(ValueError, match="invalid version requirement"):
        satisfies("1.2.3", requirement)


#
# Building
#

def test_builds_a_package_into_its_own_build_directory(
        home, monkeypatch, capsys):  # noqa: F811
    """
    The binary lands in '<path>/build/<name>'.
    """
    app = package(home, "app")

    assert run_sie(monkeypatch, "build", app) == 0

    binary = app / "build" / "app"
    assert binary.is_file()
    assert "built" in capsys.readouterr().out


def test_silent_build_hides_progress_output(
        home, monkeypatch, capsys):  # noqa: F811
    """'--silent' builds the binary without build progress on stdout."""
    app = package(home, "app")

    assert run_sie(monkeypatch, "build", app, "--silent") == 0

    assert (app / "build" / "app").is_file()
    assert capsys.readouterr().out == ""


def test_the_package_is_the_working_directory_by_default(
        home, monkeypatch):  # noqa: F811
    """
    With no path, the package built is the one the command was run in.
    """
    app = package(home, "app")
    monkeypatch.chdir(app)

    assert run_sie(monkeypatch, "build") == 0
    assert (app / "build" / "app").is_file()


def test_run_jits_a_package_without_building_a_binary(
        home, monkeypatch, capsys):  # noqa: F811
    """'build --run' returns the app's status and leaves no build output."""
    app = package(home, "app", files=[
        ("src/main.sie", "fn main() -> i32 { return 7; }"),
    ])

    assert run_sie(monkeypatch, "build", app, "--run") == 7
    assert not (app / "build").exists()
    out = capsys.readouterr().out
    assert "running app" in out
    assert "built " not in out


def test_silent_run_keeps_program_output(
        home, monkeypatch, capfd):  # noqa: F811
    """Silent mode hides progress but does not hide the program's stdout."""
    app = package(home, "app", files=[
        ("src/main.sie", "@extern fn printf(fmt: char*, ...) -> i32; "
                         "fn main() -> i32 { "
                         "printf(\"program output\\n\"); return 0; }"),
    ])

    assert run_sie(
        monkeypatch, "build", app, "--silent", "--run") == 0

    out = capfd.readouterr().out
    assert "program output" in out
    assert "running app" not in out


def test_run_uses_the_working_package_and_forwards_arguments(
        home, monkeypatch):  # noqa: F811
    """With no path, everything after '--run' becomes the app's argv."""
    app = package(home, "app", files=[
        ("src/main.sie", "fn main(argc: i32, argv: char**) -> i32 { "
                         "return argc; }"),
    ])
    monkeypatch.chdir(app)

    assert run_sie(monkeypatch, "build", "--run", "one", "two") == 3


def test_run_resolves_and_compiles_installed_dependencies(
        home, monkeypatch, capsys):  # noqa: F811
    """The JIT receives the same resolved include tree as a normal build."""
    install(monkeypatch, package(
        home, "answer", version="1.0.0",
        files=[("src/answer.sie",
                "fn answer() -> i32 { return 42; }")]))
    app = package(
        home, "app", deps={"answer": "*"},
        files=[("src/main.sie", "import answer; "
                "fn main() -> i32 { return answer.answer(); }")])
    capsys.readouterr()

    assert run_sie(monkeypatch, "build", app, "--run") == 42
    assert "answer@1.0.0" in capsys.readouterr().out


def test_a_package_needs_no_version_to_be_built(home, monkeypatch):  # noqa: F811
    """
    Only the name decides what the binary is called, so a package that is
    never installed does not have to carry a version.
    """
    app = package(home, "app", version=None)

    assert run_sie(monkeypatch, "build", app) == 0
    assert (app / "build" / "app").is_file()


def test_an_app_name_cannot_escape_the_build_directory(
        home, monkeypatch, capsys):  # noqa: F811
    """
    The package name becomes the binary filename and must remain one safe
    component below build/.
    """
    app = home / "unsafe-app"
    app.mkdir()
    (app / "src").mkdir()
    (app / "src" / "main.sie").write_text(HELLO)
    (app / "package.toml").write_text(
        '[package]\nname = "../escaped"\n'
        '\n[app]\nsources = ["src/"]\n')

    assert run_sie(monkeypatch, "build", app) == 1
    assert "[package] 'name'" in capsys.readouterr().err
    assert not (app / "escaped").exists()
    assert not (app / "build").exists()


def test_every_source_file_of_the_package_is_compiled(
        home, monkeypatch):  # noqa: F811
    """
    The units are the '.sie' files a 'sources' directory holds, so a
    program split across files builds as one.
    """
    app = package(home, "app", files=[
        ("src/main.sie", "import helper;\n\n"
                         "fn main() -> i32 { return helper.answer(); }\n"),
        ("src/helper.sie", "fn answer() -> i32 { return 0; }\n"),
    ])

    assert run_sie(monkeypatch, "build", app) == 0
    assert (app / "build" / "app").is_file()


def test_what_sits_below_a_source_directory_is_not_its_own_unit(
        home, monkeypatch):  # noqa: F811
    """
    A file deeper in the tree is reached through an import or an
    '@include', so compiling it again on its own would be wrong. Here it
    would not even compile alone.
    """
    app = package(home, "app", files=[
        ("src/main.sie", '@include("inner/part")\n\n'
                         "fn main() -> i32 { return part(); }\n"),
        ("src/inner/part.sie", "fn part() -> i32 { return 0; }\n"),
    ])

    assert run_sie(monkeypatch, "build", app) == 0
    assert (app / "build" / "app").is_file()


#
# Dependencies
#

def test_a_dependency_is_resolved_from_what_is_installed(
        home, monkeypatch, capsys):  # noqa: F811
    """
    A package imports a dependency's modules because its source directory
    goes onto the include path.
    """
    install(monkeypatch, package(
        home, "greet", version="1.0.0",
        files=[("src/greet.sie", "fn hello() -> i32 { return 0; }\n")]))

    app = package(home, "app", deps={"greet": "*"}, files=[
        ("src/main.sie", "import greet;\n\n"
                         "fn main() -> i32 { return greet.hello(); }\n")])

    capsys.readouterr()

    assert run_sie(monkeypatch, "build", app) == 0
    assert "greet@1.0.0" in capsys.readouterr().out


def test_dependencies_of_dependencies_come_too(
        home, monkeypatch, capsys):  # noqa: F811
    """
    The tree is walked whole: what a dependency depends on is on the
    include path as well.
    """
    install(monkeypatch, package(
        home, "deep", version="1.0.0",
        files=[("src/deep.sie", "fn deep() -> i32 { return 0; }\n")]))
    install(monkeypatch, package(
        home, "mid", version="1.0.0", deps={"deep": "*"},
        files=[("src/mid.sie", "import deep;\n\n"
                               "fn mid() -> i32 { return deep.deep(); }\n")]))

    app = package(home, "app", deps={"mid": "*"}, files=[
        ("src/main.sie", "import mid;\n\nfn main() -> i32 { return mid.mid(); }\n")])

    capsys.readouterr()

    assert run_sie(monkeypatch, "build", app) == 0

    out = capsys.readouterr().out
    assert "mid@1.0.0" in out and "deep@1.0.0" in out


def test_dependency_resolution_snapshots_installed_packages_once(
        home, monkeypatch):  # noqa: F811
    """
    Recursive dependency discovery and backtracking share one indexed
    snapshot of the installation directory.
    """
    install(monkeypatch, package(home, "leaf", version="1.0.0",
                                 files=[("src/leaf.sie", "")]))
    app = package(home, "app", deps={"leaf": "*"})

    import siec.sie

    real_installed = siec.sie.installed
    calls = 0

    def counted():
        nonlocal calls
        calls += 1
        return real_installed()

    monkeypatch.setattr(siec.sie, "installed", counted)

    assert run_sie(monkeypatch, "build", app) == 0
    assert calls == 1


def test_the_newest_version_that_answers_is_the_one_used(
        home, monkeypatch, capsys):  # noqa: F811
    """
    Several installed versions can answer a requirement; the newest wins,
    and versions compare as numbers.
    """
    for version in ("1.0.0", "2.0.0", "10.0.0"):
        install(monkeypatch, package(home, "leaf", version=version,
                                     files=[("src/leaf.sie", "")]))

    app = package(home, "app", deps={"leaf": "*"})
    capsys.readouterr()

    assert run_sie(monkeypatch, "build", app) == 0
    assert "leaf@10.0.0" in capsys.readouterr().out


def test_a_requirement_narrows_which_version_is_taken(
        home, monkeypatch, capsys):  # noqa: F811
    """
    A tilde keeps a build off a version that may not fit it.
    """
    for version in ("1.0.0", "2.0.0"):
        install(monkeypatch, package(home, "leaf", version=version,
                                     files=[("src/leaf.sie", "")]))

    app = package(home, "app", deps={"leaf": "~1"})
    capsys.readouterr()

    assert run_sie(monkeypatch, "build", app) == 0
    assert "leaf@1.0.0" in capsys.readouterr().out


def test_one_version_of_a_package_answers_every_requirement_for_it(
        home, monkeypatch, capsys):  # noqa: F811
    """
    A package reached from two places is resolved once, against both
    requirements, so a build never holds two versions of it.
    """
    for version in ("1.0.0", "2.0.0"):
        install(monkeypatch, package(home, "leaf", version=version,
                                     files=[("src/leaf.sie", "")]))

    install(monkeypatch, package(home, "mid", version="1.0.0",
                                 deps={"leaf": "~1"},
                                 files=[("src/mid.sie", "")]))

    # the wildcard would take 2.0.0 alone; mid's '~1' holds it back
    app = package(home, "app", deps={"mid": "*", "leaf": "*"})
    capsys.readouterr()

    assert run_sie(monkeypatch, "build", app) == 0

    out = capsys.readouterr().out
    assert "leaf@1.0.0" in out
    assert "leaf@2.0.0" not in out


def test_dependencies_of_a_discarded_version_do_not_constrain_the_build(
        home, monkeypatch, capsys):  # noqa: F811
    """
    Backtracking from a newer package version removes the dependencies that
    only that abandoned version introduced.
    """
    for version in ("1.0.0", "2.0.0"):
        install(monkeypatch, package(home, "leaf", version=version,
                                     files=[("src/leaf.sie", "")]))

    install(monkeypatch, package(home, "choice", version="1.0.0",
                                 files=[("src/choice.sie", "")]))
    install(monkeypatch, package(home, "choice", version="2.0.0",
                                 deps={"leaf": "2"},
                                 files=[("src/choice.sie", "")]))
    install(monkeypatch, package(home, "narrow", version="1.0.0",
                                 deps={"choice": "<2", "leaf": "1"},
                                 files=[("src/narrow.sie", "")]))

    app = package(home, "app", deps={"choice": "*", "narrow": "*"})
    capsys.readouterr()

    assert run_sie(monkeypatch, "build", app) == 0

    out = capsys.readouterr().out
    assert "choice@1.0.0" in out
    assert "choice@2.0.0" not in out
    assert "leaf@1.0.0" in out
    assert "leaf@2.0.0" not in out


def test_requirements_that_cannot_be_met_together_are_reported(
        home, monkeypatch, capsys):  # noqa: F811
    """
    Two requirements no installed version answers stop the build, naming
    both and who asked.
    """
    for version in ("1.0.0", "2.0.0"):
        install(monkeypatch, package(home, "leaf", version=version,
                                     files=[("src/leaf.sie", "")]))

    install(monkeypatch, package(home, "mid", version="1.0.0",
                                 deps={"leaf": "~2"},
                                 files=[("src/mid.sie", "")]))

    app = package(home, "app", deps={"mid": "*", "leaf": "~1"})
    capsys.readouterr()

    assert run_sie(monkeypatch, "build", app) == 1

    err = capsys.readouterr().err
    assert "'~1'" in err and "'~2'" in err
    assert "mid@1.0.0" in err
    assert "1.0.0, 2.0.0" in err


def test_a_dependency_that_is_not_installed_is_reported(
        home, monkeypatch, capsys):  # noqa: F811
    """
    Nothing to resolve against stops the build before the compiler runs.
    """
    app = package(home, "app", deps={"missing": "*"})

    assert run_sie(monkeypatch, "build", app) == 1

    err = capsys.readouterr().err
    assert "missing" in err
    assert not (app / "build" / "app").exists()


def test_a_malformed_dependency_requirement_is_reported(
        home, monkeypatch, capsys):  # noqa: F811
    """
    A manifest typo is a package error, not a requirement matching all
    installed versions.
    """
    install(monkeypatch, package(home, "leaf", version="1.0.0",
                                 files=[("src/leaf.sie", "")]))
    app = package(home, "app", deps={"leaf": "banana"})
    capsys.readouterr()

    assert run_sie(monkeypatch, "build", app) == 1

    err = capsys.readouterr().err
    assert "dependency 'leaf'" in err
    assert "invalid version requirement 'banana'" in err
    assert not (app / "build" / "app").exists()


def test_a_non_string_dependency_requirement_is_reported(
        home, monkeypatch, capsys):  # noqa: F811
    """
    Invalid dependency values are diagnosed instead of silently omitted.
    """
    app = package(home, "app")
    with (app / "package.toml").open("a") as manifest:
        manifest.write("\n[dependencies]\nleaf = 1\n")

    assert run_sie(monkeypatch, "build", app) == 1

    err = capsys.readouterr().err
    assert "dependency 'leaf' requirement must be a string" in err
    assert not (app / "build" / "app").exists()


def test_a_version_that_is_not_installed_names_the_ones_that_are(
        home, monkeypatch, capsys):  # noqa: F811
    """
    A requirement no installed version answers says what is there.
    """
    install(monkeypatch, package(home, "leaf", version="1.0.0",
                                 files=[("src/leaf.sie", "")]))

    app = package(home, "app", deps={"leaf": "~2"})
    capsys.readouterr()

    assert run_sie(monkeypatch, "build", app) == 1
    assert "1.0.0" in capsys.readouterr().err


def test_a_cycle_between_dependencies_resolves_once(
        home, monkeypatch, capsys):  # noqa: F811
    """
    Two packages that depend on each other are each resolved once rather
    than chased around forever.
    """
    install(monkeypatch, package(home, "one", version="1.0.0",
                                 deps={"two": "*"},
                                 files=[("src/one.sie", "")]))
    install(monkeypatch, package(home, "two", version="1.0.0",
                                 deps={"one": "*"},
                                 files=[("src/two.sie", "")]))

    app = package(home, "app", deps={"one": "*"})
    capsys.readouterr()

    assert run_sie(monkeypatch, "build", app) == 0

    out = capsys.readouterr().out
    assert "one@1.0.0" in out and "two@1.0.0" in out


#
# What the compiler is handed
#

@pytest.fixture
def command(monkeypatch):
    """
    Catch the command line the build hands the compiler, without running it.
    """
    caught = []

    import siec.cli

    def fake(argv):
        caught.append(argv)
        return 0

    monkeypatch.setattr(siec.cli, "main", fake)

    return caught


def flags(argv, flag):
    """
    The values given to one repeated flag, in order.
    """
    return [argv[i + 1] for i, item in enumerate(argv[:-1]) if item == flag]


def test_the_include_path_holds_every_source_directory_in_the_tree(
        home, monkeypatch, command):  # noqa: F811
    """
    Each package's 'sources' directories go on the include path, the
    package being built first so a module of its own is the one found.
    """
    install(monkeypatch, package(home, "deep", version="1.0.0",
                                 files=[("src/deep.sie", "")]))
    install(monkeypatch, package(home, "mid", version="1.0.0",
                                 deps={"deep": "*"},
                                 files=[("src/mid.sie", "")]))

    app = package(home, "app", deps={"mid": "*"})

    assert run_sie(monkeypatch, "build", app) == 0

    includes = flags(command[0], "-I")
    assert includes[0] == str(app / "src")
    assert str(install_root() / "mid@1.0.0" / "src") in includes
    assert str(install_root() / "deep@1.0.0" / "src") in includes


def test_library_names_are_gathered_from_the_whole_tree(
        home, monkeypatch, command):  # noqa: F811
    """
    Every 'libs' entry in the tree is linked against, a dependent's ahead
    of what it depends on, and a name asked for twice is passed once.
    """
    install(monkeypatch, package(home, "deep", version="1.0.0", libs=["m"],
                                 files=[("src/deep.sie", "")]))
    install(monkeypatch, package(home, "mid", version="1.0.0",
                                 deps={"deep": "*"}, libs=["z", "m"],
                                 files=[("src/mid.sie", "")]))

    app = package(home, "app", deps={"mid": "*"}, libs=["curl"])

    assert run_sie(monkeypatch, "build", app) == 0
    assert flags(command[0], "-l") == ["curl", "z", "m"]


def test_the_sources_and_the_output_are_the_packages_own(
        home, monkeypatch, command):  # noqa: F811
    """
    The compiler is handed the package's source files and told to write
    the binary into its build directory.
    """
    app = package(home, "app", files=[("src/main.sie", HELLO),
                                      ("src/other.sie", "")])

    assert run_sie(monkeypatch, "build", app) == 0

    argv = command[0]
    assert argv[:2] == [str(app / "src" / "main.sie"),
                        str(app / "src" / "other.sie")]
    assert flags(argv, "-o") == [str(app / "build" / "app")]


def test_shared_compiler_flags_reach_the_compiler(
        home, monkeypatch, command):  # noqa: F811
    """
    A build passes all shared compiler options to the compiler.
    """
    app = package(home, "app")

    assert run_sie(monkeypatch, "build", app, "-O2", "-g",
                   "-Wunchecked-dereference") == 0

    assert "-O2" in command[0]
    assert "-g" in command[0]
    assert "-Wunchecked-dereference" in command[0]


def test_debug_long_option_reaches_the_compiler(
        home, monkeypatch, command):  # noqa: F811
    """'--debug' is the long form of '-g'."""
    app = package(home, "app")

    assert run_sie(monkeypatch, "build", app, "--debug") == 0
    assert "-g" in command[0]


def test_run_and_its_arguments_reach_the_compiler(
        home, monkeypatch, command):  # noqa: F811
    """The run marker closes compiler options and preserves every app arg."""
    app = package(home, "app")

    assert run_sie(monkeypatch, "build", app, "-O2", "--run",
                   "first", "-x") == 0

    argv = command[0]
    assert "-O2" in argv
    assert argv[-3:] == ["--run", "first", "-x"]
    assert "-o" not in argv
    assert not (app / "build").exists()


def test_a_dependency_that_installed_no_sources_adds_no_include(
        home, monkeypatch, command):  # noqa: F811
    """
    A package whose sources are not there is still linked against and
    still walked for its own dependencies; it just puts nothing on the
    include path.
    """
    dependency = package(home, "bare", version="1.0.0", libs=["m"],
                         files=[("src/bare.sie", "")])
    install(monkeypatch, dependency)

    import shutil
    shutil.rmtree(install_root() / "bare@1.0.0" / "src")

    app = package(home, "app", deps={"bare": "*"})

    assert run_sie(monkeypatch, "build", app) == 0
    assert flags(command[0], "-I") == [str(app / "src")]
    assert flags(command[0], "-l") == ["m"]


#
# Failures the build reports itself
#

def test_a_library_is_not_built(home, monkeypatch, capsys):  # noqa: F811
    """
    A [library] has no entry point of its own: it is installed, and an
    [app] is what turns it into something that runs.
    """
    library = package(home, "zlib", version="1.0.0")

    assert run_sie(monkeypatch, "build", library) == 1

    assert "[library] is installed, not built" in capsys.readouterr().err
    assert not (library / "build").exists()


def test_library_control_characters_are_rejected_cleanly(
        home, monkeypatch, capsys):  # noqa: F811
    """A manifest library name cannot reach subprocess with an embedded NUL."""
    app = package(home, "unsafe", libs=[r"bad\u0000name"])

    assert run_sie(monkeypatch, "build", app) == 1
    err = capsys.readouterr().err
    assert "'libs' entries must not contain NUL or control characters" in err
    assert "Traceback" not in err
    assert not (app / "build").exists()


def test_a_manifest_that_is_neither_is_reported(home, monkeypatch, capsys):  # noqa: F811
    """
    Without an [app] or a [library] there is nothing to build.
    """
    directory = home / "vague"
    directory.mkdir()
    (directory / "package.toml").write_text('[package]\nname = "vague"\n')

    assert run_sie(monkeypatch, "build", directory) == 1
    assert "neither [app] nor [library]" in capsys.readouterr().err


def test_a_dependency_is_read_from_its_library_table(
        home, monkeypatch, command):  # noqa: F811
    """
    An installed package says what it is made of under [library], and
    that is where a build reads its sources and libraries from.
    """
    install(monkeypatch, package(home, "leaf", version="1.0.0", libs=["m"],
                                 files=[("src/leaf.sie", "")]))

    app = package(home, "app", deps={"leaf": "*"})

    assert run_sie(monkeypatch, "build", app) == 0

    assert str(install_root() / "leaf@1.0.0" / "src") in flags(command[0], "-I")
    assert flags(command[0], "-l") == ["m"]


def test_a_package_without_a_name_is_reported(home, monkeypatch, capsys):  # noqa: F811
    """
    The name is what the binary is called, so there is no building
    without it.
    """
    directory = home / "nameless"
    directory.mkdir()
    (directory / "package.toml").write_text(
        '[package]\n\n[app]\nsources = ["src/"]\n')

    assert run_sie(monkeypatch, "build", directory) == 1
    assert "'name'" in capsys.readouterr().err


def test_a_package_with_no_sources_is_reported(home, monkeypatch, capsys):  # noqa: F811
    """
    Nothing to compile is reported rather than handed to the compiler.
    """
    app = package(home, "app", sources=None, files=())

    assert run_sie(monkeypatch, "build", app) == 1
    assert "no sources" in capsys.readouterr().err


def test_a_source_directory_holding_nothing_is_reported(
        home, monkeypatch, capsys):  # noqa: F811
    """
    A 'sources' that names a directory with no '.sie' in it is the same
    as naming none.
    """
    app = package(home, "app", files=[("src/notes.txt", "")])

    assert run_sie(monkeypatch, "build", app) == 1
    assert "no sources" in capsys.readouterr().err


def test_a_path_that_is_not_a_package_is_reported(home, monkeypatch, capsys):  # noqa: F811
    """
    A build needs a package to start from.
    """
    assert run_sie(monkeypatch, "build", home / "nowhere") == 1
    assert "does not exist" in capsys.readouterr().err


def test_a_compile_error_fails_the_build(home, monkeypatch, capsys):  # noqa: F811
    """
    The compiler's own exit status is the build's, and its errors are
    left as it reported them.
    """
    app = package(home, "app", files=[("src/main.sie", "fn main() -> i32 { }\n")])

    assert run_sie(monkeypatch, "build", app) != 0
    assert not (app / "build" / "app").exists()
