"""Parsing of anonymous and named lexically nested functions."""

import pytest

from siec.ast import ClosureExpr, ExprStmt, Let, LocalFunction, Return
from siec.lexer import lex
from siec.parser import parse


def test_anonymous_function_expression():
    program = parse(lex("fn main() { let callback = () => {}; }"))
    statement = program.functions[0].body[0]
    assert isinstance(statement, Let)
    assert isinstance(statement.value, ClosureExpr)


def test_named_nested_function():
    program = parse(lex("fn main() { fn callback() {} }"))
    statement = program.functions[0].body[0]
    assert isinstance(statement, LocalFunction)
    assert statement.name == "callback"


def test_closure_function_type():
    program = parse(lex("fn invoke(callback: closure fn()) {}"))
    assert program.functions[0].params[0].type == "closure fn()"


def test_anonymous_fn_declaration_syntax_is_not_a_closure():
    with pytest.raises(SyntaxError):
        parse(lex("fn main() { let callback = fn() {}; }"))


def test_let_binds_a_typed_arrow_closure():
    program = parse(lex(
        "fn main() { let f = (a: i32, b: i32) => { a + b; }; }"))
    statement = program.functions[0].body[0]
    assert isinstance(statement, Let)
    assert isinstance(statement.value, ClosureExpr)
    assert [param.type for param in statement.value.params] == ["i32", "i32"]
    assert statement.value.return_type is None


def test_let_binds_an_expression_bodied_arrow_closure():
    program = parse(lex(
        "fn main() { let f = (a: i32, b: i32) => a + b; }"))
    statement = program.functions[0].body[0]
    assert isinstance(statement, Let)
    assert isinstance(statement.value, ClosureExpr)
    assert len(statement.value.body) == 1
    assert isinstance(statement.value.body[0], ExprStmt)
    assert statement.value.return_type is None


def test_expression_bodied_arrow_closure_returns_when_annotated():
    program = parse(lex(
        "fn main() { let f = (a: i32, b: i32) -> i32 => a + b; }"))
    statement = program.functions[0].body[0]
    assert isinstance(statement.value, ClosureExpr)
    assert statement.value.return_type == "i32"
    assert isinstance(statement.value.body[0], Return)
