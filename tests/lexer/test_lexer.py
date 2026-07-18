"""Tests for siec.lexer."""

import pytest

from siec.lexer import lex


def kinds(source):
    """
    Lex source and return (kind, value) pairs, dropping the trailing eof.
    """
    return [(t.kind, t.value) for t in lex(source)[:-1]]


def test_empty_source_yields_only_eof():
    """
    Lexing an empty string produces just the eof token.
    """
    tokens = lex("")
    assert len(tokens) == 1
    assert tokens[0].kind == "eof"


def test_keywords_and_identifiers():
    """
    Words are split into keywords and identifiers, including underscores and digits.
    """
    assert kinds("fn foo let x if else return _under score9") == [
        ("kw", "fn"), ("ident", "foo"), ("kw", "let"), ("ident", "x"),
        ("kw", "if"), ("kw", "else"), ("kw", "return"),
        ("ident", "_under"), ("ident", "score9"),
    ]


def test_integer_literals():
    """
    Runs of digits lex as int tokens.
    """
    assert kinds("0 42 1234") == [("int", "0"), ("int", "42"), ("int", "1234")]


def test_hex_literals():
    """
    '0x' followed by hex digits lexes as one int token, prefix kept.
    """
    assert kinds("0xFF 0x1f 0X10") == [
        ("int", "0xFF"),
        ("int", "0x1f"),
        ("int", "0x10"),  # '0X' normalizes to '0x'
    ]


def test_hex_prefix_requires_digits():
    """
    '0x' with no hex digits raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="no hex digits"):
        lex("0x;")


def test_float_literals():
    """
    Digits with a '.digits' fraction lex as one float token.
    """
    assert kinds("1.5 0.25 3.14159") == [
        ("float", "1.5"),
        ("float", "0.25"),
        ("float", "3.14159"),
    ]


def test_dot_without_digits_stays_a_member_access(ts=None):
    """
    A '.' not followed by a digit leaves the int alone: 'a.b' member syntax.
    """
    assert kinds("1.x 5.") == [
        ("int", "1"),
        ("sym", "."),
        ("ident", "x"),
        ("int", "5"),
        ("sym", "."),
    ]


def test_single_character_symbols():
    """
    Each supported single-character symbol lexes as its own sym token.
    """
    assert kinds("(){}[];,:+-*/%@=<>?") == [("sym", s) for s in "(){}[];,:+-*/%@=<>?"]


def test_multi_character_symbols():
    """
    Multi-character symbols lex as one token, not their constituent characters.
    """
    assert kinds("-> ... == != <= >=") == [
        ("sym", "->"), ("sym", "..."), ("sym", "=="),
        ("sym", "!="), ("sym", "<="), ("sym", ">="),
    ]


def test_string_literal_with_escapes():
    """
    String literals decode every supported escape sequence.
    """
    tokens = lex(r'"a\n\t\r\0\\\" b"')
    assert tokens[0].kind == "str"
    assert tokens[0].value == 'a\n\t\r\0\\" b'


def test_unknown_escape_is_an_error():
    """
    An unsupported escape sequence raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="unknown escape"):
        lex(r'"\q"')


def test_unterminated_string_is_an_error():
    """
    A string missing its closing quote raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="unterminated string"):
        lex('"never closed')


def test_line_comments_are_skipped():
    """
    '//' comments produce no tokens up to the end of the line.
    """
    assert kinds("1 // comment 2\n3") == [("int", "1"), ("int", "3")]


def test_multiline_comments_are_skipped():
    """
    '/* */' comments produce no tokens, even across lines and comment markers.
    """
    assert kinds("1 /* 2\n // * \n */ 3") == [("int", "1"), ("int", "3")]


def test_unterminated_multiline_comment_is_an_error():
    """
    A multiline comment missing its '*/' raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="unterminated multiline comment"):
        lex("/* never closed")


def test_line_numbers_advance_through_newlines_and_comments():
    """
    Tokens carry line numbers that count newlines inside comments too.
    """
    tokens = lex("a\nb /* x\ny */ c\nd")
    lines = {t.value: t.line for t in tokens[:-1]}
    assert lines == {"a": 1, "b": 2, "c": 3, "d": 4}


def test_unexpected_character_is_an_error():
    """
    A character outside the language raises a SyntaxError with its line.
    """
    with pytest.raises(SyntaxError, match=r"line 2: unexpected character '\$'"):
        lex("ok\n$")


def test_char_literals():
    """
    One character between single quotes lexes as a char token.
    """
    assert kinds("'a' '\\n' '\\x41' '{'") == [
        ("char", "a"),
        ("char", "\n"),
        ("char", "A"),
        ("char", "{"),
    ]


def test_char_literal_must_hold_one_character():
    """
    Empty and multi-character char literals are errors.
    """
    with pytest.raises(SyntaxError, match="empty char literal"):
        lex("''")

    with pytest.raises(SyntaxError, match="exactly one character"):
        lex("'ab'")

    with pytest.raises(SyntaxError, match="single byte"):
        lex("'é'")


def test_unterminated_char_literal_is_an_error():
    """
    A char literal missing its closing quote raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="unterminated char literal"):
        lex("'a")
