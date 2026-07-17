"""Tests for parsing member access expressions."""

from siec.ast import Call, Index, IntLiteral, Member, Var
from siec.parser.expressions import parse_primary


def test_member_access(ts):
    """
    'base.field' parses to a Member over the base.
    """
    assert parse_primary(ts("p.x")) == Member(Var("p"), "x")


def test_member_access_chains(ts):
    """
    Consecutive '.field' accesses nest left to right.
    """
    assert parse_primary(ts("l.from.x")) == Member(Member(Var("l"), "from"), "x")


def test_member_and_index_mix(ts):
    """
    Member access and subscripts chain together in source order.
    """
    assert parse_primary(ts("a.b[0].c")) == Member(
        Index(Member(Var("a"), "b"), IntLiteral(0)), "c")


def test_member_applies_to_call_results(ts):
    """
    A call result may have a field selected from it.
    """
    assert parse_primary(ts("make().x")) == Member(Call("make", []), "x")
