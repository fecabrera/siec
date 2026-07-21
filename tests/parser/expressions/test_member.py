"""Tests for parsing member access expressions."""

from siec.ast import Call, Index, IntLiteral, Member, MethodCall, UnaryOp, Var
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


def test_arrow_desugars_to_a_dereferenced_member(ts):
    """
    'p->field' reaches through a pointer: '(*p).field' by another spelling.
    """
    assert parse_primary(ts("p->x")) == Member(UnaryOp("*", Var("p")), "x")


def test_arrow_chains(ts):
    """
    Consecutive '->' accesses nest left to right, dereferencing each link.
    """
    assert parse_primary(ts("node->next->value")) == Member(
        UnaryOp("*", Member(UnaryOp("*", Var("node")), "next")), "value")


def test_arrow_and_dot_mix(ts):
    """
    '->' and '.' chain together in source order.
    """
    assert parse_primary(ts("q.head->value")) == Member(
        UnaryOp("*", Member(Var("q"), "head")), "value")


def test_arrow_call_is_a_method_on_the_dereferenced_receiver(ts):
    """
    'p->init(x)' calls the method on the struct the pointer points at.
    """
    assert parse_primary(ts("p->init(5)")) == MethodCall(
        UnaryOp("*", Var("p")), "init", [IntLiteral(5)])
