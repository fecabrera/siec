"""Tests for parsing index (subscript) expressions."""

import pytest

from siec.ast import BinaryOp, Call, Index, IntLiteral, Var
from siec.parser.expressions import parse_primary


def test_index(ts):
    """
    'name[expr]' parses to an Index over the variable.
    """
    assert parse_primary(ts("argv[0]")) == Index(Var("argv"), IntLiteral(0))


def test_index_chains(ts):
    """
    Consecutive subscripts nest left to right.
    """
    assert parse_primary(ts("m[1][2]")) == Index(
        Index(Var("m"), IntLiteral(1)), IntLiteral(2))


def test_index_applies_to_call_results(ts):
    """
    A call result may be subscripted.
    """
    assert parse_primary(ts("f()[0]")) == Index(Call("f", []), IntLiteral(0))


def test_index_takes_a_full_expression(ts):
    """
    The subscript may be any expression, including comparisons.
    """
    assert parse_primary(ts("a[i < j]")) == Index(
        Var("a"), BinaryOp("<", Var("i"), Var("j")))


def test_index_requires_a_close(ts):
    """
    A subscript missing its ']' raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match=r"expected '\]'"):
        parse_primary(ts("a[0;"))
