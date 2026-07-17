"""Tests for siec.codegen.expressions."""

import pytest
from llvmlite import ir

from siec.ast import (BinaryOp, BoolLiteral, Call, Function, Index, IntLiteral, Param,
                      StrLiteral, UnaryOp, Var)
from siec.codegen.expressions import emit_bool, emit_call, emit_expression, emit_string
from siec.codegen.functions import declare_function
from siec.codegen.generator import Variable


def test_int_literal_defaults_to_i32(env):
    """
    An int literal with no expected type becomes an i32 constant.
    """
    gen, builder = env
    value = emit_expression(gen, builder, IntLiteral(7), None, {})
    assert value.type == ir.IntType(32)


def test_int_literal_takes_the_expected_type(env):
    """
    An int literal adopts the expected integer type of its context.
    """
    gen, builder = env
    value = emit_expression(gen, builder, IntLiteral(7), ir.IntType(64), {})
    assert value.type == ir.IntType(64)


def test_string_literal_becomes_a_char_pointer(env):
    """
    A string literal is emitted as a char* value.
    """
    gen, builder = env
    value = emit_expression(gen, builder, StrLiteral("hi"), None, {})
    assert value.type == ir.PointerType(ir.IntType(8))


def test_boolean_literals_become_i1_constants(env):
    """
    'true' and 'false' emit i1 constants of 1 and 0, ignoring the context type.
    """
    gen, builder = env
    true = emit_expression(gen, builder, BoolLiteral(True), ir.IntType(32), {})
    false = emit_expression(gen, builder, BoolLiteral(False), None, {})
    assert true.type == ir.IntType(1) and true.constant == 1
    assert false.type == ir.IntType(1) and false.constant == 0


def test_emit_string_creates_a_null_terminated_private_constant(env):
    """
    The string's bytes land in a private global constant one byte longer than the text.
    """
    gen, builder = env
    emit_string(gen, builder, "hi")
    const = gen.module.get_global(".str.0")
    assert const.global_constant
    assert const.linkage == "private"
    assert const.type.pointee == ir.ArrayType(ir.IntType(8), 3)


def test_emit_string_numbers_constants_sequentially(env):
    """
    Each emitted string takes the next .str.N name and bumps the counter.
    """
    gen, builder = env
    emit_string(gen, builder, "a")
    emit_string(gen, builder, "b")
    assert gen.str_count == 2
    assert gen.module.get_global(".str.1") is not None


def test_variable_loads_from_its_slot(env):
    """
    A variable reference emits a load from its stack slot.
    """
    gen, builder = env
    scope = {"x": Variable(builder.alloca(ir.IntType(32), name="x"), "i32")}
    value = emit_expression(gen, builder, Var("x"), None, scope)
    assert value.type == ir.IntType(32)
    assert value.opname == "load"


def test_undefined_variable_is_an_error(env):
    """
    Referencing a name not in scope raises a NameError.
    """
    gen, builder = env
    with pytest.raises(NameError, match="undefined variable 'ghost'"):
        emit_expression(gen, builder, Var("ghost"), None, {})


def test_index_loads_the_pointed_element(env):
    """
    Indexing a pointer offsets it and loads a value of the pointee type.
    """
    gen, builder = env
    slot = builder.alloca(ir.PointerType(ir.PointerType(ir.IntType(8))), name="argv")
    value = emit_expression(gen, builder, Index(Var("argv"), IntLiteral(0)),
                            None, {"argv": Variable(slot, "char**")})
    assert value.type == ir.PointerType(ir.IntType(8))
    assert value.opname == "load"


def test_index_on_a_non_pointer_is_an_error(env):
    """
    Indexing a scalar raises a TypeError naming the type.
    """
    gen, builder = env
    scope = {"x": Variable(builder.alloca(ir.IntType(32), name="x"), "i32")}
    with pytest.raises(TypeError, match="cannot index a value of type"):
        emit_expression(gen, builder, Index(Var("x"), IntLiteral(0)), None, scope)


@pytest.mark.parametrize("op,instruction", [("+", "add"), ("-", "sub"), ("*", "mul"),
                                            ("/", "sdiv"), ("%", "srem")])
def test_arithmetic_emits_the_signed_instruction(env, op, instruction):
    """
    Each arithmetic operator lowers to its signed integer instruction.
    """
    gen, builder = env
    value = emit_expression(
        gen, builder, BinaryOp(op, IntLiteral(6), IntLiteral(2)), None, {})
    assert value.opname == instruction
    assert value.type == ir.IntType(32)


def test_arithmetic_keeps_the_context_type(env):
    """
    Arithmetic in a typed context computes in that type.
    """
    gen, builder = env
    value = emit_expression(
        gen, builder, BinaryOp("+", IntLiteral(1), IntLiteral(2)), ir.IntType(64), {})
    assert value.type == ir.IntType(64)


@pytest.mark.parametrize("op,instruction", [("<<", "shl"), (">>", "ashr"), ("&", "and"),
                                            ("|", "or"), ("^", "xor")])
def test_bitwise_emits_the_signed_instruction(env, op, instruction):
    """
    Each bitwise operator lowers to its signed integer instruction.
    """
    gen, builder = env
    value = emit_expression(
        gen, builder, BinaryOp(op, IntLiteral(6), IntLiteral(2)), None, {})
    assert value.opname == instruction
    assert value.type == ir.IntType(32)


def test_power_emits_a_multiply_loop(env):
    """
    '**' lowers to a loop over pow.cond/pow.body/pow.end blocks, keeping the context type.
    """
    gen, builder = env
    value = emit_expression(
        gen, builder, BinaryOp("**", IntLiteral(2), IntLiteral(10)), ir.IntType(64), {})
    assert value.type == ir.IntType(64)

    blocks = [block.name for block in builder.function.blocks]
    assert {"pow.cond", "pow.body", "pow.end"} <= set(blocks)


def test_logical_yields_a_bool_from_a_phi(env):
    """
    'and'/'or' merge the short-circuit paths through a phi producing an i1.
    """
    gen, builder = env
    for op in ("and", "or"):
        value = emit_expression(
            gen, builder, BinaryOp(op, IntLiteral(1), IntLiteral(0)), None, {})
        assert value.opname == "phi"
        assert value.type == ir.IntType(1)


def test_logical_branches_around_the_right_side(env):
    """
    'and' opens blocks so the right side only runs when the left is true.
    """
    gen, builder = env
    emit_expression(
        gen, builder, BinaryOp("and", IntLiteral(1), IntLiteral(0)), None, {})

    blocks = [block.name for block in builder.function.blocks]
    assert {"and.rhs", "and.end"} <= set(blocks)


def signed_and_unsigned(builder):
    """
    A scope holding a signed 's' and an unsigned 'u' of the same width.
    """
    return {"s": Variable(builder.alloca(ir.IntType(32), name="s"), "i32"),
            "u": Variable(builder.alloca(ir.IntType(32), name="u"), "u32")}


def test_mixed_signedness_comparison_is_rejected(env):
    """
    Comparing a signed value against an unsigned one raises a TypeError.
    """
    gen, builder = env
    with pytest.raises(TypeError, match="signed and unsigned"):
        emit_expression(gen, builder, BinaryOp("<", Var("s"), Var("u")),
                        None, signed_and_unsigned(builder))


def test_mixed_signedness_arithmetic_is_rejected(env):
    """
    Arithmetic between a signed value and an unsigned one raises a TypeError.
    """
    gen, builder = env
    with pytest.raises(TypeError, match="signed and unsigned"):
        emit_expression(gen, builder, BinaryOp("+", Var("s"), Var("u")),
                        None, signed_and_unsigned(builder))


def test_signedness_is_inferred_through_subexpressions(env):
    """
    The mix is caught even when one side buries its signedness in arithmetic.
    """
    gen, builder = env
    with pytest.raises(TypeError, match="signed and unsigned"):
        emit_expression(
            gen, builder,
            BinaryOp("==", BinaryOp("+", Var("s"), IntLiteral(1)), Var("u")),
            None, signed_and_unsigned(builder))


def test_literals_adapt_to_either_signedness(env):
    """
    An int literal combines with signed and unsigned values alike.
    """
    gen, builder = env
    scope = signed_and_unsigned(builder)
    for name in ("s", "u"):
        value = emit_expression(gen, builder, BinaryOp("<", Var(name), IntLiteral(3)),
                                None, scope)
        assert value.type == ir.IntType(1)


@pytest.mark.parametrize("op,instruction", [("/", "udiv"), ("%", "urem"), (">>", "lshr")])
def test_unsigned_operands_emit_the_unsigned_instruction(env, op, instruction):
    """
    Division, remainder, and right shift switch instruction on unsigned operands.
    """
    gen, builder = env
    value = emit_expression(gen, builder, BinaryOp(op, Var("u"), IntLiteral(2)),
                            None, signed_and_unsigned(builder))
    assert value.opname == instruction


def test_unsigned_operands_compare_unsigned(env):
    """
    A comparison over unsigned operands emits an unsigned predicate.
    """
    gen, builder = env
    value = emit_expression(gen, builder, BinaryOp("<", Var("u"), IntLiteral(3)),
                            None, signed_and_unsigned(builder))
    assert "ult" in str(value)


def test_bool_coercion_compares_numbers_against_zero(env):
    """
    A non-boolean number coerces to a bool by comparing non-equal to zero.
    """
    gen, builder = env
    scope = {"x": Variable(builder.alloca(ir.IntType(32), name="x"), "i32")}
    value = emit_bool(gen, builder, Var("x"), scope)
    assert value.type == ir.IntType(1)


def test_bool_coercion_compares_pointers_against_null(env):
    """
    A pointer coerces to a bool by comparing non-equal to null.
    """
    gen, builder = env
    scope = {"p": Variable(builder.alloca(ir.PointerType(ir.IntType(8)), name="p"), "char*")}
    value = emit_bool(gen, builder, Var("p"), scope)
    assert value.type == ir.IntType(1)
    assert "null" in str(value)


def test_bool_coercion_keeps_booleans(env):
    """
    An i1 value passes through the coercion untouched.
    """
    gen, builder = env
    comparison = BinaryOp("<", IntLiteral(1), IntLiteral(2))
    value = emit_bool(gen, builder, comparison, {})
    assert value.type == ir.IntType(1)


def test_unary_minus_negates_in_the_context_type(env):
    """
    Unary minus emits a negation carrying the context type.
    """
    gen, builder = env
    value = emit_expression(
        gen, builder, UnaryOp("-", IntLiteral(5)), ir.IntType(64), {})
    assert value.type == ir.IntType(64)
    assert value.opname == "sub"


def test_unary_bitwise_not_flips_bits_in_the_context_type(env):
    """
    '~' emits a bit flip carrying the context type.
    """
    gen, builder = env
    value = emit_expression(
        gen, builder, UnaryOp("~", IntLiteral(5)), ir.IntType(64), {})
    assert value.type == ir.IntType(64)
    assert value.opname == "xor"


def test_unary_not_inverts_a_bool(env):
    """
    'not' coerces its operand to a bool and inverts it, yielding an i1.
    """
    gen, builder = env
    value = emit_expression(
        gen, builder, UnaryOp("not", Var("x")),
        None, {"x": Variable(builder.alloca(ir.IntType(32), name="x"), "i32")})
    assert value.type == ir.IntType(1)


def test_unknown_unary_operator_is_an_error(env):
    """
    A unary operator other than '-', '~', or 'not' raises a TypeError.
    """
    gen, builder = env
    with pytest.raises(TypeError, match="unknown unary operator"):
        emit_expression(gen, builder, UnaryOp("!", IntLiteral(1)), None, {})


def test_comparison_yields_an_i1(env):
    """
    A comparison emits an icmp producing an i1.
    """
    gen, builder = env
    value = emit_expression(
        gen, builder, BinaryOp("<", IntLiteral(1), IntLiteral(2)), None, {})
    assert value.type == ir.IntType(1)


def declare(gen, name, ret, params, var_arg=False):
    """
    Declare a function of the given Sie signature, registering it for calls.
    """
    fn = Function(name, [Param(f"p{i}", t) for i, t in enumerate(params)],
                  ret, None, var_arg=var_arg)
    return declare_function(gen, fn)


def test_call_returns_the_functions_value(env):
    """
    A call to a declared function yields a value of its return type.
    """
    gen, builder = env
    declare(gen, "f", "i32", ["i32"])
    value = emit_call(gen, builder, Call("f", [IntLiteral(1)]), {})
    assert value.type == ir.IntType(32)


def test_call_arguments_take_parameter_types(env):
    """
    Arguments are typed by the callee's matching parameter.
    """
    gen, builder = env
    declare(gen, "f", None, ["i64"])
    call = emit_call(gen, builder, Call("f", [IntLiteral(1)]), {})
    assert call.args[0].type == ir.IntType(64)


def test_call_to_varargs_allows_extra_arguments(env):
    """
    A varargs callee accepts arguments beyond its declared parameters.
    """
    gen, builder = env
    declare(gen, "printf", "i32", ["char*"], var_arg=True)
    call = emit_call(gen, builder, Call("printf", [StrLiteral("%d"), IntLiteral(1)]), {})
    assert call.args[1].type == ir.IntType(32)


def test_call_with_too_few_arguments_is_an_error(env):
    """
    Fewer arguments than parameters raises a TypeError.
    """
    gen, builder = env
    declare(gen, "f", None, ["i32"])
    with pytest.raises(TypeError, match="too few arguments"):
        emit_call(gen, builder, Call("f", []), {})


def test_call_with_too_many_arguments_is_an_error(env):
    """
    Extra arguments to a non-varargs callee raise a TypeError.
    """
    gen, builder = env
    declare(gen, "f", None, [])
    with pytest.raises(TypeError, match="too many arguments"):
        emit_call(gen, builder, Call("f", [IntLiteral(1)]), {})


def test_call_to_undefined_function_is_an_error(env):
    """
    Calling a name with no declaration raises a NameError.
    """
    gen, builder = env
    with pytest.raises(NameError, match="undefined function 'f'"):
        emit_call(gen, builder, Call("f", []), {})
