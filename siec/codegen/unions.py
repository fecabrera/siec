"""Shared validation for literals selecting one union field."""

from siec.ast import AggregateLiteral


def literal_field(info, literal: AggregateLiteral, union_name: str):
    """Return the sole named ``(index, field, value)`` of a union literal."""
    if literal.names is None:
        raise TypeError(f"union {union_name!r} requires a named literal with "
                        "exactly one field")
    if len(literal.names) != 1:
        raise TypeError(f"union {union_name!r} literal must initialize "
                        "exactly one field")

    name = literal.names[0]
    for index, field in enumerate(info.fields):
        if field.name == name:
            return index, field, literal.elements[0]

    raise TypeError(f"aggregate literal names unknown field {name!r}")
