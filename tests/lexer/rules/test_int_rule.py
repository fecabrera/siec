"""Tests for siec.lexer.rules.IntRule."""

from siec.lexer.cursor import Cursor
from siec.lexer.rules import IntRule


def test_validator_applies_at_a_digit():
    """
    The validator accepts digits and rejects other characters.
    """
    assert IntRule().validate(Cursor("7"))
    assert not IntRule().validate(Cursor("x"))


def test_parser_takes_the_digit_run():
    """
    The parser consumes exactly the run of digits.
    """
    cursor = Cursor("123x")
    token = IntRule().parse(cursor)
    assert token.kind == "int"
    assert token.value == "123"
    assert cursor.current() == "x"
