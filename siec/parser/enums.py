"""Parsing of enum declarations."""

from siec.ast import Enum, Variant
from siec.parser.expressions import parse_expression
from siec.parser.stream import TokenStream
from siec.parser.types import parse_type


def parse_enum(ts: TokenStream) -> Enum:
    """
    Parse 'enum Name[: T] { A, B = <value>, ... }' into an Enum node.

    The backing type defaults to i32, and a trailing comma is allowed.
    """
    line = ts.peek().line
    ts.expect("kw", "enum")
    name = ts.expect("ident").value

    backing = "i32"
    if ts.peek().syntax == ":":
        ts.next()
        backing = parse_type(ts)

    ts.expect("sym", "{")

    members = []
    while ts.peek().syntax != "}":
        member = ts.expect("ident").value

        value = None
        if ts.peek().syntax == "=":
            ts.next()
            value = parse_expression(ts)

        members.append(Variant(member, value))

        # a comma follows every member but, optionally, the last
        if ts.peek().syntax != "}":
            ts.expect("sym", ",")

    ts.expect("sym", "}")
    return Enum(name, backing, members, line=line)
