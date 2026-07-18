"""Feature tests for the 'null' pointer literal."""

import pytest


def test_null_adapts_to_any_pointer(run):
    """
    'null' initializes, compares against, and passes as any pointer type.
    """
    result = run("""
        struct node {
            value: i32;
            next: node*;
        }

        fn find(n: node*) -> i32 {
            if (n == null) {
                return 0;
            }
            return n[0].value;
        }

        fn main() -> i32 {
            let head: node = { value = 40, next = null };
            let total: i32 = find(&head) + find(null);
            if (head.next != null) {
                return 99;
            }
            return total + 2;
        }
    """)
    assert result.returncode == 42


def test_bare_null_infers_an_opaque_pointer(run):
    """
    'let p = null;' is an opaque*, comparable against any pointer.
    """
    result = run("""
        fn main() -> i32 {
            let p = null;
            if (p == null) {
                return 42;
            }
            return 1;
        }
    """)
    assert result.returncode == 42


def test_null_compares_on_either_side(run):
    """
    '==' and '!=' accept null on the left or the right of any pointer.
    """
    result = run("""
        fn main() -> i32 {
            let x: i32 = 5;
            let p: i32* = &x;
            let n: i32* = null;
            if (p != null and null != p and n == null and null == n) {
                return 42;
            }
            return 1;
        }
    """)
    assert result.returncode == 42


def test_null_returns_and_static_globals(run):
    """
    'return null;' adopts the return type; a static pointer global may
    start null.
    """
    result = run("""
        @static let name: char* = null;

        fn nothing() -> i32* {
            return null;
        }

        fn main() -> i32 {
            let p: i32* = nothing();
            if (p == null and name == null) {
                return 42;
            }
            return 1;
        }
    """)
    assert result.returncode == 42


def test_null_needs_a_pointer_context(compile_source):
    """
    'null' never lands in a non-pointer slot, locally or globally.
    """
    with pytest.raises(TypeError, match="'null' needs a pointer context"):
        compile_source("fn main() -> i32 { let x: i32 = null; return x; }")

    with pytest.raises(TypeError, match="cannot initialize a 'i32' value"):
        compile_source("@static let n: i32 = null;")
