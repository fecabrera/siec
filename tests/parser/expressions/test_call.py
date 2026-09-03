"""Tests for parsing call expressions."""

import pytest

from siec.ast import BinaryOp, Call, Index, IntLiteral, MethodCall, StrLiteral, Var
from siec.parser.expressions import parse_expression, parse_primary


def test_call_without_arguments(ts):
    """
    An identifier followed by '()' parses to a Call with no arguments.
    """
    assert parse_primary(ts("f()")) == Call("f", [])


def test_call_with_arguments(ts):
    """
    Call arguments parse as comma-separated expressions of any kind.
    """
    assert parse_primary(ts('f(1, x, "s")')) == Call(
        "f", [IntLiteral(1), Var("x"), StrLiteral("s")])


@pytest.mark.parametrize(("source", "expected"), (
    ("f(1, 2)", Call("f", [IntLiteral(1), IntLiteral(2)])),
    ("S::f(1, 2)", Call("S::f", [IntLiteral(1), IntLiteral(2)])),
    ("S<i32>::f(1, 2)",
     Call("S<i32>::f", [IntLiteral(1), IntLiteral(2)])),
    ("mod.S::f(1, 2)",
     Call("mod.S::f", [IntLiteral(1), IntLiteral(2)])),
    ("mod.f(1, 2)", Call("mod.f", [IntLiteral(1), IntLiteral(2)])),
    ("make().f(1, 2)",
     MethodCall(Call("make", []), "f", [IntLiteral(1), IntLiteral(2)])),
))
def test_call_forms_share_argument_parsing(ts, source, expected):
    """Every call form parses the same comma-separated arguments."""
    assert parse_expression(ts(source)) == expected


def test_nested_calls(ts):
    """
    A call may appear as another call's argument.
    """
    assert parse_primary(ts("f(g(1))")) == Call("f", [Call("g", [IntLiteral(1)])])


def test_array_type_qualified_method_call(ts):
    """An unsized array receiver can qualify a static method call."""
    assert parse_primary(ts("char[]::from_c_str(args[1])")) == Call(
        "char[]::from_c_str", [Index(Var("args"), IntLiteral(1))])


def test_module_qualified_static_method_call(ts):
    """A dotted type can qualify a static method call."""
    assert parse_expression(ts('Gtk.Button::from_icon("window-new")')) == Call(
        "Gtk.Button::from_icon", [StrLiteral("window-new")])


def test_call_arguments_may_be_comparisons(ts):
    """
    Full expressions, including comparisons, are allowed as call arguments.
    """
    assert parse_expression(ts("f(a < b)")) == Call(
        "f", [BinaryOp("<", Var("a"), Var("b"))])


def test_generic_call_with_nested_type_argument(ts):
    """A nested generic type argument closes before the call's '('."""
    assert parse_primary(ts("alloc<Slot<T>>(1)")) == Call(
        "alloc", [IntLiteral(1)], ["Slot<T>"])


def test_generic_call_with_function_type_argument(ts):
    """
    Parentheses inside a 'fn(...)' type argument are not the call's '('.
    """
    assert parse_primary(ts("alloc<fn(i32) -> i32>(p)")) == Call(
        "alloc", [Var("p")], ["fn(i32)->i32"])


def test_missing_angle_before_call_paren_is_a_syntax_error(ts):
    """
    After a nested generic type, '(' cannot start a comparison fallback:
    the '<' already opened a type argument list.
    """
    with pytest.raises(SyntaxError,
                       match=r"expected '>' before '\(' in type argument list"):
        parse_expression(ts("alloc<Slot<T>(1)"))


def test_missing_angle_after_function_type_argument(ts):
    """A finished 'fn(...)' type argument still needs '>' before the call."""
    with pytest.raises(SyntaxError,
                       match=r"expected '>' before '\(' in type argument list"):
        parse_expression(ts("alloc<fn(i32) -> i32(p)"))


def test_missing_angle_after_multiple_type_arguments(ts):
    """A comma commits to a type argument list, so '(' means a missing '>'."""
    with pytest.raises(SyntaxError,
                       match=r"expected '>' before '\(' in type argument list"):
        parse_expression(ts("pair<i32, i64(1, 2)"))


def test_comparison_against_a_call(ts):
    """A bare name before '(' still rewinds to a comparison."""
    assert parse_expression(ts("a < foo(x)")) == BinaryOp(
        "<", Var("a"), Call("foo", [Var("x")]))


def test_comparison_against_a_method_call(ts):
    """
    Dotted call targets share the type-path spelling, so they rewind to
    comparison instead of demanding a missing '>'.
    """
    assert parse_expression(ts("i < buf.get_length()")) == BinaryOp(
        "<", Var("i"), Call("buf.get_length", []))
