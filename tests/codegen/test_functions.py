"""Tests for siec.codegen.functions."""

import pytest
from llvmlite import ir

from siec.ast import Function, IntLiteral, Param, Return, Var
from siec.codegen.functions import declare_function, emit_function
from siec.codegen.generator import CodeGenerator


@pytest.fixture
def gen():
    """
    A fresh CodeGenerator for one test.
    """
    return CodeGenerator("test")


def test_declare_builds_the_signature(gen):
    """
    Declaring maps the Sie param and return types into the LLVM signature.
    """
    func = declare_function(gen, Function("f", [Param("a", "i32")], "i64", None))
    assert func.function_type == ir.FunctionType(ir.IntType(64), [ir.IntType(32)])


def test_declare_varargs(gen):
    """
    The var_arg flag carries into the LLVM function type.
    """
    func = declare_function(gen, Function("f", [Param("a", "char*")], None, None,
                                          is_extern=True, var_arg=True))
    assert func.function_type.var_arg


def test_matching_redeclaration_reuses_the_declaration(gen):
    """
    Redeclaring with the same signature returns the existing function.
    """
    first = declare_function(gen, Function("f", [], "i32", None))
    second = declare_function(gen, Function("f", [], "i32", None))
    assert first is second


def test_conflicting_redeclaration_is_an_error(gen):
    """
    Redeclaring with a different signature raises a TypeError.
    """
    declare_function(gen, Function("f", [], "i32", None))
    with pytest.raises(TypeError, match="conflicting declarations"):
        declare_function(gen, Function("f", [], "i64", None))


def test_emit_fills_the_declared_function(gen):
    """
    Emitting a body turns the declaration into a definition.
    """
    fn = Function("f", [], "i32", [Return(IntLiteral(7))])
    declare_function(gen, fn)
    emit_function(gen, fn)
    assert "ret i32 7" in str(gen.module)


def test_emit_spills_params_into_named_slots(gen):
    """
    Parameters are stored into stack slots named after them.
    """
    fn = Function("f", [Param("a", "i32")], "i32", [Return(Var("a"))])
    declare_function(gen, fn)
    emit_function(gen, fn)
    assert "a.addr" in str(gen.module)


def test_emit_twice_is_an_error(gen):
    """
    Defining the same function twice raises a TypeError.
    """
    fn = Function("f", [], "i32", [Return(IntLiteral(0))])
    declare_function(gen, fn)
    emit_function(gen, fn)
    with pytest.raises(TypeError, match="defined more than once"):
        emit_function(gen, fn)


def test_void_function_may_fall_off_the_end(gen):
    """
    A void function without a return gets an implicit ret void.
    """
    fn = Function("f", [], None, [])
    declare_function(gen, fn)
    emit_function(gen, fn)
    assert "ret void" in str(gen.module)


def test_non_void_function_must_return(gen):
    """
    A non-void function that can fall off the end raises a TypeError.
    """
    fn = Function("f", [], "i32", [])
    declare_function(gen, fn)
    with pytest.raises(TypeError, match="must return a value"):
        emit_function(gen, fn)


def test_main_without_a_return_type_is_i32(gen):
    """
    'fn main()' declares as i32 and falls off the end returning 0.
    """
    fn = Function("main", [], None, [])
    declare_function(gen, fn)
    emit_function(gen, fn)

    body = str(gen.module)
    assert 'define i32 @"main"' in body
    assert "ret i32 0" in body


def test_bare_return_in_main_yields_zero(gen):
    """
    A 'return;' inside main returns the implicit exit code 0.
    """
    fn = Function("main", [], None, [Return(None)])
    declare_function(gen, fn)
    emit_function(gen, fn)
    assert "ret i32 0" in str(gen.module)


def test_main_args_form_keeps_the_c_signature(gen):
    """
    'fn main(args: char*[])' declares the C-level (i32, char**) underneath.
    """
    fn = Function("main", [Param("args", "char*[]")], None, [])
    func = declare_function(gen, fn)
    assert func.function_type.args == (
        ir.IntType(32), ir.PointerType(ir.PointerType(ir.IntType(8))))


def test_main_args_form_builds_the_fat_array(gen):
    """
    The args parameter spills as {argv, argc as u64} into its slot.
    """
    fn = Function("main", [Param("args", "char*[]")], None, [])
    declare_function(gen, fn)
    emit_function(gen, fn)

    body = str(gen.module)
    assert "zext" in body
    assert "insertvalue" in body
    assert "args.addr" in body
