"""Tests for siec.lexer.cursor."""

from siec.lexer.cursor import Cursor


def test_cursor_starts_at_the_beginning():
    """
    A new cursor sits at position zero on line one.
    """
    cursor = Cursor("ab")
    assert cursor.pos == 0
    assert cursor.line == 1


def test_at_end_reflects_remaining_input():
    """
    at_end() is false while characters remain and true afterwards.
    """
    cursor = Cursor("a")
    assert not cursor.at_end()
    cursor.advance()
    assert cursor.at_end()


def test_current_returns_the_character_at_the_cursor():
    """
    current() returns the character at the cursor position.
    """
    cursor = Cursor("ab")
    assert cursor.current() == "a"
    cursor.advance()
    assert cursor.current() == "b"


def test_starts_with_checks_at_the_cursor():
    """
    starts_with() matches text at the cursor, not at the start of the source.
    """
    cursor = Cursor("abc")
    assert cursor.starts_with("ab")
    cursor.advance()
    assert cursor.starts_with("bc")
    assert not cursor.starts_with("ab")


def test_advance_moves_by_count():
    """
    advance(n) moves the position forward n characters.
    """
    cursor = Cursor("abcd")
    cursor.advance(3)
    assert cursor.current() == "d"


def test_take_while_consumes_the_matching_run():
    """
    take_while() consumes and returns the run satisfying the predicate.
    """
    cursor = Cursor("123abc")
    assert cursor.take_while(str.isdigit) == "123"
    assert cursor.current() == "a"


def test_take_while_stops_at_the_end():
    """
    take_while() stops safely at the end of the source.
    """
    cursor = Cursor("123")
    assert cursor.take_while(str.isdigit) == "123"
    assert cursor.at_end()
