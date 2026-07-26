"""Tests for '@sizeof' parsing."""

import pytest

from siec.ast import BinaryOp, IntLiteral, SizeOf
from siec.parser.expressions import parse_expression


def test_sizeof_takes_a_type_or_name(ts):
    """
    The parentheses hold a type expression; a bare name is one too.
    """
    assert parse_expression(ts("@sizeof(i32)")) == SizeOf("i32")
    assert parse_expression(ts("@sizeof(c)")) == SizeOf("c")
    assert parse_expression(ts("@sizeof(char[])")) == SizeOf("char[]")
    assert parse_expression(ts("@sizeof(u8**)")) == SizeOf("u8**")
    assert parse_expression(ts("@sizeof(fn(i32) -> i32)")) == SizeOf("fn(i32)->i32")


def test_sizeof_composes_like_a_value(ts):
    """
    sizeof sits inside expressions like any primary.
    """
    assert parse_expression(ts("@sizeof(i32) * 2")) == BinaryOp(
        "*", SizeOf("i32"), IntLiteral(2))


def test_sizeof_requires_a_closing_paren(ts):
    """
    The parentheses are mandatory and must close.
    """
    with pytest.raises(SyntaxError, match="expected ';'|expected ','|expected '\\)'"):
        parse_expression(ts("@sizeof(i32"))


def test_the_bare_word_says_how_it_is_spelled(ts):
    """
    A size is a compile-time function, so the old bare 'sizeof(T)' names
    its spelling rather than reading as a call to an undeclared function.
    """
    with pytest.raises(SyntaxError, match=r"spelled '@sizeof\(T\)'"):
        parse_expression(ts("sizeof(i32)"))
