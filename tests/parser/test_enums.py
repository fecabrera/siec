"""Tests for siec.parser.enums."""

import pytest

from siec.ast import BinaryOp, Enum, EnumMember, IntLiteral, Variant
from siec.parser.enums import parse_enum


def test_enum_declaration(ts):
    """
    Members without values parse to Variants with None.
    """
    assert parse_enum(ts("enum Color { RED, GREEN }")) == Enum(
        "Color", "i32", [Variant("RED"), Variant("GREEN")])


def test_enum_backing_type(ts):
    """
    ': T' after the name sets the backing type; the default is i32.
    """
    assert parse_enum(ts("enum Flags: u8 { POS }")).type == "u8"
    assert parse_enum(ts("enum Color { RED }")).type == "i32"


def test_enum_member_values(ts):
    """
    '= <value>' takes a full constant expression.
    """
    enum = parse_enum(ts("enum E { A = 5, B = A::X | 2 }"))
    assert enum.members == [
        Variant("A", IntLiteral(5)),
        Variant("B", BinaryOp("|", EnumMember("A", "X"), IntLiteral(2))),
    ]


def test_enum_trailing_comma(ts):
    """
    A trailing comma after the last member is allowed.
    """
    assert parse_enum(ts("enum E { A, B, }")).members == [
        Variant("A"), Variant("B")]


def test_enum_members_need_commas(ts):
    """
    Members are comma-separated; two names in a row are an error.
    """
    with pytest.raises(SyntaxError, match="expected ','"):
        parse_enum(ts("enum E { A B }"))
