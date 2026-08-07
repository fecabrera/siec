"""Feature tests for intrinsic signed and unsigned 128-bit integers."""

import pytest


def test_i128_intrinsic_operations(run):
    """Signed i128 values use integer arithmetic and comparisons."""
    result = run("""
        fn calculate(value: i128) -> i128 {
            return ((value + 9) * 3 - 6) / 3;
        }

        fn main() -> i32 {
            let high: i128 = 1 as i128 << 100;
            if (high >> 100 != 1) { return 1; }
            if (calculate(-12) != -5) { return 2; }
            if (-9 as i128 / 2 != -4) { return 3; }
            if (-9 as i128 % 2 != -1) { return 4; }
            if (((2 as i128) ** 100) != high) { return 5; }
            return 42;
        }
    """)
    assert result.returncode == 42


def test_u128_intrinsic_operations(run):
    """Unsigned u128 operations retain the full 128-bit value."""
    result = run("""
        fn main() -> i32 {
            let high: u128 = 1 as u128 << 100;
            let value: u128 = high | 123;
            if ((value & high) != high) { return 1; }
            if ((value ^ 123) != high) { return 2; }
            if (value / 7 * 7 + value % 7 != value) { return 3; }
            if (~(0 as u128) + 1 != 0) { return 4; }
            let maximum: u128 = -1 as u128;
            if (maximum / 2 >> 126 != 1) { return 5; }
            return 42;
        }
    """)
    assert result.returncode == 42


def test_i128_widening_casts_size_and_static_storage(run):
    """The new widths participate in coercion, casts, layout, and globals."""
    result = run("""
        @static let MASK: u128 = 0x10000000000000000000000000000005;

        fn main() -> i32 {
            let small_signed: i64 = -42;
            let small_unsigned: u64 = 42;
            let wide_signed: i128 = small_signed;
            let wide_unsigned: u128 = small_unsigned;
            if (wide_signed as i64 != -42) { return 1; }
            if (wide_unsigned as u64 != 42) { return 2; }
            if (MASK >> 124 != 1) { return 3; }
            if (@sizeof(i128) != 16 or @sizeof(u128) != 16) { return 4; }
            return 42;
        }
    """)
    assert result.returncode == 42


def test_i128_and_u128_keep_signedness_distinct(compile_source):
    """The two 128-bit types still require an explicit signedness cast."""
    with pytest.raises(TypeError, match="signed, unsigned, and float"):
        compile_source("""
            fn main() -> i32 {
                let signed: i128 = 1;
                let unsigned: u128 = signed;
                return 0;
            }
        """)


def test_u128_enum_and_integer_bound(run):
    """Enums and the sealed Integer category include both new types."""
    result = run("""
        enum Wide: u128 { LOW = 1, HIGH = 1 << 100 }

        fn identity<T: Integer>(value: T) -> T { return value; }

        fn main() -> i32 {
            let signed: i128 = identity(40 as i128);
            let unsigned: u128 = identity(2 as u128);
            if (Wide::HIGH >> 100 != 1) { return 1; }
            return (signed + unsigned as i128) as i32;
        }
    """)
    assert result.returncode == 42


def test_large_untyped_integer_defaults_to_i128(run):
    """A literal beyond i64 defaults to i128 without a type annotation."""
    result = run("""
        fn main() -> i32 {
            let value = 0x10000000000000000;
            return (value >> 64) as i32 + 41;
        }
    """)
    assert result.returncode == 42
