"""Tests for parsing integer literal expressions."""

from siec.ast import IntLiteral
from siec.parser.expressions import parse_primary


def test_int_literal(ts):
    """
    An int token parses to an IntLiteral node.
    """
    assert parse_primary(ts("42")) == IntLiteral(42)
