"""Feature tests for implicit numeric widening between same-prefix types."""

import pytest


def test_unsigned_widens_on_assignment(run):
    """
    A u8 widens to a u64 on assignment, zero-extending its value.
    """
    source = """
    fn main() -> i32 {
        let a: u8 = 200;
        let b: u64 = a;
        if (b == 200) {
            return 1;
        }
        return 0;
    }
    """
    assert run(source).returncode == 1


def test_signed_widens_and_keeps_its_sign(run):
    """
    A negative i8 sign-extends when widened to i32.
    """
    source = """
    fn widen(x: i32) -> i32 {
        return x;
    }

    fn main() -> i32 {
        let a: i8 = -5;
        return widen(a) + 105; // sign-extended -5, then +105
    }
    """
    assert run(source).returncode == 100


def test_widening_as_a_call_argument(run):
    """
    A narrower value widens to a wider parameter of the same prefix.
    """
    source = """
    fn take(n: u64) -> u64 {
        return n;
    }

    fn main() -> i32 {
        let a: u16 = 500;
        if (take(a) == 500) {
            return 7;
        }
        return 0;
    }
    """
    assert run(source).returncode == 7


def test_widening_into_a_struct_field(run):
    """
    A narrower value widens to a wider struct field on assignment.
    """
    source = """
    struct Box {
        big: u64;
    }

    fn main() -> i32 {
        let a: u8 = 42;
        let box: Box;
        box.big = a; // widens u8 -> u64
        if (box.big == 42) {
            return 9;
        }
        return 0;
    }
    """
    assert run(source).returncode == 9


def test_widening_on_return(run):
    """
    A narrower value widens to the function's wider return type.
    """
    source = """
    fn promote(x: u8) -> u32 {
        return x; // widens u8 -> u32
    }

    fn main() -> i32 {
        if (promote(250) == 250) {
            return 5;
        }
        return 0;
    }
    """
    assert run(source).returncode == 5


@pytest.mark.parametrize("decl,message", [
    ("let a: i16 = 0; let b: i8 = a;", "narrow"),
    ("let a: i16 = 0; let b: u32 = a;", "signed, unsigned, and float"),
    ("let a: i16 = 0; let b: f32 = a;", "signed, unsigned, and float"),
    ("let a: u32 = 0; let b: i32 = a;", "signed, unsigned, and float"),
])
def test_disallowed_conversions_are_errors(compile_source, decl, message):
    """
    Narrowing and cross-prefix conversions need an explicit cast.
    """
    with pytest.raises(TypeError, match=message):
        compile_source(f"fn main() -> i32 {{ {decl} return 0; }}")


def test_mixed_width_arithmetic_widens_the_narrower_operand(run):
    """
    Same-prefix operands of different widths meet at the wider one,
    whichever side it sits on.
    """
    result = run("""
        fn main() -> i32 {
            let a: u64 = 40;
            let b: u32 = 2;
            let c: u64 = a - b + b + b;
            return c as i32;
        }
    """)
    assert result.returncode == 42


def test_mixed_width_signed_arithmetic_sign_extends(run):
    """
    A narrower signed operand sign-extends, keeping its negative value.
    """
    result = run("""
        fn main() -> i32 {
            let a: i64 = 50;
            let b: i16 = -8 as i16;
            return (a + b) as i32;
        }
    """)
    assert result.returncode == 42


def test_mixed_width_comparisons(run):
    """
    Comparisons widen too, whichever side is narrower.
    """
    result = run("""
        fn main() -> i32 {
            let a: u64 = 40;
            let b: u32 = 2;
            if (b < a and a > b) {
                return 42;
            }
            return 1;
        }
    """)
    assert result.returncode == 42


def test_mixed_float_widths_extend(run):
    """
    An f32 operand extends to meet an f64.
    """
    result = run("""
        fn main() -> i32 {
            let a: f64 = 40.5;
            let b: f32 = 1.5;
            return (a + b) as i32;
        }
    """)
    assert result.returncode == 42


def test_mixed_signedness_arithmetic_stays_an_error(compile_source):
    """
    Width matching never bridges signedness: u64 + i32 still errors.
    """
    with pytest.raises(TypeError, match="unsigned and signed"):
        compile_source("""
            fn main() -> i32 {
                let a: u64 = 4;
                let b: i32 = 2;
                return (a + b) as i32;
            }
        """)


def test_char_never_widens_into_integers(compile_source):
    """
    A char mixed with a wider integer is an error, not a widening.
    """
    with pytest.raises(TypeError, match="'char' operand"):
        compile_source("""
            fn main() -> i32 {
                let c: char = 'a';
                let x: i32 = 5;
                return (c + x) as i32;
            }
        """)
