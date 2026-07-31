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


def test_private_function_and_method_decorators(ts):
    """
    '@private' marks both free functions and receiver methods.
    """
    fn = parse_function(ts("@private fn helper();"))
    method = parse_function(ts("@private fn S::helper();"))

    assert fn.is_private
    assert method.is_private
    assert method.receiver == "S"


def test_decorators_stack(ts):
    """
    '@static @inline' applies both markings.
    """
    fn = parse_function(ts("@static @inline fn f() {}"))
    assert fn.is_static
    assert fn.is_inline


def test_override_decorator_marks_functions_and_methods(ts):
    """'@override' marks the declaration whose implementation takes precedence."""
    fn = parse_function(ts("@override fn f() {}"))
    method = parse_function(ts("@override fn char[]::f(&self) {}"))

    assert fn.is_override
    assert method.is_override


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


def test_generic_function_and_method_bounds(ts):
    """
    Each generic parameter may carry a full type bound, including a
    generic interface spelling on a method's own parameter.
    """
    fn = parse_function(ts("fn f<T: u64, U>(t: T) -> U;"))
    method = parse_function(
        ts("fn S<T>::f<U: Iface<T>>(&self, value: U) -> T;"))

    assert fn.type_params == ["T", "U"]
    assert fn.constraints == {"T": "u64"}
    assert method.receiver_params == ["T"]
    assert method.type_params == ["U"]
    assert method.constraints == {"U": "Iface<T>"}


def test_bounded_extension_block_supplies_method_receivers(ts):
    """
    An extension block binds its receiver parameter and bounds for every
    method it contains.
    """
    program = parse_program(ts("""
    @extend<T: Scalar> T[]: Hashable {
        fn hash(const &self) -> u64 { return 0; }
    }
    """))

    ext = program.extends[0]
    method = program.functions[0]
    assert ext.name == "T[]"
    assert ext.interfaces == ["Hashable"]
    assert ext.params == ["T"]
    assert ext.constraints == {"T": "Scalar"}
    assert ext.actions == [method]
    assert method.name == "T[]::hash"
    assert method.receiver == "T[]"
    assert method.receiver_params == ["T"]
    assert method.receiver_constraints == {"T": "Scalar"}


def test_template_block_supplies_bounds_to_extensions_and_methods(ts):
    """A template block binds both implicit and separate method receivers."""
    program = parse_program(ts("""
    @template<T: Scalar> {
        @extend T[]: Hashable {
            fn hash(const &self) -> u64 { return 0; }
        }

        fn T[]::other(const &self) -> u64 { return 1; }
    }
    """))

    ext = program.extends[0]
    hash_method, other = program.functions
    assert ext.params == ["T"]
    assert ext.constraints == {"T": "Scalar"}
    assert hash_method.receiver == "T[]"
    assert hash_method.receiver_params == ["T"]
    assert hash_method.receiver_constraints == {"T": "Scalar"}
    assert other.receiver == "T[]"
    assert other.receiver_params == ["T"]
    assert other.receiver_constraints == {"T": "Scalar"}


def test_template_decorator_supplies_one_declaration(ts):
    """The unbraced form decorates one extension or method at a time."""
    program = parse_program(ts("""
    @template<T: Scalar>
    @extend T[]: Hashable {
        fn hash(const &self) -> u64 { return 0; }
    }

    @template<T: Scalar>
    fn T[]::other(const &self) -> u64 { return 1; }
    """))

    ext = program.extends[0]
    assert ext.params == ["T"]
    assert ext.constraints == {"T": "Scalar"}
    assert all(fn.receiver_params == ["T"] for fn in program.functions)
    assert all(fn.receiver_constraints == {"T": "Scalar"}
               for fn in program.functions)


def test_template_decorator_bounds_subset_of_generic_receiver(ts):
    """A decorator keeps unconstrained receiver parameters in the family."""
    program = parse_program(ts("""
    @template<K: Hashable>
    @override
    fn Map<K, V>::hash(const &self) -> u64 { return 42; }
    """))

    method = program.functions[0]
    assert method.receiver == "Map"
    assert method.receiver_params == ["K", "V"]
    assert method.receiver_constraints == {"K": "Hashable"}
    assert method.params[0] == Param("self", "const &Map<K,V>")


def test_template_decorator_parameter_must_belong_to_receiver(ts):
    """A decorator cannot introduce an unrelated receiver parameter."""
    with pytest.raises(SyntaxError, match="parameter 'T'.*not occur"):
        parse_program(ts("""
        @template<T: Hashable>
        fn Map<K, V>::hash(const &self) -> u64 { return 42; }
        """))


def test_template_decorator_intersects_existing_receiver_bound(ts):
    """Repeated bounds on one receiver parameter form an intersection."""
    program = parse_program(ts("""
    @template<K: Hashable>
    fn Map<K: Scalar, V>::hash(const &self) -> u64 { return 42; }
    """))

    assert program.functions[0].receiver_constraints == {
        "K": ("Hashable", "Scalar"),
    }


def test_nested_template_decorator_in_extension_body(ts):
    """A nested method template adds to its extension environment."""
    program = parse_program(ts("""
    @template<T: Formattable> {
        @extend T[]: Formattable {
            fn format(const &self) -> i32 { return 1; }

            @template<T: Iterable<char>>
            @override
            fn format(const &self) -> i32 { return 42; }
        }
    }
    """))

    ordinary, override = program.functions
    assert ordinary.receiver_constraints == {"T": "Formattable"}
    assert override.receiver_constraints == {
        "T": ("Formattable", "Iterable<char>"),
    }


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
