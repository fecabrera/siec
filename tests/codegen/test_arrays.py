"""Tests for array member access codegen."""

import pytest
from llvmlite import ir

from siec.ast import AggregateLiteral, ArrayLiteral, IntLiteral, Member, MemberAssign, StrLiteral, Var
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


def test_array_literal_builds_a_fat_array(env):
    """
    An '[a, b, c]' literal stores its elements into a backing array and wraps
    a pointer to it with their count in the fat array value.
    """
    gen, builder = env

    literal = ArrayLiteral([IntLiteral(1), IntLiteral(2), IntLiteral(3)])
    value = emit_expression(gen, builder, literal, resolve_type("i32[]"), {})
    assert value.opname == "insertvalue"
    assert value.type == resolve_type("i32[]")
    assert "alloca [3 x i32]" in str(builder.function)
    assert "store i32 1" in str(builder.function)
    assert "store i32 2" in str(builder.function)
    assert "store i32 3" in str(builder.function)


def test_array_literal_length_matches_element_count(env):
    """
    The fat array's length field is a constant equal to the element count.
    """
    gen, builder = env

    literal = ArrayLiteral([IntLiteral(1)] * 5)
    emit_expression(gen, builder, literal, resolve_type("i32[]"), {})
    assert "insertvalue {i32*, i64} %\"" in str(builder.function)
    assert "i64 5, 1" in str(builder.function)


def test_empty_array_literal_needs_an_array_type(env):
    """
    An array literal used without an array-type context raises a TypeError.
    """
    gen, builder = env
    with pytest.raises(TypeError, match="needs an array type"):
        emit_expression(gen, builder, ArrayLiteral([IntLiteral(3)]), ir.IntType(32), {})


def test_string_literal_fills_a_char_array(env):
    """
    A string literal in a 'char[]' context builds the fat {char*, u64} value,
    its length excluding the null terminator.
    """
    gen, builder = env

    value = emit_expression(gen, builder, StrLiteral("hello"), resolve_type("char[]"), {})
    assert value.opname == "insertvalue"
    assert value.type == resolve_type("char[]")
    assert "i64 5, 1" in str(builder.function)


def test_string_literal_stays_a_pointer_without_an_array_context(env):
    """
    A string literal without a 'char[]' context still emits as a plain char*.
    """
    gen, builder = env

    value = emit_expression(gen, builder, StrLiteral("hello"), None, {})
    assert value.type == ir.PointerType(ir.IntType(8))


def test_array_literal_of_strings_stores_pointers(env):
    """
    A 'char*[]' literal stores each string's pointer into the backing array.
    """
    gen, builder = env

    literal = ArrayLiteral([StrLiteral("ls"), StrLiteral("cd")])
    value = emit_expression(gen, builder, literal, resolve_type("char*[]"), {})
    assert value.type == resolve_type("char*[]")
    assert "alloca [2 x i8*]" in str(builder.function)


def test_nested_array_literal_stores_fat_arrays(env):
    """
    A 'char[][]' literal stores each string's fat array into the backing array.
    """
    gen, builder = env

    literal = ArrayLiteral([StrLiteral("hello"), StrLiteral("world")])
    value = emit_expression(gen, builder, literal, resolve_type("char[][]"), {})
    assert value.type == resolve_type("char[][]")
    assert "alloca [2 x {i8*, i64}]" in str(builder.function)


def test_array_literal_element_widens_to_the_declared_element_type(env):
    """
    Coercing an array literal through a Let-style context widens each
    element to the array's declared element Sie type.
    """
    from siec.codegen.expressions import emit_coerced

    gen, builder = env
    literal = ArrayLiteral([IntLiteral(1), IntLiteral(2)])
    value = emit_coerced(gen, builder, literal, "i64[]", {})
    assert value.type == resolve_type("i64[]")
    assert "alloca [2 x i64]" in str(builder.function)


def test_array_decays_to_its_element_pointer(env):
    """
    An array coerced to its element pointer type lowers to its data field.
    """
    from siec.codegen.expressions import emit_coerced

    gen, builder = env
    scope = array_scope(builder)

    value = emit_coerced(gen, builder, Var("a"), "i32*", scope)
    assert value.opname == "extractvalue"
    assert value.type == ir.PointerType(ir.IntType(32))


def test_array_does_not_decay_to_a_mismatched_pointer(env):
    """
    An array only decays to a pointer of its own element type.
    """
    from siec.codegen.expressions import emit_coerced

    gen, builder = env
    scope = array_scope(builder)

    with pytest.raises(TypeError, match="cannot implicitly convert"):
        emit_coerced(gen, builder, Var("a"), "i64*", scope)


def test_array_casts_to_its_element_pointer(env):
    """
    An 'arr as X*' cast extracts the array's data pointer.
    """
    from siec.ast import Cast
    from siec.codegen.expressions import emit_cast

    gen, builder = env
    scope = array_scope(builder)

    value = emit_cast(gen, builder, Cast(Var("a"), "i32*"), scope)
    assert value.opname == "extractvalue"
    assert value.type == ir.PointerType(ir.IntType(32))


def test_array_cast_to_a_mismatched_pointer_is_an_error(env):
    """
    Casting an array to a pointer of a different element type is rejected.
    """
    from siec.ast import Cast
    from siec.codegen.expressions import emit_cast

    gen, builder = env
    scope = array_scope(builder)

    with pytest.raises(TypeError, match="cannot cast"):
        emit_cast(gen, builder, Cast(Var("a"), "i64*"), scope)
