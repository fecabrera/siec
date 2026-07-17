"""Tests for siec.lexer.rules.StringRule."""

import pytest

from siec.lexer.cursor import Cursor
from siec.lexer.rules import StringRule


def test_validator_applies_at_a_quote():
    """
    The validator accepts a double quote and rejects other characters.
    """
    assert StringRule().validate(Cursor('"hi"'))
    assert not StringRule().validate(Cursor("hi"))


def test_parser_takes_the_quoted_text():
    """
    The parser returns the text between the quotes and consumes the close.
    """
    cursor = Cursor('"hi" x')
    token = StringRule().parse(cursor)
    assert token.kind == "str"
    assert token.value == "hi"
    assert cursor.current() == " "


def test_parser_decodes_all_simple_escapes():
    """
    Every simple gcc escape decodes to its control or literal character.
    """
    token = StringRule().parse(Cursor(r'"\a\b\e\f\n\r\t\v\\\'\"\?"'))
    assert token.value == "\a\b\x1b\f\n\r\t\v\\'\"?"


def test_parser_decodes_octal_escapes():
    """
    Octal escapes take one to three digits, stopping at the first non-digit.
    """
    token = StringRule().parse(Cursor(r'"\0\101\78"'))
    assert token.value == "\0A\a8"


def test_parser_rejects_out_of_range_octal():
    """
    An octal escape above 255 raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="octal escape sequence out of range"):
        StringRule().parse(Cursor(r'"\777"'))


def test_parser_decodes_hex_escapes():
    """
    Hex escapes consume every following hex digit.
    """
    token = StringRule().parse(Cursor(r'"\x41\x0ag"'))
    assert token.value == "A\ng"


def test_parser_rejects_hex_without_digits():
    """
    '\\x' with no hex digits raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match=r"\\x used with no following hex digits"):
        StringRule().parse(Cursor(r'"\xg"'))


def test_parser_rejects_out_of_range_hex():
    """
    A hex escape above 255 raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="hex escape sequence out of range"):
        StringRule().parse(Cursor(r'"\x100"'))


def test_parser_decodes_universal_character_names():
    """
    '\\u' takes four hex digits and '\\U' eight.
    """
    token = StringRule().parse(Cursor('"\\u00e9\\U0001F600"'))
    assert token.value == "é\U0001F600"


def test_parser_rejects_incomplete_universal_names():
    """
    A universal character name with too few digits raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="incomplete universal character name"):
        StringRule().parse(Cursor(r'"\u00e"'))


def test_parser_rejects_out_of_range_universal_names():
    """
    A universal character name beyond U+10FFFF raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="invalid universal character"):
        StringRule().parse(Cursor(r'"\UFFFFFFFF"'))


def test_parser_rejects_unknown_escapes():
    """
    An unsupported escape raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="unknown escape"):
        StringRule().parse(Cursor(r'"\q"'))


def test_parser_requires_a_close():
    """
    A string missing its closing quote raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="unterminated string"):
        StringRule().parse(Cursor('"open'))


def test_parser_rejects_a_newline_inside():
    """
    A string interrupted by a newline raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="unterminated string"):
        StringRule().parse(Cursor('"a\nb"'))
