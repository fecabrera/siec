"""Tests for siec.loader."""

import pytest

from siec.loader import load_program, resolve_include


def write(path, text):
    """
    Create a source file (and its parents) with the given text.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_resolve_prefers_the_includer_directory(tmp_path):
    """
    The including file's own directory is searched before the include paths.
    """
    local = write(tmp_path / "src" / "mod.sie", "fn a();")
    write(tmp_path / "lib" / "mod.sie", "fn b();")
    assert resolve_include("mod", tmp_path / "src", [tmp_path / "lib"]) == local


def test_resolve_falls_back_to_include_paths(tmp_path):
    """
    Includes missing locally resolve through the include paths.
    """
    shared = write(tmp_path / "lib" / "libc" / "stdio.sie", "fn a();")
    assert resolve_include("libc/stdio", tmp_path / "src", [tmp_path / "lib"]) == shared


def test_resolve_tries_include_paths_in_order(tmp_path):
    """
    The first include path holding the file wins.
    """
    first = write(tmp_path / "one" / "mod.sie", "fn a();")
    write(tmp_path / "two" / "mod.sie", "fn b();")
    found = resolve_include("mod", tmp_path / "src", [tmp_path / "one", tmp_path / "two"])
    assert found == first


def test_resolve_missing_include_is_an_error(tmp_path):
    """
    An include found in no search root raises FileNotFoundError.
    """
    with pytest.raises(FileNotFoundError, match="cannot resolve include 'nope'"):
        resolve_include("nope", tmp_path, [])


def test_load_merges_included_functions_first(tmp_path):
    """
    Included files contribute their functions ahead of the includer's.
    """
    write(tmp_path / "util.sie", "fn util() {}")
    main = write(tmp_path / "main.sie", '@include("util") fn main() {}')
    program = load_program([main], [])
    assert [fn.name for fn in program.functions] == ["util", "main"]


def test_load_includes_diamond_includes_once(tmp_path):
    """
    A file included along two paths is included exactly once.
    """
    write(tmp_path / "common.sie", "fn common() {}")
    write(tmp_path / "a.sie", '@include("common") fn a() {}')
    write(tmp_path / "b.sie", '@include("common") fn b() {}')
    main = write(tmp_path / "main.sie", '@include("a") @include("b") fn main() {}')
    program = load_program([main], [])
    assert [fn.name for fn in program.functions] == ["common", "a", "b", "main"]


def test_load_survives_include_cycles(tmp_path):
    """
    Mutually including files load once each instead of recursing forever.
    """
    write(tmp_path / "a.sie", '@include("b") fn a() {}')
    write(tmp_path / "b.sie", '@include("a") fn b() {}')
    main = write(tmp_path / "main.sie", '@include("a") fn main() {}')
    program = load_program([main], [])
    assert [fn.name for fn in program.functions] == ["b", "a", "main"]


def test_load_accepts_multiple_sources(tmp_path):
    """
    Every source file passed in contributes its functions in order.
    """
    one = write(tmp_path / "one.sie", "fn one() {}")
    two = write(tmp_path / "two.sie", "fn two() {}")
    program = load_program([one, two], [])
    assert [fn.name for fn in program.functions] == ["one", "two"]


def test_load_dedupes_repeated_sources(tmp_path):
    """
    Passing the same file twice includes it once.
    """
    one = write(tmp_path / "one.sie", "fn one() {}")
    program = load_program([one, one], [])
    assert [fn.name for fn in program.functions] == ["one"]


def test_load_searches_include_paths(tmp_path):
    """
    load_program resolves includes through the given include paths.
    """
    write(tmp_path / "lib" / "dep.sie", "fn dep() {}")
    main = write(tmp_path / "src" / "main.sie", '@include("dep") fn main() {}')
    program = load_program([main], [tmp_path / "lib"])
    assert [fn.name for fn in program.functions] == ["dep", "main"]


def test_load_marks_the_unit_files(tmp_path):
    """
    The sources and their includes, transitively, form the unit; a file
    reached only through 'import' sits outside it.
    """
    write(tmp_path / "mod.sie", "fn entry() {}")
    write(tmp_path / "impl.sie", "fn impl() {}")
    part = write(tmp_path / "part.sie", '@include("impl") fn part() {}')
    main = write(tmp_path / "main.sie",
                 '@include("part") import mod; fn main() {}')

    program = load_program([main], [])
    assert program.unit_files == {str(main.resolve()), str(part.resolve()),
                                  str((tmp_path / "impl.sie").resolve())}


def test_load_prefers_overlay_text(tmp_path):
    """
    An overlay's text stands in for the file's on-disk contents.
    """
    main = write(tmp_path / "main.sie", "fn stale() {}")
    program = load_program([main], [],
                           overlays={str(main.resolve()): "fn fresh() {}"})
    assert [fn.name for fn in program.functions] == ["fresh"]


def test_load_tags_declarations_with_their_source_file(tmp_path):
    """
    Each function and struct is tagged with the file it was parsed from.
    """
    dep = write(tmp_path / "lib" / "dep.sie", "struct D { x: i32; } fn dep() {}")
    main = write(tmp_path / "src" / "main.sie", '@include("dep") fn main() {}')
    program = load_program([main], [tmp_path / "lib"])

    files = {fn.name: fn.file for fn in program.functions}
    assert files["dep"] == str(dep.resolve())
    assert files["main"] == str(main.resolve())
    assert program.structs[0].file == str(dep.resolve())


def test_load_tags_a_parse_error_with_its_file(tmp_path):
    """
    A parse error in an included file carries that file, not the includer.
    """
    dep = write(tmp_path / "lib" / "dep.sie", "fn dep() -> i32 { return 1 }")
    main = write(tmp_path / "src" / "main.sie", '@include("dep") fn main() {}')

    with pytest.raises(SyntaxError) as info:
        load_program([main], [tmp_path / "lib"])
    assert info.value.sie_file == str(dep.resolve())


def test_conditional_include_loads_the_chosen_arm(tmp_path):
    """
    An '@include' inside an '@if' loads only when its arm is chosen,
    decided at load time against the target triple.
    """
    write(tmp_path / "on_darwin.sie", "fn on_darwin() {}")
    write(tmp_path / "on_linux.sie", "fn on_linux() {}")
    main = write(tmp_path / "main.sie", """
        @if (TARGET_OS == OS_DARWIN) {
            @include("on_darwin");
        } @else @if (TARGET_OS == OS_LINUX) {
            @include("on_linux");
        }
        fn main() {}
    """)

    program = load_program([main], [], "arm64-apple-darwin")
    assert [fn.name for fn in program.functions] == ["on_darwin", "main"]

    program = load_program([main], [], "x86_64-unknown-linux-gnu")
    assert [fn.name for fn in program.functions] == ["on_linux", "main"]


def test_conditional_include_never_resolves_the_unchosen_arm(tmp_path):
    """
    The unchosen arm's include is never resolved, so its file need not
    even exist, C-header-style.
    """
    write(tmp_path / "here.sie", "fn here() {}")
    main = write(tmp_path / "main.sie", """
        @if (TARGET_OS == OS_DARWIN) {
            @include("here");
        } @else {
            @include("nowhere/gone");
        }
        fn main() {}
    """)

    program = load_program([main], [], "arm64-apple-darwin")
    assert [fn.name for fn in program.functions] == ["here", "main"]


def test_conditional_include_sees_loaded_constants(tmp_path):
    """
    The condition may use '@const' values from the file itself and from
    files already included, and nested '@if's follow the chosen arms.
    """
    write(tmp_path / "config.sie", "@const WITH_EXTRAS = true;")
    write(tmp_path / "extras.sie", "fn extras() {}")
    write(tmp_path / "deep.sie", "fn deep() {}")
    main = write(tmp_path / "main.sie", """
        @include("config");
        @const DEPTH = 2;

        @if (WITH_EXTRAS and DEPTH > 1) {
            @include("extras");
            @if (DEPTH == 2) {
                @include("deep");
            }
        }
        fn main() {}
    """)

    program = load_program([main], [])
    assert [fn.name for fn in program.functions] == ["extras", "deep", "main"]


def test_conditional_include_condition_must_be_loadable(tmp_path):
    """
    A condition guarding an '@include' evaluates before the program
    assembles: a name that is not a loaded constant is an error naming
    the file and line.
    """
    write(tmp_path / "dep.sie", "fn dep() {}")
    main = write(tmp_path / "main.sie", """
        @if (MYSTERY == 1) {
            @include("dep");
        }
        fn main() {}
    """)

    with pytest.raises(TypeError, match="'MYSTERY' is not a constant in view") as info:
        load_program([main], [])
    assert info.value.sie_file == str(main.resolve())


def test_conditional_include_joins_the_unit(tmp_path):
    """
    A conditionally included file is part of the unit and its names come
    into the includer's view, like any include.
    """
    write(tmp_path / "picked.sie", "fn picked() -> i32 { return 3; }")
    main = write(tmp_path / "main.sie", """
        @if (true) {
            @include("picked");
        }
        fn main() -> i32 { return picked(); }
    """)

    program = load_program([main], [])
    assert str((tmp_path / "picked.sie").resolve()) in program.unit_files
    assert "picked" in program.visible[str(main.resolve())]
