"""Feature tests for arithmetic operators and their precedence."""

import pytest


@pytest.mark.parametrize("expr,expected", [
    ("10 + 5", 15),
    ("10 - 5", 5),
    ("10 * 5", 50),
    ("21 / 5", 4),
    ("21 % 5", 1),
    ("2 ** 5", 32),
])
def test_binary_operators(run, expr, expected):
    """
    Each arithmetic operator computes the expected result.
    """
    assert run(f"fn main() -> i32 {{ return {expr}; }}").returncode == expected


def test_multiplication_binds_tighter_than_addition(run):
    """
    '2 + 3 * 4' multiplies before adding.
    """
    assert run("fn main() -> i32 { return 2 + 3 * 4; }").returncode == 14


def test_power_binds_tighter_than_multiplication(run):
    """
    '2 * 3 ** 2' raises before multiplying.
    """
    assert run("fn main() -> i32 { return 2 * 3 ** 2; }").returncode == 18


def test_power_is_right_associative(run):
    """
    '2 ** 1 ** 4' groups from the right: 2 ** (1 ** 4) == 2, not (2 ** 1) ** 4 == 16.
    """
    assert run("fn main() -> i32 { return 2 ** 1 ** 4; }").returncode == 2


def test_parentheses_override_precedence(run):
    """
    Parentheses force the addition first.
    """
    assert run("fn main() -> i32 { return (2 + 3) * 4; }").returncode == 20


def test_unary_minus(run):
    """
    Unary minus negates, and binds tighter than multiplication.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = 5;
        let b: i32 = -a * 2; // -10
        return b + 20;       // 10
    }
    """
    assert run(source).returncode == 10


def test_method_call_on_negated_receiver(run):
    """A parenthesized unary minus keeps its operand's type for method lookup."""
    source = """
    fn i64::to_unsigned(const &self) -> u64 { return self as u64; }

    fn main() -> i32 {
        let e2: i64 = -3;
        return (-e2).to_unsigned() as i32;
    }
    """
    assert run(source).returncode == 3


def test_method_call_on_arithmetic_receiver(run):
    """An arithmetic expression keeps an operand's type for method lookup."""
    source = """
    fn i64::abs(const &self) -> i64 {
        return self < 0 ? -self : self;
    }

    fn main() -> i32 {
        let e: i64 = -3;
        let n: i64 = 5;
        return (e + n - 1).abs() as i32;
    }
    """
    assert run(source).returncode == 1


def test_cast_signedness_rejects_mixed_power(compile_source):
    """A cast's signedness is visible to '**', so mixed operands are rejected."""
    with pytest.raises(TypeError, match="cannot apply '\\*\\*' to signed "
                                        "and unsigned operands"):
        compile_source("""
        fn main() -> i32 {
            let exp: u64 = 3;
            let value = (5 as i128) ** exp;
            return value as i32;
        }
        """)
