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
