"""Feature tests for logical operators and short-circuit evaluation."""

import pytest


@pytest.mark.parametrize("expr,expected", [
    ("true and true", 1),
    ("true and false", 0),
    ("false or true", 1),
    ("false or false", 0),
    ("not false", 1),
    ("not true", 0),
])
def test_logical_operators_on_boolean_literals(run, expr, expected):
    """
    Each logical operator yields the expected boolean over 'true'/'false' literals.
    """
    source = f"""
    fn main() -> i32 {{
        if ({expr}) {{
            return 1;
        }}
        return 0;
    }}
    """
    assert run(source).returncode == expected


@pytest.mark.parametrize("expr,expected", [
    ("1 < 2 and 3 < 4", 1),
    ("1 < 2 and 4 < 3", 0),
    ("2 < 1 or 3 < 4", 1),
    ("2 < 1 or 4 < 3", 0),
    ("not (2 < 1)", 1),
    ("not (1 < 2)", 0),
])
def test_logical_operators_on_comparisons(run, expr, expected):
    """
    Each logical operator yields the expected boolean over comparison results.
    """
    source = f"""
    fn main() -> i32 {{
        if ({expr}) {{
            return 1;
        }}
        return 0;
    }}
    """
    assert run(source).returncode == expected


def test_boolean_variables(run):
    """
    'true'/'false' can be stored in bool variables and combined.
    """
    source = """
    fn main() -> i32 {
        let a: bool = true;
        let b: bool = false;
        if (a and not b) {
            return 5;
        }
        return 0;
    }
    """
    assert run(source).returncode == 5


def test_and_short_circuits(run):
    """
    'and' skips its right side when the left is false, avoiding a divide by zero.
    """
    source = """
    fn main() -> i32 {
        let x: i32 = 0;
        if (x != 0 and 10 / x > 0) {
            return 1;
        }
        return 2;
    }
    """
    assert run(source).returncode == 2


def test_or_short_circuits(run):
    """
    'or' skips its right side when the left is true, avoiding a divide by zero.
    """
    source = """
    fn main() -> i32 {
        let x: i32 = 0;
        if (x == 0 or 10 / x > 0) {
            return 1;
        }
        return 2;
    }
    """
    assert run(source).returncode == 1


def test_logical_operators_take_truthy_operands(run):
    """
    Non-boolean values combine logically through their truthiness.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = 5;
        let b: i32 = 0;
        if (a and not b) { // 5 is truthy, 0 is falsy
            return 3;
        }
        return 0;
    }
    """
    assert run(source).returncode == 3
