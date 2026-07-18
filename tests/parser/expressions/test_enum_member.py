"""Tests for parsing 'A::member' enum member expressions."""

from siec.ast import BinaryOp, EnumMember, IntLiteral
from siec.parser.expressions import parse_expression


def test_enum_member(ts):
    """
    'A::B' parses to an EnumMember node.
    """
    assert parse_expression(ts("Color::RED")) == EnumMember("Color", "RED")


def test_enum_member_in_expressions(ts):
    """
    Members participate in operators like any other primary.
    """
    assert parse_expression(ts("Flags::INF | 1")) == BinaryOp(
        "|", EnumMember("Flags", "INF"), IntLiteral(1))
