"""Parsing of '@const' declarations."""

from siec.ast import Const
from siec.parser.expressions import parse_expression
from siec.parser.stream import TokenStream
from siec.parser.types import parse_type


def parse_const(ts: TokenStream) -> Const:
    """
    Parse '@const name[: T] = <value>;', the type annotation optional.
    """
    line = ts.peek().line
    ts.expect("sym", "@")
    ts.expect("ident", "const")
    name = ts.expect("ident").value

    type_ = None
    if ts.peek().syntax == ":":
        ts.next()
        type_ = parse_type(ts)

    ts.expect("sym", "=")
    value = parse_expression(ts)
    ts.expect("sym", ";")

    return Const(name, type_, value, line=line)
