"""Tests for primary-level parsing behaviors not tied to a single node:
grouping parentheses and the rejection of tokens that can't start an expression.
"""

import pytest

from siec.ast import BinaryOp, IntLiteral
from siec.parser.expressions import parse_expression, parse_primary


def test_parentheses_override_precedence(ts):
    """
    '(1 + 2) * 3' groups the sum first.
    """
    assert parse_expression(ts("(1 + 2) * 3")) == BinaryOp(
        "*", BinaryOp("+", IntLiteral(1), IntLiteral(2)), IntLiteral(3))


def test_parentheses_require_a_close(ts):
    """
    A group missing its ')' raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match=r"expected '\)'"):
        parse_expression(ts("(1 + 2;"))


def test_rejects_unexpected_token(ts):
    """
    A token that cannot start an expression raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="in expression"):
        parse_primary(ts(";"))
