"""Tests for parsing variable expressions."""

from siec.ast import Var
from siec.parser.expressions import parse_primary


def test_variable(ts):
    """
    A lone identifier parses to a Var node.
    """
    assert parse_primary(ts("x")) == Var("x")
