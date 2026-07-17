"""Tests for explicit numeric cast codegen."""

import pytest
from llvmlite import ir

from siec.ast import Cast, IntLiteral, Var
from siec.codegen.expressions import emit_cast
from siec.codegen.generator import Variable


def var(builder, name, sie_type, llvm_type):
    """
    A scope holding one variable of the given Sie and LLVM types.
    """
    return {name: Variable(builder.alloca(llvm_type, name=name), sie_type)}


def test_narrowing_truncates(env):
    """
    Casting to a narrower integer truncates.
    """
    gen, builder = env
    scope = var(builder, "a", "i32", ir.IntType(32))
    value = emit_cast(gen, builder, Cast(Var("a"), "u8"), scope)
    assert value.opname == "trunc"
    assert value.type == ir.IntType(8)


def test_signed_widening_sign_extends(env):
    """
    Casting a signed value to a wider integer sign-extends.
    """
    gen, builder = env
    scope = var(builder, "a", "i8", ir.IntType(8))
    value = emit_cast(gen, builder, Cast(Var("a"), "i32"), scope)
    assert value.opname == "sext"


def test_unsigned_widening_zero_extends(env):
    """
    Casting an unsigned value to a wider integer zero-extends.
    """
    gen, builder = env
    scope = var(builder, "a", "u8", ir.IntType(8))
    value = emit_cast(gen, builder, Cast(Var("a"), "u32"), scope)
    assert value.opname == "zext"


def test_same_width_reinterpret_emits_no_instruction(env):
    """
    Casting between same-width prefixes reinterprets, needing no instruction.
    """
    gen, builder = env
    scope = var(builder, "a", "i32", ir.IntType(32))
    value = emit_cast(gen, builder, Cast(Var("a"), "u32"), scope)
    assert value.opname == "load"  # the operand value, unchanged
    assert value.type == ir.IntType(32)


def test_signed_int_to_float(env):
    """
    A signed integer converts to a float with sitofp.
    """
    gen, builder = env
    scope = var(builder, "a", "i32", ir.IntType(32))
    value = emit_cast(gen, builder, Cast(Var("a"), "f64"), scope)
    assert value.opname == "sitofp"
    assert value.type == ir.DoubleType()


def test_unsigned_int_to_float(env):
    """
    An unsigned integer converts to a float with uitofp.
    """
    gen, builder = env
    scope = var(builder, "a", "u32", ir.IntType(32))
    value = emit_cast(gen, builder, Cast(Var("a"), "f32"), scope)
    assert value.opname == "uitofp"


def test_float_to_signed_int(env):
    """
    A float converts to a signed integer with fptosi.
    """
    gen, builder = env
    scope = var(builder, "a", "f64", ir.DoubleType())
    value = emit_cast(gen, builder, Cast(Var("a"), "i32"), scope)
    assert value.opname == "fptosi"


def test_float_to_unsigned_int(env):
    """
    A float converts to an unsigned integer with fptoui.
    """
    gen, builder = env
    scope = var(builder, "a", "f64", ir.DoubleType())
    value = emit_cast(gen, builder, Cast(Var("a"), "u8"), scope)
    assert value.opname == "fptoui"


def test_float_widening_and_narrowing(env):
    """
    Float-to-float casts extend or truncate the mantissa.
    """
    gen, builder = env
    wide = emit_cast(gen, builder, Cast(Var("a"), "f64"),
                     var(builder, "a", "f32", ir.FloatType()))
    narrow = emit_cast(gen, builder, Cast(Var("b"), "f32"),
                       var(builder, "b", "f64", ir.DoubleType()))
    assert wide.opname == "fpext"
    assert narrow.opname == "fptrunc"


def test_literal_casts_as_a_signed_integer(env):
    """
    A bare literal casts as a signed integer, so a widening cast sign-extends.
    """
    gen, builder = env
    value = emit_cast(gen, builder, Cast(IntLiteral(5), "i64"), {})
    assert value.opname == "sext"
    assert value.type == ir.IntType(64)


def test_cast_to_non_numeric_type_is_an_error(env):
    """
    Casting to a non-numeric type is rejected.
    """
    gen, builder = env
    scope = var(builder, "a", "i32", ir.IntType(32))
    with pytest.raises(TypeError, match="cannot cast to non-numeric type"):
        emit_cast(gen, builder, Cast(Var("a"), "bool"), scope)


def test_cast_from_non_numeric_value_is_an_error(env):
    """
    Casting a non-numeric value (a char) is rejected.
    """
    gen, builder = env
    scope = var(builder, "c", "char", ir.IntType(8))
    with pytest.raises(TypeError, match="cannot cast a non-numeric value"):
        emit_cast(gen, builder, Cast(Var("c"), "i32"), scope)
