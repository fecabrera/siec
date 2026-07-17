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


def test_struct_fields_keep_pointer_and_struct_types(ts):
    """
    Field types are parsed like any other type annotation.
    """
    assert parse_struct(ts("struct S { p: i32*; inner: T; }")) == Struct(
        "S", [Field("p", "i32*"), Field("inner", "T")])


def test_forward_declaration_has_no_fields(ts):
    """
    'struct Name;' parses to a Struct with None fields, marking a forward declaration.
    """
    assert parse_struct(ts("struct Handle;")) == Struct("Handle", None)


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
