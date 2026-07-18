"""Tests that a string literal's content never reads as syntax."""

from siec.ast import ArrayLiteral, Call, Index, IntLiteral, StrLiteral
from siec.parser.expressions import parse_expression, parse_primary


def test_string_holding_a_bracket_is_not_an_array_literal(ts):
    """
    '"["' stays a string argument; it must not open an array literal.
    """
    assert parse_primary(ts('f("[")')) == Call("f", [StrLiteral("[")])


def test_string_holding_a_close_paren_is_not_the_calls(ts):
    """
    '")"' stays a string argument; it must not close the call.
    """
    assert parse_primary(ts('f(")")')) == Call("f", [StrLiteral(")")])


def test_string_holding_a_brace_is_not_an_aggregate(ts):
    """
    '"{"' and '"}"' stay string elements inside a literal.
    """
    assert parse_primary(ts('["{", "}"]')) == ArrayLiteral(
        [StrLiteral("{"), StrLiteral("}")])


def test_string_holding_an_operator_is_not_an_operator(ts):
    """
    A string with operator content must not extend a binary expression.
    """
    assert parse_expression(ts('f("+", "and", "as")')) == Call(
        "f", [StrLiteral("+"), StrLiteral("and"), StrLiteral("as")])


def test_bare_string_literal_takes_postfix_chains(ts):
    """
    A string literal is indexable like any other postfix base.
    """
    assert parse_primary(ts('";"[0]')) == Index(StrLiteral(";"), IntLiteral(0))
