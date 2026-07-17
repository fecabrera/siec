"""Tests for siec.lexer.rules.SymbolRule."""

from siec.lexer.cursor import Cursor
from siec.lexer.rules import SymbolRule


def test_validator_accepts_single_and_multi_character_symbols():
    """
    The validator accepts both symbol forms and rejects other characters.
    """
    assert SymbolRule().validate(Cursor("("))
    assert SymbolRule().validate(Cursor("->"))
    assert not SymbolRule().validate(Cursor("a"))


def test_validator_accepts_multi_character_symbols_with_no_single_prefix():
    """
    Symbols like '->' validate even though '-' alone is not a symbol.
    """
    assert SymbolRule().validate(Cursor("->"))
    assert SymbolRule().validate(Cursor("-"))


def test_parser_prefers_multi_character_symbols():
    """
    '==' lexes as one symbol rather than two '=' tokens.
    """
    cursor = Cursor("==")
    token = SymbolRule().parse(cursor)
    assert token.value == "=="
    assert cursor.at_end()


def test_parser_takes_each_multi_character_symbol_whole():
    """
    Every multi-character symbol parses as itself.
    """
    for sym in ("->", "...", "==", "!=", "<=", ">=", "**", "<<", ">>",
                "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
                "**=", "<<=", ">>="):
        assert SymbolRule().parse(Cursor(sym)).value == sym


def test_parser_prefers_the_longest_symbol():
    """
    '**=' lexes as one symbol rather than '**' plus '='.
    """
    cursor = Cursor("**=")
    token = SymbolRule().parse(cursor)
    assert token.value == "**="
    assert cursor.at_end()


def test_parser_takes_single_characters():
    """
    A lone symbol character lexes as itself.
    """
    for sym in "(){}[];,:+-*/%@=<>&|^~":
        assert SymbolRule().parse(Cursor(sym)).value == sym
