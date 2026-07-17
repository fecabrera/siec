"""Tests for siec.parser.stream."""

import pytest

from siec.lexer import Token, lex
from siec.parser.stream import TokenStream


def test_init_positions_at_first_token(ts):
    """
    A new stream starts at position zero on the first token.
    """
    stream = ts("a b")
    assert stream.pos == 0
    assert stream.peek().value == "a"


def test_peek_does_not_consume(ts):
    """
    Repeated peeks return the same token without advancing.
    """
    stream = ts("a b")
    assert stream.peek().value == "a"
    assert stream.peek().value == "a"


def test_peek_with_offset_looks_ahead(ts):
    """
    peek(n) returns the token n places ahead without advancing.
    """
    stream = ts("a b c")
    assert stream.peek(1).value == "b"
    assert stream.peek(2).value == "c"
    assert stream.peek().value == "a"


def test_peek_clamps_to_eof(ts):
    """
    Peeking past the end returns the eof token instead of failing.
    """
    stream = ts("a")
    assert stream.peek(99).kind == "eof"


def test_next_consumes_in_order(ts):
    """
    next() returns tokens in order, ending at eof.
    """
    stream = ts("a b")
    assert stream.next().value == "a"
    assert stream.next().value == "b"
    assert stream.next().kind == "eof"


def test_expect_returns_matching_token(ts):
    """
    expect() consumes and returns the token when kind and value match.
    """
    stream = ts("fn foo")
    assert stream.expect("kw", "fn").value == "fn"
    assert stream.expect("ident").value == "foo"


def test_expect_rejects_wrong_kind(ts):
    """
    expect() raises a SyntaxError naming what it wanted and what it got.
    """
    with pytest.raises(SyntaxError, match="expected 'int', got 'foo'"):
        ts("foo").expect("int")


def test_expect_rejects_wrong_value_with_line(ts):
    """
    expect() reports the offending token's line number.
    """
    with pytest.raises(SyntaxError, match="line 2: expected 'fn'"):
        stream = ts("\nlet")
        stream.expect("kw", "fn")
