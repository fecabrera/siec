"""Tests for parsing aggregate literal expressions."""

from siec.ast import AggregateLiteral, BinaryOp, Call, IntLiteral, Var
from siec.parser.expressions import parse_primary


def test_aggregate_literal(ts):
    """
    '{a, b}' parses to an AggregateLiteral of its element expressions.
    """
    assert parse_primary(ts("{ptr, n}")) == AggregateLiteral([Var("ptr"), Var("n")])


def test_empty_aggregate_literal(ts):
    """
    '{}' parses to an AggregateLiteral with no elements.
    """
    assert parse_primary(ts("{}")) == AggregateLiteral([])


def test_aggregate_elements_are_full_expressions(ts):
    """
    Aggregate elements may be any expression, including calls and arithmetic.
    """
    assert parse_primary(ts("{f(), n + 1}")) == AggregateLiteral(
        [Call("f", []), BinaryOp("+", Var("n"), IntLiteral(1))])
