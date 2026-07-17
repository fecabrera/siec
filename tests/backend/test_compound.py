"""Feature tests for compound assignment operators."""

import pytest


@pytest.mark.parametrize("init,op,operand,expected", [
    (10, "+=", 5, 15),
    (10, "-=", 5, 5),
    (10, "*=", 5, 50),
    (20, "/=", 4, 5),
    (10, "%=", 3, 1),
    (2, "**=", 5, 32),
    (1, "<<=", 5, 32),
    (64, ">>=", 3, 8),
    (12, "&=", 10, 8),
    (12, "|=", 3, 15),
    (12, "^=", 10, 6),
])
def test_compound_operators(run, init, op, operand, expected):
    """
    Each compound assignment updates the variable in place with the operator's result.
    """
    source = f"""
    fn main() -> i32 {{
        let a: i32 = {init};
        a {op} {operand};
        return a;
    }}
    """
    assert run(source).returncode == expected


def test_compound_desugars_to_the_binary_op(run):
    """
    'a += b' behaves as 'a = a + b', reading the current value first.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = 4;
        a *= a; // 16
        a += a; // 32
        return a;
    }
    """
    assert run(source).returncode == 32


def test_compound_assignment_on_a_struct_field(run):
    """
    Compound assignment applies to a struct field target.
    """
    source = """
    struct Counter {
        n: i32;
    }

    fn main() -> i32 {
        let c: Counter;
        c.n = 10;
        c.n += 5;
        c.n *= 2;
        return c.n; // 30
    }
    """
    assert run(source).returncode == 30
