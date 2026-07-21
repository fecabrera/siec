"""Tests for siec.parser.functions and the parse() entry point."""

import pytest

from siec.ast import Global, Include, IntLiteral, Param, Program, Return
from siec.lexer import lex
from siec.parser import parse
from siec.parser.functions import parse_function, parse_global, parse_program


def test_inline_decorator(ts):
    """
    '@inline fn' marks the function always-inline.
    """
    fn = parse_function(ts("@inline fn f() {}"))
    assert fn.is_inline
    assert not fn.is_extern


def test_unknown_decorator_is_an_error(ts):
    """
    A decorator other than '@extern', '@inline', or '@static' is rejected.
    """
    with pytest.raises(SyntaxError, match="unknown decorator '@wrong'"):
        parse_function(ts("@wrong fn f() {}"))


def test_static_decorator(ts):
    """
    '@static fn' marks the function file-local.
    """
    assert parse_function(ts("@static fn f() {}")).is_static


def test_decorators_stack(ts):
    """
    '@static @inline' applies both markings.
    """
    fn = parse_function(ts("@static @inline fn f() {}"))
    assert fn.is_static
    assert fn.is_inline


def test_extern_combines_only_with_noreturn(ts):
    """
    '@extern' functions have no body for other decorators to act on;
    '@noreturn', which describes the signature, is the one exception.
    """
    with pytest.raises(SyntaxError, match="'@extern' only combines"):
        parse_function(ts("@extern @static fn f();"))

    assert parse_function(ts("@extern @noreturn fn f();")).noreturn


def test_extern_let_parses_to_a_global(ts):
    """
    '@extern let name: T;' parses to a Global with its declared type.
    """
    assert parse_global(ts("@extern let environ: char**;")) == Global(
        "environ", "char**")


def test_static_let_parses_with_an_initializer(ts):
    """
    '@static let name: T = <value>;' parses to a static Global.
    """
    assert parse_global(ts("@static let count: i32 = 5;")) == Global(
        "count", "i32", True, IntLiteral(5))


def test_static_let_initializer_is_optional(ts):
    """
    A static without a value is zero-initialized later.
    """
    assert parse_global(ts("@static let count: i32;")) == Global(
        "count", "i32", True, None)


def test_extern_let_rejects_an_initializer(ts):
    """
    An extern global's storage lives elsewhere; '= v' is an error.
    """
    with pytest.raises(SyntaxError, match="cannot have an initializer"):
        parse_global(ts("@extern let x: i64 = 5;"))


def test_program_collects_globals(ts):
    """
    parse_program routes '@extern let' to globals, '@extern fn' to functions.
    """
    program = parse_program(ts("@extern let x: i64; @extern fn f();"))
    assert program.globals == [Global("x", "i64")]
    assert [fn.name for fn in program.functions] == ["f"]


def test_function_without_params_or_return_type(ts):
    """
    A minimal definition parses with empty params, no return type, and an empty body.
    """
    fn = parse_function(ts("fn f() {}"))
    assert (fn.name, fn.params, fn.return_type, fn.body) == ("f", [], None, [])
    assert not fn.is_extern
    assert not fn.var_arg


def test_function_with_params(ts):
    """
    Parameters parse as named, typed Params in order.
    """
    fn = parse_function(ts("fn f(a: i32, b: u8*) {}"))
    assert fn.params == [Param("a", "i32"), Param("b", "u8*")]


def test_function_with_return_type(ts):
    """
    The '-> type' annotation parses into return_type.
    """
    fn = parse_function(ts("fn f() -> i32 { return 0; }"))
    assert fn.return_type == "i32"
    assert fn.body == [Return(IntLiteral(0))]


def test_forward_declaration_has_no_body(ts):
    """
    A signature ending in ';' parses as a declaration with body None.
    """
    fn = parse_function(ts("fn f(a: i32) -> i32;"))
    assert fn.body is None
    assert not fn.is_extern


def test_extern_declaration(ts):
    """
    '@extern' marks the function extern, with varargs and no body.
    """
    fn = parse_function(ts("@extern fn printf(fmt: char*, ...) -> i32;"))
    assert fn.is_extern
    assert fn.var_arg
    assert fn.body is None
    assert fn.params == [Param("fmt", "char*")]


def test_extern_function_cannot_have_a_body(ts):
    """
    An extern function with a body raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="cannot have a body"):
        parse_function(ts("@extern fn f() { return; }"))


def test_varargs_must_be_last(ts):
    """
    '...' ends the parameter list and sets var_arg.
    """
    fn = parse_function(ts("fn f(a: i32, ...);"))
    assert fn.var_arg
    assert len(fn.params) == 1


def test_program_collects_includes_and_functions(ts):
    """
    A program separates '@include' directives from function definitions.
    """
    program = parse_program(ts('@include("a/b") fn f() {} @extern fn g();'))
    assert program.includes == [Include("a/b")]
    assert [fn.name for fn in program.functions] == ["f", "g"]


def test_program_stops_at_eof(ts):
    """
    An empty token stream parses to an empty Program.
    """
    assert parse_program(ts("")) == Program([], [])


def test_program_collects_structs(ts):
    """
    Struct declarations are gathered into the program's struct list.
    """
    program = parse_program(ts("struct S { x: i32; } fn f() {}"))
    assert [s.name for s in program.structs] == ["S"]
    assert [fn.name for fn in program.functions] == ["f"]


def test_parse_wires_lexer_tokens_into_a_program():
    """
    parse() turns a token list into a Program AST.
    """
    program = parse(lex("fn main() -> i32 { return 0; }"))
    assert isinstance(program, Program)
    assert program.functions[0].name == "main"
