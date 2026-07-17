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
