"""Tests for parsing string literal expressions."""

from siec.ast import StrLiteral
from siec.parser.expressions import parse_primary


def test_str_literal(ts):
    """
    A str token parses to a StrLiteral node.
    """
    assert parse_primary(ts('"hi"')) == StrLiteral("hi")
