"""Tests for parsing unary operator expressions ('-', '~', 'not', '&', '*')."""

from siec.ast import BinaryOp, Index, IntLiteral, Member, UnaryOp, Var
from siec.parser.expressions import parse_expression


def test_unary_minus(ts):
    """
    A prefix '-' parses to a UnaryOp over its operand.
    """
    assert parse_expression(ts("-x")) == UnaryOp("-", Var("x"))


def test_unary_minus_folds_int_literals(ts):
    """
    '-' over an int literal folds to a negative constant with no UnaryOp.
    """
    assert parse_expression(ts("-5")) == IntLiteral(-5)


def test_folded_negative_literals_participate_in_arithmetic(ts):
    """
    '-2 * 3' multiplies the folded constant.
    """
    assert parse_expression(ts("-2 * 3")) == BinaryOp(
        "*", IntLiteral(-2), IntLiteral(3))


def test_unary_minus_over_a_negative_literal_does_not_fold_twice(ts):
    """
    '--5' folds the inner minus only, leaving a UnaryOp around the constant.
    """
    assert parse_expression(ts("--5")) == UnaryOp("-", IntLiteral(-5))


def test_unary_minus_binds_tighter_than_multiplication(ts):
    """
    '-a * b' negates a before multiplying.
    """
    assert parse_expression(ts("-a * b")) == BinaryOp(
        "*",
        UnaryOp("-", Var("a")),
        Var("b"),
    )


def test_unary_minus_nests(ts):
    """
    Repeated '-' prefixes nest.
    """
    assert parse_expression(ts("--x")) == UnaryOp("-", UnaryOp("-", Var("x")))


def test_unary_minus_applies_to_groups(ts):
    """
    '-' may negate a parenthesized expression.
    """
    assert parse_expression(ts("-(a + b)")) == UnaryOp(
        "-", BinaryOp("+", Var("a"), Var("b")))


def test_unary_minus_binds_tighter_than_power(ts):
    """
    '-a ** b' negates a before raising it.
    """
    assert parse_expression(ts("-a ** b")) == BinaryOp(
        "**", UnaryOp("-", Var("a")), Var("b"))


def test_unary_not(ts):
    """
    A prefix 'not' parses to a UnaryOp over its operand.
    """
    assert parse_expression(ts("not x")) == UnaryOp("not", Var("x"))


def test_unary_bitwise_not(ts):
    """
    A prefix '~' parses to a UnaryOp over its operand.
    """
    assert parse_expression(ts("~x")) == UnaryOp("~", Var("x"))


def test_unary_not_binds_tighter_than_and(ts):
    """
    'not a and b' negates a before conjoining.
    """
    assert parse_expression(ts("not a and b")) == BinaryOp(
        "and",
        UnaryOp("not", Var("a")),
        Var("b"),
    )


def test_address_of(ts):
    """
    A prefix '&' parses to a UnaryOp over its operand.
    """
    assert parse_expression(ts("&x")) == UnaryOp("&", Var("x"))


def test_address_of_takes_postfix_chains(ts):
    """
    '&' applies to a whole member or index chain, not just the base name.
    """
    assert parse_expression(ts("&p.x")) == UnaryOp("&", Member(Var("p"), "x"))
    assert parse_expression(ts("&arr[2]")) == UnaryOp(
        "&",
        Index(Var("arr"),
        IntLiteral(2),
    ))


def test_prefix_address_of_is_distinct_from_bitwise_and(ts):
    """
    'a & &b' masks a with b's address: infix '&' stays binary.
    """
    assert parse_expression(ts("a & &b")) == BinaryOp(
        "&",
        Var("a"),
        UnaryOp("&", Var("b")),
    )


def test_dereference(ts):
    """
    A prefix '*' parses to a UnaryOp over its operand.
    """
    assert parse_expression(ts("*p")) == UnaryOp("*", Var("p"))


def test_dereference_nests(ts):
    """
    '**pp' is two dereferences, despite lexing as one power token.
    """
    assert parse_expression(ts("**pp")) == UnaryOp("*", UnaryOp("*", Var("pp")))


def test_dereference_takes_postfix_chains(ts):
    """
    '*' applies to a whole member or index chain, not just the base name.
    """
    assert parse_expression(ts("*p.x")) == UnaryOp("*", Member(Var("p"), "x"))
    assert parse_expression(ts("*arr[2]")) == UnaryOp(
        "*",
        Index(Var("arr"),
        IntLiteral(2),
    ))


def test_prefix_dereference_is_distinct_from_multiplication(ts):
    """
    'a * *b' multiplies a by b's element: infix '*' stays binary.
    """
    assert parse_expression(ts("a * *b")) == BinaryOp(
        "*",
        Var("a"),
        UnaryOp("*", Var("b")),
    )


def test_prefix_dereference_is_distinct_from_power(ts):
    """
    'a ** *b' raises a to b's element: infix '**' stays a power.
    """
    assert parse_expression(ts("a ** *b")) == BinaryOp(
        "**",
        Var("a"),
        UnaryOp("*", Var("b")),
    )


def test_dereference_composes_with_address_of(ts):
    """
    '&' and '*' prefixes nest in either order.
    """
    assert parse_expression(ts("&*p")) == UnaryOp("&", UnaryOp("*", Var("p")))
    assert parse_expression(ts("*&x")) == UnaryOp("*", UnaryOp("&", Var("x")))
