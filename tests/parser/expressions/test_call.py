"""Tests for parsing call expressions."""

from siec.ast import BinaryOp, Call, IntLiteral, StrLiteral, Var
from siec.parser.expressions import parse_expression, parse_primary


def test_call_without_arguments(ts):
    """
    An identifier followed by '()' parses to a Call with no arguments.
    """
    assert parse_primary(ts("f()")) == Call("f", [])


def test_call_with_arguments(ts):
    """
    Call arguments parse as comma-separated expressions of any kind.
    """
    assert parse_primary(ts('f(1, x, "s")')) == Call(
        "f", [IntLiteral(1), Var("x"), StrLiteral("s")])


def test_nested_calls(ts):
    """
    A call may appear as another call's argument.
    """
    assert parse_primary(ts("f(g(1))")) == Call("f", [Call("g", [IntLiteral(1)])])


def test_call_arguments_may_be_comparisons(ts):
    """
    Full expressions, including comparisons, are allowed as call arguments.
    """
    assert parse_expression(ts("f(a < b)")) == Call(
        "f", [BinaryOp("<", Var("a"), Var("b"))])
