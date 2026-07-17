"""Tests for siec.lexer.rules.MultilineCommentRule."""

import pytest

from siec.lexer.cursor import Cursor
from siec.lexer.rules import MultilineCommentRule


def test_validator_requires_the_open_marker():
    """
    The validator accepts '/*' and rejects lone slashes or stars.
    """
    assert MultilineCommentRule().validate(Cursor("/* hi */"))
    assert not MultilineCommentRule().validate(Cursor("/ *"))


def test_parser_consumes_through_the_close():
    """
    The comment is consumed whole, leaving the cursor after '*/'.
    """
    cursor = Cursor("/* a b */x")
    assert MultilineCommentRule().parse(cursor) is None
    assert cursor.current() == "x"


def test_parser_counts_lines_inside():
    """
    Newlines inside the comment advance the line counter.
    """
    cursor = Cursor("/* a\nb\nc */x")
    MultilineCommentRule().parse(cursor)
    assert cursor.line == 3


def test_parser_requires_a_close():
    """
    An unclosed comment raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="unterminated multiline comment"):
        MultilineCommentRule().parse(Cursor("/* open"))


def test_parser_does_not_nest():
    """
    The first '*/' closes the comment, C-style.
    """
    cursor = Cursor("/* a /* b */x")
    MultilineCommentRule().parse(cursor)
    assert cursor.current() == "x"
