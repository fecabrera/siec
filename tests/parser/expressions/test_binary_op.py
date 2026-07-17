"""Tests for parsing binary operator expressions and their precedence."""

from siec.ast import BinaryOp, IntLiteral, Var
from siec.parser.expressions import parse_expression


def test_no_operator_is_just_the_primary(ts):
    """
    With no operator following, an expression is just its primary.
    """
    assert parse_expression(ts("42")) == IntLiteral(42)


def test_comparison(ts):
    """
    A comparison operator builds a BinaryOp from its two sides.
    """
    assert parse_expression(ts("argc < 3")) == BinaryOp("<", Var("argc"), IntLiteral(3))


def test_all_comparison_operators(ts):
    """
    Every comparison operator parses to a BinaryOp with that op.
    """
    for op in ("<", ">", "<=", ">=", "==", "!="):
        assert parse_expression(ts(f"a {op} b")) == BinaryOp(op, Var("a"), Var("b"))


def test_comparisons_fold_left(ts):
    """
    Chained comparisons group left-associatively.
    """
    assert parse_expression(ts("a < b == c")) == BinaryOp(
        "==", BinaryOp("<", Var("a"), Var("b")), Var("c"))


def test_arithmetic(ts):
    """
    Each arithmetic operator parses to a BinaryOp with that op.
    """
    for op in ("+", "-", "*", "/", "%"):
        assert parse_expression(ts(f"a {op} b")) == BinaryOp(op, Var("a"), Var("b"))


def test_multiplication_binds_tighter_than_addition(ts):
    """
    '1 + 2 * 3' groups the product first.
    """
    assert parse_expression(ts("1 + 2 * 3")) == BinaryOp(
        "+", IntLiteral(1), BinaryOp("*", IntLiteral(2), IntLiteral(3)))


def test_addition_binds_tighter_than_comparison(ts):
    """
    'a + b < c' compares the sum against c.
    """
    assert parse_expression(ts("a + b < c")) == BinaryOp(
        "<", BinaryOp("+", Var("a"), Var("b")), Var("c"))


def test_arithmetic_folds_left(ts):
    """
    Same-precedence chains group left-associatively.
    """
    assert parse_expression(ts("1 - 2 - 3")) == BinaryOp(
        "-", BinaryOp("-", IntLiteral(1), IntLiteral(2)), IntLiteral(3))


def test_bitwise(ts):
    """
    Each bitwise operator parses to a BinaryOp with that op.
    """
    for op in ("<<", ">>", "&", "|", "^"):
        assert parse_expression(ts(f"a {op} b")) == BinaryOp(op, Var("a"), Var("b"))


def test_logical(ts):
    """
    Each logical operator parses to a BinaryOp with that op.
    """
    for op in ("and", "or"):
        assert parse_expression(ts(f"a {op} b")) == BinaryOp(op, Var("a"), Var("b"))


def test_logical_and_binds_tighter_than_or(ts):
    """
    'a or b and c' groups the conjunction first.
    """
    assert parse_expression(ts("a or b and c")) == BinaryOp(
        "or", Var("a"), BinaryOp("and", Var("b"), Var("c")))


def test_comparison_binds_tighter_than_logical(ts):
    """
    'a < b and c' compares before conjoining.
    """
    assert parse_expression(ts("a < b and c")) == BinaryOp(
        "and", BinaryOp("<", Var("a"), Var("b")), Var("c"))


def test_bitwise_binds_tighter_than_comparison(ts):
    """
    'a & b == c' masks before comparing.
    """
    assert parse_expression(ts("a & b == c")) == BinaryOp(
        "==", BinaryOp("&", Var("a"), Var("b")), Var("c"))


def test_bitwise_operators_bind_or_xor_and_from_loosest(ts):
    """
    'a | b ^ c & d' groups the mask, then the xor, then the or.
    """
    assert parse_expression(ts("a | b ^ c & d")) == BinaryOp(
        "|", Var("a"), BinaryOp("^", Var("b"), BinaryOp("&", Var("c"), Var("d"))))


def test_addition_binds_tighter_than_shift(ts):
    """
    '1 << 2 + 3' sums the shift amount first.
    """
    assert parse_expression(ts("1 << 2 + 3")) == BinaryOp(
        "<<", IntLiteral(1), BinaryOp("+", IntLiteral(2), IntLiteral(3)))


def test_power_binds_tighter_than_multiplication(ts):
    """
    '2 * 3 ** 2' groups the power first.
    """
    assert parse_expression(ts("2 * 3 ** 2")) == BinaryOp(
        "*", IntLiteral(2), BinaryOp("**", IntLiteral(3), IntLiteral(2)))


def test_power_folds_right(ts):
    """
    '2 ** 3 ** 2' groups from the right.
    """
    assert parse_expression(ts("2 ** 3 ** 2")) == BinaryOp(
        "**", IntLiteral(2), BinaryOp("**", IntLiteral(3), IntLiteral(2)))
