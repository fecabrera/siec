"""Tests for struct registration and member access codegen."""

import pytest
from llvmlite import ir

from siec.ast import AggregateLiteral, Field, IntLiteral, Member, MemberAssign, Program, Struct, Var
from siec.codegen.expressions import emit_expression, emit_lvalue, signedness
from siec.codegen.generator import Variable
from siec.codegen.statements import emit_statement
from siec.codegen.structs import register_structs
from siec.codegen.types import resolve_type


def point(gen):
    """
    Register a 'Point { x: i32; y: u32; }' struct into the generator.
    """
    register_structs(gen, Program([], [], [
        Struct("Point", [Field("x", "i32"), Field("y", "u32")])]))
    return gen.structs["Point"]


def test_register_creates_an_identified_struct_type(env):
    """
    Registration builds an identified LLVM struct with the fields' types as its body.
    """
    gen, _ = env
    info = point(gen)
    assert info.type.name == "Point"
    assert info.type.elements == (ir.IntType(32), ir.IntType(32))


def test_register_rejects_duplicate_structs(env):
    """
    Declaring two structs with the same name raises a TypeError.
    """
    gen, _ = env
    program = Program([], [], [Struct("S", []), Struct("S", [])])
    with pytest.raises(TypeError, match="declared more than once"):
        register_structs(gen, program)


def test_forward_declaration_takes_fields_from_the_definition(env):
    """
    A bodiless declaration registers the type; a later definition fills its fields.
    """
    gen, _ = env
    register_structs(gen, Program([], [], [
        Struct("S", None), Struct("S", [Field("x", "i32")])]))

    info = gen.structs["S"]
    assert info.field("x") == (0, "i32")
    assert info.type.elements == (ir.IntType(32),)


def test_forward_declaration_after_the_definition_is_allowed(env):
    """
    A forward declaration may also follow the definition, changing nothing.
    """
    gen, _ = env
    register_structs(gen, Program([], [], [
        Struct("S", [Field("x", "i32")]), Struct("S", None)]))
    assert gen.structs["S"].field("x") == (0, "i32")


def test_opaque_struct_resolves_only_behind_a_pointer(env):
    """
    A struct never given a body resolves as a pointer, but not by value.
    """
    gen, _ = env
    register_structs(gen, Program([], [], [Struct("Handle", None)]))

    assert resolve_type("Handle*", gen.structs) == ir.PointerType(
        gen.structs["Handle"].type)
    with pytest.raises(TypeError, match="has no body"):
        resolve_type("Handle", gen.structs)


def test_opaque_struct_has_no_fields_to_access(env):
    """
    Member access on an opaque struct reports the missing field.
    """
    gen, _ = env
    register_structs(gen, Program([], [], [Struct("Handle", None)]))
    with pytest.raises(TypeError, match="has no field 'x'"):
        gen.structs["Handle"].field("x")


def test_struct_fields_may_reference_other_structs(env):
    """
    A field typed by another struct resolves to that struct's type.
    """
    gen, _ = env
    register_structs(gen, Program([], [], [
        Struct("Point", [Field("x", "i32")]),
        Struct("Line", [Field("from", "Point"), Field("to", "Point")])]))
    assert gen.structs["Line"].type.elements == (
        gen.structs["Point"].type, gen.structs["Point"].type)


def test_resolve_type_finds_registered_structs(env):
    """
    A struct name resolves through the registry, including as a pointer.
    """
    gen, _ = env
    info = point(gen)
    assert resolve_type("Point", gen.structs) == info.type
    assert resolve_type("Point*", gen.structs) == ir.PointerType(info.type)


def test_member_read_extracts_the_field(env):
    """
    Reading a field emits an extractvalue at the field's index.
    """
    gen, builder = env
    info = point(gen)
    scope = {"p": Variable(builder.alloca(info.type, name="p"), "Point")}

    value = emit_expression(gen, builder, Member(Var("p"), "y"), None, scope)
    assert value.opname == "extractvalue"
    assert value.type == ir.IntType(32)


def test_member_lvalue_geps_to_the_field(env):
    """
    The address of a field is a gep past the struct to the field index.
    """
    gen, builder = env
    info = point(gen)
    scope = {"p": Variable(builder.alloca(info.type, name="p"), "Point")}

    ptr = emit_lvalue(gen, builder, Member(Var("p"), "x"), scope)
    assert ptr.opname == "getelementptr"
    assert ptr.type == ir.PointerType(ir.IntType(32))


def test_member_assignment_stores_into_the_field(env):
    """
    A member assignment stores into the field's slot.
    """
    gen, builder = env
    info = point(gen)
    scope = {"p": Variable(builder.alloca(info.type, name="p"), "Point")}

    from siec.ast import IntLiteral
    emit_statement(gen, builder, MemberAssign(Var("p"), "x", IntLiteral(7)), scope)
    assert "store i32 7" in str(builder.function)


def test_unknown_field_is_an_error(env):
    """
    Accessing a field the struct doesn't declare raises a TypeError.
    """
    gen, builder = env
    info = point(gen)
    scope = {"p": Variable(builder.alloca(info.type, name="p"), "Point")}

    with pytest.raises(TypeError, match="has no field 'z'"):
        emit_expression(gen, builder, Member(Var("p"), "z"), None, scope)


def test_member_on_a_non_struct_is_an_error(env):
    """
    Selecting a field from a non-struct value raises a TypeError.
    """
    gen, builder = env
    point(gen)
    scope = {"n": Variable(builder.alloca(ir.IntType(32), name="n"), "i32")}

    with pytest.raises(TypeError, match="non-struct type"):
        emit_expression(gen, builder, Member(Var("n"), "x"), None, scope)


def test_field_signedness_is_inferred(env):
    """
    A field's signedness comes from its declared type, catching mixed operations.
    """
    gen, builder = env
    info = point(gen)
    scope = {"p": Variable(builder.alloca(info.type, name="p"), "Point")}

    assert signedness(gen, Member(Var("p"), "x"), scope) == "signed"
    assert signedness(gen, Member(Var("p"), "y"), scope) == "unsigned"


def test_aggregate_literal_fills_the_struct(env):
    """
    A '{a, b}' literal builds the struct by inserting each element by position.
    """
    gen, builder = env
    info = point(gen)

    literal = AggregateLiteral([IntLiteral(3), IntLiteral(4)])
    value = emit_expression(gen, builder, literal, info.type, {})
    assert value.opname == "insertvalue"
    assert value.type == info.type


def test_aggregate_literal_element_count_must_match(env):
    """
    An aggregate with the wrong number of elements raises a TypeError.
    """
    gen, builder = env
    info = point(gen)

    with pytest.raises(TypeError, match="expected 2"):
        emit_expression(gen, builder, AggregateLiteral([IntLiteral(3)]), info.type, {})


def test_aggregate_literal_needs_an_aggregate_type(env):
    """
    An aggregate literal used without a struct or array context raises a TypeError.
    """
    gen, builder = env
    with pytest.raises(TypeError, match="needs a struct or array type"):
        emit_expression(gen, builder, AggregateLiteral([IntLiteral(3)]), ir.IntType(32), {})
