"""Tests for siec.lexer.rules.LineCommentRule."""

from siec.lexer.cursor import Cursor
from siec.lexer.rules import LineCommentRule


def test_validator_requires_two_slashes():
    """
    The validator accepts '//' and nothing shorter.
    """
    assert LineCommentRule().validate(Cursor("// hi"))
    assert not LineCommentRule().validate(Cursor("/ hi"))


def test_parser_stops_before_the_newline():
    """
    The comment is consumed up to, but not including, the newline.
    """
    cursor = Cursor("// hi\nx")
    assert LineCommentRule().parse(cursor) is None
    assert cursor.current() == "\n"


def test_parser_consumes_to_the_end_without_a_newline():
    """
    A comment on the last line consumes the rest of the source.
    """
    cursor = Cursor("// hi")
    LineCommentRule().parse(cursor)
    assert cursor.at_end()
