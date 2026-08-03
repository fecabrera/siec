"""Parsing of anonymous and named lexically nested functions."""

import pytest

from siec.ast import ClosureExpr, Let, LocalFunction
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
