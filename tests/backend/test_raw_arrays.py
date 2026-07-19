"""Feature tests for '@raw<T>[N]' inline arrays."""

import pytest


def test_raw_local_reads_writes_and_length(run):
    """
    A raw local indexes in place; '.length' is its element count.
    """
    result = run("""
        fn main() -> i32 {
            let arr: @raw<i32>[8];
            arr[0] = 30;
            arr[7] = 10;
            return arr[0] + arr[7] + arr.length as i32 - 6;
        }
    """)
    assert result.returncode == 42


def test_size_takes_any_constant_expression(run):
    """
    '[N]' evaluates literals, '@const's, 'sizeof', and their mixes.
    """
    result = run("""
        @const N = 4;

        fn main() -> i32 {
            let a: @raw<u8>[N * 2 + sizeof(i32)];
            return a.length as i32;
        }
    """)
    assert result.returncode == 12


def test_raw_struct_field_lays_out_like_c(run):
    """
    A raw field sits inline: the struct measures like C's, and the
    elements are reachable through a plain pointer.
    """
    result = run("""
        struct buf {
            len: i32;
            data: @raw<u8>[16];
        }

        fn main() -> i32 {
            let b: buf;
            b.data[0] = 40;
            b.data[15] = 2;
            let p: u8* = &b.data[0];
            let through: i32 = (p[0] + p[15]) as i32;
            return through + (sizeof(buf) as i32) - 20;
        }
    """)
    assert result.returncode == 42


def test_raw_passes_and_returns_by_value(run):
    """
    A raw array is a value: calls copy all N elements.
    """
    result = run("""
        fn sum(a: @raw<i32>[3]) -> i32 {
            return a[0] + a[1] + a[2];
        }

        fn make() -> @raw<i32>[3] {
            let a: @raw<i32>[3];
            a[0] = 20; a[1] = 12; a[2] = 10;
            return a;
        }

        fn main() -> i32 {
            return sum(make()) + make()[1] - 12;
        }
    """)
    assert result.returncode == 42


def test_pointer_to_raw_array(run):
    """
    '@raw<T>[N]*' points at the whole array; indexing it steps whole
    arrays, C-style.
    """
    result = run("""
        fn main() -> i32 {
            let a: @raw<i32>[4];
            a[2] = 42;
            let p: @raw<i32>[4]* = &a;
            return p[0][2];
        }
    """)
    assert result.returncode == 42


def test_static_global_raw_array(run):
    """
    A '@static' raw global starts zeroed and indexes like a local.
    """
    result = run("""
        @static let table: @raw<i32>[4];

        fn main() -> i32 {
            table[1] = 42;
            return table[1] + table[0];
        }
    """)
    assert result.returncode == 42


def test_raw_through_an_alias(run):
    """
    An alias may name a raw array, its size settled at expansion.
    """
    result = run("""
        @type quad = @raw<i32>[4];

        fn main() -> i32 {
            let a: quad;
            a[3] = 42;
            return a[3] + (sizeof(quad) as i32) - 16;
        }
    """)
    assert result.returncode == 42


def test_raw_sizes_must_agree(compile_source):
    """
    Different element counts are different types.
    """
    with pytest.raises(TypeError, match="cannot implicitly convert"):
        compile_source("""
            fn f(a: @raw<i32>[4]) { }

            fn main() -> i32 {
                let a: @raw<i32>[8];
                f(a);
                return 0;
            }
        """)
