"""Resolution of borrowed and consuming plain-assignment operations."""

from dataclasses import dataclass

from siec.ast import (AggregateLiteral, ArrayLiteral, Index, Member,
                      MethodCall, Move, Var)
from siec.codegen.aliases import expand_alias
from siec.codegen.generator import CodeGenerator
from siec.codegen.generics import split_generic
from siec.codegen.inference import expr_sie_type, infer_type, item_call
from siec.codegen.interfaces import claimed_interfaces, type_implements
from siec.codegen.types import strip_const, strip_reference


@dataclass
class AssignmentAction:
    """Either an in-place operator call or a value to store directly."""
    call: MethodCall | None
    value: object | None


def assignment_action(gen: CodeGenerator, target, target_type: str,
                      value, scope: dict) -> AssignmentAction:
    """
    Select assignment by RHS ownership.

    A named place borrows and prefers ``AssignFrom<B>``. ``move`` and
    temporary values consume and prefer ``Assign<B>``. A same-type borrowed
    assignment falls back through ``Clone`` before ordinary storage coercion.
    """
    # A trait-indexed target is not an addressable V. Its SetItem contract
    # owns the write, so assignment dispatch applies inside that method.
    if isinstance(target, Index) and item_call(
            gen, target, scope, "set_item", value) is not None:
        return AssignmentAction(None, value)

    explicit_move = isinstance(value, Move)
    consuming = explicit_move or not isinstance(
        value, (Var, Member, Index))
    if (isinstance(value, Move) and isinstance(value.operand, Var)
            and isinstance(target, Var)
            and value.operand.name == target.name):
        raise TypeError(f"cannot move {target.name!r} into itself")
    source = value.operand if isinstance(value, Move) else value
    source_type = expr_sie_type(gen, source, scope) or infer_type(
        gen, source, scope)
    if source_type is None and consuming and isinstance(
            source, (AggregateLiteral, ArrayLiteral)):
        source_type = target_type
    if source_type is None:
        return AssignmentAction(None, source)

    target_type = canonical_value_type(gen, target_type)
    source_type = canonical_value_type(gen, source_type)

    if consuming:
        if accepts_source(
                gen, target_type, "Assign", source_type, source):
            return AssignmentAction(
                MethodCall(target, "assign", [value]), None)
        # An unnamed temporary may be borrowed for this call when no
        # consuming action exists. An explicit move, on the other hand,
        # promises consumption and never silently becomes a borrow.
        if (not explicit_move and accepts_source(
                gen, target_type, "AssignFrom", source_type, source)):
            return AssignmentAction(
                MethodCall(target, "assign_from", [source]), None)
        return AssignmentAction(None, value)

    if accepts_source(
            gen, target_type, "AssignFrom", source_type, source):
        return AssignmentAction(
            MethodCall(target, "assign_from", [source]), None)

    if (target_type == source_type
            and type_implements(gen, source_type, "Clone")):
        return AssignmentAction(
            None, MethodCall(source, "clone", []))

    return AssignmentAction(None, source)


def canonical_value_type(gen: CodeGenerator, spelling: str) -> str:
    """Canonicalize one non-reference value type for an interface claim."""
    return strip_const(strip_reference(
        expand_alias(gen, spelling, checked=False)))


def accepts_source(gen: CodeGenerator, target: str, interface: str,
                   source: str, expression) -> bool:
    """Whether one assignment claim accepts the concrete source type."""
    required = f"{interface}<{source}>"
    if type_implements(gen, target, required):
        return True

    # An interface-typed parameter specializes to its concrete argument in
    # the caller. Keep matching it against the contract named by the claim:
    # AssignFrom<Iterable<char>> accepts a char[] even though the source's
    # carried type at this point is char[].
    for claim in claimed_interfaces(gen, target):
        parts = split_generic(claim)
        if parts is None or parts[0] != interface or len(parts[1]) != 1:
            continue
        contract = parts[1][0]
        contract_base = (split_generic(contract) or (contract, []))[0]
        if (contract_base in gen.interfaces
                and type_implements(gen, source, contract)):
            return True

        # Assignment follows ordinary value conversion rules as well. In
        # particular, an i32 source selects AssignFrom<i64> through the
        # documented same-family widening conversion.
        from siec.codegen.overloads import parameter_fit

        if parameter_fit(gen, expression, source, contract) is not None:
            return True

    return False
