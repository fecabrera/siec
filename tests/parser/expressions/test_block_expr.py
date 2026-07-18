"""Tests for parsing block expressions and their aggregate disambiguation."""

from siec.ast import (AggregateLiteral, BlockExpr, Call, Emit, ExprStmt, IntLiteral, Let,
                      Var)
from siec.parser.expressions import parse_primary
from siec.parser.statements import parse_statement


def test_braces_with_statements_parse_as_a_block_expression(ts):
    """
    '{ ...; emit v; }' in expression position parses to a BlockExpr.
    """
    assert parse_primary(ts("{ let x: i32 = 1; emit x; }")) == BlockExpr(
        [Let("x", "i32", IntLiteral(1)), Emit(Var("x"))])


def test_braces_with_commas_stay_an_aggregate(ts):
    """
    '{a, b}' keeps parsing as an aggregate literal.
    """
    assert parse_primary(ts("{ptr, n}")) == AggregateLiteral([Var("ptr"), Var("n")])


def test_empty_braces_stay_an_aggregate(ts):
    """
    '{}' keeps parsing as the empty aggregate.
    """
    assert parse_primary(ts("{}")) == AggregateLiteral([])


def test_single_expression_with_semicolon_is_a_block(ts):
    """
    A ';' after the first expression makes the braces a block, not a literal.
    """
    assert parse_primary(ts("{ f(); emit 1; }")) == BlockExpr(
        [ExprStmt(Call("f", [])), Emit(IntLiteral(1))])


def test_emit_statement(ts):
    """
    'emit expr;' parses to an Emit statement.
    """
    assert parse_statement(ts("emit x + 1;")) is not None
    assert parse_statement(ts("emit 5;")) == Emit(IntLiteral(5))


def test_block_expression_initializes_a_let(ts):
    """
    A block expression sits anywhere an expression does, a let initializer say.
    """
    stmt = parse_statement(ts("let a: i32 = { emit 1; };"))
    assert stmt == Let("a", "i32", BlockExpr([Emit(IntLiteral(1))]))
