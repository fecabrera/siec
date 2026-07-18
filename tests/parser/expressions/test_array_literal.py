"""Tests for parsing array literal expressions."""

from siec.ast import ArrayLiteral, BinaryOp, Call, IntLiteral, Var
from siec.parser.expressions import parse_primary


def test_array_literal(ts):
    """
    '[a, b]' parses to an ArrayLiteral of its element expressions.
    """
    assert parse_primary(ts("[1, 2, 3]")) == ArrayLiteral(
        [IntLiteral(1), IntLiteral(2), IntLiteral(3)])


def test_empty_array_literal(ts):
    """
    '[]' parses to an ArrayLiteral with no elements.
    """
    assert parse_primary(ts("[]")) == ArrayLiteral([])


def test_array_elements_are_full_expressions(ts):
    """
    Array elements may be any expression, including calls and arithmetic.
    """
    assert parse_primary(ts("[f(), n + 1]")) == ArrayLiteral(
        [Call("f", []), BinaryOp("+", Var("n"), IntLiteral(1))])


def test_array_literal_allows_a_trailing_comma(ts):
    """
    A comma may follow the last element.
    """
    assert parse_primary(ts("[1, 2,]")) == ArrayLiteral(
        [IntLiteral(1), IntLiteral(2)])
