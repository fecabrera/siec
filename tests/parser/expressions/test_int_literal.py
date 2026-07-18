"""Tests for parsing integer literal expressions."""

from siec.ast import IntLiteral
from siec.parser.expressions import parse_primary


def test_int_literal(ts):
    """
    An int token parses to an IntLiteral node.
    """
    assert parse_primary(ts("42")) == IntLiteral(42)


def test_hex_literal(ts):
    """
    A hex token parses to its decimal value.
    """
    assert parse_primary(ts("0xFF")) == IntLiteral(255)


def test_negative_hex_literal_folds(ts):
    """
    '-' folds over a hex literal like a decimal one.
    """
    assert parse_primary(ts("-0x10")) == IntLiteral(-16)
