"""Tests for implicit numeric widening and its rejections."""

import pytest
from llvmlite import ir

from siec.ast import IntLiteral, Var
from siec.codegen.expressions import emit_coerced, numeric_class
from siec.codegen.generator import Variable


@pytest.mark.parametrize("name,expected", [
    ("i8", ("i", 8)), ("i64", ("i", 64)),
    ("u8", ("u", 8)), ("u32", ("u", 32)),
    ("f32", ("f", 32)), ("f64", ("f", 64)),
    ("char", None), ("bool", None), ("i32*", None), ("i32[]", None), ("Point", None),
])
def test_numeric_class(name, expected):
    """
    Only scalar iN/uN/fN names classify into a prefix and width.
    """
    assert numeric_class(name) == expected


def scope_with(builder, name, sie_type, llvm_type):
    """
    A scope holding one variable of the given Sie and LLVM types.
    """
    return {name: Variable(builder.alloca(llvm_type, name=name), sie_type)}


def test_literal_adopts_the_target_without_extension(env):
    """
    A literal takes the target type directly, needing no extension instruction.
    """
    gen, builder = env
    value = emit_coerced(gen, builder, IntLiteral(5), "u64", {})
    assert value.type == ir.IntType(64)
    assert not isinstance(value, ir.Instruction)  # a constant, not a zext


def test_unsigned_widening_zero_extends(env):
    """
    A narrower unsigned value zero-extends to a wider unsigned target.
    """
    gen, builder = env
    scope = scope_with(builder, "a", "u8", ir.IntType(8))
    value = emit_coerced(gen, builder, Var("a"), "u64", scope)
    assert value.opname == "zext"
    assert value.type == ir.IntType(64)


def test_signed_widening_sign_extends(env):
    """
    A narrower signed value sign-extends to a wider signed target.
    """
    gen, builder = env
    scope = scope_with(builder, "a", "i8", ir.IntType(8))
    value = emit_coerced(gen, builder, Var("a"), "i32", scope)
    assert value.opname == "sext"
    assert value.type == ir.IntType(32)


def test_same_type_is_left_alone(env):
    """
    A value already of the target type is returned untouched.
    """
    gen, builder = env
    scope = scope_with(builder, "a", "i32", ir.IntType(32))
    value = emit_coerced(gen, builder, Var("a"), "i32", scope)
    assert value.opname == "load"


def test_narrowing_is_rejected(env):
    """
    Assigning a wider value to a narrower same-prefix type is an error.
    """
    gen, builder = env
    scope = scope_with(builder, "a", "i16", ir.IntType(16))
    with pytest.raises(TypeError, match="narrow"):
        emit_coerced(gen, builder, Var("a"), "i8", scope)


def test_signed_to_unsigned_is_rejected(env):
    """
    Widening across the signed/unsigned prefix is an error.
    """
    gen, builder = env
    scope = scope_with(builder, "a", "i16", ir.IntType(16))
    with pytest.raises(TypeError, match="signed, unsigned, and float"):
        emit_coerced(gen, builder, Var("a"), "u32", scope)


def test_integer_to_float_is_rejected(env):
    """
    Widening from an integer to a float is an error.
    """
    gen, builder = env
    scope = scope_with(builder, "a", "i16", ir.IntType(16))
    with pytest.raises(TypeError, match="signed, unsigned, and float"):
        emit_coerced(gen, builder, Var("a"), "f32", scope)
