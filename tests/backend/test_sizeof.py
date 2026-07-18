"""Feature tests for 'sizeof'."""

import pytest


def test_sizeof_builtin_types(run):
    """
    Scalars measure their storage; pointers are 8 bytes on the host.
    """
    result = run("""
        fn main() -> i32 {
            return (sizeof(char) + sizeof(u16) + sizeof(i32)
                    + sizeof(f64) + sizeof(opaque*)) as i32;
        }
    """)
    assert result.returncode == 1 + 2 + 4 + 8 + 8


def test_sizeof_array_type_is_the_fat_value(run):
    """
    'X[]' is a {pointer, length} pair: 16 bytes, whatever the element.
    """
    result = run("""
        fn main() -> i32 {
            return (sizeof(char[]) + sizeof(i64[])) as i32;
        }
    """)
    assert result.returncode == 32


def test_sizeof_variable_measures_its_type(run):
    """
    A variable's name measures its declared type, not its value.
    """
    result = run("""
        fn main() -> i32 {
            let c: char = 'a';
            let msg: char[] = "hello";
            let n: u16 = 9;
            return (sizeof(c) + sizeof(msg) + sizeof(n)) as i32;
        }
    """)
    assert result.returncode == 1 + 16 + 2


def test_sizeof_struct_and_its_variable(run):
    """
    A struct measures its padded layout, by type name or variable.
    """
    result = run("""
        struct pair {
            a: i32;
            b: i64;
        }

        fn main() -> i32 {
            let p: pair;
            return (sizeof(pair) + sizeof(p)) as i32;
        }
    """)
    assert result.returncode == 32


def test_sizeof_packed_struct(run):
    """
    '@packed' drops the padding sizeof would otherwise count.
    """
    result = run("""
        @packed
        struct tight {
            a: i32;
            b: i64;
        }

        fn main() -> i32 {
            return sizeof(tight) as i32;
        }
    """)
    assert result.returncode == 12


def test_sizeof_adopts_integer_context(run):
    """
    Like a literal, sizeof adopts the integer type around it, defaulting
    to u64.
    """
    result = run("""
        fn main() -> i32 {
            let a: i32 = sizeof(i64);
            let b = sizeof(u8);
            let arr: i32[] = [1, 2];
            if (arr.length < sizeof(i64)) {
                return a + b as i32;
            }
            return 0;
        }
    """)
    assert result.returncode == 9


def test_sizeof_through_an_alias(run):
    """
    An alias measures its target.
    """
    result = run("""
        type id = u32;
        type words = i64[];

        fn main() -> i32 {
            return (sizeof(id) + sizeof(words)) as i32;
        }
    """)
    assert result.returncode == 20


def test_sizeof_in_constant_contexts(run):
    """
    sizeof works in '@const' values, array sizes, and enum member values.
    """
    result = run("""
        @const DOUBLE = sizeof(u64) * 2;

        enum sz: u8 { PTR = sizeof(opaque*) }

        fn main() -> i32 {
            let arr: u8[sizeof(i32)];
            return (DOUBLE + sz::PTR + arr.length) as i32;
        }
    """)
    assert result.returncode == 16 + 8 + 4


def test_sizeof_reference_parameter_measures_the_referent(run):
    """
    A '&T' parameter reads as its T, so sizeof measures T.
    """
    result = run("""
        struct pair {
            a: i64;
            b: i64;
        }

        fn measure(p: &pair) -> i32 {
            return sizeof(p) as i32;
        }

        fn main() -> i32 {
            let p: pair;
            return measure(p);
        }
    """)
    assert result.returncode == 16


def test_sizeof_unknown_name_is_an_error(compile_source):
    """
    A name that is neither a variable in scope nor a type is rejected.
    """
    with pytest.raises(TypeError, match="unknown type 'wat'"):
        compile_source("fn main() -> i32 { return sizeof(wat) as i32; }")
