"""Tests for siec.parser.constants."""

import pytest

from siec.ast import BinaryOp, Const, FloatLiteral, IntLiteral, Var
from siec.parser.constants import parse_const
from siec.parser.functions import parse_program


def test_annotated_constant(ts):
    """
    '@const name: T = value;' parses name, type, and value.
    """
    assert parse_const(ts("@const SIZE: u64 = 64;")) == Const(
        "SIZE", "u64", IntLiteral(64))


def test_inferred_constant(ts):
    """
    The type annotation may be omitted, leaving it None.
    """
    assert parse_const(ts("@const PI = 3.14;")) == Const("PI", None, FloatLiteral(3.14))


def test_private_const_decorator(ts):
    """
    A private constant uses declaration syntax after its decorator.
    """
    const = parse_program(ts("@private @const C = 0;")).consts[0]
    assert const.name == "C"
    assert const.is_private


def test_private_const_keeps_the_const_directive_spelling(ts):
    """
    '@private' stacks with '@const'; bare 'const' is not a declaration.
    """
    with pytest.raises(SyntaxError):
        parse_program(ts("@private const C = 0;"))


def test_constant_value_is_a_full_expression(ts):
    """
    The value may be any expression, including references to other constants.
    """
    assert parse_const(ts("@const TAU = PI * 2.0;")) == Const(
        "TAU", None, BinaryOp("*", Var("PI"), FloatLiteral(2.0)))


def test_constant_requires_an_initializer(ts):
    """
    '@const name;' without '= value' raises a SyntaxError.
    """
    with pytest.raises(SyntaxError, match="expected '='"):
        parse_const(ts("@const X;"))
