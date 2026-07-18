"""Tests for parsing 'case ... when' statements."""

import pytest

from siec.ast import Assign, Case, IntLiteral, Return, Var, When
from siec.parser.statements import parse_statement


def test_case_with_arms(ts):
    """
    Each 'when value:' opens an arm holding the statements that follow.
    """
    stmt = parse_statement(ts("""
    case (x) {
        when 1: return 1;
        when 2: return 2;
    }
    """))
    assert stmt == Case(Var("x"), [
        When([IntLiteral(1)], [Return(IntLiteral(1))]),
        When([IntLiteral(2)], [Return(IntLiteral(2))]),
    ])


def test_when_takes_several_values(ts):
    """
    'when a, b, c:' lists the values that all select one arm.
    """
    stmt = parse_statement(ts("case (x) { when 1, 2, 3: return 1; }"))
    assert stmt.arms[0].values == [IntLiteral(1), IntLiteral(2), IntLiteral(3)]


def test_case_else_is_optional(ts):
    """
    'else:' collects the unmatched path; without it, orelse is None.
    """
    stmt = parse_statement(ts("case (x) { when 1: y = 1; else: y = 2; }"))
    assert stmt.orelse == [Assign("y", IntLiteral(2))]

    assert parse_statement(ts("case (x) { when 1: y = 1; }")).orelse is None


def test_arm_bodies_take_several_statements(ts):
    """
    An arm runs everything up to the next 'when', 'else', or brace.
    """
    stmt = parse_statement(ts("""
    case (x) {
        when 1:
            y = 1;
            y = 2;
        when 2: y = 3;
    }
    """))
    assert len(stmt.arms[0].body) == 2
    assert len(stmt.arms[1].body) == 1


def test_else_must_be_last(ts):
    """
    A 'when' after the else arm is rejected.
    """
    with pytest.raises(SyntaxError, match="'else' must be the last arm"):
        parse_statement(ts("case (x) { else: y = 1; when 2: y = 2; }"))


def test_arms_must_be_when_or_else(ts):
    """
    A case body holds only arms.
    """
    with pytest.raises(SyntaxError, match="expected 'when' or 'else'"):
        parse_statement(ts("case (x) { y = 1; }"))
