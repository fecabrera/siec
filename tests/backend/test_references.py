"""Feature tests for '&T' reference parameters."""

import pytest


def test_reference_writes_the_callers_variable(run):
    """
    A '&T' parameter aliases the argument: writes reach the caller.
    """
    source = """
    fn add(a: &i32, b: i32) {
        a += b;
    }

    fn main() -> i32 {
        let a: i32 = 40;
        add(a, 2);
        return a;
    }
    """
    assert run(source).returncode == 42


def test_reference_struct_fields_write_through(run):
    """
    Member assignment on a '&S' parameter mutates the caller's struct.
    """
    source = """
    struct Point {
        x: i32;
        y: i32;
    }

    fn shift(p: &Point, dx: i32) {
        p.x += dx;
    }

    fn main() -> i32 {
        let p: Point = {40, 0};
        shift(p, 2);
        return p.x + p.y;
    }
    """
    assert run(source).returncode == 42


def test_reference_forwards_as_a_reference(run):
    """
    Passing a '&T' parameter onward keeps aliasing the original variable.
    """
    source = """
    fn add(a: &i32, b: i32) {
        a += b;
    }

    fn double(a: &i32) {
        add(a, a);
    }

    fn main() -> i32 {
        let a: i32 = 21;
        double(a);
        return a;
    }
    """
    assert run(source).returncode == 42


def test_const_reference_reads_without_copying(run):
    """
    'const &T' is a read-only view; const values bind to it too.
    """
    source = """
    struct Point {
        x: i32;
        y: i32;
    }

    fn norm1(p: const &Point) -> i32 {
        return p.x + p.y;
    }

    fn main() -> i32 {
        let p: const Point = {40, 2};
        return norm1(p);
    }
    """
    assert run(source).returncode == 42


def test_reference_needs_an_assignable_argument(compile_source):
    """
    A literal has no storage to alias.
    """
    with pytest.raises(TypeError, match="needs an assignable argument"):
        compile_source("""
        fn f(a: &i32) {}
        fn main() -> i32 { f(5); return 0; }
        """)


def test_reference_types_must_match_exactly(compile_source):
    """
    No widening can happen in place: a u8 cannot bind to '&i32'.
    """
    with pytest.raises(TypeError, match="cannot bind a 'u8' value"):
        compile_source("""
        fn f(a: &i32) {}
        fn main() -> i32 { let b: u8 = 1; f(b); return 0; }
        """)


def test_const_value_needs_a_const_reference(compile_source):
    """
    A const value never binds to a mutable reference.
    """
    with pytest.raises(TypeError, match="cannot bind a 'const i32' value"):
        compile_source("""
        fn f(a: &i32) {}
        fn main() -> i32 { let c: const i32 = 1; f(c); return 0; }
        """)


def test_const_reference_cannot_be_written(compile_source):
    """
    Writing through a 'const &T' violates the contract.
    """
    with pytest.raises(TypeError, match="cannot assign to const variable 'p'"):
        compile_source("fn f(p: const &i32) { p = 5; }")


def test_reference_is_not_addressable(compile_source):
    """
    '&a' on a reference parameter is a compile error.
    """
    with pytest.raises(TypeError, match="cannot take an address of reference"):
        compile_source("fn f(a: &i32) { let p = &a; }")


def test_reference_members_are_not_addressable(compile_source):
    """
    '&s.member' would leak the caller's storage and is rejected too.
    """
    with pytest.raises(TypeError, match="through reference parameter 's'"):
        compile_source("""
        struct P { x: i32; }
        fn f(s: &P) { let p = &s.x; }
        """)


def test_reference_cannot_type_a_variable(compile_source):
    """
    'let a: &i32;' is invalid: a variable is its own storage.
    """
    with pytest.raises(TypeError, match="reference cannot type a variable"):
        compile_source("fn main() -> i32 { let a: &i32; return 0; }")


def test_reference_return_needs_a_reference_parameter(compile_source):
    """
    A returned reference must alias storage that outlives the call: it
    can only derive from a reference parameter.
    """
    with pytest.raises(TypeError, match="reference return must derive from a "
                                        "reference parameter"):
        compile_source("fn f() -> &i32;")


def test_reference_cannot_be_a_field(compile_source):
    """
    A struct field is its own storage, never a reference.
    """
    with pytest.raises(TypeError, match="field 'r' cannot be a reference"):
        compile_source("struct S { r: &i32; }")
