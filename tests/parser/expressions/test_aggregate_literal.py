"""Tests for parsing aggregate literal expressions."""

import pytest

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


def test_named_aggregate_literal(ts):
    """
    '{x = a, y = b}' parses names aligned with elements.
    """
    assert parse_primary(ts("{x = 1, y = 2}")) == AggregateLiteral(
        [IntLiteral(1), IntLiteral(2)], ["x", "y"])


def test_named_aggregate_values_are_full_expressions(ts):
    """
    A named field's value is any expression, calls and operators included.
    """
    assert parse_primary(ts("{x = f() + 1}")) == AggregateLiteral(
        [BinaryOp("+", Call("f", []), IntLiteral(1))], ["x"])


def test_named_and_positional_do_not_mix(ts):
    """
    A literal is all-positional or all-named.
    """
    with pytest.raises(SyntaxError):
        parse_primary(ts("{1, y = 2}"))
