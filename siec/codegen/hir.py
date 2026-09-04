"""Typed HIR annotations attached to AST expressions during checking.

Checking records semantic decisions once; emission reads them instead of
re-inferring. Annotations live on the expression nodes themselves (dynamic
attributes) so macro identity, hoisting, and deepcopy paths stay unchanged.
Production emission requires these stamps. Isolated emitter tests can use
the legacy path when they do not run the compiler phases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from siec.ast import Expr

CoerceKind = Literal[
    "identity",
    "sign_extend",
    "zero_extend",
    "float_extend",
    "array_decay",
    "opaque",
    "null",
    "adopt",
    "option_wrap",
    "option_decay",
    "aggregate",
    "block",
    "tuple",
    "array",
    "array_literal_decay",
    "function_reference",
]

ValueCategory = Literal[
    "rvalue",
    "lvalue",
    "reference",
    "temporary",
]

BinaryKind = Literal[
    "rewrite",
    "logical",
    "pointer_offset",
    "arithmetic",
    "power",
    "comparison",
]


@dataclass(frozen=True)
class CallPlan:
    """The call target and receiver action selected during Check."""

    kind: Literal["direct", "indirect", "constructor", "rewrite"]
    symbol: str | None = None
    receiver: Expr | None = None
    passes_receiver: bool = False
    constructor_type: str | None = None
    indirect_type: str | None = None
    indirect_symbol: str | None = None
    replacement: Expr | None = None


@dataclass(frozen=True)
class CoercionPlan:
    """One implicit conversion selected during Check."""

    source: str | None
    target: str
    kind: CoerceKind
    nested: CoercionPlan | None = None
    symbol: str | None = None
    const_target: bool = False


@dataclass(frozen=True)
class BinaryPlan:
    """One binary operation selected during Check."""

    kind: BinaryKind
    result: str
    left_target: str | None = None
    right_target: str | None = None
    instruction: str | None = None
    unsigned: bool = False
    float_operation: bool = False
    pointer: Expr | None = None
    index: Expr | None = None
    index_target: str | None = None
    subtract: bool = False
    replacement: Expr | None = None


@dataclass(frozen=True)
class ForeachPlan:
    """The iterator protocol selected for one foreach statement."""

    iterator_type: str
    iterator_call: Expr | None
    has_next_call: Expr
    next_call: Expr
    has_next_symbol: str
    next_symbol: str
    element_reference_type: str


@dataclass(frozen=True)
class TypedExpr:
    """
    View of the typed-HIR fields stamped on an expression.

    Every field is optional so partial annotations remain valid while
    emission migrates site by site.
    """

    sie_type: str | None = None
    expected_type: str | None = None
    coerce_to: str | None = None
    coerce_kind: CoerceKind | None = None
    coercion_plan: CoercionPlan | None = None
    aggregate_plan: object | None = None
    binary_plan: BinaryPlan | None = None
    foreach_plan: ForeachPlan | None = None
    resolved_symbol: str | None = None
    call_plan: CallPlan | None = None
    truthy_symbol: str | None = None
    truthy_plan: CallPlan | None = None
    field_index: int | None = None
    field_type: str | None = None
    value_category: ValueCategory | None = None
    const_value: int | float | bool | str | None = None
    span: tuple[str | None, int] | None = None


_TYPED_ATTRS = (
    "sie_type",
    "expected_type",
    "coerce_to",
    "coerce_kind",
    "coercion_plan",
    "aggregate_plan",
    "binary_plan",
    "foreach_plan",
    "resolved_symbol",
    "call_plan",
    "truthy_symbol",
    "truthy_plan",
    "field_index",
    "field_type",
    "value_category",
    "const_value",
    "hir_span",
)


def typed(expr: Expr | object) -> TypedExpr:
    """Read stamped HIR fields from an expression into a TypedExpr view."""
    span = getattr(expr, "hir_span", None)
    return TypedExpr(
        sie_type=getattr(expr, "sie_type", None),
        expected_type=getattr(expr, "expected_type", None),
        coerce_to=getattr(expr, "coerce_to", None),
        coerce_kind=getattr(expr, "coerce_kind", None),
        coercion_plan=getattr(expr, "coercion_plan", None),
        aggregate_plan=getattr(expr, "aggregate_plan", None),
        binary_plan=getattr(expr, "binary_plan", None),
        foreach_plan=getattr(expr, "foreach_plan", None),
        resolved_symbol=getattr(expr, "resolved_symbol", None),
        call_plan=getattr(expr, "call_plan", None),
        truthy_symbol=getattr(expr, "truthy_symbol", None),
        truthy_plan=getattr(expr, "truthy_plan", None),
        field_index=getattr(expr, "field_index", None),
        field_type=getattr(expr, "field_type", None),
        value_category=getattr(expr, "value_category", None),
        const_value=getattr(expr, "const_value", None),
        span=span,
    )


def stamp(expr: Expr | object, **fields) -> None:
    """
    Attach newly known HIR fields to ``expr``.

    Existing non-None values are kept unless ``overwrite`` is true, so a
    later generic re-check cannot erase a more specific resolution.
    """
    overwrite = fields.pop("overwrite", False)
    if "span" in fields:
        fields["hir_span"] = fields.pop("span")

    for name, value in fields.items():
        if value is None:
            continue
        if name not in _TYPED_ATTRS and name != "hir_span":
            raise TypeError(f"unknown HIR field {name!r}")
        if not overwrite and getattr(expr, name, None) is not None:
            continue
        setattr(expr, name, value)


def copy_typed(source: Expr | object, target: Expr | object) -> None:
    """Copy all checked expression annotations to an equivalent HIR node."""
    for name in _TYPED_ATTRS:
        if (value := getattr(source, name, None)) is not None:
            setattr(target, name, value)


def annotate_result(expr: Expr | object, result: str | None,
                    expected: str | None = None, *,
                    line: int = 0, file: str | None = None) -> str | None:
    """
    Stamp an expression's resolved type after a successful check.

    Returns ``result`` unchanged: callers that bind inferred types (including
    ``const`` views) must see the checked type, not the expectation that
    drove coercion.
    """
    if result is not None:
        stamp(expr, sie_type=result)
    if expected is not None:
        stamp(expr, expected_type=expected)
        if result is None:
            stamp(expr, sie_type=expected)
    if line or file:
        stamp(expr, span=(file, line))
    return result


def resolved_callee(expr: Expr | object) -> str | None:
    """The callee symbol stamped during checking, if any."""
    return getattr(expr, "resolved_symbol", None)


def checked_call(expr: Expr | object) -> CallPlan | None:
    """Return the call plan recorded during Check, if present."""
    return getattr(expr, "call_plan", None)


def checked_coercion(expr: Expr | object) -> CoercionPlan | None:
    """Return the implicit conversion selected during Check, if present."""
    return getattr(expr, "coercion_plan", None)


def checked_aggregate(expr: Expr | object):
    """Return the aggregate field plan recorded during Check, if present."""
    return getattr(expr, "aggregate_plan", None)


def checked_binary(expr: Expr | object) -> BinaryPlan | None:
    """Return the binary-operation plan recorded during Check, if present."""
    return getattr(expr, "binary_plan", None)


def checked_foreach(stmt: object) -> ForeachPlan | None:
    """Return the iterator protocol selected during Check, if present."""
    return getattr(stmt, "foreach_plan", None)
