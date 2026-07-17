"""Feature tests for bitwise operators."""

import pytest


@pytest.mark.parametrize("expr,expected", [
    ("1 << 3", 8),
    ("32 >> 2", 8),
    ("6 & 3", 2),
    ("5 | 2", 7),
    ("6 ^ 3", 5),
])
def test_binary_operators(run, expr, expected):
    """
    Each bitwise operator computes the expected result.
    """
    assert run(f"fn main() -> i32 {{ return {expr}; }}").returncode == expected


def test_bitwise_not(run):
    """
    '~' flips every bit; ~0 is -1, so ~0 + 43 is 42.
    """
    assert run("fn main() -> i32 { return ~0 + 43; }").returncode == 42


def test_operators_combine(run):
    """
    A chain of shifts and masks composes as written.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = 240 >> 2 | 1; // 60 | 1 = 61
        return a & 63;             // 61
    }
    """
    assert run(source).returncode == 61
