"""Tests for parsing 'cond ? then : orelse' expressions."""

from siec.ast import BinaryOp, IntLiteral, Slice, Ternary, Var
from siec.parser.expressions import parse_expression


def test_ternary(ts):
    """
    '?' and ':' split condition, then, and else.
    """
    assert parse_expression(ts("c ? a : b")) == Ternary(
        Var("c"), Var("a"), Var("b"))


def test_ternary_binds_loosest(ts):
    """
    The condition takes the whole binary expression before '?'.
    """
    assert parse_expression(ts("a > 1 ? x + 1 : y * 2")) == Ternary(
        BinaryOp(">", Var("a"), IntLiteral(1)),
        BinaryOp("+", Var("x"), IntLiteral(1)),
        BinaryOp("*", Var("y"), IntLiteral(2)))


def test_ternary_nests_right(ts):
    """
    'a ? b : c ? d : e' chains in the else arm, C-style.
    """
    assert parse_expression(ts("a ? b : c ? d : e")) == Ternary(
        Var("a"), Var("b"), Ternary(Var("c"), Var("d"), Var("e")))


def test_slices_keep_their_colon(ts):
    """
    A ':' after a plain expression inside brackets still slices.
    """
    assert parse_expression(ts("arr[a:b]")) == Slice(Var("arr"), Var("a"), Var("b"))
