"""Tests for siec.lexer.rules.WhitespaceRule."""

from siec.lexer.cursor import Cursor
from siec.lexer.rules import WhitespaceRule


def test_validator_accepts_whitespace_only():
    """
    The validator accepts whitespace and rejects other characters.
    """
    assert WhitespaceRule().validate(Cursor(" "))
    assert WhitespaceRule().validate(Cursor("\t"))
    assert WhitespaceRule().validate(Cursor("\n"))
    assert not WhitespaceRule().validate(Cursor("a"))


def test_parser_produces_no_token():
    """
    Parsing whitespace consumes one character and yields nothing.
    """
    cursor = Cursor("  ")
    assert WhitespaceRule().parse(cursor) is None
    assert cursor.pos == 1


def test_parser_counts_newlines():
    """
    Parsing a newline advances the line counter.
    """
    cursor = Cursor("\n")
    WhitespaceRule().parse(cursor)
    assert cursor.line == 2


def test_parser_leaves_the_line_alone_for_plain_spaces():
    """
    Non-newline whitespace does not touch the line counter.
    """
    cursor = Cursor(" ")
    WhitespaceRule().parse(cursor)
    assert cursor.line == 1
