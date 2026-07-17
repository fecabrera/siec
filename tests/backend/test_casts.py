"""Feature tests for explicit numeric casts through the whole pipeline."""

import pytest


def test_signed_to_unsigned_reinterprets_the_bits(run):
    """
    Casting a negative signed value to unsigned reinterprets its bits.
    """
    source = """
    fn main() -> i32 {
        let a: i8 = -1;
        let b: u8 = a as u8; // 255
        return b as i32;
    }
    """
    assert run(source).returncode == 255


def test_narrowing_truncates(run):
    """
    Casting to a narrower integer keeps only the low bits.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = 300;
        return (a as u8) as i32; // 300 & 0xFF = 44
    }
    """
    assert run(source).returncode == 44


def test_unsigned_widening_zero_extends(run):
    """
    Casting a small unsigned value to a wider type keeps its magnitude.
    """
    source = """
    fn main() -> i32 {
        let a: u8 = 200;
        let b: u64 = a as u64;
        if (b == 200) {
            return 1;
        }
        return 0;
    }
    """
    assert run(source).returncode == 1


def test_literal_cast(run):
    """
    A literal can be cast, narrowing to the target width.
    """
    source = "fn main() -> i32 { return (300 as u8) as i32; }"
    assert run(source).returncode == 44


def test_int_float_roundtrip(run):
    """
    An integer converts to a float and back, preserving its value.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = 7;
        let f: f64 = a as f64;
        let back: i32 = f as i32;
        return back * 6; // 42
    }
    """
    assert run(source).returncode == 42


def test_cast_enables_mixed_signedness_arithmetic(run):
    """
    A cast bridges signed and unsigned operands that could not be mixed directly.
    """
    source = """
    fn main() -> i32 {
        let s: i32 = 5;
        let u: u32 = 10;
        if ((s as u32) + u == 15) {
            return 3;
        }
        return 0;
    }
    """
    assert run(source).returncode == 3


def test_cast_binds_tighter_than_addition(run):
    """
    'a as u8 + 1' casts a before adding, per the cast's precedence.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = 300;
        let b: u8 = a as u8 + 1; // (300 as u8) + 1 = 45
        return b as i32;
    }
    """
    assert run(source).returncode == 45


@pytest.mark.parametrize("decl", [
    "let a: i32 = 0; let b: bool = a as bool;",   # to a non-numeric type
    "let a: bool = true; let b: i32 = a as i32;", # from a non-numeric value
])
def test_non_numeric_casts_are_errors(compile_source, decl):
    """
    Casting to or from a non-numeric type is rejected.
    """
    with pytest.raises(TypeError, match="cannot cast"):
        compile_source(f"fn main() -> i32 {{ {decl} return 0; }}")
