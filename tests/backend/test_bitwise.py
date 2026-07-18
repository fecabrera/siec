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


def test_hex_literals_mask(run):
    """
    Hex literals work anywhere ints do: masks, constants, and sizes.
    """
    source = """
    @const MASK: u32 = 0xF0;

    fn main() -> i32 {
        let buf: u8[0x10];
        let color: u32 = 0xAB;
        return ((color & MASK) >> 4) as i32 + buf.length as i32; // 10 + 16
    }
    """
    assert run(source).returncode == 26
