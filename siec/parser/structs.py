"""Parsing of struct declarations."""

from ..ast import Field, Struct
from .stream import TokenStream
from .types import parse_type


def parse_struct(ts: TokenStream) -> Struct:
    """
    Parse a struct declaration: 'struct Name { a: A; b: B; }', with an optional
    trailing ';', or a bodiless forward declaration 'struct Name;'.
    """
    line = ts.peek().line
    ts.expect("kw", "struct")
    name = ts.expect("ident").value

    # a ';' in place of a body is a forward declaration, leaving the fields
    # to a later definition — or to none, for an opaque struct
    if ts.peek().value == ";":
        ts.next()
        return Struct(name, None, line=line)

    ts.expect("sym", "{")

    # 'name: type;' fields until the closing brace
    fields = []
    while ts.peek().value != "}":
        field_name = ts.expect("ident").value
        ts.expect("sym", ":")
        fields.append(Field(field_name, parse_type(ts)))
        ts.expect("sym", ";")

    ts.expect("sym", "}")

    # an optional ';' may close the declaration, C-style
    if ts.peek().value == ";":
        ts.next()

    return Struct(name, fields, line=line)
