"""Tests for parsing the 'null' literal."""

from siec.ast import BinaryOp, NullLiteral, Var
from siec.parser.expressions import parse_expression, parse_primary


def test_null_literal(ts):
    """
    The 'null' keyword parses to a NullLiteral.
    """
    assert parse_primary(ts("null")) == NullLiteral()


def test_null_composes_in_expressions(ts):
    """
    'null' sits inside expressions like any literal.
    """
    assert parse_expression(ts("p == null")) == BinaryOp(
        "==", Var("p"), NullLiteral())
