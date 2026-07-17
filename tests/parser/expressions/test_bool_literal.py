"""Tests for parsing boolean literal expressions."""

from siec.ast import BoolLiteral
from siec.parser.expressions import parse_primary


def test_boolean_literals(ts):
    """
    'true' and 'false' parse to BoolLiteral nodes.
    """
    assert parse_primary(ts("true")) == BoolLiteral(True)
    assert parse_primary(ts("false")) == BoolLiteral(False)
