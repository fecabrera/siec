"""Tests for normalized source-level callable arity."""

from siec.ast import IntLiteral, Param
from siec.codegen.arity import CallArity


def test_fixed_arity_accepts_only_the_declared_count():
    """A fixed callable has equal minimum and maximum counts."""
    arity = CallArity.from_parameters([Param("a", "i32"), Param("b", "i32")])

    assert arity.minimum == 2
    assert arity.maximum == 2
    assert arity.accepts(2)
    assert arity.error(1) == "too few"
    assert arity.error(3) == "too many"


def test_trailing_defaults_reduce_the_minimum_count():
    """Each trailing default makes one source argument optional."""
    arity = CallArity.from_parameters([
        Param("a", "i32"),
        Param("b", "i32", IntLiteral(1)),
        Param("c", "i32", IntLiteral(2)),
    ])

    assert arity.minimum == 1
    assert arity.maximum == 3
    assert arity.accepts(1)
    assert arity.accepts(3)


def test_sie_variadic_has_no_maximum_and_marks_the_pack():
    """A Sie variadic accepts extras and identifies its pack parameter."""
    arity = CallArity.from_parameters([
        Param("first", "i32"),
        Param("args", "const Any[]"),
    ], variadic=True)

    assert arity.minimum == 1
    assert arity.maximum is None
    assert arity.parameter_count == 2
    assert arity.variadic
    assert arity.accepts(8)


def test_c_varargs_has_no_maximum_without_a_sie_pack():
    """C varargs accept extras but keep all declared parameters fixed."""
    arity = CallArity.from_parameters(
        [Param("format", "char*")], var_arg=True)

    assert arity.minimum == 1
    assert arity.maximum is None
    assert not arity.variadic
    assert arity.accepts(8)


def test_implicit_receiver_is_removed_from_constructor_arity():
    """An implicit receiver does not count as a written call argument."""
    arity = CallArity.from_parameters([
        Param("self", "&S"),
        Param("value", "i32", IntLiteral(1)),
    ])

    source_arity = arity.without_prefix(1)
    assert source_arity.minimum == 0
    assert source_arity.maximum == 1
    assert source_arity.parameter_count == 1
