"""Tests for array member access codegen."""

import pytest
from llvmlite import ir

from siec.ast import AggregateLiteral, IntLiteral, Member, MemberAssign, Var
from siec.codegen.expressions import emit_expression, emit_lvalue, member_field, signedness
from siec.codegen.generator import Variable
from siec.codegen.types import resolve_type


def array_scope(builder, name="a", element="i32"):
    """
    A scope holding one array-typed variable of the given element type.
    """
    slot = builder.alloca(resolve_type(f"{element}[]"), name=name)
    return {name: Variable(slot, f"{element}[]")}


def test_array_exposes_data_and_length_fields():
    """
    An array's synthetic fields are 'data' (X*) at index 0 and 'length' (u64) at index 1.
    """
    # member_field goes through a scope, but the field table itself is fixed
    from siec.codegen.expressions import type_info
    from siec.codegen.generator import CodeGenerator

    info = type_info(CodeGenerator("t"), "i32[]")
    assert info.field("data") == (0, "i32*")
    assert info.field("length") == (1, "u64")


def test_data_read_extracts_the_pointer(env):
    """
    Reading '.data' extracts the pointer field, typed as the element pointer.
    """
    gen, builder = env
    scope = array_scope(builder)

    value = emit_expression(gen, builder, Member(Var("a"), "data"), None, scope)
    assert value.opname == "extractvalue"
    assert value.type == ir.PointerType(ir.IntType(32))


def test_length_read_extracts_the_count(env):
    """
    Reading '.length' extracts the i64 count field.
    """
    gen, builder = env
    scope = array_scope(builder)

    value = emit_expression(gen, builder, Member(Var("a"), "length"), None, scope)
    assert value.opname == "extractvalue"
    assert value.type == ir.IntType(64)


def test_length_write_stores_into_the_field(env):
    """
    Assigning '.length' stores into the array's count slot.
    """
    gen, builder = env
    scope = array_scope(builder)

    emit_statement_length(gen, builder, scope)
    assert "store i64 9" in str(builder.function)


def emit_statement_length(gen, builder, scope):
    """
    Emit 'a.length = 9;' through the statement path.
    """
    from siec.codegen.statements import emit_statement
    emit_statement(gen, builder, MemberAssign(Var("a"), "length", IntLiteral(9)), scope)


def test_length_lvalue_geps_to_index_one(env):
    """
    The address of '.length' is a gep to field index 1, an i64 pointer.
    """
    gen, builder = env
    scope = array_scope(builder)

    ptr = emit_lvalue(gen, builder, Member(Var("a"), "length"), scope)
    assert ptr.type == ir.PointerType(ir.IntType(64))


def test_length_is_unsigned(env):
    """
    'length' is a u64, so it participates in the mixed-signedness check as unsigned.
    """
    gen, builder = env
    scope = array_scope(builder)
    assert signedness(gen, Member(Var("a"), "length"), scope) == "unsigned"


def test_unknown_array_field_is_an_error(env):
    """
    Accessing a field other than 'data' or 'length' raises a TypeError.
    """
    gen, builder = env
    scope = array_scope(builder)

    with pytest.raises(TypeError, match="has no field 'size'"):
        member_field(gen, Member(Var("a"), "size"), scope)


def test_array_built_from_a_pointer_and_length(env):
    """
    A '{ptr, n}' literal builds the fat struct from a data pointer and a length.
    """
    gen, builder = env
    # a data pointer of the right element type and a u64 length in scope
    scope = {
        "ptr": Variable(builder.alloca(ir.PointerType(ir.IntType(32)), name="ptr"), "i32*"),
        "n": Variable(builder.alloca(ir.IntType(64), name="n"), "u64"),
    }

    literal = AggregateLiteral([Var("ptr"), Var("n")])
    value = emit_expression(gen, builder, literal, resolve_type("i32[]"), scope)
    assert value.opname == "insertvalue"
    assert value.type == resolve_type("i32[]")
