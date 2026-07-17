"""Tests for parsing cast expressions."""

from siec.ast import BinaryOp, Cast, UnaryOp, Var
from siec.parser.expressions import parse_expression


def test_cast(ts):
    """
    'x as T' parses to a Cast over the value.
    """
    assert parse_expression(ts("x as i32")) == Cast(Var("x"), "i32")


def test_cast_to_pointer_and_array_types(ts):
    """
    A cast target is parsed like any other type annotation.
    """
    assert parse_expression(ts("x as u8*")) == Cast(Var("x"), "u8*")


def test_casts_chain_left_to_right(ts):
    """
    Consecutive casts nest, applying in source order.
    """
    assert parse_expression(ts("x as i64 as u8")) == Cast(Cast(Var("x"), "i64"), "u8")


def test_cast_binds_tighter_than_binary_operators(ts):
    """
    'a as u32 + b' casts a before adding.
    """
    assert parse_expression(ts("a as u32 + b")) == BinaryOp(
        "+", Cast(Var("a"), "u32"), Var("b"))


def test_cast_binds_looser_than_unary_and_power(ts):
    """
    '-a ** b as i32' casts the whole power expression, not just b.
    """
    assert parse_expression(ts("-a ** b as i32")) == Cast(
        BinaryOp("**", UnaryOp("-", Var("a")), Var("b")), "i32")
