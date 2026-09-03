"""Semantic field selection for aggregate literals."""

from dataclasses import dataclass

from siec.ast import AggregateLiteral, Expr, Field
from siec.codegen.generator import CodeGenerator
from siec.codegen.inference import check_field_access, type_info
from siec.codegen.types import is_aliasing, is_const, strip_const


@dataclass(frozen=True)
class AggregateElementPlan:
    """One literal element and the aggregate field that it initializes."""

    index: int
    field: Field
    value: Expr | None
    target: str


@dataclass(frozen=True)
class AggregatePlan:
    """The complete field mapping selected for one aggregate literal."""

    target: str
    elements: tuple[AggregateElementPlan, ...]
    omitted: tuple[AggregateElementPlan, ...]
    is_union: bool = False


def resolve_aggregate(gen: CodeGenerator, literal: AggregateLiteral,
                      target_name: str) -> AggregatePlan:
    """Resolve and validate an aggregate literal's field mapping."""
    canonical = strip_const(target_name)
    info = type_info(gen, canonical)
    if info is None or info.fields is None:
        raise TypeError(
            f"aggregate initializer needs a struct or array type, not "
            f"{target_name!r}")

    fields = info.fields
    if info.is_union:
        from siec.codegen.unions import literal_field

        index, field, value = literal_field(info, literal, canonical)
        check_field_access(gen, target_name, field)
        element = AggregateElementPlan(
            index, field, value, _field_target(target_name, field))
        return AggregatePlan(target_name, (element,), (), is_union=True)

    if literal.names is None:
        if len(literal.elements) != len(fields):
            raise TypeError(
                f"aggregate literal has {len(literal.elements)} elements, "
                f"expected {len(fields)}")
        selected = list(enumerate(literal.elements))
        omitted_indexes = []
    else:
        index_of = {field.name: index for index, field in enumerate(fields)}
        seen = set()
        selected = []
        for name, value in zip(literal.names, literal.elements):
            if name not in index_of:
                raise TypeError(
                    f"aggregate literal names unknown field {name!r}")
            if name in seen:
                raise TypeError(
                    f"aggregate literal sets field {name!r} more than once")
            seen.add(name)
            selected.append((index_of[name], value))
        omitted_indexes = [
            index for index, field in enumerate(fields)
            if field.name not in seen
        ]

    elements = []
    for index, value in selected:
        field = fields[index]
        check_field_access(gen, target_name, field)
        elements.append(AggregateElementPlan(
            index, field, value, _field_target(target_name, field)))

    omitted = tuple(
        AggregateElementPlan(
            index,
            fields[index],
            fields[index].default,
            _field_target(target_name, fields[index]),
        )
        for index in omitted_indexes
    )
    return AggregatePlan(target_name, tuple(elements), omitted)


def _field_target(target_name: str, field: Field) -> str:
    """Apply an aggregate's const view to one aliasing field."""
    field_type = field.type
    if (is_const(target_name) and is_aliasing(field_type)
            and not is_const(field_type)):
        return f"const {field_type}"
    return field_type
