"""Tests for parsing 'try <call> except (e) { ... }' and its '??' shorthand."""

import pytest

from siec.ast import (Assign, BinaryOp, Call, Emit, ExprStmt, IntLiteral, Let,
                      MethodCall, Return, Try, Var)
from siec.parser.expressions import parse_primary
from siec.parser.statements import parse_statement


def test_try_parses_its_call_and_its_arm(ts):
    """
    'try f() except (e) { ... }' parses to a Try over the call, naming
    the binding its arm takes.
    """
    assert parse_primary(ts("try f() except (e) { return 1; }")) == Try(
        Call("f", []), "e", [Return(IntLiteral(1))])


def test_try_takes_a_method_call(ts):
    """
    A receiver that isn't a name chain folds into a MethodCall, which a
    'try' unwraps like any other call.
    """
    assert parse_primary(ts("try get(0).read() except (e) { emit 0; }")) == Try(
        MethodCall(Call("get", [IntLiteral(0)]), "read", [], None), "e",
        [Emit(IntLiteral(0))])


def test_try_records_its_source_line(ts):
    """
    The 'try' carries the line it starts on, for error reporting.
    """
    assert parse_primary(ts("\n\ntry f() except (e) { return 1; }")).line == 3


def test_try_rejects_anything_but_a_call(ts):
    """
    A result already in a variable was there to be checked; a 'try'
    unwraps what a call just handed back.
    """
    with pytest.raises(SyntaxError, match="'try' takes a call"):
        parse_primary(ts("try res except (e) { return 1; }"))

    with pytest.raises(SyntaxError, match="'try' takes a call"):
        parse_primary(ts("try 1 + 2 except (e) { return 1; }"))


def test_try_needs_an_arm_one_way_or_the_other(ts):
    """
    The arm is part of the form: nothing unwraps without one, spelled
    'except' or '??'.
    """
    with pytest.raises(SyntaxError, match="expected 'except'"):
        parse_primary(ts("try f();"))


def test_a_try_statement_needs_no_semicolon(ts):
    """
    The arm's '}' closes the statement, the way an if's body does: as a
    let's value, an assignment's, a return's, or on its own.
    """
    arm = Try(Call("f", []), "e", [Return(IntLiteral(1))])

    assert parse_statement(ts("let v = try f() except (e) { return 1; }")) == \
        Let("v", None, arm)
    assert parse_statement(ts("let v: i32 = try f() except (e) { return 1; }")) == \
        Let("v", "i32", arm)
    assert parse_statement(ts("v = try f() except (e) { return 1; }")) == \
        Assign("v", arm)
    assert parse_statement(ts("try f() except (e) { return 1; }")) == ExprStmt(arm)
    assert parse_statement(ts("return try f() except (e) { return 1; }")) == \
        Return(arm)
    assert parse_statement(ts("emit try f() except (e) { return 1; }")) == Emit(arm)


def test_a_fallback_stands_for_the_emit_of_it(ts):
    """
    '?? v' parses to an arm binding no error, whose body emits v.
    """
    assert parse_primary(ts("try f() ?? 0")) == Try(
        Call("f", []), None, [Emit(IntLiteral(0))], braced=False)


def test_a_fallback_takes_the_whole_expression_after_it(ts):
    """
    The fallback runs to the end of the expression, so an operator in
    it belongs to the fallback rather than to the 'try'.
    """
    assert parse_primary(ts("try f() ?? a + b")) == Try(
        Call("f", []), None,
        [Emit(BinaryOp("+", Var("a"), Var("b")))], braced=False)


def test_a_braced_fallback_is_the_arm_itself(ts):
    """
    Braces make the fallback the arm's own statements, emitted or not.
    """
    assert parse_primary(ts("try f() ?? { g(); emit 0; }")) == Try(
        Call("f", []), None,
        [ExprStmt(Call("g", [])), Emit(IntLiteral(0))])


def test_a_fallback_statement_keeps_its_semicolon(ts):
    """
    A '??' arm is part of the expression, so the statement around it
    closes the way any other expression statement does.
    """
    fallback = Try(Call("f", []), None, [Emit(IntLiteral(0))], braced=False)
    assert parse_statement(ts("let v = try f() ?? 0;")) == Let("v", None, fallback)

    braced = Try(Call("f", []), None, [Emit(IntLiteral(0))])
    assert parse_statement(ts("let v = try f() ?? { emit 0; };")) == \
        Let("v", None, braced)

    with pytest.raises(SyntaxError, match="expected ';'"):
        parse_statement(ts("let v = try f() ?? 0"))

    with pytest.raises(SyntaxError, match="expected ';'"):
        parse_statement(ts("let v = try f() ?? { emit 0; }"))


def test_a_statement_on_anything_else_still_needs_its_semicolon(ts):
    """
    Only a 'try' closes on its brace; every other value keeps its ';'.
    """
    with pytest.raises(SyntaxError, match="expected ';'"):
        parse_statement(ts("let v = f()"))

    assert parse_statement(ts("let v = f();")) == Let("v", None, Call("f", []))
