"""Tests for siec.codegen.types."""

import pytest
from llvmlite import ir

from siec.codegen.types import SCALAR_TYPES, fn_type_parts, resolve_type


def test_none_resolves_to_void():
    """
    A missing return type resolves to LLVM void.
    """
    assert isinstance(resolve_type(None), ir.VoidType)


@pytest.mark.parametrize("name,width", [("i8", 8), ("i16", 16), ("i32", 32), ("i64", 64),
                                        ("u8", 8), ("u16", 16), ("u32", 32), ("u64", 64)])
def test_integer_types(name, width):
    """
    Signed and unsigned integer names resolve to LLVM ints of their width.
    """
    assert resolve_type(name) == ir.IntType(width)


def test_float_types():
    """
    'f32' and 'f64' resolve to LLVM float and double.
    """
    assert resolve_type("f32") == ir.FloatType()
    assert resolve_type("f64") == ir.DoubleType()


def test_bool_and_char():
    """
    'bool' resolves to i1 and 'char' to i8.
    """
    assert resolve_type("bool") == ir.IntType(1)
    assert resolve_type("char") == ir.IntType(8)


def test_pointer_types_wrap_per_star():
    """
    Each trailing '*' wraps the base type in one more pointer.
    """
    assert resolve_type("i32*") == ir.PointerType(ir.IntType(32))
    assert resolve_type("u8**") == ir.PointerType(ir.PointerType(ir.IntType(8)))


def test_array_type_lowers_to_a_fat_struct():
    """
    An 'X[]' array lowers to a struct of a pointer to X and an i64 length.
    """
    assert resolve_type("i32[]") == ir.LiteralStructType(
        [ir.PointerType(ir.IntType(32)), ir.IntType(64)])


def test_pointer_to_array_wraps_the_fat_struct():
    """
    A trailing '*' on an array type wraps the fat struct in a pointer.
    """
    array = ir.LiteralStructType([ir.PointerType(ir.IntType(32)), ir.IntType(64)])
    assert resolve_type("i32[]*") == ir.PointerType(array)


def test_array_of_pointers_keeps_the_inner_star():
    """
    A '*' before '[]' belongs to the element type, not a trailing pointer.
    """
    assert resolve_type("i32*[]") == ir.LiteralStructType(
        [ir.PointerType(ir.PointerType(ir.IntType(32))), ir.IntType(64)])


def test_opaque_pointer_lowers_to_i8_pointer():
    """
    'opaque*' lowers to i8*, like C's void*.
    """
    assert resolve_type("opaque*") == ir.PointerType(ir.IntType(8))


def test_bare_opaque_is_an_error():
    """
    'opaque' without a '*' raises a TypeError.
    """
    with pytest.raises(TypeError, match="can only be used as a pointer"):
        resolve_type("opaque")


def test_unknown_type_is_an_error():
    """
    An unrecognized type name raises a TypeError naming it.
    """
    with pytest.raises(TypeError, match="unknown type 'wat'"):
        resolve_type("wat")


def test_fn_type_parts_splits_params_and_return():
    """
    A canonical fn name splits into parameter names, return name, and suffix.
    """
    assert fn_type_parts("fn()") == ([], None, "")
    assert fn_type_parts("fn(i32,u8*)->bool") == (["i32", "u8*"], "bool", "")
    assert fn_type_parts("fn()*") == ([], None, "*")


def test_fn_type_parts_keeps_nested_fn_types_whole():
    """
    Nested function types stay unsplit inside the parameter list.
    """
    assert fn_type_parts("fn(fn()->i32,i8)->fn()") == (["fn()->i32", "i8"], "fn()", "")


def test_fn_type_resolves_to_a_function_pointer():
    """
    A function reference type lowers to a pointer to the LLVM signature.
    """
    fn_type = ir.FunctionType(ir.IntType(32), [ir.IntType(32)])
    assert resolve_type("fn(i32)->i32") == ir.PointerType(fn_type)


def test_fn_type_without_return_resolves_to_void():
    """
    A function reference with no '->' returns void.
    """
    assert resolve_type("fn()") == ir.PointerType(ir.FunctionType(ir.VoidType(), []))


def test_fn_type_return_pointer_belongs_to_the_return():
    """
    A trailing '*' after '->' is part of the return type, not the reference.
    """
    fn_type = ir.FunctionType(ir.PointerType(ir.IntType(32)), [])
    assert resolve_type("fn()->i32*") == ir.PointerType(fn_type)


def test_scalar_table_covers_the_documented_builtins():
    """
    Every builtin type from the README is present in the scalar table.
    """
    assert {"i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64",
            "f32", "f64", "bool", "char"} <= set(SCALAR_TYPES)
