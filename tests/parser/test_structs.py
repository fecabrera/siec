"""Tests for siec.parser.structs."""

import pytest

from siec.ast import Field, Struct
from siec.parser.structs import parse_struct


def test_struct_with_fields(ts):
    """
    A struct parses to a Struct node with its ordered fields.
    """
    assert parse_struct(ts("struct Point { x: i32; y: i32; }")) == Struct(
        "Point", [Field("x", "i32"), Field("y", "i32")])


def test_empty_struct(ts):
    """
    A struct with no fields parses to a Struct with an empty field list.
    """
    assert parse_struct(ts("struct Empty { }")) == Struct("Empty", [])


def test_struct_methods_use_the_enclosing_receiver(ts):
    """Methods nested with fields retain the enclosing generic receiver."""
    struct = parse_struct(ts("""
        struct S<A> {
            value: A;
            fn f(&self, value: A);
            @inline fn g(const &self, values: A[]) -> A { return self.value; }
        }
    """))

    assert [field.name for field in struct.fields] == ["value"]
    assert [method.name for method in struct.actions] == ["S::f", "S::g"]
    assert all(method.receiver == "S" for method in struct.actions)
    assert all(method.receiver_params == ["A"] for method in struct.actions)
    assert struct.actions[0].params[0].type == "&S<A>"
    assert struct.actions[0].params[1].type == "A"
    assert struct.actions[0].body is None
    assert struct.actions[1].params[0].type == "const &S<A>"
    assert struct.actions[1].is_inline
    assert struct.actions[1].body is not None


def test_nested_methods_inherit_struct_bounds(ts):
    """A nested generic method carries the receiver's declared bounds."""
    struct = parse_struct(ts("""
        struct S<A: I1 & I2> {
            fn convert<U: I3>(const &self, value: U) -> A;
        }
    """))

    method = struct.actions[0]
    assert method.receiver_constraints == {"A": ("I1", "I2")}
    assert method.type_params == ["U"]
    assert method.constraints == {"U": "I3"}


def test_nested_receiver_template_constrains_an_override(ts):
    """A nested '@where' decorates a method on the enclosing family."""
    struct = parse_struct(ts("""
        struct S<A, B> {
            @where<A: Iface>
            @override
            fn f(const &self) -> B;
        }
    """))

    method = struct.actions[0]
    assert method.receiver == "S"
    assert method.receiver_params == ["A", "B"]
    assert method.receiver_constraints == {"A": "Iface"}
    assert method.is_override


def test_struct_fields_keep_pointer_and_struct_types(ts):
    """
    Field types are parsed like any other type annotation.
    """
    assert parse_struct(ts("struct S { p: i32*; inner: T; }")) == Struct(
        "S", [Field("p", "i32*"), Field("inner", "T")])


def test_generic_struct_bounds(ts):
    """
    Structs and interfaces retain optional type-like bounds by parameter.
    """
    struct = parse_struct(
        ts("struct Map<K: Hashable, V: Pair<u64, i32>>;"))

    assert struct.params == ["K", "V"]
    assert struct.constraints == {
        "K": "Hashable",
        "V": "Pair<u64,i32>",
    }


def test_forward_declaration_has_no_fields(ts):
    """
    'struct Name;' parses to a Struct with None fields, marking a forward declaration.
    """
    assert parse_struct(ts("struct Handle;")) == Struct("Handle", None)


def test_private_forward_declaration(ts):
    """
    '@private struct S;' records import visibility on an opaque type.
    """
    struct = parse_struct(ts("@private struct Handle;"))
    assert struct.name == "Handle"
    assert struct.fields is None
    assert struct.is_private


def test_private_field(ts):
    """'@private' on a field marks it reachable only from the struct's methods."""
    struct = parse_struct(ts("""
        struct S {
            @private handle: opaque*;
            value: i32;
        }
    """))
    assert struct.fields[0] == Field("handle", "opaque*", is_private=True)
    assert struct.fields[1] == Field("value", "i32")


def test_forward_declaration_consumes_its_semicolon(ts):
    """
    A forward declaration consumes its ';', leaving following tokens untouched.
    """
    stream = ts("struct Handle; next")
    assert parse_struct(stream) == Struct("Handle", None)
    assert stream.peek().value == "next"


def test_struct_field_requires_a_semicolon(ts):
    """
    A field missing its ';' raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="expected ';'"):
        parse_struct(ts("struct S { x: i32 }"))


def test_struct_allows_a_trailing_semicolon(ts):
    """
    A ';' after the closing brace is accepted and consumed.
    """
    stream = ts("struct S { x: i32; }; next")
    assert parse_struct(stream) == Struct("S", [Field("x", "i32")])
    assert stream.peek().value == "next"


def test_struct_trailing_semicolon_is_optional(ts):
    """
    A struct without a trailing ';' leaves the following token untouched.
    """
    stream = ts("struct S { x: i32; } next")
    assert parse_struct(stream) == Struct("S", [Field("x", "i32")])
    assert stream.peek().value == "next"


def test_packed_decorator(ts):
    """
    '@packed struct' marks the struct padding-free.
    """
    assert parse_struct(ts("@packed struct S { x: i32; }")).packed


def test_align_decorator(ts):
    """
    '@align(N)' records the allocation alignment, hex included.
    """
    assert parse_struct(ts("@align(16) struct S { x: i32; }")).align == 16
    assert parse_struct(ts("@align(0x40) struct S { x: i32; }")).align == 64


def test_struct_decorators_stack(ts):
    """
    '@packed @align(N)' applies both, in either order.
    """
    struct = parse_struct(ts("@align(8) @packed struct S { x: i32; }"))
    assert struct.packed
    assert struct.align == 8


def test_alignment_must_be_a_power_of_two(ts):
    """
    LLVM alignments are powers of two; anything else is rejected.
    """
    with pytest.raises(SyntaxError, match="power of two, not 6"):
        parse_struct(ts("@align(6) struct S { x: i32; }"))


def test_unknown_struct_decorator_is_an_error(ts):
    """
    A struct decorator other than '@packed' or '@align' is rejected.
    """
    with pytest.raises(SyntaxError, match="unknown struct decorator '@both'"):
        parse_struct(ts("@both struct S { x: i32; }"))


def test_volatile_decorator(ts):
    """
    '@volatile struct' marks every access to its values volatile.
    """
    assert parse_struct(ts("@volatile struct S { x: i32; }")).volatile


def test_volatile_stacks_with_layout_decorators(ts):
    """
    '@volatile @packed @align(N)' all apply to one struct.
    """
    struct = parse_struct(ts("@volatile @packed @align(4) struct S { x: i32; }"))
    assert struct.volatile
    assert struct.packed
    assert struct.align == 4
