"""Parsing tests for checked non-null pointer conversion."""

from siec.ast import Index, IntLiteral, UnaryOp, Var
from siec.parser.expressions import parse_expression


def test_postfix_bang_checks_a_pointer(ts):
    """Postfix bang binds before following postfix operations."""
    assert parse_expression(ts("pointer!")) == UnaryOp(
        "nonnull", Var("pointer"))
    assert parse_expression(ts("pointer![0]")) == Index(
        UnaryOp("nonnull", Var("pointer")), IntLiteral(0))
