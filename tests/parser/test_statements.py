"""Tests for siec.parser.statements."""

import pytest

from siec.ast import (Assign, BinaryOp, Call, ExprStmt, If, Index, IndexAssign, IntLiteral,
                      Let, Member, MemberAssign, Return, Var)
from siec.parser.statements import parse_block, parse_statement


def test_statements_record_their_source_line(ts):
    """
    Each statement carries the line it starts on, for error reporting.
    """
    body = parse_block(ts("{\n  let a: i32 = 0;\n  a = 1;\n  return a;\n}"))
    assert [stmt.line for stmt in body] == [2, 3, 4]


def test_statement_line_is_excluded_from_equality(ts):
    """
    Two statements differing only in line still compare equal.
    """
    assert parse_statement(ts("\n\nreturn 1;")) == Return(IntLiteral(1))


def test_block_collects_statements_between_braces(ts):
    """
    A block gathers every statement up to the closing brace.
    """
    assert parse_block(ts("{ return 1; return 2; }")) == [
        Return(IntLiteral(1)), Return(IntLiteral(2))]


def test_block_may_be_empty(ts):
    """
    '{}' parses to an empty statement list.
    """
    assert parse_block(ts("{}")) == []


def test_block_requires_braces(ts):
    """
    A block not starting with '{' raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="expected '{'"):
        parse_block(ts("return 1;"))


def test_let_without_initializer(ts):
    """
    'let name: type;' parses to a Let with no value.
    """
    assert parse_statement(ts("let num: i32;")) == Let("num", "i32", None)


def test_let_with_initializer(ts):
    """
    'let name: type = expr;' parses the initializer expression.
    """
    assert parse_statement(ts("let num: i32 = 4;")) == Let("num", "i32", IntLiteral(4))


def test_let_with_pointer_type(ts):
    """
    Let declarations accept pointer type annotations.
    """
    assert parse_statement(ts('let msg: char* = "hi";')) is not None


def test_return_with_value(ts):
    """
    'return expr;' parses the value expression.
    """
    assert parse_statement(ts("return x;")) == Return(Var("x"))


def test_return_without_value(ts):
    """
    A bare 'return;' parses to a Return with no value.
    """
    assert parse_statement(ts("return;")) == Return(None)


def test_assignment(ts):
    """
    'name = expr;' parses to an Assign node.
    """
    assert parse_statement(ts("num = f();")) == Assign("num", Call("f", []))


def test_compound_assignment_desugars_to_a_binary_op(ts):
    """
    'name <op>= expr;' parses to 'name = name <op> expr'.
    """
    for op in ("+", "-", "*", "/", "%", "**", "<<", ">>", "&", "|", "^"):
        assert parse_statement(ts(f"num {op}= 2;")) == Assign(
            "num", BinaryOp(op, Var("num"), IntLiteral(2)))


def test_member_assignment(ts):
    """
    'base.field = expr;' parses to a MemberAssign over the base.
    """
    assert parse_statement(ts("p.x = 5;")) == MemberAssign(Var("p"), "x", IntLiteral(5))


def test_nested_member_assignment(ts):
    """
    A member target may itself be a chain of accesses.
    """
    assert parse_statement(ts("l.to.x = 5;")) == MemberAssign(
        Member(Var("l"), "to"), "x", IntLiteral(5))


def test_compound_member_assignment_desugars(ts):
    """
    'base.field <op>= expr;' desugars to 'base.field = base.field <op> expr'.
    """
    assert parse_statement(ts("p.x += 2;")) == MemberAssign(
        Var("p"), "x", BinaryOp("+", Member(Var("p"), "x"), IntLiteral(2)))


def test_block_statement(ts):
    """
    A bare '{ ... }' parses to a Block of its statements.
    """
    from siec.ast import Block

    assert parse_statement(ts("{ let x: i32 = 1; }")) == Block(
        [Let("x", "i32", IntLiteral(1))])


def test_empty_block_statement(ts):
    """
    '{ }' parses to a Block with no statements.
    """
    from siec.ast import Block

    assert parse_statement(ts("{ }")) == Block([])


def test_blocks_nest(ts):
    """
    A block statement may contain another block.
    """
    from siec.ast import Block

    assert parse_statement(ts("{ { f(); } }")) == Block(
        [Block([ExprStmt(Call("f", []))])])


def test_index_assignment(ts):
    """
    'base[i] = expr;' parses to an IndexAssign over the base.
    """
    assert parse_statement(ts("p[0] = 5;")) == IndexAssign(
        Var("p"), IntLiteral(0), IntLiteral(5))


def test_compound_index_assignment_desugars(ts):
    """
    'base[i] += v' desugars to 'base[i] = base[i] + v'.
    """
    assert parse_statement(ts("p[1] += 2;")) == IndexAssign(
        Var("p"), IntLiteral(1),
        BinaryOp("+", Index(Var("p"), IntLiteral(1)), IntLiteral(2)))


def test_member_of_indexed_element_assignment(ts):
    """
    'base[i].field = expr;' parses to a MemberAssign over the indexed base.
    """
    assert parse_statement(ts("p[0].x = 5;")) == MemberAssign(
        Index(Var("p"), IntLiteral(0)), "x", IntLiteral(5))


def test_invalid_assignment_target_is_an_error(ts):
    """
    Assigning to something that isn't a variable or field raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="invalid assignment target"):
        parse_statement(ts("f() = 5;"))


def test_expression_statement(ts):
    """
    A lone expression followed by ';' parses to an ExprStmt.
    """
    assert parse_statement(ts("f(1);")) == ExprStmt(Call("f", [IntLiteral(1)]))


def test_statement_requires_semicolon(ts):
    """
    A statement missing its ';' raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="expected ';'"):
        parse_statement(ts("return 1"))


def test_if_without_else(ts):
    """
    'if (cond) { ... }' parses condition and body with no else.
    """
    stmt = parse_statement(ts("if (a < b) { return 1; }"))
    assert stmt == If(BinaryOp("<", Var("a"), Var("b")), [Return(IntLiteral(1))], None)


def test_if_with_else(ts):
    """
    An 'else' block parses into the orelse list.
    """
    stmt = parse_statement(ts("if (x) { return 1; } else { return 2; }"))
    assert stmt.orelse == [Return(IntLiteral(2))]


def test_if_with_else_if_chain(ts):
    """
    'else if' nests the next If inside the orelse list.
    """
    stmt = parse_statement(ts("if (a) { } else if (b) { } else { return 3; }"))
    assert isinstance(stmt.orelse[0], If)
    assert stmt.orelse[0].orelse == [Return(IntLiteral(3))]


def test_if_condition_requires_parentheses(ts):
    """
    An if condition without parentheses raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match=r"expected '\('"):
        parse_statement(ts("if a { }"))
