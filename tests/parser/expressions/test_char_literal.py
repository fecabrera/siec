"""Tests for parsing char literal expressions."""

from siec.ast import BinaryOp, CharLiteral, Var
from siec.parser.expressions import parse_expression, parse_primary


def test_char_literal(ts):
    """
    A char token parses to a CharLiteral node.
    """
    assert parse_primary(ts("'a'")) == CharLiteral("a")


def test_char_content_is_never_syntax(ts):
    """
    A char holding a syntax character stays data: 'c == '}'' compares.
    """
    assert parse_expression(ts("c == '}'")) == BinaryOp(
        "==", Var("c"), CharLiteral("}"))
