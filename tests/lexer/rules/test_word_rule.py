"""Tests for siec.lexer.rules.WordRule."""

from siec.lexer.cursor import Cursor
from siec.lexer.rules import WordRule


def test_validator_applies_at_letters_and_underscores():
    """
    The validator accepts word starters and rejects digits and symbols.
    """
    assert WordRule().validate(Cursor("a"))
    assert WordRule().validate(Cursor("_x"))
    assert not WordRule().validate(Cursor("9"))
    assert not WordRule().validate(Cursor("("))


def test_parser_classifies_keywords():
    """
    Reserved words parse as keyword tokens.
    """
    for word in ("fn", "return", "let", "if", "else", "and", "or", "not", "struct",
                 "true", "false", "as"):
        assert WordRule().parse(Cursor(word)).kind == "kw"


def test_parser_classifies_identifiers():
    """
    Unreserved words parse as identifiers.
    """
    assert WordRule().parse(Cursor("foo")).kind == "ident"


def test_parser_takes_the_whole_word():
    """
    Words include letters, digits, and underscores after the first character.
    """
    cursor = Cursor("a_b9 x")
    assert WordRule().parse(cursor).value == "a_b9"
    assert cursor.current() == " "
