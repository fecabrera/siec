"""Tests for siec.parser.types."""

import pytest

from siec.parser.types import parse_type


def test_scalar_type(ts):
    """
    A bare type name parses to itself.
    """
    assert parse_type(ts("i32")) == "i32"


def test_pointer_type(ts):
    """
    A trailing '*' is folded into the type name.
    """
    assert parse_type(ts("u8*")) == "u8*"


def test_array_type(ts):
    """
    An array type is a type followed by an empty '[]'.
    """
    assert parse_type(ts("u8[]")) == "u8[]"


def test_array_type_consumes_its_brackets(ts):
    """
    An unsized array consumes its closing ']', leaving following tokens untouched.
    """
    stream = ts("u8[], next")
    assert parse_type(stream) == "u8[]"
    assert stream.peek().value == ","


def test_sized_array_type(ts):
    """
    An array type may carry a size between its brackets.
    """
    assert parse_type(ts("u8[4]")) == "u8[4]"


def test_sized_array_type_accepts_hex(ts):
    """
    A hex size normalizes to decimal in the type name.
    """
    assert parse_type(ts("u8[0x10]")) == "u8[16]"


def test_const_type(ts):
    """
    A leading 'const' is kept as a prefix on the canonical name.
    """
    assert parse_type(ts("const i32")) == "const i32"


def test_const_pointer_type(ts):
    """
    'const' covers the whole suffixed type that follows it.
    """
    assert parse_type(ts("const char*")) == "const char*"
    assert parse_type(ts("const i32[]")) == "const i32[]"


def test_array_of_pointers_type(ts):
    """
    An array's element type may itself be a pointer.
    """
    assert parse_type(ts("char*[]")) == "char*[]"


def test_nested_array_type(ts):
    """
    An array's element type may itself be an array.
    """
    assert parse_type(ts("char[][]")) == "char[][]"


def test_pointer_to_array_type(ts):
    """
    A '*' may follow an array suffix, making a pointer to the array.
    """
    assert parse_type(ts("char[]*")) == "char[]*"


def test_nested_pointer_type(ts):
    """
    Multiple trailing '*'s all fold into the type name.
    """
    assert parse_type(ts("char***")) == "char***"


def test_stops_after_the_type(ts):
    """
    Parsing consumes only the type, leaving following tokens untouched.
    """
    stream = ts("i32, next")
    assert parse_type(stream) == "i32"
    assert stream.peek().value == ","


def test_type_must_start_with_identifier(ts):
    """
    A type not starting with an identifier raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="expected 'ident'"):
        parse_type(ts("42"))


def test_fn_type_without_params_or_return(ts):
    """
    'fn()' parses to its canonical name.
    """
    assert parse_type(ts("fn()")) == "fn()"


def test_fn_type_with_return(ts):
    """
    A '->' return annotation joins the canonical name.
    """
    assert parse_type(ts("fn() -> i32")) == "fn()->i32"


def test_fn_type_with_params(ts):
    """
    Parameter types are comma-separated types, kept in order.
    """
    assert parse_type(ts("fn(i32, u8*) -> bool")) == "fn(i32,u8*)->bool"


def test_fn_type_nests(ts):
    """
    A parameter or return type may itself be a function reference.
    """
    assert parse_type(ts("fn(fn() -> i32) -> fn()")) == "fn(fn()->i32)->fn()"


def test_fn_type_requires_parens(ts):
    """
    'fn' without a parameter list raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match=r"expected '\('"):
        parse_type(ts("fn;"))
