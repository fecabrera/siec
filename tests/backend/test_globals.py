"""Feature tests for '@extern let' global variables."""

import pytest


def test_global_declares_external_storage(compile_source):
    """
    An '@extern let' lowers to an external global in the module.
    """
    module = compile_source("""
    @extern let MPD_MINALLOC: i64;
    fn main() -> i32 { return MPD_MINALLOC as i32; }
    """)
    assert '@"MPD_MINALLOC" = external global i64' in str(module)


def test_reading_a_libc_global(run):
    """
    'environ' links against libc and reads like any variable.
    """
    source = """
    @extern let environ: char**;

    fn main() -> i32 {
        if (environ) {
            return 42;
        }
        return 0;
    }
    """
    assert run(source).returncode == 42


def test_globals_can_be_assigned(compile_source):
    """
    Assignment stores into the global's module-level storage.
    """
    module = compile_source("""
    @extern let counter: i64;
    fn main() -> i32 {
        counter = 5;
        counter += 1;
        return counter as i32;
    }
    """)
    assert 'store i64 5, i64* @"counter"' in str(module)


def test_const_global_cannot_be_assigned(compile_source):
    """
    A 'const' global rejects assignment like any const variable.
    """
    with pytest.raises(TypeError, match="cannot assign to const variable"):
        compile_source("""
        @extern let version: const char*;
        fn main() -> i32 { version = "x"; return 0; }
        """)


def test_global_function_reference_is_callable(compile_source):
    """
    A global holding a 'fn(...)' reference is assigned and called through.
    """
    module = compile_source("""
    @extern let handler: fn(i64) -> i64;

    fn double(n: i64) -> i64 { return n * 2; }

    fn main() -> i32 {
        handler = double;
        return handler(21) as i32;
    }
    """)
    text = str(module)
    assert 'external global i64 (i64)*' in text
    assert 'load i64 (i64)*, i64 (i64)** @"handler"' in text


def test_global_infers_its_declared_type(compile_source):
    """
    'let x = G;' adopts the global's declared type.
    """
    module = compile_source("""
    @extern let MPD_MINALLOC: i64;
    fn main() -> i32 {
        let x = MPD_MINALLOC; // i64
        return (x / 2) as i32;
    }
    """)
    assert "sdiv i64" in str(module)


def test_duplicate_global_is_an_error(compile_source):
    """
    Declaring the same global twice is rejected.
    """
    with pytest.raises(TypeError, match="declared more than once"):
        compile_source("""
        @extern let x: i64;
        @extern let x: i64;
        """)


def test_global_and_function_cannot_share_a_name(compile_source):
    """
    One name cannot be both a global and a function.
    """
    with pytest.raises(TypeError, match="both a function and a global"):
        compile_source("""
        @extern let free: fn(opaque*);
        fn free(p: opaque*) {}
        """)
