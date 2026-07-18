"""Feature tests for the '@symbol' decorator."""

import pytest


def test_extern_symbol_binds_a_libc_name(run):
    """
    '@extern @symbol("labs")' calls libc's labs under a Sie name.
    """
    result = run("""
        @extern @symbol("labs") fn absolute(x: i64) -> i64;

        fn main() -> i32 {
            return absolute(-42 as i64) as i32;
        }
    """)
    assert result.returncode == 42


def test_defined_function_lives_under_its_symbol(compile_source):
    """
    A defined '@symbol' function is emitted and called by its chosen
    module symbol.
    """
    module = str(compile_source("""
        @symbol("sie_helper") fn helper() -> i32 { return 42; }

        fn main() -> i32 { return helper(); }
    """))
    assert 'define i32 @"sie_helper"()' in module
    assert 'call i32 @"sie_helper"()' in module
    assert '@"helper"' not in module


def test_symbol_with_conditional_compilation(run):
    """
    The errno pattern: '@if' picks the platform's symbol behind one name.
    """
    result = run("""
        @if (TARGET_OS == OS_DARWIN) {
            @extern @symbol("__error") fn errno_location() -> i32*;
        } @else {
            @extern @symbol("__errno_location") fn errno_location() -> i32*;
        }

        fn set_errno(value: i32) {
            errno_location()[0] = value;
        }

        fn main() -> i32 {
            set_errno(42);
            return errno_location()[0];
        }
    """)
    assert result.returncode == 42


def test_main_cannot_be_renamed(compile_source):
    """
    The C runtime looks for 'main' by name.
    """
    with pytest.raises(TypeError, match="'main' cannot be renamed"):
        compile_source('@symbol("start") fn main() -> i32 { return 0; }')


def test_symbol_cannot_combine_with_static(compile_source):
    """
    A static function's symbol is the compiler's to mangle.
    """
    with pytest.raises(SyntaxError, match="'@symbol' cannot combine with '@static'"):
        compile_source('@static @symbol("x") fn f() { }')


def test_conflicting_symbols_for_one_name_are_an_error(compile_source):
    """
    One Sie name cannot map to two different module symbols.
    """
    with pytest.raises(TypeError, match="conflicting '@symbol' names"):
        compile_source("""
            @extern @symbol("__error") fn errno_location() -> i32*;
            @extern @symbol("__errno_location") fn errno_location() -> i32*;
        """)


def test_extern_global_under_a_symbol(run):
    """
    '@extern @symbol' binds an outside data symbol behind a Sie name;
    '@if' picks the platform's spelling of libc's stdout.
    """
    result = run("""
        struct FILE;

        @if (TARGET_OS == OS_DARWIN) {
            @extern @symbol("__stdoutp") let out: FILE*;
        } @else {
            @extern @symbol("stdout") let out: FILE*;
        }

        @extern fn fprintf(f: FILE*, fmt: char*, ...) -> i32;

        fn main() -> i32 {
            fprintf(out, "hi from %s\\n", "sie");
            return 0;
        }
    """)
    assert result.stdout == "hi from sie\n"


def test_global_symbol_names_the_module_symbol(compile_source):
    """
    The module global lives under the chosen symbol, not the Sie name.
    """
    module = str(compile_source("""
        @extern @symbol("__stdoutp") let out: opaque*;

        fn peek() -> opaque* { return out; }
    """))
    assert '@"__stdoutp" = external global' in module
    assert '@"out"' not in module


def test_global_symbol_requires_extern(compile_source):
    """
    '@symbol' names outside storage; static and bare globals have none.
    """
    with pytest.raises(SyntaxError, match="'@symbol' requires an '@extern' global"):
        compile_source('@static @symbol("x") let g: i32 = 1;')
