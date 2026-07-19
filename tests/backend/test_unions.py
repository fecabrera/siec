"""Feature tests for unions."""

import pytest


def test_fields_share_their_storage(run):
    """
    Writing one field and reading another reinterprets the same bytes.
    """
    result = run("""
        union pun {
            f: f64;
            bits: u64;
        }

        fn main() -> i32 {
            let u: pun;
            u.f = 1.0;
            if (u.bits == 0x3FF0000000000000) { return 42; }
            return 1;
        }
    """)
    assert result.returncode == 42


def test_size_and_alignment_follow_the_largest_field(run):
    """
    A union measures its largest field, and its alignment holds inside a
    struct.
    """
    result = run("""
        union mixed {
            small: u8;
            wide: u64;
            fp: f32;
        }

        struct holder {
            c: u8;
            u: mixed;
        }

        fn main() -> i32 {
            return (sizeof(mixed) as i32) * 10 + (sizeof(holder) as i32);
        }
    """)
    assert result.returncode == 8 * 10 + 16


def test_unions_nest_in_structs_and_pointers(run):
    """
    A union field inside a struct reads and writes through any chain.
    """
    result = run("""
        union value {
            i: i64;
            f: f64;
        }

        struct tagged {
            tag: i32;
            v: value;
        }

        fn main() -> i32 {
            let t: tagged;
            t.tag = 1;
            t.v.i = 42;
            let p: tagged* = &t;
            return p[0].v.i as i32;
        }
    """)
    assert result.returncode == 42


def test_narrow_field_reads_the_low_bytes(run):
    """
    A smaller field overlays the start of the storage (little-endian).
    """
    result = run("""
        union overlay {
            whole: u32;
            byte: u8;
        }

        fn main() -> i32 {
            let u: overlay;
            u.whole = 0x11223342;
            return u.byte as i32;
        }
    """)
    assert result.returncode == 0x42


def test_aggregate_literals_are_rejected(compile_source):
    """
    A union's fields share storage; no literal fills them field by field.
    """
    with pytest.raises(TypeError, match="no aggregate literal"):
        compile_source("""
            union pun { f: f64; bits: u64; }

            fn main() -> i32 {
                let u: pun = { f = 1.0 };
                return 0;
            }
        """)


def test_packed_union_is_rejected(compile_source):
    """
    '@packed' reorders nothing in a union; it is refused.
    """
    with pytest.raises(SyntaxError, match="a union has no field layout"):
        compile_source("@packed union u { a: i32; }")


def test_union_passes_and_returns_by_value(run):
    """
    A union value travels through calls like any struct, its bytes intact.
    """
    result = run("""
        union pun {
            f: f64;
            bits: u64;
        }

        fn make(x: f64) -> pun {
            let u: pun;
            u.f = x;
            return u;
        }

        fn main() -> i32 {
            if (make(1.0).bits == 0x3FF0000000000000) { return 42; }
            return 1;
        }
    """)
    assert result.returncode == 42
