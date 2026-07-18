"""Parsing of statements."""

from ..ast import (Assign, BinaryOp, Block, ExprStmt, If, Index, IndexAssign, Let, Member,
                   MemberAssign, Return, Var, While)
from .expressions import parse_expression
from .stream import TokenStream
from .types import parse_type

COMPOUND = {"+=", "-=", "*=", "/=", "%=", "**=", "<<=", ">>=", "&=", "|=", "^="}


def parse_block(ts: TokenStream) -> list:
    """
    Parse a brace-enclosed list of statements.
    """
    ts.expect("sym", "{")

    body = []
    while ts.peek().syntax != "}":
        body.append(parse_statement(ts))

    ts.expect("sym", "}")
    return body


def parse_statement(ts: TokenStream):
    """
    Parse a statement: a let, an if, a return, an assignment, or an expression.
    """
    tok = ts.peek()
    line = tok.line

    # 'if (cond) { ... }' with an optional 'else' block or 'else if' chain
    if tok.kind == "kw" and tok.value == "if":
        ts.next()

        ts.expect("sym", "(")
        condition = parse_expression(ts)
        ts.expect("sym", ")")

        body = parse_block(ts)

        orelse = None
        if ts.peek().syntax == "else":
            ts.next()
            orelse = [parse_statement(ts)] if ts.peek().syntax == "if" else parse_block(ts)

        return If(condition, body, orelse, line=line)

    # 'while (cond) { ... }' loops its block while the condition is truthy
    if tok.kind == "kw" and tok.value == "while":
        ts.next()

        ts.expect("sym", "(")
        condition = parse_expression(ts)
        ts.expect("sym", ")")

        return While(condition, parse_block(ts), line=line)

    # a bare '{' opens a block statement, a statement list in its own scope
    if tok.syntax == "{":
        return Block(parse_block(ts), line=line)

    # 'let name: type' with an optional '= <expr>' initializer
    if tok.kind == "kw" and tok.value == "let":
        ts.next()

        name = ts.expect("ident").value
        ts.expect("sym", ":")

        var_type = parse_type(ts)

        value = None
        if ts.peek().syntax == "=":
            ts.next()
            value = parse_expression(ts)

        ts.expect("sym", ";")
        return Let(name, var_type, value, line=line)

    # 'return' with an optional value expression before the ';'
    if tok.kind == "kw" and tok.value == "return":
        ts.next()

        value = None
        if ts.peek().syntax != ";":
            value = parse_expression(ts)

        ts.expect("sym", ";")
        return Return(value, line=line)

    # otherwise parse an expression; an assignment operator after it makes the
    # expression an assignment target, else it's evaluated for its effects
    expr = parse_expression(ts)

    if ts.peek().syntax == "=" or ts.peek().syntax in COMPOUND:
        op = ts.next().value

        value = parse_expression(ts)
        # a compound 'lvalue <op>= v' desugars to 'lvalue = lvalue <op> v'
        if op != "=":
            value = BinaryOp(op[:-1], expr, value)

        ts.expect("sym", ";")
        return make_assignment(expr, value, line)

    ts.expect("sym", ";")
    return ExprStmt(expr, line=line)


def make_assignment(target, value, line: int):
    """
    Build the assignment for an lvalue target: a variable, a struct field,
    or an indexed element.
    """
    if isinstance(target, Var):
        return Assign(target.name, value, line=line)

    if isinstance(target, Member):
        return MemberAssign(target.base, target.field, value, line=line)

    if isinstance(target, Index):
        return IndexAssign(target.base, target.index, value, line=line)

    raise SyntaxError(f"line {line}: invalid assignment target")
